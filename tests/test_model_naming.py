import json
from pathlib import Path

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
