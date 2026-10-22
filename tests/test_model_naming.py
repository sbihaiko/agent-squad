import json
from pathlib import Path

from squad import modelreg
from squad.envelope import validate_request
from squad.workflows import DYNAMIC_SEED

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "contrib" / "squad-goal" / "scripts" / "models.json"


def _seed():
    """The seed the loop runs, with the chains a launcher reads from models.json (SPEC 750): the envelope names kinds
    and the engine synthesizes each kind's models before it checks a request."""
    doc = json.loads(DYNAMIC_SEED.read_text())
    models = json.loads(MODELS.read_text())
    disp = next(n for n in doc["nodes"] if n["kind"] == "dispatch")
    disp["allow"]["kind_models"] = {k: [e.raw for e in modelreg.chain(models, k).entries]
                                    for k in disp["allow"]["kinds"]}
    return doc, disp, next(n for n in doc["nodes"] if n["name"] == disp["entry"])


def test_squad_goal_coding_chain_names_the_chain_a_coding_child_answers_on():
    """The request builder names coding_models[0]; a coding child takes its chain from that same read."""
    chain = [e.raw for e in modelreg.chain(json.loads(MODELS.read_text()), "coding").entries]
    _, disp, _ = _seed()
    assert disp["allow"]["kind_models"]["coding"] == chain


def test_a_request_that_names_a_coding_kind_fits_the_shipped_envelope():
    """The agreement read through the rule's owner, `squad.envelope.validate_request`: a request that names a kind is
    accepted, and the model it resolves is the first entry of that kind's chain. A request the envelope refuses is a
    dispatch that cannot start (ADR-0113)."""
    _, disp, router = _seed()
    raw = {"prompt": "Move the counter into its own module.", "skill": "tdd", "kind": "coding",
           "tools": list(router["tools"])}
    req, errs = validate_request(disp, raw, 1, router["tools"], disp["budget"]["spend"]["max"])
    assert errs == [], f"a coding request is refused by the shipped envelope: {errs}"
    assert req["model"] == disp["allow"]["kind_models"]["coding"][0]
