"""Goal navigation for gui-test/1 (#1199, ADR-0271): a `nav.goal` step names a check-vocabulary predicate and the runner
reaches it one move at a time, asking Jev one Choice per move.

The state Jev sees is the UI state the in-app hook reads (screen, focus, visible controls, menu options) as typed JSON,
never a screenshot. The options are the adapter's advertised pad or keyboard moves; a move that left the state unchanged is
withdrawn from that state. Each decision is recorded (`gui_decision`) with its options, choice, probabilities, served
snapshot and cost, and its cost counts against the run's spend. A replay injects the recorded moves and never asks Jev.
Goals serve setup, preconditions and recovery only: the validator rejects them on a step under test.
"""
import json

from squad.gui_test_checks import holds

SETTLE_TICKS = 4


def label(move):
    return move["name"] + " " + " ".join(str(v) for k, v in move["args"].items() if k not in ("ticks", "frames", "pad"))


def recorded_moves(events):
    """The moves each step's goal made, from a run's `gui_decision` events: {step id: [move, ...]}."""
    out = {}
    for e in events:
        if e["event"] == "gui_decision":
            out.setdefault(e["node"], []).append(e["move"])
    return out


class Navigator:
    """`chooser(state, option_labels) -> {"choice", "probabilities", "cost"}` is the Jev Choice call."""

    def __init__(self, chooser, max_decisions=12, max_spend=0.01, replay=None):
        self.chooser, self.max_decisions, self.max_spend, self.replay = chooser, max_decisions, max_spend, replay

    def reach(self, adapter, step, emit, budget=None):
        """None once the goal holds; else the reason it was not reached, ending in the last state."""
        goal = step["action"]["args"]["goal"]
        family = step["action"]["args"].get("input", "pad")
        if self.replay is not None:
            return self._replay(adapter, goal, self.replay.get(step["id"], []))
        failed, decisions, spent = {}, 0, 0.0
        while not holds(goal, adapter):
            state = adapter.ui_state()
            key = json.dumps({k: state.get(k) for k in ("screen", "focus")}, sort_keys=True)
            moves = {label(m): m for m in adapter.moves(family)}
            options = [o for o in moves if o not in failed.get(key, set())]
            why = ("decision cap " + str(self.max_decisions) if decisions >= self.max_decisions
                   else "spend cap $%.2f" % self.max_spend if spent >= self.max_spend
                   else "run spend cap" if budget is not None and budget.exhausted
                   else "no move left" if not options else None)
            if why:
                return f"goal not reached: {why}; last state {json.dumps(state, sort_keys=True)}"
            pick = self.chooser(state, options)
            choice = pick["choice"] if pick["choice"] in options else options[0]
            cost = float(pick.get("cost", 0))
            decisions, spent = decisions + 1, spent + cost
            if budget is not None:
                budget.spent += cost
            emit("gui_decision", step["id"], options=options, choice=choice, probabilities=pick.get("probabilities", {}),
                 state=state, cost=cost, move=moves[choice])
            self._play(adapter, moves[choice])
            if adapter.ui_state().get("screen") == state.get("screen") and adapter.ui_state().get("focus") == state.get("focus"):
                failed.setdefault(key, set()).add(choice)
        return None

    def _replay(self, adapter, goal, moves):
        for m in moves:
            self._play(adapter, m)
        return None if holds(goal, adapter) else f"goal not reached: replayed moves did not reach it; last state {json.dumps(adapter.ui_state(), sort_keys=True)}"

    @staticmethod
    def _play(adapter, move):
        adapter.inject(move["name"], move["args"])
        for _ in range(SETTLE_TICKS):
            adapter.advance()


DECISION_COST = 0.00002  # USD per Jev decision (ADR-0271, measured under ADR-0238)


def jev_chooser(client, goal_text="reach the goal"):
    """The Jev Choice call over `squad.jev.JevClient`: state is the hook's typed JSON, options are move labels."""
    def choose(state, options):
        q = {"move": {"type": "choice", "instructions": "Which move gets the application closer to the goal in `goal`?",
                      "criteria": {o: f"Press: {o}" for o in options}}}
        answers, _ = client.ask({"goal": goal_text, "ui": state}, q)
        a = answers["move"]
        return {"choice": a["choice"], "probabilities": a["probabilities"], "cost": DECISION_COST}
    return choose
