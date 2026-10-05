"""The loop runner: repeat the ordered children under the `next` words until an exit rule fires.

A plain loop counts every entry against `max`. Two opt-ins change that, each kept generic to this one
executor (ADR-0100), never special-cased to a node name:

- `stall_after` (#93) counts only entries that land nothing: an entry where a child lands resets the count,
  so a loop making progress can run past `max` without the author tuning it by hand.
- `max_by: "task"` (#98) counts attempts per task, not entries. `max` is the ceiling of one task's attempts.
  An entry adds one attempt to every `task` key its accepted children name; a task that reaches `max`
  attempts ends the loop `fail` with reason `task_max_exhausted`. An entry that names no task key adds one
  unnamed attempt, so termination still holds. `loop_max_exhausted` never fires in this mode: advancing to
  a task not seen before must not spend the ceiling (owner ruling, 2026-10-01).

`max` stays the hard backstop otherwise.
"""
from squad.engine_errors import Defect
from squad.runners.walk import walk_children


class LoopRunner:
    async def run(self, ctx, node, entry, sc):
        t0 = ctx.clock()   # wall time of this loop entry: every exit below reports it (ADR-0109)
        i, why, limit = 0, [], ctx.eff.loop_max(node)   # a change applies at the next entry, never mid-entry
        st = _State(node, limit)
        while True:
            i += 1
            if not st.by_task and i > limit:
                return self._stop(ctx, node, entry, sc, t0, "fail", ["loop_max_exhausted", *why])
            pos0 = len(ctx.record.events)
            child, word, res = await walk_children(ctx, node, sc)
            again = word == "repeat"
            if again:
                why = res.reasons
            elif word.startswith("exit:"):
                return self._stop(ctx, node, entry, sc, t0, word[5:], res.reasons)
            elif word != "continue":
                raise Defect(f"{child}: unknown next word {word}")
            reason = st.task_max(ctx.record.events[pos0:]) or st.stall(ctx.record.events[pos0:])
            if reason:
                return self._stop(ctx, node, entry, sc, t0, "fail", [reason, *why])
            if not again:
                return self._stop(ctx, node, entry, sc, t0, "pass", [])

    def _stop(self, ctx, node, entry, sc, t0, outcome, reasons):
        return ctx.finish(node, entry, sc, outcome, reasons, elapsed=ctx.clock() - t0)


class _State:
    """The counters one loop entry keeps; each checker returns an exit reason or None."""

    def __init__(self, node, limit):
        self.limit, self.stall_after = limit, node.get("stall_after")
        self.by_task = node.get("max_by") == "task"
        self.attempts, self.unnamed, self.streak = {}, 0, 0

    def task_max(self, events):
        """`max_by: "task"`: the task keys this entry named are the ones its accepted children's requests carried."""
        if not self.by_task:
            return None
        tasks = {e["definition"].get("task") for e in events
                 if e["event"] == "spawn_accepted" and e.get("definition", {}).get("task")}
        for task in tasks:
            self.attempts[task] = self.attempts.get(task, 0) + 1
        self.unnamed += 0 if tasks else 1
        if self.unnamed >= self.limit or (tasks and max(self.attempts.values()) >= self.limit):
            return "task_max_exhausted"
        return None

    def stall(self, events):
        """`stall_after`: an entry that lands nothing extends the streak; a landing resets it."""
        if self.stall_after is None:
            return None
        landed = any(e["event"] == "land" and e["outcome"] == "pass" for e in events)
        self.streak = 0 if landed else self.streak + 1
        return "waves_stalled" if self.streak >= self.stall_after["count"] else None
