"""One interface owns every GitHub read, and a refusal is never an answer (#1893, ADR-0277).

The loop reads GitHub from shell scripts through `gh api`, which writes the error body to *stdout* and exits non-zero:
on 2026-10-12 a rate-limit body became the open-PR list and the cycle read each word of the JSON message as a PR number.
The remedy is a contract with three distinguishable outcomes -- an answer, a refusal carrying GitHub's own message and
request id, and a failure before GitHub reached a verdict -- so that a `403` can never again be mistaken for an empty
list. The whole point of the seam is that it is *injectable*: every case below drives a stub transport and no case
touches the network. The two CLI cases drive the same contract through a stub `gh` on PATH, which is how a shell script
reaches the module.
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
