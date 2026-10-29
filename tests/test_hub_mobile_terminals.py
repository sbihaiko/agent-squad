"""The mobile Terminals tab (#840): the session list, a full-screen conversation that polls its read route, older turns on
request, and every turn drawn as text. The page is read as source; the server rules are in test_sessions.py.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
PAGE = SCRIPTS / "hub-mobile.html"


def page():
    return PAGE.read_text()


def terminals_panel():
    m = re.search(r'<section data-panel="terminals"[^>]*>(.*?)</section>', page(), re.S)
    assert m, "no terminals panel"
    return m.group(0)


def terminals_js():
    m = re.search(r"// terminals:begin(.*?)// terminals:end", page(), re.S)
    assert m, "no terminals script"
    return m.group(1)


def test_the_terminals_tab_has_no_placeholder():
    assert "Placeholder" not in terminals_panel()


def test_the_session_list_reads_the_list_route():
    assert "/api/sessions\"" in terminals_js() or "/api/sessions?" in terminals_js()


def test_a_tap_on_a_session_opens_its_conversation_read_route():
    assert "/api/sessions/conversation?sid=" in terminals_js()


def test_the_open_conversation_polls_its_read_route():
    js = terminals_js()
    assert "setInterval" in js and "loadConversation" in js


def test_older_turns_come_on_request_with_a_cursor():
    assert 'id="termview-older"' in page()
    assert "before=" in terminals_js()


def test_turns_are_drawn_as_text_only():
    js = terminals_js()
    assert "textContent" in js
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js


def test_the_terminals_script_loads_nothing_from_outside_the_hub():
    assert "https://" not in terminals_js() and "http://" not in terminals_js()
