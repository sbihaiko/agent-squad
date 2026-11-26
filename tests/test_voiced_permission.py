"""The permission relay in `voiced` (ADR-0180, SPEC 2964).

A full turn names the relay's prompt tool. Each request of the turn reaches the owner's DM. Only the owner's reply in
that DM answers a request; the same reply from anyone else stays inbox data.
"""
import json

from tests.test_voiced import OWNER, voiced
from tests.test_voiced_active_revision import WORKSPACE
from tests.test_voiced_full_turn import deliver, role, setup, stand_in  # noqa: F401  the stand-in CLI fixture
from tests.test_voiced_full_turn import OTHER
from tests.test_voiced_policy import socket_message

DM = "D0OWNERDM"
CALL = {"command": "touch notes.txt"}


def pinned(daemon):
    daemon.state.pin_dm(DM)
    return daemon


def test_a_request_of_a_full_turn_is_posted_to_the_owner_dm_with_its_call(tmp_path, stand_in):
    _, client, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    daemon.post_permission_requests(relay)
    posts = [params for method, params, _ in client.calls if method == "chat.postMessage"]
    assert len(posts) == 1
    assert posts[0]["channel"] == DM
    assert "Bash" in posts[0]["text"]
    assert json.dumps(CALL, sort_keys=True) in posts[0]["text"]
    assert stand_in["checkout"] in posts[0]["text"]
    daemon.post_permission_requests(relay)
    assert len([m for m, _, _ in client.calls if m == "chat.postMessage"]) == 1


def test_a_full_turn_runs_with_the_relay_prompt_tool(tmp_path, stand_in):
    _, _, channel, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    deliver(daemon, channel, "check the loop", user=OWNER)
    argv = json.loads(stand_in["record"].read_text().splitlines()[-1])["argv"]
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__squadrelay__approve"
    assert "--mcp-config" in argv


def test_the_owner_dm_reply_approves_the_request_and_stays_out_of_the_inbox(tmp_path, stand_in):
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    text = f"approve {req['hash']}"
    outcome = daemon.handle(socket_message(DM, user=OWNER, text=text, team=WORKSPACE))
    assert outcome == "permission"
    assert relay.verdict(req["id"]) == "allow"


def test_a_reply_from_another_member_approves_nothing_and_stays_data(tmp_path, stand_in):
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    outcome = daemon.handle(socket_message(DM, user=OTHER, text=f"approve {req['hash']}", team=WORKSPACE))
    assert outcome != "permission"
    assert relay.verdict(req["id"]) == "pending"
