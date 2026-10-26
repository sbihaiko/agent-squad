"""#813: a dispatch request asks for output, never for a tool call.

The engine reads a dispatch entry's children from the `spawns` field of its output (`squad/runners/dispatch.py`, the
`output_key` of the shipped router), and the router role holds no spawn tool: its declared tools are Read, Grep and Glob.
A request that demands a spawn call makes the entry fail before a child exists, and the failure names a tool the role
never had.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
REQUESTERS = ("autofrontier.sh", "autobug.sh", "autohunt.sh", "branchfixpass.sh", "childfix.sh")


def test_no_dispatch_request_asks_for_a_spawn_tool():
    for name in REQUESTERS:
        text = (SCRIPTS / name).read_text()
        assert "spawn tool" not in text and "spawn call" not in text, f"{name}: the dispatch entry holds no spawn tool (#813)"
        assert "spawns" in text, f"{name}: the request names the field the engine reads (#813)"


def test_the_shipped_router_names_the_field_and_holds_no_spawn_tool():
    doc = json.loads((ROOT / "docs" / "design" / "router.graph.json").read_text())
    router = next(n for n in doc["nodes"] if n.get("may_spawn"))
    assert router["output_key"] == "spawns"
    assert not {"Task", "Agent"} & set(router["tools"])
