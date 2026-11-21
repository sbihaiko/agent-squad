"""The phone's warnings answer the keyboard, its message lines are announced, and it carries no terminal list
(#1457, part of #1115).

The page is read as served for the markup facts, and booted under node over the stub DOM of the move tests for the
behaviour: a warning row opens from a tap, from Enter and from Space, and the result is read back from the DOM.
"""
import json
from html.parser import HTMLParser

import pytest

from tests.hub_mobile_stub import hub, run_page  # noqa: F401  (hub is a fixture)

STATUS = {
    "loop": {"on": False, "since": "1h"},
    "live": 0, "spend_today": 0, "open_bugs": 0,
    "warnings": [
        {"text": "codex 90%", "tip": "codex is near its limit"},
        {"text": "sonnet 80%", "tip": "sonnet is near its limit"},
    ],
    "load": {"line": ""},
}


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(page):
    p = _Tags()
    p.feed(page)
    p.close()
    return p.tags


def _is_message_line(attrs):
    return "msg" in (attrs.get("class") or "").split() or attrs.get("id") == "msg"


def test_every_message_line_carries_role_status(hub):  # noqa: F811
    rows = [(tag, a) for tag, a in _tags(hub.MOBILE_PAGE) if _is_message_line(a)]
    assert rows, "the phone page carries no message line"
    unnamed = [f"{tag} id={a.get('id')}" for tag, a in rows if a.get("role") != "status"]
    assert unnamed == [], f"message lines without role=status: {unnamed}"


def test_the_phone_page_carries_no_terminal_list(hub):  # noqa: F811
    row = next((a for tag, a in _tags(hub.MOBILE_PAGE) if a.get("id") == "sessions"), None)
    assert row is None, f"the phone page still carries a terminal list: {row}"


DRIVER = """
  await __tick(20);
  const rows = $("warnings").children;
  const read = () => ({ hidden: $("warn-detail").hidden, text: $("warn-detail").textContent });
  const reset = () => { $("warn-detail").hidden = true; $("warn-detail").textContent = ""; };
  const out = { rows: rows.length, focusable: rows.map(r => r.tabIndex >= 0) };
  rows[0].onclick({});
  out.tap = read();
  reset();
  rows[0].onkeydown({ key: "Enter", preventDefault() {} });
  out.enter = read();
  reset();
  rows[0].onkeydown({ key: " ", preventDefault() {} });
  out.space = read();
  reset();
  rows[0].onkeydown({ key: "a", preventDefault() {} });
  out.other_key = read();
  reset();
  rows[1].onkeydown({ key: "Enter", preventDefault() {} });
  out.second = read();
  return out;
"""


@pytest.mark.node_bound
def test_a_warning_row_opens_by_enter_and_by_space_through_the_tap_path(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, DRIVER, routes={"/api/status": [{"body": json.dumps(STATUS)}]})
    open_tip = {"hidden": False, "text": "codex is near its limit"}
    assert out["rows"] == 2, out
    assert all(out["focusable"]), f"a warning row is not focusable: {out}"
    assert out["tap"] == open_tip, out
    assert out["enter"] == open_tip, f"Enter does not open the row the tap opens: {out}"
    assert out["space"] == open_tip, f"Space does not open the row the tap opens: {out}"
    assert out["other_key"] == {"hidden": True, "text": ""}, f"a bare key opened the row: {out}"
    assert out["second"] == {"hidden": False, "text": "sonnet is near its limit"}, \
        f"the keyboard opens the wrong row: {out}"
