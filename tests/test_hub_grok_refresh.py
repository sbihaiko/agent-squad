"""The SuperGrok gauge refreshes itself: when the kept reading is stale or its period has ended, the quota probe starts
the grok TUI briefly so the CLI writes a new credit line, at most once an hour and never while one already runs (#1530).

The hub hosts no terminal and opens no pseudo-terminal on its own path (#2492): the TUI start lives in
`grokrefresh.sh`, which the hub runs as a child. The script is what these tests pin, with a stub `grok`.
"""
import importlib.util
import os
import pathlib
import subprocess
import tempfile
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
REFRESH = SCRIPTS / "grokrefresh.sh"


@pytest.fixture
def env(tmp_path):
    """A home with a stub `grok` on PATH that writes one credit line into `$HOME/.grok/logs/unified.jsonl`."""
    home = tmp_path / "home"
    (home / ".grok" / "logs").mkdir(parents=True)
    log = home / ".grok" / "logs" / "unified.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    grok = bin_dir / "grok"
    grok.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo started >> {home}/grok-starts
        printf '%s\\n' '{{"ts":"2026-11-07T12:00:00Z","ctx":{{"config":{{"currentPeriod":{{"start":"2026-11-07T00:00:00Z","end":"2026-11-14T00:00:00Z"}},"creditUsagePercent":6.0}}}}}}' >> {log}
        sleep 0.2
    """))
    grok.chmod(0o755)
    e = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for k in ("SG_STATE", "SG_REPO", "SG_SQUAD"):
        e.pop(k, None)
    return e, home, log


def run(env, *args):
    e, home, log = env
    return subprocess.run(["bash", str(REFRESH), *args], env=e, capture_output=True, text=True, timeout=120)


@pytest.mark.macos_bound   # starts a TUI in a pty, which only the loop host has (ADR-0139)
def test_a_stale_period_starts_the_tui_once_and_writes_a_credit_line(env):
    out = run(env, "--ttl", "3600")
    e, home, log = env
    assert out.returncode == 0, out.stdout + out.stderr
    assert (home / "grok-starts").exists(), out.stdout + out.stderr
    assert log.exists() and "creditUsagePercent" in log.read_text(), out.stdout + out.stderr


@pytest.mark.macos_bound   # starts a TUI in a pty, which only the loop host has (ADR-0139)
def test_a_second_start_within_the_ttl_writes_nothing(env):
    assert run(env, "--ttl", "3600").returncode == 0
    second = run(env, "--ttl", "3600")
    e, home, log = env
    assert "skip" in second.stdout.lower(), second.stdout + second.stderr
    assert (home / "grok-starts").read_text().count("started") == 1


@pytest.mark.macos_bound   # starts a TUI in a pty, which only the loop host has (ADR-0139)
def test_a_running_refresh_is_never_started_twice(env):
    """The second caller sees the lock of the live child and leaves, instead of opening a second TUI."""
    e, home, log = env
    lock = pathlib.Path(e["SG_GROK_REFRESH_LOCK"]) if e.get("SG_GROK_REFRESH_LOCK") else home / ".grok" / "refresh.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))   # this test's own process: alive
    out = subprocess.run(["bash", str(REFRESH), "--ttl", "0"], env=e, capture_output=True, text=True, timeout=60)
    assert "skip" in out.stdout.lower(), out.stdout + out.stderr
    assert not (home / "grok-starts").exists()


def test_the_hub_module_never_opens_a_pseudo_terminal_itself():
    """The TUI start is a child script, never a pty on the hub's own path (the guard is kept here, #2492)."""
    source = HUB_PY.read_text()
    for token in ("openpty", "forkpty", "os.fork(", "pty.spawn"):
        assert token not in source, f"hub.py carries {token}: the TUI start belongs in grokrefresh.sh"


def test_the_refresh_is_due_only_when_the_kept_reading_is_stale(tmp_path):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_grok_refresh", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    now = 1_800_000_000
    assert mod.grok_refresh_due({"pct": 6.0, "end": now - 10}, now=now), \
        "a period that ended is stale"
    assert not mod.grok_refresh_due({"pct": 6.0, "end": now + 3600}, now=now), \
        "a reading inside its period is fresh"
    assert mod.grok_refresh_due(None, now=now), "no kept reading is a reason to start the TUI"
