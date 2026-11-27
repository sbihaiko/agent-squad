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

Aggregate routes (#5). A caller asks for a *fact*, and the fact is one request whatever the transport behind it. Two
routes are declared aggregate today -- the open PRs of a repository, with the head ref, the draft flag, the labels, the
review-thread count and the check rollup, and its open issues, with the labels and the blocking edges:

    repos/<owner>/<repo>/pulls?state=open      one query, one response, every open PR of that repository
    repos/<owner>/<repo>/issues?state=open     one query, one response, every open issue of that repository

Both are answered by GraphQL, which is what makes them one request: REST needs one read per PR for the review threads and
one per PR head for the check rollup, and one read per issue for its state and its body, which is the per-item fan-out
the landing pass paid on every beat. The answer is shaped like REST's -- `head.ref`, `draft`, `labels[].name`, the
issue's `state` -- so a caller cannot tell which transport answered it, and the fields REST has no equivalent for
(`review_threads`, `check_rollup`, `blocked_by`) arrive beside them. A single issue read (`repos/<o>/<r>/issues/<n>`)
carries `blocked_by` too, so a caller that falls back to one issue reads the same fact it would have read from the list.

REST stays the transport for Project v2 items, for every write, and for every other route: where the equivalent query is
not demonstrably cheaper, the route is not aggregate. `errors` beside a `data` in a GraphQL answer is a *partial*
failure, and `Read.partial` names it: the field that failed is null and the rest of the answer is kept.

The call log. Every door appends one line per call to `$SG_STATE/api.log`, and the writer lives here because this is the one
module every door carries (see `log_call`):

    <UTC timestamp> <transport> <route> <hit|miss> <outcome> <pass> <cost> <remaining>
    2025-10-09T08:53:20Z REST repos/o/r/labels miss answer landing - -
    2026-11-27T18:12:04Z GraphQL graphql miss answer branchland 11 4210

* transport: `REST`, or `GraphQL` for an aggregate route.
* cache: `hit` when the snapshot answered it, `miss` for every read that went to GitHub, snapshottable or not, and `write` for
  every call that changes GitHub, whatever its outcome (`ghspend.py` reads a miss rate over `hit` and `miss` only, so a write is never
  a snapshot miss, #1328).
* outcome: `answer`, `refused` (GitHub refused it), `failed` (no verdict), `done` (a write GitHub took) or `cooling` (the shared
  cooldown held the call back and no request was sent).
* pass: the pass that spent the call -- `frontier`, `landing`, `autobug` or `board` -- from `SG_GH_PASS`, which each pass script
  exports for itself. A caller that names none is `unattributed`: a guess would put the spend on the wrong pass, which is the one
  thing this column exists to prevent.
* cost, remaining: the points the call spent and what its pool had left, as the answer's own `rateLimit` field reported them
  (#3043), or `-` when the answer carried no reading -- a REST call, a snapshot hit, a query that did not ask, or a line written
  before these columns. The hourly budget is spent in points, so a log of request counts alone cannot say which pass emptied it.

A line holds no space, so it splits into exactly eight fields; a line of six is a line written before the points columns, and it
reads with both points unknown. A failed append costs the line and never the call, and the log is rotated once at
`LOG_MAX_BYTES` so it cannot grow without bound. This module logs at its two CLI doors (`read`/`write` as a
process); a library caller -- the throttle -- logs its own calls, because only it knows a snapshot answered them, and
`graphql_read` logs its own read.

    python3 ghread.py --log <REST|GraphQL> <route> <hit|miss|write> <outcome>    # one line, for a shell door that has no Python of its own
    python3 ghread.py --fast-tier <sha>    # `<pass|fail|unproven>` TAB `<run id|->`, the fast tier of one sha (#1926)

Scope (ADR-0277): this is the outcome contract, the transports behind it and the call log. The token bucket, the shared
`$SG_STATE` cooldown and the per-resource response cache are `ghthrottle.py`, and the health causes read from the log and the
cooldown are `ghspend.py`; adding them here would put a rate governor behind an interface whose whole job is to keep three
outcomes apart.
"""

from __future__ import annotations

import calendar
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple

ANSWER = "answer"
DONE = "done"
REFUSED = "refused"
FAILED = "failed"

def loop_stop(issues):
    """The open loop-bug that blocks a child's entry, or None (drainpolicy.py, #1980)."""
    import drainpolicy
    return drainpolicy.held("child", issues or [])

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
    """One read's outcome. Exactly one of the three kinds, and the fields that belong to it.

    `cost` and `remaining` are the GraphQL points the call spent and what its pool had left, as the answer's own
    `rateLimit` field reported them (#3043). They are `None` for a REST call and for a GraphQL answer whose query did not
    ask, and the call log writes them either way."""

    kind: str
    answer: Any = None
    body: str = ""
    message: str = ""
    request_id: str = ""
    detail: str = ""
    partial: tuple[str, ...] = ()  # the fields that failed to compute while the rest of the answer was computed
    cost: int | None = None
    remaining: int | None = None

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


def gh_transport(route: str, timeout: float | None = None) -> Transport:
    """The default transport: `gh api --include <route>` -- or the aggregate route's one GraphQL request.

    `--include` is what puts GitHub's response headers in front of the body, and the request id is one of them. The
    transport is chosen from the route and from nothing else, so the caller that asked for the fact never learns which
    one answered it.
    """
    argv = ["gh", "api", "--include", *aggregate_argv(route)]
    p = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
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


def headers_of(body: str) -> dict[str, str]:
    """The response headers `gh api --include` put in front of the body, names lowercased; `{}` when the body has none."""
    return _split_headers(body)[1]


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


def rate_limit(value: Any) -> tuple[int | None, int | None]:
    """The points one GraphQL answer cost, and what its pool had left after it: `data.rateLimit { cost remaining }`,
    which a query asks for itself (#3043).

    The cost is the only measure of a GraphQL call's price that GitHub publishes; the hourly budget is spent in points, so
    a pass that logs only its request count cannot say how much of the hour it spent. Anything other than that shape -- a
    REST body, a query that did not ask, a value of another type -- reads as `(None, None)`: an unknown cost is never
    written as zero."""
    limit = _connection(_connection(value).get("data")).get("rateLimit")
    if not isinstance(limit, dict):
        return None, None

    def whole(key: str) -> int | None:
        number = limit.get(key)
        return number if isinstance(number, int) and not isinstance(number, bool) else None

    return whole("cost"), whole("remaining")


# --- the aggregate routes: the facts the loop re-reads every beat -----------------------------------------------------

_PAGE = 100  # the bound of one request: GitHub's first page. A repository with more open items than this is not the
# loop's case -- the repositories it watches hold tens -- and walking the cursor is the fan-out this route removes.
# An answer the bound cut short says so, in `Read.partial`, rather than passing as the whole repository.

_PULLS_QUERY = """query($owner: String!, $repo: String!) {
  rateLimit { cost remaining }
  repository(owner: $owner, name: $repo) {
    pullRequests(states: OPEN, first: %d, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage }
      nodes {
        number title body isDraft headRefName headRefOid baseRefName
        labels(first: 100) { nodes { name } }
        reviewThreads { totalCount }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
                contexts(first: 100) {
                  totalCount
                  nodes { __typename ... on CheckRun { conclusion status } ... on StatusContext { state } }
                }
              }
            }
          }
        }
      }
    }
  }
}""" % _PAGE

# What one issue of a fact carries. `comments(last: ...)` is how the TDD RED comment of a fork-only fix is found without a
# read of the issue's comments, which was one request per candidate (#45).
_COMMENTS = 50
_ISSUE_FIELDS = f"number title body state stateReason createdAt repository {{ nameWithOwner }} labels(first: 100) {{ nodes {{ name }} }} blockedBy(first: 100) {{ pageInfo {{ hasNextPage }} nodes {{ number state repository {{ nameWithOwner }} }} }} parent {{ number }} subIssuesSummary {{ total completed }} subIssues(first: 100) {{ nodes {{ number }} }} comments(last: {_COMMENTS}) {{ nodes {{ body }} }}"
_ISSUES_QUERY = """query($owner: String!, $repo: String!) {
  rateLimit { cost remaining }
  repository(owner: $owner, name: $repo) {
    issues(states: OPEN, first: %d) {
      pageInfo { hasNextPage }
      nodes { %s }
    }
  }
}""" % (_PAGE, _ISSUE_FIELDS)

_FAILED_CONCLUSIONS = ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE")
_PASSED_CONCLUSIONS = ("SUCCESS", "NEUTRAL", "SKIPPED")
_FAILED_STATES = ("FAILURE", "ERROR")
_ROUTE = re.compile(r"^repos/(?P<owner>[^/?]+)/(?P<repo>[^/?]+)/(?P<rest>[^?]*)(?:\?(?P<params>.*))?$")
_SINGLE_ISSUE = re.compile(r"^issues/\d+$")


def _connection(node: Any) -> dict[str, Any]:
    return node if isinstance(node, dict) else {}


def _labels(connection: Any) -> list[dict[str, Any]]:
    nodes = _connection(connection).get("nodes")
    return [{"name": n.get("name")} for n in nodes or [] if isinstance(n, dict)]


def _total(connection: Any) -> int:
    nodes = _connection(connection).get("nodes") or []
    total = _connection(connection).get("totalCount")
    return total if isinstance(total, int) else len(nodes)


def _context_verdict(context: Any) -> str:
    """One check context's own verdict: passed, failed, or still pending. A context with no verdict yet is pending."""
    if not isinstance(context, dict):
        return "pending"
    if context.get("__typename") == "StatusContext":
        state = context.get("state")
        if state == "SUCCESS":
            return "passed"
        return "failed" if state in _FAILED_STATES else "pending"
    conclusion, status = context.get("conclusion"), context.get("status")
    if conclusion in _FAILED_CONCLUSIONS:
        return "failed"
    if status == "COMPLETED" and conclusion in _PASSED_CONCLUSIONS:
        return "passed"
    return "pending"


def _check_rollup(commits: Any) -> dict[str, Any]:
    """The check rollup of a PR's last commit: the state GitHub reports and the counts a landing decision reads."""
    rollup: dict[str, Any] | None = None
    for commit in _connection(commits).get("nodes") or []:
        candidate = _connection(_connection(commit).get("commit")).get("statusCheckRollup")
        if isinstance(candidate, dict):
            rollup = candidate
            break
    if rollup is None:
        return {"state": None, "total": 0, "failed": 0, "pending": 0}
    contexts = _connection(rollup.get("contexts"))
    verdicts = [_context_verdict(c) for c in contexts.get("nodes") or []]
    state = rollup.get("state")
    return {
        "state": state.lower() if isinstance(state, str) else None,
        "total": _total(contexts),
        "failed": verdicts.count("failed"),
        "pending": verdicts.count("pending"),
    }


FAST_TIER_CHECKS = ("ruff", "mypy", "fast-battery")
_RUN_URL = re.compile(r"/actions/runs/(\d+)")


@dataclass(frozen=True)
class FastTier:
    verdict: Literal["pass", "fail", "unproven"]
    run_id: int | None


RestFn = Callable[[list[str]], Transport]


def _run_id(check: dict[str, Any]) -> int | None:
    match = _RUN_URL.search(str(check.get("details_url") or ""))
    return int(match[1]) if match else None


def _shared_run_id(checks: list[dict[str, Any]]) -> int | None:
    ids = {_run_id(check) for check in checks}
    return ids.pop() if len(ids) == 1 else None


def _gh_rest_get(argv: list[str]) -> Transport:
    p = subprocess.run(["gh", "api", *argv], capture_output=True, text=True, check=False)
    return Transport(rc=p.returncode, body=p.stdout, detail=(p.stderr or "").strip())


def _rest_get(route: str, transport: RestFn | None) -> dict[str, Any] | None:
    kind, value, *_ = _verdict((transport or _gh_rest_get)(["--method", "GET", route]), ANSWER)
    return value if kind == ANSWER and isinstance(value, dict) else None


def fast_tier_verdict(sha: str, transport: RestFn | None = None) -> FastTier:
    """The fast-tier verdict of one sha from its check runs, and the Actions run that carries them."""
    answer = _rest_get(f"repos/sbihaiko/agent-squad/commits/{sha}/check-runs?per_page=100", transport)
    if answer is None:
        return FastTier("unproven", None)
    latest: dict[str, dict[str, Any]] = {}
    for check in answer.get("check_runs") or []:
        if not isinstance(check, dict) or check.get("name") not in FAST_TIER_CHECKS:
            continue
        # A re-run is a newer check run of the same name, and it replaces the failed one it re-ran.
        kept = latest.get(check["name"])
        if kept is None or check.get("id", 0) > kept.get("id", 0):
            latest[check["name"]] = check
    failed = [name for name in FAST_TIER_CHECKS if latest.get(name, {}).get("conclusion") == "failure"]
    if failed:
        return FastTier("fail", _run_id(latest[failed[0]]))
    passed = all(latest.get(name, {}).get("conclusion") == "success" for name in FAST_TIER_CHECKS)
    run_id = _shared_run_id([latest[name] for name in FAST_TIER_CHECKS if name in latest])
    return FastTier("pass" if passed else "unproven", run_id)


def _pull(node: Any) -> dict[str, Any]:
    """One PR of the fact, on REST's names -- `head.ref`, `draft`, `labels[].name` -- plus what REST cannot answer in
    the same request. A field that failed to compute arrives null and does not take the rest of the PR with it."""
    node = _connection(node)
    return {
        "number": node.get("number"),
        "title": node.get("title"),
        "body": node.get("body"),
        "state": "open",
        "draft": node.get("isDraft") is True,
        "head": {"ref": node.get("headRefName"), "sha": node.get("headRefOid")},
        "base": {"ref": node.get("baseRefName")},
        "labels": _labels(node.get("labels")),
        "review_threads": _total(node.get("reviewThreads")) if node.get("reviewThreads") is not None else None,
        "check_rollup": _check_rollup(node.get("commits")),
    }


def _issue(node: Any) -> dict[str, Any]:
    """One issue of the fact, on REST's names (`state` lowercased, as REST spells it) plus its blocking edges."""
    node = _connection(node)
    state = node.get("state")
    return {
        "number": node.get("number"),
        "title": node.get("title"),
        "body": node.get("body"),
        "created_at": node.get("createdAt"),
        "state": state.lower() if isinstance(state, str) else None,
        # The close reason, on REST's name and spelling: a card's terminal column follows it (board_columns.terminal_column, #1932)
        "state_reason": node.get("stateReason").lower() if isinstance(node.get("stateReason"), str) else None,
        "repo": _name_with_owner(node.get("repository")),
        "labels": _labels(node.get("labels")),
        "blocked_by_edges": [   # each blocker names its own repository, so issue #12 of one repo is never #12 of another (#1983)
            {"repo": _name_with_owner(n.get("repository")), "number": n.get("number"), "state": n.get("state").lower() if isinstance(n.get("state"), str) else None}
            for n in _connection(node.get("blockedBy")).get("nodes") or []
            if isinstance(n, dict) and isinstance(n.get("number"), int)
        ],
        "blocked_by_complete": _connection(_connection(node.get("blockedBy")).get("pageInfo")).get("hasNextPage") is False,   # False: a page of blockers is missing or unread, so the edges are partial (#1983)
        "blocked_by": [
            n.get("number")
            for n in _connection(node.get("blockedBy")).get("nodes") or []
            if isinstance(n, dict) and isinstance(n.get("number"), int)
        ],
        "blocked_by_read": isinstance(node.get("blockedBy"), dict),   # False: the answer carried no relation, so [] proves nothing
        "blockers": [   # each blocker's own state, so a stacking decision needs no read per blocker (#383); a missing state is unread
            {"number": n.get("number"), "state": n.get("state").lower() if isinstance(n.get("state"), str) else None}
            for n in _connection(node.get("blockedBy")).get("nodes") or []
            if isinstance(n, dict) and isinstance(n.get("number"), int)
        ],
        "tdd_red": _tdd_red(node.get("comments")),
        "not_startable": _not_startable(node.get("comments")),
        # The native sub-issue link's other end: the parent an issue is a ticket of, and None when it has none. A ticket
        # of a spec dispatches only once the owner starts the spec (ADR-0142 rule 6), so the dispatch gate reads it.
        "parent": node["parent"].get("number") if isinstance(node.get("parent"), dict) else None,
        "sub_issues_summary": _sub_issues(node.get("subIssuesSummary")),
        "sub_issues": [   # the native relation's children, numbers only: the spec's column folds over their cards (spec_fold.py)
            n.get("number")
            for n in _connection(node.get("subIssues")).get("nodes") or []
            if isinstance(n, dict) and isinstance(n.get("number"), int)
        ],
    }


def _name_with_owner(repository: Any) -> str | None:
    name = _connection(repository).get("nameWithOwner")
    return name if isinstance(name, str) else None


def _sub_issues(summary: Any) -> dict[str, int] | None:
    """REST's `sub_issues_summary`: how many sub-issues the issue has and how many are closed. `None` when the answer
    carried no summary, so a reader never takes a missing count for zero."""
    if not isinstance(summary, dict) or not all(isinstance(summary.get(k), int) for k in ("total", "completed")):
        return None
    return {"total": summary["total"], "completed": summary["completed"]}


def _tdd_red(comments: Any) -> bool | None:
    """Does a comment of the issue name a TDD RED report? `None` when the answer carried no comments to look in."""
    if not isinstance(comments, dict):
        return None
    return any("TDD RED" in str(c.get("body") or "") for c in _connection(comments).get("nodes") or [] if isinstance(c, dict))


_MARKER_BUILD = re.compile(r"\bbuild=([0-9a-f]+)\b")
_LOOP_BUILD: str | None = None


def loop_build() -> str:
    """The loop's build, as `sg_loop_build` in ghcall.sh reads it: the sha of the checkout it publishes from. Empty when
    the checkout cannot be read; a marker then keeps its stop, because an unknown build proves nothing."""
    global _LOOP_BUILD
    if _LOOP_BUILD is None:
        _LOOP_BUILD = ""
        try:
            r = subprocess.run(["/usr/bin/git", "-C", os.environ.get("SG_CHECKOUT") or ".", "rev-parse", "--short=12", "HEAD"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                _LOOP_BUILD = r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            _LOOP_BUILD = ""
    return _LOOP_BUILD


def _not_startable(comments: Any) -> bool | None:
    """Does a comment line start with the loop's `SQUAD-NOT-STARTABLE:` marker (health.sh and wave.sh write it)? `None`
    when the answer carried no comments to look in, so a reader never takes a missing read for "startable". A marker names
    the build that wrote it: another build re-arms it (owner answer B2, ADR-0134), and a marker that names none, or a build
    that cannot be read, keeps its stop because neither proves anything."""
    if not isinstance(comments, dict):
        return None
    cur = loop_build()
    for c in _connection(comments).get("nodes") or []:
        if not isinstance(c, dict):
            continue
        for line in str(c.get("body") or "").splitlines():
            if not line.startswith("SQUAD-NOT-STARTABLE:"):
                continue
            m = _MARKER_BUILD.search(line)
            if m and cur and m.group(1) != cur:
                continue     # written by another build: re-armed
            return True
    return False


@dataclass(frozen=True)
class Fact:
    """One aggregate route: its query, how GitHub's `data` becomes the fact, and whether the page bound cut it short."""

    name: str
    document: str
    of: Callable[[Any], list[dict[str, Any]]]
    more: Callable[[Any], bool]


_FACT_PULLS = Fact(
    "the open PRs of the repository",
    _PULLS_QUERY,
    lambda data: [_pull(n) for n in _connection(_connection(_connection(data).get("repository")).get("pullRequests")).get("nodes") or []],
    lambda data: _connection(_connection(_connection(data).get("repository")).get("pullRequests")).get("pageInfo", {}).get("hasNextPage") is True,
)
_FACT_ISSUES = Fact(
    "the open issues of the repository",
    _ISSUES_QUERY,
    lambda data: [_issue(n) for n in _connection(_connection(_connection(data).get("repository")).get("issues")).get("nodes") or []],
    lambda data: _connection(_connection(_connection(data).get("repository")).get("issues")).get("pageInfo", {}).get("hasNextPage") is True,
)
_AGGREGATE = {"pulls": _FACT_PULLS, "issues": _FACT_ISSUES}
_AGGREGATE_PARAMS = frozenset({"state", "per_page"})   # the only query parameters the open-list facts answer (#847)


def _route(route: Any) -> re.Match[str] | None:
    return _ROUTE.match(route) if isinstance(route, str) else None


_NUMBERS_LIMIT = 100  # aliases in one request; a longer list is cut here and the fact says so


def _numbers(route: str) -> list[int] | None:
    """The issue numbers of `repos/o/r/issues?numbers=1,2,3`, or `None` for any other route."""
    m = _route(route)
    if m is None or m.group("rest") != "issues":
        return None
    raw = dict(p.split("=", 1) for p in (m.group("params") or "").split("&") if "=" in p).get("numbers")
    if raw is None:
        return None
    return [int(x) for x in raw.split(",") if x.isdigit()]


def _numbers_fact(numbers: list[int]) -> Fact:
    """The issues of a known set of numbers -- open or closed -- in ONE request: the fork landing pass asks for the
    candidates the open list did not carry, and the answer must not cost a read per candidate (#45)."""
    shown = numbers[:_NUMBERS_LIMIT]
    body = " ".join(f"i{n}: issue(number: {n}) {{ {_ISSUE_FIELDS} }}" for n in shown)
    document = "query($owner: String!, $repo: String!) { rateLimit { cost remaining } repository(owner: $owner, name: $repo) { %s } }" % body
    return Fact(
        "the issues of the named numbers",
        document,
        lambda data: [_issue(v) for v in _connection(_connection(data).get("repository")).values() if isinstance(v, dict)],
        lambda data: len(numbers) > _NUMBERS_LIMIT,
    )


def aggregate(route: str) -> Fact | None:
    """The fact a route asks for -- or `None`, which is every route that stays REST: Project v2 items, writes, and any
    endpoint whose equivalent query is not demonstrably cheaper."""
    m = _route(route)
    numbers = _numbers(route)
    if numbers is not None:
        return _numbers_fact(numbers) if numbers else None
    if m is not None and _SINGLE_ISSUE.match(m.group("rest")) is not None and not m.group("params"):
        return _numbers_fact([int(m.group("rest").split("/")[1])])
    if m is None or m.group("rest") not in _AGGREGATE:
        return None
    params = dict(p.split("=", 1) for p in (m.group("params") or "").split("&") if "=" in p)
    # A filter the fact does not answer (labels=, sort=, direction=) makes the route a REST list: the fact is the whole
    # open list, and answering a labelled route with it hands a debt card to the bug pass (#847).
    if not set(params) <= _AGGREGATE_PARAMS:
        return None
    return _AGGREGATE[m.group("rest")] if params.get("state") == "open" else None


def _single_issue(route: str) -> bool:
    m = _route(route)
    return m is not None and _SINGLE_ISSUE.match(m.group("rest")) is not None and not m.group("params")


def aggregate_argv(route: str) -> list[str]:
    """The argv the fact's one request runs: `graphql` with the query and its variables, or the REST route unchanged."""
    fact = aggregate(route)
    if fact is None:
        return [route]
    m = _route(route)
    owner, repo = (m.group("owner"), m.group("repo")) if m is not None else ("", "")
    return ["graphql", "-f", f"query={fact.document}", "-F", f"owner={owner}", "-F", f"repo={repo}"]


def _aggregate_read(fact: Fact, value: dict[str, Any], headers: dict[str, str]) -> Read:
    """GitHub's GraphQL answer as the fact. A query refused outright is a refusal; a failed field inside an answer this
    GitHub computed is not, and the rest of the answer is kept and the field is named."""
    errors = tuple(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in value.get("errors") or [])
    data = value.get("data")
    if data is None:
        if errors:
            request_id = headers.get("x-github-request-id") or (_REQUEST_ID_IN_MESSAGE.search("; ".join(errors)) or [None, ""])[1]
            return Read(kind=REFUSED, message="; ".join(errors), request_id=str(request_id))
        return Read(kind=FAILED, detail=f"the query answered no data for {fact.name}")
    shaped = fact.of(data)
    partial = list(errors)
    if fact.more(data):
        partial.append(f"{fact.name}: more than {_PAGE} exist, and the first {_PAGE} are here")
    cost, remaining = rate_limit(value)
    return Read(kind=ANSWER, answer=shaped, body=json.dumps(shaped), partial=tuple(partial), cost=cost, remaining=remaining)


def read(route: str, transport: TransportFn | None = None) -> Read:
    """Read one route. Returns an answer, a refusal, or a failure -- never a mixture, and never `[]` for a refusal.

    An aggregate route is answered by its fact, whichever transport answered it; every other route is answered as it
    arrives. A single issue route carries the derived `blocked_by` its list carries, so a caller that falls back to one
    issue reads the same fact it would have read from the repository's list.
    """
    fact = aggregate(route)
    res = (transport or gh_transport)(route)
    body, headers = _split_headers(res.body)
    value, parsed = _decode(body)
    if parsed and isinstance(value, dict) and ("data" in value or "errors" in value):
        if fact is not None:
            out = _aggregate_read(fact, value, headers)
            if out.is_answer and _single_issue(route):
                issue = out.answer[0] if isinstance(out.answer, list) and out.answer else {}
                return Read(kind=ANSWER, answer=issue, body=json.dumps(issue), partial=out.partial,
                            cost=out.cost, remaining=out.remaining)
            return out
    kind, value, message, request_id, detail, raw = _verdict(res, ANSWER)
    if kind == ANSWER and isinstance(value, dict) and _single_issue(route):
        shaped = _issue(value)
        cost, remaining = rate_limit(value)
        return Read(kind=ANSWER, answer=shaped, body=json.dumps(shaped), cost=cost, remaining=remaining)
    cost, remaining = rate_limit(value) if kind == ANSWER else (None, None)
    return Read(kind=kind, answer=value, body=raw, message=message, request_id=request_id, detail=detail,
                cost=cost, remaining=remaining)


def graphql_argv(query: str, variables: dict[str, Any] | None = None) -> list[str]:
    """The argv a GraphQL read runs: `graphql -f query=<query>`, then one typed `-F` per variable.

    It is the same door: a query no aggregate route names still asks GitHub once, and its answer, its refusal and its
    failure are read exactly as every other read's. `-F` types the variables (`number` is an Int), because `-f` would
    send a string where the query declares an integer.
    """
    argv = ["graphql", "-f", f"query={query}"]
    for k, v in (variables or {}).items():
        argv += ["-F", f"{k}={v}"]
    return argv


def graphql_read(query: str, variables: dict[str, Any] | None = None, transport: TransportFn | None = None,
                 timeout: float | None = None, pass_name: str | None = None) -> Read:
    """One GraphQL read of a query no aggregate route names, through the same three-outcome contract (`read`), and one
    call-log line for it: the outcome, and the pass that spent it (`pass_name`, else `SG_GH_PASS`).

    The transport is injectable like every other read's, so a caller's test names the outcomes and no test touches the
    network. The default transport runs the argv `graphql_argv` builds, with `--include` for the request id.
    """
    argv = graphql_argv(query, variables)

    def run(route: str, when: float | None = None) -> Transport:
        p = subprocess.run(["gh", "api", "--include", *argv], capture_output=True, text=True, check=False,
                           timeout=timeout if when is None else when)
        return Transport(rc=p.returncode, body=p.stdout, detail=(p.stderr or "").strip())

    try:
        res = (transport or run)("graphql")
    except (OSError, subprocess.TimeoutExpired):
        log_call(None, "graphql", MISS, FAILED, transport="GraphQL", pass_name=pass_name)
        raise
    out = _graphql_outcome(res)
    log_call(None, "graphql", MISS, out.kind, transport="GraphQL", pass_name=pass_name,
             cost=out.cost, remaining=out.remaining)
    return out


def _graphql_outcome(res: Transport) -> Read:
    body, headers = _split_headers(res.body)
    value, parsed = _decode(body)
    # A GraphQL answer is `data`, an `errors` list or both; `errors` with no `data` is a refusal, and a field that failed
    # beside a `data` is partial, exactly as an aggregate route's answer is read (#1893).
    if parsed and isinstance(value, dict) and ("data" in value or "errors" in value):
        errors = tuple(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in value.get("errors") or [])
        if value.get("data") is None:
            if errors:
                request_id = headers.get("x-github-request-id") or (
                    _REQUEST_ID_IN_MESSAGE.search("; ".join(errors)) or [None, ""])[1]
                return Read(kind=REFUSED, message="; ".join(errors), request_id=str(request_id))
            return Read(kind=FAILED, detail="the query answered no data")
        cost, remaining = rate_limit(value)
        return Read(kind=ANSWER, answer=value, body=json.dumps(value), partial=errors, cost=cost, remaining=remaining)
    kind, answer, message, request_id, detail, raw = _verdict(res, ANSWER)
    return Read(kind=kind, answer=answer, body=raw, message=message, request_id=request_id, detail=detail)


# A conditional read hands its transport the route and the ETag the caller already holds for it.
RevalidateFn = Callable[[str, str], Transport]


def gh_conditional_transport(route: str, etag: str, timeout: float | None = None) -> Transport:
    """`gh api --include -H 'If-None-Match: <etag>' <route>`: GitHub answers 304 when the entity is the one the ETag names.

    It is a read like any other (`--include` for the same reason, so the status is in front of the body), and a 304 is
    how a caller learns that what it holds is still the answer without being handed -- or parsing -- the answer again.
    """
    p = subprocess.run(
        ["gh", "api", "--include", "-H", f"If-None-Match: {etag}", route], capture_output=True, text=True, check=False,
        timeout=timeout,
    )
    return Transport(rc=p.returncode, body=p.stdout, detail=(p.stderr or "").strip())


def gh_write_transport(argv: list[str]) -> Transport:
    """The default write transport: `gh api --include <argv>` -- `--include` for the same reason the read wants it.

    The request id of a refusal rides in the response headers, and a refused write is exactly the refusal a caller has
    to be able to name in the log.
    """
    p = subprocess.run(["gh", "api", "--include", *argv], capture_output=True, text=True, check=False)
    return Transport(rc=p.returncode, body=p.stdout, detail=(p.stderr or "").strip())


def write_argv(route: str, method: str = "POST", fields: Any = None) -> list[str]:
    """The argv `gh api` runs for one write: the method, the route, then one field per pair, typed when it is an integer."""
    argv = ["--method", method.upper(), route]
    pairs = fields.items() if isinstance(fields, Mapping) else (fields or [])
    for key, value in pairs:
        argv += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
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


# --- the call log: one line per call (ADR-0277) -----------------------------------------------------------------------

STATE_ENV = "SG_STATE"
PASS_ENV = "SG_GH_PASS"
LOG_FILE = "api.log"
LOG_MAX_BYTES = 2_000_000  # one rotation: days of a healthy loop, and far more than any health window reads
UNATTRIBUTED = "unattributed"
HIT, MISS, COOLING = "hit", "miss", "cooling"
WRITE = "write"   # a call that changes GitHub: not a read, so never a snapshot miss, whatever its outcome (#1328)
_PASS = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_LOG_TS = "%Y-%m-%dT%H:%M:%SZ"


class Call(NamedTuple):
    """One line of the call log, parsed. `cost` and `remaining` are the points the call spent and what its pool had left
    after it, as the answer's own `rateLimit` reported them; `None` when the answer carried no reading (#3043)."""

    at: float
    transport: str
    route: str
    cache: str
    outcome: str
    pass_name: str
    cost: int | None = None
    remaining: int | None = None


def state_dir() -> Path:
    """Where the loop's shared state lives: `$SG_STATE`, else the loop's default cache dir."""
    return Path(os.environ.get(STATE_ENV) or Path.home() / ".cache" / "squad-goal")


def current_pass() -> str:
    """The pass that is spending: `SG_GH_PASS` when it is one lowercase word, else `unattributed` (never a guess)."""
    name = os.environ.get(PASS_ENV, "")
    return name if _PASS.match(name) else UNATTRIBUTED


def transport_name(route: str) -> str:
    """`GraphQL` for an aggregate route -- its one request is a query -- and `REST` for every other."""
    return "GraphQL" if aggregate(route) is not None else "REST"


def _points_field(points: int | None) -> str:
    """The points column: the number, or `-` when the call carried no `rateLimit` reading (a REST call, a hit, a line
    written before the columns existed, #3043). The dash keeps one line six or eight fields and never a hole."""
    return "-" if points is None else str(points)


def _points(column: str) -> int | None:
    """The number a points column carries, or None for `-`; a column that is neither is not a line this reader trusts."""
    if column == "-":
        return None
    return int(column) if column.isdigit() else None


def call_line(call: Call) -> str:
    return " ".join((
        time.strftime(_LOG_TS, time.gmtime(call.at)), call.transport, call.route, call.cache, call.outcome, call.pass_name,
        _points_field(call.cost), _points_field(call.remaining),
    ))


def parse_call(text: str) -> Call | None:
    """One log line as a `Call`, or None for anything that is not one: a half line, a foreign line, a forged one.

    A line of six fields is a line written before the points columns (#3043): it reads, and its points are unknown. A line
    of eight carries the cost and what the pool had left."""
    parts = text.split()
    if len(parts) not in (6, 8):
        return None
    try:
        at = float(calendar.timegm(time.strptime(parts[0], _LOG_TS)))
    except ValueError:
        return None
    if len(parts) == 6:
        return Call(at, parts[1], parts[2], parts[3], parts[4], parts[5])
    cost, remaining = _points(parts[6]), _points(parts[7])
    if (cost is None) != (parts[6] == "-") or (remaining is None) != (parts[7] == "-"):
        return None   # a points column that is neither a number nor the dash: no line this reader trusts
    return Call(at, parts[1], parts[2], parts[3], parts[4], parts[5], cost, remaining)


def log_call(
    state: Path | str | None,
    route: str,
    cache: str,
    outcome: str,
    transport: str | None = None,
    pass_name: str | None = None,
    clock: Callable[[], float] = time.time,
    cost: int | None = None,
    remaining: int | None = None,
) -> None:
    """Append one line to the call log. Never raises: an unwritable state dir costs the line and not the call."""
    path = (Path(state) if state is not None else state_dir()) / LOG_FILE
    call = Call(clock(), transport or transport_name(route), "".join(route.split()), cache, outcome,
                pass_name or current_pass(), cost, remaining)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            if path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))  # atomic: a writer that loses the race appends to the new file
        with path.open("a") as f:
            f.write(call_line(call) + "\n")  # one write of one short line: O_APPEND keeps two processes' lines apart
    except OSError:
        return


def main(argv: list[str]) -> int:
    """The CLI: the exit code carries the outcome, stdout carries the answer or the one-line refusal."""
    name = argv[0] if argv else "ghread.py"
    args = argv[1:]
    if args and args[0] == "--fast-tier":
        # The fast-tier verdict of one sha, for a shell door (#1926, #1927): `pass|fail|unproven`, a TAB, then the Actions
        # run id or `-`. An unanswered read is `unproven` with no run, so a caller that acts only on `pass` keeps its
        # today's path on a refusal. The call is logged as the read it is.
        if len(args) != 2:
            sys.stderr.write(f"usage: {name} --fast-tier <sha>\n")
            return _USAGE
        route = f"repos/sbihaiko/agent-squad/commits/{args[1]}/check-runs?per_page=100"
        out = fast_tier_verdict(args[1])
        log_call(None, route, MISS, FAILED if out.run_id is None and out.verdict == "unproven" else ANSWER)
        print(f"{out.verdict}\t{out.run_id if out.run_id is not None else '-'}")
        return 0
    if args and args[0] == "--log":
        # The points are optional: a door that holds no body, or whose body carried no `rateLimit`, logs no reading (#3043).
        if len(args) not in (5, 7) or args[3] not in (HIT, MISS, WRITE):
            sys.stderr.write(f"usage: {name} --log <REST|GraphQL> <route> <hit|miss|write> <outcome> [<cost> <remaining>]\n")
            return _USAGE
        cost, remaining = (_points(args[5]), _points(args[6])) if len(args) == 7 else (None, None)
        log_call(None, args[2], args[3], args[4], transport=args[1], cost=cost, remaining=remaining)
        return 0
    if args and args[0] in _WRITE_FLAGS:
        parsed = _parse_write(args)
        if parsed is None:
            sys.stderr.write(f"usage: {name} --method <METHOD> <route> [-f k=v ...]\n")
            return _USAGE
        route, method, fields = parsed
        outw = write(route, method=method, fields=fields)
        log_call(None, route, WRITE, outw.kind, transport="REST")   # a write is a REST request, whatever a read of its route uses (#393)
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
    log_call(None, args[0], MISS, out.kind, cost=out.cost, remaining=out.remaining)
    if out.is_answer:
        sys.stdout.write(out.body)
        return 0
    print(out.line())
    return 2 if out.is_refused else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
