import ast
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GENERATED_PLAN_PATH,
    GENERATED_STATE_PATH,
    GENERATED_TEST_PATH,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    PROJECT_ROOT,
)
from utils.task_reader import TaskDocument


class MissingLLMKeyError(RuntimeError):
    """Raised when test generation is invoked without an OpenRouter/OpenAI API key."""


class GenerationError(RuntimeError):
    """Raised when the LLM request fails or produces invalid test code."""


class ApprovalRequiredError(PermissionError):
    """Raised when attempting to execute generated tests without explicit user approval."""


@dataclass
class GeneratedScenario:
    """Human-reviewable test scenario derived from a plain-English task."""

    id: str
    title: str
    steps: List[str] = field(default_factory=list)
    expected_result: str = ""
    synthetic_data_used: str = ""


@dataclass
class GenerationResult:
    """Result bundle produced by Task -> LLM -> tests/test_generated.py."""

    task_files: List[str]
    scenarios: List[GeneratedScenario]
    pytest_code: str
    test_file_path: Path
    plan_file_path: Path
    model_used: str
    approved: bool = False


class LLMTestGenerator:
    """
    Reads human-English tasks + application context (Page Objects & live UI snapshots),
    generates human-reviewable test scenarios and tests/test_generated.py via GPT/OpenRouter,
    and requires approval before pytest execution.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        transport: Optional[Callable[[str, str], str]] = None,
    ):
        self.api_key = (api_key if api_key is not None else (LLM_API_KEY or GEMINI_API_KEY)).strip()

        if provider:
            self.provider = provider.lower()
        elif self.api_key.startswith(("AIza", "AQ.")) or (model and model.lower().startswith("gemini")):
            self.provider = "gemini"
        elif self.api_key.startswith("sk-or-"):
            self.provider = "openrouter"
        elif self.api_key:
            self.provider = "openai"
        elif GEMINI_API_KEY and not LLM_API_KEY:
            self.provider = "gemini"
        else:
            self.provider = "openai"

        if base_url:
            self.base_url = base_url.rstrip("/")
        elif self.provider == "openrouter":
            self.base_url = "https://openrouter.ai/api/v1"
        elif self.provider == "gemini":
            self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        elif self.api_key:
            self.base_url = "https://api.openai.com/v1"
        else:
            self.base_url = LLM_BASE_URL.rstrip("/")

        if model:
            self.model = model
        elif self.provider == "gemini":
            self.model = GEMINI_MODEL
        elif self.provider == "openrouter" and "/" not in LLM_MODEL:
            self.model = f"openai/{LLM_MODEL}"
        else:
            self.model = LLM_MODEL

        self._transport = transport

    def generate(
        self,
        tasks: List[TaskDocument],
        page_object_summary: str,
        live_snapshots: Optional[List[Dict[str, Any]]] = None,
        output_test_path: Path = GENERATED_TEST_PATH,
        output_plan_path: Path = GENERATED_PLAN_PATH,
        state_path: Path = GENERATED_STATE_PATH,
    ) -> GenerationResult:
        """
        Generates structured scenarios and tests/test_generated.py, validates Python syntax,
        writes artifacts, and marks approval=False awaiting user sign-off.
        """
        if not tasks:
            raise ValueError("At least one TaskDocument is required.")

        if self._transport is None and not self.api_key:
            raise MissingLLMKeyError(
                "LLM API key not configured. Set GEMINI_API_KEY, OPENROUTER_API_KEY, "
                "or OPENAI_API_KEY (or pass --gemini-key / --api-key)."
            )

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(tasks, page_object_summary, live_snapshots or [])

        raw_reply = self._invoke_llm(system_prompt, user_prompt)
        scenarios, pytest_code = self._parse_generation_payload(raw_reply)

        issues = self._find_code_issues(pytest_code)
        if issues and self._transport is None:
            repair_prompt = (
                "Fix ONLY the Playwright/pytest issues listed below in `pytest_code` and return "
                "the corrected JSON (`scenarios` and `pytest_code`):\n"
                + "\n".join(f"- {issue}" for issue in issues)
                + f"\n\n### Current pytest_code:\n```python\n{pytest_code}\n```"
            )
            repaired_reply = self._invoke_llm(system_prompt, repair_prompt)
            scenarios, pytest_code = self._parse_generation_payload(repaired_reply)

        self._validate_python_syntax(pytest_code)

        output_test_path.parent.mkdir(parents=True, exist_ok=True)
        output_test_path.write_text(pytest_code.rstrip() + "\n", encoding="utf-8")

        task_names = [t.filename for t in tasks]
        plan_md = self._render_plan_markdown(task_names, scenarios, output_test_path)
        output_plan_path.parent.mkdir(parents=True, exist_ok=True)
        output_plan_path.write_text(plan_md, encoding="utf-8")

        code_hash = hashlib.sha256(pytest_code.encode("utf-8")).hexdigest()
        state_payload = {
            "approved": False,
            "generated_at": datetime.now().isoformat(),
            "model_used": self.model,
            "task_files": task_names,
            "test_file": str(output_test_path),
            "code_sha256": code_hash,
            "scenarios": [
                {
                    "id": s.id,
                    "title": s.title,
                    "steps": s.steps,
                    "expected_result": s.expected_result,
                    "synthetic_data_used": s.synthetic_data_used,
                }
                for s in scenarios
            ],
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

        return GenerationResult(
            task_files=task_names,
            scenarios=scenarios,
            pytest_code=pytest_code,
            test_file_path=output_test_path,
            plan_file_path=output_plan_path,
            model_used=self.model,
            approved=False,
        )

    def _build_system_prompt(self) -> str:
        return (
            "You are an expert QA Automation Engineer specializing in Python Playwright and pytest.\n"
            "Given plain-English task requirements, existing Page Object signatures, and live UI "
            "snapshots of the target PGx web application (https://pgx-dev.datum.bio/), generate:\n"
            "1. A concise list of test scenarios for human review.\n"
            "2. A complete, runnable pytest module for tests/test_generated.py.\n\n"
            "STRICT RULES FOR GENERATED PYTEST CODE:\n"
            "1. Always import config values: `from config import BASE_URL, TEST_USERNAME, TEST_PASSWORD, HEADLESS`.\n"
            "   Never hardcode passwords or API keys. Skip authenticated tests if TEST_USERNAME/TEST_PASSWORD are empty.\n"
            "2. BASE_URL already has a trailing slash. ALWAYS build URLs using `f\"{BASE_URL.rstrip('/')}/path\"` "
            "(NEVER `BASE_URL + '/path'`, which creates double slashes `//path`).\n"
            "3. Never use real patient data; only use made-up synthetic ages, genes (e.g. CYP2C19, CYP2D6), and conditions.\n"
            "4. Locate interactive UI buttons/links/tabs strictly with `page.get_by_role(...)` or `page.get_by_text(...)`, NEVER CSS classes.\n"
            "5. ONLY call methods that explicitly exist in the provided Page Object signatures "
            "(for example, `dismiss_modal_if_present()` is on `NavigationPage`, NOT `ClinicalPage`).\n"
            "6. Playwright `expect(...)` ONLY works on `Page` or `Locator` objects (e.g. `expect(locator).to_be_visible()`). "
            "NEVER pass booleans or strings into `expect(...)` (there is NO `to_be_true()` or `to_equal()` in Python Playwright!). "
            "Use plain Python `assert` for booleans, strings, URLs, and helper method return values.\n"
            "7. Authentication & App Routing:\n"
            "   - Every page EXCEPT `/shared_chat` (including `/`, `/clinical`, `/education`, `/terms-of-use`) requires login via `LoginPage`.\n"
            "   - ALWAYS guard login calls with `if login_page.is_login_page_displayed(): login_page.login(TEST_USERNAME, TEST_PASSWORD)` "
            "(NEVER write `assert login_page.is_login_page_displayed()` because shared browser contexts persist auth cookies!).\n"
            "   - Direct `page.goto('/clinical')`, `page.goto('/education')`, or `page.goto('/terms-of-use')` redirects back to '/'. "
            "Always use `NavigationPage.navigate_to_clinical()`, `NavigationPage.navigate_to_education()`, and `NavigationPage.navigate_to_terms_of_use()`.\n"
            "   - Always call `NavigationPage.dismiss_modal_if_present()` on '/' and after navigating to '/clinical' or '/education'.\n"
            "   - On `/clinical`, call `clinical_page.click_add_demographics()` before `fill_age()` or `select_sex()`.\n"
            "   - On `/education`, clicking 'Drug Monograph', 'Lectures', or 'Patient Case Simulation' stays on `/education`; do NOT call `page.go_back()` after clicking them.\n"
            "8. Check `get_floating_mode_switch_btn()` visibility safely with `btn = nav.get_floating_mode_switch_btn(); assert not (btn.count() > 0 and btn.first.is_visible())`.\n"
            "9. Use this EXACT fixture pattern (NEVER pass `sync_playwright` as a fixture parameter and NEVER use `autouse=True` on login fixtures):\n"
            "```python\n"
            "@pytest.fixture(scope='module')\n"
            "def browser_context():\n"
            "    with sync_playwright() as p:\n"
            "        browser = p.chromium.launch(headless=HEADLESS)\n"
            "        context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'])\n"
            "        yield context\n"
            "        browser.close()\n\n"
            "@pytest.fixture\n"
            "def page(browser_context):\n"
            "    pg = browser_context.new_page()\n"
            "    yield pg\n"
            "    pg.close()\n"
            "```\n"
            "10. Respond strictly with valid JSON matching:\n"
            "{\n"
            '  "scenarios": [\n'
            '    {"id": "TC01", "title": "...", "steps": ["..."], "expected_result": "...", "synthetic_data_used": "..."}\n'
            "  ],\n"
            '  "pytest_code": "complete valid python file content"\n'
            "}"
        )

    def _build_user_prompt(
        self,
        tasks: List[TaskDocument],
        page_object_summary: str,
        live_snapshots: List[Dict[str, Any]],
    ) -> str:
        task_sections = "\n\n".join(
            f"### Task File: {t.filename}\n{t.content}" for t in tasks
        )
        snapshot_text = (
            json.dumps(live_snapshots, indent=2)[:8000]
            if live_snapshots
            else "Live UI exploration skipped or unauthenticated; rely on Page Objects and task specs."
        )
        return (
            f"## 1. Plain-English Task Specifications\n{task_sections}\n\n"
            f"## 2. Existing Project Page Objects (Reuse These)\n{page_object_summary[:8000]}\n\n"
            f"## 3. Live Application Snapshot (Visible Controls)\n{snapshot_text}\n\n"
            "Generate the JSON response with `scenarios` and runnable `pytest_code`."
        )

    def _call_chat_completions(self, system_prompt: str, user_prompt: str) -> str:
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "https://pgx-dev.datum.bio"
            headers["X-Title"] = "PGx Playwright AI Test Generator"

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise GenerationError(f"LLM API HTTP {exc.code}: {err_body}") from exc
        except Exception as exc:
            raise GenerationError(f"LLM API request failed: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise GenerationError(f"LLM API returned no choices: {data}")
        content = choices[0].get("message", {}).get("content", "").strip()
        if not content:
            raise GenerationError("LLM API returned empty message content.")
        return content

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        """Calls Google Gemini generateContent REST API with structured JSON output."""
        endpoint = f"{self.base_url}/models/{self.model}:generateContent"
        response_schema = {
            "type": "object",
            "properties": {
                "scenarios": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                            "expected_result": {"type": "string"},
                            "synthetic_data_used": {"type": "string"},
                        },
                        "required": ["id", "title", "steps", "expected_result"],
                    },
                },
                "pytest_code": {"type": "string"},
            },
            "required": ["scenarios", "pytest_code"],
        }
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise GenerationError(f"Gemini API HTTP {exc.code}: {err_body}") from exc
        except Exception as exc:
            raise GenerationError(f"Gemini API request failed: {exc}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise GenerationError(f"Gemini API returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts") or []
        content = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if not content:
            raise GenerationError(f"Gemini API returned empty text parts: {data}")
        return content

    def _parse_generation_payload(
        self, raw_reply: str
    ) -> Tuple[List[GeneratedScenario], str]:
        cleaned = self._strip_code_fences(raw_reply)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise GenerationError(
                f"LLM response was not valid JSON: {exc}\nPreview: {raw_reply[:400]}"
            ) from exc

        scenarios: List[GeneratedScenario] = []
        for idx, item in enumerate(data.get("scenarios") or [], start=1):
            if isinstance(item, dict):
                scenarios.append(
                    GeneratedScenario(
                        id=str(item.get("id") or f"TC{idx:02d}"),
                        title=str(item.get("title") or f"Generated Scenario {idx}"),
                        steps=[str(s) for s in (item.get("steps") or [])],
                        expected_result=str(item.get("expected_result") or ""),
                        synthetic_data_used=str(item.get("synthetic_data_used") or ""),
                    )
                )

        pytest_code = self._strip_code_fences(str(data.get("pytest_code") or ""))
        if not pytest_code:
            raise GenerationError("LLM JSON did not include non-empty 'pytest_code'.")
        if not scenarios:
            raise GenerationError("LLM JSON did not include any 'scenarios'.")
        return scenarios, pytest_code

    def _strip_code_fences(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines).strip()
        return stripped

    def _invoke_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Dispatches generation/repair prompt to the configured transport or LLM API."""
        if self._transport is not None:
            return self._transport(system_prompt, user_prompt)
        if self.provider == "gemini":
            return self._call_gemini(system_prompt, user_prompt)
        return self._call_chat_completions(system_prompt, user_prompt)

    def _find_code_issues(self, code: str) -> List[str]:
        """Detects common Playwright Python anti-patterns so the LLM can self-repair before approval."""
        issues: List[str] = []
        try:
            ast.parse(code)
        except SyntaxError as exc:
            issues.append(f"Python syntax error: {exc}")

        if ".to_be_true(" in code or ".to_be_false(" in code or ".to_equal(" in code:
            issues.append(
                "Invalid Python Playwright assertions (.to_be_true(), .to_be_false(), or .to_equal()). "
                "Use plain Python `assert` for booleans and strings; `expect()` is ONLY for Page/Locator."
            )
        if 'BASE_URL + "/' in code or "BASE_URL + '/" in code:
            issues.append(
                'Direct `BASE_URL + "/..."` creates double slashes. Replace with `f"{BASE_URL.rstrip(\'/\')}/..."`.'
            )
        if "clinical_page.dismiss_modal_if_present" in code or "clinical.dismiss_modal_if_present" in code:
            issues.append(
                "`dismiss_modal_if_present()` does not exist on `ClinicalPage`; call it on `NavigationPage`."
            )
        if "assert login_page.is_login_page_displayed()" in code:
            issues.append(
                "Do not assert `login_page.is_login_page_displayed()`; guard `login_page.login(...)` with "
                "`if login_page.is_login_page_displayed():` instead."
            )
        if "login_page.login(" in code and "is_login_page_displayed()" not in code:
            issues.append(
                "Wrap every `login_page.login(...)` call inside `if login_page.is_login_page_displayed():`."
            )
        if "def browser_context(sync_playwright" in code:
            issues.append(
                "`sync_playwright` is not a pytest fixture! Use `def browser_context():` with `with sync_playwright() as p:` inside."
            )
        if "autouse=True" in code and "login" in code:
            issues.append(
                "Do not use `autouse=True` on an authentication fixture (public tests like `/shared_chat` should not require login)."
            )
        return issues

    def _validate_python_syntax(self, code: str):
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise GenerationError(f"Generated pytest_code has invalid Python syntax: {exc}") from exc

    def _render_plan_markdown(
        self,
        task_names: List[str],
        scenarios: List[GeneratedScenario],
        test_path: Path,
    ) -> str:
        lines = [
            "# Generated Test Scenarios (Pending Approval)",
            "",
            f"- **Source Task Files**: {', '.join(task_names)}",
            f"- **Generated Pytest File**: `{test_path}`",
            f"- **Model**: `{self.model}`",
            "",
            "---",
            "",
        ]
        for s in scenarios:
            lines.append(f"## {s.id}: {s.title}")
            if s.synthetic_data_used:
                lines.append(f"- **Synthetic Data**: {s.synthetic_data_used}")
            if s.steps:
                lines.append("- **Steps**:")
                for i, step in enumerate(s.steps, start=1):
                    lines.append(f"  {i}. {step}")
            if s.expected_result:
                lines.append(f"- **Expected Result**: {s.expected_result}")
            lines.append("")
        return "\n".join(lines)


def approve_generated_tests(
    test_path: Path = GENERATED_TEST_PATH,
    state_path: Path = GENERATED_STATE_PATH,
) -> Dict[str, Any]:
    """Marks the currently generated tests/test_generated.py as approved for execution."""
    if not test_path.exists():
        raise FileNotFoundError(f"Generated test file not found: {test_path}")

    current_hash = hashlib.sha256(test_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    state: Dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    state["approved"] = True
    state["approved_at"] = datetime.now().isoformat()
    state["code_sha256"] = current_hash
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def is_generated_suite_approved(
    test_path: Path = GENERATED_TEST_PATH,
    state_path: Path = GENERATED_STATE_PATH,
) -> bool:
    """Checks that tests/test_generated.py exists and matches the approved state."""
    if not test_path.exists() or not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not state.get("approved"):
            return False
        current_hash = hashlib.sha256(
            test_path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        return state.get("code_sha256") == current_hash
    except Exception:
        return False


def execute_generated_pytest(
    test_path: Path = GENERATED_TEST_PATH,
    state_path: Path = GENERATED_STATE_PATH,
    approve: bool = False,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Executes pytest on tests/test_generated.py ONLY after approval.
    If approve=True, records approval first; otherwise enforces prior approval.
    """
    if not test_path.exists():
        raise FileNotFoundError(
            f"Generated test file does not exist: {test_path}. Run generation first."
        )

    if approve:
        approve_generated_tests(test_path=test_path, state_path=state_path)
    elif not is_generated_suite_approved(test_path=test_path, state_path=state_path):
        raise ApprovalRequiredError(
            "Generated test suite is pending human approval. Review artifacts/generated_scenarios.md "
            "and tests/test_generated.py, then re-run with --approve."
        )

    import os

    run_env = os.environ.copy()
    if env_overrides:
        run_env.update({k: v for k, v in env_overrides.items() if v is not None})

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-v"],
        cwd=str(PROJECT_ROOT),
        env=run_env,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return {
        "exit_code": proc.returncode,
        "status": "PASSED" if proc.returncode == 0 else "FAILED",
        "test_file": str(test_path),
        "output": output.strip(),
    }
