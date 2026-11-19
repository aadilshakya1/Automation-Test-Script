import pytest
import re
from playwright.sync_api import sync_playwright, expect, Page

from config import BASE_URL, TEST_USERNAME, TEST_PASSWORD, HEADLESS
from pages.login_page import LoginPage
from pages.clinical_page import ClinicalPage
from pages.navigation_page import NavigationPage

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
def setup_pages(page):
    """Provides instantiated Page Objects."""
    return {
        "login_page": LoginPage(page),
        "clinical_page": ClinicalPage(page),
        "navigation_page": NavigationPage(page)
    }

def perform_login_and_open_clinical(setup_pages):
    """Logs in if needed and navigates to /clinical cleanly."""
    login_page: LoginPage = setup_pages["login_page"]
    navigation_page: NavigationPage = setup_pages["navigation_page"]
    
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not configured in .env.")
    
    login_page.navigate()
    if login_page.is_login_page_displayed():
        login_page.login(TEST_USERNAME, TEST_PASSWORD)
    
    navigation_page.dismiss_modal_if_present()
    navigation_page.navigate_to_clinical()
    navigation_page.dismiss_modal_if_present()


# =============================================================================
# Third QA Task: Clinical Screen Chat, Examples & Profile Menu
# =============================================================================

class TestThirdQATask:

    # -------------------------------------------------------------------------
    # Task 1: Send button
    # -------------------------------------------------------------------------

    def test_tc01_send_button_state_toggle(self, page: Page, setup_pages):
        """Task 1: Send button state and whitespace validations."""
        v = StepValidator("Task 1: Send Button & Whitespace Validations")
        clinical_page: ClinicalPage = setup_pages["clinical_page"]
        perform_login_and_open_clinical(setup_pages)

        chat_input = clinical_page.get_chat_input()
        send_btn = clinical_page.get_send_message_button()

        # 1. Initially empty: disabled
        chat_input.fill("")
        page.wait_for_timeout(300)
        is_empty_disabled = send_btn.is_disabled()
        v.check("Check if Send button is disabled when chat box is empty", is_empty_disabled)

        # 2. Type only spaces: still disabled
        chat_input.fill("     ")
        page.wait_for_timeout(300)
        is_spaces_disabled = send_btn.is_disabled()
        v.check("Check if whitespace is disallowed (typing only spaces keeps button disabled)", is_spaces_disabled)

        # 3. Type real synthetic clinical text: becomes enabled
        chat_input.fill("What is the recommended clopidogrel alternative for CYP2C19 intermediate metabolizer?")
        page.wait_for_timeout(300)
        is_text_enabled = send_btn.is_enabled()
        v.check("Check if Send button becomes enabled when valid text is typed", is_text_enabled)

        # 4. Clear the box: disabled again
        chat_input.fill("")
        page.wait_for_timeout(300)
        is_cleared_disabled = send_btn.is_disabled()
        v.check("Check if Send button is disabled again when field is cleared", is_cleared_disabled)

        v.assert_all_passed()

    # -------------------------------------------------------------------------
    # Task 2: Enter vs Shift+Enter
    # -------------------------------------------------------------------------

    def test_tc02_enter_vs_shift_enter_behavior(self, page: Page, setup_pages):
        """Task 2: Enter vs Shift+Enter key handling validations."""
        v = StepValidator("Task 2: Enter vs Shift+Enter Validations")
        clinical_page: ClinicalPage = setup_pages["clinical_page"]
        perform_login_and_open_clinical(setup_pages)

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
        line1 = "Synthetic patient with CYP2D6 poor metabolizer profile."
        line2 = "Evaluate codeine pain management safety alternatives."

        chat_input.click()
        chat_input.fill("")
        chat_input.type(line1, delay=20)
        page.keyboard.press("Shift+Enter")
        page.keyboard.type(line2, delay=20)
        page.wait_for_timeout(500)

        input_val = chat_input.input_value()
        has_two_lines = (line1 in input_val) and (line2 in input_val) and ("\n" in input_val)
        v.check("Check if Shift+Enter preserves two separate lines in chat box", has_two_lines)

        no_api_on_shift_enter = len(followup_requests) == 0
        v.check("Check that Shift+Enter sends zero requests to /api/followupchat", no_api_on_shift_enter)

        # 2. Press Enter to submit
        chat_input.fill("")
        submit_prompt = "Explain warfarin dosing guidance for CYP2C9 intermediate metabolizer"
        chat_input.type(submit_prompt, delay=20)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)

        has_followup_request = len(followup_requests) > 0
        v.check("Check that pressing Enter sends request to /api/followupchat", has_followup_request)

        matched_body = [r for r in followup_requests if submit_prompt in r["body"] or "warfarin" in r["body"].lower()]
        has_text_in_payload = len(matched_body) > 0
        v.check("Check that /api/followupchat request body contains the submitted text", has_text_in_payload)

        v.assert_all_passed()

    # -------------------------------------------------------------------------
    # Task 3: Example prompts
    # -------------------------------------------------------------------------

    def test_tc03_suggested_prompts_carousel_navigation(self, page: Page, setup_pages):
        """Task 3: Example prompts carousel validations."""
        v = StepValidator("Task 3: Example Prompts Carousel Validations")
        clinical_page: ClinicalPage = setup_pages["clinical_page"]
        perform_login_and_open_clinical(setup_pages)

        # 1. Click "Suggested Prompts"
        clinical_page.click_suggested_prompts()
        page.wait_for_timeout(500)

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

    # -------------------------------------------------------------------------
    # Task 4: Profile menu
    # -------------------------------------------------------------------------

    def test_tc04_user_profile_menu_elements(self, page: Page, setup_pages):
        """Task 4: User Profile Menu validations."""
        v = StepValidator("Task 4: User Profile Menu Validations")
        clinical_page: ClinicalPage = setup_pages["clinical_page"]
        perform_login_and_open_clinical(setup_pages)

        # 1. Open profile menu from sidebar
        clinical_page.open_user_profile_menu()
        page.wait_for_timeout(600)

        # Validation 1: Menu shows Edit Profile
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
