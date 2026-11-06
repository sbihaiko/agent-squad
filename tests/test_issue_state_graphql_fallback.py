"""The GraphQL projectItems fallback answers for a card the REST items route omits (#1463): its state, its REST item id, one call."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

OMITTED = 2098
REST_ITEM_ID = 98765
NODE_ID = "PVTI_lADOexample"
FIELDS = [{"name": "Status", "id": "FIELD_STATUS",
           "options": [{"name": "Todo", "id": "OPT_TODO"}, {"name": "Code Complete", "id": "OPT_CC"}]}]


class Stub:
    """A gh stub: the REST items route omits OMITTED, and GraphQL names its card in Todo under REST_ITEM_ID."""

    def __init__(self, graphql_node):
        self.graphql_node = graphql_node
        self.calls = []

    def __call__(self, args, input=None, capture_output=True, text=True, timeout=None):
        joined = " ".join(args)
        self.calls.append((joined, input))
        if "graphql" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"repository": {"issue": {"projectItems": {"nodes": [self.graphql_node]}}}}}), stderr="")
        if "-X" in args and "PATCH" in args:
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        if "/fields" in joined:
            return SimpleNamespace(returncode=0, stdout=json.dumps(FIELDS), stderr="")
        if "--jq" in args and "@json" in args[-1]:   # the items map read: [number, id, repository url]
            return SimpleNamespace(returncode=0, stdout=json.dumps([2001, 1, "https://api.github.com/repos/x/y"]) + "\n", stderr="")
        if "--jq" in args and "@tsv" in args[-1]:   # the status read: number, Status, repository url
            return SimpleNamespace(returncode=0, stdout="2001\tTodo\thttps://api.github.com/repos/x/y", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr=f"unexpected {joined}")

    def graphql_calls(self):
        return [c for c, _ in self.calls if "graphql" in c.split()]

    def patches(self):
        return [c for c, _ in self.calls if "PATCH" in c.split()]


def binding(tmp_path, stub):
    env = {"SG_BOARD_READ_ATTEMPTS": "1", "SG_BOARD_READ_BACKOFF": "0", "SG_BOARD_SETTLE_SECONDS": "0"}
    return issue_state.BoardBinding("o", 4, "x/y", kind="users", state_dir=tmp_path, run=stub, env=env)


def card(item_id=REST_ITEM_ID, status_id="OPT_TODO"):
    return {"id": NODE_ID, "databaseId": item_id, "isArchived": False, "project": {"number": 4},
            "fieldValueByName": {"optionId": status_id}}


def test_the_fallback_names_the_rest_item_id_and_the_todo_state(tmp_path):
    stub = Stub(card())

    ans = binding(tmp_path, stub).read_fallback(OMITTED)

    assert ans == {"id": REST_ITEM_ID, "state": "Todo"}


def test_the_fallback_reads_the_omitted_card_as_todo_for_the_dispatch_path(tmp_path):
    stub = Stub(card())

    assert issue_state.State(binding(tmp_path, stub)).read(OMITTED) == "Todo"


def test_one_graphql_call_per_omitted_issue_however_often_it_is_asked(tmp_path):
    stub = Stub(card())
    b = binding(tmp_path, stub)

    b.read_fallback(OMITTED)
    issue_state.State(b).read(OMITTED)
    b.read_fallback(OMITTED)

    assert len(stub.graphql_calls()) == 1


def test_a_graphql_card_with_no_rest_id_is_never_stored_as_an_id(tmp_path):
    node = {"id": NODE_ID, "isArchived": False, "project": {"number": 4}, "fieldValueByName": {"optionId": "OPT_TODO"}}
    b = binding(tmp_path, Stub(node))

    ans = b.read_fallback(OMITTED)

    assert not (ans or {}).get("id")


def test_the_status_patch_goes_to_the_rest_item_id(tmp_path):
    stub = Stub(card())

    binding(tmp_path, stub).write(OMITTED, "Code Complete")

    assert stub.patches() and f"/projectsV2/4/items/{REST_ITEM_ID} " in stub.patches()[0] + " "
