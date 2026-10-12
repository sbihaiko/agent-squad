#!/usr/bin/env python3
"""One interface owns every GitHub read the loop makes (#1893, ADR-0277).

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

The transport is injectable (`read(route, transport=...)`), so a test drives this module with a stub and needs no
network. The same contract is reachable as a process -- `python3 ghread.py <route>` -- which is how a shell script
reaches it. The exit code is the contract a script reads, because a script cannot see a Python return value:

    0  the answer, verbatim on stdout (the caller's `jq` reads it)
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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ANSWER = "answer"
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


TransportFn = Callable[[str], Transport]


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


def read(route: str, transport: TransportFn | None = None) -> Read:
    """Read one route. Returns an answer, a refusal, or a failure -- never a mixture, and never `[]` for a refusal."""
    res = (transport or gh_transport)(route)
    body, headers = _split_headers(res.body)
    value, parsed = _decode(body)

    shaped = _refusal_shape(value)
    if shaped is not None:
        message = str(shaped["message"])
        request_id = headers.get("x-github-request-id") or (_REQUEST_ID_IN_MESSAGE.search(message) or [None, ""])[1]
        return Read(kind=REFUSED, message=message, request_id=str(request_id), detail=res.detail)

    if res.rc != 0:
        return Read(kind=FAILED, detail=res.detail or f"gh exited {res.rc} without a verdict from GitHub")

    if not parsed:
        # A 200 that is not JSON is a proxy page or a truncated body, not data a caller may act on.
        return Read(kind=FAILED, detail="the answer was not JSON", body=body)

    return Read(kind=ANSWER, answer=value, body=body)


def main(argv: list[str]) -> int:
    """The CLI: the exit code carries the outcome, stdout carries the answer or the one-line refusal."""
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        sys.stderr.write(f"usage: {argv[0] if argv else 'ghread.py'} <route>   # e.g. repos/o/r/pulls?state=open\n")
        return _USAGE
    out = read(argv[1])
    if out.is_answer:
        sys.stdout.write(out.body)
        return 0
    print(out.line())
    return 2 if out.is_refused else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
