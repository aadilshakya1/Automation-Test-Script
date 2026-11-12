import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config import TASK_DIR

SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx"}
IGNORED_FILENAMES = {"readme.md", ".gitkeep"}


class NoTaskFilesError(FileNotFoundError):
    """Raised when no readable task document files exist in the task folder."""


@dataclass
class TaskDocument:
    """Represents a loaded human-English task specification."""

    filename: str
    path: Path
    content: str


def _extract_docx_text(path: Path) -> str:
    """Extracts paragraph text from a .docx archive using standard-library XML parsing."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: List[str] = []

    with zipfile.ZipFile(path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError(f"Invalid .docx file (missing word/document.xml): {path}")
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    for para in root.iter(f"{ns}p"):
        runs = [node.text for node in para.iter(f"{ns}t") if node.text]
        line = "".join(runs).strip()
        if line:
            paragraphs.append(line)

    return "\n".join(paragraphs)


def read_task_file(file_path: Path) -> TaskDocument:
    """Reads a single .txt, .md, or .docx task document into a TaskDocument."""
    resolved = Path(file_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Task file not found: {resolved}")

    ext = resolved.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported task file extension '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    if ext == ".docx":
        content = _extract_docx_text(resolved)
    else:
        content = resolved.read_text(encoding="utf-8")

    content = content.strip()
    if not content:
        raise ValueError(f"Task file is empty: {resolved}")

    return TaskDocument(filename=resolved.name, path=resolved, content=content)


def load_tasks(
    task_dir: Path = TASK_DIR,
    specific_file: Optional[Path] = None,
) -> List[TaskDocument]:
    """
    Loads either a specific task file or all supported task files inside task_dir.
    Automatically skips README.md and hidden/example files.
    """
    if specific_file is not None:
        candidate = Path(specific_file)
        if not candidate.is_absolute() and not candidate.exists():
            candidate = Path(task_dir) / candidate
        return [read_task_file(candidate)]

    directory = Path(task_dir)
    if not directory.exists():
        raise NoTaskFilesError(f"Task directory does not exist: {directory}")

    files = sorted(
        p
        for p in directory.iterdir()
        if p.is_file()
        and p.name.lower() not in IGNORED_FILENAMES
        and not p.name.startswith(".")
        and not p.name.lower().endswith(".example")
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        raise NoTaskFilesError(
            f"No task files found in '{directory}'. Add a .txt, .md, or .docx file to generate tests."
        )

    return [read_task_file(f) for f in files]
