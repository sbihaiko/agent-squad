"""The loop runner: repeat the ordered children under the `next` words until an exit rule fires.

A plain loop counts against `max` the entries that fail the same way: an entry whose failure reads like the
one before it extends the streak, a changed failure restarts it at one, and `max` entries in a row ends the loop `fail`,
reason `loop_max_exhausted`. Total entries are never capped: spend and the deadline bound a loop that keeps changing what
fails (ADR-0134). Two opt-ins change that, each kept generic to this one executor, never special-cased to a node name:

- `stall_after` counts only entries that land nothing: an entry where a child lands resets the count,
  so a loop making progress can run past `max` without the author tuning it by hand.
- `max_by: "task"` counts attempts per task, not entries. `max` is the ceiling of one task's attempts.
  An entry adds one attempt to every `task` key its accepted children name. A task that reaches `max`
  attempts is dropped: a `diagnostic` event records it with the reasons of its last children, and the loop
  goes on, so one task that cannot be done never stops the others. An entry that names only dropped tasks, or
  names no task key `max` times, ends the loop `fail` with reason `task_max_exhausted`, so termination still
  holds. A loop that would end `pass` with a dropped task ends `fail` and names each one, `task_exhausted:<task>`:
  the dropped work is never hidden. `loop_max_exhausted` never fires in this mode: advancing to
  a task not seen before must not spend the ceiling (owner ruling, 2026-10-01).

`max_by: "task"` never reads the streak.
"""
from squad import acrepeat
from squad.engine_errors import Defect
from squad.runners.walk import walk_children
from squad.stall import signature


class LoopRunner:
    async def run(self, ctx, node, entry, sc):
        t0 = ctx.clock()   # wall time of this loop entry: every exit below reports it
        why, limit = [], ctx.eff.loop_max(node)   # a change applies at the next entry, never mid-entry
        st = _State(node, limit)
        while True:
            pos0 = len(ctx.record.events)
            child, word, res = await walk_children(ctx, node, sc)
            again = word == "repeat"
            if again:
                why = res.reasons
            elif word.startswith("exit:"):
                return self._stop(ctx, node, entry, sc, t0, word[5:], res.reasons, st)
            elif word != "continue":
                raise Defect(f"{child}: unknown next word {word}")
            events = ctx.record.events[pos0:]
            repeated_ac = st.ac_repeated(ctx, child, res, events)
            if again and repeated_ac:
                return self._stop(ctx, node, entry, sc, t0, "needs-human", ["ac_repeated", *st.ac_findings], st, repeated=st.ac_evidence)
            reason = st.task_max(ctx, node, entry, events) or st.stall(events) or (st.same_failure(res.reasons) if again else None)
            if reason:
                return self._stop(ctx, node, entry, sc, t0, "fail", [reason, *why], st)
            if not again:
                return self._stop(ctx, node, entry, sc, t0, "pass", [], st)

    def _stop(self, ctx, node, entry, sc, t0, outcome, reasons, st=None, **extra):
        if st is not None and st.dropped and outcome == "pass":
            outcome, reasons = "fail", []
        if st is not None and st.dropped:
            reasons = [*reasons, *(f"task_exhausted:{t}" for t in st.dropped)]
        return ctx.finish(node, entry, sc, outcome, reasons, elapsed=ctx.clock() - t0, **extra)


class _State:
    """The counters one loop entry keeps; each checker returns an exit reason or None."""

    def __init__(self, node, limit):
        self.limit, self.stall_after = limit, node.get("stall_after")
        self.by_task = node.get("max_by") == "task"
        self.attempts, self.unnamed, self.streak, self.dropped = {}, 0, 0, []
        self.last_failure, self.same = None, 0
        self.ac, self.ac_findings, self.ac_evidence = acrepeat.Streak(), [], {}

    def ac_repeated(self, ctx, child, res, events):
        """#1378: True when an ac-verify gate fails the same way twice in a row. A gate that passes, or a commit that moves
        the head, in the walk starts the count again."""
        if any(e["event"] == "outcome" and e["outcome"] == "pass" and ctx.nodes.get(e["node"], {}).get("kind") == "gate" for e in events):
            self.ac.reset()
        if child is None or ctx.nodes[child].get("check") != "ac-verify" or res.outcome != "fail":
            return False
        out = next(e for e in reversed(events) if e["event"] == "outcome" and e["node"] == child)
        heads = [e["workspace"]["head"] for e in events if e["event"] == "outcome" and e.get("workspace")]
        evidence = dict(out.get("evidence") or {})
        self.ac_findings, self.ac_evidence = list(res.reasons), evidence
        return self.ac.failed(acrepeat.fingerprint(res.reasons, evidence), heads[-1] if heads else self.ac.head)

    def same_failure(self, reasons):
        """A repeated entry extends the streak when its failure reads like the last one, else restarts it at one. At `max` it ends the loop."""
        if self.by_task:
            return None
        sig = signature(" | ".join(str(r) for r in reasons))
        self.same = self.same + 1 if sig == self.last_failure else 1
        self.last_failure = sig
        return "loop_max_exhausted" if self.same >= self.limit else None

    def task_max(self, ctx, node, entry, events):
        """`max_by: "task"`: the task keys this entry named are the ones its accepted children's requests carried.
        A task that reaches `max` is dropped and recorded. An entry that names only tasks dropped before it ends the loop."""
        if not self.by_task:
            return None
        tasks = {e["definition"].get("task") for e in events
                 if e["event"] == "spawn_accepted" and e.get("definition", {}).get("task")}
        asked_again = bool(tasks) and all(t in self.dropped for t in tasks)
        for task in tasks:
            self.attempts[task] = self.attempts.get(task, 0) + 1
            if self.attempts[task] >= self.limit and task not in self.dropped:
                self.dropped.append(task)
                ctx.record.append("diagnostic", source="loop", node=node["name"], entry=entry,
                                  payload={"task_dropped": task, "attempts": self.attempts[task], "reasons": _last_reasons(events, task)})
        self.unnamed += 0 if tasks else 1
        return "task_max_exhausted" if self.unnamed >= self.limit or asked_again else None

    def stall(self, events):
        """`stall_after`: an entry that lands nothing extends the streak; a landing resets it."""
        if self.stall_after is None:
            return None
        landed = any(e["event"] == "land" and e["outcome"] == "pass" for e in events)
        self.streak = 0 if landed else self.streak + 1
        return "waves_stalled" if self.streak >= self.stall_after["count"] else None


def _last_reasons(events, task):
    """The reasons of the last finished child that carried `task` in this entry, for the record of a dropped task."""
    kids = [e["child"] for e in events if e["event"] == "spawn_accepted" and e.get("definition", {}).get("task") == task]
    outs = [e for e in events if e["event"] == "outcome" and e["node"] in kids]
    return [str(r)[:300] for r in (outs[-1]["reasons"] if outs else [])][:4]
