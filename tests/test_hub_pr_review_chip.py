"""Hub OPEN PRS card: a failed or timed-out review attempt on the PR's current head says so, with the retry (#1502).

PR #1491 sat on `awaiting review` for 40+ minutes while the loop's review had timed out and been re-queued.
"""
import importlib.util
import os
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

SHA = "1e38e5a7b41c0d2e"          # current head of the PR under test
SHA9 = SHA[:9]
OLD = "9c0ffee12deadbeef"        # the head before it


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_pr_review_chip", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """A squad state dir with the files the loop leaves behind (review-times.tsv, auto/ markers)."""
    st = tmp_path / "state"
    (st / "auto").mkdir(parents=True)
    monkeypatch.setattr(hub, "STATE", st)
    monkeypatch.delenv("SG_REREVIEW_MINUTES", raising=False)
    return st


def _row(st, ts, pr, model, secs, rc):
    """One review-times.tsv row: timestamp, PR, model, seconds, exit code (review.sh appends one per model attempt)."""
    with (st / "review-times.tsv").open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(ts))}\t{pr}\t{model}\t{secs}\t{rc}\n")


def _mark(st, kind, pr, sha9, at):
    """A loop marker in auto/: `launched-` when the loop queued a review for this head, `rereview-` when it re-queued."""
    p = st / "auto" / f"{kind}-{pr}-{sha9}"
    p.write_text("")
    os.utime(p, (at, at))


def _pr(n=1491, head=SHA):
    return {"n": n, "t": "Hub: the OPEN PRS card says why a review is late", "u": "2026-10-11T09:00:00Z", "h": head, "d": False, "ci": [], "ms": "clean", "o": "2026-10-11T08:00:00Z"}


def test_a_timed_out_attempt_on_the_current_head_names_the_model_and_the_seconds(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 900)
    _row(state, now - 840, 1491, "deepseek-v4-flash", 120, 142)
    st = hub.review_state(1491, SHA9, now=now)
    assert st is not None
    assert st["chip"] == "review timed out · deepseek-v4-flash · 120 s"
    assert st["cls"] == "changes"
    ps = hub.pr_state(_pr())
    assert ps[1] == "review timed out · deepseek-v4-flash · 120 s"
    assert "reviewing" not in ps[1] and "awaiting review" not in ps[1]


def test_a_head_that_moved_after_the_attempt_goes_back_to_awaiting_review(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9[:9], now - 900)          # the attempt belonged to the head the PR no longer has
    _row(state, now - 840, 1491, "deepseek-v4-flash", 120, 142)
    assert hub.review_attempts(1491, OLD[:9]) == []
    assert hub.review_state(1491, OLD[:9], now=now) is None
    assert hub.pr_state(_pr(head=OLD))[1] == "awaiting review"


def test_the_requeue_marker_says_when_the_next_attempt_is_due(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 1500)
    _mark(state, "rereview", 1491, SHA9, now - 240)              # re-queued 4 min ago: next attempt no sooner than 20 min after it
    _row(state, now - 30, 1491, "deepseek-v4-flash", 120, 142)   # the attempt the re-queue started, timed out again
    st = hub.review_state(1491, SHA9, now=now)
    assert st["retry"] == "retry in 16 min"
    assert hub.pr_state(_pr())[3] == "retry in 16 min"


def test_an_attempt_in_flight_reads_retrying_now(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 1500)
    _row(state, now - 1400, 1491, "deepseek-v4-flash", 120, 142)  # the first timeout
    _mark(state, "rereview", 1491, SHA9, now - 60)                # the loop re-queued: that attempt is running now
    st = hub.review_state(1491, SHA9, now=now)
    assert st["retry"] == "retrying now"
    assert hub.pr_state(_pr())[3] == "retrying now"


def test_a_failed_attempt_names_the_model_and_keeps_the_reason_in_the_tooltip(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 900)
    _row(state, now - 600, 1491, "grok-4.5", 300, 141)
    (state / "revci-1491.out").write_text(
        f"#1491 {SHA9} clean CI=success:2 VERDICT: BLOCKED (every review model failed)\n")
    st = hub.review_state(1491, SHA9, now=now)
    assert st["chip"] == "review failed"   # the model stays in the tooltip, not on the chip (owner, 2026-10-11)
    assert "BLOCKED (every review model failed)" in st["tip"]
    assert "exit 141" not in st["tip"]


def test_a_failed_attempt_with_no_verdict_line_falls_back_to_the_exit_code(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 900)
    _row(state, now - 600, 1491, "grok-4.5", 300, 99)
    assert "exit 99" in hub.review_state(1491, SHA9, now=now)["tip"]


def test_the_tooltip_lists_the_attempts_for_this_head_newest_first(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 1500)
    _row(state, now - 1400, 1491, "codex-gpt-5", 300, 1)
    _row(state, now - 200, 1491, "deepseek-v4-flash", 120, 142)
    att = hub.review_attempts(1491, SHA9)
    assert [a["model"] for a in att] == ["deepseek-v4-flash", "codex-gpt-5"]
    assert [a["result"] for a in att] == ["timed out", "failed"]
    tip = hub.review_state(1491, SHA9, now=now)["tip"]
    assert tip.index("deepseek-v4-flash") < tip.index("codex-gpt-5")
    assert "120 s" in tip and "timed out" in tip


def test_a_passing_attempt_keeps_todays_chip(hub, state):
    now = time.time()
    _mark(state, "launched", 1491, SHA9, now - 900)
    _row(state, now - 600, 1491, "deepseek-v4-flash", 90, 0)
    (state / "revci-1491.out").write_text(f"#1491 {SHA9} clean CI=success:2 VERDICT: APPROVE\n")
    assert hub.review_state(1491, SHA9, now=now) is None
    ps = hub.pr_state(_pr())
    assert ps[1] == "ready to merge"
    assert len(ps) == 2 and not (ps[2] if len(ps) > 2 else "")


def test_a_pr_the_loop_never_reviewed_has_no_review_state(hub, state):
    now = time.time()
    _row(state, now - 600, 1491, "deepseek-v4-flash", 120, 142)   # a row with no marker for any head of this PR
    assert hub.review_attempts(1491, SHA9) == []
    ps = hub.pr_state(_pr())
    assert ps == ["idle", "awaiting review"]
    assert hub.pr_state(_pr(n=1492))[1] == "awaiting review"


def test_the_requeue_window_follows_the_loops_own_setting(hub, state, monkeypatch):
    now = time.time()
    monkeypatch.setenv("SG_REREVIEW_MINUTES", "5")
    _mark(state, "launched", 1491, SHA9, now - 1500)
    _mark(state, "rereview", 1491, SHA9, now - 240)
    _row(state, now - 30, 1491, "deepseek-v4-flash", 120, 142)
    assert hub.review_state(1491, SHA9, now=now)["retry"] == "retry in 1 min"


def test_page_carries_the_chip_tooltip_and_the_retry_line(hub):
    card = hub.PAGE
    assert "${esc(x.ps[2])}" in card and "${esc(x.ps[3])}" in card   # the chip's tooltip and the muted retry line
    assert "<div class=prr>" in card
    css = card[card.index("#bbody .pst{"):]
    assert "max-width:100%" in css[:220] and "overflow-wrap:anywhere" in css[:220]   # a long chip wraps inside the column
    prr = css[css.index("#bbody .it .prr{"):]
    assert "text-overflow:ellipsis" in prr[:220] and "max-width:100%" in prr[:220]
