import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from config import GEMINI_API_KEY, GEMINI_MODEL

try:
    from google import genai
    from google.genai import types as genai_types
    _HAS_GOOGLE_GENAI = True
except ImportError:
    genai = None
    genai_types = None
    _HAS_GOOGLE_GENAI = False


class MissingAPIKeyError(RuntimeError):
    """Raised when Gemini AI evaluation is invoked without an API key."""


class JudgeAPIError(RuntimeError):
    """Raised when the Gemini API request or response parsing fails."""


@dataclass
class PGxCaseContext:
    """Synthetic clinical inputs and expected validation criteria for a PGx test case."""

    gene: str
    phenotype: str
    age: Optional[str] = None
    sex: Optional[str] = None
    medications: List[str] = field(default_factory=list)
    medical_history: List[str] = field(default_factory=list)
    question: Optional[str] = None
    expected_criteria: List[str] = field(default_factory=list)


@dataclass
class CriterionResult:
    """Evaluation outcome for a single clinical criterion."""

    criterion: str
    met: bool
    evidence: str


@dataclass
class JudgeVerdict:
    """Structured evaluation verdict produced by the Gemini AI Judge."""

    passed: bool
    score: float
    summary: str
    criteria_results: List[CriterionResult] = field(default_factory=list)
    clinical_safety_flags: List[str] = field(default_factory=list)
    model_used: str = GEMINI_MODEL
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the verdict into JSON-safe primitives for test reporting."""
        return {
            "passed": self.passed,
            "score": self.score,
            "summary": self.summary,
            "model_used": self.model_used,
            "clinical_safety_flags": list(self.clinical_safety_flags),
            "criteria_results": [
                {"criterion": c.criterion, "met": c.met, "evidence": c.evidence}
                for c in self.criteria_results
            ],
        }


_RESPONSE_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "criteria_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "met": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["criterion", "met", "evidence"],
            },
        },
        "clinical_safety_flags": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "passed",
        "score",
        "summary",
        "criteria_results",
        "clinical_safety_flags",
    ],
}


class GeminiAIJudge:
    """
    Evaluates rendered pharmacogenomic (PGx) clinical responses from pgx-dev.datum.bio
    against synthetic patient inputs and CPIC/FDA criteria using Google Gemini.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = GEMINI_MODEL,
        pass_threshold: float = 0.8,
        transport: Optional[Callable[[str, str], str]] = None,
    ):
        """
        Initializes the Gemini AI Judge.

        Args:
            api_key: Gemini API key (defaults to GEMINI_API_KEY / GOOGLE_API_KEY env).
            model: Gemini model identifier (default from config.GEMINI_MODEL).
            pass_threshold: Minimum score (0.0-1.0) required alongside met criteria.
            transport: Optional callable(system_prompt, user_prompt) -> json_str for unit testing.
        """
        self.api_key = (api_key if api_key is not None else GEMINI_API_KEY).strip()
        self.model = model
        self.pass_threshold = pass_threshold
        self._transport = transport

    def evaluate(self, case: PGxCaseContext, rendered_output: str) -> JudgeVerdict:
        """
        Evaluates a rendered clinical PGx output against the synthetic case context.

        Returns a JudgeVerdict with criteria-level evidence and safety flags.
        """
        if not rendered_output or not rendered_output.strip():
            raise ValueError("rendered_output must be a non-empty string captured from the UI.")

        if self._transport is None and not self.api_key:
            raise MissingAPIKeyError(
                "Gemini API key not configured. Set GEMINI_API_KEY (or pass --gemini-key)."
            )

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(case, rendered_output)

        if self._transport is not None:
            raw_text = self._transport(system_prompt, user_prompt)
        elif _HAS_GOOGLE_GENAI:
            raw_text = self._call_sdk(system_prompt, user_prompt)
        else:
            raw_text = self._call_rest_api(system_prompt, user_prompt)

        return self._parse_verdict(raw_text, case)

    def _build_system_prompt(self) -> str:
        return (
            "You are an automated QA Clinical Pharmacogenomics (PGx) Judge evaluating "
            "the rendered UI output of a clinical decision support web application.\n"
            "Strictly check ONLY what appears in the provided UI text against the "
            "synthetic patient inputs and expected criteria.\n"
            "Rules:\n"
            "1. Do NOT invent clinical facts not shown in the rendered UI output.\n"
            "2. Score from 0.0 to 1.0 based on clinical accuracy, completeness, and safety.\n"
            "3. Flag any contradiction with the input gene/phenotype, wrong drug warning, "
            "or missing clinical disclaimer under clinical_safety_flags.\n"
            "4. Evaluate every criterion in expected_criteria with direct text evidence.\n"
            "5. Respond strictly with valid JSON matching the required schema."
        )

    def _build_user_prompt(self, case: PGxCaseContext, rendered_output: str) -> str:
        criteria_block = "\n".join(
            f"- {c}" for c in (case.expected_criteria or self._default_criteria(case))
        )
        meds = ", ".join(case.medications) if case.medications else "None specified"
        history = ", ".join(case.medical_history) if case.medical_history else "None specified"
        question_line = f"Follow-up Question: {case.question}\n" if case.question else ""

        return (
            f"### Synthetic Patient Input\n"
            f"- Age: {case.age or 'Not specified'}\n"
            f"- Sex: {case.sex or 'Not specified'}\n"
            f"- Gene: {case.gene}\n"
            f"- Phenotype: {case.phenotype}\n"
            f"- Medications: {meds}\n"
            f"- Medical History: {history}\n"
            f"{question_line}\n"
            f"### Expected Evaluation Criteria\n"
            f"{criteria_block}\n\n"
            f"### Rendered UI Clinical Output\n"
            f"{rendered_output.strip()[:12000]}"
        )

    def _default_criteria(self, case: PGxCaseContext) -> List[str]:
        return [
            f"Output references the tested gene '{case.gene}' and phenotype '{case.phenotype}'.",
            "Output provides actionable pharmacogenomic medication guidance categorized by risk/optimization level.",
            "Output attributes recommendations to a recognized PGx guideline source (e.g. CPIC, FDA, DPWG).",
            "Output includes a clinical disclaimer stating content is a guide and not a substitute for clinical judgment.",
        ]

    def _call_sdk(self, system_prompt: str, user_prompt: str) -> str:
        """Calls Gemini using the official google-genai Python SDK."""
        try:
            client = genai.Client(api_key=self.api_key)
            config = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=_RESPONSE_JSON_SCHEMA,
            )
            response = client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=config,
            )
            if not response.text:
                raise JudgeAPIError("Gemini SDK returned an empty response.")
            return response.text
        except Exception as exc:
            if isinstance(exc, JudgeAPIError):
                raise
            raise JudgeAPIError(f"Gemini SDK call failed: {exc}") from exc

    def _call_rest_api(self, system_prompt: str, user_prompt: str) -> str:
        """Standard-library HTTPS fallback using Gemini REST generateContent endpoint."""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_JSON_SCHEMA,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise JudgeAPIError(f"Gemini REST HTTP {exc.code}: {err_body}") from exc
        except Exception as exc:
            raise JudgeAPIError(f"Gemini REST request failed: {exc}") from exc

        candidates = resp_data.get("candidates") or []
        if not candidates:
            raise JudgeAPIError(f"Gemini REST returned no candidates: {resp_data}")

        parts = candidates[0].get("content", {}).get("parts") or []
        text_chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
        combined = "".join(text_chunks).strip()
        if not combined:
            raise JudgeAPIError(f"Gemini REST returned empty text parts: {resp_data}")
        return combined

    def _parse_verdict(self, raw_text: str, case: PGxCaseContext) -> JudgeVerdict:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise JudgeAPIError(f"Gemini returned invalid JSON: {exc}\nRaw: {raw_text[:400]}") from exc

        criteria_items: List[CriterionResult] = []
        for item in data.get("criteria_results", []):
            if isinstance(item, dict):
                criteria_items.append(
                    CriterionResult(
                        criterion=str(item.get("criterion", "")),
                        met=bool(item.get("met", False)),
                        evidence=str(item.get("evidence", "")),
                    )
                )

        safety_flags = [str(f) for f in (data.get("clinical_safety_flags") or []) if str(f).strip()]
        try:
            score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0

        # Deterministic QA gate: require model approval, threshold score, all criteria met, and zero safety flags
        all_criteria_met = all(c.met for c in criteria_items) if criteria_items else True
        model_passed = bool(data.get("passed", False))
        final_passed = (
            model_passed
            and score >= self.pass_threshold
            and all_criteria_met
            and len(safety_flags) == 0
        )

        # If model passed=False or criteria failed with empty summary, ensure clear summary
        summary = str(data.get("summary", "")).strip() or (
            f"Evaluated {case.gene} ({case.phenotype}) output."
        )

        return JudgeVerdict(
            passed=final_passed,
            score=score,
            summary=summary,
            criteria_results=criteria_items,
            clinical_safety_flags=safety_flags,
            model_used=self.model,
            raw_response=data,
        )
