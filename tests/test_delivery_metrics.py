"""GitHub issue #705: the hub shows read-only delivery metrics.

The computation is one pure module (`squad/delivery_metrics.py`) over the loop's own local records: the issue
ledgers, the landing ledger, the auto/ markers and the run folders. The hub binds it to a GET route with no
write surface and the page renders it. The panel decides nothing: it only counts what the records hold.

The fixtures are the records the loop actually writes, at the shape each writer keeps:
- `state/<owner>__<repo>__<n>.json` is one issue ledger (issue_ledger.py): its `own` and `loop` failures, the
  `wave` fact of each run, and the `landed` facts a landing pass wrote.
- `landed.tsv` is one row per delivered issue: epoch, branch, landed sha, issue.
- `auto/waved-<run>` is the durable record that the run cut a wave; `auto/wave-<run>` is the same record while
  the wave is not landed. `auto/<prefix>-same-<key>` is the failed rounds a fix pass left with the same findings.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SRC = HUB_PY.read_text()
PAGE = SRC[SRC.index('PAGE = r"""'): SRC.index("PAGE = PAGE.replace")]
DM_PY = ROOT / "squad/delivery_metrics.py"

SESSION_LIMIT = ("ResultError: Claude Code returned an error result: You've hit your session limit · resets "
                 "7:27am (America/Sao_Paulo) (exit code: 1)")


@pytest.fixture(scope="module")
def dm():
    spec = importlib.util.spec_from_file_location("delivery_metrics_under_test", DM_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_delivery_metrics", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ledger(key, own_runs=(), loop_runs=(), wave=None, landed=None):
    """One issue ledger as issue_ledger.py writes it: the two failure ledgers, the per-run wave facts, the landings."""
    def side(runs):
        return {"count": len(runs), "runs": [{"run": r, "key": "evo", "why": "why"} for r in runs], "stopped": None}
    return {"key": key, "own": side(own_runs), "loop": side(loop_runs), "wave": wave or {},
            "landed": landed or {}, "requeued": {}}


def build_state(tmp_path):
    """The records of one loop: seven issues, five waved runs and two fix passes."""
    st = tmp_path / "state"
    (st / "state").mkdir(parents=True)
    (st / "auto").mkdir()
    (st / "landed.tsv").write_text(
        "1700000000\twave/run-1\tabc123\t101\n"
        "1700000100\twave/run-1\tabc123\t106\n"
        "1700000200\twave/run-2\tdef456\t102\n"
        "1700000300\tfix/7-x\taaa111\t103\n")
    ledgers = {
        "acme__repo__101.json": ledger("acme/repo#101", wave={"run-1": {"state": "delivered", "cause": "landed"}},
                                       landed={"run-1": {"kind": "run", "sha": "abc123"}}),
        "acme__repo__102.json": ledger("acme/repo#102", own_runs=["run-2"],
                                       wave={"run-2": {"state": "refused", "cause": "gate"},
                                             "run-0": {"state": "held", "cause": "review"}}),
        "acme__repo__103.json": ledger("acme/repo#103", wave={"run-3": {"state": "entered", "cause": "entered"}},
                                       landed={"fix/7-x": {"kind": "branch", "sha": "aaa111"}}),
        "acme__repo__105.json": ledger("acme/repo#105", wave={"run-5": {"state": "stuck", "cause": "landing",
                                                                      "tries": 3}}),
    }
    for name, rec in ledgers.items():
        (st / "state" / name).write_text(json.dumps(rec))
    (st / "state" / "acme__repo__104.json").write_text('{"key": "acme/repo#104", "own": ')
    (st / "auto" / "waved-run-1").write_text("wave/run-1\n")
    (st / "auto" / "waved-run-4").write_text("wave/run-4\n")   # landed empty: no landing row, no failure
    (st / "auto" / "waved-run-5").write_text("wave/run-5\n")   # its landing failed: a stuck fact, never a no-op
    (st / "auto" / "wave-run-6").write_text("wave/run-6\n")    # still in flight
    (st / "auto" / "waved-run-6").write_text("wave/run-6\n")
    (st / "auto" / "branchfix-same-gui_7-x").write_text("2\n")
    (st / "auto" / "branchfix-fp-gui_7-x").write_text("deadbeef\n")
    (st / "auto" / "runfix-same-run-x_child").write_text("0\n")   # the findings changed: no repeat left
    return st


def _outcome(model, error):
    return {"event": "outcome", "node": "router", "entry": 1, "outcome": "fail", "reasons": ["adapter_error"],
            "spend": 0.0, "turns": 0, "cap": 1.0, "elapsed": 1.0, "pos": 2,
            "model": model, "diagnostic": {"error": error}}


def _verdict():
    return {"event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["adapter_error"], "remains": [],
            "folded": [2], "pos": 3}


def _run(root, name):
    d = root / name
    d.mkdir(parents=True)
    events = [_outcome("claude-gpt-5.6-terra", SESSION_LIMIT), _verdict()]
    (d / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return d


def snapshot(root):
    """Every file under `root` with its bytes: the proof that a read wrote nothing."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def test_dispatched_work_that_landed_nothing_is_counted_apart_from_noop_waves(tmp_path, dm):
    st = build_state(tmp_path)
    m = dm.compute(st, [])
    assert m["dispatched"] == {"attempts": 5, "landed": 1, "landed_nothing": 3, "open": 1,
                               "by_state": {"refused": 1, "held": 1, "stuck": 1}}
    assert m["noop_waves"] == {"count": 1, "runs": ["run-4"]}
    # a no-op wave is a fact about the wave, never an attempt of an issue: the two numbers never overlap
    assert m["dispatched"]["landed_nothing"] == 3 and m["noop_waves"]["count"] == 1


def test_unique_attempts_per_issue_counts_each_run_once(tmp_path, dm):
    st = build_state(tmp_path)
    per = {r["issue"]: r for r in dm.compute(st, [])["attempts"]["per_issue"]}
    assert (per["acme/repo#102"]["attempts"], per["acme/repo#102"]["landed_nothing"]) == (2, 2)
    assert (per["acme/repo#101"]["attempts"], per["acme/repo#101"]["landed"]) == (1, 1)
    assert (per["acme/repo#103"]["attempts"], per["acme/repo#103"]["open"]) == (1, 1)


def test_adapter_failures_are_counted_by_class(tmp_path, dm):
    st = build_state(tmp_path)
    runs = tmp_path / "runs"
    a = _run(runs, "run-a")
    b = _run(runs, "run-b")
    m = dm.compute(st, [a, b])
    assert m["adapter"]["runs"] == 2
    assert m["adapter"]["classes"] == [{"model": "gpt-5.6-terra", "backend": "gateway", "cause": "quota", "count": 2}]
    assert m["adapter"]["count"] == 2 and m["adapter"]["err"] is None


def test_repeated_review_findings_are_counted_from_the_fix_markers(tmp_path, dm):
    st = build_state(tmp_path)
    r = dm.compute(st, [])["review"]
    assert r["repeated"] == 2
    assert r["loops"] == [{"key": "gui_7-x", "prefix": "branchfix", "rounds": 2}]   # a changed fingerprint is no repeat


def test_landings_are_counted_from_the_landing_ledger(tmp_path, dm):
    st = build_state(tmp_path)
    assert dm.compute(st, [])["landings"] == {"count": 4, "branches": 3, "issues": 4}


def test_a_corrupt_ledger_is_skipped_and_never_raises(tmp_path, dm):
    st = build_state(tmp_path)
    m = dm.compute(st, [])
    assert m["sources"]["ledgers"] == 4 and m["sources"]["unreadable"] == 1
    assert m["err"] is None


def test_the_metrics_read_their_records_and_write_nothing(tmp_path, dm):
    st = build_state(tmp_path)
    runs = tmp_path / "runs"
    _run(runs, "run-a")
    before = snapshot(tmp_path)
    dm.compute(st, [runs / "run-a"])
    assert snapshot(tmp_path) == before


def test_the_module_opens_no_file_for_writing(tmp_path, dm):
    src = DM_PY.read_text()
    assert "write_text(" not in src and "os.replace(" not in src and "open(" not in src and ".write(" not in src


def test_the_hub_binds_the_metrics_and_the_route_is_a_read(hub, tmp_path, monkeypatch):
    st = build_state(tmp_path)
    monkeypatch.setattr(hub, "STATE", st)
    monkeypatch.setattr(hub, "project_dirs", lambda: [tmp_path / "runs"])
    d = hub.delivery()
    assert d["err"] is None and d["dispatched"]["landed_nothing"] == 3 and d["landings"]["count"] == 4
    get = SRC[SRC.index("def do_GET"): SRC.index("def do_POST")]
    post = SRC[SRC.index("def do_POST"): SRC.index("SELF = pathlib.Path")]
    assert '"/api/delivery"' in get and "/api/delivery" not in post
    assert "def do_PUT" not in SRC and "def do_DELETE" not in SRC


def test_a_missing_squad_package_is_reported_and_never_guessed(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "STATE", build_state(tmp_path))
    monkeypatch.setattr(hub, "project_dirs", lambda: [tmp_path / "runs"])

    def boom(*a, **kw):
        raise ModuleNotFoundError("no squad package")

    monkeypatch.setattr(hub, "load_squad", boom)
    d = hub.delivery()
    assert d["err"] and "dispatched" not in d


def test_the_page_carries_the_delivery_panel_and_reads_the_route():
    assert "<details id=dlv class=sp>" in PAGE
    assert 'fetch("/api/delivery")' in SRC


def test_the_panel_renders_the_five_numbers():
    i = SRC.index("async function pollDelivery")
    body = SRC[i: SRC.index("\n", SRC.index("pollDelivery();setInterval", i))]
    for field in ("landed_nothing", "noop_waves", "per_issue", "adapter", "review", "landings"):
        assert field in body
