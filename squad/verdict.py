"""The verdict module: one pure function folds a run record and a target observation (ADR-0019, ADR-0105, ADR-0112).

Nothing else computes a verdict or an exit code. The engine derives node outcomes and routes on them.
The fold reads the record and the observation, never the graph.
"""
from dataclasses import dataclass, field

EXIT = {"completed": 0, "needs-human": 10, "conflict": 20, "failed": 1, "stopped": 30}
WORST = ("fail", "deadline", "needs-human", "pass")  # the join order of `each` and `dispatch` (ADR-0102 rule 9)
DEFAULT_ROUTE = {"pass": "continue", "fail": "exit:fail", "deadline": "exit:deadline", "needs-human": "exit:needs-human"}


@dataclass(frozen=True)
class Verdict:
    verdict: str
    exit_code: int
    reasons: list = field(default_factory=list)
    remains: list = field(default_factory=list)
    folded: list = field(default_factory=list)
    message: str = ""

    def event_fields(self):
        return {"verdict": self.verdict, "exit_code": self.exit_code, "reasons": self.reasons,
                "remains": self.remains, "folded": self.folded}


def exit_code(verdict):
    return EXIT[verdict]


def join(outcomes, rule="all"):
    """Join item or child outcomes. `all`: worst-of. `first_failure`: the first non-pass, in completion order."""
    if rule == "first_failure":
        return next((o for o in outcomes if o != "pass"), "pass")
    return next((w for w in WORST if w in outcomes), "pass")


def fold_dispatch(child_outcomes, rule, accepted):
    """A dispatch folds like `each`. A dispatch entry with no accepted child is fail, `no_dispatch` (ADR-0114 rule 7)."""
    if accepted == 0:
        return "fail", ["no_dispatch"]
    return join(child_outcomes, rule), []


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


def fold(events, observation=None, run_id=None):
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
    failing = [e for _, r, e in contrib if r in ("exit:fail", "exit:deadline") and e["outcome"] in ("fail", "deadline")]
    reasons = _dedupe([r for e in failing for r in e["reasons"]] + [f"{e['node']}:{e['outcome']}" for e in failing if not e["reasons"]])
    waits = _open_waits(events)
    since = max(e["pos"] for e in invocations)
    paused = [e["node"] for e in events if e["event"] == "paused" and e["pos"] > since]     # a person paused the run after its last start (ADR-0128)
    ran = {n for n, _ in last}
    remains = [n for n in plan if n not in ran]
    conflict = list(obs.get("conflict", [])) + ([pre["reason"]] if pre.get("status") == "conflict" else [])

    def make(v, rs, rem=True):
        rem_list = remains if v != "completed" and rem else []
        msg = _message(v, rs, rem_list, run_id, opts)
        return Verdict(v, EXIT[v], _dedupe(rs), rem_list, folded, msg)

    if pre.get("status") == "failed":
        return make("failed", [pre["reason"]])
    if root_ev is not None:
        o = root_ev["outcome"]
        if o == "deadline" and obs.get("effect_observed"):
            base, rs = "completed", []
        elif o == "deadline":
            base, rs = "failed", _dedupe(["deadline", *reasons])
        elif o == "fail":
            base, rs = "failed", _dedupe(root_ev["reasons"] + reasons)
        elif o == "needs-human":
            base, rs = "needs-human", root_ev["reasons"]
        else:
            base, rs = "completed", []
    elif failing:
        base, rs = "failed", reasons
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
    if conflict:
        return make("conflict", conflict)
    return make(base or "failed", rs)


def _message(verdict, reasons, remains, run_id, opts):
    lines = [f"verdict {verdict} (exit {EXIT[verdict]})"]
    if reasons:
        lines.append("reasons: " + ", ".join(reasons))
    if remains:
        lines.append("remains: " + ", ".join(remains))
    if verdict in ("needs-human", "stopped", "conflict") or (verdict == "failed" and remains):
        lines.append(f"resume: squad resume {run_id or '<run-id>'}")
    return "\n".join(lines)
