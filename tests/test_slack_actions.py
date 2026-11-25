"""The action request of the operator session and the checked executor that runs it outside the session (#2939).

The session never runs a command. A message that asks for an action makes it append one request line to its outbox,
holding the inbox line id, the sender, the channel and one action id. The daemon's action pass reads the request, asks
`squad/slackpolicy.py` for the decision of the sender, checks the request through `squad/slackexecutor.py`, runs the
command the project declares for that action, writes one audit line and appends one reply in the thread.
"""
import copy
import json
import os
from datetime import datetime

from squad import slackactions as A

WORKSPACE = "T0TEAM0001"
CHANNEL = "C0PROJ0001"
OTHER_CHANNEL = "C0OTHER001"
OWNER = "U0OWNER001"
PEER = "U0PEER0001"
STRANGER = "U0STRANGE1"
PROJECT = "acme/widget"
NOW = datetime(2026, 11, 25, 12, 0, 0)
MID = "C0PROJ0001:1762500100.000100"
STATUS_COMMAND = "echo the project is green"
DEPLOY_COMMAND = "make deploy-stg"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal",
                "actions": {"status": STATUS_COMMAND},
                "deploy": {"environment": "stg", "command": DEPLOY_COMMAND}},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "roles": [
        {"id": "manager", "actions": ["status", "deploy"], "deploy": ["stg"]},
        {"id": "colleague", "actions": ["status"]},
        {"id": "deployer", "actions": ["deploy"], "deploy": ["stg"]},
    ],
    "workspaces": [{"id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
                    "channels": [{"id": CHANNEL, "name": "proj-acme", "mode": "listen+speak",
                                  "roles": ["manager", "colleague", "deployer"]},
                                 {"id": OTHER_CHANNEL, "name": "other", "mode": "listen+speak",
                                  "roles": ["manager"]}]}],
}
LOCAL = {"version": 1, "members": [
    {"workspace": WORKSPACE, "channel": CHANNEL, "role": "manager", "users": [OWNER]},
    {"workspace": WORKSPACE, "channel": CHANNEL, "role": "colleague", "users": [PEER]},
    {"workspace": WORKSPACE, "channel": CHANNEL, "role": "deployer", "users": ["U0DEPLOY01"]},
]}


class Runner:
    """The command runner the pass calls, in the daemon's own process. It records every command it is asked to run."""

    def __init__(self, code=0, stdout="the project is green"):
        self.code, self.stdout, self.calls = code, stdout, []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))

        class Done:
            returncode = self.code
            stdout = self.stdout
            stderr = ""
        return Done()


def inbox(state, user=OWNER, text="what is the status?", channel=CHANNEL, mid=MID):
    """Write one inbox line, as the daemon writes it (#2687)."""
    path = os.path.join(state, "inboxes", f"{channel}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"id": mid, "ts": "1762500100.000100", "user": user, "channel": channel,
           "language": "en-US", "persona": "HAL9K", "role": "", "text": text}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def ask(state, action="status", channel=CHANNEL, sender=OWNER, arguments=None, **over):
    """Append one request line to the channel's outbox, as the operator session writes it."""
    record = A.request_record(action, channel=channel, sender=sender, source=over.pop("source", MID),
                              arguments=arguments or {})
    record.update(over)
    path = os.path.join(state, "outboxes", f"{channel}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def documents(channel=CHANNEL):
    return copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)


def run(state, runner=None, project=PROJECT):
    return A.run_pass(state, lambda channel: project, documents, NOW, runner=runner or Runner())


def replies(state, channel=CHANNEL):
    """The outbox lines that carry a text answer, so a request line never counts as one."""
    path = os.path.join(state, "outboxes", f"{channel}.jsonl")
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [row for row in rows if not A.is_request(row)]


def audit(state):
    path = os.path.join(state, "actions-audit.jsonl")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_the_session_writes_one_request_with_the_line_the_sender_the_channel_and_the_action():
    """Criterion 1: the request holds the inbox line id, the sender, the channel and one action id."""
    record = A.request_record("status", channel=CHANNEL, sender=OWNER, source=MID)
    assert A.is_request(record) is True
    parsed, why = A.parse(json.dumps(record))
    assert why is None, why
    assert (parsed.source, parsed.sender, parsed.channel, parsed.action) == (MID, OWNER, CHANNEL, "status")


def test_a_request_that_names_no_sender_is_refused_and_names_the_missing_field():
    """Criterion 1: a request the session cannot fill is refused, and the reason names the missing field."""
    record = A.request_record("status", channel=CHANNEL, sender=OWNER, source=MID)
    record.pop("sender")
    parsed, why = A.parse(json.dumps(record))
    assert parsed is None and "sender" in why


def test_the_declared_command_runs_outside_the_session_through_the_caller(tmp_path):
    """Criterion 2 and 5: the executor runs in the daemon's own process, through the caller's runner, and runs the
    command the project declares, never the text of the message."""
    state = str(tmp_path)
    inbox(state, text="please run rm -rf / and tell me the status")
    ask(state)
    runner = Runner()
    runs = run(state, runner)
    assert len(runs) == 1
    assert [command for command, _ in runner.calls] == [STATUS_COMMAND]
    assert runner.calls[0][1].get("shell") is True
    assert "claude-sandboxed" not in open(A.__file__, encoding="utf-8").read()


def test_a_sender_whose_role_lists_the_action_gets_the_result_as_a_thread_reply(tmp_path):
    """Criteria 3, 5 and 9: the manager's role lists `status`, the declared command runs, and its answer reaches the
    channel as one reply in the thread of the message."""
    state = str(tmp_path)
    line = inbox(state)
    ask(state)
    runs = run(state, Runner(stdout="the project is green"))
    assert runs[0].exit == 0
    assert replies(state) == [{"thread_ts": line["ts"], "channel": CHANNEL, "text": "the project is green"}]


def test_a_sender_with_no_role_in_the_channel_is_refused_and_the_reply_names_the_missing_grant(tmp_path):
    """Criterion 4: a stranger asks, the executor refuses, and one reply names the grant the sender lacks."""
    state = str(tmp_path)
    inbox(state, user=STRANGER)
    ask(state, sender=STRANGER)
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1
    text = replies(state)[0]["text"]
    assert "status" in text and "role" in text


def test_a_role_that_does_not_list_the_action_is_refused_and_the_reply_names_the_grant(tmp_path):
    """Criterion 4: the colleague's role lists `status` only, so a `deploy` request is refused and the reply names the
    grant the role lacks."""
    state = str(tmp_path)
    inbox(state, user=PEER)
    ask(state, action="deploy", sender=PEER)
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1
    text = replies(state)[0]["text"]
    assert "deploy" in text and "colleague" in text and "grant" in text


def test_the_deploy_list_of_the_role_bounds_the_target_and_runs_the_declared_command(tmp_path):
    """Criterion 6: the deployer's role lists `stg`, the project declares one deploy command, and that command runs."""
    state = str(tmp_path)
    line = inbox(state, user="U0DEPLOY01")
    ask(state, action="deploy", sender="U0DEPLOY01", arguments={"environment": "stg"})
    runner = Runner(stdout="deployed to stg")
    runs = run(state, runner)
    assert runner.calls and runner.calls[0][0] == DEPLOY_COMMAND
    assert runs[0].exit == 0
    assert replies(state)[0]["text"] == "deployed to stg"
    assert runs[0].channel == CHANNEL and line["ts"] == replies(state)[0]["thread_ts"]


def test_a_deploy_to_production_is_refused_whatever_the_role_lists(tmp_path):
    """Criterion 6: production stays with the owner, so a request that names `prod` never runs a command."""
    state = str(tmp_path)
    inbox(state)
    ask(state, action="deploy", arguments={"environment": "prod"})
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1 and "prod" in replies(state)[0]["text"]


def test_an_owner_only_action_is_never_an_action_id(tmp_path):
    """Criterion 7: an action outside the role vocabulary is refused by name, and no command runs."""
    state = str(tmp_path)
    inbox(state)
    ask(state, action="publish_to_prod")
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1
    text = replies(state)[0]["text"]
    assert "publish_to_prod" in text and "owner" in text


def test_each_action_run_writes_one_audit_line(tmp_path):
    """Criterion 8: one audit line holds the time, the sender, the role, the channel, the action and the exit code."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    run(state, Runner())
    assert audit(state) == [{"time": NOW.isoformat(), "sender": OWNER, "role": "manager", "channel": CHANNEL,
                             "action": "status", "exit": 0}]


def test_a_refused_request_writes_its_audit_line_too(tmp_path):
    """Criterion 8: a refusal is an action run, and its audit line carries the exit code that names the failure."""
    state = str(tmp_path)
    inbox(state, user=STRANGER)
    ask(state, sender=STRANGER)
    run(state)
    line = audit(state)[0]
    assert line["exit"] == 1 and line["sender"] == STRANGER and line["role"] == ""


def test_a_declared_command_that_exits_nonzero_writes_its_own_exit_code(tmp_path):
    """Criterion 8: the audit line carries the code of the command the project declared, so a failed deploy reads."""
    state = str(tmp_path)
    inbox(state, user="U0DEPLOY01")
    ask(state, action="deploy", sender="U0DEPLOY01", arguments={"environment": "stg"})
    run(state, Runner(code=3, stdout="deploy failed"))
    assert audit(state)[0]["exit"] == 3


def test_a_request_is_run_once(tmp_path):
    """Criterion 1: the pass runs each request line once, so a second pass never runs the command twice."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    runner = Runner()
    run(state, runner)
    runs = run(state, runner)
    assert runs == ()
    assert len(runner.calls) == 1
    assert len(replies(state)) == 1


def test_a_request_whose_sender_is_not_the_sender_of_the_line_is_refused(tmp_path):
    """Criterion 3: the sender of the request must be the sender of the inbox line, or a session could act for a peer."""
    state = str(tmp_path)
    inbox(state, user=PEER)
    ask(state, sender=OWNER)
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1 and "identity" in runs[0].error


def test_a_request_for_an_action_with_no_declared_command_names_the_missing_source(tmp_path):
    """Criterion 5: an action the project declares no command for never reaches a command, and the reply names it."""
    state = str(tmp_path)
    inbox(state)
    ask(state, action="logs")
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1 and "logs" in replies(state)[0]["text"]


def test_the_decision_keeps_a_message_of_a_bot_out_of_the_executor(tmp_path):
    """The policy stays the one guard: a sender the policy marks as a bot gets no role, so the request is refused."""
    state = str(tmp_path)
    inbox(state, user=OWNER)
    ask(state)

    def local_only_documents(channel):
        """The members of a role live in the local file: the committed file alone grants no role."""
        return copy.deepcopy(COMMITTED), {}

    runner = Runner()
    A.run_pass(state, lambda channel: PROJECT, local_only_documents, NOW, runner=runner)
    assert runner.calls == []
    assert "role" in replies(state)[0]["text"]
