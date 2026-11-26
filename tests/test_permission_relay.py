"""The permission relay of a full turn (ADR-0180, SPEC 2964).

A permission that a full turn needs reaches the owner's DM as one request that names the literal tool call and its
working folder. An approval allows the call whose hash it names, once. A request with no answer for ten minutes is
denied. Only the owner's id, in the owner's DM of that workspace, approves. A sender holds at most three open requests.
Each request, approval and denial writes one audit line.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = "U0OWNER"
OTHER = "U0STRANGER"
DM = "D0OWNERDM"
OTHER_CHANNEL = "C0PROJECT"
WORKSPACE = "T0WORKSPACE"
OTHER_WORKSPACE = "T0ELSEWHERE"
CWD = "/projects/checkout"
TOOL = "Bash"
INPUT = {"command": "touch notes.txt"}


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "contrib" / "voice" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def relay(tmp_path, clock):
    return load("permrelay").Relay(tmp_path, owner=OWNER, dm_channel=DM, workspace=WORKSPACE, now=clock)


def audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def events(tmp_path, name):
    return [row for row in audit(tmp_path) if row.get("event") == name]


def test_a_request_is_posted_to_the_owner_dm_once(relay, tmp_path):
    relay.request(OWNER, TOOL, INPUT, CWD)
    queued = relay.unsent()
    assert len(queued) == 1
    assert queued[0]["channel"] == DM
    relay.mark_sent(queued[0]["id"])
    assert relay.unsent() == []


def test_a_request_text_shows_the_literal_call_and_its_folder(relay):
    relay.request(OWNER, TOOL, INPUT, CWD)
    text = relay.unsent()[0]["text"]
    assert TOOL in text
    assert json.dumps(INPUT, sort_keys=True) in text
    assert CWD in text


def test_a_hash_names_the_call_and_its_folder(relay):
    first = relay.request(OWNER, TOOL, INPUT, CWD)["hash"]
    other_input = relay.request(OWNER, TOOL, {"command": "rm notes.txt"}, CWD)["hash"]
    other_folder = relay.request(OWNER, TOOL, INPUT, "/projects/elsewhere")["hash"]
    assert len(first) == 64
    assert len({first, other_input, other_folder}) == 3


def test_an_approval_allows_the_call_it_names_once(relay):
    first = relay.request(OWNER, TOOL, INPUT, CWD)
    assert relay.decide(OWNER, DM, WORKSPACE, "approve", first["hash"]) == "approved"
    assert relay.verdict(first["id"]) == "allow"
    again = relay.request(OWNER, TOOL, INPUT, CWD)
    assert relay.verdict(again["id"]) == "pending"


def test_an_approval_does_not_allow_a_call_with_another_hash(relay):
    approved = relay.request(OWNER, TOOL, INPUT, CWD)
    relay.decide(OWNER, DM, WORKSPACE, "approve", approved["hash"])
    other = relay.request(OWNER, TOOL, {"command": "rm -rf ."}, CWD)
    assert relay.verdict(other["id"]) == "pending"


def test_a_request_without_an_answer_is_denied_after_ten_minutes(relay, clock, tmp_path):
    req = relay.request(OWNER, TOOL, INPUT, CWD)
    clock.now += 599
    assert relay.verdict(req["id"]) == "pending"
    clock.now += 2
    assert relay.verdict(req["id"]) == "deny"
    assert relay.verdict(req["id"]) == "deny"
    assert len(events(tmp_path, "timeout")) == 1


def test_only_the_owner_in_the_owner_dm_of_the_workspace_approves(relay, tmp_path):
    req = relay.request(OWNER, TOOL, INPUT, CWD)
    assert relay.decide(OTHER, DM, WORKSPACE, "approve", req["hash"]) == "refused"
    assert relay.decide(OWNER, OTHER_CHANNEL, WORKSPACE, "approve", req["hash"]) == "refused"
    assert relay.decide(OWNER, DM, OTHER_WORKSPACE, "approve", req["hash"]) == "refused"
    assert relay.verdict(req["id"]) == "pending"
    assert events(tmp_path, "approval") == []


def test_a_sender_holds_at_most_three_open_requests(relay):
    opened = [relay.request(OWNER, TOOL, {"command": f"step {n}"}, CWD) for n in range(3)]
    assert all(row["status"] == "pending" for row in opened)
    fourth = relay.request(OWNER, TOOL, {"command": "step 4"}, CWD)
    assert fourth["status"] == "refused"
    assert len(relay.unsent()) == 3
    assert relay.request(OTHER, TOOL, INPUT, CWD)["status"] == "pending"
    relay.decide(OWNER, DM, WORKSPACE, "deny", opened[0]["hash"])
    assert relay.request(OWNER, TOOL, {"command": "step 5"}, CWD)["status"] == "pending"


def test_each_request_approval_denial_and_timeout_writes_one_audit_line(relay, clock, tmp_path):
    approved = relay.request(OWNER, TOOL, {"command": "a"}, CWD)
    assert len(events(tmp_path, "request")) == 1
    relay.decide(OWNER, DM, WORKSPACE, "approve", approved["hash"])
    assert len(events(tmp_path, "approval")) == 1
    denied = relay.request(OWNER, TOOL, {"command": "b"}, CWD)
    relay.decide(OWNER, DM, WORKSPACE, "deny", denied["hash"])
    assert len(events(tmp_path, "denial")) == 1
    silent = relay.request(OWNER, TOOL, {"command": "c"}, CWD)
    clock.now += 601
    relay.verdict(silent["id"])
    assert len(events(tmp_path, "timeout")) == 1
    assert len(events(tmp_path, "request")) == 3


def test_a_full_turn_names_the_permission_prompt_tool(tmp_path):
    fullturn = load("fullturn")
    mcp = tmp_path / "mcp.json"
    command = fullturn.argv("claude", None, relay=(str(mcp), "mcp__squadrelay__approve"))
    assert command[command.index("--permission-prompt-tool") + 1] == "mcp__squadrelay__approve"
    assert command[command.index("--mcp-config") + 1] == str(mcp)


def test_the_prompt_tool_answers_deny_when_no_one_answers(tmp_path):
    relay = load("permrelay").Relay(tmp_path, owner=OWNER, dm_channel=DM, workspace=WORKSPACE, timeout=0)
    call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "approve", "arguments": {"tool_name": TOOL, "input": INPUT}}}
    reply = load("permrelay").handle_rpc(relay, CWD, call, poll=0)
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert reply["id"] == 7
    assert payload["behavior"] == "deny"


def test_the_serve_command_accepts_a_fractional_timeout(tmp_path):
    script = ROOT / "contrib" / "voice" / "permrelay.py"
    done = subprocess.run([sys.executable, str(script), "serve", "--state", str(tmp_path), "--owner", OWNER,
                           "--dm", DM, "--workspace", WORKSPACE, "--folder", CWD, "--timeout", "0.5"],
                          input="", capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_extra_arguments_come_before_the_output_format(tmp_path):
    fullturn = load("fullturn")
    settings = tmp_path / "settings.json"
    command = fullturn.argv("claude", None, extra=("--settings", str(settings)))
    assert command[command.index("--settings") + 1] == str(settings)
    assert command.index("--settings") < command.index("--output-format")
    assert command[command.index("--output-format") + 1] == "json"
