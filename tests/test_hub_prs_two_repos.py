"""Hub board: the OPEN PRs and Closed PRs columns carry every repository the squad works in (#1820).

The squad works in two repositories -- the emulator (`SG_REPO`, sbihaiko/MesenAI) and its own tooling
(sbihaiko/agent-squad) -- and the column read only the first, so a tooling PR was invisible to the hub and to the loop.

Every assertion names its seam: the repositories the hub is told to read, `prs_pass` (the one read of both PR columns,
with its fetchers injected so no test touches the network), the payload `board()` serves, the issue panel's read, and
the declarations the served page paints.
"""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
EMU = "sbihaiko/MesenAI"
TOOL = "sbihaiko/agent-squad"


@pytest.fixture(scope="module")
def hub():
    # the hub reads its profile from the environment at import, so the values this module pins are restored at the end:
    # a module that leaves SG_TOOLING_REPO behind makes every later hub test read a second repository (#1820)
    pinned = {"SG_REPO": EMU, "SG_OWNER": "sbihaiko", "SG_PROJECT": "4", "SG_TOOLING_REPO": TOOL,
              "SG_CHECKOUT": tempfile.mkdtemp(), "SG_HUB_DIR": tempfile.mkdtemp()}
    before = {k: os.environ.get(k) for k in pinned}
    os.environ.update(pinned)
    spec = importlib.util.spec_from_file_location("hub_prs_two_repos", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # the board reads the profile's repository alone (#20); the column machinery below is still proven over two
    # repositories, handed to it directly, so the list read at import is kept aside for the one test that checks it
    mod.read_at_import, mod.REPOS = list(mod.REPOS), [EMU, TOOL]
    yield mod
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# --- the fixture/stub seam: the GraphQL payload the hub already parses, one node per PR ---------------------------

def _node(n, title="a PR", updated="2026-10-11T09:00:00Z"):
    return {"number": n, "title": title, "isDraft": False, "updatedAt": updated, "createdAt": "2026-10-11T08:00:00Z",
            "headRefOid": "a" * 40, "baseRefName": "main", "mergeStateStatus": "CLEAN",
            "labels": {"nodes": [{"name": "bug"}]}, "reviewThreads": {"totalCount": 0, "nodes": []},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {
                "state": "SUCCESS",
                "contexts": {"nodes": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}}}}]}}


def _payload(numbers, title="a PR"):
    return {"data": {"repository": {"pullRequests": {"nodes": [_node(n, title) for n in numbers]}}}}


@pytest.fixture
def reads(hub):
    """The three fetchers `prs_pass` reads a repository through, stubbed: open PRs, closed PRs, closed total."""
    class Reads:
        def __init__(self):
            self.open, self.closed, self.total, self.seen = {}, {}, {}, []
            self.boom = set()

        def fetch_open(self, repo):
            self.seen.append(repo)
            if repo in self.boom:
                raise RuntimeError(f"{repo} is unreachable")
            return hub.prs_from_graphql(_payload(self.open.get(repo, [])), repo)

        def fetch_closed(self, repo):
            if repo in self.boom:
                raise RuntimeError(f"{repo} is unreachable")
            return [{"n": n, "t": "a closed PR", "c": "2026-10-11T07:00:00Z", "m": True, "r": repo}
                    for n in self.closed.get(repo, [])]

        def fetch_total(self, repo):
            if repo in self.boom:
                raise RuntimeError(f"{repo} is unreachable")
            return len(self.closed.get(repo, []))
    return Reads()


# --- the repositories the hub reads, named in one place -----------------------------------------------------------

def test_the_board_reads_the_repo_the_profile_names_and_never_the_retired_tooling_repo(hub):
    assert hub.read_at_import == [EMU], "the board reads the profile's repository alone; SG_TOOLING_REPO is retired (#20)"


def test_a_tag_that_names_one_repository_is_the_short_name(hub):
    assert hub.repo_tag(EMU) == "MesenAI" and hub.repo_tag(TOOL) == "agent-squad"


def test_a_tag_that_does_not_fit_is_the_full_owner_and_name(hub):
    """Two repositories whose names collide have no short tag: the owner is what tells them apart."""
    both = ["sbihaiko/tooling", "other/tooling"]
    assert hub.repo_tag("sbihaiko/tooling", both) == "sbihaiko/tooling"
    assert hub.repo_tag("other/tooling", both) == "other/tooling"


# --- the read itself ---------------------------------------------------------------------------------------------

def test_every_open_pr_of_both_repositories_reaches_the_column_with_its_own_tag(hub, reads):
    reads.open = {EMU: [1512], TOOL: [1821]}
    prs, prev, prev_n, errs = hub.prs_pass(reads.fetch_open, reads.fetch_closed, reads.fetch_total, repos=[EMU, TOOL])
    assert sorted(reads.seen) == sorted([EMU, TOOL]), "both repositories are read"
    assert {p["r"] for p in prs} == {EMU, TOOL}, "each row names the repository it came from"
    assert {p["n"] for p in prs} == {1512, 1821} and errs == []


def test_a_repository_the_hub_cannot_read_is_an_error_on_the_column(hub, reads):
    """An unreachable repository is named as an error, never silently dropped from the read."""
    reads.boom = {TOOL}; reads.open = {EMU: [1512]}
    prs, prev, prev_n, errs = hub.prs_pass(reads.fetch_open, reads.fetch_closed, reads.fetch_total, repos=[EMU, TOOL])
    assert any(TOOL in e for e in errs), f"the unreadable repository is reported: {errs}"
    assert {p["r"] for p in prs} == {EMU}, "the repository that answered still fills the column"


def test_a_repository_the_hub_cannot_read_keeps_the_last_good_rows_of_that_repository(hub, reads, monkeypatch):
    reads.open = {EMU: [1512], TOOL: [1821]}
    prs, _, _, _ = hub.prs_pass(reads.fetch_open, reads.fetch_closed, reads.fetch_total, repos=[EMU, TOOL])
    monkeypatch.setattr(hub, "board_cache", {"prs": prs, "prev": [], "prev_n": 0, "prev_n_of": {EMU: 0, TOOL: 0}})
    reads.boom = {TOOL}
    prs, prev, prev_n, errs = hub.prs_pass(reads.fetch_open, reads.fetch_closed, reads.fetch_total, repos=[EMU, TOOL])
    assert {p["r"] for p in prs} == {EMU, TOOL}, "the failed repository keeps the rows the last good pass left"
    assert any(TOOL in e for e in errs)


def test_the_closed_prs_column_carries_both_repositories_and_their_total(hub, reads):
    reads.open = {EMU: [], TOOL: []}
    reads.closed = {EMU: [1, 2], TOOL: [3]}
    prs, prev, prev_n, errs = hub.prs_pass(reads.fetch_open, reads.fetch_closed, reads.fetch_total, repos=[EMU, TOOL])
    assert {p["r"] for p in prev} == {EMU, TOOL}, "a closed PR of either repository lands in Closed PRs"
    assert prev_n == 3, "the total counts both repositories"
    assert errs == []


# --- what the column serves --------------------------------------------------------------------------------------

def _served(hub, monkeypatch, rows, errs=()):
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": [], "prs": rows, "prev": [], "prev_n": 0, "errs": list(errs), "err": None, "lc": {}})
    return hub.board()["prs"]


def test_the_open_prs_column_tags_every_card_with_its_repository(hub, monkeypatch):
    rows = [{"n": 1512, "t": "emu", "u": "2026-10-11T09:00:00Z", "o": "2026-10-11T08:00:00Z", "r": EMU, "l": []},
            {"n": 1821, "t": "tooling", "u": "2026-10-11T09:30:00Z", "o": "2026-10-11T08:00:00Z", "r": TOOL, "l": []}]
    column = _served(hub, monkeypatch, rows)
    assert column["n"] == 2, "the count spans both repositories"
    tags = {i["n"]: i["rt"] for i in column["items"]}
    assert tags == {1821: "agent-squad", 1512: "MesenAI"}, f"each card wears the tag of its own repository: {tags}"


def test_an_unreadable_repository_is_served_on_the_column_as_an_error(hub, monkeypatch):
    rows = [{"n": 1512, "t": "emu", "u": "2026-10-11T09:00:00Z", "o": "2026-10-11T08:00:00Z", "r": EMU, "l": []}]
    column = _served(hub, monkeypatch, rows, errs=[f"{TOOL}: the open-PR query returned nothing"])
    assert TOOL in (column.get("err") or ""), "the column carries the repository it could not read"
    assert column["n"] == 1


# --- the page ----------------------------------------------------------------------------------------------------

def _page_line(hub, prefix):
    m = re.search(rf"^{re.escape(prefix)}.*$", hub.PAGE, re.M)
    assert m, f"the board's script has no line starting with {prefix!r}"
    return m.group(0)


def _page_block(hub, start, end):
    """The board's script from `start` up to (not including) `end`: a handler or a function spans more than one line."""
    i = hub.PAGE.index(start)
    return hub.PAGE[i:hub.PAGE.index(end, i)]


def test_a_card_links_and_opens_in_its_own_repository(hub):
    card = _page_line(hub, "const cards=")
    assert "data-r=" in card, "the card carries the repository it belongs to, for the link and the popup"
    assert 'github.com/${esc(x.r||"__REPO__")}/' in card, "the id links to the card's own repository"
    click = _page_block(hub, 'document.addEventListener("click",e=>{const c=e.target.closest("#bbody .it")', 'const hm=location.hash')
    assert 'github.com/"+(c.dataset.r||"__REPO__")+"/' in click, "clicking a card opens its own repository"
    assert "showIssue(c.dataset.n,c.dataset.r)" in click, "the popup is asked for the card's own repository"
    assert "x.dataset.r||\"__REPO__\"" in click, "the popup's arrows walk the column's cards with their repositories"


def test_a_refs_number_in_a_tooling_pr_links_that_repositorys_issue(hub):
    """The markdown of the panel is painted for the repository the card names, not for the profile's one."""
    m = re.search(r"const inl=x=>.*$", hub.PAGE, re.M)
    assert m, "the panel's markdown renderer is gone"
    assert "github.com/__REPO__/issues/$2" not in m.group(0), "the panel still hardcodes the profile's repository (#1820)"
    assert "${irepo}/issues/$2" in m.group(0), "a `Refs #N` links in the repository the card belongs to"


def test_the_issue_panel_is_asked_for_the_repository_the_card_names(hub):
    body = _page_block(hub, "async function showIssue(", "document.addEventListener(\"click\"")
    assert '"/api/issue?n="+n+(r?"&r="+encodeURIComponent(r):"")' in body, "the panel asks for the card's repository"
    assert 'irepo=r||"__REPO__"' in body, "and paints that repository's markdown from then on"


def test_the_panel_read_resolves_the_number_in_the_repository_it_was_given(hub, monkeypatch):
    asked = []

    def gh_json(path, timeout=30):
        asked.append(path)
        if path.endswith("/comments?per_page=100"):
            return []
        return {"number": 1809, "title": "t", "state": "open", "html_url": "u", "user": {"login": "x"},
                "created_at": "2026-10-11T08:00:00Z", "updated_at": "2026-10-11T09:00:00Z", "labels": [], "body": ""}

    monkeypatch.setattr(hub, "gh_json", gh_json)
    monkeypatch.setattr(hub, "_issue_cache", {})
    d = hub.issue_detail(1809, TOOL)
    assert asked and asked[0].startswith(f"repos/{TOOL}/issues/1809"), f"read in the card's repository: {asked}"
    assert d["r"] == TOOL
    asked.clear()
    hub.issue_detail(1809)     # a card that names no repository is the profile's, never a guess at another one
    assert asked[0].startswith(f"repos/{EMU}/issues/1809"), f"the profile repository is the default: {asked}"


def test_a_repository_the_hub_was_not_told_to_read_is_never_read(hub, monkeypatch):
    asked = []
    monkeypatch.setattr(hub, "gh_json", lambda path, timeout=30: asked.append(path) or {"err": "no"})
    monkeypatch.setattr(hub, "_issue_cache", {})
    hub.issue_detail(1809, "someone/unlisted")
    assert asked and asked[0].startswith(f"repos/{EMU}/issues/1809"), f"an unlisted repository is never read: {asked}"
