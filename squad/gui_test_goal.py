"""Goal navigation for gui-test/1 (#1199, ADR-0271): a `nav.goal` step names a check-vocabulary predicate and the runner
reaches it one move at a time, asking Jev one Choice per move.

The state Jev sees is the UI state the in-app hook reads (screen, focus, visible controls, menu options) as typed JSON,
never a screenshot. The options are the adapter's advertised pad or keyboard moves; a move that left the state unchanged is
withdrawn from that state. Each decision is recorded (`gui_decision`) with its options, choice, probabilities, served
snapshot and cost, and its cost counts against the run's spend. A replay injects the recorded moves and never asks Jev.
Goals serve setup, preconditions and recovery only: the validator rejects them on a step under test.

A `nav.sweep` step (#1200) walks the same way, toward screens it has not visited, and fails on a trap: a screen every
advertised move was tried on without any leaving it. The verdict names the screen and the path that reached it.
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

    def sweep(self, adapter, step, emit, budget=None):
        """None when every reachable screen was explored within the bound and none is a trap; else the failure reason."""
        args = step["action"]["args"]
        family, bound, scope = args.get("input", "pad"), args["bound"], args.get("scope")
        replayed = iter(self.replay.get(step["id"], [])) if self.replay is not None else None
        screens = {}  # key -> {"state", "tried": {label: next key or None}}
        path, spent, played = [], 0.0, 0  # path: [(move label, key reached)] from the start screen

        def key_of(state):
            return json.dumps({"screen": state.get("screen"), "dialogs": state.get("dialogs", [])}, sort_keys=True)

        def visit(state):
            screens.setdefault(key_of(state), {"state": state, "tried": {}})

        def route(frm):
            """The known moves from screen `frm` to the nearest screen with an untried move, as [(move label, key)]."""
            seen, queue = {frm}, [(frm, [])]
            while queue:
                at, how = queue.pop(0)
                if len(how) and any(m not in screens[at]["tried"] for m in moves_by_label):
                    return how
                for m, to in screens[at]["tried"].items():
                    if to is not None and to not in seen:
                        seen.add(to)
                        queue.append((to, how + [(m, to)]))
            return None

        state = adapter.ui_state()
        visit(state)
        start = state.get("screen")
        here = key_of(state)
        moves_by_label = {label(m): m for m in adapter.moves(family)}
        while True:
            node = screens[here]
            untried = [m for m in moves_by_label if m not in node["tried"]]
            if not untried:
                if not any(to != here and to is not None for to in node["tried"].values()) and self._judged(node["state"], scope):
                    return "trap screen %s: no advertised action leaves it; path %s" % (
                        node["state"].get("screen"), self._path(start, path))
                hop = route(here)
                if hop is None:
                    return None  # every reachable screen explored, none a trap
                if played + len(hop) > bound:
                    return f"sweep bound {bound} reached after {played} moves with screens left to explore; path {self._path(start, path)}"
                for m, to in hop:
                    self._play(adapter, moves_by_label[m])
                    played, here = played + 1, to
                    path.append((m, to))
                continue
            if played >= bound or (budget is not None and budget.exhausted) or (self.replay is None and spent >= self.max_spend):
                why = f"sweep bound {bound}" if played >= bound else "run spend cap" if budget is not None and budget.exhausted else "spend cap $%.2f" % self.max_spend
                return f"{why} reached after {played} moves with screens left to explore; path {self._path(start, path)}"
            if replayed is not None:
                nxt = next(replayed, None)
                if nxt is None:
                    return "replayed moves ended before the sweep did; path " + self._path(start, path)
                choice, pick = label(nxt), {"cost": 0}
            else:
                pick = self.chooser(state, untried)
                choice = pick["choice"] if pick["choice"] in untried else untried[0]
            cost = float(pick.get("cost", 0))
            spent += cost
            if budget is not None:
                budget.spent += cost
            emit("gui_decision", step["id"], options=untried, choice=choice, probabilities=pick.get("probabilities", {}),
                 state=state, cost=cost, move=moves_by_label[choice])
            self._play(adapter, moves_by_label[choice])
            played += 1
            state = adapter.ui_state()
            there = key_of(state)
            visit(state)
            node["tried"][choice] = there
            if there != here:
                path.append((choice, there))
                here = there

    @staticmethod
    def _judged(state, scope):
        return scope != "dialogs" or bool(state.get("dialogs"))

    @staticmethod
    def _path(start, path):
        return start + "".join(" -[%s]-> %s" % (m, json.loads(to)["screen"]) for m, to in path)

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
