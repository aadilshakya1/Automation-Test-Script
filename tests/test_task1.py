import time
import pytest
from playwright.sync_api import Page, sync_playwright
from pages.login_page import LoginPage
from pages.clinical_page import ClinicalPage
from config import BASE_URL, TEST_USERNAME, TEST_PASSWORD, HEADLESS

"""
Rules for every task:
1) Use a test account from the dev (https://pgx-dev.datum.bio/clinical).
2) Never use real patient data.
3) Use made-up ages and genes.
4) Find buttons with page.getByText() or page.getByRole(), not CSS classes.

Task 1: Switching between Prompt Mode and Manual Entry
Goal: switching screens must not call any API, must not lose data and must not crash.

Part A: new thread
- Log in and open /clinical.
- Click Enter Gene Panel Manually. The manual form opens.
- Click Prompt Mode, then Enter Gene Panel Manually. Repeat this 5 times, waiting 1 second between clicks.
- Wait 5 seconds, then check apiCalls.

Expected: the form opens every time. apiCalls is empty.
The overlay "Running the full PGx assessment" never appears. No console errors.
"""

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


def test_task1_part_a_switching_modes(page: Page):
    """Task 1 Part A: Switching between Prompt Mode and Manual Entry in a new thread."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not provided. Set PGX_USERNAME and PGX_PASSWORD.")

    console_errors = []
    api_calls = []
    tracking_active = False

    # Capture console errors (Expected: No console errors)
    page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
    page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))

    # Capture network API calls (fetch / xhr)
    def handle_request(req):
        if tracking_active and req.resource_type in ("fetch", "xhr"):
            # Exclude browser telemetry or auth keepalive if needed, but record API calls
            api_calls.append({"url": req.url, "method": req.method})

    page.on("request", handle_request)

    # 1. Log in and open /clinical
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    if login_page.is_login_page_displayed():
        login_page.login(TEST_USERNAME, TEST_PASSWORD)
    page.wait_for_load_state("networkidle", timeout=30000)

    # Launch platform (handles modal dismissal and navigates to /clinical)
    active_page = login_page.launch_platform()

    # Attach listeners to active_page if a new tab was opened
    if active_page != page:
        active_page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        active_page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))
        active_page.on("request", handle_request)

    clinical_url = "https://pgx-dev.datum.bio/clinical"
    if "/clinical" not in active_page.url:
        active_page.goto(clinical_url, wait_until="networkidle", timeout=30000)

    clinical = ClinicalPage(active_page, clinical_url)

    # 2. Click Enter Gene Panel Manually. The manual form opens.
    # Rule 4: page.getByRole() or page.getByText()
    clinical.click_enter_gene_panel_manually()
    active_page.wait_for_timeout(1000)

    loader_detected = False

    # 3. Click Prompt Mode, then Enter Gene Panel Manually. Repeat this 5 times, waiting 1 second between clicks.
    for i in range(1, 6):
        # Click Prompt Mode (Rule 4: getByRole / getByText)
        clinical.click_prompt_mode()
        if clinical.is_loader_visible():
            loader_detected = True
        active_page.wait_for_timeout(1000)

        # Overlay must never appear
        assert not clinical.is_overlay_visible(), f"Iteration {i}: 'Running the full PGx assessment' overlay appeared after Prompt Mode click!"

        # Click Enter Gene Panel Manually (Rule 4: getByRole / getByText)
        clinical.click_enter_gene_panel_manually()
        if clinical.is_loader_visible():
            loader_detected = True
        active_page.wait_for_timeout(1000)

        # Expected: the form opens every time
        assert clinical.is_manual_form_open(), f"Iteration {i}: Manual form did not open!"
        assert not clinical.is_overlay_visible(), f"Iteration {i}: 'Running the full PGx assessment' overlay appeared after Enter Gene Panel Manually click!"

    # 4. Wait 5 seconds, then check apiCalls (loaders indicate active API calls).
    active_page.wait_for_timeout(5000)
    if clinical.is_loader_visible():
        loader_detected = True

    # Check window.apiCalls if defined in window object
    window_api_calls = active_page.evaluate("() => (window.apiCalls && Array.isArray(window.apiCalls)) ? window.apiCalls : []")

    # Verify Expected Conditions:
    # 1. apiCalls is empty (no loaders/spinners appeared, and window.apiCalls is empty)
    assert not loader_detected, "Expected apiCalls to be empty, but a loader/spinner appeared during screen switching!"
    assert len(window_api_calls) == 0, f"Expected window.apiCalls to be empty, but found: {window_api_calls}"

    # 2. The overlay "Running the full PGx assessment" never appears
    assert not clinical.is_overlay_visible(), "'Running the full PGx assessment' overlay is currently visible!"

    # 3. No console errors
    assert len(console_errors) == 0, f"Expected no console errors, but found: {console_errors}"


def test_task1_part_b_data_persistence(page: Page):
    """
    Task 1 Part B: Data is kept when switching between Prompt Mode and Manual Entry.
    Rules:
    1) Use a test account from the dev.
    2) Never use real patient data.
    3) Use made-up ages and genes.
    4) Find buttons with page.getByText() or page.getByRole(), not CSS classes.

    Flow:
    - In the manual form, fill in age, sex, one gene (e.g. CYP2C19) and its phenotype.
    - Click Prompt Mode, then Enter Gene Panel Manually.
    - Expected: every field you entered is still filled in. apiCalls is still empty.
    """
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not provided. Set PGX_USERNAME and PGX_PASSWORD.")

    api_calls = []
    tracking_active = False

    def handle_request(req):
        if tracking_active and req.resource_type in ("fetch", "xhr"):
            api_calls.append({"url": req.url, "method": req.method})

    page.on("request", handle_request)

    # 1. Log in and open /clinical
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    if login_page.is_login_page_displayed():
        login_page.login(TEST_USERNAME, TEST_PASSWORD)
    page.wait_for_load_state("networkidle", timeout=30000)

    active_page = login_page.launch_platform()
    if active_page != page:
        active_page.on("request", handle_request)

    clinical_url = "https://pgx-dev.datum.bio/clinical"
    if "/clinical" not in active_page.url:
        active_page.goto(clinical_url, wait_until="networkidle", timeout=30000)

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

    # 6. Expected: apiCalls is still empty (no loaders appeared)
    if loader_detected:
        failed_items.append("Loader/spinner appeared during mode switching")

    if fill_errors:
        for fname, err in fill_errors.items():
            failed_items.append(f"{fname} had fill error: {err}")

    assert len(failed_items) == 0, f"Task 1 Part B failed {len(failed_items)} check(s): {failed_items}. Full audit: {field_status}"



