import os
from pathlib import Path

# Load local .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Base Paths
PROJECT_ROOT = Path(__file__).parent.resolve()
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
SCREENSHOTS_DIR = ARTIFACTS_DIR / "screenshots"

# Ensure directories exist
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Test Configuration
BASE_URL = os.environ.get("PGX_URL", "https://pgx-dev.datum.bio/")
DEFAULT_TIMEOUT = int(os.environ.get("PLAYWRIGHT_TIMEOUT", "30000"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")

# Optional credentials for authenticated tests
TEST_USERNAME = os.environ.get("PGX_USERNAME", "")
TEST_PASSWORD = os.environ.get("PGX_PASSWORD", "")

# Gemini AI Judge Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
