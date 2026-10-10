"""Browser work goes through ego-browser, one private task space per goal; no test launches headless Chrome."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_test_file_launches_headless_chrome_or_names_a_chrome_lock():
    bad = [p.name for p in (ROOT / "tests").glob("*.py")
           if p.name != Path(__file__).name and ("--headless" in p.read_text() or "Google Chrome" in p.read_text() or "pytest.mark.chrome" in p.read_text())]
    assert bad == []


def test_the_ego_driver_opens_a_new_task_space_per_call_so_calls_run_side_by_side():
    src = (ROOT / "tests" / "test_dashboard_page.py").read_text()
    assert "uuid.uuid4()" in src and "task.finish" in src and "LOCK_EX" not in (ROOT / "tests" / "conftest.py").read_text()


def test_claude_md_sends_browser_work_to_ego_browser():
    text = (ROOT / "CLAUDE.md").read_text()
    assert "ego-browser, never headless Chrome" in text and "several goals at once" in text


def test_a_test_run_opens_no_page_in_the_owners_browser(monkeypatch):
    import os
    import webbrowser
    assert os.environ.get("SQUAD_HEADLESS") == "1"
    assert webbrowser.open("http://127.0.0.1:1/") is False
