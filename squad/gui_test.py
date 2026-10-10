"""Validator for gui-test/1 scripts (the GUI test hook ADR, items 6-7)."""
from squad.gui_test_render import FORMATS

ACTION_NS = {"pad", "key", "text", "pointer", "window", "nav"}
CHECK_NS = {"ui", "fs", "log"}
TOP = ("format", "name", "target", "requires", "variants", "fixtures", "batches")
STEP = ("id", "role", "precondition", "action", "wait", "expect", "severity", "mode")
MANUAL_OPTIONAL = {"action", "wait"}
ROLES = {"under-test", "setup", "teardown", "recovery"}
MODES = {"automated", "manual"}


class ScriptError(ValueError):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("; ".join(problems))


def _name(kind, ns, name, requires, caps, where, bad):
    prefix, _, verb = name.partition(".")
    if prefix == "emu":
        bad.append(f"{where}: {name} is reserved and not part of gui-test/1")
    elif prefix not in ns or not verb:
        bad.append(f"{where}: unknown {kind} namespace {prefix}")
    elif name not in requires.get(kind + "s", []):
        bad.append(f"{where}: {kind} {name} is not in requires")
    elif caps is not None and name not in caps.get(kind + "s", []):
        bad.append(f"{where}: adapter does not advertise {kind} {name}")


# Preconditions are free text in gui-test/1 until a later slice parses them; only
# actions, checks, waits and variants are validated here.
def _wait(w, where, bad):
    units = [k for k in ("timeout_frames", "timeout_ticks") if k in w]
    if not units:
        bad.append(f"{where}: wait needs timeout_frames or timeout_ticks")
    elif len(units) > 1:
        bad.append(f"{where}: wait needs exactly one of timeout_frames, timeout_ticks")
    elif type(w[units[0]]) is not int or w[units[0]] < 1:
        bad.append(f"{where}: {units[0]} must be a positive integer")


def _step(s, in_steps, ctx, bad):
    sid = s.get("id", "?")
    for f in STEP:
        if f not in s and not (s.get("mode") == "manual" and f in MANUAL_OPTIONAL) \
                and f != "action":
            bad.append(f"{sid}: missing field {f}")
    # An automated step may only observe: no action, but then it needs wait and check.
    if "action" not in s and s.get("mode") == "automated" and "check" not in s:
        bad.append(f"{sid}: observe-only step needs check")
    if s.get("id") in ctx["ids"]:
        bad.append(f"duplicate step id {sid}")
    ctx["ids"].add(s.get("id"))
    if s.get("role") not in ROLES | {None}:
        bad.append(f"{sid}: unknown role {s['role']}")
    if s.get("mode") not in MODES | {None}:
        bad.append(f"{sid}: unknown mode {s['mode']}")
    act = s.get("action")
    if act:
        _name("action", ACTION_NS, act["name"], ctx["requires"], ctx["caps"], sid, bad)
        if act["name"] == "nav.goal" and s.get("role") == "under-test":
            bad.append(f"{sid}: nav.goal is not allowed on a step under test")
        args = act.get("args") or {}
        if "ticks" in args and "frames" in args:
            bad.append(f"{sid}: action has both ticks and frames")
        family = "ticks" if "ticks" in args else "frames" if "frames" in args else None
        waited = [k for k in ("timeout_frames", "timeout_ticks") if k in (s.get("wait") or {})]
        if family and len(waited) == 1 and waited[0] != "timeout_" + family:
            bad.append(f"{sid}: action counts {family} but wait uses {waited[0]}")
    if "sensitive" in s and type(s["sensitive"]) is not bool:
        bad.append(f"{sid}: sensitive must be true or false")
    if s.get("check"):
        _name("check", CHECK_NS, s["check"]["name"], ctx["requires"], ctx["caps"], sid, bad)
    if "wait" in s:
        _wait(s["wait"] or {}, sid, bad)
    for axis, value in (s.get("variants") or {}).items():
        declared = ctx["variants"]
        if axis not in declared:
            bad.append(f"{sid}: variant axis {axis} is not declared")
        elif value not in declared[axis]:
            bad.append(f"{sid}: variant {axis}={value} is not declared")


def validate(script, capabilities=None):
    """Raise ScriptError listing every problem; return None when the script is well formed."""
    fmt = script.get("format") if isinstance(script, dict) else None
    if fmt not in FORMATS:
        raise ScriptError([f"unknown format {fmt!r}"])
    bad = [f"missing field {f}" for f in TOP if f not in script]
    if bad:
        raise ScriptError(bad)
    ctx = {"ids": set(), "requires": script["requires"], "caps": capabilities,
           "variants": script["variants"]}
    if capabilities is not None:
        for axis, values in script["variants"].items():
            for v in values:
                if v not in capabilities.get("variants", {}).get(axis, []):
                    bad.append(f"adapter does not advertise variant {axis}={v}")
    for batch in script["batches"]:
        for part in ("setup", "teardown", "steps"):
            for s in batch.get(part, []):
                _step(s, part == "steps", ctx, bad)
    if bad:
        raise ScriptError(bad)
