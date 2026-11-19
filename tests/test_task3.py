import pytest
import re
from playwright.sync_api import sync_playwright, expect, Page

from config import BASE_URL, TEST_USERNAME, TEST_PASSWORD, HEADLESS
from pages.login_page import LoginPage
from pages.clinical_page import ClinicalPage
from pages.navigation_page import NavigationPage

"""
Third QA Task Specifications with Step-by-Step Validation Checks:
Task 1: Send button
- Check 1: Button is disabled when the field is empty.
- Check 2: Check if white space is allowed (spaces only) -> button remains disabled.
- Check 3: Check real text -> button becomes enabled.
- Check 4: Check clearing the field -> button is disabled again.

Task 2: Enter vs Shift+Enter
- Check 1: Type line 1, press Shift+Enter, type line 2 -> box holds two lines.
- Check 2: Shift+Enter does not dispatch request to /api/followupchat.
- Check 3: Pressing Enter dispatches request to /api/followupchat.
- Check 4: Request payload contains the typed prompt text.

Task 3: Example prompts
- Check 1: Panel opens on "CYP2D6 Poor Metabolizer".
- Check 2: Click "Next example" -> shows "Warfarin Dosing".
- Check 3: Click "Previous example" twice -> wraps around to "Oncology Case".
- Check 4: Click "Go to example 3" -> shows "Depression Treatment".
- Check 5: Click "Use this example" -> chat box contains that example's text.
- Check 6: Click "Close examples" -> panel hides and "Suggested Prompts" button is back.

Task 4: Profile menu
- Check 1: Click user profile in sidebar -> profile menu opens.
- Check 2: Menu displays the user's email.
- Check 3: Menu shows "Edit profile" button.
- Check 4: Menu shows "Send feedback" button.
- Check 5: Menu shows "Log out" button.
- Check 6: "Cancel subscription" button is absent (Free plan guard).
"""

class StepValidator:
    """Tracks and outputs explicit PASS / FAIL status for each validation checkpoint."""

    def __init__(self, task_name: str):
        self.task_name = task_name
        self.checks = []
        print(f"\n{'=' * 65}")
        print(f"  {self.task_name.upper()} - VALIDATION CHECKPOINTS")
        print(f"{'=' * 65}")

    def check(self, description: str, condition: bool, failure_msg: str = ""):
        mark = "[PASS]" if condition else "[FAIL]"
        print(f"  {mark} {description}")
        if not condition and failure_msg:
            print(f"         Reason: {failure_msg}")
        self.checks.append({"description": description, "passed": bool(condition), "error": failure_msg})

    def assert_all_passed(self):
        failed = [c for c in self.checks if not c["passed"]]
        print(f"{'-' * 65}")
        print(f"  Task Summary: {len(self.checks) - len(failed)}/{len(self.checks)} validations PASSED.")
        print(f"{'=' * 65}\n")
        assert len(failed) == 0, f"{len(failed)} validation check(s) FAILED in {self.task_name}: {failed}"


@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        yield context
        browser.close()

@pytest.fixture
def page(browser_context):
    pg = browser_context.new_page()
    yield pg
    pg.close()

@pytest.fixture
def clinical_page(page: Page):
    """Logs in and navigates to the clinical page."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not provided in .env.")

    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    if login_page.is_login_page_displayed():
        login_page.login(TEST_USERNAME, TEST_PASSWORD)

    nav = NavigationPage(page, BASE_URL)
    nav.dismiss_modal_if_present()
    nav.navigate_to_clinical()
    nav.dismiss_modal_if_present()

    return ClinicalPage(page, BASE_URL)


# =============================================================================
# Individual Test Tasks with Explicit Validations
# =============================================================================

def test_task3_1_send_button_state(page: Page, clinical_page: ClinicalPage):
    """Sub-Task 1: Send button state and whitespace validations."""
    v = StepValidator("Task 1: Send Button & Whitespace Validations")

    chat_input = clinical_page.get_chat_input()
    send_btn = clinical_page.get_send_message_button()

    # Validation 1: Empty field -> Send message is disabled
    chat_input.fill("")
    page.wait_for_timeout(300)
    is_empty_disabled = send_btn.is_disabled()
    v.check("Check if Send button is disabled when chat box is empty", is_empty_disabled)

    # Validation 2: Spaces only -> Whitespace is NOT allowed to enable button
    chat_input.fill("      ")
    page.wait_for_timeout(300)
    is_spaces_disabled = send_btn.is_disabled()
    v.check("Check if whitespace is disallowed (typing only spaces keeps button disabled)", is_spaces_disabled)

    # Validation 3: Real synthetic text -> Send button becomes enabled
    sample_text = "What is the recommended alternative for CYP2C19 intermediate metabolizer?"
    chat_input.fill(sample_text)
    page.wait_for_timeout(300)
    is_text_enabled = send_btn.is_enabled()
    v.check("Check if Send button becomes enabled when valid text is typed", is_text_enabled)

    # Validation 4: Clearing the field -> Button is disabled again
    chat_input.fill("")
    page.wait_for_timeout(300)
    is_cleared_disabled = send_btn.is_disabled()
    v.check("Check if Send button is disabled again when field is cleared", is_cleared_disabled)

    v.assert_all_passed()


def test_task3_2_enter_vs_shift_enter(page: Page, clinical_page: ClinicalPage):
    """Sub-Task 2: Enter vs Shift+Enter key handling validations."""
    v = StepValidator("Task 2: Enter vs Shift+Enter Validations")

    chat_input = clinical_page.get_chat_input()

    followup_requests = []
    def on_request(request):
        if "followupchat" in request.url.lower():
            body_data = ""
            try:
                body_data = request.post_data or ""
            except Exception:
                pass
            followup_requests.append({"url": request.url, "body": body_data})

    page.on("request", on_request)

    # 1. Multi-line typing with Shift+Enter
    line1 = "Synthetic patient profile with CYP2D6 poor metabolizer status."
    line2 = "Evaluate analgesia alternatives avoiding codeine."

    chat_input.click()
    chat_input.fill("")
    chat_input.type(line1, delay=20)
    page.keyboard.press("Shift+Enter")
    page.keyboard.type(line2, delay=20)
    page.wait_for_timeout(500)

    val = chat_input.input_value()

    # Validation 1: Box holds two lines separated by newline
    has_two_lines = (line1 in val) and (line2 in val) and ("\n" in val)
    v.check("Check if Shift+Enter preserves two separate lines in chat box", has_two_lines)

    # Validation 2: Shift+Enter does NOT send request to /api/followupchat
    no_api_on_shift_enter = len(followup_requests) == 0
    v.check("Check that Shift+Enter sends zero requests to /api/followupchat", no_api_on_shift_enter)

    # 2. Press Enter to submit
    chat_input.fill("")
    submit_prompt = "Evaluate warfarin dosage adjustment for CYP2C9 poor metabolizer"
    chat_input.type(submit_prompt, delay=20)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)

    # Validation 3: Enter sends a request to /api/followupchat
    has_followup_request = len(followup_requests) > 0
    v.check("Check that pressing Enter sends request to /api/followupchat", has_followup_request)

    # Validation 4: Request payload contains the typed text
    matched_body = [r for r in followup_requests if submit_prompt in r["body"] or "warfarin" in r["body"].lower()]
    has_text_in_payload = len(matched_body) > 0
    v.check("Check that /api/followupchat request body contains the submitted text", has_text_in_payload)

    v.assert_all_passed()


def test_task3_3_example_prompts_navigation(page: Page, clinical_page: ClinicalPage):
    """Sub-Task 3: Example prompts carousel validations."""
    v = StepValidator("Task 3: Example Prompts Carousel Validations")

    # 1. Click "Suggested Prompts"
    clinical_page.click_suggested_prompts()
    page.wait_for_timeout(500)

    # Validation 1: Panel opens on "CYP2D6 Poor Metabolizer"
    cyp2d6_el = page.get_by_text(re.compile(r"CYP2D6 Poor Metabolizer", re.I))
    cyp2d6_visible = cyp2d6_el.count() > 0 and cyp2d6_el.first.is_visible()
    v.check("Check panel opens on 'CYP2D6 Poor Metabolizer'", cyp2d6_visible)

    # 2. Next example -> "Warfarin Dosing"
    clinical_page.click_next_example()
    page.wait_for_timeout(400)
    warfarin_el = page.get_by_text(re.compile(r"Warfarin Dosing", re.I))
    warfarin_visible = warfarin_el.count() > 0 and warfarin_el.first.is_visible()
    v.check("Check 'Next example' advances to 'Warfarin Dosing'", warfarin_visible)

    # 3. Previous example twice -> wraps to "Oncology Case"
    clinical_page.click_previous_example()
    page.wait_for_timeout(300)
    clinical_page.click_previous_example()
    page.wait_for_timeout(400)
    oncology_el = page.get_by_text(re.compile(r"Oncology Case", re.I))
    oncology_visible = oncology_el.count() > 0 and oncology_el.first.is_visible()
    v.check("Check 'Previous example' twice wraps around to 'Oncology Case'", oncology_visible)

    # 4. Go to example 3 -> "Depression Treatment"
    clinical_page.click_go_to_example(3)
    page.wait_for_timeout(400)
    depression_el = page.get_by_text(re.compile(r"Depression Treatment", re.I))
    depression_visible = depression_el.count() > 0 and depression_el.first.is_visible()
    v.check("Check 'Go to example 3' directly displays 'Depression Treatment'", depression_visible)

    # 5. Use this example -> Chat box populated with text
    clinical_page.click_use_this_example()
    page.wait_for_timeout(500)
    chat_input = clinical_page.get_chat_input()
    chat_text = chat_input.input_value().strip()
    has_example_text = len(chat_text) > 0 and any(t in chat_text.lower() for t in ["depression", "ssri", "cyp2d6", "cyp2c19", "treatment"])
    v.check("Check 'Use this example' populates chat box with the example's text", has_example_text)

    # 6. Close examples -> Panel hides and Suggested Prompts button returns
    clinical_page.click_close_examples()
    page.wait_for_timeout(500)
    sp_btn = page.get_by_role("button", name=re.compile(r"Suggested Prompts", re.I))
    if sp_btn.count() == 0:
        sp_btn = page.get_by_text(re.compile(r"Suggested Prompts", re.I))
    sp_restored = sp_btn.count() > 0 and sp_btn.first.is_visible()
    v.check("Check 'Close examples' hides panel and restores 'Suggested Prompts' button", sp_restored)

    v.assert_all_passed()


def test_task3_4_user_profile_menu(page: Page, clinical_page: ClinicalPage):
    """Sub-Task 4: User Profile Menu validations."""
    v = StepValidator("Task 4: User Profile Menu Validations")

    # 1. Open profile menu from sidebar
    clinical_page.open_user_profile_menu()
    page.wait_for_timeout(600)

    # Validation 1: Menu opened and shows Edit Profile
    edit_profile = page.get_by_role("button", name=re.compile(r"Edit profile", re.I))
    if edit_profile.count() == 0:
        edit_profile = page.get_by_text(re.compile(r"Edit profile", re.I))
    has_edit_profile = edit_profile.count() > 0 and edit_profile.first.is_visible()
    v.check("Check menu shows 'Edit profile' button", has_edit_profile)

    # Validation 2: Menu shows Send Feedback
    send_feedback = page.get_by_role("button", name=re.compile(r"Send feedback", re.I))
    if send_feedback.count() == 0:
        send_feedback = page.get_by_text(re.compile(r"Send feedback", re.I))
    has_send_feedback = send_feedback.count() > 0 and send_feedback.first.is_visible()
    v.check("Check menu shows 'Send feedback' button", has_send_feedback)

    # Validation 3: Menu shows Log out
    log_out = page.get_by_role("button", name=re.compile(r"Log out", re.I))
    if log_out.count() == 0:
        log_out = page.get_by_text(re.compile(r"Log out", re.I))
    has_log_out = log_out.count() > 0 and log_out.first.is_visible()
    v.check("Check menu shows 'Log out' button", has_log_out)

    # Validation 4: User email is displayed in menu
    email_el = page.locator("text=@")
    has_email = email_el.count() > 0 and email_el.first.is_visible()
    v.check("Check menu displays user's email address", has_email)

    # Validation 5: 'Cancel subscription' is ABSENT (Free plan guard)
    cancel_sub = page.get_by_role("button", name=re.compile(r"Cancel subscription", re.I))
    if cancel_sub.count() == 0:
        cancel_sub = page.get_by_text(re.compile(r"Cancel subscription", re.I))
    no_cancel_subscription = not (cancel_sub.count() > 0 and cancel_sub.first.is_visible())
    v.check("Check 'Cancel subscription' button is absent (Free plan guard)", no_cancel_subscription)

    v.assert_all_passed()
