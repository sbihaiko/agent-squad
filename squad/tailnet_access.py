"""Decides whether one request may pass a squad server: a loopback request, or a tailnet login on the allow list.

Documented assumption, not yet confirmed by probe #713: a tailnet request has `Host` equal to `tailnet.host`, and its
identity is the `Tailscale-User-Login` header. If the probe disagrees, only this module changes.
Display headers (`Tailscale-User-Name`, the picture) never decide. The decision follows the action the request asks for,
never its HTTP method. The caller keeps its own loopback Origin check for writes.
Standard library only: contrib/squad-goal/scripts/hub.py loads this file by path.
"""
from typing import NamedTuple

LOGIN_HEADER = "Tailscale-User-Login"

VIEW = "view"        # a page or a status read that no terminal grant covers
READ = "read"        # the scrollback of a hosted session
ATTACH = "attach"    # attach, detach, start, stop and end of a hosted session
SEND = "send"        # input to a running session
CONTROL = "control"  # any other write, such as the loop switch or a run's pause
ACTIONS = frozenset({VIEW, READ, ATTACH, SEND, CONTROL})
WRITES = frozenset({ATTACH, SEND, CONTROL})
GRANT_OF = {READ: "terminals_read", ATTACH: "terminals_attach", SEND: "terminals_send"}


class Verdict(NamedTuple):
    allowed: bool
    reason: str


ALLOW = Verdict(True, "")


def decide(*, host, origin, action, login, port, tailnet_host, allowed_logins, grants=frozenset()) -> Verdict:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        if login and action != VIEW:
            return Verdict(False, "forbidden host")
        if origin is not None and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return Verdict(False, "origin not allowed")
        return ALLOW
    if not tailnet_host or host != tailnet_host:
        return Verdict(False, "forbidden host")
    if not login:
        return Verdict(False, "no tailnet identity")
    if not any(login.casefold() == allowed.casefold() for allowed in allowed_logins):
        return Verdict(False, "login not allowed")
    if action in WRITES and origin != f"https://{tailnet_host}":
        return Verdict(False, "origin not allowed")
    grant = GRANT_OF.get(action)
    if grant is not None and grant not in grants:
        return Verdict(False, f"tailnet.{grant} is off")
    return ALLOW
