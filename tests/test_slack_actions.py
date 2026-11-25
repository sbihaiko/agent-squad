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

import pytest

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
    """The command runner the pass calls, in the daemon's own process. It records every command it is asked to run, and
    the process that calls it, so a test can tell a run in the caller from a run handed to another process."""

    def __init__(self, code=0, stdout="the project is green"):
        self.code, self.stdout, self.calls, self.pids = code, stdout, [], []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.pids.append(os.getpid())

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
    over.setdefault("at", NOW.timestamp())
    record = A.request_record(action, channel=channel, sender=sender, source=over.pop("source", MID),
                              arguments=arguments or {}, **over)
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
    assert [command for command, _ in runner.calls] == [["echo", "the", "project", "is", "green"]]
    assert runner.calls[0][1].get("shell") is False
    # The command runs through the caller's runner, in the caller's own process: the runner records the process that
    # calls it, so a pass that handed the command to another process would name a different pid.
    assert runner.pids == [os.getpid()]


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
    # The reply names the missing grant (the action) and the role that lacks it, so a reply that names only the role
    # fails here.
    assert "status" in text and "grant" in text and "role" in text


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
    assert runner.calls and runner.calls[0][0] == ["make", "deploy-stg"]
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
                             "action": "status", "exit": 0, "error": ""}]


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


def test_a_crash_after_the_reply_never_runs_the_declared_command_twice(tmp_path):
    """Finding 1: the pass records the handled key before the reply and before the command, so a pass that dies after it
    wrote the reply leaves the key recorded and a later pass never runs the declared command a second time."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    runner = Runner()
    real = A._append_line

    def crash_on_the_reply(path, record):
        real(path, record)
        if "thread_ts" in record:       # the reply line: the old order wrote it before the handled key
            raise RuntimeError("the pass died after the reply")

    A._append_line = crash_on_the_reply
    try:
        with pytest.raises(RuntimeError):
            run(state, runner)
    finally:
        A._append_line = real
    assert len(replies(state)) == 1
    run(state, runner)
    assert len(runner.calls) == 1


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


def test_a_request_in_the_outbox_of_a_channel_no_project_owns_never_runs(tmp_path):
    """The pass reads only the outbox of a channel a project owns, so a request line in any other outbox never runs."""
    state = str(tmp_path)
    inbox(state, channel=OTHER_CHANNEL)
    ask(state, channel=OTHER_CHANNEL)
    runner = Runner()
    runs = A.run_pass(state, lambda channel: PROJECT if channel == CHANNEL else "", documents, NOW, runner=runner)
    assert runner.calls == []
    assert runs == ()
    assert "owned by no project" in audit(state)[0]["error"]


def test_a_request_in_the_dm_outbox_never_runs(tmp_path):
    """The DM outbox carries the owner's answers and proposals, never an action request, so a request line there never
    runs even when the DM channel is named."""
    state = str(tmp_path)
    inbox(state, channel="dm")
    ask(state, channel="dm")
    runner = Runner()
    runs = A.run_pass(state, lambda channel: PROJECT, documents, NOW, runner=runner)
    assert runner.calls == []
    assert runs == ()
    assert "DM outbox" in audit(state)[0]["error"]


def test_a_request_older_than_the_window_never_runs(tmp_path):
    """A request line older than the small window is stale: a line a crashed pass left behind never runs on a later
    pass, so an old request cannot act on a channel long after its message."""
    state = str(tmp_path)
    inbox(state)
    ask(state, at=NOW.timestamp() - A.REQUEST_WINDOW - 1)
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs == ()
    assert "older" in audit(state)[0]["error"]


def test_a_request_with_no_timestamp_is_stale(tmp_path):
    """A request line with no `at` is stale, because the pass cannot tell its age, and it never runs."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    path = os.path.join(state, "outboxes", f"{CHANNEL}.jsonl")
    with open(path, encoding="utf-8") as f:
        record = json.loads(f.readline())
    record.pop("at")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    runner = Runner()
    assert run(state, runner) == ()
    assert runner.calls == []
    assert "older" in audit(state)[0]["error"]


def test_a_request_for_a_project_with_no_active_revision_gets_no_reply(tmp_path):
    """A request whose channel names no project has no session writing its outbox, so its refusal is silent: the reply
    is None and the audit is the only record."""
    state = str(tmp_path)
    inbox(state)
    record = ask(state)
    outbox_for = lambda channel: os.path.join(state, "outboxes", f"{channel}.jsonl")  # noqa: E731
    run = A._run_one(record, state, lambda channel: "", documents, NOW, Runner(), outbox_for)
    assert run.exit == 1 and run.reply is None
    assert replies(state) == []


def test_a_run_with_no_source_is_not_a_success():
    """A run that reports a success with no source is a wiring bug, so the audit never reads it as a success."""
    from squad import slackexecutor as X

    assert A._exit_code(X.Outcome(True, {"text": "ok"}, None), None) == 1
    assert A._exit_code(X.Outcome(False, None, {}), None) == 1


def test_an_answer_line_that_carries_every_request_field_is_not_a_request():
    """The reserved `kind` field is the sole discriminator, so an answer line that happens to carry the five request
    fields is still an answer and the drain still posts it."""
    answer = {"action": "note", "arguments": {}, "sender": OWNER, "channel": CHANNEL, "source": MID,
              "thread_ts": "1762500100.000100", "text": "one answer, not one request"}
    assert A.is_request(answer) is False
    assert A.is_request(A.request_record("status", CHANNEL, OWNER, MID)) is True


def test_the_pass_reads_only_the_inbox_of_the_request_channel(tmp_path):
    """The inbox line is read from the request's own channel inbox, so an id that lives in another channel's inbox never
    matches and the request is refused at the identity."""
    state = str(tmp_path)
    inbox(state, channel=OTHER_CHANNEL)
    ask(state, channel=CHANNEL)
    runner = Runner()
    runs = A.run_pass(state, lambda channel: PROJECT, documents, NOW, runner=runner)
    assert runner.calls == []
    assert runs[0].exit == 1 and "identity" in runs[0].error


def test_the_handled_ledger_appends_one_line_per_key(tmp_path):
    """The ledger appends one JSON line per handled key, so a pass never rewrites its whole list for one request."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    run(state, Runner())
    path = os.path.join(state, A.HANDLED)
    lines = [line for line in open(path, encoding="utf-8").read().splitlines() if line.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["key"]
    run(state, Runner())
    lines = [line for line in open(path, encoding="utf-8").read().splitlines() if line.strip()]
    assert len(lines) == 1


def test_a_declared_command_of_a_request_action_runs(tmp_path):
    """The sources build a command source for every action id the project declares, so a declared `request_work`
    command runs instead of the executor refusing the action for a missing source."""
    state = str(tmp_path)
    inbox(state, text="please start the work")
    committed = copy.deepcopy(COMMITTED)
    committed["project"]["actions"]["request_work"] = "echo done"
    committed["roles"][0]["actions"] = ["status", "request_work"]
    ask(state, action="request_work", arguments={"title": "fix the thing"})
    runner = Runner(stdout="done")
    runs = A.run_pass(state, lambda channel: PROJECT, lambda channel: (committed, copy.deepcopy(LOCAL)), NOW,
                      runner=runner)
    assert [command for command, _ in runner.calls] == [["echo", "done"]]
    assert runs[0].exit == 0
    assert replies(state)[0]["text"] == "done"


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


def test_a_second_pass_reads_only_the_new_bytes_of_each_outbox(tmp_path):
    """Finding 2: the pass keeps its place in each outbox beside the ledger, so a pass reads only the bytes appended
    since the last one and a request that arrives later still runs."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    runner = Runner()
    run(state, runner)
    path = os.path.join(state, A.OFFSETS)
    assert os.path.exists(path), "the pass kept no place in the outbox"
    second = "C0PROJ0001:1762500200.000200"
    inbox(state, text="and again?", mid=second)
    ask(state, source=second)
    runs = run(state, runner)
    assert len(runs) == 1 and len(runner.calls) == 2


def test_an_outbox_shorter_than_its_kept_place_is_read_from_the_start(tmp_path):
    """Finding 2: an outbox a rotation shrank below its kept place is read from the start, never from a byte past its
    end."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    path = os.path.join(state, "outboxes", f"{CHANNEL}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write("x" * 5000 + "\n")          # a long line the pass skips, so the kept place sits past any request
    run(state, Runner())
    fresh = "C0PROJ0001:1762500300.000300"
    line = inbox(state, text="start over", mid=fresh)
    request = A.request_record("status", channel=CHANNEL, sender=OWNER, source=fresh, at=NOW.timestamp())
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(request) + "\n")
    assert line["id"] == fresh and os.path.getsize(path) < 400
    runner = Runner()
    runs = run(state, runner)
    assert len(runs) == 1 and len(runner.calls) == 1


def test_the_reply_of_an_action_that_never_runs_through_project_actions_names_it(tmp_path):
    """Finding 3: `approve_and_move` and `loop_control` keep their own executor path, so a request for one refuses and
    the reply states that no `project.actions` command stands in for the source it needs."""
    state = str(tmp_path)
    inbox(state, text="move the card")
    committed = copy.deepcopy(COMMITTED)
    committed["roles"][0]["actions"] = ["approve_and_move"]
    ask(state, action="approve_and_move", arguments={"card": "1", "to": "Doing"})
    runner = Runner()
    runs = A.run_pass(state, lambda channel: PROJECT, lambda channel: (committed, copy.deepcopy(LOCAL)), NOW,
                      runner=runner)
    assert runner.calls == []
    assert runs[0].exit == 1 and "project.actions" in replies(state)[0]["text"]


def test_an_unaddressed_request_in_an_addressed_only_channel_runs_no_command(tmp_path):
    """Finding 6: the grant the executor checks is the decision the policy computed from the inbox line, so a channel
    that answers only an addressed message runs no action for a message that addresses no persona."""
    state = str(tmp_path)
    inbox(state, text="what is the status?")
    ask(state)
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"][0]["channels"][0]["reply"] = "addressed"
    runner = Runner()
    runs = A.run_pass(state, lambda channel: PROJECT, lambda channel: (committed, copy.deepcopy(LOCAL)), NOW,
                      runner=runner)
    assert runner.calls == []
    assert runs[0].exit == 1 and runs[0].error.startswith("reply:")
    assert audit(state)[0]["error"].startswith("reply:")


def test_every_audit_line_carries_an_error_field_that_names_why_it_exited(tmp_path):
    """Finding 9: the audit alone says why a run exited nonzero, so every line carries an `error`: empty on success, the
    executor's refusal, the policy's reason, or the code of a declared command that failed."""
    state = str(tmp_path)
    inbox(state, text="what is the status?")
    ask(state)
    run(state, Runner(code=3, stdout="deploy failed"))
    row = audit(state)[0]
    assert "error" in row and "3" in row["error"]

    other = str(tmp_path / "refused")
    os.makedirs(other)
    inbox(other, user=STRANGER)
    ask(other, sender=STRANGER)
    run(other)
    assert audit(other)[0]["error"].startswith("identity:")

    stale = str(tmp_path / "stale")
    os.makedirs(stale)
    inbox(stale)
    ask(stale, at=NOW.timestamp() - A.REQUEST_WINDOW - 1)
    run(stale)
    assert "older" in audit(stale)[0]["error"]

    green = str(tmp_path / "green")
    os.makedirs(green)
    inbox(green)
    ask(green)
    run(green)
    assert audit(green)[0]["error"] == ""



def test_a_second_request_line_with_the_same_source_and_action_runs(tmp_path):
    """Finding 10 (BLOCKING 2): the handled key names the whole request line, so a second request with the same source
    and action but different arguments is a different request and runs, with its own audit line."""
    state = str(tmp_path)
    inbox(state, text="please start the work")
    committed = copy.deepcopy(COMMITTED)
    committed["project"]["actions"]["request_work"] = "echo done"
    committed["roles"][0]["actions"] = ["status", "request_work"]
    documents_here = lambda channel: (committed, copy.deepcopy(LOCAL))  # noqa: E731
    ask(state, action="request_work", arguments={"title": "first"})
    ask(state, action="request_work", arguments={"title": "second"})
    runner = Runner(stdout="done")
    runs = A.run_pass(state, lambda channel: PROJECT, documents_here, NOW, runner=runner)
    assert len(runs) == 2, runs
    assert len(runner.calls) == 2, runner.calls
    assert len(audit(state)) == 2, audit(state)


def test_the_handled_key_names_the_whole_request_line(tmp_path):
    """Finding 10 (BLOCKING 2): the key holds the arguments and the sender, so two requests that share the source and the
    action never collapse into one handled key."""
    state = str(tmp_path)
    inbox(state, text="please start the work")
    committed = copy.deepcopy(COMMITTED)
    committed["project"]["actions"]["request_work"] = "echo done"
    committed["roles"][0]["actions"] = ["status", "request_work"]
    documents_here = lambda channel: (committed, copy.deepcopy(LOCAL))  # noqa: E731
    ask(state, action="request_work", arguments={"title": "first"})
    ask(state, action="request_work", arguments={"title": "second"})
    A.run_pass(state, lambda channel: PROJECT, documents_here, NOW, runner=Runner(stdout="done"))
    path = os.path.join(state, A.HANDLED)
    with open(path, encoding="utf-8") as f:
        keys = [json.loads(line)["key"] for line in f if line.strip()]
    assert len(keys) == 2 and keys[0] != keys[1], keys


def test_a_deploy_request_from_a_status_only_role_is_refused_by_the_action_grant(tmp_path):
    """Finding 11 (BLOCKING 3 / DEBT 4): the inbox line asks for a deploy and the request asks for a deploy, and the
    colleague's role lists `status` only. The refusal comes from the grant's action list, never from the decision's
    kind alone."""
    state = str(tmp_path)
    inbox(state, user=PEER, text="please deploy to stg")
    ask(state, action="deploy", sender=PEER, arguments={"environment": "stg"})
    runner = Runner()
    runs = run(state, runner)
    assert runner.calls == []
    assert runs[0].exit == 1
    assert runs[0].error.startswith("grant:"), runs[0].error
    assert "deploy" in runs[0].error and "colleague" in runs[0].error


def test_the_declared_command_runs_as_one_argv_without_a_shell(tmp_path):
    """Finding 12 (DEBT 1): the declared command is one argv, never a shell line, so no metacharacter of a declared
    value reaches a shell."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    runner = Runner()
    run(state, runner)
    assert runner.calls[0][0] == ["echo", "the", "project", "is", "green"], runner.calls[0][0]
    assert runner.calls[0][1].get("shell") is False


def test_a_declared_command_that_names_a_shell_metacharacter_never_runs(tmp_path):
    """Finding 12 (DEBT 1): a declared command that carries `;`, `|`, `$(` or a backtick is refused, so a project value
    can never chain a second command."""
    state = str(tmp_path)
    inbox(state)
    ask(state)
    committed = copy.deepcopy(COMMITTED)
    committed["project"]["actions"]["status"] = "echo green; rm -rf /"
    runner = Runner()
    runs = A.run_pass(state, lambda channel: PROJECT, lambda channel: (committed, copy.deepcopy(LOCAL)), NOW,
                      runner=runner)
    assert runner.calls == [], runner.calls
    assert runs[0].exit == 1


def test_a_crash_between_the_key_and_the_refusal_audit_writes_no_second_row(tmp_path):
    """Finding 13 (DEBT 2): the handled key is written before the refusal's audit line, so a crash between the two
    writes never repeats the audit row on the next pass."""
    state = str(tmp_path)
    inbox(state)
    ask(state, at=NOW.timestamp() - A.REQUEST_WINDOW - 1)      # a stale line: the pass's own refusal branch
    run(state)
    assert len(audit(state)) == 1
    second = "C0PROJ0001:1762500400.000400"
    inbox(state, mid=second)
    ask(state, source=second, at=NOW.timestamp() - A.REQUEST_WINDOW - 1)
    real = A._append_line

    def crash_on_audit(path, record):
        if path.endswith(A.AUDIT):
            raise RuntimeError("the pass died before the audit line")
        real(path, record)

    A._append_line = crash_on_audit
    try:
        with pytest.raises(RuntimeError):
            run(state)
    finally:
        A._append_line = real
    run(state)
    assert len(audit(state)) == 1, audit(state)


def test_a_first_pass_reads_only_the_tail_of_a_large_outbox(tmp_path):
    """Finding 14 (DEBT 6): the first pass of a state with no kept place reads a bounded tail of each outbox, so a long
    answer history costs one bounded read and a request at the very start never runs."""
    state = str(tmp_path)
    path = os.path.join(state, "outboxes", f"{CHANNEL}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    early = A.request_record("status", channel=CHANNEL, sender=OWNER,
                             source="C0PROJ0001:1762500500.000500", at=NOW.timestamp())
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(early) + "\n")
        f.write(("x" * 2000 + "\n") * 800)          # an answer history past the bounded first read
    inbox(state, mid="C0PROJ0001:1762500600.000600")
    ask(state, source="C0PROJ0001:1762500600.000600")
    runner = Runner()
    runs = run(state, runner)
    assert [r.action for r in runs] == ["status"], runs
    assert len(runner.calls) == 1
