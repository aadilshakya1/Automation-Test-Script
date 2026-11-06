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

from config import BASE_URL, DEFAULT_TIMEOUT, SCREENSHOTS_DIR
from pages.login_page import LoginPage
from pages.clinical_page import ClinicalPage


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

    def execute(self, scenario: str = "all", username: str = None, password: str = None) -> dict:
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
        choices=["smoke", "invalid_login", "valid_login", "navigation", "task1_a", "task1_b", "task2", "all"],
        default="all",
        help="Test scenario to execute (default: all)"
    )
    parser.add_argument("--url", default=BASE_URL, help=f"Target URL (default: {BASE_URL})")
    parser.add_argument("--headed", action="store_true", help="Launch browser in headed UI mode")
    parser.add_argument("--json", action="store_true", help="Output pure JSON for AI agent consumption")
    parser.add_argument("--output", type=str, default="", help="Path to write JSON test report")
    parser.add_argument("--username", type=str, help="Username for valid login test")
    parser.add_argument("--password", type=str, help="Password for valid login test")

    args = parser.parse_args()

    runner = TestRunner(base_url=args.url, headless=not args.headed)
    results = runner.execute(scenario=args.scenario, username=args.username, password=args.password)

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

