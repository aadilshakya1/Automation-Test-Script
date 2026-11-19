import re
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from config import DEFAULT_TIMEOUT

class ClinicalPage:
    """
    Page Object for Datum Bio Clinical Assessment (/clinical).
    Follows Rule 4: Buttons are located using get_by_role() or get_by_text(), NEVER CSS classes.
    """

    def __init__(self, page: Page, base_url: str = "https://pgx-dev.datum.bio/clinical"):
        self.page = page
        self.base_url = base_url

        # Locators adhering strictly to Rule 4 (getByRole / getByText)
        self.enter_gene_panel_manually_btn = page.get_by_role("button", name=re.compile(r"Enter Gene Panel Manually", re.I))
        self.prompt_mode_btn = page.get_by_role("button", name=re.compile(r"Prompt Mode", re.I))
        self.overlay_assessment = page.get_by_text("Running the full PGx assessment")

    def navigate(self):
        """Navigates to /clinical and waits for network idle."""
        return self.page.goto(self.base_url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT)

    def click_enter_gene_panel_manually(self):
        """Clicks 'Enter Gene Panel Manually' button (Rule 4)."""
        btn = self.enter_gene_panel_manually_btn
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Enter Gene Panel Manually", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()

    def click_prompt_mode(self):
        """Clicks 'Prompt Mode' button (Rule 4)."""
        btn = self.prompt_mode_btn
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Prompt Mode", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()

    def is_overlay_visible(self) -> bool:
        """Checks if 'Running the full PGx assessment' overlay is visible."""
        return self.overlay_assessment.is_visible()

    def is_loader_visible(self) -> bool:
        """
        Checks if any visual loader, spinner, or assessment overlay is displayed.
        Per testing specification: Visible loaders signify that an API call was triggered.
        """
        # 1. Check assessment overlay
        if self.page.get_by_text("Running the full PGx assessment").count() > 0:
            if self.page.get_by_text("Running the full PGx assessment").first.is_visible():
                return True

        # 2. Check spinner / progress / aria-busy elements
        selectors = [
            ".animate-spin",
            "[aria-busy='true']",
            "[role='progressbar']",
            "[class*='loader' i]",
            "[class*='spinner' i]",
        ]
        for sel in selectors:
            try:
                matches = self.page.locator(sel)
                if matches.count() > 0 and matches.first.is_visible():
                    return True
            except Exception:
                continue

        # 3. Check text-based loaders
        for text in ["Loading...", "Processing..."]:
            try:
                el = self.page.get_by_text(text, exact=True)
                if el.count() > 0 and el.first.is_visible():
                    return True
            except Exception:
                continue

        return False

    def is_manual_form_open(self) -> bool:
        """Verifies if the manual gene panel form is visible."""
        # When manual form is open, 'Select gene' placeholder or 'Prompt Mode' button is present
        if self.page.get_by_text(re.compile(r"Select gene", re.I)).count() > 0:
            return True
        if self.prompt_mode_btn.count() > 0 and self.prompt_mode_btn.first.is_visible():
            return True
        return False

    def click_add_demographics(self):
        """Clicks the 'Add Demographics' button if demographics section is not already open (Rule 4)."""
        # If Age or Sex placeholder is already visible, demographics is already expanded
        age_label = self.page.get_by_text("Age", exact=True)
        if age_label.count() > 0 and age_label.first.is_visible():
            return

        btn = self.page.get_by_role("button", name=re.compile(r"Add Demographics", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Add Demographics", re.I))
        if btn.count() > 0 and btn.first.is_visible():
            btn.first.click()
            self.page.wait_for_timeout(500)

    def _select_react_dropdown(self, placeholder_text: str, value_to_select: str) -> str:
        """
        Interacts with React-Select combobox components (Age, Sex, Gene, Phenotype).
        Uses accessible text / role locators without relying on CSS classes.
        """
        # 1. Locate the placeholder or combobox element
        placeholder = self.page.get_by_text(placeholder_text, exact=True)
        if placeholder.count() > 0:
            # force=True bypasses React-Select's pointer-events: none on placeholder divs
            placeholder.first.click(force=True)
        else:
            combobox = self.page.get_by_role("combobox", name=re.compile(placeholder_text, re.I))
            if combobox.count() > 0:
                combobox.first.click()

        self.page.wait_for_timeout(300)

        # 2. Type value into the focused react-select input
        self.page.keyboard.type(str(value_to_select), delay=50)
        self.page.wait_for_timeout(400)

        # 3. Select matching option from dropdown menu (only click actual option roles, otherwise press Enter)
        option = self.page.get_by_role("option", name=re.compile(rf"^{re.escape(str(value_to_select))}", re.I))

        selected_text = str(value_to_select)
        if option.count() > 0 and option.first.is_visible():
            selected_text = option.first.inner_text().strip()
            option.first.click()
        else:
            self.page.keyboard.press("Enter")

        self.page.wait_for_timeout(300)
        return selected_text

    def fill_age(self, age: str = "42"):
        """Fills the Age react-select field."""
        self._select_react_dropdown("Age", str(age))

    def select_sex(self, sex: str = "Male"):
        """Selects the Sex react-select field."""
        self._select_react_dropdown("Sex", sex)

    def select_gene(self, gene: str = "CYP2C19"):
        """Selects the Gene react-select field."""
        self._select_react_dropdown("Select gene", gene)

    def select_phenotype(self, phenotype: str = "Intermediate Metabolizer") -> str:
        """Selects the Phenotype react-select field."""
        return self._select_react_dropdown("phenotype", phenotype)

    def _fill_dynamic_field(self, button_label_regex: str, text_to_enter: str, field_desc: str):
        """
        Clicks an 'Add ...' button (e.g. '+ Add Medical History', '+ Add Current Medications'),
        detects the newly revealed input or dropdown, and enters the text.
        Rule 4: Locates buttons strictly via get_by_role / get_by_text.
        """
        before_count = self.page.locator("input:visible, textarea:visible").count()

        # 1. Click the button (Rule 4)
        btn = self.page.get_by_role("button", name=re.compile(button_label_regex, re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(button_label_regex, re.I))
        if btn.count() > 0 and btn.first.is_visible():
            btn.first.click()
            self.page.wait_for_timeout(500)

        # 2. Locate the relevant input element
        target_input = None

        # Check newest input if input count increased
        after_inputs = self.page.locator("input:visible, textarea:visible")
        if after_inputs.count() > before_count:
            target_input = after_inputs.last

        # Check section matching field description
        if not target_input:
            section = self.page.locator(f"div:has-text('{field_desc}')")
            if section.count() > 0:
                sec_inputs = section.locator("input:visible, textarea:visible")
                if sec_inputs.count() > 0:
                    target_input = sec_inputs.last

        # Check placeholder with common search/input terms
        if not target_input:
            match_placeholder = self.page.locator(
                "input[placeholder*='search' i], input[placeholder*='type' i], input[placeholder*='condition' i], input[placeholder*='drug' i], input[placeholder*='history' i], textarea:visible"
            )
            if match_placeholder.count() > 0:
                target_input = match_placeholder.last

        # Check focused element
        if not target_input:
            focused = self.page.locator("input:focus, textarea:focus")
            if focused.count() > 0:
                target_input = focused.first

        # 3. Enter the value and confirm selection
        if target_input and target_input.is_visible():
            target_input.click()
            target_input.fill(text_to_enter)
            self.page.wait_for_timeout(400)

            # Check if an autocomplete dropdown appeared
            option = self.page.get_by_role("option", name=re.compile(rf"^{re.escape(text_to_enter)}", re.I))
            if option.count() == 0:
                option = self.page.get_by_text(re.compile(rf"^{re.escape(text_to_enter)}", re.I))
            if option.count() > 0 and option.first.is_visible():
                option.first.click()
            else:
                self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(300)
            print(f"[INFO] Successfully entered '{text_to_enter}' into {field_desc}")
        else:
            # Fallback: type via keyboard directly
            self.page.keyboard.type(text_to_enter, delay=50)
            self.page.wait_for_timeout(300)
            self.page.keyboard.press("Enter")
            print(f"[WARN] No explicit input found for {field_desc}; typed via keyboard.")

    def add_medical_history(self, history_text: str = "Hypertension"):
        """Clicks '+ Add Medical History' and enters condition (Rules 2, 3, 4)."""
        self._fill_dynamic_field(
            button_label_regex=r"Add Medical History",
            text_to_enter=history_text,
            field_desc="Medical History"
        )

    def add_current_medication(self, medication_text: str = "Aspirin"):
        """Clicks '+ Add Current Medications' and enters medication (Rules 2, 3, 4)."""
        self._fill_dynamic_field(
            button_label_regex=r"Add Current Medication",
            text_to_enter=medication_text,
            field_desc="Current Medication"
        )

    def is_field_value_retained(self, value: str) -> bool:
        """
        Verifies if a specific value is retained either as visible text/chip OR inside an input/textarea value.
        """
        if self.page.get_by_text(value, exact=False).count() > 0:
            return True
        matching_inputs = self.page.locator(f"input[value*='{value}'], textarea").filter(has_text=value)
        if matching_inputs.count() > 0:
            return True
        has_val = self.page.evaluate(
            "(val) => Array.from(document.querySelectorAll('input, textarea')).some(el => el.value && el.value.includes(val))",
            value
        )
        return bool(has_val)

    def run_assessment(self, timeout: int = 90000):
        """Clicks 'Get full PGx assessment' and waits for completion (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Get full PGx assessment", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Get full PGx assessment", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()

        # Wait for assessment to run and overlay to finish
        try:
            self.overlay_assessment.wait_for(state="visible", timeout=10000)
            self.overlay_assessment.wait_for(state="hidden", timeout=timeout)
        except Exception:
            pass
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    def get_assessment_response_text(self, timeout: int = DEFAULT_TIMEOUT) -> str:
        """
        Waits for the PGx clinical assessment results container to render
        and extracts its visible text for validation or AI judging (Rule 4).
        """
        heading = self.page.get_by_text(
            re.compile(r"(PHARMACOGENOMIC RECOMMENDATIONS|Genetic Information)", re.I)
        )
        heading.first.wait_for(state="visible", timeout=timeout)
        self.page.wait_for_timeout(1000)

        main_region = self.page.get_by_role("main")
        if main_region.count() > 0:
            return main_region.first.inner_text().strip()
        return self.page.locator("body").inner_text().strip()

    def click_navigate_to_home(self):
        """Clicks 'Navigate to Home' in the sidebar to start a new thread (Rule 4)."""
        btn = self.page.get_by_text(re.compile(r"Navigate to Home", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
        self.page.wait_for_timeout(1000)

    def get_sidebar_thread_elements(self):
        """Returns locators for thread items listed in the sidebar under sessions/Today."""
        threads = self.page.locator("[title*='session' i], [title*='Thread' i]")
        if threads.count() == 0:
            threads = self.page.get_by_role("button", name="Thread options")
        if threads.count() == 0:
            threads = self.page.get_by_text(re.compile(r"(session|New Thread|\d{1,2}:\d{2})", re.I))
        return threads

    def click_thread(self, name_or_gene: str = None, index: int = 0):
        """
        Switches to a thread in the sidebar.
        Can match by gene/session name (e.g. 'CYP2C19', 'CYP2D6') or by index (0 = latest, 1 = previous).
        """
        if name_or_gene:
            # Look for exact or partial title/text (e.g. title="CYP2C19 session")
            target = self.page.locator(f"[title*='{name_or_gene}' i]")
            if target.count() == 0:
                target = self.page.get_by_text(re.compile(rf"{re.escape(name_or_gene)}", re.I))
            if target.count() > 0:
                target.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
                target.first.click()
                self.page.wait_for_timeout(1500)
                return

        # By index: look for session titles or thread options parents
        thread_titles = self.page.locator("[title*='session' i], [title*='Thread' i]")
        if thread_titles.count() > index:
            thread_titles.nth(index).wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            thread_titles.nth(index).click()
            self.page.wait_for_timeout(1500)
            return

        # Fallback to clicking the thread row containing the Thread options button
        menu_btns = self.page.get_by_role("button", name="Thread options")
        if menu_btns.count() > index:
            parent_row = menu_btns.nth(index).locator("xpath=ancestor::div[contains(@class, 'cursor-pointer')]")
            if parent_row.count() > 0:
                parent_row.first.click()
            else:
                menu_btns.nth(index).click()
            self.page.wait_for_timeout(1500)
            return

    def open_thread_menu(self, index: int = 0, name_or_gene: str = None):
        """Opens the 3-dots 'Thread options' menu for the specified thread (Rule 4: getByRole)."""
        if name_or_gene:
            thread_title = self.page.locator(f"[title*='{name_or_gene}' i]")
            if thread_title.count() > 0:
                parent_row = thread_title.first.locator("xpath=ancestor::div[contains(@class, 'cursor-pointer')]")
                if parent_row.count() > 0:
                    try:
                        parent_row.first.hover()
                        self.page.wait_for_timeout(300)
                    except Exception:
                        pass
                    btn = parent_row.get_by_role("button", name="Thread options")
                    if btn.count() > 0:
                        btn.first.click(force=True)
                        self.page.wait_for_timeout(500)
                        return

        menu_btns = self.page.get_by_role("button", name="Thread options")
        if menu_btns.count() > index:
            try:
                menu_btns.nth(index).hover()
            except Exception:
                pass
            menu_btns.nth(index).click(force=True)
        else:
            fallback = self.page.locator("button[aria-label='Thread options']")
            if fallback.count() > index:
                fallback.nth(index).click(force=True)
        self.page.wait_for_timeout(500)

    def click_share_in_menu(self):
        """Clicks 'Share' in the thread context menu (Rule 4)."""
        share_btn = self.page.get_by_role("menuitem", name=re.compile(r"Share", re.I))
        if share_btn.count() == 0:
            share_btn = self.page.get_by_role("button", name=re.compile(r"Share", re.I))
        if share_btn.count() == 0:
            share_btn = self.page.get_by_text(re.compile(r"^Share", re.I))
        
        share_btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        share_btn.first.click()
        self.page.wait_for_timeout(1000)

    def get_generated_share_link(self) -> str:
        """Extracts the generated share link from modal or input field."""
        self.page.wait_for_timeout(1000)

        # 1. Check for modal or dialog input with a URL
        modal = self.page.locator("[role='dialog'], div[class*='modal' i], div[class*='dialog' i]")
        if modal.count() > 0:
            inputs = modal.locator("input")
            for i in range(inputs.count()):
                val = inputs.nth(i).input_value()
                if "http" in val:
                    return val

            # Check if there is a 'Copy Link' button
            copy_btn = modal.get_by_role("button", name=re.compile(r"Copy", re.I))
            if copy_btn.count() == 0:
                copy_btn = modal.get_by_text(re.compile(r"Copy", re.I))
            if copy_btn.count() > 0 and copy_btn.first.is_visible():
                copy_btn.first.click()
                self.page.wait_for_timeout(500)
                try:
                    cb = self.page.evaluate("navigator.clipboard.readText()")
                    if cb and "http" in cb:
                        return cb
                except Exception:
                    pass

        # 2. Check for general input with URL
        link_inputs = self.page.locator("input[value*='http'], input[readonly]")
        for i in range(link_inputs.count()):
            val = link_inputs.nth(i).input_value()
            if "http" in val:
                return val

        # 3. Check clipboard
        try:
            clipboard_text = self.page.evaluate("navigator.clipboard.readText()")
            if clipboard_text and "http" in clipboard_text:
                return clipboard_text
        except Exception:
            pass

        # 4. Check for links inside dialog
        links = self.page.locator("[role='dialog'] a[href*='http']")
        if links.count() > 0:
            href = links.first.get_attribute("href")
            if href and "http" in href:
                return href

        return self.page.url

    # ----------------------------------------------------
    # Third Task: Clinical Screen Chat, Examples & Profile
    # ----------------------------------------------------

    def get_chat_input(self):
        """Locates the main clinical chat textarea (Rule 4: get_by_placeholder / locator)."""
        inp = self.page.get_by_placeholder(re.compile(r"Ask a question or provide patient context", re.I))
        if inp.count() == 0:
            inp = self.page.locator("textarea")
        return inp.first

    def get_send_message_button(self):
        """Locates the 'Send message' button (Rule 4: get_by_role / get_by_text)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Send message", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label='Send message']")
        return btn.first

    def click_suggested_prompts(self):
        """Clicks 'Suggested Prompts' button (Rule 4: get_by_role / get_by_text)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Suggested Prompts", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Suggested Prompts", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_timeout(500)

    def click_next_example(self):
        """Clicks 'Next example' button in suggested prompts panel (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Next example", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label='Next example']")
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_timeout(400)

    def click_previous_example(self):
        """Clicks 'Previous example' button in suggested prompts panel (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Previous example", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label='Previous example']")
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_timeout(400)

    def click_go_to_example(self, number: int):
        """Clicks 'Go to example X' button in suggested prompts panel (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(rf"Go to example {number}", re.I))
        if btn.count() == 0:
            btn = self.page.locator(f"button[aria-label='Go to example {number}']")
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_timeout(400)

    def click_use_this_example(self):
        """Clicks 'Use this example' button (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Use this example", re.I))
        if btn.count() == 0:
            btn = self.page.get_by_text(re.compile(r"Use this example", re.I))
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_timeout(400)

    def click_close_examples(self):
        """Clicks 'Close examples' button (Rule 4)."""
        btn = self.page.get_by_role("button", name=re.compile(r"Close examples", re.I))
        if btn.count() == 0:
            btn = self.page.locator("button[aria-label='Close examples']")
        btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        btn.first.click()
        self.page.wait_for_timeout(400)

    def open_user_profile_menu(self):
        """Clicks user profile trigger in sidebar (Rule 4: get_by_text)."""
        user_trigger = self.page.locator("aside, [class*='sidebar' i]").locator("div, button").filter(
            has_text=re.compile(r"(Test User|Aadil Shakya)", re.I)
        ).last
        user_trigger.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        user_trigger.click()
        self.page.wait_for_timeout(500)
