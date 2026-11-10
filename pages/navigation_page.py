import re
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from config import BASE_URL, DEFAULT_TIMEOUT

class NavigationPage:
    """
    Page Object for 'Pages and Navigation' tasks.
    Follows Rule 4: Buttons/links are located using get_by_role() or get_by_text(), NEVER CSS classes.
    """

    def __init__(self, page: Page, base_url: str = BASE_URL):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def dismiss_modal_if_present(self):
        """Dismisses the 'Got it, thanks!' Quick Mode Switch modal if present."""
        try:
            # Check for "Got it, thanks!" button
            got_it_btn = self.page.get_by_role("button", name=re.compile(r"Got it,\s*thanks!?", re.I))
            if got_it_btn.count() == 0:
                got_it_btn = self.page.get_by_text(re.compile(r"Got it,\s*thanks!?", re.I))
            if got_it_btn.count() > 0 and got_it_btn.first.is_visible():
                got_it_btn.first.click(force=True)
                self.page.wait_for_timeout(400)
                return

            # Check for close 'X' button on modal if Got It button isn't found
            close_btn = self.page.get_by_role("button", name=re.compile(r"close", re.I))
            if close_btn.count() > 0 and close_btn.first.is_visible():
                close_btn.first.click(force=True)
                self.page.wait_for_timeout(400)
        except Exception:
            pass

    # ----------------------------------------------------
    # Task 1 & 2: Landing Page Locators & Actions
    # ----------------------------------------------------

    def get_launch_platform_btn(self):
        """Locates 'Launch Platform' button (Rule 4: get_by_role / get_by_text)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Launch Platform", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Launch Platform", re.I))
        return btn

    def get_start_learning_btn(self):
        """Locates 'Start Learning' button (Rule 4: get_by_role / get_by_text)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Start Learning", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Start Learning", re.I))
        return btn

    def navigate_to_education(self):
        """
        Navigates to /education cleanly.
        Direct URL navigation via page.goto('/education') triggers a server/SPA redirect back to '/',
        refreshing the page. Therefore, if not already on /education, we navigate via 'Start Learning'.
        """
        if self.page.url.rstrip("/").endswith("/education"):
            return

        # Ensure we are on the landing page first
        if not self.page.url.rstrip("/") == self.base_url.rstrip("/"):
            self.page.goto(self.base_url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT)

        self.dismiss_modal_if_present()

        start_btn = self.get_start_learning_btn()
        start_btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        start_btn.first.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
        self.dismiss_modal_if_present()

    def click_education_tab(self):
        """Clicks the 'Education' tab in responsive/phone view (Rule 4)."""
        tab = self.page.get_by_role("tab", name=re.compile(r"^Education", re.I))
        if tab.count() == 0:
            tab = self.page.get_by_role("button", name=re.compile(r"^Education", re.I))
        if tab.count() == 0:
            tab = self.page.get_by_text(re.compile(r"^Education", re.I))
        tab.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        tab.first.click()
        self.page.wait_for_timeout(400)

    def click_clinical_tab(self):
        """Clicks the 'Clinical' tab in responsive/phone view (Rule 4)."""
        tab = self.page.get_by_role("tab", name=re.compile(r"^Clinical", re.I))
        if tab.count() == 0:
            tab = self.page.get_by_role("button", name=re.compile(r"^Clinical", re.I))
        if tab.count() == 0:
            tab = self.page.get_by_text(re.compile(r"^Clinical", re.I))
        tab.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        tab.first.click()
        self.page.wait_for_timeout(400)

    # ----------------------------------------------------
    # Task 3: Education Cards Locators & Actions
    # ----------------------------------------------------

    def click_take_quiz(self):
        """Clicks 'Take Quiz' button on /education (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Take Quiz", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Take Quiz", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    def click_education_card(self, card_name: str):
        """Clicks an educational card by name (e.g. Drug Monograph, Lectures, Patient Case Simulation)."""
        card = self.page.get_by_role("button", name=re.compile(rf"{re.escape(card_name)}", re.I))
        if card.count() == 0:
            card = self.page.get_by_text(re.compile(rf"{re.escape(card_name)}", re.I))
        card.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        card.first.click()
        self.page.wait_for_timeout(500)

    # ----------------------------------------------------
    # Task 4: Floating Mode Switch Locators & Actions
    # ----------------------------------------------------

    def get_floating_mode_switch_btn(self):
        """
        Locates the floating mode switch button on screen.
        Accepts role='button' or title/aria-label containing switch mode text.
        """
        btn = self.page.get_by_role("button", name=re.compile(r"Switch to (education|clinical) mode", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Switch to (education|clinical) mode", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label*='Switch to' i], button[title*='Switch to' i]")
        return btn

    def click_switch_to_education_mode(self):
        """Clicks 'Switch to education mode' floating button (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Switch to education mode", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Switch to education mode", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label*='education' i], button[title*='education' i]")
        if btn.count() == 0:
            # Fallback to floating toggle button
            btn = self.page.locator("button").filter(has=self.page.locator("svg")).last
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    def click_switch_to_clinical_mode(self):
        """Clicks 'Switch to clinical mode' floating button (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Switch to clinical mode", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Switch to clinical mode", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label*='clinical' i], button[title*='clinical' i]")
        if btn.count() == 0:
            btn = self.page.locator("button").filter(has=self.page.locator("svg")).last
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    # ----------------------------------------------------
    # Task 5: Learn More Link
    # ----------------------------------------------------

    def click_learn_more(self) -> Page:
        """
        Clicks 'Learn more' in the sidebar on /clinical.
        Handles both current tab navigation and new tab targets.
        """
        link = self.page.get_by_role("link", name=re.compile(r"Learn more", re.I))
        if link.count() == 0:
            link = self.page.get_by_text(re.compile(r"Learn more", re.I))

        link.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        is_new_tab = link.first.get_attribute("target") == "_blank"

        if is_new_tab:
            with self.page.context.expect_page() as new_page_info:
                link.first.click()
            new_page = new_page_info.value
            new_page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
            return new_page
        else:
            link.first.click()
            self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
            return self.page
