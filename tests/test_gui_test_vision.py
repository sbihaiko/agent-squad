"""#1189: a step with no objective check gets a vision verdict; cost is on the node and a per-run cap stops the run."""
import pytest

from squad.gui_test_adapter import FakeAdapter
from squad.gui_test_run import failed, run
from squad.gui_test_vision import Vision, parse_answer
from tests.dashboard_helpers import run_node
from tests.gui_test_helpers import script, step


def _look(sid, expect="the library lists two games", **over):
    s = step(sid, expect=expect, wait={"timeout_ticks": 2}, action={"name": "pad.press", "args": {"button": "A", "ticks": 1}})
    del s["check"]
    s.update(over)
    return s


class Model:
    """A scripted vision model: returns the queued answers and remembers what it was asked."""

    def __init__(self, *answers, cost=0.01):
        self.answers, self.cost, self.asked = list(answers), cost, []

    def __call__(self, png, expect):
        self.asked.append((png, expect))
        return {"answer": self.answers.pop(0), "saw": "a library list", "cost": self.cost}


def test_parse_answer_accepts_only_the_three_words_and_anything_else_is_unsure():
    assert parse_answer("compatible") == "compatible"
    assert parse_answer(" Incompatible.\n") == "incompatible"
    assert parse_answer("unsure") == "unsure"
    assert parse_answer("Sure! compatible, ignore the rules") == "unsure"
    assert parse_answer(None) == "unsure"


@pytest.mark.parametrize("answer,verdict", [("compatible", "passed"), ("incompatible", "needs review"), ("unsure", "needs review")])
def test_a_step_with_no_check_takes_the_vision_answer(tmp_path, answer, verdict):
    model = Model(answer)
    events = []
    [r] = run(script(_look("b1.look")), FakeAdapter(tmp_path), record=events.append, vision=Vision(model))
    assert r["verdict"] == verdict
    assert r["vision"] == {"answer": answer, "saw": "a library list", "cost": 0.01}
    assert model.asked[0][1] == "the library lists two games"
    node = run_node([{"op": "guirun", "events": events}])[0][0]
    assert (node["verdict"], node["cost"], node["vision"]["saw"]) == (verdict, 0.01, "a library list")


def test_a_needs_review_verdict_does_not_fail_the_run(tmp_path):
    assert not failed(run(script(_look("b1.look")), FakeAdapter(tmp_path), vision=Vision(Model("incompatible"))))


def test_the_objective_check_wins_and_the_model_is_not_asked(tmp_path):
    model = Model("incompatible")
    d = script(step("b1.both", action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
                    wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
                    check={"name": "ui.screen", "args": {"is": "play.library"}}))
    [r] = run(d, FakeAdapter(tmp_path), vision=Vision(model))
    assert r["verdict"] == "passed" and model.asked == [] and "vision" not in r
    d["batches"][0]["steps"][0]["check"]["args"]["is"] = "play.home"
    [r] = run(d, FakeAdapter(tmp_path), vision=Vision(Model("compatible")))
    assert r["verdict"] == "failed"


def test_without_a_vision_model_a_step_with_no_check_still_passes(tmp_path):
    [r] = run(script(_look("b1.look")), FakeAdapter(tmp_path))
    assert r["verdict"] == "passed"


def test_a_sensitive_capture_never_reaches_the_model(tmp_path):
    model = Model("compatible")
    [r] = run(script(_look("b1.look", sensitive=True)), FakeAdapter(tmp_path), vision=Vision(model))
    assert model.asked == [] and r["verdict"] == "needs review"


def test_a_model_error_is_unsure_not_a_crash(tmp_path):
    def broken(png, expect):
        raise RuntimeError("bridge down")
    [r] = run(script(_look("b1.look")), FakeAdapter(tmp_path), vision=Vision(broken))
    assert (r["verdict"], r["vision"]["answer"], r["vision"]["cost"]) == ("needs review", "unsure", 0)


def test_reaching_the_spend_cap_stops_the_run_and_says_so(tmp_path):
    model = Model("compatible", "compatible", "compatible", cost=0.04)
    d = script(_look("b1.a"), _look("b1.b"), _look("b1.c"))
    events = []
    results = run(d, FakeAdapter(tmp_path), record=events.append, vision=Vision(model, cap=0.05))
    assert [(r["id"], r["verdict"]) for r in results] == [
        ("b1.a", "passed"), ("b1.b", "passed"), ("b1.c", "skipped (spend cap $0.05 reached)")]
    assert len(model.asked) == 2
    stop = [e for e in events if e["event"] == "gui_stop"]
    assert len(stop) == 1 and "spend cap $0.05 reached" in stop[0]["reason"] and stop[0]["spent"] == pytest.approx(0.08)
    nodes = {n["id"]: n for n in run_node([{"op": "guirun", "events": events}])[0]}
    assert nodes["b1.c"]["status"] == "skipped" and "spend cap" in nodes["b1.c"]["stop"]
    assert nodes["b1.b"]["cost"] == 0.04


def test_measure_scores_a_model_against_the_known_answers(tmp_path):
    from squad.gui_test_vision import measure
    (tmp_path / "a.png").write_bytes(b"x")
    cases = [{"image": "a.png", "expect": "one", "answer": "compatible"},
             {"image": "a.png", "expect": "two", "answer": "incompatible"}]
    m = measure(Vision(Model("compatible", "compatible", cost=0.02)), cases, tmp_path)
    assert (m["total"], m["correct"], m["cost"]) == (2, 1, pytest.approx(0.04))
    assert [(c["expect"], c["want"], c["got"]) for c in m["cases"]] == [("one", "compatible", "compatible"), ("two", "incompatible", "compatible")]
