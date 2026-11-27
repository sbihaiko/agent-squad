"""Hub GitHub reads under the quota floor and on a repeat pass (#855).

Under SG_GH_FLOOR a pass reads nothing and says so once. Labels and closed pull requests are read at most once an hour.
An open-issue list and the labels send the ETag they were answered with, and a 304 keeps the rows held.
"""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
ETAG = 'W/"v1"'
ISSUE = {"number": 5, "title": "an open issue", "state": "open", "labels": [{"name": "bug"}],
         "created_at": "2026-10-20T10:00:00Z", "sub_issues_summary": {"total": 0, "completed": 0}}
LABELS = [{"name": "bug", "color": "D73A4A"}]
MERGED = {"number": 1200, "merged_at": "2026-10-27T10:00:00Z", "created_at": "2026-10-27T09:00:00Z"}
BLOCKERS = json.dumps({"data": {"repository": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}})


class _Stop(BaseException):
    """Ends refresh_prs after one iteration: its sleep is the only way out of the loop."""


class _Seam:
    def rows(self, *a, **k):
        return json.dumps({"_page": 0}) + "\n"   # an empty project: its one page count, no card


def _payload(route):
    if "issues?state=open" in route:
        return [ISSUE]
    if "labels?" in route:
        return LABELS
    if "pulls?state=closed" in route:
        return [MERGED]
    return []


def _jq_lines(route):
    """What `gh api --jq` prints for the three list filters the pass uses: one JSON object a line."""
    if "issues?state=open" in route:
        return [{"n": 5, "t": "an open issue", "s": "open", "c": None, "st": None, "l": ["bug"],
                 "si": {"total": 0, "completed": 0}, "o": "2026-10-20T10:00:00Z"}]
    if "labels?" in route:
        return [{"n": "bug", "c": "D73A4A"}]
    if "pulls?state=closed" in route:
        return [{"n": 1200, "c": MERGED["created_at"], "m": MERGED["merged_at"]}]
    return [{"_page": 0}]   # the board's items read: the page count line board_items_jq always ends with


class Gh:
    """The gh CLI as the hub reaches it. Every call is recorded; a conditional read whose If-None-Match names ETAG gets a 304."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if "graphql" in cmd:
            return subprocess.CompletedProcess(cmd, 0, BLOCKERS, "")
        if "--jq" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "".join(json.dumps(x) + "\n" for x in _jq_lines(cmd[-1])), "")
        held = next((c.split(": ", 1)[1] for c in cmd if c.startswith("If-None-Match: ")), None)
        if held == ETAG:
            return subprocess.CompletedProcess(cmd, 0, f"HTTP/2.0 304 Not Modified\r\netag: {ETAG}\r\n\r\n", "")
        body = json.dumps(_payload(cmd[-1]))
        return subprocess.CompletedProcess(cmd, 0, f"HTTP/2.0 200 OK\r\netag: {ETAG}\r\n\r\n{body}", "")

    def reads(self, needle):
        return [c for c in self.calls if needle in " ".join(c)]


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    d = tmp_path_factory.mktemp("hub-quota")
    pins = {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": str(d),
            "SG_HUB_DIR": str(d / "hub"), "SG_STATE": str(d / "state")}
    saved = {k: os.environ.get(k) for k in pins}
    os.environ.update(pins)
    try:
        spec = importlib.util.spec_from_file_location("hub_quota_855", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


@pytest.fixture
def pass_env(hub, monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SG_STATE", str(state))
    gh = Gh()
    monkeypatch.setattr(hub.subprocess, "run", gh)
    monkeypatch.setattr(hub, "board_seam", lambda: _Seam())
    monkeypatch.setattr(hub, "prs_pass", lambda *a, **k: ([], [], []))
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": [], "prs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub, "prs_cache", {"t": 0, "rows": [], "err": None})
    monkeypatch.setattr(hub, "_etags", {}, raising=False)
    monkeypatch.setattr(hub, "_SKIPPING", set())
    hub.CAPS_LOGGED.clear()
    return gh, state


def _quota_below_floor(state):
    (state / "gh-ratelimit.json").write_text(json.dumps({"core": {"remaining": 120, "reset": time.time() + 1800}}))


def _one_refresh_prs_iteration(hub, monkeypatch):
    def stop(seconds):
        raise _Stop()
    monkeypatch.setattr(hub.time, "sleep", stop)
    with pytest.raises(_Stop):
        hub.refresh_prs()


def test_under_the_floor_a_board_pass_reads_nothing_and_says_so_once(hub, pass_env, capsys):
    gh, state = pass_env
    _quota_below_floor(state)
    hub.board_pass()
    hub.board_pass()
    assert gh.calls == []
    assert capsys.readouterr().err.count("skipped board pass") == 1


def test_under_the_floor_the_closed_pull_read_is_skipped(hub, pass_env, monkeypatch, capsys):
    gh, state = pass_env
    _quota_below_floor(state)
    _one_refresh_prs_iteration(hub, monkeypatch)
    assert gh.reads("pulls?state=closed") == []
    assert capsys.readouterr().err.count("skipped") == 1


def test_a_repeat_pass_inside_the_hour_reads_no_labels_and_no_closed_pulls(hub, pass_env, monkeypatch):
    gh, _ = pass_env
    hub.board_pass()
    _one_refresh_prs_iteration(hub, monkeypatch)
    assert len(gh.reads("labels?")) == 1
    assert len(gh.reads("pulls?state=closed")) == 1
    hub.board_pass()
    _one_refresh_prs_iteration(hub, monkeypatch)
    assert len(gh.reads("labels?")) == 1
    assert len(gh.reads("pulls?state=closed")) == 1


def test_an_unchanged_issue_list_sends_its_etag_and_a_304_keeps_the_rows(hub, pass_env):
    gh, _ = pass_env
    hub.board_pass()
    assert all("If-None-Match" not in " ".join(c) for c in gh.reads("issues?state=open"))
    hub.board_pass()
    second = gh.reads("issues?state=open")[-1]
    assert f"If-None-Match: {ETAG}" in second
    assert [i["n"] for i in hub.board_cache["items"]] == [5]
    assert hub.board_cache["err"] is None


def test_a_304_on_the_labels_keeps_the_colours_held(hub, pass_env):
    gh, _ = pass_env
    hub.board_pass()
    hub.board_cache["lt"] = 0
    hub.board_pass()
    assert f"If-None-Match: {ETAG}" in gh.reads("labels?")[-1]
    assert hub.board_cache["lc"] == {"bug": "d73a4a"}


def test_every_gh_read_the_pass_makes_carries_a_deadline(hub, pass_env, monkeypatch):
    gh, _ = pass_env
    deadlines = []

    def run(cmd, **kw):
        deadlines.append(kw.get("timeout"))
        return gh(cmd, **kw)

    monkeypatch.setattr(hub.subprocess, "run", run)
    hub.board_pass()
    _one_refresh_prs_iteration(hub, monkeypatch)
    assert deadlines and all(t is not None for t in deadlines), deadlines


def test_a_stalled_rest_page_keeps_the_rows_held_and_sets_the_error(hub, pass_env, monkeypatch):
    gh, _ = pass_env
    seam_row = {"n": 7, "t": "a card", "s": "open", "c": None, "st": "Todo", "k": None, "l": [], "si": {"total": None, "completed": None}, "o": None}

    class _Rows:
        def rows(self, *a, **k):
            return json.dumps(seam_row) + "\n" + json.dumps({"_page": 1}) + "\n"

    monkeypatch.setattr(hub, "board_seam", lambda: _Rows())
    hub.board_pass()
    held = hub.board_cache["items"]
    assert [i["n"] for i in held] == [7, 5]

    def stalled(cmd, **kw):
        if "issues?state=open" in " ".join(cmd):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return gh(cmd, **kw)

    monkeypatch.setattr(hub.subprocess, "run", stalled)
    hub.board_pass()
    assert hub.board_cache["items"] == held
    assert hub.board_cache["err"]


def test_a_failed_label_read_is_read_again_on_the_next_pass(hub, pass_env, monkeypatch):
    gh, _ = pass_env

    def broken(cmd, **kw):
        if "labels?" in " ".join(cmd):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return gh(cmd, **kw)

    monkeypatch.setattr(hub.subprocess, "run", broken)
    hub.board_pass()
    monkeypatch.setattr(hub.subprocess, "run", gh)
    hub.board_pass()
    assert len(gh.reads("labels?")) == 1
    assert hub.board_cache["lc"] == {"bug": "d73a4a"}


def test_a_failed_closed_pull_read_is_read_again_on_the_next_pass(hub, pass_env, monkeypatch):
    gh, _ = pass_env
    failed = []

    def broken(cmd, **kw):
        if "pulls?state=closed" in " ".join(cmd):
            failed.append(cmd)
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return gh(cmd, **kw)

    monkeypatch.setattr(hub.subprocess, "run", broken)
    _one_refresh_prs_iteration(hub, monkeypatch)
    monkeypatch.setattr(hub.subprocess, "run", gh)
    _one_refresh_prs_iteration(hub, monkeypatch)
    assert len(failed) == 1
    assert len(gh.reads("pulls?state=closed")) == 1
    assert hub.prs_cache["rows"][0]["n"] == 1200


def test_the_skip_line_is_one_per_floor_episode_not_per_remaining_count(hub, pass_env, capsys):
    gh, state = pass_env
    _quota_below_floor(state)
    hub.board_pass()
    (state / "gh-ratelimit.json").write_text(json.dumps({"core": {"remaining": 110, "reset": time.time() + 1800}}))
    hub.board_pass()
    assert gh.calls == []
    assert capsys.readouterr().err.count("skipped board pass") == 1


def test_a_new_floor_episode_after_recovery_says_so_again(hub, pass_env, capsys):
    gh, state = pass_env
    _quota_below_floor(state)
    hub.board_pass()
    (state / "gh-ratelimit.json").write_text(json.dumps({"core": {"remaining": 4000, "reset": time.time() + 1800}}))
    hub.board_pass()
    _quota_below_floor(state)
    hub.board_pass()
    assert capsys.readouterr().err.count("skipped board pass") == 2


def test_the_closed_pull_cache_is_written_through_a_temp_file(hub, pass_env, monkeypatch, tmp_path):
    prs_file = tmp_path / "hub-prs.json"
    monkeypatch.setattr(hub, "PRS", prs_file)
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(hub.os, "replace", lambda a, b: (replaced.append(str(b)), real_replace(a, b)))
    hub.refresh_closed_prs()
    assert replaced == [str(prs_file)]
    assert json.loads(prs_file.read_text())["rows"][0]["n"] == 1200


def test_a_closed_pull_read_with_no_rows_writes_no_cache_file(hub, pass_env, monkeypatch, tmp_path):
    prs_file = tmp_path / "hub-prs.json"
    monkeypatch.setattr(hub, "PRS", prs_file)
    monkeypatch.setattr(hub, "gh_list", lambda path, query="": [])
    hub.refresh_closed_prs()
    assert not prs_file.exists()
