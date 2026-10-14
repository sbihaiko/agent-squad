"""No caller outside the binding reaches the board API for state (#236, ADR-0141 clause 4).

The board's state route is the Project v2 endpoint: it is the one route that answers a card's Status and the
one a Status write goes to. It is the binding's (`contrib/squad-goal/scripts/issue_state.py`), and a caller
asks the seam: `State.read`, `State.read_all`, `State.write`.

This guard is a scan of the live scripts and the host package, not a promise: it names the one file that may
hold the route, and it fails the moment another file holds one.
"""
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"

# The board's state route: the Project v2 endpoint a card's Status is read and written at.
ROUTE = "projectsV2"

# The binding: the one file that holds the route.
BINDING = {"issue_state.py"}

# The callers that ask the seam: the reconciler, the hub, the dispatch pass, the janitor, the health check,
# the board command door, and the host-side debt filer.
CALLERS = {
    SCRIPTS / "reconcile.py": "board_state(",
    SCRIPTS / "hub.py": "board_state(",
    SCRIPTS / "childcc.py": "board_state(",
    SCRIPTS / "janitor.py": "board_state(",
    SCRIPTS / "board.py": "board_state(",
    SCRIPTS / "health.sh": "issue_state.py",
    ROOT / "squad" / "debt_filing.py": "board_state(",
}


def reaching(files):
    return {p.name for p in files if ROUTE in p.read_text()}


def live(pattern):
    return (p for p in SCRIPTS.glob(pattern) if not p.name.startswith("test-"))


def test_only_the_binding_holds_the_board_state_route():
    reached = reaching(live("*.py")) | reaching(live("*.sh"))
    assert reached == BINDING, f"a caller outside the binding reaches the board for state: {sorted(reached - BINDING)}"


def test_no_host_module_holds_the_board_state_route():
    assert reaching((ROOT / "squad").rglob("*.py")) == set()


def test_every_named_caller_asks_the_seam():
    for path, ask in CALLERS.items():
        text = path.read_text()
        assert ask in text, f"{path.name} does not ask the seam"
        assert ROUTE not in text, f"{path.name} still holds a board state route of its own"
