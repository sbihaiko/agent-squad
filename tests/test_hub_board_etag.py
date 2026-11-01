"""The served board answers 304 to a page that holds it (#973).

The board is built once per change and kept serialized with an ETag. Its time-dependent values are absolute instants,
so an unchanged board keeps its ETag while time passes. The suite serves a fixture over HTTP; it never reads the wall clock.
"""
import http.client
import json
import pathlib

from tests.test_hub_board_pending import card, hub, served  # noqa: F401  (fixtures)

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
CASE = "test case #306 | fail | abc1234 | 2026-10-14T01:00:00Z"
CLOSED_ROWS = 300
SIZE_BOUND = 12_000   # bytes: a closed row costs about 100, so shipping them again passes this by far


def fixture(mod, closed=CLOSED_ROWS):
    shut = [card(1000 + n, mod.COLUMN["issue_closed"], s="CLOSED", t="a closed card with a title of some length") for n in range(closed)]
    mod.board_cache = {"t": 1000.0, "rt": 1000, "err": None, "errs": [], "prs": [], "lc": {}, "cases": [CASE],
                       "items": [card(1, "Todo"), card(2, "Backlog"), *shut]}


def get(port, tag=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": f"127.0.0.1:{port}"}
    if tag:
        headers["If-None-Match"] = tag
    conn.request("GET", "/api/board", headers=headers)
    answer = conn.getresponse()
    out = answer.status, answer.getheader("ETag"), answer.read()
    conn.close()
    return out


def test_a_matching_etag_gets_304_and_no_body(served):  # noqa: F811
    mod, _, port = served
    fixture(mod)
    status, tag, body = get(port)
    assert status == 200 and tag and json.loads(body)["groups"]
    status, again, body = get(port, tag)
    assert (status, body) == (304, b"") and again == tag


def test_another_etag_gets_the_whole_board(served):  # noqa: F811
    mod, _, port = served
    fixture(mod)
    status, _, body = get(port, '"not-this-one"')
    assert status == 200 and json.loads(body)["groups"]


def test_a_changed_board_changes_the_etag(served):  # noqa: F811
    mod, _, port = served
    fixture(mod)
    _, tag, _ = get(port)
    mod.board_cache["items"][0]["st"] = "Backlog"
    status, new, _ = get(port, tag)
    assert status == 200 and new != tag


def test_time_passing_keeps_the_body_and_its_etag(hub, monkeypatch):  # noqa: F811
    mod, _ = hub
    fixture(mod)
    mod.board_cache["prs"] = [{"n": 5, "t": "a pr", "u": "2026-10-14T01:00:00Z", "r": "x/y", "h": "abcdef012", "cl": []}]
    monkeypatch.setattr(mod.time, "time", lambda: 5000.0)
    first = json.dumps(mod.board(), sort_keys=True)
    monkeypatch.setattr(mod.time, "time", lambda: 90000.0)
    assert json.dumps(mod.board(), sort_keys=True) == first


def test_closed_rows_never_ship_again(served):  # noqa: F811
    mod, _, port = served
    fixture(mod)
    status, _, body = get(port)
    assert status == 200 and len(body) < SIZE_BOUND, f"{len(body)} bytes: closed rows ship again"
    assert not any(i["n"] >= 1000 for g in json.loads(body)["groups"] for i in g["items"])


def test_both_pages_send_if_none_match_and_keep_the_board_on_304():
    desktop = (SCRIPTS / "hub.py").read_text()
    mobile = (SCRIPTS / "hub-mobile.html").read_text()
    assert "If-None-Match" in desktop and "r.status===304" in desktop
    assert "If-None-Match" in mobile and "r.status === 304" in mobile
