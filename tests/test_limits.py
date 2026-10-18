"""GitHub issue #572: a model refused for its session limit writes the account's limited marker.

The loop already skips a model while its provider's shared marker is ahead of now: `codex-limited` closes every gpt-
model, `agy-limited` every gemini- one, and the launcher turns them into the run's `spent_models`. But the engine only
recorded `model_refused`; nothing wrote the marker, so the next launch named the same spent model again, and each try
cost minutes of work that ended on the provider's refusal. A refusal that names a reset time now writes the marker
until that time. A refusal with no readable reset time writes nothing, so the chain behaves as it did before.
"""
import ast
import pathlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from squad import limits
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from tests.gitrepo import sibling_repo
from tests.helpers import router

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "contrib/squad-goal/scripts/launch.py"

# The subscription's own refusal, as the CLI prints it: no HTTP status, and the reset it names in its own words.
SESSION_LIMIT = ("ResultError: Claude Code returned an error result: "
                 "You've hit your session limit · resets 6:49am (America/Sao_Paulo) (exit code: 1)")
NO_RESET = "ResultError: Claude Code returned an error result: You've hit your session limit (exit code: 1)"

TERRA = "claude-gpt-5.6-terra"
# 05:57 UTC on the day the loop named terra again; the refusal says 06:49, so the marker is that same morning.
NOW = datetime(2026, 10, 18, 5, 57, tzinfo=timezone.utc).timestamp()
RESET = datetime(2026, 10, 18, 6, 49, tzinfo=timezone.utc).timestamp()

REQUEST = {"prompt": "Move the counter into its own module.", "skill": "tdd", "model": TERRA, "effort": "medium",
           "tools": ["Read", "Edit"], "stalls": 4, "spend": 1.0, "deadline": 100, "workspace": "worktrees/child"}
SPAWNS = {"output": {"spawns": [REQUEST]}, "spend": 0.1}
CHILD = {"output": {"diff": {"files": [{"path": "a.py"}]}, "ac_commands": ["true"]}, "spend": 0.2, "turns": 3}


def _fn(path, name, **ns_extra):
    """One function of a script, compiled alone: importing launch.py would start a run."""
    tree = ast.parse(path.read_text())
    keep = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert keep, f"{path.name} no longer ships {name}"
    ns = dict(ns_extra)
    exec(compile(ast.Module(body=keep[:1], type_ignores=[]), str(path), "exec"), ns)
    return ns[name]


def _spent_models():
    """The launcher's own read of the shared markers: the models a run must not start on."""
    return _fn(LAUNCH, "_spent_models", time=time, pathlib=pathlib,
               ALIASES={"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5-20251001"},
               SPENT_MARKERS=(("gpt-", "codex-limited"), ("gemini-", "agy-limited")),
               provkey=SimpleNamespace(closed=lambda m: False),
               _run_models=_fn(LAUNCH, "_run_models"))


def engine(tmp_path, script):
    d = router()
    d["run"]["deadline"] = 7200
    return Engine(from_doc(d), ScriptedAdapter(script), tmp_path, target=sibling_repo(tmp_path))


def test_a_session_limit_names_the_epoch_of_its_reset():
    assert limits.reset_epoch(SESSION_LIMIT, now=NOW) == RESET


def test_a_session_limit_without_a_zone_reads_the_local_clock():
    at = datetime.fromtimestamp(NOW).replace(hour=6, minute=49, second=0, microsecond=0)
    if at.timestamp() <= NOW:
        at += timedelta(days=1)
    assert limits.reset_epoch("You've hit your session limit · resets 6:49am", now=NOW) == int(at.timestamp())


def test_a_reset_time_already_past_is_the_next_time_that_clock_comes_round():
    later = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc).timestamp()   # the refusal came after its own reset clock
    assert limits.reset_epoch(SESSION_LIMIT, now=later) == datetime(2026, 10, 19, 6, 49, tzinfo=timezone.utc).timestamp()


def test_a_refusal_with_no_reset_time_names_no_epoch():
    assert limits.reset_epoch(NO_RESET, now=NOW) is None


def test_the_marker_names_the_provider_account_never_the_model():
    assert limits.marker(TERRA) == "codex-limited"                       # `claude-gpt-` is the gateway's own prefix
    assert limits.marker("gpt-5.6-terra") == "codex-limited"
    assert limits.marker("claude-gemini-3.8-flash") == "agy-limited"
    assert limits.marker("claude-sonnet-5-5") is None                    # Anthropic answers no shared marker


def test_a_refusal_with_a_reset_time_writes_the_marker_until_it(tmp_path):
    path = limits.write(TERRA, SESSION_LIMIT, shared=tmp_path, now=NOW)
    assert path == tmp_path / "codex-limited"
    assert int(path.read_text()) == RESET                                # the same epoch format review.sh writes


def test_a_refusal_with_no_reset_time_writes_no_marker(tmp_path):
    assert limits.write(TERRA, NO_RESET, shared=tmp_path, now=NOW) is None
    assert not list(tmp_path.iterdir())


def test_a_model_with_no_provider_marker_writes_nothing(tmp_path):
    assert limits.write("claude-sonnet-5-5", SESSION_LIMIT, shared=tmp_path, now=NOW) is None
    assert not list(tmp_path.iterdir())


def test_no_shared_directory_writes_no_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("SG_SHARED", raising=False)
    assert limits.write(TERRA, SESSION_LIMIT) is None


@pytest.mark.anyio
async def test_a_child_refused_for_its_session_limit_writes_the_marker_and_the_next_launch_skips_it(tmp_path, monkeypatch):
    """The refusal is recorded as before, and the shared marker it names is on disk before the next launch resolves
    its chain: the launcher's own resolver then reads terra as spent."""
    shared = tmp_path / "shared"
    monkeypatch.setenv("SG_SHARED", str(shared))
    eng = engine(tmp_path, {"router": SPAWNS, "route.router.1": [{"error": SESSION_LIMIT}, CHILD]})
    v = await eng.run()
    assert v.verdict == "completed"
    marker = shared / "codex-limited"
    assert marker.is_file()
    soon = time.time()
    assert soon < int(marker.read_text()) <= soon + 24 * 3600
    # the next launch resolves the graph's models through this marker, and terra is left out of its chain
    assert TERRA in _spent_models()(router(), shared, now=soon)
