import json
import pytest
from playwright.sync_api import Page, sync_playwright

from config import (
    BASE_URL,
    GEMINI_API_KEY,
    HEADLESS,
    TEST_PASSWORD,
    TEST_USERNAME,
)
from pages.clinical_page import ClinicalPage
from pages.login_page import LoginPage
from pages.navigation_page import NavigationPage
from utils.ai_judge import (
    GeminiAIJudge,
    JudgeAPIError,
    MissingAPIKeyError,
    PGxCaseContext,
)


# ---------------------------------------------------------------------------
# Unit Tests for GeminiAIJudge (Offline, deterministic via mock transport)
# ---------------------------------------------------------------------------

def test_ai_judge_passes_when_all_criteria_met_and_no_safety_flags():
    """Verifies GeminiAIJudge returns passed=True when score, criteria, and safety pass."""
    mock_payload = {
        "passed": True,
        "score": 0.95,
        "summary": "Output accurately reflects CYP2C19 Intermediate Metabolizer CPIC guidance.",
        "criteria_results": [
            {
                "criterion": "References CYP2C19 Intermediate Metabolizer.",
                "met": True,
                "evidence": "CYP2C19•Intermediate Metabolizer",
            },
            {
                "criterion": "Recommends alternative to clopidogrel.",
                "met": True,
                "evidence": "Alternative use strongly recommended: clopidogrel",
            },
        ],
        "clinical_safety_flags": [],
    }

    judge = GeminiAIJudge(
        api_key="dummy-key",
        pass_threshold=0.8,
        transport=lambda sys_p, usr_p: json.dumps(mock_payload),
    )
    case = PGxCaseContext(
        age="42",
        sex="Male",
        gene="CYP2C19",
        phenotype="Intermediate Metabolizer",
        expected_criteria=[
            "References CYP2C19 Intermediate Metabolizer.",
            "Recommends alternative to clopidogrel.",
        ],
    )
    verdict = judge.evaluate(case, "Genetic Information CYP2C19•Intermediate Metabolizer clopidogrel")

    assert verdict.passed is True
    assert verdict.score == pytest.approx(0.95)
    assert len(verdict.criteria_results) == 2
    assert verdict.clinical_safety_flags == []
    assert verdict.to_dict()["passed"] is True


def test_ai_judge_fails_on_safety_flag_even_if_score_high():
    """Ensures deterministic QA gate rejects responses with clinical safety flags."""
    mock_payload = {
        "passed": True,
        "score": 0.9,
        "summary": "High overlap, but missing disclaimer.",
        "criteria_results": [
            {"criterion": "Gene present", "met": True, "evidence": "CYP2C19"}
        ],
        "clinical_safety_flags": ["Missing clinical disclaimer"],
    }

    judge = GeminiAIJudge(
        api_key="dummy-key",
        pass_threshold=0.8,
        transport=lambda sys_p, usr_p: json.dumps(mock_payload),
    )
    case = PGxCaseContext(gene="CYP2C19", phenotype="Intermediate Metabolizer")
    verdict = judge.evaluate(case, "CYP2C19 Intermediate Metabolizer")

    assert verdict.passed is False
    assert verdict.clinical_safety_flags == ["Missing clinical disclaimer"]


def test_ai_judge_fails_when_criterion_unmet_or_below_threshold():
    """Ensures verdict.passed=False if any criterion is unmet or score < threshold."""
    mock_payload = {
        "passed": True,
        "score": 0.65,
        "summary": "Clopidogrel warning missing.",
        "criteria_results": [
            {"criterion": "Clopidogrel warning", "met": False, "evidence": "Not found"}
        ],
        "clinical_safety_flags": [],
    }

    judge = GeminiAIJudge(
        api_key="dummy-key",
        pass_threshold=0.8,
        transport=lambda sys_p, usr_p: json.dumps(mock_payload),
    )
    case = PGxCaseContext(gene="CYP2C19", phenotype="Poor Metabolizer")
    verdict = judge.evaluate(case, "Some partial PGx text")

    assert verdict.passed is False
    assert verdict.score == pytest.approx(0.65)


def test_ai_judge_missing_api_key_raises():
    """Verifies MissingAPIKeyError when no key and no mock transport are provided."""
    judge = GeminiAIJudge(api_key="")
    case = PGxCaseContext(gene="CYP2C19", phenotype="Intermediate Metabolizer")
    with pytest.raises(MissingAPIKeyError):
        judge.evaluate(case, "Sample PGx clinical output")


def test_ai_judge_empty_output_raises_value_error():
    """Verifies ValueError when rendered UI output is empty."""
    judge = GeminiAIJudge(api_key="dummy-key", transport=lambda s, u: "{}")
    case = PGxCaseContext(gene="CYP2C19", phenotype="Intermediate Metabolizer")
    with pytest.raises(ValueError):
        judge.evaluate(case, "   ")


def test_ai_judge_invalid_json_raises_judge_api_error():
    """Verifies malformed Gemini output raises JudgeAPIError."""
    judge = GeminiAIJudge(api_key="dummy-key", transport=lambda s, u: "not json")
    case = PGxCaseContext(gene="CYP2C19", phenotype="Intermediate Metabolizer")
    with pytest.raises(JudgeAPIError):
        judge.evaluate(case, "CYP2C19 output")


# ---------------------------------------------------------------------------
# End-to-End Playwright + Gemini AI Judge Test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        yield context
        browser.close()


@pytest.fixture
def page(browser_context):
    page = browser_context.new_page()
    yield page
    page.close()


def test_live_pgx_clinical_assessment_with_ai_judge(page: Page):
    """
    Runs a synthetic PGx clinical assessment (CYP2C19 Intermediate Metabolizer, Age 42)
    on pgx-dev.datum.bio/clinical and evaluates the rendered response with Gemini AI Judge.
    """
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not provided. Set PGX_USERNAME and PGX_PASSWORD.")
    if not GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not provided. Set GEMINI_API_KEY to run live Gemini evaluation.")

    # 1. Authenticate and launch /clinical cleanly (handles onboarding modal on / and /clinical)
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    login_page.login(TEST_USERNAME, TEST_PASSWORD)
    page.wait_for_load_state("networkidle", timeout=30000)

    nav = NavigationPage(page, BASE_URL)
    nav.navigate_to_clinical()
    clinical = ClinicalPage(page)

    if not clinical.is_manual_form_open():
        clinical.click_enter_gene_panel_manually()
        page.wait_for_timeout(1000)

    # 2. Enter synthetic patient values (made-up age & gene; no real patient data)
    case = PGxCaseContext(
        age="42",
        sex="Male",
        gene="CYP2C19",
        phenotype="Intermediate Metabolizer",
        expected_criteria=[
            "Displays Genetic Information with CYP2C19 and Intermediate Metabolizer.",
            "Categorizes clopidogrel as requiring alternative therapy (e.g., Alternative use strongly recommended).",
            "Indicates guideline coverage citing CPIC.",
            "Includes a medical disclaimer noting content is a guide and not a substitute for clinical judgment.",
        ],
    )

    clinical.click_add_demographics()
    clinical.fill_age(case.age)
    clinical.select_sex(case.sex)
    clinical.select_gene(case.gene)
    clinical.select_phenotype(case.phenotype)
    page.wait_for_timeout(500)

    # 3. Run full PGx assessment and extract rendered UI response
    clinical.run_assessment(timeout=90000)
    rendered_output = clinical.get_assessment_response_text()

    # 4. Evaluate rendered clinical text using Gemini AI Judge
    judge = GeminiAIJudge(api_key=GEMINI_API_KEY)
    verdict = judge.evaluate(case, rendered_output)

    assert verdict.passed, (
        f"Gemini AI Judge failed clinical validation (score={verdict.score}): "
        f"{verdict.summary} | Safety flags: {verdict.clinical_safety_flags} | "
        f"Criteria: {verdict.to_dict()['criteria_results']}"
    )
