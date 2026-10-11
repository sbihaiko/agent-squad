"""The hub's gate strip: one batched query per poll feeds the five cells of R C T B H (#1772).

The card used to guess: a REST list plus two REST calls per open PR, and a per-PR `except Exception: pass` that left the
CI list and the merge state unset -- which the chip read as green. The strip renders what is known, `?` when it is not,
and greys out past 180 s, so a failed or partial query can never read as passing.
"""
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

SHA = "9c0ffee12ab34cd5"           # the head of the PR under test
SHA9 = SHA[:9]
OLD = "1e38e5a7b"                  # an earlier head of the same PR
PR = 1790

REVIEW_APPROVE = "VERDICT: APPROVE\n## Blocking\n- src/a.py:10 — null deref on empty input — raises TypeError — return []\n"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_gate_strip", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """A squad state dir with the files the loop leaves behind: the run's output, the review and the gate reason."""
    st = tmp_path / "state"
    (st / "auto").mkdir(parents=True)
    monkeypatch.setattr(hub, "STATE", st)
    monkeypatch.delenv("SG_REREVIEW_MINUTES", raising=False)
    return st


def _summary(st, verdict="APPROVE", ci="success:9", sha9=SHA9, pr=PR):
    """The loop's own summary line for a head: the `#<pr> <sha9> ` namespace plus the CI snapshot and the verdict."""
    p = st / f"revci-{pr}.out"
    p.write_text((p.read_text() if p.exists() else "") + f"#{pr} {sha9} clean CI={ci} VERDICT: {verdict}\n")


def _row(**kw):
    """One open PR as the batched query returns it."""
    r = {"n": PR, "t": "Hub: a gate strip under the chip", "u": "2026-10-11T09:00:00Z", "o": "2026-10-11T08:00:00Z",
         "h": SHA, "d": False, "base": "local/next", "ms": "clean", "l": ["wave:12"], "th": 0,
         "ci": ["success"], "rollup": "pass", "facts": True}
    r.update(kw)
    return r


def _cells(hub, row, **kw):
    return {c["k"]: c for c in hub.gate_strip(row, **kw)["cells"]}


def test_the_strip_carries_the_five_named_cells(hub, state):
    """R C T B H, in that order, each with its own tooltip."""
    _summary(state)
    strip = hub.gate_strip(_row(), t=1000.0, now=1000.0)
    assert [c["k"] for c in strip["cells"]] == ["R", "C", "T", "B", "H"]
    assert all(c["tip"] for c in strip["cells"])


def test_the_review_ci_threads_base_and_holds_cells_read_their_source(hub, state):
    """Every cell comes from its own source: the loop's verdict, the loop's CI snapshot, the live threads, GitHub's base and merge state, the labels."""
    _summary(state)
    c = _cells(hub, _row(th=2), t=1000.0, now=1000.0)
    assert c["R"]["v"] == "OK"
    assert c["C"]["v"] == "success:9"
    assert c["T"]["v"] == "2"
    assert c["B"]["v"] == "local/next·clean"
    assert c["H"]["v"] == "wave:12"


def test_an_approve_from_an_older_head_renders_dash_with_the_old_sha(hub, state):
    """An APPROVE is a word about the head it names: on a newer head the cell says `-` and the tooltip names the old sha."""
    _summary(state, sha9=OLD)
    c = _cells(hub, _row(), t=1000.0, now=1000.0)
    assert c["R"]["v"] == "-"
    assert OLD in c["R"]["tip"]


def test_a_review_queued_for_this_head_renders_in_progress(hub, state):
    """`auto/launched-<pr>-<sha9>` says the loop is reviewing this head; no verdict has been printed yet."""
    (state / "auto" / f"launched-{PR}-{SHA9}").write_text("")
    c = _cells(hub, _row(), t=1000.0, now=1000.0)
    assert c["R"]["v"] == "..."


def test_a_live_rollup_that_disagrees_with_the_loop_snapshot_is_marked(hub, state):
    """The loop's snapshot is the CI cell; when GitHub's live rollup says otherwise the cell says so instead of hiding it."""
    _summary(state, ci="success:9")
    c = _cells(hub, _row(rollup="fail"), t=1000.0, now=1000.0)
    assert c["C"]["v"] == "success:9 live!=fail"
    assert "success:9" in c["C"]["tip"]


def test_a_head_the_loop_never_summarised_reads_unknown_not_passing(hub, state):
    """No `CI=` line for this head means the CI cell knows nothing: `?`, never a green word."""
    c = _cells(hub, _row(), t=1000.0, now=1000.0)
    assert c["C"]["v"] == "?"


def test_a_partial_query_leaves_the_live_cells_unknown(hub, state):
    """The live cells (threads, base, labels) are `?` when the query did not return them -- the shape the old per-PR `except Exception: pass` produced."""
    _summary(state)
    c = _cells(hub, _row(th=None, ms=None, base=None, l=None, facts=False), t=1000.0, now=1000.0)
    assert c["T"]["v"] == "?"
    assert c["B"]["v"] == "?"
    assert c["H"]["v"] == "?"
    assert c["R"]["v"] == "OK"          # the loop's own words survive: they are not live facts


def test_an_unset_merge_state_does_not_read_as_passing(hub, state):
    """The path that must go: `ci`/`ms` unset used to fall through to `awaiting review`, which reads as green."""
    _summary(state)
    row = _row(ms=None, ci=None, rollup=None, th=None, facts=False)
    assert hub.pr_state(row) != ["idle", "awaiting review"]
    assert "unknown" in hub.pr_state(row)[1]
    assert hub.pr_state(row)[0] not in ("ready", "running")


def test_the_strip_greys_out_past_180_s(hub, state):
    """Aging is the strip's own honesty: up to 180 s the facts are the poll's, past that the strip greys out."""
    _summary(state)
    assert hub.gate_strip(_row(), t=1000.0, now=1180.0)["old"] is False
    assert hub.gate_strip(_row(), t=1000.0, now=1181.0)["old"] is True


def test_a_debt_only_changes_is_not_blocking(hub, state):
    """The autopilot merges a debt-only CHANGES once the debt is filed: the card must not render it as blocking."""
    _summary(state, verdict="CHANGES")
    (state / f"review-{PR}.md").write_text(
        "VERDICT: CHANGES\n## Debt\n- src/b.py:42 — retry loop has no backoff — it hammers the API after a 500 — retries wait with exponential backoff\n")
    pass_epoch = int(time.time())
    (state / "auto" / ".pass").write_text(str(pass_epoch))
    (state / "auto" / f"gate-{PR}").write_text(f"{SHA9}\t{pass_epoch}\tmerging\tthe debt-only CHANGES is filed; the merge is going ahead\n")
    c = _cells(hub, _row(), t=1000.0, now=1000.0)
    assert c["R"]["v"] == "debt"
    assert hub.pr_state(_row())[:2] == ["ready", "ready to merge"]        # the gate's word, not `changes requested`
    assert "does not block" in c["R"]["tip"]


def test_a_blocking_changes_is_still_blocking(hub, state):
    """The positive control: a CHANGES with a Blocking finding is a blocking verdict, in the cell and in the chip."""
    _summary(state, verdict="CHANGES")
    (state / f"review-{PR}.md").write_text(REVIEW_APPROVE.replace("APPROVE", "CHANGES"))
    c = _cells(hub, _row(), t=1000.0, now=1000.0)
    assert c["R"]["v"] == "CHG"
    assert hub.pr_state(_row()) == ["changes", "changes requested"]


def test_one_query_serves_every_open_pr(hub):
    """One GraphQL query carries every fact the card needs: no per-PR REST pair and no separate open-PR list."""
    q = hub.OPEN_PR_QUERY
    for field in ("pullRequests", "isDraft", "headRefOid", "baseRefName", "mergeStateStatus", "labels",
                  "reviewThreads", "statusCheckRollup", "updatedAt"):
        assert field in q, field


def test_the_query_response_maps_to_one_row_per_open_pr(hub):
    """The mapping keeps unknown as None: a null sub-object is `?` downstream, never an empty-but-green list."""
    rows = hub.prs_from_graphql({"data": {"repository": {"pullRequests": {"nodes": [
        {"number": PR, "title": "Hub: a gate strip", "isDraft": False, "updatedAt": "2026-10-11T09:00:00Z",
         "createdAt": "2026-10-11T08:00:00Z", "headRefOid": SHA, "baseRefName": "local/next", "mergeStateStatus": "CLEAN",
         "labels": {"nodes": [{"name": "wave:12"}]},
         "reviewThreads": {"totalCount": 2, "nodes": [{"isResolved": False}, {"isResolved": True}]},
         "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": [
             {"__typename": "CheckRun", "conclusion": "SUCCESS", "status": "COMPLETED"},
             {"__typename": "StatusContext", "state": "PENDING"}]}}}}]}},
        {"number": PR + 1, "title": "Hub: threads", "isDraft": True, "updatedAt": "2026-10-11T09:30:00Z",
         "createdAt": "2026-10-11T08:30:00Z", "headRefOid": OLD + "c0d2e", "baseRefName": "local/next",
         "mergeStateStatus": "UNKNOWN", "labels": None, "reviewThreads": None, "commits": None}]}}}})
    assert [r["n"] for r in rows] == [PR, PR + 1]
    a, b = rows
    assert (a["d"], a["h"], a["base"], a["ms"], a["l"], a["th"]) == (False, SHA, "local/next", "clean", ["wave:12"], 1)
    assert a["ci"] == ["success", "pending"] and a["rollup"] == "pending" and a["facts"] is True
    assert (b["l"], b["th"], b["ci"], b["rollup"]) == (None, None, None, None)   # unknown, not empty-and-green
    assert b["ms"] == "" or b["ms"] == "unknown"                                  # GitHub has not computed it
    assert b["facts"] is False


def test_a_query_that_returns_nothing_is_not_a_pass(hub, state):
    """A failed call leaves the last good rows in place: it never stands in as a fresh, passing one."""
    assert hub.prs_from_graphql({"data": None}) is None
    assert hub.prs_from_graphql({"errors": [{"message": "rate limited"}]}) is None
    assert hub.prs_from_graphql(json.loads("null")) is None
