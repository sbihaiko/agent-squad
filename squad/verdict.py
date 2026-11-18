"""The verdict module: one pure function folds a run record and a target observation.

Nothing else computes a verdict or an exit code. The engine derives node outcomes and routes on them.
The fold reads the record and the observation, never the graph.
"""
import shlex
from dataclasses import dataclass, field

EXIT = {"completed": 0, "needs-human": 10, "conflict": 20, "failed": 1, "stopped": 30}
# The join order of `each` and `dispatch`: an unfinished proof is worse than a pass and better than a hold, because
# delivery outranks the proof (ADR-0164).
WORST = ("fail", "deadline", "needs-human", "unproven", "pass")
LEDGER_HOLDS = ("ledger_held", "after_held", "review_needs_human")  # a child its issue ledger held, a child that runs after one (ADR-0149 rule 4), and a child whose review streak ended the issue (ADR-0151, #1574)
# An `unproven` outcome ends the step it proves, the way a deadline does, and the fold turns it into a completed run whose
# reasons name the node whose proof did not finish (ADR-0164).
DEFAULT_ROUTE = {"pass": "continue", "fail": "exit:fail", "deadline": "exit:deadline", "needs-human": "exit:needs-human",
                 "unproven": "exit:unproven"}


@dataclass(frozen=True)
class Verdict:
    verdict: str
    exit_code: int
    reasons: list = field(default_factory=list)
    remains: list = field(default_factory=list)
    folded: list = field(default_factory=list)
    message: str = ""
    end_class: str | None = None

    def event_fields(self):
        fields = {"verdict": self.verdict, "exit_code": self.exit_code, "reasons": self.reasons,
                  "remains": self.remains, "folded": self.folded}
        return {**fields, "end_class": self.end_class} if self.end_class else fields


def exit_code(verdict):
    return EXIT[verdict]


def killed(reason):
    """The verdict of a run whose process is gone: it never reached its own fold, so the record names why it ended."""
    return Verdict("failed", EXIT["failed"], [reason])


def join(outcomes, rule="all"):
    """Join item or child outcomes. `all`: worst-of. `first_failure`: the first non-pass, in completion order."""
    if rule == "first_failure":
        return next((o for o in outcomes if o != "pass"), "pass")
    return next((w for w in WORST if w in outcomes), "pass")


def fold_dispatch(child_outcomes, rule, accepted):
    """A dispatch folds like `each`. A dispatch entry with no accepted child is fail, `no_dispatch`."""
    if accepted == 0:
        return "fail", ["no_dispatch"]
    return join(child_outcomes, rule), []


def ledger_holds(seen):
    """The reasons of the children an issue ledger held, `[]` when none was. A hold asks the owner (ADR-0029), so neither
    a dependent's failure nor a cancelled sibling hides it: the dispatch asks the owner whatever its join rule says."""
    return _dedupe([r for _n, o, rs in seen if o == "needs-human" and rs and rs[0] in LEDGER_HOLDS for r in rs])


def _route(ev):
    return ev.get("routed") or DEFAULT_ROUTE[ev["outcome"]]


def _last_outcomes(events):
    last = {}
    for e in events:
        if e["event"] == "outcome":
            last[(e["node"], e.get("item"))] = e
    return last


def _open_waits(events):
    waits = {}
    for e in events:
        if e["event"] == "wait":
            waits[(e["node"], e.get("item"))] = e["pos"]
        elif e["event"] == "outcome":
            waits.pop((e["node"], e.get("item")), None)
    return [k[0] for k in waits]


def _dedupe(xs):
    return list(dict.fromkeys(xs))


UNFINISHED = ("deadline", "unproven")   # the outcomes whose proof did not finish, so the work they hold is kept (ADR-0164)
DELIVERS = ("pass", "unproven")   # a child outcome whose work goes to review and to the run's wave branch: unproven holds no red (#2037)


def delivers(outcome):
    """True for a child outcome whose work is reviewed and landed in the run's own target. The wave tip check still measures
    it before `dev`, and an unproven tip never lands there (ADR-0164)."""
    return outcome in DELIVERS


def _kept_after_deadline(events, failing):
    """The last commit a passed node wrote before the first unfinished proof, when every failing outcome is a deadline or
    unproven: the branch the run keeps (ADR-0164). None when the run failed for another reason or no commit came first."""
    if not failing or any(e["outcome"] not in UNFINISHED for e in failing):
        return None
    first = min(e["pos"] for e in failing)
    commits = [e for e in events if e["event"] == "workspace_commit" and e.get("status") == "committed" and e["pos"] < first]
    return commits[-1] if commits else None


def _unproven(failing, kept, root=None):
    """The reasons an unfinished proof carries: each node whose proof did not finish, and the branch's commit when one was kept.

    The root is not named when another node's proof is the cause: an unproven root only says the step it holds ended that way."""
    nodes = _dedupe([e["node"] for e in failing if e["node"] != root]) or ([root] if root else [])
    return [f"unproven:{n}" for n in nodes] + ([f"kept:{kept['workspace']}@{str(kept.get('head') or '')[:9]}"] if kept else [])


def fold(events, observation=None, run_id=None, run_folder=None):
    """Fold a run record and a target observation into one verdict.

    observation: {"conflict": [reasons], "effect_observed": bool}. A `conflict` comes from the target only.
    """
    obs = observation or {}
    start = next(e for e in events if e["event"] == "run_start")
    root, plan = start["root"], start["plan"]
    last = _last_outcomes(events)
    invocations = [e for e in events if e["event"] in ("run_start", "resume")]
    opts = invocations[-1].get("engine_options") or {}
    pres = [e["preflight"] for e in invocations if e.get("preflight")]
    pre = pres[-1] if pres else {"status": "ok"}
    root_ev = last.get((root, None))
    folded = sorted(e["pos"] for e in last.values())
    contrib = [(k, _route(e), e) for k, e in last.items()]
    # An unproven outcome is a failing outcome that holds nothing: the work it covers is still delivered (ADR-0164), and
    # the reasons name the node whose proof did not finish, so it is never silently unproven. It counts only when it
    # exited its step, symmetric with fail and deadline: a node that answered unproven but was routed to continue failed
    # nothing, so it never enters the fold as a failure (#1825).
    failing = sorted([e for _, r, e in contrib if r in ("exit:fail", "exit:deadline") and e["outcome"] in ("fail", "deadline")]
                     + [e for _, r, e in contrib if r == "exit:unproven" and e["outcome"] == "unproven"],
                     key=lambda e: e["pos"])
    reasons = _dedupe([r for e in failing for r in e["reasons"]] + [f"{e['node']}:{e['outcome']}" for e in failing if not e["reasons"]])
    waits = _open_waits(events)
    since = max(e["pos"] for e in invocations)
    paused = [e["node"] for e in events if e["event"] == "paused" and e["pos"] > since]     # a person paused the run after its last start
    ran = {n for n, _ in last}
    remains = [n for n in plan if n not in ran]
    conflict = list(obs.get("conflict", [])) + ([pre["reason"]] if pre.get("status") == "conflict" else [])
    kept = _kept_after_deadline(events, failing)
    # A run completes when its proof did not finish and no check measured a red: every failing outcome is unproven, or a
    # deadline whose branch was committed first. A measured red is never in `unproven`, so it still fails the run.
    only_unproven = bool(failing) and all(e["outcome"] == "unproven" for e in failing)
    unproven = _unproven(failing, kept, root) if failing and (kept or only_unproven) else None

    def make(v, rs, rem=True):
        rem_list = remains if v != "completed" and rem else []
        msg = _message(v, rs, rem_list, _resume_ref(run_folder, run_id), opts)
        return Verdict(v, EXIT[v], _dedupe(rs), rem_list, folded, msg, _end_class(failing) if v == "failed" else None)

    if pre.get("status") == "failed":
        return make("failed", [pre["reason"]])
    if root_ev is not None:
        o = root_ev["outcome"]
        if o == "deadline" and obs.get("effect_observed"):
            base, rs = "completed", []
        elif o == "deadline" and unproven:
            base, rs = "completed", unproven
        elif o == "deadline":
            base, rs = "failed", _dedupe(["deadline", *reasons])
        elif o == "unproven":
            base, rs = "completed", (unproven or [f"unproven:{root}"])
        elif o == "fail":
            base, rs = "failed", _dedupe(root_ev["reasons"] + reasons)
        elif o == "needs-human":
            base, rs = "needs-human", root_ev["reasons"]
        else:
            base, rs = "completed", []
    elif failing:
        base, rs = ("completed", unproven) if unproven else ("failed", reasons)
    elif waits or any(_route(e) == "exit:needs-human" for _, _, e in contrib):
        base, rs = "needs-human", [f"wait:{w}" for w in waits]
    elif paused:
        base, rs = "stopped", [f"paused:{n}" for n in paused]
    elif opts.get("stop_after") and (opts["stop_after"], None) in last or opts.get("stop_after") in ran:
        base, rs = "stopped", [f"stopped_after:{opts['stop_after']}"]
    else:
        base, rs = None, ["no_root_outcome"]
    if base == "failed":
        return make("failed", rs)
    if base == "completed" and unproven:      # a step passed, but a node's proof did not finish: the run says so (ADR-0164)
        rs = _dedupe(rs + unproven)
    if conflict:
        return make("conflict", conflict)
    return make(base or "failed", rs)


def _end_class(failing):
    """The end class a failed run names: the one class its failing outcomes carry, or None when they name two or none (#2217)."""
    classes = {e["end_class"] for e in failing if e.get("end_class")}
    return next(iter(classes)) if len(classes) == 1 else None


def _resume_ref(run_folder, run_id):
    """What `squad resume` takes: the absolute run folder when known, so the hint works from any directory; else the run id."""
    return shlex.quote(str(run_folder)) if run_folder else (run_id or "<run-id>")


def _message(verdict, reasons, remains, ref, opts):
    lines = [f"verdict {verdict} (exit {EXIT[verdict]})"]
    if reasons:
        lines.append("reasons: " + ", ".join(reasons))
    if remains:
        lines.append("remains: " + ", ".join(remains))
    if verdict in ("needs-human", "stopped", "conflict") or (verdict == "failed" and remains):
        lines.append(f"resume: squad resume {ref}")
    return "\n".join(lines)
