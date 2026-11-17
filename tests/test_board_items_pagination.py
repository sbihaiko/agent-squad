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
