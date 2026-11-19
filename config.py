import os
from pathlib import Path

# Base Paths
PROJECT_ROOT = Path(__file__).parent.resolve()
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
SCREENSHOTS_DIR = ARTIFACTS_DIR / "screenshots"
TASK_DIR = PROJECT_ROOT / "task"
GENERATED_TEST_PATH = PROJECT_ROOT / "tests" / "test_generated.py"
GENERATED_PLAN_PATH = ARTIFACTS_DIR / "generated_scenarios.md"
GENERATED_STATE_PATH = ARTIFACTS_DIR / "generated_status.json"


def _load_env_file():
    """Loads key=value pairs from .env if python-dotenv is unavailable."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

# Ensure directories exist
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
TASK_DIR.mkdir(parents=True, exist_ok=True)

# Test Configuration
BASE_URL = os.environ.get("PGX_URL", "https://pgx-dev.datum.bio/")
DEFAULT_TIMEOUT = int(os.environ.get("PLAYWRIGHT_TIMEOUT", "30000"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")

# Optional credentials for authenticated tests
TEST_USERNAME = os.environ.get("PGX_USERNAME", "")
TEST_PASSWORD = os.environ.get("PGX_PASSWORD", "")

# Gemini AI Judge Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# GPT / OpenRouter / Gemini Test Generator Configuration
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").lower()

if LLM_PROVIDER == "gemini" and GEMINI_API_KEY:
    LLM_API_KEY = GEMINI_API_KEY
    LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    LLM_MODEL = os.environ.get("LLM_MODEL", GEMINI_MODEL)
else:
    LLM_API_KEY = (
        OPENAI_API_KEY
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or GEMINI_API_KEY
    )
    LLM_BASE_URL = os.environ.get(
        "LLM_BASE_URL",
        "https://openrouter.ai/api/v1" if LLM_API_KEY.startswith("sk-or-") else "https://api.openai.com/v1",
    )
    if os.environ.get("LLM_MODEL"):
        LLM_MODEL = os.environ["LLM_MODEL"]
    elif LLM_API_KEY.startswith("sk-or-"):
        LLM_MODEL = "openai/gpt-4o"
    elif LLM_API_KEY.startswith(("AIza", "AQ.")) or (GEMINI_API_KEY and LLM_API_KEY == GEMINI_API_KEY):
        LLM_MODEL = GEMINI_MODEL
    else:
        LLM_MODEL = "gpt-4o"
