"""The loop stays under the GitHub user rate limit (#45): what a read may skip, and how the cycle backs off.

The primary limit is per user and per hour, and the loop spends it from many processes. Three seams keep the spend down,
each driven here through an injected transport, clock and state dir -- never the network, never a real sleep:

* a **conditional request** for a snapshotted fact: once the window has passed the entry is checked with its ETag, and a
  `304` refreshes it without a read (GitHub does not count a `304` against the limit);
* an **aggregate snapshot**: the open-PR and open-issue lists are one GraphQL request each, and a repeat inside
  `SG_GH_AGGREGATE_WINDOW` (60 to 120 s) is answered from the state dir with no request at all;
* an **adaptive cycle**: the `x-ratelimit-*` headers of every live read are kept, and `stretch(base)` says how many
  extra seconds a cycle waits once less than 20 percent of the limit is left.

The cost of a whole pass over N candidates is proved by `contrib/squad-goal/scripts/test-gh-pass-bounded.sh`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from tests.test_gh_cache import CHEAP, NOT_MODIFIED, Clock, Revalidator, Stub, answer, throttle

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


ghthrottle = _load("ghthrottle")

PULLS = "repos/o/r/pulls?state=open&per_page=100"
LIST = '[{"number":7,"title":"t","body":"","head":{"ref":"gui/7"}}]'


def limited(body: str, remaining: int, limit: int = 5000, reset: float = 10_000.0, resource: str = "core") -> str:
    """A body as `gh api --include` hands it over, with the rate-limit headers GitHub puts on every answer."""
    return (
        f"HTTP/2.0 200 OK\r\nx-ratelimit-limit: {limit}\r\nx-ratelimit-remaining: {remaining}\r\n"
        f"x-ratelimit-reset: {int(reset)}\r\nx-ratelimit-resource: {resource}\r\n\r\n{body}"
    )


# --- conditional requests ----------------------------------------------------------------------------------------------


def test_a_cheap_fact_past_its_window_is_checked_with_its_etag_and_a_304_is_not_a_read(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub(answer('[{"name":"bug"}]', etag="v1"))
    first = th.read(CHEAP, transport=transport)
    clock.at += ghthrottle.DEFAULT_CACHE_WINDOW + 1
    revalidate = Revalidator(NOT_MODIFIED)
    again = th.read(CHEAP, transport=transport, revalidate=revalidate)
    assert revalidate.asked == [(CHEAP, '"v1"')], "the window passed: the entry is checked once, with the ETag it holds"
    assert transport.seen == [CHEAP], f"a 304 means the entry is current, so nothing is read again: {transport.seen}"
    assert again.answer == first.answer == [{"name": "bug"}]


def test_a_cheap_fact_whose_etag_moved_is_read_again(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub(answer('[{"name":"bug"}]', etag="v1"), answer('[{"name":"bug"},{"name":"ux"}]', etag="v2"))
    th.read(CHEAP, transport=transport)
    clock.at += ghthrottle.DEFAULT_CACHE_WINDOW + 1
    out = th.read(CHEAP, transport=transport, revalidate=Revalidator(answer("[]", etag="v2")))
    assert transport.seen == [CHEAP, CHEAP] and out.answer == [{"name": "bug"}, {"name": "ux"}]


# --- the aggregate snapshot --------------------------------------------------------------------------------------------


def test_an_aggregate_read_repeated_inside_its_window_sends_no_request(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub(LIST)
    first = th.read_aggregate(PULLS, transport=transport)
    left = json.loads((tmp_path / ghthrottle.BUCKET_FILE).read_text())["tokens"]
    clock.at += ghthrottle.DEFAULT_AGGREGATE_WINDOW - 1
    second = th.read_aggregate(PULLS, transport=transport)
    assert second.answer == first.answer and transport.seen == [PULLS], f"the repeat reached GitHub: {transport.seen}"
    assert json.loads((tmp_path / ghthrottle.BUCKET_FILE).read_text())["tokens"] == left


def test_an_aggregate_read_is_live_once_the_window_has_passed(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    transport = Stub(LIST)
    th.read_aggregate(PULLS, transport=transport)
    clock.at += ghthrottle.DEFAULT_AGGREGATE_WINDOW + 1
    th.read_aggregate(PULLS, transport=transport)
    assert transport.seen == [PULLS, PULLS]


def test_the_aggregate_window_is_between_one_and_two_minutes():
    assert 60 <= ghthrottle.DEFAULT_AGGREGATE_WINDOW <= 120


def test_a_refusal_is_never_kept_as_an_aggregate_snapshot(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    refusal = Stub('{"message":"Not Found","status":"404"}', rc=1)
    assert th.read_aggregate(PULLS, transport=refusal).is_refused
    th.read_aggregate(PULLS, transport=Stub(LIST))
    assert th.read_aggregate(PULLS, transport=refusal).answer == json.loads(LIST), "the answer that followed is kept"


def test_the_gating_routes_stay_live_through_the_plain_read(tmp_path):
    """The aggregate door is the caller's explicit choice; a plain read of the same route is still never snapshotted."""
    th = throttle(tmp_path, Clock())
    transport = Stub(LIST)
    th.read(PULLS, transport=transport)
    th.read(PULLS, transport=transport)
    assert transport.seen == [PULLS, PULLS]


# --- the adaptive cycle ------------------------------------------------------------------------------------------------


def test_a_healthy_quota_does_not_lengthen_the_cycle(tmp_path):
    clock = Clock(1000.0)
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/commits/abc", transport=Stub(limited("{}", remaining=4000)))
    assert th.stretch(90) == 0


def test_under_twenty_percent_left_the_cycle_is_longer_and_the_lower_the_longer(tmp_path):
    clock = Clock(1000.0)
    th = throttle(tmp_path, clock)
    stretches = []
    for remaining in (999, 400, 100):  # 19.98 percent, 8 percent, 2 percent of 5000
        th.read("repos/o/r/commits/abc", transport=Stub(limited("{}", remaining=remaining)))
        stretches.append(th.stretch(90))
    assert 0 < stretches[0] < stretches[1] < stretches[2], stretches


def test_exactly_twenty_percent_left_is_not_yet_low(tmp_path):
    clock = Clock(1000.0)
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/commits/abc", transport=Stub(limited("{}", remaining=1000)))
    assert th.stretch(90) == 0


def test_the_lowest_resource_decides_and_a_window_that_reset_does_not_count(tmp_path):
    clock = Clock(1000.0)
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/commits/abc", transport=Stub(limited("{}", remaining=4900, reset=2000)))
    th.read(PULLS, transport=Stub(limited(LIST, remaining=100, reset=2000, resource="graphql")))
    assert th.stretch(90) > 0, "the GraphQL pool is nearly empty even though the REST pool is not"
    clock.at = 2500.0
    assert th.stretch(90) == 0, "the hour rolled over: the old reading says nothing about the new window"


def test_a_reading_the_state_dir_does_not_hold_is_a_healthy_quota(tmp_path):
    assert throttle(tmp_path, Clock()).stretch(90) == 0
    (tmp_path / ghthrottle.RATELIMIT_FILE).write_text("{not json")
    assert throttle(tmp_path, Clock()).stretch(90) == 0
