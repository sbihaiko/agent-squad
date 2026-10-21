"""Decides whether one request may pass a squad server: a loopback request, or a tailnet login on the allow list.

Documented assumption, not yet confirmed by probe #713: a tailnet request has `Host` equal to `tailnet.host`, and its
identity is the `Tailscale-User-Login` header. If the probe disagrees, only this module changes.
Display headers (`Tailscale-User-Name`, the picture) never decide. The caller keeps its own loopback Origin check.
Standard library only: contrib/squad-goal/scripts/hub.py loads this file by path.
"""
from typing import NamedTuple

LOGIN_HEADER = "Tailscale-User-Login"
READ_METHODS = frozenset({"GET", "HEAD"})


class Verdict(NamedTuple):
    allowed: bool
    reason: str


ALLOW = Verdict(True, "")


def decide(*, host, origin, method, login, port, tailnet_host, allowed_logins) -> Verdict:
    if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return ALLOW
    if not tailnet_host or host != tailnet_host:
        return Verdict(False, "forbidden host")
    if not login:
        return Verdict(False, "no tailnet identity")
    if not any(login.casefold() == allowed.casefold() for allowed in allowed_logins):
        return Verdict(False, "login not allowed")
    if method.upper() not in READ_METHODS and origin != f"https://{tailnet_host}":
        return Verdict(False, "origin not allowed")
    return ALLOW
