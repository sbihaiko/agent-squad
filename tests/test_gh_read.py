"""One interface owns every GitHub read and write the loop makes, and a refusal is never an answer (#1893, ADR-0277).

The loop reads GitHub from shell scripts through `gh api`, which writes the error body to *stdout* and exits non-zero:
on 2026-10-12 a rate-limit body became the open-PR list and the cycle read each word of the JSON message as a PR number.
The remedy is a contract with three distinguishable outcomes -- an answer, a refusal carrying GitHub's own message and
request id, and a failure before GitHub reached a verdict -- so that a `403` can never again be mistaken for an empty
list. The whole point of the seam is that it is *injectable*: every case below drives a stub transport and no case
touches the network. The CLI cases drive the same contract through a stub `gh` on PATH, which is how a shell script
reaches the module.

The same three outcomes own the writes (revision 2026-10-12): on that afternoon the pass moved 16 board cards and closed
16 issues, and a `403` on any of those writes would have been a silent no-op -- the loop believed the card moved because
nothing counted the refusal as a failure. A write therefore answers `done`, `refused` or `failed`, a refusal is reported
with GitHub's message and request id, and only `done` may be counted as a write that happened.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "contrib/squad-goal/scripts/ghread.py"

# The refusal GitHub actually answered with on 2026-10-12: its own message, carrying its own request id, and the HTTP
# status in the body. This is the body that used to become the open-PR list.
RATE_LIMIT_MESSAGE = (
    "API rate limit exceeded for user ID 122891372. If you reach out to GitHub Support for help, please include the "
    "request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A and timestamp 2026-10-12 05:12:10 UTC. For more on scraping "
    "GitHub and how it may affect your rights, please review our Terms of Service "
    "(https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)"
)
REQUEST_ID = "06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A"
REFUSAL_BODY = json.dumps(
    {
        "message": RATE_LIMIT_MESSAGE,
        "documentation_url": "https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#rate-limiting",
        "status": "403",
    }
)


def _load():
    """Load the module from its path: it ships as a script, not as an installed package.

    The module goes into `sys.modules` before it runs: a `@dataclass` looks its own module up there while it is being
    built, on 3.14 as well as before it.
    """
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


# --- outcome (a): an answer -----------------------------------------------------------------------------------------


def test_an_answer_is_an_answer():
    t = stub(0, '[{"number": 7}]')
    out = ghread.read("repos/o/r/pulls?state=open&per_page=100", transport=t)
    assert out.kind == "answer"
    assert out.answer == [{"number": 7}]
    assert t.seen == ["repos/o/r/pulls?state=open&per_page=100"], "the read must ask the transport for the route it was given"


def test_an_empty_answer_is_still_an_answer_and_not_a_failure():
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(0, ""))
    assert out.kind == "answer"
    assert out.answer is None


# --- outcome (b): a refusal -----------------------------------------------------------------------------------------


def test_a_403_is_a_refusal_and_never_an_answer():
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(1, REFUSAL_BODY))
    assert out.kind == "refused"
    assert out.answer is None


def test_a_403_is_never_an_empty_list():
    """The heart of the regression: nothing about a refusal may look like `[]` to the caller."""
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(1, REFUSAL_BODY))
    assert out.answer != []
    assert out.answer is None


def test_the_refusal_names_githubs_message_and_request_id():
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(1, REFUSAL_BODY))
    assert out.message == RATE_LIMIT_MESSAGE
    assert out.request_id == REQUEST_ID
    assert RATE_LIMIT_MESSAGE in out.line(), "the line a caller logs must name GitHub's own message"
    assert REQUEST_ID in out.line(), "the line a caller logs must name the request id"


def test_a_403_body_is_a_refusal_even_when_the_transport_reports_success():
    """GitHub's own error shape decides, not the exit code alone: a transport that swallowed the status is still a refusal."""
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(0, REFUSAL_BODY))
    assert out.kind == "refused"
    assert out.answer is None
    assert out.message == RATE_LIMIT_MESSAGE


def test_an_error_shape_without_a_request_id_still_names_one_field():
    body = json.dumps({"message": "Bad credentials", "status": "401"})
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(1, body))
    assert out.kind == "refused"
    assert out.message == "Bad credentials"
    assert out.request_id == ""
    assert "Bad credentials" in out.line()


def test_the_request_id_comes_from_the_response_header_when_github_sends_one():
    body = "HTTP/2.0 403 Forbidden\r\nX-GitHub-Request-Id: 9F1A:2B3C:4D5E:6F70:11223344\r\n\r\n" + REFUSAL_BODY
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(1, body))
    assert out.kind == "refused"
    assert out.request_id == "9F1A:2B3C:4D5E:6F70:11223344"
    assert out.answer is None


# --- outcome (c): a failure before GitHub reached a verdict ---------------------------------------------------------


def test_a_failure_before_github_answered_is_the_third_outcome():
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(1, "", "gh: command not found"))
    assert out.kind == "failed"
    assert out.answer is None
    assert out.message == "", "a pre-verdict failure carries no message from GitHub"
    assert "gh: command not found" in out.line()


def test_a_200_that_is_not_json_is_a_failure_and_not_data():
    out = ghread.read("repos/o/r/pulls?state=open", transport=stub(0, "<html>proxy error</html>"))
    assert out.kind == "failed"
    assert out.answer is None


def test_the_three_outcomes_are_distinguishable_from_one_another():
    kinds = {
        ghread.read("repos/o/r/pulls?state=open", transport=stub(0, "[]")).kind,
        ghread.read("repos/o/r/pulls?state=open", transport=stub(1, REFUSAL_BODY)).kind,
        ghread.read("repos/o/r/pulls?state=open", transport=stub(1, "", "boom")).kind,
    }
    assert kinds == {"answer", "refused", "failed"}


# --- the same contract as a process, which is how a shell script reaches it ------------------------------------------


def _cli(tmp_path: Path, body: str, rc: int):
    """Put a stub `gh` on PATH and run the module as a process. No network, no real gh."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    gh = b / "gh"
    gh.write_text(f'#!/bin/bash\nprintf %s {json.dumps(body)}\nexit {rc}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}")
    return subprocess.run(
        [sys.executable, str(MODULE), "repos/o/r/pulls?state=open&per_page=100"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_cli_prints_the_answer_and_exits_zero(tmp_path):
    r = _cli(tmp_path, '[{"number": 7}]', 0)
    assert r.returncode == 0
    assert json.loads(r.stdout) == [{"number": 7}]


def test_the_cli_names_the_refusal_and_never_prints_a_list(tmp_path):
    r = _cli(tmp_path, REFUSAL_BODY, 1)
    assert r.returncode == 2, "a refusal has its own exit code, distinct from both the answer and a pre-verdict failure"
    assert RATE_LIMIT_MESSAGE in r.stdout and REQUEST_ID in r.stdout
    try:
        json.loads(r.stdout)
    except ValueError:
        pass
    else:
        raise AssertionError(f"a refusal must not be parseable as data: {r.stdout!r}")


def test_the_cli_reports_a_pre_verdict_failure_with_its_own_exit_code(tmp_path):
    r = _cli(tmp_path, "", 127)
    assert r.returncode == 1
    assert "no verdict" in r.stdout


# --- the same three outcomes own the writes (revision 2026-10-12) -----------------------------------------------------
# The loop's writes are the card move (GraphQL) and the close (REST PATCH). Both used to be a bare `gh api ...` whose
# failure nobody read: the command exits non-zero, the loop printed nothing and went on counting the card as moved. The
# write seam hands the transport the argv `gh api` would have run, because that is what a stub has to see to prove the
# method and the fields reached GitHub -- a refusal is not a fact about the route alone.


def write_stub(rc: int, body: str = "", detail: str = ""):
    """A write transport that answers from a literal instead of from GitHub; it records the argv it was asked to run."""
    seen: list[list[str]] = []

    def transport(argv: list[str]):
        seen.append(list(argv))
        return ghread.Transport(rc=rc, body=body, detail=detail)

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


# The card move is a GraphQL mutation, which is the write that moved those 16 cards.
CARD_MOVE = "mutation { updateProjectV2ItemFieldValue(input: {itemId: \"x\", fieldId: \"y\"}) { projectV2Item { id } } }"
CARD_MOVE_ROUTE = "graphql"


def test_a_refused_status_move_is_a_refusal_and_never_done():
    t = write_stub(1, REFUSAL_BODY)
    out = ghread.write(CARD_MOVE_ROUTE, fields={"query": CARD_MOVE}, transport=t)
    assert out.kind == "refused"
    assert out.is_done is False, "a refused status move must never be counted as a move"
    assert out.answer is None
    assert out.message == RATE_LIMIT_MESSAGE, "the refusal carries GitHub's own message"
    assert out.request_id == REQUEST_ID, "the refusal carries GitHub's own request id"
    assert RATE_LIMIT_MESSAGE in out.line() and REQUEST_ID in out.line()
    assert t.seen == [["--method", "POST", CARD_MOVE_ROUTE, "-f", f"query={CARD_MOVE}"]], (
        "the move reaches the transport as the method, the route and the fields -- not as a route alone"
    )


def test_a_refused_close_is_reported_and_never_counted_done():
    t = write_stub(1, REFUSAL_BODY)
    out = ghread.write("repos/o/r/issues/3", method="PATCH", fields={"state": "closed"}, transport=t)
    assert out.kind == "refused"
    assert out.is_done is False, "a refused close must never be counted as a close"
    argv = t.seen[0]
    assert argv[:2] == ["--method", "PATCH"], f"the close is a PATCH; the transport saw {argv!r}"
    assert "repos/o/r/issues/3" in argv and "state=closed" in argv, f"the close reached the transport as {argv!r}"
    assert RATE_LIMIT_MESSAGE in out.line() and REQUEST_ID in out.line()


def test_a_write_github_accepted_is_done():
    t = write_stub(0, '{"id": 7, "state": "closed"}')
    out = ghread.write("repos/o/r/issues/3", method="PATCH", fields={"state": "closed"}, transport=t)
    assert out.kind == "done"
    assert out.is_done is True
    assert out.answer == {"id": 7, "state": "closed"}
    assert out.message == "", "a write GitHub accepted carries no refusal message"


def test_a_write_github_answered_with_no_body_is_still_done():
    """`204 No Content` is a verdict: the write happened. It is not the third outcome."""
    out = ghread.write("repos/o/r/issues/3/comments", fields={"body": "hi"}, transport=write_stub(0, ""))
    assert out.kind == "done"
    assert out.is_done is True


def test_a_write_that_never_reached_github_is_the_third_outcome():
    out = ghread.write(
        "repos/o/r/issues/3", method="PATCH", fields={"state": "closed"}, transport=write_stub(1, "", "gh: command not found")
    )
    assert out.kind == "failed"
    assert out.is_done is False
    assert out.message == "", "a pre-verdict failure carries no message from GitHub"
    assert "gh: command not found" in out.line()


def test_a_403_write_body_is_a_refusal_even_when_the_transport_reports_success():
    """GitHub's own error shape decides, not the exit code alone -- the same rule the reads live by."""
    out = ghread.write("repos/o/r/issues/3", method="PATCH", fields={"state": "closed"}, transport=write_stub(0, REFUSAL_BODY))
    assert out.kind == "refused"
    assert out.is_done is False
    assert out.message == RATE_LIMIT_MESSAGE


def test_the_three_write_outcomes_are_distinguishable_from_one_another():
    kinds = {
        ghread.write("repos/o/r/issues/3", transport=write_stub(0, '{"id": 7}')).kind,
        ghread.write("repos/o/r/issues/3", transport=write_stub(1, REFUSAL_BODY)).kind,
        ghread.write("repos/o/r/issues/3", transport=write_stub(1, "", "boom")).kind,
    }
    assert kinds == {"done", "refused", "failed"}


def test_a_refused_write_is_never_counted_as_a_move_the_loop_made():
    """The count the loop keeps: 16 cards moved that afternoon, and a 403 must not be one of them."""
    outcomes = [
        ghread.write("repos/o/r/issues/3", transport=write_stub(0, '{"id": 7}')),
        ghread.write("repos/o/r/issues/3", transport=write_stub(1, REFUSAL_BODY)),
        ghread.write("repos/o/r/issues/3", transport=write_stub(1, "", "boom")),
    ]
    assert [o.is_done for o in outcomes] == [True, False, False]
    assert sum(1 for o in outcomes if o.is_done) == 1


# --- the write contract as a process, which is how a shell script reaches it ------------------------------------------


def _gh_recorder(tmp_path: Path, body: str, rc: int):
    """Put a stub `gh` on PATH that records its own argv, and hand back the env to run the module with. No network."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    rec = tmp_path / "gh.argv"
    gh = b / "gh"
    gh.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {rec}\nprintf %s {json.dumps(body)}\nexit {rc}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    return dict(os.environ, PATH=f"{b}:{os.environ['PATH']}"), rec


def test_the_cli_moves_a_card_and_exits_zero_when_github_accepts(tmp_path):
    env, rec = _gh_recorder(tmp_path, '{"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "x"}}}}', 0)
    r = subprocess.run(
        [sys.executable, str(MODULE), "--method", "POST", CARD_MOVE_ROUTE, "-f", f"query={CARD_MOVE}"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, f"a done write exits 0: {r.stdout!r} {r.stderr!r}"
    assert "updateProjectV2ItemFieldValue" in r.stdout, "the answer is on stdout for the caller's jq"
    assert "POST" in rec.read_text() and CARD_MOVE in rec.read_text(), f"the move reached gh as {rec.read_text()!r}"


def test_the_cli_reports_a_refused_write_and_never_exits_zero(tmp_path):
    env, rec = _gh_recorder(tmp_path, REFUSAL_BODY, 1)
    r = subprocess.run(
        [sys.executable, str(MODULE), "--method", "PATCH", "repos/o/r/issues/3", "-f", "state=closed"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 2, f"a refusal has its own exit code, never 0: {r.stdout!r}"
    assert RATE_LIMIT_MESSAGE in r.stdout and REQUEST_ID in r.stdout, f"the refusal names GitHub's words: {r.stdout!r}"
    assert "state=closed" in rec.read_text(), "the refused close still reached gh as the write it was"


# --- one GraphQL read no aggregate route names, through the same door (#919) ----------------------------------------

def test_a_graphql_query_is_the_argv_the_door_runs():
    """The rendered issue body is one GraphQL read; the door builds the argv, so the page's reader carries no `gh` call."""
    argv = ghread.graphql_argv("query($number:Int!){x}", {"number": 7})
    assert argv[:3] == ["graphql", "-f", "query=query($number:Int!){x}"], argv
    assert argv[3:] == ["-F", "number=7"], f"a variable goes through `-F`: {argv}"


def test_a_graphql_read_is_an_answer_or_a_refusal_like_every_other_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    body = '{"data":{"repository":{"issueOrPullRequest":{"bodyHTML":"<p>x</p>"}}}}'
    out = ghread.graphql_read("query{x}", transport=stub(0, body))
    assert out.kind == "answer" and out.answer["data"]["repository"]["issueOrPullRequest"]["bodyHTML"] == "<p>x</p>"
    refused = ghread.graphql_read("query{x}", transport=stub(0, '{"errors":[{"message":"API rate limit exceeded"}]}'))
    assert refused.kind == "refused" and "rate limit" in refused.message, refused


def test_a_graphql_read_is_logged_with_its_outcome_and_its_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setenv("SG_GH_PASS", "board")
    ghread.graphql_read("query{x}", transport=stub(0, '{"data":{"x":1}}'))
    ghread.graphql_read("query{x}", transport=stub(0, '{"errors":[{"message":"API rate limit exceeded"}]}'))
    calls = [ghread.parse_call(line) for line in (tmp_path / "api.log").read_text().splitlines()]
    assert [(c.transport, c.route, c.cache, c.outcome, c.pass_name) for c in calls] == [
        ("GraphQL", "graphql", "miss", "answer", "board"),
        ("GraphQL", "graphql", "miss", "refused", "board"),
    ], calls


def test_a_graphql_read_names_the_pass_its_caller_gives(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.delenv("SG_GH_PASS", raising=False)
    ghread.graphql_read("query{x}", transport=stub(0, '{"data":{"x":1}}'), pass_name="hub")
    assert ghread.parse_call((tmp_path / "api.log").read_text().splitlines()[0]).pass_name == "hub"


def test_a_graphql_read_that_never_reaches_github_is_logged_as_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setenv("SG_GH_PASS", "board")

    def gone(route):
        raise OSError("no gh on PATH")

    with pytest.raises(OSError):
        ghread.graphql_read("query{x}", transport=gone)
    assert ghread.parse_call((tmp_path / "api.log").read_text().splitlines()[0]).outcome == "failed"


def test_an_integer_write_field_is_sent_typed_so_github_reads_a_number():
    argv = ghread.write_argv("repos/o/r/issues/3/dependencies/blocked_by", "POST", {"issue_id": 42, "note": "x"})
    assert argv == ["--method", "POST", "repos/o/r/issues/3/dependencies/blocked_by", "-F", "issue_id=42", "-f", "note=x"]
