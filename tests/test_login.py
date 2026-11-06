import pytest
from playwright.sync_api import Page, sync_playwright
from pages.login_page import LoginPage
from config import BASE_URL, TEST_USERNAME, TEST_PASSWORD, HEADLESS

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

def test_login_page_renders_elements(page: Page):
    """Smoke test: Login page renders with username, password, and sign-in button."""
    login_page = LoginPage(page, BASE_URL)
    response = login_page.navigate()
    assert response is not None, "Failed to receive HTTP response"
    assert response.status == 200, f"Expected 200 OK, got {response.status}"
    assert login_page.is_login_page_displayed(), "Login page elements not visible"



def test_invalid_credentials_shows_error(page: Page):
    """Negative test: Submitting invalid credentials shows error banner."""
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    login_page.login("invalid_user_999@datum.bio", "IncorrectPass!123")
    
    error_msg = login_page.get_error_message()
    assert "Invalid username or password" in error_msg, f"Unexpected error message: '{error_msg}'"

def test_navigation_to_registration(page: Page):
    """Flow test: Clicking 'Register' navigates to Keycloak registration page."""
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    login_page.click_register()
    
    assert "registration" in page.url.lower(), f"Did not navigate to registration: {page.url}"
    assert page.locator("input[name='firstName']").is_visible(), "First name input not visible"

def test_navigation_to_forgot_password(page: Page):
    """Flow test: Clicking 'Forgot Password?' navigates to reset password page."""
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    login_page.click_forgot_password()
    
    assert "reset-credentials" in page.url.lower(), f"Did not navigate to reset: {page.url}"
    assert page.locator("input[name='username']").is_visible(), "Username input for reset not visible"

@pytest.mark.skipif(not TEST_USERNAME or not TEST_PASSWORD, reason="Valid credentials not provided")
def test_valid_credentials_login(page: Page):
    """Positive test: Logging in with valid credentials (if configured)."""
    login_page = LoginPage(page, BASE_URL)
    login_page.navigate()
    login_page.login(TEST_USERNAME, TEST_PASSWORD)
    # Check that user is redirected away from login page
    assert "login" not in page.url.lower(), f"Still on login page: {page.url}"

