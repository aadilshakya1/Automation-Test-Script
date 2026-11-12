import json
import zipfile
from pathlib import Path
import pytest

from utils.app_explorer import summarize_page_objects
from utils.task_reader import NoTaskFilesError, load_tasks
from utils.test_generator import (
    ApprovalRequiredError,
    LLMTestGenerator,
    MissingLLMKeyError,
    GenerationError,
    approve_generated_tests,
    execute_generated_pytest,
    is_generated_suite_approved,
)


def _create_minimal_docx(path: Path, lines: list[str]):
    """Builds a minimal valid .docx zip archive for testing Word document extraction."""
    body_xml = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in lines
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)


def test_load_tasks_supports_txt_md_and_docx_and_ignores_readme(tmp_path: Path):
    """Verifies task reader parses .txt, .md, and .docx while ignoring README.md."""
    (tmp_path / "README.md").write_text("Ignore me", encoding="utf-8")
    (tmp_path / "01_nav.txt").write_text("Click Launch Platform and check /clinical.", encoding="utf-8")
    _create_minimal_docx(
        tmp_path / "02_clinical.docx",
        ["Open manual entry.", "Select synthetic gene CYP2C19 and verify results."],
    )

    docs = load_tasks(task_dir=tmp_path)
    names = [d.filename for d in docs]

    assert names == ["01_nav.txt", "02_clinical.docx"]
    assert "Launch Platform" in docs[0].content
    assert "CYP2C19" in docs[1].content


def test_load_tasks_raises_when_folder_has_only_readme(tmp_path: Path):
    """Ensures NoTaskFilesError is raised cleanly if no user task docs exist."""
    (tmp_path / "README.md").write_text("Instructions only", encoding="utf-8")
    with pytest.raises(NoTaskFilesError):
        load_tasks(task_dir=tmp_path)


def test_summarize_page_objects_includes_core_classes():
    """Confirms app explorer summarizes LoginPage, ClinicalPage, and NavigationPage."""
    summary = summarize_page_objects()
    assert "class LoginPage" in summary
    assert "class ClinicalPage" in summary
    assert "class NavigationPage" in summary
    assert "navigate_to_clinical" in summary


def test_llm_generator_writes_scenarios_and_valid_pytest_and_enforces_approval(tmp_path: Path):
    """
    Tests end-to-end pipeline (Task -> LLM -> test_generated.py -> Approval Gate -> Pytest PASS):
    1. Generates scenarios + valid Python pytest file using mock LLM transport.
    2. Verifies execution is blocked until user approves.
    3. Approves and runs pytest, verifying PASS result.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "sample.md").write_text(
        "Verify landing page title and arithmetic sanity check.", encoding="utf-8"
    )
    tasks = load_tasks(task_dir=task_dir)

    mock_response = {
        "scenarios": [
            {
                "id": "TC01",
                "title": "Sample generated test executes cleanly",
                "steps": ["Run sample assertion"],
                "expected_result": "Assertion passes cleanly.",
                "synthetic_data_used": "CYP2C19 (synthetic)",
            }
        ],
        "pytest_code": (
            "def test_generated_smoke():\n"
            "    gene = 'CYP2C19'\n"
            "    assert gene.startswith('CYP')\n"
        ),
    }

    test_out = tmp_path / "tests" / "test_generated.py"
    plan_out = tmp_path / "artifacts" / "generated_scenarios.md"
    state_out = tmp_path / "artifacts" / "generated_status.json"

    generator = LLMTestGenerator(
        api_key="sk-or-v1-test",
        transport=lambda sys_p, usr_p: json.dumps(mock_response),
    )
    result = generator.generate(
        tasks=tasks,
        page_object_summary=summarize_page_objects(),
        live_snapshots=[{"route": "/", "buttons": ["Launch Platform", "Start Learning"]}],
        output_test_path=test_out,
        output_plan_path=plan_out,
        state_path=state_out,
    )

    assert test_out.exists()
    assert plan_out.exists()
    assert state_out.exists()
    assert result.approved is False
    assert not is_generated_suite_approved(test_path=test_out, state_path=state_out)

    # Attempting to run without approval must raise ApprovalRequiredError
    with pytest.raises(ApprovalRequiredError):
        execute_generated_pytest(test_path=test_out, state_path=state_out, approve=False)

    # Approve and run pytest
    approve_generated_tests(test_path=test_out, state_path=state_out)
    assert is_generated_suite_approved(test_path=test_out, state_path=state_out)

    outcome = execute_generated_pytest(test_path=test_out, state_path=state_out)
    assert outcome["status"] == "PASSED"
    assert outcome["exit_code"] == 0


def test_llm_generator_rejects_invalid_python_syntax(tmp_path: Path):
    """Ensures malformed Python code from LLM is caught by ast.parse before writing."""
    (tmp_path / "t.txt").write_text("Check login", encoding="utf-8")
    tasks = load_tasks(task_dir=tmp_path)

    bad_payload = {
        "scenarios": [{"id": "TC01", "title": "Bad syntax", "steps": ["x"], "expected_result": "y"}],
        "pytest_code": "def broken_syntax(:\n    pass",
    }

    gen = LLMTestGenerator(
        api_key="sk-or-v1-test",
        transport=lambda s, u: json.dumps(bad_payload),
    )
    with pytest.raises(GenerationError, match="invalid Python syntax"):
        gen.generate(
            tasks=tasks,
            page_object_summary="summary",
            output_test_path=tmp_path / "test_generated.py",
            output_plan_path=tmp_path / "plan.md",
            state_path=tmp_path / "state.json",
        )


def test_llm_generator_missing_key_raises(tmp_path: Path):
    """Ensures MissingLLMKeyError is raised when no API key is configured."""
    (tmp_path / "t.txt").write_text("Check login", encoding="utf-8")
    tasks = load_tasks(task_dir=tmp_path)
    gen = LLMTestGenerator(api_key="")
    with pytest.raises(MissingLLMKeyError):
        gen.generate(tasks=tasks, page_object_summary="summary")
