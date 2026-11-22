"""The private state of `guest` content and its 10-day purge (SPEC 2682, story 69; #2710).

The inbox, the queue, and the transcripts of a `guest` session live in one folder outside any repository and outside the
project `.claude` folder. Every entry older than 10 days is deleted. The folder and each entry are private to the
session model. A log line, an entry description, and an error reply never quote the client text.

Every id and every text here is a placeholder. The clock is a fake, so the purge is proved at an exact age.
"""
import os
import stat
from pathlib import Path

import pytest

from squad import gueststate as G

SENTINEL = "CLIENT-CONFIDENTIAL-7f3a"
DAY = 86400.0
START = 1_800_000_000.0


class Clock:
    """A fake clock: `now()` answers the value set here, and the test moves it forward by whole days."""

    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def state(tmp_path, clock=None):
    return G.GuestState(tmp_path / "guest-state", clock or Clock())


# --- the location of the folder ------------------------------------------------------------------------------------

def test_the_state_folder_sits_outside_the_repository_and_the_claude_folder(tmp_path):
    root = G.default_root({})
    assert not G.is_inside(root, tmp_path / "repo")
    assert not G.is_inside(root, tmp_path / "repo" / ".claude")
    assert G.default_root({"SQUAD_GUEST_STATE": str(tmp_path / "custom")}) == tmp_path / "custom"


def test_a_folder_inside_a_repository_or_its_claude_folder_is_refused(tmp_path):
    repo = tmp_path / "repo"
    claude = repo / ".claude"
    for public in (repo / "state", claude / "guest", claude):
        with pytest.raises(ValueError):
            G.GuestState(public, Clock(), repo=repo, claude=claude)
    G.GuestState(tmp_path / "private", Clock(), repo=repo, claude=claude)


# --- only the session model reads the content ----------------------------------------------------------------------

def test_the_folder_and_every_entry_are_private(tmp_path):
    g = state(tmp_path)
    for kind in G.KINDS:
        g.put(kind, "m1", SENTINEL)
        assert stat.S_IMODE(os.stat(g.root / kind).st_mode) == 0o700
    g.note("stored", "inbox", "m1")
    assert stat.S_IMODE(os.stat(g.root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(g.root / G.LOG_DIR).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(g.root / G.LOG_DIR / G.LOG_NAME).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(g.path("inbox", "m1")).st_mode) == 0o600


# --- the three kinds -----------------------------------------------------------------------------------------------

def test_an_entry_of_each_kind_round_trips(tmp_path):
    g = state(tmp_path)
    for kind in ("inbox", "queue", "transcripts"):
        g.put(kind, "m1", f"{kind}: {SENTINEL}")
        assert g.get(kind, "m1") == f"{kind}: {SENTINEL}"
    assert g.get("inbox", "m2") is None


def test_the_poller_checkpoint_lives_in_the_state_folder(tmp_path):
    g = state(tmp_path)
    assert g.checkpoint_path() == Path(g.root) / G.CHECKPOINT_NAME
    assert G.CHECKPOINT_NAME == "guest-checkpoint.json"


def test_a_kind_outside_the_three_is_refused(tmp_path):
    g = state(tmp_path)
    with pytest.raises(ValueError):
        g.put("drafts", "m1", SENTINEL)


def test_a_name_that_climbs_out_of_its_kind_folder_is_refused(tmp_path):
    g = state(tmp_path)
    for name in ("../escape", "a/b", "", ".hidden", "x" * 65):
        with pytest.raises(ValueError):
            g.put("inbox", name, SENTINEL)


# --- the purge on a fake clock -------------------------------------------------------------------------------------

def test_the_purge_deletes_every_entry_older_than_ten_days(tmp_path):
    clock = Clock()
    g = state(tmp_path, clock)
    g.put("inbox", "old-inbox", SENTINEL)
    g.put("queue", "old-queue", SENTINEL)
    g.put("transcripts", "old-transcript", SENTINEL)
    clock.advance(10 * DAY + 1)
    g.put("inbox", "fresh", SENTINEL)
    deleted = sorted((e.kind, e.name) for e in g.purge())
    assert deleted == [("inbox", "old-inbox"), ("queue", "old-queue"), ("transcripts", "old-transcript")]
    assert g.get("inbox", "fresh") == SENTINEL
    assert g.get("inbox", "old-inbox") is None


def test_an_entry_exactly_ten_days_old_stays(tmp_path):
    clock = Clock()
    g = state(tmp_path, clock)
    g.put("inbox", "boundary", SENTINEL)
    clock.advance(10 * DAY)
    assert g.purge() == []
    assert g.get("inbox", "boundary") == SENTINEL


def test_the_purge_leaves_an_entry_newer_than_ten_days(tmp_path):
    clock = Clock()
    g = state(tmp_path, clock)
    g.put("inbox", "recent", SENTINEL)
    clock.advance(9 * DAY)
    assert g.purge() == []
    assert g.get("inbox", "recent") == SENTINEL


def test_the_purge_removes_an_entry_it_cannot_read(tmp_path):
    g = state(tmp_path, Clock())
    g.put("inbox", "broken", SENTINEL)
    g.path("inbox", "broken").write_text("{not json")
    assert [(e.kind, e.name) for e in g.purge()] == [("inbox", "broken")]
    assert not g.path("inbox", "broken").exists()


# --- no guest text in the logs, the descriptions or an error reply --------------------------------------------------

def test_a_scan_finds_no_guest_text_in_the_logs(tmp_path):
    clock = Clock()
    g = state(tmp_path, clock)
    g.put("inbox", "m1", SENTINEL)
    g.note("stored", "inbox", "m1")
    assert g.get("inbox", "m1") == SENTINEL
    g.note("read", "inbox", "m1")
    clock.advance(10 * DAY + 1)
    for entry in g.purge():
        g.note("purged", entry.kind, entry.name)
    scanned = []
    logs = Path(g.root) / G.LOG_DIR
    for path in logs.rglob("*"):
        if path.is_file():
            scanned.append(path.read_text())
    assert scanned, "the log folder holds the log the scan reads"
    assert all(SENTINEL not in text for text in scanned), scanned


def test_an_entry_description_never_quotes_its_text(tmp_path):
    g = state(tmp_path)
    entry = g.put("inbox", "m1", SENTINEL)
    assert SENTINEL not in str(entry)
    assert SENTINEL not in entry.describe()
    assert SENTINEL not in g.note("stored", "inbox", "m1")


def test_an_error_reply_never_quotes_the_client_text():
    reply = G.error_reply(RuntimeError(f"the connector returned: {SENTINEL}"))
    assert SENTINEL not in reply
    assert reply == G.error_reply(None)
    assert reply.strip()
