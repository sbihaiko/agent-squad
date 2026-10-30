"""Each board pass appends one flow sample to hub-flow.jsonl beside the board cache, and keeps the opened time of each card."""
import importlib.util
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
FLOW_PY = SCRIPTS / "board_flow_history.py"
HOUR = 3600
DAY = 86400
OPENED = "2026-10-01T09:00:00Z"


def _load_hub(tag):
    spec = importlib.util.spec_from_file_location(f"hub_flow_{tag}", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_flow")
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir()
    mp.setenv("SG_REPO", "x/y")
    mp.setenv("SG_OWNER", "x")
    mp.setenv("SG_PROJECT", "4")
    mp.setenv("SG_CHECKOUT", str(base / "checkout"))
    mp.setenv("SG_HUB_DIR", str(base / "hub"))
    mp.setenv("SG_STATE", str(base / "state"))
    mp.delenv("SG_BOARD_CACHE", raising=False)
    try:
        yield _load_hub("main")
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(hub, "BOARD", cache / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    return cache / "hub-board.json"


def _card(n, st="Todo", s="OPEN", k=None, labels=(), o=OPENED):
    return {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": s, "c": None, "st": st, "k": k, "l": list(labels),
            "si": {"total": None, "completed": None}, "o": o}


def _rest_issue(c):
    """An open issue as the REST list answers it: GitHub's own fields."""
    return {"number": c["n"], "title": c["t"], "state": c["s"].lower(), "labels": [{"name": x} for x in c["l"]],
            "created_at": c.get("o"), "sub_issues_summary": c["si"]}


def _gh(calls, cards=(), open_issues=(), fail=False):
    """The gh CLI as one pass uses it: each call is recorded, and the answer is canned per endpoint."""
    def run(cmd, **kw):
        calls.append(cmd)
        if fail:
            raise FileNotFoundError("gh is not on PATH")
        s = " ".join(cmd)
        if "/fields?" in s:
            out = "PVTSSF_status\n"
        elif "projectsV2" in s and "/items" in s:
            out = "".join(json.dumps(c) + "\n" for c in cards)
        elif "search/issues" in s:
            out = "0\n"
        elif "issues?state=open" in s:
            out = json.dumps([_rest_issue(c) for c in open_issues])
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def _pass(hub, monkeypatch, cards=(), open_issues=(), fail=False):
    calls = []
    monkeypatch.setattr(hub.subprocess, "run", _gh(calls, cards, open_issues, fail))
    hub.board_pass(repos=[])
    return calls


def _flow(board):
    return board.with_name("hub-flow.jsonl")


def _samples(board):
    path = _flow(board)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _sample(t, counts):
    return {"t": t, "counts": counts, "frozen": 0, "none": 0}


def test_a_pass_appends_one_sample_that_counts_every_card(hub, board, monkeypatch):
    cards = [_card(1, st="Todo"), _card(2, st="In Progress", k="bug"), _card(3, st=None), _card(4, st="Retired Status")]
    _pass(hub, monkeypatch, cards)
    samples = _samples(board)
    assert len(samples) == 1
    s = samples[0]
    assert s["counts"] == {"issue": {"Todo": 1, "Backlog": 1}, "bug": {"Doing": 1}}
    assert s["frozen"] == 0 and s["none"] == 1
    assert sum(n for by_column in s["counts"].values() for n in by_column.values()) + s["none"] == len(cards)


def test_a_pass_with_the_same_counts_appends_no_row(hub, board, monkeypatch):
    _pass(hub, monkeypatch, [_card(1, st="Todo")])
    _pass(hub, monkeypatch, [_card(1, st="Todo")])
    assert len(_samples(board)) == 1
    _pass(hub, monkeypatch, [_card(1, st="Doing")])
    assert len(_samples(board)) == 2, "a pass whose counts moved appends its sample"


def test_a_failed_items_read_appends_nothing_and_leaves_the_file_unchanged(hub, board, monkeypatch):
    _flow(board).write_text(json.dumps(_sample(1.0, {"issue": {"Todo": 9}})) + "\n")
    before = _flow(board).read_bytes()
    _pass(hub, monkeypatch, fail=True)
    assert hub.board_cache["err"], "the failed read is surfaced on the board"
    assert _flow(board).read_bytes() == before


def test_counts_key_by_kind_and_column_and_a_frozen_card_stays_in_its_column(hub, board, monkeypatch):
    cards = [_card(1, st="In Progress", k="bug"),
             _card(2, st="Doing", k=None, labels=["frozen:owner"]),
             _card(3, st="Todo", k="task", labels=["frozen:dependency"])]
    _pass(hub, monkeypatch, cards)
    s = _samples(board)[-1]
    assert s["counts"] == {"bug": {"Doing": 1}, "issue": {"Doing": 1}, "task": {"Todo": 1}}
    assert s["frozen"] == 2
    assert s["none"] == 0


def test_each_cached_card_row_carries_its_opened_time(hub, board, monkeypatch):
    open_only = {"n": 12, "t": "open only", "s": "OPEN", "c": None, "st": None, "l": [],
                 "si": {"total": None, "completed": None}, "o": "2026-10-02T09:00:00Z"}
    calls = _pass(hub, monkeypatch, [_card(11, st="Todo")], open_issues=[open_only])
    rows = {i["n"]: i for i in hub.board_cache["items"]}
    assert rows[11]["o"] == OPENED
    assert rows[12]["o"] == "2026-10-02T09:00:00Z"
    items_call = next(" ".join(c) for c in calls if "projectsV2" in " ".join(c) and "/items" in " ".join(c))
    assert "o:.content.created_at" in items_call, "the project items route reads the opened time"


def test_samples_older_than_thirty_days_keep_one_per_utc_day_and_newer_ones_stay_raw(hub, board, monkeypatch):
    now = time.time()
    day0 = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=40)
    d0 = day0.timestamp()
    old = [d0 + 1 * HOUR, d0 + 5 * HOUR, d0 + 20 * HOUR, d0 + DAY + 2 * HOUR, d0 + DAY + 3 * HOUR]
    recent = [now - 2 * DAY, now - HOUR]
    seeded = [_sample(t, {"issue": {"Todo": 9}}) for t in old + recent]
    _flow(board).write_text("".join(json.dumps(s) + "\n" for s in seeded))
    _pass(hub, monkeypatch, [_card(1, st="Todo")])
    samples = _samples(board)
    assert [s["t"] for s in samples[:4]] == [d0 + 20 * HOUR, d0 + DAY + 3 * HOUR, now - 2 * DAY, now - HOUR]
    assert len(samples) == 5, "two old UTC days roll up to one sample each; the two recent samples and the new one stay"
    assert samples[-1]["counts"] == {"issue": {"Todo": 1}} and samples[-1]["t"] >= int(now)


def test_the_history_file_sits_beside_the_board_cache_of_each_profile(tmp_path, monkeypatch):
    base = tmp_path / "profiles"
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(base / "checkout"))
    monkeypatch.setenv("SG_STATE", str(base / "state"))
    monkeypatch.setenv("SG_HUB_DIR", str(base / "hub"))
    for name in ("checkout", "state", "hub"):
        (base / name).mkdir(parents=True, exist_ok=True)
    cards = {"a": [_card(1, st="Todo")], "b": [_card(2, st="Doing")]}
    histories = {}
    for name in ("a", "b"):
        cache = base / name / "cache" / "hub-board.json"
        cache.parent.mkdir(parents=True)
        monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
        mod = _load_hub(name)
        monkeypatch.setattr(mod, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
        _pass(mod, monkeypatch, cards[name])
        histories[name] = _flow(cache)
    assert histories["a"] != histories["b"]
    assert [s["counts"] for s in _samples(base / "a" / "cache" / "hub-board.json")] == [{"issue": {"Todo": 1}}]
    assert [s["counts"] for s in _samples(base / "b" / "cache" / "hub-board.json")] == [{"issue": {"Doing": 1}}]


def test_the_flow_module_imports_no_hub_and_no_github_code():
    import ast
    tree = ast.parse(FLOW_PY.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"hub", "issue_state", "subprocess", "urllib", "http"}, imported
