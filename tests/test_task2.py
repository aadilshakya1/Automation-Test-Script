import re
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

Task 2: Thread sidebar
Goal: threads stay separate and their menu actions work.
- Run an assessment so thread A has results. Click Navigate to Home to start thread B.
- In thread B, enter a different gene, e.g. CYP2D6.
- Switch back to thread A in the sidebar, then to B again.
- Then reload the page.
- Open the menu and click Share.
- Make sure it generates proper link and when opening the shared link it shows the same data like in the original one.

Expected:
- Thread A still shows its own genes and results. Thread B shows only its own. Nothing is mixed.
- The thread is still there after the reload.
- Share gives a link.
- No assessment API calls fire just from switching threads.
"""

@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        # Grant clipboard permissions for share link verification
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        yield context
        browser.close()

@pytest.fixture
def page(browser_context):
    page = browser_context.new_page()
    yield page
    page.close()


def test_task2_thread_sidebar_and_sharing(page: Page):
    """Task 2: Thread sidebar isolation, persistence across reload, and share flow."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not provided. Set PGX_USERNAME and PGX_PASSWORD.")

    # 1. Log in and open /clinical
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    login_page.login(TEST_USERNAME, TEST_PASSWORD)
    page.wait_for_load_state("networkidle", timeout=30000)

    active_page = login_page.launch_platform()
    clinical_url = "https://pgx-dev.datum.bio/clinical"
    if "/clinical" not in active_page.url:
        active_page.goto(clinical_url, wait_until="networkidle", timeout=30000)

    clinical = ClinicalPage(active_page, clinical_url)

    # 2. Thread A: Open manual form and enter Gene A (e.g. CYP2C19)
    if not clinical.is_manual_form_open():
        clinical.click_enter_gene_panel_manually()
        active_page.wait_for_timeout(1000)

    gene_a = "CYP2C19"
    age_a = "42"
    clinical.fill_age(age_a)
    clinical.select_sex("Male")
    clinical.select_gene(gene_a)
    clinical.select_phenotype("Intermediate Metabolizer")
    active_page.wait_for_timeout(1000)

    # Run assessment so Thread A has results
    clinical.run_assessment(timeout=90000)
    active_page.wait_for_timeout(2000)

    # 3. Start Thread B: Click 'Navigate to Home' in sidebar
    clinical.click_navigate_to_home()
    active_page.wait_for_timeout(1000)

    if not clinical.is_manual_form_open():
        clinical.click_enter_gene_panel_manually()
        active_page.wait_for_timeout(1000)

    # 4. In Thread B: Enter a different gene (e.g. CYP2D6)
    gene_b = "CYP2D6"
    age_b = "55"
    clinical.fill_age(age_b)
    clinical.select_sex("Female")
    clinical.select_gene(gene_b)
    clinical.select_phenotype("Poor Metabolizer")
    active_page.wait_for_timeout(1000)

    # 5. Switch back to Thread A in the sidebar, then to B again
    loader_detected_on_switch = False

    # Switch to Thread A (identified by "CYP2C19 session" in sidebar)
    clinical.click_thread(name_or_gene=gene_a, index=1)
    if clinical.is_loader_visible():
        loader_detected_on_switch = True
    active_page.wait_for_timeout(2000)

    # Verify Thread A shows its own results (e.g. clopidogrel or CYP2C19) and does not leak Gene B
    assert active_page.get_by_text("clopidogrel").count() > 0 or active_page.get_by_text(gene_a).count() > 0, f"Thread A did not display results for '{gene_a}'!"
    main_view = active_page.locator("table, form, div.flex-1").filter(has_not=active_page.locator("button[aria-label='Thread options']"))
    assert main_view.get_by_text(gene_b).count() == 0, f"Thread A leaked data from Thread B ('{gene_b}' found in results view)!"

    # Switch back to Thread B (the most recent thread)
    clinical.click_thread(index=0)
    if clinical.is_loader_visible():
        loader_detected_on_switch = True
    active_page.wait_for_timeout(2000)

    # Verify Thread B shows only its own gene (CYP2D6) and not Thread A's clopidogrel assessment
    assert active_page.get_by_text(gene_b).count() > 0, f"Thread B did not display its gene '{gene_b}'!"
    assert active_page.get_by_text("clopidogrel").count() == 0, f"Thread B leaked Thread A's assessment results ('clopidogrel' visible)!"

    # Expected: No assessment API calls fire just from switching threads
    assert not loader_detected_on_switch, "Assessment API call (loader) was triggered simply by switching threads!"

    # 6. Reload the page
    active_page.reload(wait_until="networkidle")
    active_page.wait_for_timeout(2000)

    # After reload, check if platform selection screen is displayed and launch platform again
    launch_btn = active_page.get_by_role("button", name=re.compile(r"Launch Platform", re.I))
    if launch_btn.count() == 0:
        launch_btn = active_page.get_by_text(re.compile(r"Launch Platform", re.I))

    if "/clinical" not in active_page.url or (launch_btn.count() > 0 and launch_btn.first.is_visible()):
        login_helper = LoginPage(active_page, BASE_URL)
        active_page = login_helper.launch_platform()
        if "/clinical" not in active_page.url:
            active_page.goto(clinical_url, wait_until="networkidle", timeout=30000)
        clinical = ClinicalPage(active_page, clinical_url)

    # Expected: The thread is still there after the reload
    threads = clinical.get_sidebar_thread_elements()
    assert threads.count() > 0, "Threads disappeared from the sidebar after page reload!"

    # 7. Open the menu on Thread A and click Share
    clinical.open_thread_menu(name_or_gene=gene_a)
    clinical.click_share_in_menu()

    # Expected: Share gives a link
    share_link = clinical.get_generated_share_link()
    assert share_link and "http" in share_link, f"Failed to generate a valid share link, got: '{share_link}'"

    # 8. Open shared link in a new tab and verify it shows the same data
    shared_page = active_page.context.new_page()
    try:
        shared_page.goto(share_link, wait_until="networkidle", timeout=30000)
        shared_page.wait_for_timeout(2000)

        # Expected: shows the same data like in the original one (e.g. gene_a / clopidogrel)
        assert shared_page.get_by_text(gene_a).count() > 0 or shared_page.get_by_text("clopidogrel").count() > 0, f"Shared link did not display the original data for '{gene_a}'!"
    finally:
        shared_page.close()

