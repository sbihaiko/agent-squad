"""The permission relay in `voiced` (ADR-0180, SPEC 2964).

A full turn names the relay's prompt tool. Each request of the turn reaches the owner's DM. Only the owner's reply in
that DM answers a request; the same reply from anyone else stays inbox data.
"""
import json
import os
import time

from tests.test_voiced import OWNER, lines
from tests.test_voiced_active_revision import WORKSPACE
from tests.test_voiced_full_turn import OTHER, deliver, role, setup, stand_in  # noqa: F401 - pytest fixture discovery
from tests.test_voiced_policy import socket_message

DM = "D0OWNERDM"
CALL = {"command": "touch notes.txt"}


def pinned(daemon):
    daemon.state.pin_dm(DM)
    return daemon


def relay_audit(daemon):
    path = os.path.join(daemon.state.directory, "permission", "audit.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines() if line.strip()]


def test_a_request_of_a_full_turn_is_posted_to_the_owner_dm_with_its_call(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
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


def test_a_full_turn_runs_with_the_relay_prompt_tool(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
    _, _, channel, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    deliver(daemon, channel, "check the loop", user=OWNER)
    argv = json.loads(stand_in["record"].read_text().splitlines()[-1])["argv"]
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__squadrelay__approve"
    assert "--mcp-config" in argv


def test_the_owner_dm_reply_approves_the_request_and_stays_out_of_the_inbox(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    text = f"approve {req['hash']}"
    outcome = daemon.handle(socket_message(DM, user=OWNER, text=text, team=WORKSPACE))
    assert outcome == "permission"
    assert relay.verdict(req["id"]) == "allow"


def test_a_reply_from_another_member_approves_nothing_and_stays_data(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    outcome = daemon.handle(socket_message(DM, user=OTHER, text=f"approve {req['hash']}", team=WORKSPACE))
    assert outcome != "permission"
    assert relay.verdict(req["id"]) == "pending"


def test_an_approve_line_that_names_no_open_request_reaches_the_inbox(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    text = "approve " + "a" * 64
    outcome = daemon.handle(socket_message(DM, user=OWNER, text=text, team=WORKSPACE, channel_type="im"))
    assert outcome != "permission", "a line that names no request was swallowed by the relay"
    assert [row["text"] for row in lines(daemon.state.inbox_path)] == [text]


def test_the_owner_dm_answers_a_request_of_a_workspace_that_is_not_the_daemons(tmp_path, stand_in):  # noqa: F811
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    other = "T0ELSEWHERE"
    outcome = daemon.handle(socket_message(DM, user=OWNER, text=f"approve {req['hash']}", team=other,
                                           channel_type="im"))
    assert outcome != "permission"
    assert relay.verdict(req["id"]) == "pending"


def test_the_daemon_posts_three_requests_and_the_relay_refuses_the_fourth(tmp_path, stand_in):  # noqa: F811
    _, client, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    for n in range(3):
        relay.request(OWNER, "Bash", {"command": f"touch f{n}.txt"}, stand_in["checkout"])
    daemon.post_permission_requests(relay)
    posts = [params for method, params, _ in client.calls if method == "chat.postMessage"]
    assert len(posts) == 3, posts
    fourth = relay.request(OWNER, "Bash", {"command": "touch f4.txt"}, stand_in["checkout"])
    assert fourth["status"] == "refused"
    daemon.post_permission_requests(relay)
    assert len([m for m, _, _ in client.calls if m == "chat.postMessage"]) == 3
    rows = relay_audit(daemon)
    assert len([row for row in rows if row["event"] == "request"]) == 3, rows
    refused = [row for row in rows if row["event"] == "refused"]
    assert len(refused) == 1 and refused[0]["hash"] == fourth["hash"], rows


def test_a_request_the_owner_never_answers_times_out_in_the_daemon_state(tmp_path, stand_in):  # noqa: F811
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE, timeout=0.01)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    daemon.post_permission_requests(relay)
    time.sleep(0.02)
    assert relay.verdict(req["id"]) == "deny"
    timed = [row for row in relay_audit(daemon) if row["event"] == "timeout"]
    assert len(timed) == 1 and timed[0]["hash"] == req["hash"] and timed[0]["id"] == req["id"], timed


def test_a_request_with_no_client_is_denied_at_once(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    pinned(daemon)
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    daemon.client = None
    daemon.post_permission_requests(relay)
    assert relay.verdict(req["id"]) == "deny", "a request with no client stayed open until a later turn"
    assert [row for row in relay_audit(daemon) if row["event"] == "undeliverable"], relay_audit(daemon)


def test_a_request_with_no_pinned_dm_is_denied_at_once(tmp_path, stand_in):  # noqa: F811 the stand-in fixture
    _, _, _, daemon = setup(tmp_path, stand_in, role("full"))
    relay = daemon.permission_relay(WORKSPACE)
    req = relay.request(OWNER, "Bash", CALL, stand_in["checkout"])
    daemon.post_permission_requests(relay)
    assert relay.verdict(req["id"]) == "deny", "a request with no DM stayed open until a later turn"
    assert [row for row in relay_audit(daemon) if row["event"] == "undeliverable"], relay_audit(daemon)
