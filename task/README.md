# Task Documents Folder

Place plain-English test task files in this folder (`.txt`, `.md`, or `.docx`).

## Supported Formats
- `.docx` (Microsoft Word documents)
- `.md` (Markdown files)
- `.txt` (Plain text files)

## Workflow (`Task -> LLM -> tests/test_generated.py -> Approve -> Pytest`)

1. Drop your task file here (for example `task/my_feature_task.docx` or `task/clinical_task.md`).
2. Generate test scenarios and `tests/test_generated.py` (stops for your review):
   ```powershell
   .\.venv\Scripts\python.exe runner.py --scenario generate_tests --username "YOUR_EMAIL" --password "YOUR_PASSWORD"
   ```
3. Review the proposed scenarios in `artifacts/generated_scenarios.md` and the code in `tests/test_generated.py`.
4. Approve and execute the generated tests:
   ```powershell
   .\.venv\Scripts\python.exe runner.py --scenario run_generated --approve --username "YOUR_EMAIL" --password "YOUR_PASSWORD"
   ```
   Or run directly with pytest after review:
   ```powershell
   .\.venv\Scripts\pytest.exe tests/test_generated.py -v
   ```

*(Note: This `README.md` is ignored automatically when scanning `task/` for task documents.)*
