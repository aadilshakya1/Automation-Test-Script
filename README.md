# Playwright Automated Testing Suite for PGX Dev (Datum Bio)

An automated testing framework tailored for **https://pgx-dev.datum.bio/**, designed to be executed seamlessly by both **human software engineers** and **autonomous AI agents**.

---

## 📋 Core Rules (Strictly Enforced)

1. **Dev Account**: Tests execute against the live development environment (`https://pgx-dev.datum.bio/clinical`).
2. **Never Use Real Patient Data**: Strictly use synthetic/mock user sessions.
3. **Made-Up Clinical Values**: Use made-up ages, synthetic genes (e.g. `CYP2C19`, `CYP2D6`), conditions, and medications.
4. **Accessible Locators Only**: Interactive buttons and controls are located strictly using `page.get_by_role()` and `page.get_by_text()`, never CSS classes.

---

## 📁 Project Structure

```
Playwright/
├── .gitignore                  # Git ignore rules (.venv, __pycache__, screenshots, .env)
├── config.py                   # Central configuration, environment variables & timeouts
├── runner.py                   # Agent-first CLI test runner with JSON output & exit codes
├── requirements.txt            # Project dependencies (Playwright, Pytest, Python-dotenv)
├── README.md                   # Complete documentation & usage guide
├── artifacts/
│   └── screenshots/            # Automated failure screenshots (git-tracked directory)
│       └── .gitkeep
├── pages/
│   ├── __init__.py
│   ├── login_page.py           # Page Object for authentication & platform launching
│   └── clinical_page.py        # Page Object for /clinical (modes, form inputs, threads, sharing)
└── tests/
    ├── __init__.py
    ├── test_login.py           # Smoke, invalid credentials, and navigation tests
    ├── test_task1.py           # Task 1: Prompt Mode vs Manual Entry (Part A & Part B)
    └── test_task2.py           # Task 2: Thread isolation, persistence across reload, and sharing
```

---

## 🧪 Test Suite & Scenarios

| Scenario Flag | Target / Flow |
| :--- | :--- |
| `smoke` | Page load (HTTP 200), Keycloak auth redirection, presence of core input elements. |
| `invalid_login` | Negative test: Submits invalid credentials and verifies error message banner. |
| `valid_login` | Positive authentication: Verifies valid login redirect to platform selection. |
| `navigation` | Routing checks for "Register" and "Forgot Password?" links. |
| `task1_a` | **Screen Switching**: Switches between Prompt Mode and Manual Entry 5 times; verifies no assessment loaders appear and `apiCalls` remains empty. |
| `task1_b` | **Data Retention**: Enters Age, Sex, Gene, Phenotype, Medical History, and Medications; switches modes and audits retention across all fields. |
| `task2` | **Sidebar & Sharing**: Creates Thread A (CYP2C19), starts Thread B (CYP2D6), verifies data isolation, reloads page, and validates generated share link. |
| `all` | Runs all scenarios sequentially. |

---

## 🛠️ Setup & Installation

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright browser binaries
playwright install chromium
```

---

## 🤖 AI Agent Integration Guide

AI agents can execute tests directly via shell commands and receive structured, deterministic JSON output.

### Agent Execution Command
```bash
python runner.py --scenario all --username "USER_EMAIL" --password "USER_PASSWORD" --json
```

### Exit Codes
- `0`: All requested test scenarios passed.
- `1`: One or more test scenarios failed.

### CLI Arguments

| Argument | Choices / Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--scenario` | `smoke`, `invalid_login`, `valid_login`, `navigation`, `task1_a`, `task1_b`, `task2`, `all` | `all` | Specific scenario to execute |
| `--url` | String (URL) | `https://pgx-dev.datum.bio/` | Target site to run tests against |
| `--username` | String | Environment / `None` | Account username for authenticated scenarios |
| `--password` | String | Environment / `None` | Account password for authenticated scenarios |
| `--headed` | Flag | `False` (headless) | Launch visible browser window for debugging |
| `--json` | Flag | `False` | Emits pure JSON string to `stdout` |
| `--output` | File path | `""` | Writes JSON report to a file |

### Example JSON Output for Agents
```json
{
  "timestamp": "2026-11-03T14:30:00.000000",
  "target_url": "https://pgx-dev.datum.bio/",
  "headless": true,
  "summary": {
    "total": 6,
    "passed": 6,
    "failed": 0,
    "skipped": 0,
    "status": "PASSED"
  },
  "tests": [
    {
      "scenario": "task1_a",
      "status": "PASSED",
      "duration_ms": 11420.5,
      "details": {
        "cycles_completed": 5,
        "loader_appeared": false,
        "overlay_appeared": false
      }
    },
    {
      "scenario": "task1_b",
      "status": "PASSED",
      "duration_ms": 8340.2,
      "details": {
        "field_audit": {
          "Age": "RETAINED",
          "Sex": "RETAINED",
          "Gene": "RETAINED",
          "Phenotype": "RETAINED",
          "Medical History": "RETAINED",
          "Current Medication": "RETAINED"
        },
        "failed_fields": [],
        "loader_appeared": false
      }
    }
  ]
}
```

If a test fails:
- `"status": "FAILED"`
- The `"error"` field describes the root cause.
- The `"screenshot"` field provides the exact filepath of the full-page failure screenshot captured in `artifacts/screenshots/`.

---

## 👨‍💻 Running Tests for Humans

### Run with Terminal Summary
```powershell
python runner.py --scenario task1_b --username "YOUR_DEV_EMAIL" --password "YOUR_PASSWORD"
```

### Run Visually (Headed Browser)
```powershell
python runner.py --scenario task2 --username "YOUR_DEV_EMAIL" --password "YOUR_PASSWORD" --headed
```

### Run with Pytest
```powershell
# Set credentials (or place them in a local .env file)
$env:PGX_USERNAME="YOUR_DEV_EMAIL"
$env:PGX_PASSWORD="YOUR_PASSWORD"

# Run all tests
pytest -v -s

# Run a specific test file
pytest tests/test_task1.py -v -s
pytest tests/test_task2.py -v -s
```
