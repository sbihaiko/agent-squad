"""The tailnet access seam: one decision per request, made from the action it asks for, with a refusal reason that never names the allow list.

The hub hosts no terminal (#2492), so the seam holds no `terminals_*` grant and no terminal action; a kept route keeps its
own rule (a send, the loop switch).
"""
import ast
import inspect
import pathlib
import sys

import pytest

from squad import tailnet_access as ta

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_PY = ROOT / "squad/tailnet_access.py"

PORT = 7700
TAILNET_HOST = "hub.tail1234.ts.net"
ALLOW = ["ana@example.com", "bo@example.com"]
TAILNET_ORIGIN = f"https://{TAILNET_HOST}"
LOOPBACK = f"127.0.0.1:{PORT}"


def decide(host, action=ta.VIEW, origin=None, login=None, allow=ALLOW, port=PORT):
    return ta.decide(host=host, origin=origin, action=action, login=login, port=port, tailnet_host=TAILNET_HOST,
                     allowed_logins=allow)


def test_loopback_host_passes_a_view_with_no_identity():
    assert decide(LOOPBACK).allowed
    assert decide(f"localhost:{PORT}").allowed


def test_loopback_write_needs_no_grant_and_no_identity():
    assert decide(LOOPBACK, action=ta.CONTROL).allowed
    assert decide(LOOPBACK, action=ta.SEND).allowed


def test_loopback_request_with_a_foreign_origin_is_refused():
    for action in (ta.VIEW, ta.CONTROL, ta.SEND):
        v = decide(LOOPBACK, action=action, origin="https://evil.example")
        assert not v.allowed
        assert v.reason == "origin not allowed"


def test_loopback_request_with_its_own_origin_passes():
    assert decide(LOOPBACK, action=ta.CONTROL, origin=f"http://127.0.0.1:{PORT}").allowed
    assert decide(LOOPBACK, action=ta.CONTROL, origin=f"http://localhost:{PORT}").allowed


def test_loopback_on_another_port_is_a_forbidden_host():
    v = decide(f"127.0.0.1:{PORT + 1}")
    assert not v.allowed
    assert v.reason == "forbidden host"


def test_unknown_host_is_a_forbidden_host():
    v = decide("evil.example:7700", login="ana@example.com")
    assert not v.allowed
    assert v.reason == "forbidden host"


def test_tailnet_request_with_no_identity_is_refused_with_its_reason():
    v = decide(TAILNET_HOST)
    assert not v.allowed
    assert v.reason == "no tailnet identity"


def test_tailnet_request_with_an_unknown_login_is_refused_with_its_reason():
    v = decide(TAILNET_HOST, login="eve@example.com")
    assert not v.allowed
    assert v.reason == "login not allowed"


def test_tailnet_request_with_an_empty_list_refuses_every_login():
    v = decide(TAILNET_HOST, login="ana@example.com", allow=[])
    assert not v.allowed
    assert v.reason == "login not allowed"


def test_listed_login_passes_in_another_letter_case():
    assert decide(TAILNET_HOST, login="Ana@Example.COM").allowed


def test_login_match_is_exact_not_a_substring():
    v = decide(TAILNET_HOST, login="ana@example.co")
    assert not v.allowed
    assert v.reason == "login not allowed"


def test_listed_login_passes_a_tailnet_view():
    assert decide(TAILNET_HOST, login="bo@example.com").allowed


def test_the_seam_holds_no_terminal_action_and_no_terminal_grant():
    assert not hasattr(ta, "READ") and not hasattr(ta, "ATTACH") and not hasattr(ta, "GRANT_OF")
    assert ta.ACTIONS == frozenset({ta.VIEW, ta.SEND, ta.CONTROL})
    with pytest.raises(ValueError, match="unknown action"):
        decide(TAILNET_HOST, action="read", login="ana@example.com")


def test_tailnet_send_with_the_right_origin_passes_for_a_listed_login():
    """The seam holds no send grant; the send route keeps its own tailnet rule (hub.py send_rule)."""
    assert decide(TAILNET_HOST, action=ta.SEND, origin=TAILNET_ORIGIN, login="ana@example.com").allowed


@pytest.mark.parametrize("origin", [None, "", "https://evil.example", "http://" + TAILNET_HOST, TAILNET_ORIGIN + ":443/x"])
def test_tailnet_send_with_a_wrong_or_absent_origin_is_refused(origin):
    v = decide(TAILNET_HOST, action=ta.SEND, origin=origin, login="ana@example.com")
    assert not v.allowed
    assert v.reason == "origin not allowed"


@pytest.mark.parametrize("origin", [None, "", "https://evil.example", "http://" + TAILNET_HOST, TAILNET_ORIGIN + ":443/x"])
def test_tailnet_control_with_a_wrong_or_absent_origin_is_refused(origin):
    v = decide(TAILNET_HOST, action=ta.CONTROL, origin=origin, login="ana@example.com")
    assert not v.allowed
    assert v.reason == "origin not allowed"


def test_tailnet_control_with_the_right_origin_passes_without_a_grant():
    assert decide(TAILNET_HOST, action=ta.CONTROL, origin=TAILNET_ORIGIN, login="ana@example.com").allowed


def test_the_decision_follows_the_action_not_the_method():
    assert decide(TAILNET_HOST, action=ta.SEND, origin=TAILNET_ORIGIN, login="ana@example.com").allowed
    v = decide(TAILNET_HOST, action=ta.SEND, login="ana@example.com")
    assert not v.allowed
    assert v.reason == "origin not allowed"


def test_an_unknown_action_is_an_error():
    with pytest.raises(ValueError, match="unknown action"):
        decide(TAILNET_HOST, action="PUT", login="ana@example.com")


def test_refusal_text_never_names_a_login_from_the_list():
    cases = [
        decide("evil.example", login="ana@example.com"),
        decide(TAILNET_HOST),
        decide(TAILNET_HOST, login="eve@example.com"),
        decide(TAILNET_HOST, login="ana@example.com", allow=[]),
        decide(TAILNET_HOST, action=ta.CONTROL, login="ana@example.com"),
        decide(TAILNET_HOST, action=ta.CONTROL, origin="https://evil.example", login="bo@example.com"),
        decide(TAILNET_HOST, action=ta.SEND, login="bo@example.com"),
    ]
    refusals = [v.reason for v in cases if not v.allowed]
    assert len(refusals) == len(cases)
    for reason in refusals:
        for login in ALLOW:
            assert login not in reason.casefold()
            assert login.split("@")[0] not in reason.casefold()


def test_decision_takes_no_method_and_no_display_header():
    params = set(inspect.signature(ta.decide).parameters)
    assert params == {"host", "origin", "action", "login", "port", "tailnet_host", "allowed_logins"}
    assert ta.LOGIN_HEADER == "Tailscale-User-Login"


def test_module_imports_only_the_standard_library_and_no_squad_module():
    tree = ast.parse(MODULE_PY.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import would reach another squad module"
            modules.append(node.module or "")
    assert modules, "the module is expected to import at least one standard module"
    for name in modules:
        top = name.split(".")[0]
        assert top in sys.stdlib_module_names, f"{name} is not standard library"
        assert top != "squad"
