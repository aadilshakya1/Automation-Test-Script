import re
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from config import BASE_URL, DEFAULT_TIMEOUT

class LoginPage:
    """Page Object for Datum Bio PGX Login and Authentication Page."""

    def __init__(self, page: Page, base_url: str = BASE_URL):
        self.page = page
        self.base_url = base_url

        # Locators (Rule 4: getByRole / getByText for buttons and interactive controls)
        self.username_input = page.locator("#username")
        self.password_input = page.locator("#password")
        self.remember_me_checkbox = page.locator("#rememberMe")
        self.submit_button = page.get_by_role("button", name="Sign In")
        self.error_banner = page.locator(".text-red-600, div.alert-error, [role='alert']")
        self.register_link = page.get_by_text("Register")
        self.forgot_password_link = page.get_by_text("Forgot Password?")
        self.google_login_link = page.get_by_text("Sign in with Google")

    def navigate(self, wait_until: str = "networkidle"):
        """Navigates to the base URL and waits for redirect to login."""
        response = self.page.goto(self.base_url, wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
        return response

    def is_login_page_displayed(self) -> bool:
        """Verifies if core login elements are visible on the page."""
        try:
            self.username_input.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            self.password_input.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            self.submit_button.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            return True
        except PlaywrightTimeoutError:
            return False

    def login(self, username: str, password: str):
        """Fills credentials and clicks Sign In (Rule 4: getByRole / getByText)."""
        self.username_input.fill(username)
        self.password_input.fill(password)
        if self.submit_button.count() > 0:
            self.submit_button.first.click()
        else:
            self.page.get_by_text("Sign In").first.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    def launch_platform(self) -> Page:
        """
        Dismisses onboarding modal if present and clicks 'Launch Platform' to navigate to /clinical.
        Rule 4: Uses get_by_role() and get_by_text(), NEVER CSS classes.
        """
        import re

        # 1. Dismiss "Quick Mode Switch" modal if present
        got_it_btn = self.page.get_by_role("button", name=re.compile(r"Got it,\s*thanks!?", re.I))
        if got_it_btn.count() == 0:
            got_it_btn = self.page.get_by_text(re.compile(r"Got it,\s*thanks!?", re.I))

        try:
            if got_it_btn.count() > 0 and got_it_btn.first.is_visible():
                got_it_btn.first.click()
                self.page.wait_for_timeout(500)
        except Exception:
            pass

        # 2. Look for "Launch Platform" (matches "Launch Platform" and "Launch Platform →")
        launch_btn = self.page.get_by_role("button", name=re.compile(r"Launch Platform", re.I))
        if launch_btn.count() == 0:
            launch_btn = self.page.get_by_text(re.compile(r"Launch Platform", re.I))

        launch_btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)

        # 3. Check if target opens in new tab or same tab
        is_new_tab = launch_btn.first.get_attribute("target") == "_blank"
        if is_new_tab:
            with self.page.context.expect_page() as new_page_info:
                launch_btn.first.click()
            active_page = new_page_info.value
            active_page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
            return active_page
        else:
            launch_btn.first.click()
            self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
            return self.page

    

    def get_error_message(self) -> str:
        """Retrieves text from the error banner if displayed."""
        try:
            self.error_banner.first.wait_for(state="visible", timeout=5000)
            return self.error_banner.first.inner_text().strip()
        except PlaywrightTimeoutError:
            return ""

    def click_register(self):
        """Clicks the Register link and waits for registration page."""
        self.register_link.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    def click_forgot_password(self):
        """Clicks the Forgot Password link and waits for reset page."""
        self.forgot_password_link.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

