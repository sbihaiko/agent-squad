"""Issue #5: aggregate reads move to GraphQL, so the per-item fan-out disappears (ADR-0277).

The facts the loop re-reads every beat -- the open PRs of a repository with their head ref, draft flag, labels,
review-thread counts and check rollup, and the open issues with their labels and blocking edges -- are asked as facts.
One route, one request, one list: the transport that answered is invisible to the caller, which projects the same fields
out of the same shape whether REST or GraphQL produced it. REST stays the transport for Project v2 items, for every
write, and for any endpoint whose equivalent query is not demonstrably cheaper; so nothing here touches a write.

The last case is the measurement the issue names: the landing pass's collection (`startability.sh`) is the loop's widest
per-item fan-out, and the request count it spends on these two facts must not follow the number of candidates.

Every case drives a stub. No case reaches the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
MODULE = SCRIPTS / "ghread.py"

PRS_ROUTE = "repos/o/r/pulls?state=open&per_page=100"
ISSUES_ROUTE = "repos/o/r/issues?state=open"

# What GitHub answers an aggregate route with. Hand-written literals in GitHub's own shape: the expected values below
# come from these, not from the module's own arithmetic.
PRS_ENVELOPE = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [
                        {
                            "number": 11,
                            "title": "Play: the pad",
                            "body": "Refs #1",
                            "isDraft": True,
                            "headRefName": "gui/11",
                            "headRefOid": "1a2b3c4d",
                            "labels": {"nodes": [{"name": "bug"}, {"name": "core"}]},
                            "reviewThreads": {"totalCount": 3},
                            "commits": {
                                "nodes": [
                                    {
                                        "commit": {
                                            "statusCheckRollup": {
                                                "state": "FAILURE",
                                                "contexts": {
                                                    "totalCount": 4,
                                                    "nodes": [
                                                        {"__typename": "CheckRun", "conclusion": "SUCCESS", "status": "COMPLETED"},
                                                        {"__typename": "CheckRun", "conclusion": "FAILURE", "status": "COMPLETED"},
                                                        {"__typename": "CheckRun", "conclusion": None, "status": "IN_PROGRESS"},
                                                        {"__typename": "StatusContext", "state": "SUCCESS"},
                                                    ],
                                                },
                                            }
                                        }
                                    }
                                ]
                            },
                        },
                        {
                            "number": 12,
                            "title": "Docs: the register",
                            "body": "",
                            "isDraft": False,
                            "headRefName": "gui/12",
                            "headRefOid": "5e6f7a8b",
                            "labels": {"nodes": []},
                            "reviewThreads": {"totalCount": 0},
                            "commits": {"nodes": []},
                        },
                    ]
                }
            }
        }
    }
)

ISSUES_ENVELOPE = json.dumps(
    {
        "data": {
            "repository": {
                "issues": {
                    "nodes": [
                        {
                            "number": 5,
                            "title": "Aggregate reads move to GraphQL",
                            "state": "OPEN",
                            "body": "## What to build\n\nOne query each.\n\n## Blocked by\n\n- #3\n- sbihaiko/agent-squad#4\n\n## Notes\n- #99 is not an edge\n",
                            "labels": {"nodes": [{"name": "community-pack"}, {"name": "bug"}]},
                        },
                        {
                            "number": 6,
                            "title": "Repeat reads are answered from a snapshot",
                            "state": "CLOSED",
                            "body": "No blockers.\n",
                            "labels": {"nodes": []},
                        },
                    ]
                }
            }
        }
    }
)

# The REST body for PR #11: what the same caller would have projected before the aggregate existed. The transport-blindness
# case below asserts both answers carry the same projection.
REST_PRS = json.dumps(
    [
        {
            "number": 11,
            "title": "Play: the pad",
            "body": "Refs #1",
            "draft": True,
            "head": {"ref": "gui/11", "sha": "1a2b3c4d"},
            "labels": [{"name": "bug"}, {"name": "core"}],
        }
    ]
)

# One field of one PR failed to compute: GitHub answers the rest of the query with that field null and names the failure
# in `errors`. Rest-shaped: it is GitHub's own envelope.
PARTIAL_ENVELOPE = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [
                        {
                            "number": 11,
                            "title": "Play: the pad",
                            "body": "Refs #1",
                            "isDraft": False,
                            "headRefName": "gui/11",
                            "headRefOid": "1a2b3c4d",
                            "labels": {"nodes": [{"name": "bug"}]},
                            "reviewThreads": None,
                            "commits": {"nodes": []},
                        },
                        {
                            "number": 12,
                            "title": "Docs: the register",
                            "body": "",
                            "isDraft": False,
                            "headRefName": "gui/12",
                            "headRefOid": "5e6f7a8b",
                            "labels": {"nodes": []},
                            "reviewThreads": {"totalCount": 7},
                            "commits": {"nodes": []},
                        },
                    ]
                }
            }
        },
        "errors": [
            {
                "message": "Something went wrong while computing this field",
                "path": ["repository", "pullRequests", "nodes", 0, "reviewThreads"],
            }
        ],
    }
)

RATE_LIMITED = json.dumps(
    {
        "errors": [
            {
                "type": "RATE_LIMITED",
                "message": "API rate limit exceeded for user ID 122891372. Please include the request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A and timestamp 2026-10-12 05:12:10 UTC.",
            }
        ]
    }
)


def _load():
    """Load the module from its path: it ships as a script, not as an installed package."""
    spec = importlib.util.spec_from_file_location("ghread", MODULE)
    assert spec and spec.loader, f"cannot load {MODULE}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ghread"] = mod
    spec.loader.exec_module(mod)
    return mod


ghread = _load()


def stub(rc: int, body: str = "", detail: str = ""):
    """A transport that answers from a literal instead of from GitHub; it records the routes it was asked for."""
    seen: list[str] = []

    def transport(route: str):
        seen.append(route)
        return ghread.Transport(rc=rc, body=body, detail=detail)

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


# --- the open PRs of a repository, in one request -------------------------------------------------------------------


def test_the_open_prs_of_a_repository_arrive_in_one_request():
    t = stub(0, PRS_ENVELOPE)
    out = ghread.read(PRS_ROUTE, transport=t)
    assert out.is_answer, out.line()
    assert len(t.seen) == 1, "the repository's open PRs are one request, not one per PR"

    prs = out.answer
    assert [p["number"] for p in prs] == [11, 12], "every open PR of the answer is carried, in GitHub's order"
    assert prs[0]["title"] == "Play: the pad" and prs[0]["body"] == "Refs #1", "the shape a caller projects from is REST's"


def test_the_pr_fact_carries_the_head_ref_the_draft_flag_and_the_labels():
    pr = ghread.read(PRS_ROUTE, transport=stub(0, PRS_ENVELOPE)).answer[0]
    assert pr["head"]["ref"] == "gui/11"
    assert pr["head"]["sha"] == "1a2b3c4d"
    assert pr["draft"] is True, "the draft flag is GitHub's `isDraft`, on a REST route's name"
    assert [label["name"] for label in pr["labels"]] == ["bug", "core"]
    assert pr["state"] == "open", "the fact's state vocabulary is REST's own"


def test_the_pr_fact_carries_the_review_thread_count_and_the_check_rollup():
    pr = ghread.read(PRS_ROUTE, transport=stub(0, PRS_ENVELOPE)).answer[0]
    assert pr["review_threads"] == 3
    assert pr["check_rollup"] == {"state": "failure", "total": 4, "failed": 1, "pending": 1}


def test_a_pr_without_checks_or_threads_is_still_an_answer():
    pr = ghread.read(PRS_ROUTE, transport=stub(0, PRS_ENVELOPE)).answer[1]
    assert pr["review_threads"] == 0
    assert pr["check_rollup"] == {"state": None, "total": 0, "failed": 0, "pending": 0}


# --- the open issues of a repository, in one request ------------------------------------------------------------------


def test_the_open_issues_of_a_repository_arrive_in_one_request():
    t = stub(0, ISSUES_ENVELOPE)
    out = ghread.read(ISSUES_ROUTE, transport=t)
    assert out.is_answer, out.line()
    assert len(t.seen) == 1, "the repository's open issues are one request, not one per issue"
    assert [i["number"] for i in out.answer] == [5, 6]


def test_the_issue_fact_carries_the_labels_and_the_blocking_edges():
    issue = ghread.read(ISSUES_ROUTE, transport=stub(0, ISSUES_ENVELOPE)).answer[0]
    assert [label["name"] for label in issue["labels"]] == ["community-pack", "bug"]
    assert issue["blocked_by"] == [3, 4], "the blocking edges are read off the body, not asked for one issue at a time"
    assert issue["state"] == "open"


def test_the_blocking_edges_are_read_as_the_spec_reads_them():
    """A list, not prose: a trailing line that merely names another issue is not an edge (#1803)."""
    issue = ghread.read(ISSUES_ROUTE, transport=stub(0, ISSUES_ENVELOPE)).answer[0]
    assert 99 not in issue["blocked_by"], "the `## Notes` section is not the `## Blocked by` section"


# --- a caller cannot tell which transport answered it -----------------------------------------------------------------


def _caller_projection(answer):
    """What a caller does with the fact: plain dict lookups over the fields the loop reads. It never asks the protocol."""
    return [(pr["number"], pr["title"], pr["body"], pr["draft"], pr["head"]["ref"], tuple(label["name"] for label in pr["labels"])) for pr in answer]


def test_a_caller_cannot_tell_which_transport_answered_it():
    """The same projection over both answers: PR #11 is in both, and the caller reads it the same way either time."""
    rest = ghread.read(PRS_ROUTE, transport=stub(0, REST_PRS)).answer
    graphql = ghread.read(PRS_ROUTE, transport=stub(0, PRS_ENVELOPE)).answer
    assert _caller_projection(rest) == _caller_projection(graphql)[:1]


# --- a partial failure does not discard the rest of the answer --------------------------------------------------------


def test_a_partial_failure_of_one_field_does_not_discard_the_rest_of_the_answer():
    out = ghread.read(PRS_ROUTE, transport=stub(0, PARTIAL_ENVELOPE))
    assert out.is_answer, out.line()
    assert len(out.answer) == 2, "the answer is kept: one field of one item failed, the query did not"
    failed, intact = out.answer
    assert failed["review_threads"] is None
    assert failed["number"] == 11 and failed["title"] == "Play: the pad" and failed["head"]["ref"] == "gui/11"
    assert [label["name"] for label in failed["labels"]] == ["bug"]
    assert intact["review_threads"] == 7, "the sibling the failure did not touch keeps its own values"


def test_a_partial_failure_is_named_on_the_read():
    out = ghread.read(PRS_ROUTE, transport=stub(0, PARTIAL_ENVELOPE))
    assert "Something went wrong while computing this field" in " ".join(out.partial), "the failed field is reported, not swallowed"


# --- a refusal is still a refusal -------------------------------------------------------------------------------------


def test_a_refused_query_is_a_refusal_and_never_an_answer():
    out = ghread.read(PRS_ROUTE, transport=stub(1, RATE_LIMITED))
    assert out.is_refused, out.line()
    assert out.answer is None
    assert "rate limit exceeded" in out.message, "GitHub's own message travels"
    assert "API rate limit exceeded" in out.line()


def test_a_refused_query_names_the_request_id_from_the_response_header():
    body = "HTTP/2.0 200 OK\r\nX-GitHub-Request-Id: 9F1A:2B3C:4D5E:6F70:11223344\r\n\r\n" + RATE_LIMITED
    out = ghread.read(PRS_ROUTE, transport=stub(1, body))
    assert out.is_refused
    assert out.request_id == "9F1A:2B3C:4D5E:6F70:11223344"


# --- the same fact as a process, which is how the loop's scripts reach it ---------------------------------------------


def _cli(tmp_path: Path, body: str, rc: int, route: str = PRS_ROUTE):
    """Put a stub `gh` on PATH and run the module as a process. No network, no real gh."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    gh = b / "gh"
    gh.write_text(f"#!/bin/bash\nprintf %s {json.dumps(body)}\nexit {rc}\n")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}")
    return subprocess.run([sys.executable, str(MODULE), route], capture_output=True, text=True, env=env, check=False)


def test_the_cli_answers_an_aggregate_route_with_the_fact(tmp_path):
    r = _cli(tmp_path, PRS_ENVELOPE, 0)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)[0]["head"]["ref"] == "gui/11", "a shell caller gets the fact, not GitHub's envelope"


def test_the_cli_names_a_refused_query(tmp_path):
    r = _cli(tmp_path, RATE_LIMITED, 1)
    assert r.returncode == 2, "the refusal keeps its own exit code"
    assert "rate limit exceeded" in r.stdout
