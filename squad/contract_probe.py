"""Selection and reporting for the live model contract test (SPEC 505).

One small call per live registry entry, on its route, under a deadline, and one report line per entry. A timeout or a
refusal names the entry and its route. A closed usage window is reported closed, never as a pass. The caller injects
the runner, so the logic is proved with a stub and no model call; the live test passes the real one.
"""
import os
import subprocess
import tempfile
import time
import types
from dataclasses import dataclass, field

from squad import modelreg

PROMPT = "Reply with the single word: pong"
GATEWAY_URL = "http://127.0.0.1:8016"
DETAIL_CHARS = 120
ENV_KEEP = ("PATH", "HOME", "USER", "TMPDIR", "LANG")
SECRET_HINTS = ("KEY", "TOKEN", "SECRET")


@dataclass(frozen=True)
class Outcome:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Probe:
    entry_id: str
    route: str
    argv: tuple
    env: dict = field(default_factory=dict)
    stdin: str = PROMPT


@dataclass(frozen=True)
class Result:
    entry_id: str
    route: str
    status: str
    line: str


def _eligibility(entry):
    probe = types.SimpleNamespace(on=True, hrs=None, legacy_codex=False, subscription=entry.subscription)
    return modelreg.eligible(probe)


def _effort(entry):
    efforts = entry.efforts
    return "low" if "low" in efforts else (efforts[0] if efforts else None)


def _raw(entry, route):
    served = entry.routes[route].get("served") or []
    if entry.id in served or not served:
        return entry.id
    return next((s for s in served if s.endswith("-low")), served[0])


def _build(entry, route, prompt_path):
    inv = modelreg.invocation(_raw(entry, route), use=route, effort=_effort(entry), prompt_file=prompt_path)
    argv = inv.argv
    if argv and argv[0] == "claude":
        argv = tuple(PROMPT if a == prompt_path else a for a in argv)
    elif argv and argv[0] == "agy":
        argv = argv + (PROMPT,)
    return argv, inv.wire


def _gateway_argv(wire):
    return ("claude", "-p", "--model", wire, "--strict-mcp-config", PROMPT)


def _env(route):
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    if route == "gateway":
        env["ANTHROPIC_BASE_URL"] = os.environ.get("ANTHROPIC_BASE_URL", GATEWAY_URL)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if k in os.environ:
                env[k] = os.environ[k]
    return env


def plan(only=None, eligibility=None, reg=None):
    """The probes for every live (not retired) entry that has a route, in registry order. `only` narrows it by id."""
    reg = reg or modelreg.default_registry()
    if only is not None:
        missing = [m for m in only if m not in reg.entries]
        if missing:
            raise KeyError(f"not in the registry: {', '.join(missing)}")
    check = eligibility or _eligibility
    prompt_path = os.path.join(tempfile.gettempdir(), "squad-contract-prompt.txt")
    with open(prompt_path, "w") as fh:
        fh.write(PROMPT)
    probes = []
    for entry in reg.entries.values():
        if entry.retired or not entry.routes or (only is not None and entry.id not in only):
            continue
        route = "cli" if entry.routes.get("cli", {}).get("argv") else "gateway"
        if route == "cli":
            argv, _wire = _build(entry, route, prompt_path)
        else:
            _argv, wire = _build(entry, "gateway", prompt_path)
            argv = _gateway_argv(entry.routes["gateway"].get("model") or wire)
        gated = entry.routes[route].get("auth") == "api_key"
        verdict = check(entry)
        probes.append(Probe(entry.id, route, argv, _env("gateway" if gated else route), PROMPT) if verdict.ok
                      else Probe(entry.id, route, (), {"closed": verdict.reason or "closed"}, PROMPT))
    return probes


def _clean(text, env):
    text = " ".join((text or "").split())
    for k, v in env.items():
        if v and len(v) > 3 and any(h in k for h in SECRET_HINTS):
            text = text.replace(v, "[redacted]")
    return text[:DETAIL_CHARS]


def _result(probe, status, detail, spent=None):
    tail = f"{spent:.1f}s " if spent is not None else ""
    return Result(probe.entry_id, probe.route, status,
                  f"{probe.entry_id} {probe.route} {status.upper()} {tail}{detail}".rstrip())


def run(probe, runner, deadline):
    """One call, one result. The runner takes (argv, stdin, env, timeout) and returns an Outcome."""
    if not probe.argv:
        return _result(probe, "closed", probe.env.get("closed", "closed"))
    start = time.monotonic()
    try:
        out = runner(probe.argv, probe.stdin, probe.env, deadline)
    except subprocess.TimeoutExpired:
        return _result(probe, "timeout", f"no answer within {deadline}s")
    except FileNotFoundError as exc:
        return _result(probe, "unavailable", _clean(f"binary not found: {exc}", probe.env))
    spent = time.monotonic() - start
    if out.returncode != 0:
        return _result(probe, "refused", _clean(f"exit {out.returncode}: {out.stderr or out.stdout}", probe.env), spent)
    if not out.stdout.strip():
        return _result(probe, "refused", "empty answer", spent)
    return _result(probe, "ok", _clean(out.stdout, probe.env), spent)


def report(probes, runner, deadline):
    return [run(p, runner, deadline) for p in probes]


def subprocess_runner(argv, stdin, env, timeout):
    done = subprocess.run(list(argv), input=stdin, env=env, capture_output=True, text=True, timeout=timeout)
    return Outcome(done.returncode, done.stdout, done.stderr)
