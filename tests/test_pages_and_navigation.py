import re
import pytest
from playwright.sync_api import Page, sync_playwright
from pages.login_page import LoginPage
from pages.navigation_page import NavigationPage
from config import BASE_URL, TEST_USERNAME, TEST_PASSWORD, HEADLESS

"""
Pages and Navigation Test Suite:
1) Task 1: Landing page buttons (page "/")
2) Task 2: Landing page on a phone view (screen size 390 x 844)
3) Task 3: Education cards (page "/education")
4) Task 4: Floating mode switch
5) Task 5: 'Learn more' link
6) Task 6: Broken share links
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


def ensure_authenticated(page: Page) -> Page:
    """Helper to log in if not already logged in."""
    if not TEST_USERNAME or not TEST_PASSWORD:
        pytest.skip("Test credentials not provided. Set PGX_USERNAME and PGX_PASSWORD.")
    
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    if login_page.is_login_page_displayed():
        login_page.login(TEST_USERNAME, TEST_PASSWORD)
    
    # Dismiss any welcome / mode-switch modal
    nav = NavigationPage(page, BASE_URL)
    nav.dismiss_modal_if_present()
    return page


def test_task1_landing_page_buttons(page: Page):
    """
    Task 1: Landing page buttons (page "/")
    - Click 'Launch Platform'. The URL should end in /clinical.
    - Click 'Start Learning'. The URL should end in /education.
    """
    ensure_authenticated(page)
    nav = NavigationPage(page, BASE_URL)

    # 1. Test 'Launch Platform' from landing page "/"
    page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    nav.dismiss_modal_if_present()

    launch_btn = nav.get_launch_platform_btn()
    assert launch_btn.first.is_visible(), "'Launch Platform' button is not visible on landing page!"
    launch_btn.first.click()
    page.wait_for_load_state("networkidle", timeout=30000)
    assert page.url.rstrip("/").endswith("/clinical"), f"Expected URL to end in /clinical, got: {page.url}"

    # 2. Test 'Start Learning' from landing page "/"
    page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    nav.dismiss_modal_if_present()

    start_learning_btn = nav.get_start_learning_btn()
    assert start_learning_btn.first.is_visible(), "'Start Learning' button is not visible on landing page!"
    start_learning_btn.first.click()
    page.wait_for_load_state("networkidle", timeout=30000)
    assert page.url.rstrip("/").endswith("/education"), f"Expected URL to end in /education, got: {page.url}"


def test_task2_landing_page_phone_view(page: Page):
    """
    Task 2: Landing page on a phone view (screen size 390 x 844)
    - At first only 'Launch Platform' is visible. 'Start Learning' is hidden.
    - Click the 'Education' tab. 'Start Learning' shows and 'Launch Platform' hides.
    - Click the 'Clinical' tab. 'Launch Platform' shows again.
    """
    ensure_authenticated(page)
    nav = NavigationPage(page, BASE_URL)

    # Set mobile viewport
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    nav.dismiss_modal_if_present()

    launch_btn = nav.get_launch_platform_btn()
    start_learning_btn = nav.get_start_learning_btn()

    # Step 1: At first only 'Launch Platform' is visible. 'Start Learning' is hidden.
    assert launch_btn.first.is_visible(), "On mobile initial view, 'Launch Platform' should be visible!"
    assert not start_learning_btn.first.is_visible(), "On mobile initial view, 'Start Learning' should be hidden!"

    # Step 2: Click the 'Education' tab. 'Start Learning' shows and 'Launch Platform' hides.
    nav.click_education_tab()
    assert start_learning_btn.first.is_visible(), "After clicking 'Education' tab, 'Start Learning' should show!"
    assert not launch_btn.first.is_visible(), "After clicking 'Education' tab, 'Launch Platform' should hide!"

    # Step 3: Click the 'Clinical' tab. 'Launch Platform' shows again.
    nav.click_clinical_tab()
    assert launch_btn.first.is_visible(), "After clicking 'Clinical' tab, 'Launch Platform' should show again!"

    # Reset viewport
    page.set_viewport_size({"width": 1280, "height": 720})


def test_task3_education_cards(page: Page):
    """
    Task 3: Education cards (page "/education")
    - Click 'Take Quiz'. The URL should end in /quiz.
    - Click 'Drug Monograph', 'Lectures' and 'Patient Case Simulation' one at a time. The URL should stay /education.
    """
    ensure_authenticated(page)
    nav = NavigationPage(page, BASE_URL)

    # Navigate to /education cleanly via 'Start Learning'
    nav.navigate_to_education()

    # 1. Click 'Take Quiz'. URL should end in /quiz.
    nav.click_take_quiz()
    assert page.url.rstrip("/").endswith("/quiz"), f"Expected URL to end in /quiz after 'Take Quiz', got: {page.url}"

    # Return to /education
    page.go_back(wait_until="networkidle")
    page.wait_for_timeout(1000)
    if not page.url.rstrip("/").endswith("/education"):
        nav.navigate_to_education()

    # 2. Click 'Drug Monograph', 'Lectures' and 'Patient Case Simulation' one at a time. URL should stay /education.
    for card_name in ["Drug Monograph", "Lectures", "Patient Case Simulation"]:
        nav.click_education_card(card_name)
        assert page.url.rstrip("/").endswith("/education"), (
            f"After clicking card '{card_name}', URL should stay /education but got: {page.url}"
        )


def test_task4_floating_mode_switch(page: Page):
    """
    Task 4: Floating mode switch
    - Login & click 'Start Learning' -> /education.
    - Click 'Switch to clinical mode' floating button -> /clinical.
    - On /clinical, click 'Switch to education mode' -> /education.
    - Click 'Switch to clinical mode' -> back to /clinical.
    - The button must not appear on '/', '/terms-of-use' or '/shared_chat'.
    """
    ensure_authenticated(page)
    nav = NavigationPage(page, BASE_URL)

    # 1. Click 'Start Learning' -> /education
    nav.navigate_to_education()
    assert page.url.rstrip("/").endswith("/education"), (
        f"Expected URL to end in /education after 'Start Learning', got: {page.url}"
    )

    # 2. Click 'Switch to clinical mode' -> /clinical
    nav.click_switch_to_clinical_mode()
    assert page.url.rstrip("/").endswith("/clinical"), (
        f"Expected URL to end in /clinical after switching to clinical mode, got: {page.url}"
    )

    # 3. On /clinical, click 'Switch to education mode' -> /education
    nav.click_switch_to_education_mode()
    assert page.url.rstrip("/").endswith("/education"), (
        f"Expected URL to end in /education after switching to education mode, got: {page.url}"
    )

    # 4. Click 'Switch to clinical mode' -> back to /clinical
    nav.click_switch_to_clinical_mode()
    assert page.url.rstrip("/").endswith("/clinical"), (
        f"Expected URL to end in /clinical after switching back, got: {page.url}"
    )

    # 5. The button must not appear on '/', '/terms-of-use' or '/shared_chat'
    page.goto(f"{BASE_URL.rstrip('/')}/", wait_until="networkidle", timeout=30000)
    nav.dismiss_modal_if_present()
    switch_btn = nav.get_floating_mode_switch_btn()
    assert not (switch_btn.count() > 0 and switch_btn.first.is_visible()), (
        "Floating mode switch button unexpectedly appeared on '/'!"
    )

    nav.navigate_to_terms_of_use()
    assert page.url.rstrip("/").endswith("/terms-of-use"), (
        f"Expected URL to end in /terms-of-use, got: {page.url}"
    )
    switch_btn = nav.get_floating_mode_switch_btn()
    assert not (switch_btn.count() > 0 and switch_btn.first.is_visible()), (
        "Floating mode switch button unexpectedly appeared on '/terms-of-use'!"
    )

    page.goto(f"{BASE_URL.rstrip('/')}/shared_chat", wait_until="networkidle", timeout=30000)
    nav.dismiss_modal_if_present()
    switch_btn = nav.get_floating_mode_switch_btn()
    assert not (switch_btn.count() > 0 and switch_btn.first.is_visible()), (
        "Floating mode switch button unexpectedly appeared on '/shared_chat'!"
    )


def test_task5_learn_more_link(page: Page):
    """
    Task 5: 'Learn more' link
    - On /clinical, click 'Learn more' in the sidebar. The URL should end in /creator-insight.
    """
    ensure_authenticated(page)
    nav = NavigationPage(page, BASE_URL)

    nav.navigate_to_clinical()

    target_page = nav.click_learn_more()
    assert target_page.url.rstrip("/").endswith("/creator-insight"), (
        f"Expected URL to end in /creator-insight after clicking 'Learn more', got: {target_page.url}"
    )
    if target_page != page:
        target_page.close()


def test_task6_broken_share_links(page: Page):
    """
    Task 6: Broken share links
    - Open /shared_chat with no uId. Expect: 'No session ID found in URL. Please check the shared link.'
    - Open /shared_chat?uId=ab. Expect: 'Unable to load conversation'
    """
    # 1. Open /shared_chat with no uId
    page.goto(f"{BASE_URL.rstrip('/')}/shared_chat", wait_until="networkidle", timeout=30000)
    expected_msg_1 = "No session ID found in URL. Please check the shared link."
    assert page.get_by_text(expected_msg_1).first.is_visible(), (
        f"Expected message '{expected_msg_1}' was not displayed on /shared_chat!"
    )

    # 2. Open /shared_chat?uId=ab
    page.goto(f"{BASE_URL.rstrip('/')}/shared_chat?uId=ab", wait_until="networkidle", timeout=30000)
    assert page.get_by_text(re.compile(r"Unable to load conversation", re.I)).first.is_visible(), (
        "Expected 'Unable to load conversation' was not displayed on /shared_chat?uId=ab!"
    )
