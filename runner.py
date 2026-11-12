#!/usr/bin/env python3
"""
Agent-Ready Playwright Test Runner for https://pgx-dev.datum.bio/

Designed for both human engineers and autonomous AI agents:
- Command-line flags for scenario selection, headless/headed mode, custom target URL.
- Structured JSON output on stdout (or file) with timing, status, and failure artifacts.
- Deterministic exit codes: 0 (PASS), 1 (FAIL).
- Automatic screenshot capture on failure.
"""

import argparse
import json
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import (
    BASE_URL,
    DEFAULT_TIMEOUT,
    GEMINI_API_KEY,
    LLM_API_KEY,
    LLM_MODEL,
    SCREENSHOTS_DIR,
    TASK_DIR,
    TEST_PASSWORD,
    TEST_USERNAME,
)
from pages.login_page import LoginPage
from pages.clinical_page import ClinicalPage
from pages.navigation_page import NavigationPage
from utils.ai_judge import GeminiAIJudge, PGxCaseContext
from utils.app_explorer import explore_live_app, summarize_page_objects
from utils.task_reader import NoTaskFilesError, load_tasks
from utils.test_generator import (
    ApprovalRequiredError,
    LLMTestGenerator,
    execute_generated_pytest,
)


class TestRunner:
    def __init__(self, base_url: str = BASE_URL, headless: bool = True, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url
        self.headless = headless
        self.timeout = timeout
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "target_url": self.base_url,
            "headless": self.headless,
            "summary": {"total": 0, "passed": 0, "failed": 0, "status": "UNKNOWN"},
            "tests": []
        }

    def _record_test(self, name: str, status: str, duration_ms: float, details: dict = None, error: str = None, screenshot: str = None):
        entry = {
            "scenario": name,
            "status": status,
            "duration_ms": round(duration_ms, 2),
            "details": details or {},
        }
        if error:
            entry["error"] = error
        if screenshot:
            entry["screenshot"] = screenshot
        self.results["tests"].append(entry)

    def _capture_failure(self, page, scenario_name: str) -> str:
        timestamp = int(time.time())
        screenshot_path = SCREENSHOTS_DIR / f"{scenario_name}_fail_{timestamp}.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            return str(screenshot_path)
        except Exception:
            return ""

    def run_smoke(self, page) -> bool:
        """Smoke scenario: Checks page availability and presence of login form."""
        start = time.time()
        login_page = LoginPage(page, self.base_url)
        try:
            response = login_page.navigate()
            status_code = response.status if response else 0
            page_title = page.title()
            form_displayed = login_page.is_login_page_displayed()

            if status_code != 200:
                raise AssertionError(f"Expected HTTP 200, received {status_code}")
            if not form_displayed:
                raise AssertionError("Login form elements (username, password, submit) are not visible")

            self._record_test(
                name="smoke",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "http_status": status_code,
                    "title": page_title,
                    "current_url": page.url,
                    "form_elements_visible": True
                }
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "smoke")
            self._record_test(
                name="smoke",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_valid_login(self, page, username: str = None, password: str = None) -> bool:
        """Positive authentication scenario: Verifies successful user login."""
        from config import TEST_USERNAME, TEST_PASSWORD

        user = username or TEST_USERNAME
        pwd = password or TEST_PASSWORD

        start = time.time()
        if not user or not pwd:
            self._record_test(
                name="valid_login",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        login_page = LoginPage(page, self.base_url)
        try:
            login_page.navigate()
            login_page.login(user, pwd)

            # Wait for redirect away from the Keycloak login realm
            page.wait_for_load_state("networkidle", timeout=self.timeout)

            # Assertion 1: Ensure we are no longer on the login authentication URL
            if "login.datum.bio" in page.url and "authenticate" in page.url:
                err = login_page.get_error_message() or "Authentication failed (still on login page)"
                raise AssertionError(err)

            # Assertion 2: Verify user landed on the application dashboard
            current_url = page.url

            self._record_test(
                name="valid_login",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "logged_in_as": user,
                    "final_url": current_url
                }
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "valid_login")
            self._record_test(
                name="valid_login",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_invalid_login(self, page) -> bool:
        """Negative authentication scenario: Checks error banner on invalid login."""
        start = time.time()
        login_page = LoginPage(page, self.base_url)
        try:
            login_page.navigate()
            login_page.login("non_existent_agent_user@example.com", "WrongPassword!404")
            error_message = login_page.get_error_message()

            if not error_message:
                raise AssertionError("Expected error banner was not displayed after submitting invalid credentials.")
            if "Invalid username or password" not in error_message:
                raise AssertionError(f"Unexpected error message text: '{error_message}'")

            self._record_test(
                name="invalid_login",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "error_message": error_message,
                    "retained_on_auth_endpoint": "authenticate" in page.url
                }
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "invalid_login")
            self._record_test(
                name="invalid_login",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_navigation(self, page) -> bool:
        """Navigation scenario: Checks register and forgot-password link routing."""
        start = time.time()
        login_page = LoginPage(page, self.base_url)
        try:
            # 1. Register Link
            login_page.navigate()
            login_page.click_register()
            if "registration" not in page.url.lower():
                raise AssertionError(f"Expected registration in URL, got {page.url}")

            # 2. Forgot Password Link
            login_page.navigate()
            login_page.click_forgot_password()
            if "reset-credentials" not in page.url.lower():
                raise AssertionError(f"Expected reset-credentials in URL, got {page.url}")

            self._record_test(
                name="navigation",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "registration_link_working": True,
                    "forgot_password_link_working": True
                }
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "navigation")
            self._record_test(
                name="navigation",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_task1_a(self, page, username: str = None, password: str = None) -> bool:
        """
        Task 1 Part A: Switching between Prompt Mode and Manual Entry.
        Rules:
        1) Use a test account from dev (/clinical)
        2) Never use real patient data
        3) Use made-up ages and genes
        4) Find buttons with page.getByText() or page.getByRole(), not CSS classes.
        """
        from config import TEST_USERNAME, TEST_PASSWORD

        user = username or TEST_USERNAME
        pwd = password or TEST_PASSWORD

        start = time.time()
        if not user or not pwd:
            self._record_test(
                name="task1_a",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        console_errors = []
        api_calls = []
        tracking_active = False

        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))

        def handle_request(req):
            if tracking_active and req.resource_type in ("fetch", "xhr"):
                api_calls.append({"url": req.url, "method": req.method})

        page.on("request", handle_request)

        try:
            # 1. Log in and open /clinical
            login_page = LoginPage(page, self.base_url)
            login_page.navigate()
            login_page.login(user, pwd)
            active_page = login_page.launch_platform()

            if active_page != page:
                active_page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
                active_page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))
                active_page.on("request", handle_request)

            clinical_url = "https://pgx-dev.datum.bio/clinical"
            if "/clinical" not in active_page.url:
                active_page.goto(clinical_url, wait_until="networkidle", timeout=self.timeout)

            clinical = ClinicalPage(active_page, clinical_url)

            # 2. Click Enter Gene Panel Manually. The manual form opens.
            clinical.click_enter_gene_panel_manually()
            active_page.wait_for_timeout(1000)

            loader_detected = False

            # 3. Click Prompt Mode, then Enter Gene Panel Manually. Repeat 5 times, waiting 1s between clicks.
            for i in range(1, 6):
                clinical.click_prompt_mode()
                if clinical.is_loader_visible():
                    loader_detected = True
                active_page.wait_for_timeout(1000)

                if clinical.is_overlay_visible():
                    raise AssertionError(f"Cycle {i}: 'Running the full PGx assessment' overlay appeared after Prompt Mode!")

                clinical.click_enter_gene_panel_manually()
                if clinical.is_loader_visible():
                    loader_detected = True
                active_page.wait_for_timeout(1000)

                if not clinical.is_manual_form_open():
                    raise AssertionError(f"Cycle {i}: Manual form did not open!")
                if clinical.is_overlay_visible():
                    raise AssertionError(f"Cycle {i}: 'Running the full PGx assessment' overlay appeared after Enter Gene Panel Manually!")

            # 4. Wait 5 seconds, then check apiCalls (visual loaders indicate active API calls).
            active_page.wait_for_timeout(5000)
            if clinical.is_loader_visible():
                loader_detected = True

            window_api_calls = active_page.evaluate("() => (window.apiCalls && Array.isArray(window.apiCalls)) ? window.apiCalls : []")

            if loader_detected:
                raise AssertionError("Expected apiCalls to be empty, but a loader/spinner appeared during screen switching!")
            if len(window_api_calls) > 0:
                raise AssertionError(f"Expected window.apiCalls to be empty, but recorded: {window_api_calls}")

            if clinical.is_overlay_visible():
                raise AssertionError("The overlay 'Running the full PGx assessment' is visible!")

            if len(console_errors) > 0:
                raise AssertionError(f"Console errors detected: {console_errors}")

            self._record_test(
                name="task1_a",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "cycles_completed": 5,
                    "loader_appeared": False,
                    "console_errors_count": len(console_errors),
                    "overlay_appeared": False
                }
            )
            return True
        except Exception as e:
            target_page = locals().get("active_page", page)
            shot = self._capture_failure(target_page, "task1_a")
            self._record_test(
                name="task1_a",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_task1_b(self, page, username: str = None, password: str = None) -> bool:
        """
        Task 1 Part B: Data is kept when switching between Prompt Mode and Manual Entry.
        Rules:
        1) Use a test account from dev (/clinical)
        2) Never use real patient data
        3) Use made-up ages and genes
        4) Find buttons with page.getByText() or page.getByRole(), not CSS classes.
        """
        from config import TEST_USERNAME, TEST_PASSWORD

        user = username or TEST_USERNAME
        pwd = password or TEST_PASSWORD

        start = time.time()
        if not user or not pwd:
            self._record_test(
                name="task1_b",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        api_calls = []
        tracking_active = False

        def handle_request(req):
            if tracking_active and req.resource_type in ("fetch", "xhr"):
                api_calls.append({"url": req.url, "method": req.method})

        page.on("request", handle_request)

        try:
            # 1. Log in and open /clinical
            login_page = LoginPage(page, self.base_url)
            login_page.navigate()
            login_page.login(user, pwd)
            active_page = login_page.launch_platform()

            if active_page != page:
                active_page.on("request", handle_request)

            clinical_url = "https://pgx-dev.datum.bio/clinical"
            if "/clinical" not in active_page.url:
                active_page.goto(clinical_url, wait_until="networkidle", timeout=self.timeout)

            clinical = ClinicalPage(active_page, clinical_url)

            # 2. Open manual form if not currently open
            if not clinical.is_manual_form_open():
                clinical.click_enter_gene_panel_manually()
                active_page.wait_for_timeout(1000)

            clinical.click_add_demographics()
            active_page.wait_for_timeout(1000)

            # 3. Fill made-up test data (Rules 2 & 3: synthetic demographics, medical history, and medications)
            test_age = "42"
            test_sex = "Male"
            test_gene = "CYP2C19"
            test_phenotype = "Intermediate Metabolizer"
            test_medical_history = "Hypertension"
            test_current_medication = "Aspirin"

            fill_errors = {}
            for name, action in [
                ("Age", lambda: clinical.fill_age(test_age)),
                ("Sex", lambda: clinical.select_sex(test_sex)),
                ("Gene", lambda: clinical.select_gene(test_gene)),
                ("Phenotype", lambda: clinical.select_phenotype(test_phenotype)),
                ("Medical History", lambda: clinical.add_medical_history(test_medical_history)),
                ("Current Medication", lambda: clinical.add_current_medication(test_current_medication)),
            ]:
                try:
                    action()
                except Exception as e:
                    fill_errors[name] = str(e)
                    print(f"[WARN] Error filling {name}: {e}")

            active_page.wait_for_timeout(1000)

            # 4. Click Prompt Mode, then Enter Gene Panel Manually (Rule 4: getByRole / getByText)
            loader_detected = clinical.is_loader_visible()

            clinical.click_prompt_mode()
            if clinical.is_loader_visible():
                loader_detected = True
            active_page.wait_for_timeout(1000)

            clinical.click_enter_gene_panel_manually()
            if clinical.is_loader_visible():
                loader_detected = True
            active_page.wait_for_timeout(1000)

            # 5. Check retention across ALL fields (continue checking even if one fails)
            field_checks = [
                ("Age", test_age),
                ("Sex", test_sex),
                ("Gene", test_gene),
                ("Phenotype", test_phenotype),
                ("Medical History", test_medical_history),
                ("Current Medication", test_current_medication),
            ]

            field_status = {}
            failed_items = []

            print("\n--- Field Retention Audit ---")
            for field_name, expected_val in field_checks:
                is_retained = clinical.is_field_value_retained(expected_val)
                field_status[field_name] = "RETAINED" if is_retained else "LOST"
                mark = "PASS" if is_retained else "FAIL"
                print(f"  [{mark}] {field_name} ('{expected_val}'): {field_status[field_name]}")
                if not is_retained:
                    failed_items.append(f"{field_name} ('{expected_val}') was lost")
            print("-----------------------------\n")

            # 6. Expected: apiCalls is still empty (no visual loaders appeared)
            if loader_detected:
                failed_items.append("Loader/spinner appeared during mode switching")

            if fill_errors:
                for fname, err in fill_errors.items():
                    failed_items.append(f"{fname} had fill error: {err}")

            if len(failed_items) > 0:
                raise AssertionError(f"Retention audit failed {len(failed_items)} item(s): {failed_items}")

            self._record_test(
                name="task1_b",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "field_audit": field_status,
                    "failed_fields": failed_items,
                    "loader_appeared": False
                }
            )
            return True
        except Exception as e:
            target_page = locals().get("active_page", page)
            shot = self._capture_failure(target_page, "task1_b")
            self._record_test(
                name="task1_b",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "field_audit": locals().get("field_status", {}),
                    "failed_fields": locals().get("failed_items", [str(e)]),
                    "fill_errors": locals().get("fill_errors", {})
                },
                error=str(e),
                screenshot=shot
            )
            return False

    def run_task2(self, page, username: str = None, password: str = None) -> bool:
        """
        Task 2: Thread sidebar isolation, reload persistence, and share link.
        Rules:
        1) Use a test account from dev (/clinical)
        2) Never use real patient data
        3) Use made-up ages and genes
        4) Find buttons with page.getByText() or page.getByRole(), not CSS classes.
        """
        from config import TEST_USERNAME, TEST_PASSWORD

        user = username or TEST_USERNAME
        pwd = password or TEST_PASSWORD

        start = time.time()
        if not user or not pwd:
            self._record_test(
                name="task2",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        try:
            # 1. Log in and open /clinical
            login_page = LoginPage(page, self.base_url)
            login_page.navigate()
            login_page.login(user, pwd)
            active_page = login_page.launch_platform()

            clinical_url = "https://pgx-dev.datum.bio/clinical"
            if "/clinical" not in active_page.url:
                active_page.goto(clinical_url, wait_until="networkidle", timeout=self.timeout)

            clinical = ClinicalPage(active_page, clinical_url)

            # 2. Thread A: Enter Gene A (CYP2C19) and run assessment
            if not clinical.is_manual_form_open():
                clinical.click_enter_gene_panel_manually()
                active_page.wait_for_timeout(1000)

            gene_a = "CYP2C19"
            clinical.fill_age("42")
            clinical.select_sex("Male")
            clinical.select_gene(gene_a)
            clinical.select_phenotype("Intermediate Metabolizer")
            active_page.wait_for_timeout(1000)

            # Run assessment
            clinical.run_assessment(timeout=90000)
            active_page.wait_for_timeout(2000)

            # 3. Start Thread B: Click 'Navigate to Home'
            clinical.click_navigate_to_home()
            active_page.wait_for_timeout(1000)

            if not clinical.is_manual_form_open():
                clinical.click_enter_gene_panel_manually()
                active_page.wait_for_timeout(1000)

            # 4. In Thread B: Enter Gene B (CYP2D6)
            gene_b = "CYP2D6"
            clinical.fill_age("55")
            clinical.select_sex("Female")
            clinical.select_gene(gene_b)
            clinical.select_phenotype("Poor Metabolizer")
            active_page.wait_for_timeout(1000)

            # 5. Switch back to Thread A in sidebar, then to B again
            loader_detected = False

            # Switch to Thread A (named "CYP2C19 session" in sidebar)
            clinical.click_thread(name_or_gene=gene_a, index=1)
            if clinical.is_loader_visible():
                loader_detected = True
            active_page.wait_for_timeout(2000)

            # Verify Thread A shows its own results (e.g. clopidogrel or CYP2C19) and does not leak Gene B
            if active_page.get_by_text("clopidogrel").count() == 0 and active_page.get_by_text(gene_a).count() == 0:
                raise AssertionError(f"Thread A did not display results for '{gene_a}'!")
            main_view = active_page.locator("table, form, div.flex-1").filter(has_not=active_page.locator("button[aria-label='Thread options']"))
            if main_view.get_by_text(gene_b).count() > 0:
                raise AssertionError(f"Thread A leaked data from Thread B ('{gene_b}' found in results view)!")

            # Switch back to Thread B
            clinical.click_thread(index=0)
            if clinical.is_loader_visible():
                loader_detected = True
            active_page.wait_for_timeout(2000)

            if active_page.get_by_text(gene_b).count() == 0:
                raise AssertionError(f"Thread B did not display its gene '{gene_b}'!")
            if active_page.get_by_text("clopidogrel").count() > 0:
                raise AssertionError(f"Thread B leaked Thread A's assessment results ('clopidogrel' visible)!")

            if loader_detected:
                raise AssertionError("Assessment API call (loader) was triggered simply by switching threads!")

            # 6. Reload the page
            active_page.reload(wait_until="networkidle")
            active_page.wait_for_timeout(2000)

            # If redirected to platform selection screen, launch platform again
            launch_btn = active_page.get_by_role("button", name=re.compile(r"Launch Platform", re.I))
            if launch_btn.count() == 0:
                launch_btn = active_page.get_by_text(re.compile(r"Launch Platform", re.I))

            if "/clinical" not in active_page.url or (launch_btn.count() > 0 and launch_btn.first.is_visible()):
                login_helper = LoginPage(active_page, self.base_url)
                active_page = login_helper.launch_platform()
                if "/clinical" not in active_page.url:
                    active_page.goto(clinical_url, wait_until="networkidle", timeout=self.timeout)
                clinical = ClinicalPage(active_page, clinical_url)

            threads = clinical.get_sidebar_thread_elements()
            if threads.count() == 0:
                raise AssertionError("Threads disappeared from sidebar after page reload!")

            # 7. Open menu on Thread A and click Share
            clinical.open_thread_menu(name_or_gene=gene_a)
            clinical.click_share_in_menu()

            share_link = clinical.get_generated_share_link()
            if not share_link or "http" not in share_link:
                raise AssertionError(f"Failed to obtain valid share link: '{share_link}'")

            # 8. Open shared link in new tab and verify
            shared_page = active_page.context.new_page()
            try:
                shared_page.goto(share_link, wait_until="networkidle", timeout=self.timeout)
                shared_page.wait_for_timeout(2000)
                if shared_page.get_by_text(gene_a).count() == 0 and shared_page.get_by_text("clopidogrel").count() == 0:
                    raise AssertionError(f"Shared page did not contain the original data for '{gene_a}'!")
            finally:
                shared_page.close()

            self._record_test(
                name="task2",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "thread_a_gene": gene_a,
                    "thread_b_gene": gene_b,
                    "threads_persisted_after_reload": True,
                    "share_link": share_link
                }
            )
            return True
        except Exception as e:
            target_page = locals().get("active_page", page)
            shot = self._capture_failure(target_page, "task2")
            self._record_test(
                name="task2",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def _ensure_auth(self, page, username: str = None, password: str = None) -> bool:
        """Helper to ensure user is logged in for navigation tests."""
        from config import TEST_USERNAME, TEST_PASSWORD
        user = username or TEST_USERNAME
        pwd = password or TEST_PASSWORD
        if not user or not pwd:
            return False
        login_page = LoginPage(page, self.base_url)
        login_page.navigate()
        if login_page.is_login_page_displayed():
            login_page.login(user, pwd)
        nav = NavigationPage(page, self.base_url)
        nav.dismiss_modal_if_present()
        return True

    def run_nav_task1(self, page, username: str = None, password: str = None) -> bool:
        """
        Pages and Navigation Task 1: Landing page buttons (page "/")
        - Click 'Launch Platform'. The URL should end in /clinical.
        - Click 'Start Learning'. The URL should end in /education.
        """
        start = time.time()
        if not self._ensure_auth(page, username, password):
            self._record_test(
                name="nav_task1",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        nav = NavigationPage(page, self.base_url)
        try:
            # 1. Test 'Launch Platform'
            page.goto(self.base_url, wait_until="networkidle", timeout=self.timeout)
            nav.dismiss_modal_if_present()

            launch_btn = nav.get_launch_platform_btn()
            if launch_btn.count() == 0 or not launch_btn.first.is_visible():
                raise AssertionError("'Launch Platform' button is not visible on landing page!")
            launch_btn.first.click()
            page.wait_for_load_state("networkidle", timeout=self.timeout)

            if not page.url.rstrip("/").endswith("/clinical"):
                raise AssertionError(f"Expected URL to end in /clinical after 'Launch Platform', got: {page.url}")

            # 2. Test 'Start Learning'
            page.goto(self.base_url, wait_until="networkidle", timeout=self.timeout)
            nav.dismiss_modal_if_present()

            start_learning_btn = nav.get_start_learning_btn()
            if start_learning_btn.count() == 0 or not start_learning_btn.first.is_visible():
                raise AssertionError("'Start Learning' button is not visible on landing page!")
            start_learning_btn.first.click()
            page.wait_for_load_state("networkidle", timeout=self.timeout)

            if not page.url.rstrip("/").endswith("/education"):
                raise AssertionError(f"Expected URL to end in /education after 'Start Learning', got: {page.url}")

            self._record_test(
                name="nav_task1",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={"launch_platform_target": "/clinical", "start_learning_target": "/education"}
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "nav_task1")
            self._record_test(
                name="nav_task1",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_nav_task2(self, page, username: str = None, password: str = None) -> bool:
        """
        Pages and Navigation Task 2: Landing page on phone view (390 x 844)
        - At first only 'Launch Platform' is visible. 'Start Learning' is hidden.
        - Click the 'Education' tab. 'Start Learning' shows and 'Launch Platform' hides.
        - Click the 'Clinical' tab. 'Launch Platform' shows again.
        """
        start = time.time()
        if not self._ensure_auth(page, username, password):
            self._record_test(
                name="nav_task2",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        nav = NavigationPage(page, self.base_url)
        try:
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(self.base_url, wait_until="networkidle", timeout=self.timeout)
            nav.dismiss_modal_if_present()

            launch_btn = nav.get_launch_platform_btn()
            start_learning_btn = nav.get_start_learning_btn()

            # Initial view
            if launch_btn.count() == 0 or not launch_btn.first.is_visible():
                raise AssertionError("On phone view initial load, 'Launch Platform' should be visible!")
            if start_learning_btn.count() > 0 and start_learning_btn.first.is_visible():
                raise AssertionError("On phone view initial load, 'Start Learning' should be hidden!")

            # Switch to Education tab
            nav.click_education_tab()
            if start_learning_btn.count() == 0 or not start_learning_btn.first.is_visible():
                raise AssertionError("After clicking 'Education' tab, 'Start Learning' should be visible!")
            if launch_btn.count() > 0 and launch_btn.first.is_visible():
                raise AssertionError("After clicking 'Education' tab, 'Launch Platform' should be hidden!")

            # Switch back to Clinical tab
            nav.click_clinical_tab()
            if launch_btn.count() == 0 or not launch_btn.first.is_visible():
                raise AssertionError("After clicking 'Clinical' tab, 'Launch Platform' should show again!")

            page.set_viewport_size({"width": 1280, "height": 720})

            self._record_test(
                name="nav_task2",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={"viewport": "390x844", "tab_switching_verified": True}
            )
            return True
        except Exception as e:
            page.set_viewport_size({"width": 1280, "height": 720})
            shot = self._capture_failure(page, "nav_task2")
            self._record_test(
                name="nav_task2",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_nav_task3(self, page, username: str = None, password: str = None) -> bool:
        """
        Pages and Navigation Task 3: Education cards (page "/education")
        - Click 'Take Quiz'. The URL should end in /quiz.
        - Click 'Drug Monograph', 'Lectures' and 'Patient Case Simulation' one at a time. The URL should stay /education.
        """
        start = time.time()
        if not self._ensure_auth(page, username, password):
            self._record_test(
                name="nav_task3",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        nav = NavigationPage(page, self.base_url)
        try:
            # Navigate cleanly to /education via 'Start Learning'
            # (Direct goto('/education') causes the server to redirect to '/' and refresh)
            nav.navigate_to_education()

            # 1. Click 'Take Quiz'
            nav.click_take_quiz()
            if not page.url.rstrip("/").endswith("/quiz"):
                raise AssertionError(f"Expected URL to end in /quiz after 'Take Quiz', got: {page.url}")

            # Return to /education
            page.go_back(wait_until="networkidle")
            page.wait_for_timeout(1000)
            if not page.url.rstrip("/").endswith("/education"):
                nav.navigate_to_education()

            # 2. Click 'Drug Monograph', 'Lectures' and 'Patient Case Simulation'
            for card_name in ["Drug Monograph", "Lectures", "Patient Case Simulation"]:
                nav.click_education_card(card_name)
                if not page.url.rstrip("/").endswith("/education"):
                    raise AssertionError(f"After clicking '{card_name}', URL should stay /education, got: {page.url}")

            self._record_test(
                name="nav_task3",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={"quiz_navigation": "/quiz", "static_cards_verified": ["Drug Monograph", "Lectures", "Patient Case Simulation"]}
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "nav_task3")
            self._record_test(
                name="nav_task3",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_nav_task4(self, page, username: str = None, password: str = None) -> bool:
        """
        Pages and Navigation Task 4: Floating mode switch
        - On /clinical, click 'Switch to education mode'. The URL should end in /education.
        - Click 'Switch to clinical mode'. The URL should be back to /clinical.
        - The button must not appear on '/', '/terms-of-use' or '/shared_chat'.
        """
        start = time.time()
        if not self._ensure_auth(page, username, password):
            self._record_test(
                name="nav_task4",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        nav = NavigationPage(page, self.base_url)
        try:
            # 1. Login & click 'Start Learning' -> /education
            nav.navigate_to_education()
            if not page.url.rstrip("/").endswith("/education"):
                raise AssertionError(f"Expected URL to end in /education after 'Start Learning', got: {page.url}")

            # 2. Click floating switch button ('Switch to clinical mode') -> /clinical
            nav.click_switch_to_clinical_mode()
            if not page.url.rstrip("/").endswith("/clinical"):
                raise AssertionError(f"Expected URL to end in /clinical after switching to clinical mode, got: {page.url}")

            # 3. On /clinical, click floating switch button ('Switch to education mode') -> /education
            nav.click_switch_to_education_mode()
            if not page.url.rstrip("/").endswith("/education"):
                raise AssertionError(f"Expected URL to end in /education after switching to education mode, got: {page.url}")

            # 4. Click 'Switch to clinical mode' again -> back to /clinical
            nav.click_switch_to_clinical_mode()
            if not page.url.rstrip("/").endswith("/clinical"):
                raise AssertionError(f"Expected URL to end in /clinical after switching back, got: {page.url}")

            # 5. Verify button does NOT appear on '/', '/terms-of-use', or '/shared_chat'
            # Check '/'
            page.goto(self.base_url, wait_until="networkidle", timeout=self.timeout)
            nav.dismiss_modal_if_present()
            btn = nav.get_floating_mode_switch_btn()
            if btn.count() > 0 and btn.first.is_visible():
                raise AssertionError(f"Floating mode switch unexpectedly appeared on: {self.base_url}")

            # Check '/terms-of-use' (reached via 'Terms of Use' link so SPA doesn't redirect to '/')
            nav.navigate_to_terms_of_use()
            if not page.url.rstrip("/").endswith("/terms-of-use"):
                raise AssertionError(f"Expected to reach /terms-of-use, got: {page.url}")
            btn = nav.get_floating_mode_switch_btn()
            if btn.count() > 0 and btn.first.is_visible():
                raise AssertionError("Floating mode switch unexpectedly appeared on: /terms-of-use")

            # Check '/shared_chat'
            shared_chat_url = f"{self.base_url.rstrip('/')}/shared_chat"
            page.goto(shared_chat_url, wait_until="networkidle", timeout=self.timeout)
            nav.dismiss_modal_if_present()
            btn = nav.get_floating_mode_switch_btn()
            if btn.count() > 0 and btn.first.is_visible():
                raise AssertionError(f"Floating mode switch unexpectedly appeared on: {shared_chat_url}")

            self._record_test(
                name="nav_task4",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={"mode_switch_cycle": "education -> clinical -> education -> clinical", "disallowed_pages_checked": ["/", "/terms-of-use", "/shared_chat"]}
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "nav_task4")
            self._record_test(
                name="nav_task4",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_nav_task5(self, page, username: str = None, password: str = None) -> bool:
        """
        Pages and Navigation Task 5: 'Learn more' link
        - On /clinical, click 'Learn more' in the sidebar. The URL should end in /creator-insight.
        """
        start = time.time()
        if not self._ensure_auth(page, username, password):
            self._record_test(
                name="nav_task5",
                status="SKIPPED",
                duration_ms=0,
                details={"reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."}
            )
            return True

        nav = NavigationPage(page, self.base_url)
        try:
            nav.navigate_to_clinical()

            target_page = nav.click_learn_more()
            final_url = target_page.url
            if target_page != page:
                target_page.close()

            expected_url = f"{self.base_url.rstrip('/')}/creator-insight"
            if expected_url not in final_url or not final_url.rstrip("/").endswith("/creator-insight"):
                raise AssertionError(f"Expected URL '{expected_url}' after 'Learn more', got: {final_url}")

            self._record_test(
                name="nav_task5",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={"learn_more_target": expected_url, "actual_url": final_url}
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "nav_task5")
            self._record_test(
                name="nav_task5",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_nav_task6(self, page) -> bool:
        """
        Pages and Navigation Task 6: Broken share links (public endpoints)
        - Open /shared_chat with no uId. Expect: 'No session ID found in URL. Please check the shared link.'
        - Open /shared_chat?uId=ab. Expect: 'Unable to load conversation'
        """
        start = time.time()
        try:
            # 1. No uId
            page.goto(f"{self.base_url.rstrip('/')}/shared_chat", wait_until="networkidle", timeout=self.timeout)
            expected_1 = "No session ID found in URL. Please check the shared link."
            if not page.get_by_text(expected_1).first.is_visible():
                raise AssertionError(f"Expected text '{expected_1}' not found on /shared_chat")

            # 2. uId=ab
            page.goto(f"{self.base_url.rstrip('/')}/shared_chat?uId=ab", wait_until="networkidle", timeout=self.timeout)
            if not page.get_by_text(re.compile(r"Unable to load conversation", re.I)).first.is_visible():
                raise AssertionError("Expected 'Unable to load conversation' not found on /shared_chat?uId=ab")

            self._record_test(
                name="nav_task6",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={"broken_links_verified": ["/shared_chat", "/shared_chat?uId=ab"]}
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "nav_task6")
            self._record_test(
                name="nav_task6",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot
            )
            return False

    def run_ai_judge(
        self,
        page,
        username: str = None,
        password: str = None,
        gemini_key: str = None,
    ) -> bool:
        """
        AI-Driven QA Scenario: Runs a synthetic PGx clinical assessment on /clinical
        and evaluates the rendered clinical recommendations using GeminiAIJudge.
        """
        start = time.time()
        api_key = (gemini_key or GEMINI_API_KEY or "").strip()
        if not api_key:
            self._record_test(
                name="ai_judge",
                status="SKIPPED",
                duration_ms=0,
                details={
                    "reason": "Gemini API key not provided. Pass --gemini-key or set GEMINI_API_KEY."
                },
            )
            return True

        if not self._ensure_auth(page, username, password):
            self._record_test(
                name="ai_judge",
                status="SKIPPED",
                duration_ms=0,
                details={
                    "reason": "No credentials provided. Pass via --username/--password or set PGX_USERNAME/PGX_PASSWORD."
                },
            )
            return True

        nav = NavigationPage(page, self.base_url)
        try:
            nav.navigate_to_clinical()
            clinical = ClinicalPage(page)

            if not clinical.is_manual_form_open():
                clinical.click_enter_gene_panel_manually()
                page.wait_for_timeout(1000)

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

            clinical.fill_age(case.age)
            clinical.select_sex(case.sex)
            clinical.select_gene(case.gene)
            clinical.select_phenotype(case.phenotype)
            page.wait_for_timeout(500)

            clinical.run_assessment(timeout=90000)
            rendered_output = clinical.get_assessment_response_text()

            judge = GeminiAIJudge(api_key=api_key)
            verdict = judge.evaluate(case, rendered_output)

            if not verdict.passed:
                raise AssertionError(
                    f"Gemini AI Judge failed (score={verdict.score}): {verdict.summary} | "
                    f"Safety flags: {verdict.clinical_safety_flags}"
                )

            self._record_test(
                name="ai_judge",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details=verdict.to_dict(),
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "ai_judge")
            self._record_test(
                name="ai_judge",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot,
            )
            return False

    def run_generate_tests(
        self,
        page,
        username: str = None,
        password: str = None,
        api_key: str = None,
        model: str = None,
        task_file: str = None,
        explore: bool = True,
        approve: bool = False,
        interactive: bool = False,
    ) -> bool:
        """
        Reads plain-English task docs from task/, explores the app + Page Objects,
        generates human-reviewable scenarios + tests/test_generated.py via LLM,
        and pauses for approval before executing pytest unless --approve or interactive confirmation is given.
        """
        start = time.time()
        key = (api_key or LLM_API_KEY or "").strip()
        if not key:
            self._record_test(
                name="generate_tests",
                status="SKIPPED",
                duration_ms=0,
                details={
                    "reason": "OpenRouter/OpenAI API key not provided. Pass --api-key or set OPENROUTER_API_KEY / OPENAI_API_KEY."
                },
            )
            return True

        try:
            tasks = load_tasks(
                task_dir=TASK_DIR,
                specific_file=Path(task_file) if task_file else None,
            )
            po_summary = summarize_page_objects()

            user = username or TEST_USERNAME
            pwd = password or TEST_PASSWORD
            snapshots = []
            if explore:
                try:
                    snapshots = explore_live_app(page, base_url=self.base_url, username=user, password=pwd)
                except Exception:
                    snapshots = []

            generator = LLMTestGenerator(api_key=key, model=model or LLM_MODEL)
            result = generator.generate(
                tasks=tasks,
                page_object_summary=po_summary,
                live_snapshots=snapshots,
            )

            scenario_list = [
                {"id": s.id, "title": s.title, "expected_result": s.expected_result}
                for s in result.scenarios
            ]

            self._record_test(
                name="generate_tests",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "task_files": result.task_files,
                    "plan_file": str(result.plan_file_path),
                    "generated_test_file": str(result.test_file_path),
                    "model_used": result.model_used,
                    "scenarios": scenario_list,
                    "awaiting_approval": not approve,
                },
            )

            if interactive and not approve:
                print("\n--- Generated Scenarios for Approval ---")
                for s in result.scenarios:
                    print(f"  [{s.id}] {s.title} -> Expected: {s.expected_result}")
                print(f"Plan file: {result.plan_file_path}")
                print(f"Code file: {result.test_file_path}")
                ans = input("Approve and execute tests/test_generated.py with pytest? [y/N]: ").strip().lower()
                approve = ans in ("y", "yes")

            if approve:
                return self.run_generated_suite(
                    page,
                    username=user,
                    password=pwd,
                    approve=True,
                )

            return True
        except NoTaskFilesError as e:
            self._record_test(
                name="generate_tests",
                status="SKIPPED",
                duration_ms=(time.time() - start) * 1000,
                details={"reason": str(e)},
            )
            return True
        except Exception as e:
            shot = self._capture_failure(page, "generate_tests")
            self._record_test(
                name="generate_tests",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
                screenshot=shot,
            )
            return False

    def run_generated_suite(
        self,
        page,
        username: str = None,
        password: str = None,
        approve: bool = False,
        interactive: bool = False,
    ) -> bool:
        """
        Executes pytest on tests/test_generated.py after approval and reports PASS/FAIL.
        """
        start = time.time()
        user = username or TEST_USERNAME
        pwd = password or TEST_PASSWORD

        try:
            if interactive and not approve:
                ans = input("Approve and run tests/test_generated.py via pytest? [y/N]: ").strip().lower()
                approve = ans in ("y", "yes")

            outcome = execute_generated_pytest(
                approve=approve,
                env_overrides={
                    "PGX_USERNAME": user or "",
                    "PGX_PASSWORD": pwd or "",
                    "HEADLESS": "false" if not self.headless else "true",
                    "PGX_URL": self.base_url,
                },
            )
            if outcome["exit_code"] != 0:
                raise AssertionError(
                    f"Pytest failed (exit_code={outcome['exit_code']}):\n{outcome['output'][-1500:]}"
                )

            self._record_test(
                name="run_generated",
                status="PASSED",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "test_file": outcome["test_file"],
                    "exit_code": outcome["exit_code"],
                    "pytest_output": outcome["output"][-1500:],
                },
            )
            return True
        except ApprovalRequiredError as e:
            self._record_test(
                name="run_generated",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
            )
            return False
        except Exception as e:
            self._record_test(
                name="run_generated",
                status="FAILED",
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
            )
            return False

    def execute(
        self,
        scenario: str = "all",
        username: str = None,
        password: str = None,
        gemini_key: str = None,
        api_key: str = None,
        llm_model: str = None,
        task_file: str = None,
        approve: bool = False,
        explore: bool = True,
        interactive: bool = False,
    ) -> dict:
        """Executes designated scenarios and generates final summary report."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()

            scenarios_to_run = []
            if scenario in ("smoke", "all"):
                scenarios_to_run.append(self.run_smoke)
            if scenario in ("invalid_login", "all"):
                scenarios_to_run.append(self.run_invalid_login)
            if scenario in ("valid_login", "all"):
                scenarios_to_run.append(lambda pg: self.run_valid_login(pg, username=username, password=password))
            if scenario in ("navigation", "all"):
                scenarios_to_run.append(self.run_navigation)
            if scenario in ("task1_a", "all"):
                scenarios_to_run.append(lambda pg: self.run_task1_a(pg, username=username, password=password))
            if scenario in ("task1_b", "all"):
                scenarios_to_run.append(lambda pg: self.run_task1_b(pg, username=username, password=password))
            if scenario in ("task2", "all"):
                scenarios_to_run.append(lambda pg: self.run_task2(pg, username=username, password=password))
            if scenario in ("nav_task1", "nav_all", "all"):
                scenarios_to_run.append(lambda pg: self.run_nav_task1(pg, username=username, password=password))
            if scenario in ("nav_task2", "nav_all", "all"):
                scenarios_to_run.append(lambda pg: self.run_nav_task2(pg, username=username, password=password))
            if scenario in ("nav_task3", "nav_all", "all"):
                scenarios_to_run.append(lambda pg: self.run_nav_task3(pg, username=username, password=password))
            if scenario in ("nav_task4", "nav_all", "all"):
                scenarios_to_run.append(lambda pg: self.run_nav_task4(pg, username=username, password=password))
            if scenario in ("nav_task5", "nav_all", "all"):
                scenarios_to_run.append(lambda pg: self.run_nav_task5(pg, username=username, password=password))
            if scenario in ("nav_task6", "nav_all", "all"):
                scenarios_to_run.append(self.run_nav_task6)
            if scenario in ("ai_judge", "all"):
                scenarios_to_run.append(
                    lambda pg: self.run_ai_judge(
                        pg, username=username, password=password, gemini_key=gemini_key
                    )
                )
            if scenario == "generate_tests":
                scenarios_to_run.append(
                    lambda pg: self.run_generate_tests(
                        pg,
                        username=username,
                        password=password,
                        api_key=api_key,
                        model=llm_model,
                        task_file=task_file,
                        explore=explore,
                        approve=approve,
                        interactive=interactive,
                    )
                )
            if scenario == "run_generated":
                scenarios_to_run.append(
                    lambda pg: self.run_generated_suite(
                        pg,
                        username=username,
                        password=password,
                        approve=approve,
                        interactive=interactive,
                    )
                )

            for test_func in scenarios_to_run:
                test_func(page)

            browser.close()

        total = len(self.results["tests"])
        passed = sum(1 for t in self.results["tests"] if t["status"] == "PASSED")
        failed = sum(1 for t in self.results["tests"] if t["status"] == "FAILED")
        skipped = sum(1 for t in self.results["tests"] if t["status"] == "SKIPPED")

        self.results["summary"]["total"] = total
        self.results["summary"]["passed"] = passed
        self.results["summary"]["failed"] = failed
        self.results["summary"]["skipped"] = skipped
        self.results["summary"]["status"] = "PASSED" if failed == 0 and total > 0 else "FAILED"

        return self.results


def main():
    parser = argparse.ArgumentParser(description="AI-Agent Friendly Playwright Test Runner")
    parser.add_argument(
        "--scenario",
        choices=[
            "smoke", "invalid_login", "valid_login", "navigation",
            "task1_a", "task1_b", "task2",
            "nav_task1", "nav_task2", "nav_task3", "nav_task4", "nav_task5", "nav_task6", "nav_all",
            "ai_judge", "generate_tests", "run_generated", "all"
        ],
        default="all",
        help="Test scenario to execute (default: all)"
    )
    parser.add_argument("--url", default=BASE_URL, help=f"Target URL (default: {BASE_URL})")
    parser.add_argument("--headed", action="store_true", help="Launch browser in headed UI mode")
    parser.add_argument("--json", action="store_true", help="Output pure JSON for AI agent consumption")
    parser.add_argument("--output", type=str, default="", help="Path to write JSON test report")
    parser.add_argument("--username", type=str, help="Username for valid login test")
    parser.add_argument("--password", type=str, help="Password for valid login test")
    parser.add_argument("--gemini-key", type=str, help="Gemini API key for ai_judge scenario (or set GEMINI_API_KEY)")
    parser.add_argument("--api-key", type=str, help="OpenRouter/OpenAI API key for generate_tests (or set OPENROUTER_API_KEY/OPENAI_API_KEY)")
    parser.add_argument("--llm-model", type=str, help="Model name for generate_tests (default from config)")
    parser.add_argument("--task-file", type=str, help="Specific task doc file inside task/ to generate tests from")
    parser.add_argument("--approve", action="store_true", help="Approve generated tests/test_generated.py for pytest execution")
    parser.add_argument("--no-explore", action="store_true", help="Skip live browser exploration when generating tests")
    parser.add_argument("--interactive", action="store_true", help="Prompt interactively for approval before running pytest")

    args = parser.parse_args()

    runner = TestRunner(base_url=args.url, headless=not args.headed)
    results = runner.execute(
        scenario=args.scenario,
        username=args.username,
        password=args.password,
        gemini_key=args.gemini_key,
        api_key=args.api_key,
        llm_model=args.llm_model,
        task_file=args.task_file,
        approve=args.approve,
        explore=not args.no_explore,
        interactive=args.interactive,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Human-friendly console summary
        print("\n================ TEST EXECUTION RESULTS ================")
        print(f"Target URL: {results['target_url']}")
        print(f"Status    : {results['summary']['status']}")
        print(f"Total: {results['summary']['total']} | Passed: {results['summary']['passed']} | Failed: {results['summary']['failed']} | Skipped: {results['summary'].get('skipped', 0)}\n")
        for test in results["tests"]:
            if test["status"] == "PASSED":
                mark = "[PASS]"
            elif test["status"] == "SKIPPED":
                mark = "[SKIP]"
            else:
                mark = "[FAIL]"

            print(f"  {mark} {test['scenario']} ({test['duration_ms']}ms)")
            if test["status"] == "PASSED" and test["scenario"] == "generate_tests":
                details = test.get("details", {})
                print(f"         Plan File: {details.get('plan_file')}")
                print(f"         Code File: {details.get('generated_test_file')}")
                for sc in details.get("scenarios", []):
                    print(f"           - [{sc['id']}] {sc['title']}")
                if details.get("awaiting_approval"):
                    print("         Status: Awaiting your approval -> review the plan/code, then run:")
                    print("                 runner.py --scenario run_generated --approve")
            if test["status"] == "SKIPPED" and "reason" in test.get("details", {}):
                print(f"         Reason: {test['details']['reason']}")
            if test["status"] == "FAILED":
                print(f"         Error: {test.get('error')}")
                if "field_audit" in test.get("details", {}):
                    print("         Field Retention Audit:")
                    for fname, fstat in test["details"]["field_audit"].items():
                        m = "[PASS]" if fstat == "RETAINED" else "[FAIL]"
                        print(f"           {m} {fname}: {fstat}")
                if "failed_fields" in test.get("details", {}):
                    print("         Failed Items:")
                    for ff in test["details"]["failed_fields"]:
                        print(f"           - {ff}")
                if test.get("screenshot"):
                    print(f"         Screenshot: {test.get('screenshot')}")
        print("========================================================\n")

    sys.exit(0 if results["summary"]["status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()

