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
│   ├── clinical_page.py        # Page Object for /clinical (modes, form inputs, threads, sharing)
│   └── navigation_page.py      # Page Object for Pages & Navigation (landing, phone view, education cards, switch, links)
├── utils/
│   ├── __init__.py
│   ├── ai_judge.py             # Gemini-powered AI Judge for PGx clinical output validation
│   ├── task_reader.py          # Reads human-English .txt, .md, and .docx specs from task/
│   ├── app_explorer.py         # Summarizes Page Objects & explores live app UI controls
│   └── test_generator.py       # Generates scenarios + tests/test_generated.py with approval gate
├── task/
│   └── README.md               # Drop human-English .txt, .md, or .docx task files here
└── tests/
    ├── __init__.py
    ├── test_login.py           # Smoke, invalid credentials, and navigation tests
    ├── test_task1.py           # Task 1: Prompt Mode vs Manual Entry (Part A & Part B)
    ├── test_task2.py           # Task 2: Thread isolation, persistence across reload, and sharing
    ├── test_pages_and_navigation.py # Tasks 1-6: Pages & Navigation suite
    ├── test_ai_judge.py        # Unit tests + live Playwright/Gemini PGx evaluation suite
    └── test_ai_pipeline.py     # Unit tests for Task -> LLM -> Approval -> Pytest pipeline
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
| `nav_task1` | **Pages & Navigation Task 1**: Landing page buttons ("Launch Platform" -> `/clinical`, "Start Learning" -> `/education`). |
| `nav_task2` | **Pages & Navigation Task 2**: Landing page responsive phone view (390x844), tab toggle between Clinical and Education. |
| `nav_task3` | **Pages & Navigation Task 3**: Education cards ("Take Quiz" -> `/quiz`, other cards stay `/education`). |
| `nav_task4` | **Pages & Navigation Task 4**: Floating mode switch on `/clinical` & `/education`; absence on `/`, `/terms-of-use`, `/shared_chat`. |
| `nav_task5` | **Pages & Navigation Task 5**: Sidebar "Learn more" link navigating to `/creator-insight`. |
| `nav_task6` | **Pages & Navigation Task 6**: Broken share links error validation on `/shared_chat` and `/shared_chat?uId=ab`. |
| `nav_all` | Runs all 6 Pages and Navigation tasks. |
| `ai_judge` | **Gemini AI Judge**: Runs a synthetic PGx assessment (`CYP2C19`, `Intermediate Metabolizer`) and evaluates clinical response quality & safety via Gemini. |
| `generate_tests` | **AI Task-to-Test Generator**: Reads docs in `task/`, inspects the app + Page Objects, writes scenarios & `tests/test_generated.py`, and pauses for approval. |
| `run_generated` | **Approved Generated Suite**: Runs `pytest tests/test_generated.py -v` after `--approve` and reports PASS/FAIL. |
| `all` | Runs all core regression scenarios sequentially. |

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
| `--scenario` | `smoke`, `invalid_login`, `valid_login`, `navigation`, `task1_a`, `task1_b`, `task2`, `nav_task1`..`nav_task6`, `nav_all`, `ai_judge`, `generate_tests`, `run_generated`, `all` | `all` | Specific scenario to execute |
| `--url` | String (URL) | `https://pgx-dev.datum.bio/` | Target site to run tests against |
| `--username` | String | Environment / `None` | Account username for authenticated scenarios |
| `--password` | String | Environment / `None` | Account password for authenticated scenarios |
| `--gemini-key` | String | `GEMINI_API_KEY` env / `None` | Gemini API key for the `ai_judge` scenario |
| `--api-key` | String | `OPENROUTER_API_KEY` / `OPENAI_API_KEY` | GPT/OpenRouter key for `generate_tests` |
| `--llm-model` | String | `openai/gpt-4o` / `gpt-4o` | Model name for `generate_tests` |
| `--task-file` | File path | `None` (all in `task/`) | Specific task doc inside `task/` |
| `--approve` | Flag | `False` | Approve `tests/test_generated.py` for pytest execution |
| `--no-explore` | Flag | `False` | Skip live browser exploration during generation |
| `--interactive` | Flag | `False` | Prompt `[y/N]` interactively before running pytest |
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
pytest tests/test_pages_and_navigation.py -v -s
```
