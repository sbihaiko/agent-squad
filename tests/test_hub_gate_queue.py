"""The hub's gate queue (#1173): one reading of the ledger's hold episodes, drawn as one block on the status line. The
block names how many children wait, on which cause, and the age of the oldest episode; the figures come from the ledger,
never from a second count, and an empty queue reads as empty, never as unknown. The page reads the block from the one
poll it already makes, so a queue of five children costs no request per child.

The ledger is the one writer of its facts (ADR-0149): this suite writes the episode files the reader consumes, in the
shape issue_ledger.py writes them, and never a second counter.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = 1_000_000.0
LOCK = "the gate lock is held by pid 91; the child enters on a later pass"
SLOTS = "the gate slots (caps.gates) are full, held by 2 slots; the child enters on a later pass"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_gate_queue", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger(st, n, episodes):
    """One issue ledger file as issue_ledger.py writes it: the key in its file name, the hold episodes inside."""
    d = st / "state"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"o__r__{n}.json").write_text(json.dumps({"own": {"count": 0, "runs": [], "stopped": None},
                                                   "loop": {"count": 0, "runs": [], "stopped": None},
                                                   "episodes": episodes}))


def _queue(hub, st, monkeypatch):
    monkeypatch.setattr(hub, "STATE", st)
    return [i for i in hub.foot_items(hub.foot_sources(NOW), NOW) if i["key"] == "queue"]


def test_the_queue_block_names_the_waiting_children_their_causes_and_the_oldest_age(hub, tmp_path, monkeypatch):
    """Two children wait and a third episode is closed: the open two are the queue, each cause is counted, and the oldest
    open episode gives the age. The closed episode contributes nothing -- it is no child waiting now."""
    _ledger(tmp_path, 1, [{"run": "run-a", "cause": LOCK, "start": NOW - 7200, "count": 4}])
    _ledger(tmp_path, 2, [{"run": "run-b", "cause": SLOTS, "start": NOW - 600, "count": 1}])
    _ledger(tmp_path, 3, [{"run": "run-c", "cause": LOCK, "start": NOW - 300, "count": 1, "closed": NOW - 60}])
    (block,) = _queue(hub, tmp_path, monkeypatch)
    assert block["text"] == "queue 2 held · gate lock 1 · gate slots 1 · oldest 2h ago"
    assert "o/r#1" in block["tip"] and "o/r#2" in block["tip"]
    assert "run-a" not in block["tip"] and "o/r#3" not in block["tip"]


def test_an_empty_queue_reads_empty_never_unknown(hub, tmp_path, monkeypatch):
    """The ledger holds no open episode: the queue is empty, which is a reading, never the `?` an unreadable source draws."""
    (tmp_path / "state").mkdir()
    (block,) = _queue(hub, tmp_path, monkeypatch)
    assert block["text"] == "queue empty"
    assert "?" not in block["text"]


def test_an_unreadable_ledger_reads_unknown_never_empty(hub, tmp_path, monkeypatch):
    """A ledger file the hub cannot parse makes the queue unknown: an empty queue would claim a reading the hub does not have."""
    d = tmp_path / "state"
    d.mkdir()
    (d / "o__r__1.json").write_text("{not json")
    (block,) = _queue(hub, tmp_path, monkeypatch)
    assert block["text"] == "queue ?"
    assert "empty" not in block["text"]


def test_the_queue_is_one_read_of_the_ledger_not_one_per_waiting_child(hub, tmp_path, monkeypatch):
    """Five waiting children, five ledger files: one pass over the episodes opens each file once and the source is read
    once per refresh. A second count, or a request per child, would move both numbers."""
    for n in range(1, 6):
        _ledger(tmp_path, n, [{"run": f"run-{n}", "cause": LOCK, "start": NOW - 60 * n, "count": 1}])
    opened = []
    real_read = Path.read_text

    def counting(self, *a, **k):
        if self.parent.name == "state":
            opened.append(self.name)
        return real_read(self, *a, **k)

    calls = []
    real_episodes = hub.issue_ledger.open_episodes

    def counting_episodes(*a, **k):
        calls.append(1)
        return real_episodes(*a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    monkeypatch.setattr(hub.issue_ledger, "open_episodes", counting_episodes)
    monkeypatch.setattr(hub, "STATE", tmp_path)
    items = hub.foot_items(hub.foot_sources(NOW), NOW)
    assert len(calls) == 1, "the episodes are read once per refresh"
    assert sorted(opened) == [f"o__r__{n}.json" for n in range(1, 6)], "each ledger file is opened once, never once per child"
    assert [i for i in items if i["key"] == "queue"][0]["text"].startswith("queue 5 held")


def test_the_queue_is_read_again_only_when_a_ledger_file_changes(hub, tmp_path, monkeypatch):
    """#1746: the footer asks for the queue once a tick, and the ledger changes when a child is held or released. The pass
    is kept between ticks, so an unchanged ledger opens no file; a changed one is read on the same tick, and the queue and
    each age follow it."""
    _ledger(tmp_path, 1, [{"run": "run-a", "cause": LOCK, "start": NOW - 60, "count": 1}])
    monkeypatch.setattr(hub, "STATE", tmp_path)
    opened = []
    real_read = Path.read_text

    def counting(self, *a, **k):
        if self.parent.name == "state":
            opened.append(self.name)
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)

    def queue(now):
        return [i for i in hub.foot_refresh(now) if i["key"] == "queue"][0]["text"]

    assert queue(NOW).startswith("queue 1 held")
    assert opened == ["o__r__1.json"]

    opened.clear()
    assert queue(NOW + hub.LOAD_SAMPLE_SECS).startswith("queue 1 held")
    assert opened == [], "the footer read the ledger again with no ledger file changed"

    _ledger(tmp_path, 1, [{"run": "run-a", "cause": LOCK, "start": NOW - 60, "count": 1},
                          {"run": "run-b", "cause": LOCK, "start": NOW - 30, "count": 1}])
    assert queue(NOW + 2 * hub.LOAD_SAMPLE_SECS).startswith("queue 2 held")
    assert opened == ["o__r__1.json"], "a changed ledger file was not read again"
