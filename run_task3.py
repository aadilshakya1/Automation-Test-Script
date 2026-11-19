#!/usr/bin/env python3
"""
Dedicated Runner for Third QA Task (Clinical Screen Chat, Examples & Profile Menu).
Allows running sub-tasks individually or together with optional headed mode and HTML reporting.

Usage:
    # Run all sub-tasks in Task 3
    python run_task3.py --task all

    # Run Task 1 (Send button state)
    python run_task3.py --task 1

    # Run Task 2 (Enter vs Shift+Enter)
    python run_task3.py --task 2

    # Run Task 3 (Suggested prompts carousel)
    python run_task3.py --task 3

    # Run Task 4 (User profile menu)
    python run_task3.py --task 4

    # Run with visible browser (headed)
    python run_task3.py --task 1 --headed
"""

import argparse
import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
VENV_PYTEST = PROJECT_ROOT / ".venv" / "Scripts" / "pytest.exe"
if not VENV_PYTEST.exists():
    VENV_PYTEST = PROJECT_ROOT / ".venv" / "bin" / "pytest"
if not VENV_PYTEST.exists():
    VENV_PYTEST = Path("pytest")

TASK_MAPPING = {
    "1": ("test_task3_1_send_button_state", "Task 1: Send button state"),
    "send_button": ("test_task3_1_send_button_state", "Task 1: Send button state"),
    "2": ("test_task3_2_enter_vs_shift_enter", "Task 2: Enter vs Shift+Enter"),
    "enter_shift": ("test_task3_2_enter_vs_shift_enter", "Task 2: Enter vs Shift+Enter"),
    "3": ("test_task3_3_example_prompts_navigation", "Task 3: Example prompts carousel"),
    "example_prompts": ("test_task3_3_example_prompts_navigation", "Task 3: Example prompts carousel"),
    "4": ("test_task3_4_user_profile_menu", "Task 4: User profile menu & Free plan guard"),
    "profile_menu": ("test_task3_4_user_profile_menu", "Task 4: User profile menu & Free plan guard"),
    "all": ("", "All Task 3 sub-tasks"),
}


def main():
    parser = argparse.ArgumentParser(description="Run Third QA Task scenarios individually or together")
    parser.add_argument(
        "--task", "-t",
        choices=["1", "2", "3", "4", "send_button", "enter_shift", "example_prompts", "profile_menu", "all"],
        default="all",
        help="Sub-task to execute (1, 2, 3, 4, or 'all'). Default: all"
    )
    parser.add_argument("--headed", action="store_true", help="Run browser in visible UI mode")
    parser.add_argument("--html", action="store_true", default=True, help="Generate HTML report in artifacts/report.html")
    parser.add_argument("--no-html", dest="html", action="store_false", help="Disable HTML report generation")

    args = parser.parse_args()

    filter_fn, description = TASK_MAPPING.get(args.task, ("", f"Task {args.task}"))

    print("\n" + "=" * 65)
    print(f"  RUNNING THIRD QA TASK: {description.upper()}")
    print(f"  Mode   : {'HEADED (Visible UI)' if args.headed else 'HEADLESS (Background)'}")
    print(f"  Target : https://pgx-dev.datum.bio/clinical")
    print("=" * 65 + "\n")

    cmd = [
        str(VENV_PYTEST),
        "tests/test_task3.py",
        "-v",
        "-s"
    ]

    if filter_fn:
        cmd.extend(["-k", filter_fn])

    if args.html:
        report_path = PROJECT_ROOT / "artifacts" / "report.html"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend([f"--html={report_path}", "--self-contained-html"])

    env = os.environ.copy()
    if args.headed:
        env["HEADLESS"] = "false"
    else:
        env["HEADLESS"] = "true"

    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env)
        exit_code = proc.returncode
    except Exception as e:
        print(f"[ERROR] Failed to run pytest: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 65)
    if exit_code == 0:
        print(f"  RESULT: PASSED (Exit code: 0)")
        if args.html:
            print(f"  Report: artifacts/report.html")
    else:
        print(f"  RESULT: FAILED (Exit code: {exit_code})")
    print("=" * 65 + "\n")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
