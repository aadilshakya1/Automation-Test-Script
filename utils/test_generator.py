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
        transport: Optional[Callable[[str, str], str]] = None,
    ):
        self.api_key = (api_key if api_key is not None else LLM_API_KEY).strip()
        if base_url:
            self.base_url = base_url.rstrip("/")
        elif self.api_key.startswith("sk-or-"):
            self.base_url = "https://openrouter.ai/api/v1"
        elif self.api_key:
            self.base_url = "https://api.openai.com/v1"
        else:
            self.base_url = LLM_BASE_URL.rstrip("/")

        if model:
            self.model = model
        elif self.api_key.startswith("sk-or-") and "/" not in LLM_MODEL:
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
                "GPT/OpenRouter API key not configured. Set OPENROUTER_API_KEY or OPENAI_API_KEY "
                "(or pass --api-key)."
            )

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(tasks, page_object_summary, live_snapshots or [])

        if self._transport is not None:
            raw_reply = self._transport(system_prompt, user_prompt)
        else:
            raw_reply = self._call_chat_completions(system_prompt, user_prompt)

        scenarios, pytest_code = self._parse_generation_payload(raw_reply)
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
            "2. Never use real patient data; only use made-up synthetic ages, genes (e.g. CYP2C19, CYP2D6), and conditions.\n"
            "3. Locate interactive UI buttons/links/tabs strictly with `page.get_by_role(...)` or `page.get_by_text(...)`, NEVER CSS classes.\n"
            "4. Reuse existing Page Objects (`LoginPage`, `ClinicalPage`, `NavigationPage`) whenever appropriate.\n"
            "5. Important app routing note: Direct `page.goto('/clinical')` or `page.goto('/education')` redirects to '/'. "
            "Use `NavigationPage.navigate_to_clinical()` or `NavigationPage.navigate_to_education()` after login.\n"
            "6. Include standard pytest browser/page fixtures (`browser_context`, `page`) using `sync_playwright()`.\n"
            "7. Respond strictly with valid JSON matching:\n"
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
