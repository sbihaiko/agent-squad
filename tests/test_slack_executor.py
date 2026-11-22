"""The deterministic executor and the structured request (SPEC 2682, story 65; #2689).

The operator session never acts on Slack by itself. It emits one structured request: the action, the arguments, the
project, the channel, and the id of the source message. The executor checks the request against the policy decision of
`slackpolicy.decide` before it runs the action, so a request outside the role, the project, or the channel is refused
and recorded as data.

Every id here is a placeholder. The decision of each case comes from the real policy module and a real pair of files,
so the expected answer never repeats the executor's own rule.
"""
import copy
import json

from squad import slackexecutor as X
from squad import slackpolicy as S

REPO = "acme/app"
OTHER_REPO = "acme/other"
WORKSPACE = "T0TEAM0001"
CHANNEL = "C0PROJ0001"
OTHER_CHANNEL = "C0ALERT001"
OWNER = "U0OWNER001"
PEER = "U0PEER0001"
STRANGER = "U0STRANGER"
MID = "C0PROJ0001:1762500100.000100"
STATUS_TEXT = "3 open, 1 in review, 12 done"
GUEST_TEAM = "T0CLIENT01"
GUEST_CHANNEL = "C0CLIENT01"
# The project log: one normal pass, one line with a leaked token, two guest lines and one quiet pass.
LOG_TEXT = (
    "2026-11-22T03:00:00Z autobug pass 4: dispatched #2690 (acme/app)\n"
    "2026-11-22T03:01:00Z run refused: the token xoxb-1234567890-abcdefghij is still set\n"
    f"2026-11-22T03:02:00Z guest inbox {GUEST_CHANNEL}: the client asks about the build\n"
    f"2026-11-22T03:03:00Z guest team {GUEST_TEAM} channel {GUEST_CHANNEL} read\n"
    "2026-11-22T03:04:00Z autobug pass 5: none (no work)\n"
)
GUEST_IDS = (GUEST_TEAM, GUEST_CHANNEL)

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "roles": [
        {"id": "manager", "actions": ["status", "logs"]},
        {"id": "colleague", "actions": ["status"]},
    ],
    "workspaces": [
        {"id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
         "channels": [
             {"id": CHANNEL, "name": "proj", "mode": "listen+speak", "persona": "hal",
              "roles": ["manager", "colleague"]},
             {"id": OTHER_CHANNEL, "name": "alerts", "mode": "listen", "roles": ["manager"]},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "members": [
        {"workspace": WORKSPACE, "channel": CHANNEL, "role": "manager", "users": [OWNER]},
        {"workspace": WORKSPACE, "channel": CHANNEL, "role": "colleague", "users": [PEER]},
        {"workspace": WORKSPACE, "channel": OTHER_CHANNEL, "role": "manager", "users": [OWNER]},
    ],
}


def decision(user=OWNER, channel=CHANNEL):
    """One inbox decision of the real policy module for one message of the project channel."""
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    event = {"kind": "message", "workspace": WORKSPACE, "channel": channel, "user": user}
    return S.decide(committed, local, event, now=None)


def line(user=OWNER, channel=CHANNEL, role="manager", text="what is the status?"):
    """One inbox line, as #2687 writes it: the id of the message, its caller, the channel and the decision fields."""
    return {"id": MID, "ts": "1762500100.000100", "user": user, "channel": channel, "language": "en-US",
            "persona": "HAL9K", "role": role, "text": text}


def request_text(**over):
    """The JSON the operator session emits, with every field the executor reads."""
    body = {"action": "status", "arguments": {}, "project": REPO, "channel": CHANNEL, "source": MID}
    body.update(over)
    return json.dumps(body)


class Source:
    """The project status source the executor calls. It records each project it was asked for, so a refused request
    proves it never reached it."""

    def __init__(self, text=STATUS_TEXT):
        self.text = text
        self.calls = []

    def __call__(self, project):
        self.calls.append(project)
        return self.text


class Logs(Source):
    """The project log source the executor calls, with the same record of each project it was asked for."""

    def __init__(self, text=LOG_TEXT):
        super().__init__(text)


def run(text=None, decision_user=OWNER, line_user=OWNER, channel=CHANNEL, source=None, logs=None, guest=(),
        project=REPO):
    """One request through the executor, with its decision, line and status source. The line carries the role the
    decision named, as #2687 writes it, and the request names the channel of the case unless the test overrides it."""
    request, why = X.parse(text if text is not None else request_text(channel=channel))
    assert request is not None, why
    dec = decision(decision_user, channel)
    src = source if source is not None else Source()
    out = X.execute(request, dec, line(line_user, channel, role=dec.role), project, status=src, logs=logs, guest=guest)
    return out, src


def test_a_request_carries_the_action_the_arguments_the_project_the_channel_and_the_source():
    """Criterion (1): the operator session emits one structured request, and it holds the five fields."""
    request, why = X.parse(request_text(action="status", arguments={"scope": "board"}, project=REPO,
                                       channel=CHANNEL, source=MID))
    assert why is None
    assert request == X.Request("status", {"scope": "board"}, REPO, CHANNEL, MID)


def test_the_emitted_text_holds_the_five_fields_as_one_json_object():
    """Criterion (1): the session emits one JSON object, and its keys are exactly the five the executor reads."""
    text = X.emit("status", {}, REPO, CHANNEL, MID)
    assert json.loads(text) == {"action": "status", "arguments": {}, "project": REPO, "channel": CHANNEL,
                                "source": MID}


def test_a_request_missing_a_field_is_refused_and_names_it():
    """Criterion (1): a request the session cannot fill is refused, and the reason names the missing field."""
    request, why = X.parse(json.dumps({"action": "status", "arguments": {}, "project": REPO, "channel": CHANNEL}))
    assert request is None
    assert "source" in why


def test_a_request_that_is_not_one_mapping_is_refused():
    """Criterion (1): a text outside the request shape is refused, never read as an empty request."""
    for text in ("not json", "[]", json.dumps({"action": "status"})):
        request, why = X.parse(text)
        assert request is None and why


def test_a_role_holder_whose_role_lists_status_gets_the_project_status():
    """Criterion (4): a manager asks for `status`, and the answer is the project status of that project."""
    out, src = run()
    assert out.ran is True
    assert src.calls == [REPO]
    assert out.reply == {"path": "bot", "channel": CHANNEL, "text": STATUS_TEXT}


def test_a_colleague_whose_role_lists_status_gets_it_too():
    """Criterion (4): a second role that lists `status` gets the same answer, so the grant is the role's list."""
    out, src = run(decision_user=PEER, line_user=PEER)
    assert out.ran is True
    assert out.reply["text"] == STATUS_TEXT
    assert src.calls == [REPO]


def test_a_request_outside_the_role_is_refused_and_recorded_as_data():
    """Criteria (5) and (6): the colleague's role lists only `status`, so `logs` is refused, and the refusal is data."""
    out, src = run(text=request_text(action="logs"), decision_user=PEER, line_user=PEER)
    assert out.ran is False
    assert out.reply is None
    assert src.calls == []
    assert out.record["error"].startswith("grant:")
    assert out.record["user"] == PEER
    assert out.record["channel"] == CHANNEL
    assert out.record["text"] == "what is the status?"
    assert out.record["role"] == "colleague"


def test_a_request_for_another_project_is_refused():
    """Criterion (5): the executor holds one project, so a request that names another is refused."""
    out, src = run(text=request_text(project=OTHER_REPO))
    assert out.ran is False and src.calls == []
    assert out.record["error"].startswith("project:")


def test_a_request_for_another_channel_is_refused():
    """Criterion (5): the reply goes to the channel of the decision, so a request that names another is refused."""
    out, src = run(text=request_text(channel=OTHER_CHANNEL))
    assert out.ran is False and src.calls == []
    assert out.record["error"].startswith("channel:")


def test_a_request_from_a_message_that_is_not_the_source_line_is_refused():
    """Criterion (2): identity is the source message of the inbox line, so another message id is refused."""
    out, src = run(text=request_text(source="C0PROJ0001:1799999999.000900"))
    assert out.ran is False and src.calls == []
    assert out.record["error"].startswith("identity:")


def test_a_request_from_a_person_with_no_role_is_refused():
    """Criteria (2) and (5): a stranger's message stays data in the policy, so the executor refuses its request."""
    out, src = run(decision_user=STRANGER, line_user=STRANGER)
    assert out.ran is False and src.calls == []
    assert out.record["error"].startswith("identity:")


def test_a_request_with_arguments_the_action_does_not_take_is_refused():
    """Criterion (2): `status` takes no arguments, so any argument is a difference from the decision."""
    out, src = run(text=request_text(arguments={"scope": "board"}))
    assert out.ran is False and src.calls == []
    assert out.record["error"].startswith("arguments:")


def test_a_request_in_a_channel_with_no_reply_path_is_refused():
    """Criterion (7): a `listen` channel receives no post, so its decision names no path, and no action runs there."""
    out, src = run(channel=OTHER_CHANNEL)
    assert out.ran is False and src.calls == []
    assert out.record["error"].startswith("reply:")


def test_the_action_runs_only_after_every_check_passes():
    """Criterion (3): one refused request of each check leaves the source uncalled, and only the valid one reaches it."""
    src = Source()
    for text, kwargs in ((request_text(project=OTHER_REPO), {}),
                         (request_text(channel=OTHER_CHANNEL), {}),
                         (request_text(action="logs"), {"decision_user": PEER, "line_user": PEER}),
                         (request_text(arguments={"scope": "board"}), {}),
                         (request_text(source="C0PROJ0001:1799999999.000900"), {})):
        request, _ = X.parse(text)
        out = X.execute(request, decision(kwargs.get("decision_user", OWNER), kwargs.get("channel", CHANNEL)),
                        line(kwargs.get("line_user", OWNER), kwargs.get("channel", CHANNEL)), REPO, status=src)
        assert out.ran is False
    assert src.calls == []
    out, _ = run(source=src)
    assert out.ran is True
    assert src.calls == [REPO]


def test_every_refusal_is_recorded_as_data_with_the_check_that_refused_it():
    """Criterion (6): each refusal is one data record that names its check and keeps the caller and the text."""
    checks = []
    for text, kwargs in ((request_text(source="C0PROJ0001:1799999999.000900"), {}),
                         (request_text(project=OTHER_REPO), {}),
                         (request_text(channel=OTHER_CHANNEL), {}),
                         (request_text(action="logs"), {"decision_user": PEER, "line_user": PEER}),
                         (request_text(arguments={"scope": "board"}), {})):
        request, _ = X.parse(text)
        out = X.execute(request, decision(kwargs.get("decision_user", OWNER), kwargs.get("channel", CHANNEL)),
                        line(kwargs.get("line_user", OWNER), kwargs.get("channel", CHANNEL)), REPO,
                        status=Source())
        assert out.ran is False
        for field in ("id", "ts", "user", "channel", "language", "persona", "role", "text", "error"):
            assert field in out.record, f"the refusal record lost {field}"
        checks.append(out.record["error"].split(":", 1)[0])
    assert checks == ["identity", "project", "channel", "grant", "arguments"]


def test_a_manager_whose_role_lists_logs_gets_the_logs_of_the_channel_project():
    """Criteria (1) and (5): the manager's role lists `logs`, and the answer is the log of the project the channel owns."""
    logs = Logs()
    out, _ = run(text=request_text(action="logs"), logs=logs)
    assert out.ran is True
    assert logs.calls == [REPO]
    assert out.reply["path"] == "bot"
    assert out.reply["channel"] == CHANNEL
    assert "dispatched #2690" in out.reply["text"]


def test_the_logs_action_asks_no_other_project_for_its_logs():
    """Criterion (1): the source receives the project of the channel and nothing else, called once."""
    logs = Logs()
    out, _ = run(text=request_text(action="logs"), logs=logs)
    assert out.ran is True
    assert logs.calls == [REPO]


def test_a_request_for_the_logs_of_another_project_is_refused():
    """Criterion (2): the executor holds one project, so a request that names another reads no log at all."""
    logs = Logs()
    out, _ = run(text=request_text(action="logs", project=OTHER_REPO), logs=logs)
    assert out.ran is False
    assert logs.calls == []
    assert out.record["error"].startswith("project:")


def test_a_role_that_does_not_list_logs_is_refused():
    """Criterion (5): the colleague's role lists only `status`, so a `logs` request is refused and reads no log."""
    logs = Logs()
    out, _ = run(text=request_text(action="logs"), decision_user=PEER, line_user=PEER, logs=logs)
    assert out.ran is False
    assert logs.calls == []
    assert out.record["error"].startswith("grant:")


def test_the_logs_result_removes_every_secret():
    """Criteria (3) and (6): the fixture log holds a token pattern, and the answer carries a mask in its place."""
    out, _ = run(text=request_text(action="logs"), logs=Logs())
    text = out.reply["text"]
    assert "xoxb-1234567890" not in text
    assert "abcdefghij" not in text
    assert "[redacted]" in text
    assert "dispatched #2690" in text


def test_the_logs_result_removes_all_guest_content():
    """Criterion (4): every line that names a guest team or channel is dropped, and the project lines stay."""
    out, _ = run(text=request_text(action="logs"), logs=Logs(), guest=GUEST_IDS)
    text = out.reply["text"]
    assert GUEST_CHANNEL not in text
    assert GUEST_TEAM not in text
    assert "the client asks about the build" not in text
    assert "dispatched #2690" in text
    assert "autobug pass 5" in text


def test_the_logs_action_refuses_a_request_with_no_log_source():
    """Criterion (1): the executor runs no action without its source, so a missing log source is a refusal, not an empty answer."""
    out, _ = run(text=request_text(action="logs"))
    assert out.ran is False
    assert out.reply is None
    assert out.record["error"].startswith("source:")
    assert "logs" in out.record["error"]
