"""GitHub issue #698: every `adapter_error` run verdict is classified by route and by cause.

A run record names its failure in the `outcome` events and folds it into one `verdict` with the reason
`adapter_error`. A cascade (`route`, `main`) carries the same reason with no diagnostic of its own, so the
class is read from the root failure that names one. The route is never guessed here: the model resolver
names it (ADR-0147), so a new spelling is one registry entry, not a second table.

The diagnostic texts below are the ones the real adapter wrote: the session and usage limits as the CLI
prints them, the provider's 402 and 429, and the `issue with the selected model` sentence the router node
died on 116 times.
"""
import json
from types import SimpleNamespace

import pytest

from squad import adapter_classes as AC
from squad.runners.children import Children

SESSION_LIMIT = ("ResultError: Claude Code returned an error result: You've hit your session limit · resets "
                 "7:27am (America/Sao_Paulo) (exit code: 1)")
USAGE_LIMIT = "ResultError: Claude Code returned an error result: API Error: The usage limit has been reached (exit code: 1)"
RATE_LIMIT = 'API Error: 429 {"error": "rate limited upstream"}'
NO_FUNDS = "ResultError: Claude Code returned an error result: API Error: 402 Grok upstream rejected the request (exit code: 1)"
UNAUTHORIZED = "ResultError: Claude Code returned an error result: API Error: 401 Unauthorized (exit code: 1)"
NO_ACCESS = ("ResultError: Claude Code returned an error result: There's an issue with the selected model "
             "(gemini-3.8-flash-high). It may not exist or you may not have access (exit code: 1)")
TIMEOUT = "TimeoutError: the request timed out after 600 s"
CONNECTION_REFUSED = ("ResultError: Claude Code returned an error result: API Error: Connection refused\n"
                      "— a firewall or proxy may be blocking it (ECONNREFUSED) (exit code: 1)")
CONNECTION_RESET = ("ResultError: Claude Code returned an error result: Connection reset by peer "
                    "(ECONNRESET) (exit code: 1)")
TOOL_DENIED = "ResultError: Claude Code returned an error result: the tool call was denied by the permission hook"
PROTOCOL = "ResultError: the response was malformed: invalid JSON frame (exit code: 1)"
UNPLACED = "ProcessError: Command failed with exit code 1 (exit code: 1)\nError output: Check stderr output for details"


def outcome(model=None, diagnostic=None, reasons=("adapter_error",), node="router"):
    ev = {"event": "outcome", "node": node, "entry": 1, "outcome": "fail", "reasons": list(reasons), "artifact": None,
          "spend": 0.0, "turns": 0, "cap": 1.0, "elapsed": 1.0, "pos": 2}
    if model is not None:
        ev["model"] = model
    if diagnostic is not None:
        ev["diagnostic"] = {"error": diagnostic}
    return ev


def spawn(child="route.1.1", model="claude-gpt-5.6-terra", parent="router"):
    """The accepted request that created one dispatch child: it names the model the child was asked to run on."""
    return {"event": "spawn_accepted", "request_key": 1, "child": child, "parent": parent, "depth": 1, "envelope": "route",
            "reservation": 1.0, "parent_entry": 1, "definition": {"model": model, "effort": "medium"}, "pos": 1}


class _Outcomes:
    def __init__(self):
        self.events = []

    def child(self, name, outcome, reasons, artifact, extra, *, parent, depth, entry=1):
        ev = {"event": "outcome", "node": name, "entry": entry, "outcome": outcome, "reasons": list(reasons),
              "artifact": artifact, "parent": parent, "depth": depth,
              **{"spend": None, "turns": None, "cap": None, "elapsed": None, **extra}}
        self.events.append(ev)
        return ev


class _Engine:
    run_id = "run-test"

    def __init__(self, children):
        self.dispatch = SimpleNamespace(children=children, held={})
        self.outcomes = _Outcomes()


def verdict(reasons=("adapter_error",)):
    return {"event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": list(reasons), "remains": [],
            "folded": [2], "pos": 3}


def record(tmp_path, name, events):
    d = tmp_path / name
    d.mkdir()
    (d / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return d


# --- one cause per rule, from the text the provider or the transport wrote ------------------------------------------

@pytest.mark.parametrize("text,cause", [
    (SESSION_LIMIT, "quota"),
    (USAGE_LIMIT, "quota"),
    (RATE_LIMIT, "quota"),
    (NO_FUNDS, "quota"),
    (UNAUTHORIZED, "auth"),
    (NO_ACCESS, "auth"),
    (TIMEOUT, "timeout"),
    (CONNECTION_REFUSED, "protocol"),
    (CONNECTION_RESET, "protocol"),
    (TOOL_DENIED, "tool_refusal"),
    (PROTOCOL, "protocol"),
    (UNPLACED, "unknown"),
    ("", "unknown"),
])
def test_each_cause_is_read_from_the_text(text, cause):
    assert AC.cause(text) == cause


def test_the_cause_names_are_the_closed_set():
    assert AC.CAUSES == ("auth", "quota", "timeout", "tool_refusal", "protocol", "unknown")


# --- the route comes from the one resolver --------------------------------------------------------------------------

def test_a_known_spelling_resolves_to_its_id_and_its_route():
    [c] = AC.classify_run([outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict()])
    assert (c.model, c.backend, c.cause) == ("gpt-5.6-terra", "gateway", "quota")


def test_two_spellings_of_one_model_name_their_own_routes():
    [gateway] = AC.classify_run([outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict()])
    [cli] = AC.classify_run([outcome("gpt-5.6-terra", SESSION_LIMIT), verdict()])
    assert gateway.model == cli.model == "gpt-5.6-terra"
    assert (gateway.backend, cli.backend) == ("gateway", "cli")


def test_a_spelling_the_registry_does_not_know_passes_through_with_no_backend():
    [c] = AC.classify_run([outcome("model-that-does-not-exist", TIMEOUT), verdict()])
    assert (c.model, c.backend, c.cause) == ("model-that-does-not-exist", "unknown", "timeout")


def test_a_record_that_names_no_model_still_counts():
    [c] = AC.classify_run([outcome(None, SESSION_LIMIT), verdict()])
    assert (c.model, c.backend, c.cause) == ("unknown", "unknown", "quota")


# --- a dispatch child's failure names the model it tried -----------------------------------------------------------

def test_a_dispatch_child_adapter_error_records_and_resolves_its_route():
    # A child's failure has no node-level `model`: the model is the chain entry `session.run_chain` handed to the attempt.
    # The outcome must keep it, else a new run's child failure reads as `unknown · unknown`.
    engine = _Engine({"route.1.1": {"parent": "router", "depth": 1}})
    out = SimpleNamespace(kind="adapter_error", error=RuntimeError("API Error: 401 Unauthorized"),
                          res=SimpleNamespace(amount=1.0), elapsed=2.0)
    ev = Children(engine).close(None, "route.1.1", {"effort": "medium"}, "/tmp/ws", out, "claude-gpt-5.6-terra")
    assert ev["model"] == "claude-gpt-5.6-terra"
    [c] = AC.classify_run([ev, verdict()])
    assert (c.model, c.backend, c.cause) == ("gpt-5.6-terra", "gateway", "auth")


def test_an_old_child_failure_resolves_its_model_from_its_spawn_definition():
    # A record written before the child outcome named its model still counted: the accepted request that created the
    # child names the model it was asked to run on.
    events = [spawn(), outcome(None, SESSION_LIMIT, node="route.1.1"), verdict()]
    [c] = AC.classify_run(events)
    assert (c.model, c.backend, c.cause) == ("gpt-5.6-terra", "gateway", "quota")


def test_a_child_model_on_its_own_outcome_wins_over_the_spawn_definition():
    events = [spawn(model="gemini-3.8-flash-high"), outcome("claude-gpt-5.6-terra", SESSION_LIMIT, node="route.1.1"), verdict()]
    [c] = AC.classify_run(events)
    assert (c.model, c.backend) == ("gpt-5.6-terra", "gateway")


# --- one verdict is one class ---------------------------------------------------------------------------------------

def test_a_run_whose_verdict_is_not_an_adapter_error_is_not_counted():
    assert AC.classify_run([outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict(("something_else",))]) == []


def test_a_cascade_carries_the_reason_and_not_the_class():
    events = [outcome("claude-gpt-5.6-terra", SESSION_LIMIT), outcome(None, None), outcome(None, None), verdict()]
    [c] = AC.classify_run(events)
    assert (c.model, c.backend, c.cause) == ("gpt-5.6-terra", "gateway", "quota")


def test_a_record_with_no_outcome_is_unknown_and_still_a_class():
    [c] = AC.classify_run([verdict()])
    assert (c.model, c.backend, c.cause) == ("unknown", "unknown", "unknown")


# --- the scan -------------------------------------------------------------------------------------------------------

def test_a_corrupt_record_counts_its_valid_prefix_and_never_crashes(tmp_path):
    good = record(tmp_path, "run-good", [outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict()])
    broken = tmp_path / "run-broken"
    broken.mkdir()
    text = "".join(json.dumps(e) + "\n" for e in [outcome("gemini-3.8-flash-high", UNAUTHORIZED), verdict()])
    (broken / "record.jsonl").write_text(text + '{"event": "outcome", "trunc\n')
    rows = AC.aggregate([good, broken])
    assert {(r["model"], r["backend"], r["cause"], r["count"]) for r in rows} == {
        ("gpt-5.6-terra", "gateway", "quota", 1),
        ("gemini-3.8-flash", "cli", "auth", 1),
    }


def test_a_record_that_stops_before_its_verdict_counts_nothing(tmp_path):
    partial = tmp_path / "run-partial"
    partial.mkdir()
    (partial / "record.jsonl").write_text(json.dumps(outcome("claude-gpt-5.6-terra", SESSION_LIMIT)) + "\n{\"trunc\n")
    assert AC.aggregate([partial]) == []


def test_the_scan_counts_each_class_and_lists_the_biggest_first(tmp_path):
    a = record(tmp_path, "run-a", [outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict()])
    b = record(tmp_path, "run-b", [outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict()])
    c = record(tmp_path, "run-c", [outcome("gemini-3.8-flash-high", UNAUTHORIZED), verdict()])
    rows = AC.aggregate([a, b, c])
    assert rows[0] == {"model": "gpt-5.6-terra", "backend": "gateway", "cause": "quota", "count": 2}
    assert {"model": "gemini-3.8-flash", "backend": "cli", "cause": "auth", "count": 1} in rows
    assert len(rows) == 2


def test_a_run_folder_with_no_record_adds_nothing(tmp_path):
    empty = tmp_path / "run-empty"
    empty.mkdir()
    assert AC.aggregate([empty, tmp_path / "run-missing"]) == []


def test_the_scan_reads_the_run_folders_that_still_exist(tmp_path):
    record(tmp_path, "run-a", [outcome("claude-gpt-5.6-terra", SESSION_LIMIT), verdict()])
    (tmp_path / "not-a-run").mkdir()
    assert [d.name for d in AC.run_folders(tmp_path)] == ["run-a"]


def test_a_bare_number_is_not_the_api_s_own_status():
    # A number in a sentence about something else is not the API's answer: session.provider_error keeps the same rule, so a
    # frame size or a duration never reads as an authentication or a quota failure.
    assert AC.cause("ResultError: the call produced nothing in 403 s") == "unknown"
    assert AC.cause("ResultError: the frame was 422 bytes") == "unknown"
