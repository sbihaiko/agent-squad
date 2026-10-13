import json
from pathlib import Path

from squad.envelope import validate_request
from squad.workflows import DYNAMIC_SEED

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "contrib" / "squad-goal" / "scripts" / "models.json"


def test_squad_goal_coding_chain_names_models_the_dispatch_envelope_can_run():
    """The request builder names coding_models[0]; the dispatch envelope must honor that id."""
    chain = json.loads(MODELS.read_text())["coding_models"]
    graph = json.loads(DYNAMIC_SEED.read_text())
    allowed = {m for n in graph["nodes"] if n["kind"] == "dispatch" for m in n["allow"]["models"]}
    missing = [m for m in chain if m not in allowed]
    assert missing == [], f"coding_models outside the dynamic dispatch model envelope: {missing}"


def test_a_request_that_names_a_coding_model_fits_the_shipped_envelope():
    """The agreement read through the rule's owner, `squad.envelope.validate_request`: every model the coding chain
    names is one a spawn request may carry, so the request the chain builds fits the envelope it runs under. A model the
    envelope refuses is a dispatch that cannot start (ADR-0113), and a membership test of its own is a second copy of
    the rule the validator already holds."""
    doc = json.loads(DYNAMIC_SEED.read_text())
    disp = next(n for n in doc["nodes"] if n["kind"] == "dispatch")
    router = next(n for n in doc["nodes"] if n["name"] == disp["entry"])
    for model in json.loads(MODELS.read_text())["coding_models"]:
        raw = {"prompt": "Move the counter into its own module.", "skill": "tdd", "model": model,
               "tools": list(router["tools"])}
        _, errs = validate_request(disp, raw, 1, router["tools"], disp["budget"]["spend"]["max"])
        assert errs == [], f"the coding model {model} is refused by the shipped envelope: {errs}"
