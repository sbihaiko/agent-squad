#!/usr/bin/env python3
"""One interface owns every GitHub read and write the loop makes (#1893, ADR-0277).

`gh api` writes the error body to *stdout* and exits non-zero, and it does not honour `-q` on a failed request, so a
bare `opr=$(gh api ...)` hands the words of a rate-limit message back where a JSON list was expected. On 2026-10-12
that body became the open-PR list and the cycle read each word of the JSON message as a PR number.

This module is that one reader. It answers in exactly three distinguishable outcomes and never mixes them:

* `answer`  -- GitHub reached a verdict, and it is here in `Read.answer` / `Read.body`.
* `refused` -- GitHub refused the read. `Read.message` is GitHub's own message, `Read.request_id` its request id.
               A refusal is never an answer and never an empty list: `Read.answer` is always `None` for it, so a caller
               that only ever replaces its knowledge from an `answer` keeps the set it already had.
* `failed`  -- the command failed before GitHub reached a verdict; `Read.detail` says what happened locally, and
               `Read.message` is empty because GitHub said nothing.

The same three outcomes own the writes (`write(route, method, fields, transport=...)`, revision 2026-10-12 of #3). On
that afternoon the pass moved 16 board cards and closed 16 issues, and a `403` on any of those writes was a silent
no-op: `gh api` exited non-zero, the loop printed nothing, and the card was counted as moved. A write answers `done`
(GitHub reached a verdict and took it), `refused` (carrying GitHub's message and request id) or `failed` (no verdict),
and only `done` is a write that happened -- `Write.is_done` is the only thing a caller may count.

The transport is injectable (`read(route, transport=...)`, `write(..., transport=...)`), so a test drives this module
with a stub and needs no network. A write hands its transport the argv `gh api` would have run -- the method, the route
and the fields -- because a stub that only saw the route could not tell a move from a read. The same contract is
reachable as a process, which is how a shell script reaches it:

    python3 ghread.py <route>                              # a read
    python3 ghread.py --method POST <route> [-f k=v ...]    # a write (--method/-X and -f/--raw-field)

The exit code is the contract a script reads, because a script cannot see a Python return value:

    0  the answer (or the accepted write's body), verbatim on stdout for the caller's `jq`
    2  a refusal; stdout is the one-line refusal naming GitHub's message and request id, and it is not JSON
    1  a failure before GitHub reached a verdict

Scope (ADR-0277): this is the outcome contract and nothing else. The token bucket, the shared `$SG_STATE` cooldown, the
per-resource response cache and the api.log/health reporting are separate clauses of that decision, each with its own
ticket; adding them here would put a rate governor behind an interface whose whole job is to keep three outcomes apart.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

ANSWER = "answer"
DONE = "done"
REFUSED = "refused"
FAILED = "failed"

# GitHub names its request id inside the message body when it refuses ("... include the request ID <id> ...") and in the
# X-GitHub-Request-Id response header. Both are GitHub's own words; the caller's log must carry one of them.
_REQUEST_ID_IN_MESSAGE = re.compile(r"request ID ([0-9A-Fa-f:]{6,})")
_USAGE = 64


@dataclass(frozen=True)
class Transport:
    """What a transport hands back: the exit code, everything written to stdout, and what it said locally."""

    rc: int
    body: str = ""
    detail: str = ""


@dataclass(frozen=True)
class Read:
    """One read's outcome. Exactly one of the three kinds, and the fields that belong to it."""

    kind: str
    answer: Any = None
    body: str = ""
    message: str = ""
    request_id: str = ""
    detail: str = ""

    @property
    def is_answer(self) -> bool:
        return self.kind == ANSWER

    @property
    def is_refused(self) -> bool:
        return self.kind == REFUSED

    def line(self) -> str:
        """The one line a caller logs (and the CLI prints) when the read did not answer."""
        if self.is_refused:
            named = f" [request id {self.request_id}]" if self.request_id else " [request id not named by GitHub]"
            return f"refused: GitHub refused: {self.message}{named}"
        return f"failed: no verdict: {self.detail}"


@dataclass(frozen=True)
class Write:
    """One write's outcome. `done` is the only kind a caller may count as a write that happened.

    A refusal is never `done` and never hands back an `answer`, so the failure cannot pass as success by being read as a
    body -- which is exactly how a `403` used to leave the loop believing the card moved.
    """

    kind: str
    answer: Any = None
    body: str = ""
    message: str = ""
    request_id: str = ""
    detail: str = ""

    @property
    def is_done(self) -> bool:
        return self.kind == DONE

    @property
    def is_refused(self) -> bool:
        return self.kind == REFUSED

    def line(self) -> str:
        """The one line a caller logs (and the CLI prints) for a write that did not happen."""
        if self.is_refused:
            named = f" [request id {self.request_id}]" if self.request_id else " [request id not named by GitHub]"
            return f"refused: GitHub refused the write: {self.message}{named}"
        if self.is_done:
            return "done: GitHub accepted the write"
        return f"failed: no verdict: {self.detail}"


TransportFn = Callable[[str], Transport]
# A write hands its transport the argv `gh api` would run (["--method", <method>, <route>, "-f", <k>=<v>, ...]), not a
# bare route: a stub has to see the method and the fields to prove the write it drove is the write that was asked for.
WriteTransportFn = Callable[[list[str]], Transport]


def gh_transport(route: str) -> Transport:
    """The default transport: `gh api --include <route>`, the one place in the loop that calls `gh` for a read.

    `--include` is what puts GitHub's response headers in front of the body, and the request id is one of them.
    """
    p = subprocess.run(["gh", "api", "--include", route], capture_output=True, text=True, check=False)
    return Transport(rc=p.returncode, body=p.stdout, detail=(p.stderr or "").strip())


def _split_headers(body: str) -> tuple[str, dict[str, str]]:
    """`gh api --include` puts a header block in front of the body; a transport may hand back the body alone."""
    if not body.startswith("HTTP/"):
        return body, {}
    head, sep, rest = body.partition("\r\n\r\n")
    if not sep:
        head, sep, rest = body.partition("\n\n")
    if not sep:
        return body, {}
    headers: dict[str, str] = {}
    for line in head.splitlines()[1:]:
        name, _, value = line.partition(":")
        if name.strip():
            headers[name.strip().lower()] = value.strip()
    return rest, headers


def _decode(body: str) -> tuple[Any, bool]:
    """The parsed body and whether it parsed. An empty body is an answer too: GitHub answered with nothing."""
    if not body.strip():
        return None, True
    try:
        return json.loads(body), True
    except ValueError:
        return None, False


def _refusal_shape(value: Any) -> dict[str, Any] | None:
    """GitHub's own error shape: a `message` beside a `status`. That is a refusal whatever the exit code said."""
    if isinstance(value, dict) and value.get("message") and "status" in value:
        return value
    return None


def _refusal(value: Any, headers: dict[str, str]) -> tuple[str, str] | None:
    """GitHub's message and request id when the body is GitHub's own error shape; `None` when it is data."""
    shaped = _refusal_shape(value)
    if shaped is None:
        return None
    message = str(shaped["message"])
    request_id = headers.get("x-github-request-id") or (_REQUEST_ID_IN_MESSAGE.search(message) or [None, ""])[1]
    return message, str(request_id)


def _verdict(res: Transport, success_kind: str) -> tuple[str, Any, str, str, str, str]:
    """The verdict one response carries, whoever asked: kind, answer, message, request id, detail and the raw body.

    One place decides, so a write can never drift from the read's rule that GitHub's error shape is a refusal whatever
    the exit code said, and that a non-JSON 200 is a failure rather than data. `success_kind` is what a verdict from
    GitHub means for the caller: `answer` for a read, `done` for a write.

    `body` is the raw response only where a caller may act on it: the words of a refusal are never handed back as a
    body, which is the whole regression this module exists for.
    """
    body, headers = _split_headers(res.body)
    value, parsed = _decode(body)
    refusal = _refusal(value, headers)
    if refusal is not None:
        return REFUSED, None, refusal[0], refusal[1], res.detail, ""
    if res.rc != 0:
        return FAILED, None, "", "", res.detail or f"gh exited {res.rc} without a verdict from GitHub", ""
    if not parsed:
        # A 200 that is not JSON is a proxy page or a truncated body, not data a caller may act on.
        return FAILED, None, "", "", "the answer was not JSON", body
    return success_kind, value, "", "", "", body


def read(route: str, transport: TransportFn | None = None) -> Read:
    """Read one route. Returns an answer, a refusal, or a failure -- never a mixture, and never `[]` for a refusal."""
    res = (transport or gh_transport)(route)
    kind, value, message, request_id, detail, body = _verdict(res, ANSWER)
    return Read(kind=kind, answer=value, body=body, message=message, request_id=request_id, detail=detail)


def gh_write_transport(argv: list[str]) -> Transport:
    """The default write transport: `gh api --include <argv>` -- `--include` for the same reason the read wants it.

    The request id of a refusal rides in the response headers, and a refused write is exactly the refusal a caller has
    to be able to name in the log.
    """
    p = subprocess.run(["gh", "api", "--include", *argv], capture_output=True, text=True, check=False)
    return Transport(rc=p.returncode, body=p.stdout, detail=(p.stderr or "").strip())


def write_argv(route: str, method: str = "POST", fields: Any = None) -> list[str]:
    """The argv `gh api` runs for one write: the method, the route, then one `-f <k>=<v>` per field."""
    argv = ["--method", method.upper(), route]
    pairs = fields.items() if isinstance(fields, Mapping) else (fields or [])
    for key, value in pairs:
        argv += ["-f", f"{key}={value}"]
    return argv


def write(route: str, method: str = "POST", fields: Any = None, transport: WriteTransportFn | None = None) -> Write:
    """Write to one route. Returns `done`, `refused` or `failed` -- and only `done` is a write that happened.

    A refusal carries GitHub's own message and request id, and its `answer` is always `None`: a caller that counts
    `is_done` never counts a `403` as a moved card, and a caller that reads `answer` never reads the error body as the
    object GitHub was supposed to have written back.
    """
    res = (transport or gh_write_transport)(write_argv(route, method, fields))
    kind, value, message, request_id, detail, body = _verdict(res, DONE)
    return Write(kind=kind, answer=value, body=body, message=message, request_id=request_id, detail=detail)


_WRITE_FLAGS = ("--method", "-X", "-f", "--raw-field", "-F", "--field")


def _parse_write(args: list[str]) -> tuple[str, str, dict[str, str]] | None:
    """`--method/-X <m>` and `-f/--raw-field <k>=<v>` around one route. `None` for anything else: a malformed write is
    a usage error, never a write with a field silently dropped."""
    method, route, fields, i = "POST", "", {}, 0
    while i < len(args):
        a = args[i]
        if a in ("--method", "-X") and i + 1 < len(args) and args[i + 1] not in _WRITE_FLAGS:
            method, i = args[i + 1], i + 2
        elif a in ("-f", "--raw-field", "-F", "--field") and i + 1 < len(args):
            key, _, value = args[i + 1].partition("=")
            if not key or "=" not in args[i + 1]:
                return None
            fields[key], i = value, i + 2
        elif not route and not a.startswith("-"):
            route, i = a, i + 1
        else:
            return None
    return (route, method, fields) if route else None


def main(argv: list[str]) -> int:
    """The CLI: the exit code carries the outcome, stdout carries the answer or the one-line refusal."""
    name = argv[0] if argv else "ghread.py"
    args = argv[1:]
    if args and args[0] in _WRITE_FLAGS:
        parsed = _parse_write(args)
        if parsed is None:
            sys.stderr.write(f"usage: {name} --method <METHOD> <route> [-f k=v ...]\n")
            return _USAGE
        route, method, fields = parsed
        outw = write(route, method=method, fields=fields)
        if outw.is_done:
            sys.stdout.write(outw.body)
            return 0
        print(outw.line())
        return 2 if outw.is_refused else 1
    if len(args) != 1 or args[0] in ("-h", "--help"):
        sys.stderr.write(
            f"usage: {name} <route>   # e.g. repos/o/r/pulls?state=open\n"
            f"       {name} --method <METHOD> <route> [-f k=v ...]   # a write\n"
        )
        return _USAGE
    out = read(args[0])
    if out.is_answer:
        sys.stdout.write(out.body)
        return 0
    print(out.line())
    return 2 if out.is_refused else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
