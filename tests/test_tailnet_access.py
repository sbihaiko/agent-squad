"""The tailnet access seam: one decision per request, with a refusal reason that never names the allow list."""
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


def decide(host, method="GET", origin=None, login=None, allow=ALLOW, port=PORT):
    return ta.decide(host=host, origin=origin, method=method, login=login, port=port, tailnet_host=TAILNET_HOST, allowed_logins=allow)


def test_loopback_host_passes_for_get_and_post_with_no_identity():
    assert decide(f"127.0.0.1:{PORT}").allowed
    assert decide(f"localhost:{PORT}").allowed
    assert decide(f"127.0.0.1:{PORT}", method="POST").allowed


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


def test_listed_login_passes_a_tailnet_read():
    assert decide(TAILNET_HOST, method="GET", login="bo@example.com").allowed
    assert decide(TAILNET_HOST, method="HEAD", login="bo@example.com").allowed


def test_tailnet_write_with_the_right_origin_passes():
    assert decide(TAILNET_HOST, method="POST", origin=TAILNET_ORIGIN, login="ana@example.com").allowed


@pytest.mark.parametrize("origin", [None, "", "https://evil.example", "http://" + TAILNET_HOST, TAILNET_ORIGIN + ":443/x"])
def test_tailnet_write_with_a_wrong_or_absent_origin_is_refused(origin):
    v = decide(TAILNET_HOST, method="POST", origin=origin, login="ana@example.com")
    assert not v.allowed
    assert v.reason == "origin not allowed"


def test_tailnet_write_with_no_origin_is_refused_even_for_a_listed_login():
    assert not decide(TAILNET_HOST, method="PUT", login="ana@example.com").allowed


def test_refusal_text_never_names_a_login_from_the_list():
    cases = [
        decide("evil.example", login="ana@example.com"),
        decide(TAILNET_HOST),
        decide(TAILNET_HOST, login="eve@example.com"),
        decide(TAILNET_HOST, login="ana@example.com", allow=[]),
        decide(TAILNET_HOST, method="POST", login="ana@example.com"),
        decide(TAILNET_HOST, method="POST", origin="https://evil.example", login="bo@example.com"),
    ]
    refusals = [v.reason for v in cases if not v.allowed]
    assert len(refusals) == len(cases)
    for reason in refusals:
        for login in ALLOW:
            assert login not in reason.casefold()
            assert login.split("@")[0] not in reason.casefold()


def test_decision_takes_no_display_header():
    params = set(inspect.signature(ta.decide).parameters)
    assert params == {"host", "origin", "method", "login", "port", "tailnet_host", "allowed_logins"}
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
