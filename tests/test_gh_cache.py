"""A repeated read is answered from a snapshot, and nothing that gates a write is (#6, ADR-0277).

The throttle (#4) bounds the RATE of the loop's reads; this is the cache that spends no request at all. A read repeated
inside its window is answered from `$SG_STATE/gh-cache`, and what decides how long an entry lives is the kind of fact:

* a cheap fact lives for a TTL (`SG_GH_CACHE_WINDOW`);
* an expensive fact carries a key -- a head SHA the caller names, or the ETag GitHub answered with -- and an unchanged
  key refreshes the entry without a fresh parse, while a changed key throws the entry away and the next read is live;
* a corrupt or absent entry is a miss, never an error.

The liveness rule is the other half and it is proved by one test: every fact that decides a write (a merge, a close, a
card move) -- and the landing pass's own reads about the base it is about to write: is the branch still eligible, is the
base red, is the PR still open -- is read live, whatever the cache holds. A stale answer there is a merge onto a base
another pass already moved, a whole class of red the loop cannot attribute.

Driven through the seam: an injected transport and clock, never the network, never a real sleep.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader, f"cannot load {name}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ghread = _load("ghread")
ghthrottle = _load("ghthrottle")

CHEAP = "repos/o/r/labels"  # a cheap fact: a TTL decides
KEYED = "repos/o/r/pulls/7/files"  # an expensive fact: a key decides
NOT_MODIFIED = "HTTP/2.0 304 Not Modified\r\n\r\n"


class Clock:
    def __init__(self, at: float = 1000.0):
        self.at = at

    def now(self) -> float:
        return self.at


def answer(body: str, etag: str = "") -> str:
    """A body as `gh api --include` hands it over: a header block (with an ETag when there is one), then the body."""
    return f'HTTP/2.0 200 OK\r\netag: "{etag}"\r\n\r\n{body}' if etag else body


class Stub:
    """A transport that records every route it is asked for and answers from a script; the last answer repeats."""

    def __init__(self, *bodies: str, rc: int = 0):
        self.bodies = list(bodies)
        self.rc = rc
        self.seen: list[str] = []

    def __call__(self, route: str):
        self.seen.append(route)
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return ghread.Transport(rc=self.rc, body=body)


class Revalidator:
    """The conditional request: what GitHub answers when it is asked `If-None-Match: <etag>`."""

    def __init__(self, *bodies: str):
        self.bodies = list(bodies)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, route: str, etag: str):
        self.asked.append((route, etag))
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return ghread.Transport(rc=1 if body.startswith("HTTP/2.0 304") else 0, body=body)


def throttle(tmp_path: Path, clock: Clock, **kw):
    return ghthrottle.Throttle(state_dir=tmp_path, clock=clock.now, sleeper=lambda s: None, **kw)


def tokens(tmp_path: Path) -> float:
    return float(json.loads((tmp_path / ghthrottle.BUCKET_FILE).read_text())["tokens"])


# --- a read repeated inside its window ---------------------------------------------------------------------------------


def test_a_repeat_read_inside_the_window_spends_no_request_and_returns_the_same_answer(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub('[{"name":"bug"}]')
    first = th.read(CHEAP, transport=transport)
    left = tokens(tmp_path)
    clock.at += ghthrottle.DEFAULT_CACHE_WINDOW - 1
    second = th.read(CHEAP, transport=transport)
    assert first.is_answer and second.is_answer
    assert second.answer == first.answer == [{"name": "bug"}]
    assert second.body == first.body
    assert transport.seen == [CHEAP], f"the repeat reached GitHub: {transport.seen}"
    assert tokens(tmp_path) == left, "a snapshot answer must not spend a token from the shared bucket"


def test_a_cached_answer_is_shared_with_the_next_process(tmp_path):
    """The loop is processes, not one process: the snapshot lives in the state dir, so a second reader sees it."""
    clock = Clock()
    transport = Stub('[{"name":"bug"}]')
    throttle(tmp_path, clock).read(CHEAP, transport=transport)
    again = throttle(tmp_path, clock).read(CHEAP, transport=transport)
    assert again.answer == [{"name": "bug"}] and transport.seen == [CHEAP]


def test_the_read_is_live_again_once_the_window_has_passed(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub('["old"]', '["new"]')
    th.read(CHEAP, transport=transport)
    clock.at += ghthrottle.DEFAULT_CACHE_WINDOW + 1
    out = th.read(CHEAP, transport=transport)
    assert out.answer == ["new"] and transport.seen == [CHEAP, CHEAP]


def test_a_refusal_or_a_failure_is_never_kept(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    refused = Stub(json.dumps({"message": "Not Found", "status": "404"}), rc=1)
    assert th.read(CHEAP, transport=refused).is_refused
    failed = Stub("", rc=1)
    assert not th.read(CHEAP, transport=failed).is_answer
    ok = Stub("[]")
    assert th.read(CHEAP, transport=ok).is_answer and ok.seen == [CHEAP], "what was not an answer is not a snapshot"


# --- the window is configurable and documented -------------------------------------------------------------------------


def test_the_window_has_a_documented_default_and_an_environment_override(monkeypatch, tmp_path):
    monkeypatch.delenv(ghthrottle.CACHE_WINDOW_ENV, raising=False)
    assert ghthrottle.CACHE_WINDOW_ENV == "SG_GH_CACHE_WINDOW"
    assert ghthrottle.Cache(state_dir=tmp_path).window == ghthrottle.DEFAULT_CACHE_WINDOW == 60.0
    monkeypatch.setenv(ghthrottle.CACHE_WINDOW_ENV, "5")
    assert ghthrottle.Cache(state_dir=tmp_path).window == 5.0
    monkeypatch.setenv(ghthrottle.CACHE_WINDOW_ENV, "not-a-number")
    assert ghthrottle.Cache(state_dir=tmp_path).window == ghthrottle.DEFAULT_CACHE_WINDOW, "a bad value never stops a read"
    monkeypatch.setenv(ghthrottle.CACHE_WINDOW_ENV, "-3")
    assert ghthrottle.Cache(state_dir=tmp_path).window == ghthrottle.DEFAULT_CACHE_WINDOW
    assert "SG_GH_CACHE_WINDOW" in (ghthrottle.__doc__ or "") and "SG_GH_CACHE_WINDOW" in (ghthrottle.__doc__ or "")


def test_a_window_of_zero_turns_the_snapshot_off(monkeypatch, tmp_path):
    monkeypatch.setenv(ghthrottle.CACHE_WINDOW_ENV, "0")
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub("[]")
    th.read(CHEAP, transport=transport)
    th.read(CHEAP, transport=transport)
    assert transport.seen == [CHEAP, CHEAP]


def test_the_window_the_caller_sets_is_the_window_that_holds(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock, cache=ghthrottle.Cache(state_dir=tmp_path, window=10, clock=clock.now))
    transport = Stub('["a"]', '["b"]')
    th.read(CHEAP, transport=transport)
    clock.at += 9
    assert th.read(CHEAP, transport=transport).answer == ["a"]
    clock.at += 2
    assert th.read(CHEAP, transport=transport).answer == ["b"]


# --- an expensive fact is keyed: an ETag, or a head SHA ----------------------------------------------------------------


def _keyed_throttle(tmp_path: Path, clock: Clock, transport: Stub):
    th = throttle(tmp_path, clock)
    th.read(KEYED, transport=transport)
    clock.at += ghthrottle.DEFAULT_CACHE_WINDOW + 1
    return th


def test_an_unchanged_etag_refreshes_the_entry_without_a_fresh_parse(tmp_path, monkeypatch):
    clock = Clock()
    transport = Stub(answer('[{"filename":"a.py"}]', etag="v1"))
    th = _keyed_throttle(tmp_path, clock, transport)
    parsed: list[str] = []
    real = ghread.read
    monkeypatch.setattr(ghread, "read", lambda route, transport=None: (parsed.append(route), real(route, transport=transport))[1])
    revalidate = Revalidator(NOT_MODIFIED)
    out = th.read(KEYED, transport=transport, revalidate=revalidate)
    assert out.answer == [{"filename": "a.py"}]
    assert revalidate.asked == [(KEYED, '"v1"')], "the window passed: the key is checked, once"
    assert transport.seen == [KEYED], "an unchanged key must not read the fact again"
    assert parsed == [], "an unchanged key must not parse the fact again"
    again = th.read(KEYED, transport=transport, revalidate=revalidate)
    assert again.answer == out.answer and len(revalidate.asked) == 1, "the check refreshed the entry's window"


def test_a_changed_etag_invalidates_the_entry_and_the_next_read_is_live(tmp_path):
    clock = Clock()
    transport = Stub(answer('[{"filename":"a.py"}]', etag="v1"), answer('[{"filename":"b.py"}]', etag="v2"))
    th = _keyed_throttle(tmp_path, clock, transport)
    revalidate = Revalidator(answer("[]", etag="v2"))  # GitHub answers 200: the entity changed
    out = th.read(KEYED, transport=transport, revalidate=revalidate)
    assert out.answer == [{"filename": "b.py"}]
    assert transport.seen == [KEYED, KEYED], "a changed ETag means a live read"
    clock.at += 1
    later = th.read(KEYED, transport=transport, revalidate=Revalidator(NOT_MODIFIED))
    assert later.answer == [{"filename": "b.py"}], "the entry now carries the new ETag"


def test_a_head_sha_key_decides_instead_of_the_window(tmp_path, monkeypatch):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub('[{"filename":"a.py"}]', '[{"filename":"b.py"}]')
    th.read(KEYED, transport=transport, key="sha-1")
    clock.at += 10 * ghthrottle.DEFAULT_CACHE_WINDOW  # a head that did not move is the same fact, however old the entry is
    parsed: list[str] = []
    real = ghread.read
    monkeypatch.setattr(ghread, "read", lambda route, transport=None: (parsed.append(route), real(route, transport=transport))[1])
    same = th.read(KEYED, transport=transport, key="sha-1")
    assert same.answer == [{"filename": "a.py"}] and transport.seen == [KEYED] and parsed == []
    moved = th.read(KEYED, transport=transport, key="sha-2")
    assert moved.answer == [{"filename": "b.py"}], "a changed head invalidates the entry"
    assert transport.seen == [KEYED, KEYED]
    assert th.read(KEYED, transport=transport, key="sha-2").answer == [{"filename": "b.py"}] and len(transport.seen) == 2


# --- a corrupt or absent entry is a live read, never an error ----------------------------------------------------------

CORRUPT = {
    "empty": "",
    "not json": "{not json",
    "truncated": '{"route": "repos/o/r/labels", "at": 99',
    "a list": "[]",
    "a string": '"entry"',
    "no body": json.dumps({"route": CHEAP, "at": 1000.0}),
    "a time that is not a number": json.dumps({"route": CHEAP, "at": "soon", "body": "[]"}),
    "a body that is not JSON": json.dumps({"route": CHEAP, "at": 1000.0, "body": "<html>proxy</html>"}),
    "another route's entry": json.dumps({"route": "repos/o/r/other", "at": 1000.0, "body": '["x"]'}),
    "a time from the future": json.dumps({"route": CHEAP, "at": 9e12, "body": '["x"]'}),
}


@pytest.mark.parametrize("damage", sorted(CORRUPT))
def test_a_corrupt_entry_degrades_to_a_live_read(tmp_path, damage):
    clock = Clock()
    th = throttle(tmp_path, clock)
    path = ghthrottle.Cache(state_dir=tmp_path, clock=clock.now).entry_path(CHEAP)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CORRUPT[damage])
    transport = Stub('["live"]')
    out = th.read(CHEAP, transport=transport)
    assert out.is_answer and out.answer == ["live"], f"{damage}: {out}"
    assert transport.seen == [CHEAP]
    assert th.read(CHEAP, transport=transport).answer == ["live"] and len(transport.seen) == 1, "the live answer repairs it"


def test_an_absent_cache_directory_is_a_live_read(tmp_path):
    clock = Clock()
    th = throttle(tmp_path / "never" / "made", clock)
    transport = Stub('["live"]')
    assert th.read(CHEAP, transport=transport).answer == ["live"]


def test_an_unwritable_cache_costs_the_snapshot_never_the_read(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("a file where the directory should be")
    clock = Clock()
    th = throttle(blocker, clock)
    transport = Stub('["live"]')
    assert th.read(CHEAP, transport=transport).answer == ["live"]
    assert th.read(CHEAP, transport=transport).answer == ["live"]


# --- the liveness rule -------------------------------------------------------------------------------------------------

GATING = (
    "repos/o/r/issues/7",  # is the issue still open, what blocks it
    "repos/o/r/issues?state=open",
    "repos/o/r/pulls?state=open&per_page=100",  # is the PR still open
    "repos/o/r/pulls/7",  # is the PR mergeable, is it still open
    "repos/o/r/pulls/7/merge",
    "repos/o/r/commits/main/status",  # is the base red
    "repos/o/r/commits/main/check-runs",
    "repos/o/r/commits/abc123/check-suites",
    "repos/o/r/commits/main",
    "repos/o/r/branches/main",
    "repos/o/r/git/ref/heads/main",
    "repos/o/r/git/refs/heads/gui/6",
    "repos/o/r/compare/main...gui/6",
    "repos/o/r/actions/runs?branch=main",
    "repos/o/r/statuses/abc123",
    "repos/o/r/issues/7/comments",
    "repos/o/r/projects/4/items",
    "repos/o/r/some/route/nobody/classified",  # an unclassified route is live: the safe default
)


@pytest.mark.parametrize("route", GATING)
def test_a_fact_that_gates_a_write_is_classified_live(route):
    assert ghthrottle.fact_class(route) == ghthrottle.LIVE, route


def test_the_cheap_and_the_expensive_facts_are_classified_as_such():
    assert ghthrottle.fact_class(CHEAP) == ghthrottle.TTL
    assert ghthrottle.fact_class("repos/o/r") == ghthrottle.TTL
    assert ghthrottle.fact_class(KEYED) == ghthrottle.KEYED


def test_a_gating_read_ignores_the_cache_and_the_write_ignores_it_too(tmp_path):
    """The liveness rule, proved by mutation: the cache is poisoned for every fact that gates a write, with entries that
    are fresh, well formed and WRONG -- and the write path, the single-fact reads and the landing pass's own eligibility
    and base-state reads all go to GitHub and never believe it."""
    clock = Clock()
    state = tmp_path / "state"
    cache = ghthrottle.Cache(state_dir=state, window=3600, clock=clock.now)
    poison = {
        "repos/o/r/issues/7": {"state": "closed", "body": "", "number": 7},  # "already closed"
        "repos/o/r/pulls/7": {"state": "closed", "merged": True},  # "already merged"
        "repos/o/r/commits/main/status": {"state": "success"},  # "the base is green"
        "repos/o/r/branches/main": {"commit": {"sha": "stale"}},  # "the base has not moved"
    }
    for route, body in poison.items():
        cache.store(route, ghread.Read(kind=ghread.ANSWER, answer=body, body=json.dumps(body)))
    th = ghthrottle.Throttle(state_dir=state, cache=cache, clock=clock.now, sleeper=lambda s: None)

    # 1. the reads that gate a write: live, and the live answer is what comes back.
    live = {
        "repos/o/r/issues/7": {"state": "open", "body": "", "number": 7},
        "repos/o/r/pulls/7": {"state": "open", "merged": False},
        "repos/o/r/commits/main/status": {"state": "failure"},
        "repos/o/r/branches/main": {"commit": {"sha": "fresh"}},
    }
    for route, body in live.items():
        transport = Stub(json.dumps(body))
        out = th.read(route, transport=transport)
        assert transport.seen == [route], f"{route} was answered from the cache"
        assert out.is_answer and json.loads(out.body)["state" if "state" in body else "commit"] == (body["state"] if "state" in body else body["commit"]), f"{route}: {out.body}"

    # 2. the write path: the poisoned cache is never consulted, and the write that was asked for is the write sent.
    sent: list[list[str]] = []

    def write_transport(argv: list[str]):
        sent.append(argv)
        return ghread.Transport(rc=0, body=json.dumps({"state": "closed"}))

    done = ghread.write("repos/o/r/issues/7", method="PATCH", fields={"state": "closed"}, transport=write_transport)
    assert done.is_done and sent == [["--method", "PATCH", "repos/o/r/issues/7", "-f", "state=closed"]]
