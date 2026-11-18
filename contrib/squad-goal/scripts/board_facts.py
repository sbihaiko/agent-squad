"""The runtime producer of the board facts `board_columns.column_for` reads (ADR-0145).

The janitor's reconcile passes what the loop already knows; this module turns it into the facts. It reads no host and calls
no client, so the same inputs always give the same facts. The table decides the column from them, never this module.
"""
IN_DEV = ("behind", "identical")   # a compare standing: the branch tip is in the landing branch
AHEAD = ("ahead", "diverged")     # a compare standing: the branch holds commits the landing branch lacks
APPROVED = "VERDICT: APPROVE"
# A child card in Todo or beyond, before done: the legacy board's columns (In Progress, Code Complete) and the shipped
# board's (Doing, Review, Landing, Gating). Each vocabulary names its own columns, and no alias stands between them (#2190);
# this module reads no host, so it holds the names as data.
LIVE_WORK = ("Todo", "In Progress", "Doing", "Code Complete", "Review", "Landing", "Gating")


def review_state(verdict_text, sha_text, tip):
    """`none` when no review of the branch was read, `approved` when the review read this tip and approved it, else `waiting`."""
    if not (verdict_text or "").strip() or not (sha_text or "").strip(): return "none"
    first = verdict_text.strip().splitlines()[0]
    return "approved" if sha_text.strip() == tip and APPROVED in first else "waiting"


def issue_facts(*, entered, standing, approved, dev_running, review_waits, run_live=False, gating=False):
    """The facts of one issue. `entered`: its child is in the run's wave branch. `standing`: that branch against the landing
    branch. `approved`: the review of the wave tip approved it. `dev_running`: a check on the landing branch tip has not ended.
    `review_waits`: a review of its work branch was read and does not approve the tip. `gating`: a gate is live for the wave,
    or the wave's last attempt ended without a pass and none was made since (#1257, read from the attempt journal by the
    caller, so a restart answers the same)."""
    ahead = bool(entered) and standing in AHEAD
    return {"wave_branch": ahead, "landing": ahead and bool(approved), "in_dev": bool(entered) and standing in IN_DEV,
            "dev_ci": bool(entered) and standing in IN_DEV and bool(dev_running),
            "review_wait": bool(review_waits), "run_live": bool(run_live), "gating": bool(gating)}


def spec_facts(children):
    """The `child_live` fact of a spec: true when at least one sub-issue card is in flight (Todo or beyond, before done:
    Todo, Doing/In Progress, Review, Landing/Code Complete or Gating).

    `children` is the column each sub-issue's card reads, in the loop's own words; the sub-issue relation comes with the
    pass's one read. A spec is a document, never work (ADR-0142): the fact moves the spec's card, never a dispatch."""
    return {"child_live": any(c in LIVE_WORK for c in children)}
