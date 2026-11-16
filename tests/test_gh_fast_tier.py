"""The fast-tier CI verdict of one sha, read from its check runs through REST (#1926).

A landing reads one verdict per sha: pass only when the three fast-tier checks each concluded success, fail when one
concluded failure, and unproven for anything else -- a cancelled, timed-out, missing or unfinished check, or a read that
GitHub never answered. Every case drives a stub transport, so no case touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "contrib/squad-goal/scripts/ghread.py"
SHA = "3f9c2a1d8e7b6054a3c2d1e0f9a8b7c6d5e4f3a2"
ROUTE = f"repos/sbihaiko/agent-squad/commits/{SHA}/check-runs?per_page=100"
REFUSAL_BODY = json.dumps({"message": "API rate limit exceeded", "status": "403"})


def _load():
    spec = importlib.util.spec_from_file_location("ghread", MODULE)
    assert spec and spec.loader, f"cannot load {MODULE}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ghread"] = mod
    spec.loader.exec_module(mod)
    return mod


ghread = _load()


def check_run(name: str, conclusion: str | None, run_id: int = 101, check_id: int = 1, status: str = "completed") -> dict:
    return {
        "id": check_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://github.com/sbihaiko/agent-squad/actions/runs/{run_id}/job/{check_id}",
    }


def answer(*runs: dict) -> ghread.Transport:
    return ghread.Transport(rc=0, body=json.dumps({"total_count": len(runs), "check_runs": list(runs)}))


def fast_tier(*runs: dict):
    return ghread.fast_tier_verdict(SHA, transport=lambda route: answer(*runs))


def test_three_successful_checks_pass_and_name_the_run():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "success", check_id=2),
        check_run("fast-battery", "success", check_id=3),
    )
    assert (out.verdict, out.run_id) == ("pass", 101)


def test_a_failed_fast_battery_is_a_fail():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "success", check_id=2),
        check_run("fast-battery", "failure", check_id=3),
    )
    assert out.verdict == "fail"


def test_a_failure_outranks_an_unfinished_sibling():
    out = fast_tier(
        check_run("ruff", "failure", check_id=1),
        check_run("mypy", "cancelled", check_id=2),
        check_run("fast-battery", "success", check_id=3),
    )
    assert out.verdict == "fail"


def test_a_cancelled_check_is_unproven():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "success", check_id=2),
        check_run("fast-battery", "cancelled", check_id=3),
    )
    assert out.verdict == "unproven"


def test_a_timed_out_check_is_unproven():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "timed_out", check_id=2),
        check_run("fast-battery", "success", check_id=3),
    )
    assert out.verdict == "unproven"


def test_a_missing_fast_tier_check_is_unproven():
    out = fast_tier(check_run("ruff", "success", check_id=1), check_run("mypy", "success", check_id=2))
    assert out.verdict == "unproven"


def test_a_check_that_has_not_started_is_unproven():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "success", check_id=2),
        check_run("fast-battery", None, status="queued", check_id=3),
    )
    assert out.verdict == "unproven"


def test_a_check_outside_the_fast_tier_does_not_count():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "success", check_id=2),
        check_run("fast-battery", "success", check_id=3),
        check_run("fast-battery-311", "failure", check_id=4),
    )
    assert out.verdict == "pass"


def test_a_rerun_that_passed_replaces_the_failed_run_of_the_same_check():
    out = fast_tier(
        check_run("ruff", "success", check_id=1),
        check_run("mypy", "success", check_id=2),
        check_run("fast-battery", "failure", check_id=3, run_id=100),
        check_run("fast-battery", "success", check_id=9, run_id=101),
    )
    assert (out.verdict, out.run_id) == ("pass", 101)


def test_a_refused_read_is_unproven_and_names_no_run():
    refused = ghread.fast_tier_verdict(SHA, transport=lambda route: ghread.Transport(rc=1, body=REFUSAL_BODY))
    assert (refused.verdict, refused.run_id) == ("unproven", None)


def test_the_reader_asks_one_check_runs_route_for_the_sha():
    seen: list[str] = []

    def transport(route: str) -> ghread.Transport:
        seen.append(route)
        return answer()

    ghread.fast_tier_verdict(SHA, transport=transport)
    assert seen == [ROUTE]
