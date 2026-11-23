"""The items read carries every card of the project, not only its first page (#2170).

GitHub answers the REST items route one page at a time and names the next page in the `Link` header,
`rel="next"`. The route answers a bare array with no total, so a reader that stops at the first page
reports every later card as absent: `board.sh sbihaiko 5 Todo 2151` printed `#2151 not on the board`
while the card sat on a later page. The stub below is `gh api` over a paged project: it answers one
page a call, and only a reader that follows `Link` reaches the card on page two.
"""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_pagination", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

FIELDS = [{"id": "F_STATUS", "name": "Status",
           "options": [{"id": "O_TODO", "name": {"raw": "Todo"}}]}]


def card(n, status="Todo"):
    """One item of the project, as the REST items route answers it."""
    return {"id": f"PVTI_{n}", "content": {"number": n, "repository_url": "https://api.github.com/repos/o/r"},
            "fields": [{"name": "Status", "value": {"name": status}}], "isArchived": False, "archived_at": None}


class PagedGh:
    """`gh api` over a project of several pages. A page read with `--include` carries the Link header that
    names the next page; a read without it is the reader that never asks for one, and answers page one."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, cmd, input=None, **kw):
        self.calls.append(" ".join(cmd))
        if "/fields?" in " ".join(cmd):
            return subprocess.CompletedProcess(cmd, 0, json.dumps(FIELDS), "")
        if "graphql" in cmd:   # the one-issue fallback read answers no card of this project
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"data": {"repository": {"issue": {"projectItems": {"nodes": []}}}}}), "")
        if "--include" not in cmd:
            assert "--paginate" in cmd, cmd   # the reader asks gh to paginate for it, and never sees a next page
            return subprocess.CompletedProcess(cmd, 0, self.body(0, cmd), "")
        i = self.after(" ".join(cmd))
        head = "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n"
        if i + 1 < len(self.pages):
            head += ('Link: <https://api.github.com/user/1/projectsV2/5/items'
                     f'?per_page=100&after={i + 1}>; rel="next"\r\n')
        return subprocess.CompletedProcess(cmd, 0, head + "\r\n" + self.body(i, cmd), "")

    @staticmethod
    def after(s):
        m = re.search(r"\bafter=(\d+)", s)
        return int(m.group(1)) if m else 0

    def body(self, i, cmd):
        page = json.dumps(self.pages[i])
        if "--jq" not in cmd:
            return page + "\n"
        jq = cmd[cmd.index("--jq") + 1]
        return subprocess.run([shutil.which("jq"), "-r", jq], input=page, capture_output=True, text=True, check=True).stdout


def seam(tmp_path, gh):
    return issue_state.BoardBinding("o", "5", "o/r", kind="users", state_dir=tmp_path, run=gh,
                                    env={"SG_BOARD_READ_BACKOFF": "0"})


def test_the_item_map_carries_a_card_that_sits_on_page_two(tmp_path):
    """#2170: the card is on page two, and `board.sh` reads the item map to find it."""
    gh = PagedGh([[card(1)], [card(2)]])

    assert "2" in seam(tmp_path, gh).items()


def test_the_board_read_carries_a_card_that_sits_on_page_two(tmp_path):
    """The Status read the compare and the hub use reaches the card too, not only the item map."""
    gh = PagedGh([[card(1)], [card(2)]])

    assert seam(tmp_path, gh).read_all().get("2") == "Todo"


def test_the_read_follows_the_link_to_the_last_page(tmp_path):
    """A project of three pages: the reader stops on the page no Link follows, never at the second."""
    gh = PagedGh([[card(1)], [card(2)], [card(3)]])

    assert set(seam(tmp_path, gh).items()) == {"1", "2", "3"}


def test_a_move_that_cannot_reach_the_card_names_it(tmp_path):
    """A card on no page is named by the write it blocks, never written on a guessed item id."""
    gh = PagedGh([[card(1)], [card(2)]])

    with pytest.raises(RuntimeError, match=r"#9 not on the board"):
        seam(tmp_path, gh).write(9, "Todo")


LONG = "Get \"https://api.github.com/user/122891372/projectsV2/5/items?per_page=100&fields%5B%5D=425772760%2C427848739%2C427848747&after=Y3Vyc29yOnYyOpK5MDAwMDAwMDAuMDAwODMxMjU1MTk1MzQ0Oc4SOAnV\": "


class FlakyGh(PagedGh):
    """`gh api` whose first read of page two dies on the network, after a URL long enough to pass 200 characters (#2319)."""

    def __init__(self, pages, error):
        super().__init__(pages)
        self.error, self.failed = error, False

    def __call__(self, cmd, input=None, **kw):
        if "--include" in cmd and self.after(" ".join(cmd)) == 1 and not self.failed:
            self.failed = True
            self.calls.append(" ".join(cmd))
            return subprocess.CompletedProcess(cmd, 1, "", LONG + self.error)
        return super().__call__(cmd, input=input, **kw)


@pytest.mark.parametrize("error", ["dial tcp 4.228.31.149:443: i/o timeout", "net/http: TLS handshake timeout",
                                   "read tcp 10.0.0.2:5000->4.228.31.149:443: read: connection reset by peer",
                                   "unexpected EOF", "dial tcp 4.228.31.149:443: connect: connection refused"])
def test_a_page_that_meets_a_transient_network_error_is_read_again(tmp_path, error):
    """#2319: one page that dies on the network once is read again; the read does not fail on it."""
    gh = FlakyGh([[card(1)], [card(2)]], error)

    assert set(seam(tmp_path, gh).items()) == {"1", "2"}
    assert gh.failed


@pytest.mark.parametrize("error", ["dial tcp: lookup api.github.com: no such host", "x509: certificate signed by unknown authority",
                                   "HTTP 404: Not Found"])
def test_a_page_that_meets_a_lasting_error_fails_at_once(tmp_path, error):
    """A name, certificate or client error does not pass by itself: the read fails on the first try."""
    gh = FlakyGh([[card(1)], [card(2)]], error)

    with pytest.raises(RuntimeError, match=re.escape(error[:20])):
        seam(tmp_path, gh).items()
    assert sum("after=1" in c for c in gh.calls) == 1


def test_a_write_that_may_have_landed_is_not_sent_again(tmp_path):
    """A write whose connection broke after the request went out may have landed, so it is not sent twice."""
    calls = []

    def gh(cmd, input=None, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", LONG + "read tcp 10.0.0.2:5000->4.228.31.149:443: read: connection reset by peer")

    board = seam(tmp_path, gh)
    with pytest.raises(RuntimeError, match="connection reset"):
        board._call(["-X", "PATCH", "/user/1/projectsV2/5/items/PVTI_1"])
    assert len(calls) == 1


def test_a_write_that_never_reached_the_server_is_sent_again(tmp_path):
    """A connect that timed out sent nothing, so the write is safe to send again."""
    calls = []

    def gh(cmd, input=None, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, "", LONG + "dial tcp 4.228.31.149:443: i/o timeout")
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    assert seam(tmp_path, gh)._call(["-X", "PATCH", "/user/1/projectsV2/5/items/PVTI_1"]) == "{}"
    assert len(calls) == 2


def test_no_retry_starts_past_the_callers_deadline(tmp_path, monkeypatch):
    """ADR-0051: the retries of one call stop once the caller's deadline has passed, whatever attempts remain."""
    clock = [0.0]
    monkeypatch.setattr(issue_state.time, "monotonic", lambda: clock[0])
    calls = []

    def gh(cmd, input=None, **kw):
        calls.append(cmd)
        clock[0] += 30.0   # each dial waits 30 s before it times out
        return subprocess.CompletedProcess(cmd, 1, "", LONG + "dial tcp 4.228.31.149:443: i/o timeout")

    with pytest.raises(RuntimeError, match="i/o timeout"):
        seam(tmp_path, gh)._call(["/user/1/projectsV2/5/items"], timeout=50)
    assert len(calls) == 2


def test_a_call_with_no_caller_deadline_still_has_one(tmp_path):
    """#2798, ADR-0051: a read whose caller names no deadline gets the binding's default, never an unbounded wait."""
    seen = []

    def gh(cmd, input=None, timeout=None, **kw):
        seen.append(timeout)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    seam(tmp_path, gh)._call(["/user/1/projectsV2/5/items"])
    assert seen and all(t and t > 0 for t in seen)


def test_a_call_past_its_deadline_raises_a_read_failure_and_is_not_retried(tmp_path):
    """#2798: a gh call that outlives its deadline is one failed read, raised as RuntimeError, so its pass releases its lock."""
    calls = []

    def gh(cmd, input=None, timeout=None, **kw):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(RuntimeError, match="deadline"):
        seam(tmp_path, gh)._call(["/user/1/projectsV2/5/items"], timeout=5)
    assert len(calls) == 1


def test_the_shorter_of_the_caller_and_binding_deadlines_wins(tmp_path):
    """#2798: a caller cut short passes its own deadline, and a longer binding default never overrides it."""
    seen = []

    def gh(cmd, input=None, timeout=None, **kw):
        seen.append(timeout)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    b = seam(tmp_path, gh)
    b.timeout = 300
    b._call(["/user/1/projectsV2/5/items"], timeout=7)
    assert seen == [7]
