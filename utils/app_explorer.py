import ast
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from config import BASE_URL, DEFAULT_TIMEOUT, PROJECT_ROOT
from pages.login_page import LoginPage
from pages.navigation_page import NavigationPage


def summarize_page_objects(pages_dir: Optional[Path] = None) -> str:
    """
    Extracts class names, method signatures, and docstrings from pages/*.py
    so the test generator reuses existing Page Object helpers instead of guessing.
    """
    target_dir = pages_dir or (PROJECT_ROOT / "pages")
    if not target_dir.exists():
        return "No Page Object files found."

    sections: List[str] = []
    for py_file in sorted(target_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        module_rel = f"pages.{py_file.stem}"
        lines: List[str] = [f"### {module_rel} ({py_file.name})"]

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                cls_doc = ast.get_docstring(node) or ""
                lines.append(f"class {node.name}:  # {cls_doc.splitlines()[0] if cls_doc else ''}")
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                        args = [a.arg for a in item.args.args if a.arg != "self"]
                        doc = ast.get_docstring(item) or ""
                        doc_line = doc.splitlines()[0].strip() if doc else ""
                        sig = f"  def {item.name}({', '.join(args)})"
                        if doc_line:
                            sig += f"  # {doc_line}"
                        lines.append(sig)
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _snapshot_visible_controls(page: Page, route_label: str) -> Dict[str, Any]:
    """Collects visible interactive controls and headings using DOM accessibility info."""
    data = page.evaluate(
        """() => {
            const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            };
            const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
                .filter(visible)
                .map(el => (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\\s+/g, ' '))
                .filter(Boolean);
            const links = Array.from(document.querySelectorAll('a, [role="link"]'))
                .filter(visible)
                .map(el => ({
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' '),
                    href: el.getAttribute('href') || ''
                }))
                .filter(item => item.text);
            const tabs = Array.from(document.querySelectorAll('[role="tab"]'))
                .filter(visible)
                .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim())
                .filter(Boolean);
            const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
                .filter(visible)
                .map(el => el.innerText.trim().replace(/\\s+/g, ' '))
                .filter(Boolean);
            const inputs = Array.from(document.querySelectorAll('input, textarea'))
                .filter(visible)
                .map(el => el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('name') || el.type)
                .filter(Boolean);
            return {
                url: window.location.href,
                title: document.title,
                headings: headings.slice(0, 12),
                buttons: [...new Set(buttons)].slice(0, 30),
                links: links.slice(0, 20),
                tabs: [...new Set(tabs)],
                inputs: [...new Set(inputs)].slice(0, 15)
            };
        }"""
    )
    data["route"] = route_label
    return data


def explore_live_app(
    page: Page,
    base_url: str = BASE_URL,
    username: str = "",
    password: str = "",
) -> List[Dict[str, Any]]:
    """
    Explores key routes of the live PGx application (/, /clinical, /education)
    and returns accessible control snapshots to ground LLM test generation.
    """
    snapshots: List[Dict[str, Any]] = []
    login_page = LoginPage(page, base_url)
    login_page.navigate()

    if login_page.is_login_page_displayed():
        snapshots.append(_snapshot_visible_controls(page, "login"))
        if not username or not password:
            return snapshots
        login_page.login(username, password)
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)

    nav = NavigationPage(page, base_url)
    nav.dismiss_modal_if_present()
    snapshots.append(_snapshot_visible_controls(page, "/ (landing)"))

    try:
        nav.navigate_to_clinical()
        snapshots.append(_snapshot_visible_controls(page, "/clinical"))
    except Exception:
        pass

    try:
        nav.navigate_to_education()
        snapshots.append(_snapshot_visible_controls(page, "/education"))
    except Exception:
        pass

    return snapshots
