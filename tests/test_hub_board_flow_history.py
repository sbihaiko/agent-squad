"""Each board pass appends one flow sample to hub-flow.jsonl beside the board cache, and keeps the opened time of each card."""
import importlib.util
import json
import os
import pathlib
import shutil
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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def _pages(total):
    """The page counts the items read answers: one per page of 100, as board_items_jq emits them."""
    return [min(100, total - i) for i in range(0, total, 100)] or [0]


def _gh(calls, cards=(), open_issues=(), fail=False, unparseable=False):
    """The gh CLI as one pass uses it: each call is recorded, and the answer is canned per endpoint. The items read ends
    with one page count per page (board_items_jq); `unparseable` answers the items read with no JSON at all."""
    def run(cmd, **kw):
        calls.append(cmd)
        if fail:
            raise FileNotFoundError("gh is not on PATH")
        s = " ".join(cmd)
        if "/fields?" in s:
            out = "PVTSSF_status\n"
        elif "projectsV2" in s and "/items" in s:
            if unparseable:
                return subprocess.CompletedProcess(cmd, 0, "<html>bad gateway</html>\n", "")
            out = "".join(json.dumps(c) + "\n" for c in cards) + "".join(json.dumps({"_page": n}) + "\n" for n in _pages(len(cards)))
        elif "search/issues" in s:
            out = "0\n"
        elif "issues?state=open" in s:
            out = json.dumps([_rest_issue(c) for c in open_issues])
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}})
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def _pass(hub, monkeypatch, cards=(), open_issues=(), fail=False, unparseable=False):
    calls = []
    monkeypatch.setattr(hub.subprocess, "run", _gh(calls, cards, open_issues, fail, unparseable))
    hub.board_pass(repos=[])
    return calls


def _flow(board, repo="x_y"):
    return board.with_name(f"hub-flow.{repo}.jsonl")


def _samples(board, repo="x_y"):
    path = _flow(board, repo)
    if not path.exists():
        return []
    return [obj for obj in (json.loads(line) for line in path.read_text().splitlines() if line.strip()) if "counts" in obj]


def _sample(t, counts):
    return {"t": t, "counts": counts, "frozen": 0, "none": 0}


def test_a_pass_appends_one_sample_that_counts_every_card(hub, board, monkeypatch):
    cards = [_card(1, st="Todo"), _card(2, st="In Progress", k="bug", labels=["bug"]), _card(3, st=None), _card(4, st="Retired Status")]
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


def test_a_failed_items_read_is_a_flow_skip_and_no_sample(hub, board, monkeypatch):
    _flow(board).write_text(json.dumps(_sample(1.0, {"issue": {"Todo": 9}})) + "\n")
    _pass(hub, monkeypatch, fail=True)
    assert hub.board_cache["err"], "the failed read is surfaced on the board"
    assert [s["counts"] for s in _samples(board)] == [{"issue": {"Todo": 9}}], "a failed read is no sample"
    records = _skip_records(board)
    assert len(records) == 1 and "items" in records[0]["reason"]


def test_an_unparseable_items_read_is_a_flow_skip_and_no_sample(hub, board, monkeypatch):
    _pass(hub, monkeypatch, _rows(3))
    _pass(hub, monkeypatch, _rows(5), unparseable=True)
    assert [s["counts"] for s in _samples(board)] == [{"issue": {"Todo": 3}}], "an unparseable read is no sample"
    records = _skip_records(board)
    assert [r["flow_skip"] for r in records] == [3], "the skip counts the last whole read's cards"
    assert "items" in records[0]["reason"]


def test_counts_key_by_the_bars_kind_and_column_and_a_frozen_card_stays_in_its_column(hub, board, monkeypatch):
    """The kind is the board bar's (#904): `bug` by the bug or loop-bug label, never by the project's Kind field."""
    cards = [_card(1, st="In Progress", k="task", labels=["bug"]),
             _card(2, st="Doing", k="bug", labels=["frozen:owner"]),
             _card(3, st="Todo", k="task", labels=["frozen:dependency"])]
    _pass(hub, monkeypatch, cards)
    s = _samples(board)[-1]
    assert s["counts"] == {"bug": {"Doing": 1}, "issue": {"Doing": 1, "Todo": 1}}
    assert s["frozen"] == 2
    assert s["none"] == 0


def test_a_card_closed_as_not_planned_counts_in_abandoned_not_closed(hub, board, monkeypatch):
    """#921: the flow sample counts a card closed as NOT_PLANNED, or as a duplicate, with Status Closed in Abandoned, so
    the CFD's Closed band and its throughput leave it out of delivered work."""
    cards = [_card(1, st="Closed", s="CLOSED"),
             {**_card(2, st="Closed", s="CLOSED"), "cr": "not_planned"},
             {**_card(3, st="Closed", s="CLOSED"), "cr": "completed"},
             {**_card(4, st="Closed", s="CLOSED"), "cr": "duplicate"}]
    _pass(hub, monkeypatch, cards)
    assert _samples(board)[-1]["counts"] == {"issue": {"Abandoned": 2, "Closed": 2}}


def test_a_history_written_before_the_close_reason_is_re_bucketed_once(hub, board, monkeypatch):
    """#921 (DEBT 1): a sample written before the close reason counted put a not-planned card in Closed. The first
    append after the upgrade moves it across in the past samples, once, and records the boundary."""
    _flow(board).write_text(json.dumps(_sample(1.0, {"issue": {"Closed": 3}})) + "\n")
    first = [{**_card(1, st="Closed", s="CLOSED"), "cr": "completed"},
             {**_card(2, st="Closed", s="CLOSED"), "cr": "completed"},
             {**_card(3, st="Closed", s="CLOSED"), "cr": "not_planned"}]

    _pass(hub, monkeypatch, first)

    samples = _samples(board)
    assert samples[0]["counts"] == {"issue": {"Closed": 2, "Abandoned": 1}}, "the past sample kept the card in Closed"
    assert samples[1]["counts"] == {"issue": {"Closed": 2, "Abandoned": 1}}
    markers = [json.loads(line) for line in _flow(board).read_text().splitlines() if "flow_rebucket" in line]
    assert [m["flow_rebucket"] for m in markers] == [1], "the boundary is recorded once"

    second = [*first, {**_card(4, st="Closed", s="CLOSED"), "cr": "not_planned"}]
    _pass(hub, monkeypatch, second)

    samples = _samples(board)
    assert samples[0]["counts"] == {"issue": {"Closed": 2, "Abandoned": 1}}, "the past is corrected once, never again"
    assert samples[1]["counts"] == {"issue": {"Closed": 2, "Abandoned": 1}}
    assert samples[2]["counts"] == {"issue": {"Closed": 2, "Abandoned": 2}}
    markers = [json.loads(line) for line in _flow(board).read_text().splitlines() if "flow_rebucket" in line]
    assert [m["flow_rebucket"] for m in markers] == [1], "a second pass adds no boundary"


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
    seeded = [_sample(t, {"issue": {"Todo": 1}}) for t in old + recent]
    _flow(board).write_text("".join(json.dumps(s) + "\n" for s in seeded))
    _pass(hub, monkeypatch, [_card(1, st="Doing")])
    samples = _samples(board)
    assert [s["t"] for s in samples[:4]] == [d0 + 20 * HOUR, d0 + DAY + 3 * HOUR, now - 2 * DAY, now - HOUR]
    assert len(samples) == 5, "two old UTC days roll up to one sample each; the two recent samples and the new one stay"
    assert samples[-1]["counts"] == {"issue": {"Doing": 1}} and samples[-1]["t"] >= int(now)


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


def _load_flow():
    spec = importlib.util.spec_from_file_location("board_flow_history_under_test", FLOW_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(total, column="Todo"):
    return [_card(n, st=column) for n in range(1, total + 1)]


def test_two_repositories_on_one_board_write_two_histories(tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "hub-board.json"
    cache.parent.mkdir()
    base = tmp_path / "profiles"
    for name in ("checkout", "state", "hub"):
        (base / name).mkdir(parents=True)
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "5")
    monkeypatch.setenv("SG_CHECKOUT", str(base / "checkout"))
    monkeypatch.setenv("SG_STATE", str(base / "state"))
    monkeypatch.setenv("SG_HUB_DIR", str(base / "hub"))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    reads = {"x/alpha": _rows(3), "x/beta": _rows(1, column="Doing")}
    for repo, rows in reads.items():
        monkeypatch.setenv("SG_REPO", repo)
        mod = _load_hub(repo.split("/")[1])
        monkeypatch.setattr(mod, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
        _pass(mod, monkeypatch, rows)
    assert not cache.with_name("hub-flow.jsonl").exists(), "no shared history is written"
    assert [s["counts"] for s in _samples(cache, "x_alpha")] == [{"issue": {"Todo": 3}}]
    assert [s["counts"] for s in _samples(cache, "x_beta")] == [{"issue": {"Doing": 1}}]


def _relations_incomplete(hub, monkeypatch):
    """The relations read ran past its page cap, or asked for a page with no cursor: the pass keeps its rows, and the read is incomplete."""
    monkeypatch.setattr(hub, "open_relations", lambda: ([], False))


def _skip_records(board):
    return [json.loads(line) for line in _flow(board).read_text().splitlines() if '"flow_skip"' in line]


def test_an_incomplete_read_is_never_appended_even_when_it_repeats_the_same_total(hub, board, monkeypatch):
    _pass(hub, monkeypatch, _rows(9))
    _relations_incomplete(hub, monkeypatch)
    _pass(hub, monkeypatch, _rows(9, column="Doing"))
    _pass(hub, monkeypatch, _rows(9, column="Doing"))
    assert [s["counts"] for s in _samples(board)] == [{"issue": {"Todo": 9}}], "an incomplete read is no sample, at any total"
    records = _skip_records(board)
    assert [r["flow_skip"] for r in records] == [9], "the day's skip line is one line: the repeat updates it"
    assert all(set(r) == {"flow_skip", "t", "reason"} and r["reason"] for r in records)


def test_a_hundred_failed_reads_on_one_day_leave_one_skip_line(tmp_path):
    flow = _load_flow()
    day = datetime(2026, 10, 20, 0, 0, tzinfo=UTC).timestamp()
    path = tmp_path / "hub-flow.x_y.jsonl"
    for k in range(100):
        flow.append(path, _rows(k % 5 + 1), now=day + 60 * k, incomplete="items read incomplete")
    records = flow.skips(path)
    assert len(records) == 1, "one flow_skip line per UTC day and reason"
    assert records[0] == {"flow_skip": 5, "t": int(day + 60 * 99), "reason": "items read incomplete"}, "latest t and count"
    assert flow.samples(path) == []


def test_a_skip_of_another_reason_or_another_day_is_its_own_line(tmp_path):
    flow = _load_flow()
    day = datetime(2026, 10, 20, 12, tzinfo=UTC).timestamp()
    path = tmp_path / "hub-flow.x_y.jsonl"
    flow.append(path, _rows(2), now=day, incomplete="items read incomplete")
    flow.append(path, _rows(3), now=day + HOUR, incomplete="relations read incomplete")
    flow.append(path, _rows(4), now=day + DAY, incomplete="items read incomplete")
    assert [(r["reason"], r["flow_skip"]) for r in flow.skips(path)] == [
        ("items read incomplete", 2), ("relations read incomplete", 3), ("items read incomplete", 4)]


def test_a_complete_read_under_the_last_sample_is_a_sample(hub, board, monkeypatch):
    _pass(hub, monkeypatch, _rows(9))
    _pass(hub, monkeypatch, _rows(1))
    assert [s["counts"] for s in _samples(board)] == [{"issue": {"Todo": 9}}, {"issue": {"Todo": 1}}], "a size alone never rejects a complete read"
    assert _skip_records(board) == []


def test_the_shared_legacy_history_is_renamed_never_deleted_at_first_use(tmp_path):
    flow = _load_flow()
    board = tmp_path / "hub-board.json"
    shared = tmp_path / "hub-flow.jsonl"
    old = json.dumps(_sample(1.0, {"issue": {"Todo": 4}})) + "\n"
    shared.write_text(old)
    path = flow.history_path(board, "x/y")
    flow.append(path, _rows(2), now=time.time())
    assert not shared.exists(), "the shared file is retired at first use"
    assert (tmp_path / "hub-flow.jsonl.legacy").read_text() == old, "its bytes survive under the legacy name"
    assert [s["counts"] for s in flow.samples(path)] == [{"issue": {"Todo": 2}}]
    flow.append(path, _rows(3), now=time.time() + 60)
    assert (tmp_path / "hub-flow.jsonl.legacy").read_text() == old, "a second use changes nothing and raises nothing"


def test_a_legacy_name_already_taken_takes_the_later_bytes_and_no_numbered_file(tmp_path):
    flow = _load_flow()
    board = tmp_path / "hub-board.json"
    (tmp_path / "hub-flow.jsonl.legacy").write_text("earlier\n")
    (tmp_path / "hub-flow.jsonl").write_text("later\n")
    flow.append(flow.history_path(board, "x/y"), _rows(2), now=time.time())
    assert (tmp_path / "hub-flow.jsonl.legacy").read_text() == "earlier\nlater\n", "the later bytes follow the earlier ones"
    assert [p.name for p in tmp_path.glob("hub-flow.jsonl*")] == ["hub-flow.jsonl.legacy"], "no numbered temp file is left"


def test_a_legacy_file_another_process_renamed_first_is_no_error(tmp_path, monkeypatch):
    flow = _load_flow()
    board = tmp_path / "hub-board.json"
    (tmp_path / "hub-flow.jsonl").write_text("shared\n")
    real_replace = os.replace

    def renamed_by_someone_else(src, dst):
        if pathlib.Path(src).name == "hub-flow.jsonl":
            raise FileNotFoundError(src)
        return real_replace(src, dst)

    monkeypatch.setattr(flow.os, "replace", renamed_by_someone_else)
    path = flow.history_path(board, "x/y")
    flow.append(path, _rows(2), now=time.time())
    assert [s["counts"] for s in flow.samples(path)] == [{"issue": {"Todo": 2}}]


def test_flow_skip_lines_older_than_thirty_days_keep_one_per_utc_day(tmp_path):
    flow = _load_flow()
    now = time.time()
    day0 = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=40)
    d0 = day0.timestamp()
    skips = [d0 + 1 * HOUR, d0 + 5 * HOUR, d0 + DAY + 2 * HOUR, d0 + DAY + 3 * HOUR]
    recent = now - HOUR
    lines = [{"flow_skip": 1, "t": int(t), "reason": "incomplete"} for t in skips]
    lines.append({"flow_skip": 2, "t": int(recent), "reason": "incomplete"})
    path = tmp_path / "hub-flow.x_y.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    flow.append(path, _rows(3), now=now)
    kept = [json.loads(line)["t"] for line in path.read_text().splitlines() if '"flow_skip"' in line]
    assert kept == [int(d0 + 5 * HOUR), int(d0 + DAY + 3 * HOUR), int(recent)], "the last skip of each old UTC day stays; recent ones stay raw"


def test_a_legacy_file_recreated_by_an_old_hub_is_not_renamed_again_in_this_process(tmp_path):
    flow = _load_flow()
    board = tmp_path / "hub-board.json"
    shared = tmp_path / "hub-flow.jsonl"
    path = flow.history_path(board, "x/y")
    shared.write_text("first\n")
    flow.append(path, _rows(2), now=time.time())
    shared.write_text("second\n")   # an old hub still running the old code writes the shared file again
    flow.append(path, _rows(3), now=time.time() + 60)
    legacy = sorted(p.name for p in tmp_path.glob("hub-flow.jsonl.legacy*"))
    assert legacy == ["hub-flow.jsonl.legacy"], "one rename per process, never one file per pass"
    assert shared.read_text() == "second\n", "the recreated file is left alone, never deleted"


def test_a_whole_read_of_exactly_200_items_is_a_sample(hub, board, monkeypatch):
    _pass(hub, monkeypatch, _rows(200))
    assert [s["counts"] for s in _samples(board)] == [{"issue": {"Todo": 200}}], "a full last page is no proof of a missing page"
    assert _skip_records(board) == []


def test_an_items_read_that_ends_on_a_short_page_is_whole(hub, board, monkeypatch):
    _pass(hub, monkeypatch, _rows(137))
    assert [s["counts"] for s in _samples(board)] == [{"issue": {"Todo": 137}}]


def test_the_items_jq_answers_one_page_count_per_page(hub, tmp_path):
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not installed")
    page = [{"content_type": "Issue", "id": f"PVT_{n}", "fields": [],
             "content": {"number": n, "repository_url": "https://api.github.com/repos/x/y", "title": "t", "state": "OPEN",
                         "closed_at": None, "labels": [], "sub_issues_summary": {}, "created_at": OPENED}}
            for n in range(1, 101)]
    src = tmp_path / "page.json"
    src.write_text(json.dumps(page))
    out = subprocess.run([jq, "-c", hub.board_items_jq("x/y"), str(src)], capture_output=True, text=True, check=True).stdout
    lines = [json.loads(line) for line in out.splitlines()]
    assert lines[-1] == {"_page": 100}
    assert sum(1 for line in lines if "_page" not in line) == 100


def test_the_items_read_carries_the_close_reason_so_no_card_is_read_again(hub, tmp_path):
    """#921: the charts tell a delivered card from one closed as not planned, and the items read already answers
    `state_reason` on the issue content, so the reason rides the same read."""
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not installed")
    def item(n, reason):
        return {"content_type": "Issue", "id": f"PVT_{n}", "fields": [],
                "content": {"number": n, "repository_url": "https://api.github.com/repos/x/y", "title": f"t{n}",
                            "state": "CLOSED", "state_reason": reason, "closed_at": "2026-10-02T00:00:00Z",
                            "labels": [], "sub_issues_summary": {}, "created_at": OPENED}}
    src = tmp_path / "items.json"
    src.write_text(json.dumps([item(1, "not_planned"), item(2, "completed")]))
    out = subprocess.run([jq, "-c", hub.board_items_jq("x/y"), str(src)], capture_output=True, text=True, check=True).stdout
    rows = {r["n"]: r for r in (json.loads(line) for line in out.splitlines()) if "_page" not in r}
    assert rows[1]["cr"] == "not_planned"
    assert rows[2]["cr"] == "completed"
    assert all(row["r"] == "x/y" for row in rows.values()), "the close reason owns its own key; `r` stays the row's repository (#1429)"


def test_the_history_name_is_derived_from_the_repository(tmp_path):
    flow = _load_flow()
    assert flow.history_path(tmp_path / "hub-board.json", "sbihaiko/agent-squad") == tmp_path / "hub-flow.sbihaiko_agent-squad.jsonl"
    assert flow.history_path(tmp_path / "hub-board.json", "x/y") != flow.history_path(tmp_path / "hub-board.json", "x/z")


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
