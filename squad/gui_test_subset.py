"""Derive the runnable subset of a `gui-test/1` script from a target adapter's capabilities (MesenAI #1242).

The run command of #1222 rejects, before launch, any script naming an action, check or variant value the adapter does
not advertise (ADR-0272 item 6). The pad-only pilot script names five actions and two window modes while the
`mesen-gui` adapter advertises `pad.press` and `windowed` alone, so the whole script is refused and nothing runs.

Narrowing the script is the other answer, and this module is it: a step the adapter can be asked to run stays
`automated`, and a step it cannot becomes `manual` - never dropped - with the capability it is missing appended to the
step's own `expect` text, which is where the rendered Markdown lists it for a person. The derived script is a
`gui-test/1` script like any other and passes the same validator against those capabilities.

What changes, exactly:

  - a step naming an unadvertised action, check or variant value loses `action`, `wait`, `check` and `variants` (the
    validator allows a manual step to carry none of them) and gains `mode: "manual"`;
  - `requires` narrows to the actions and checks the surviving automated steps actually use, and the top-level
    `variants` narrows to the advertised values - an axis left with no value is dropped whole, so `run_variants` still
    runs the script once instead of zero times;
  - a step whose `precondition` names a control id the target does not report is manual for the same reason: the
    supervised runner checks the precondition before the step and fails the batch when it does not hold;
  - a batch whose `setup` could not be run takes its automated steps with it, because they assert a state nothing
    established - so what the derived script claims to run is only what its own first step can reach;
  - nothing else moves: ids, order, `precondition`, `expect`, `severity` and `role` are the input's.

The input script is never mutated: a caller keeps the full script and asks for as many narrowings as it has adapters.
"""
import copy
import re

# A control id: dotted, no spaces. The wait predicate is free-ish text (`ui.focused == play.home.open-rom`), so a
# token that is not an id on its own is not read as one.
_ID = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")


def _values(value):
    return [value] if isinstance(value, str) else list(value or [])


def _predicate_ids(text):
    """The ids a `ui.<what> == <id>` predicate names, `and`-joined predicates included (`ui.screen` and `ui.focused`
    both read control ids)."""
    out = []
    for clause in re.split(r"\s+and\s+", str(text or "")):
        match = re.fullmatch(r"ui\.[a-z]+ == ([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)", clause.strip())
        if match and match.group(1) not in out:
            out.append(match.group(1))
    return out


def control_ids(step):
    """Every control id the step names, in order - what `ui.screen`, `ui.focused`, `ui.visible` and `ui.dialogs` read,
    the id a `ui.screen == ...` or `ui.focused == ...` wait reads, and the one its `precondition` names.

    The precondition is read because it is a state the step is not run without: the runner checks it before the step
    and fails the step when it does not hold, so an id nothing reports is the same objection as an unadvertised check.
    """
    out = []
    for name in ("check", "action"):
        args = (step.get(name) or {}).get("args") or {}
        for key, value in args.items():
            if not isinstance(value, (str, list)) or (name == "action" and key in ("button", "buttons", "key", "text")):
                continue
            for one in _values(value):
                if isinstance(one, str) and _ID.fullmatch(one) and one not in out:
                    out.append(one)
    for text in ((step.get("wait") or {}).get("check"), step.get("precondition")):
        for one in _predicate_ids(text):
            if one not in out:
                out.append(one)
    return out


def missing_capabilities(step, capabilities):
    """The advertised names `step` needs and the target does not have, as short readable strings (empty when it has
    them all).

    A `capabilities` carrying `controls` - the control ids the application reports - also speaks for the ids the
    step's check and wait name: a check of an id nothing reports is `failed (unknown id)` at runtime, never a false
    reading (ADR-0272 item 6), so the step cannot be asked of this target either.
    """
    out = []
    action = step.get("action")
    if action and action.get("name") not in (capabilities.get("actions") or []):
        out.append("action " + str(action.get("name")))
    check = step.get("check")
    if check and check.get("name") not in (capabilities.get("checks") or []):
        out.append("check " + str(check.get("name")))
    declared = capabilities.get("variants") or {}
    for axis, value in (step.get("variants") or {}).items():
        for one in _values(value):
            if one not in (declared.get(axis) or []):
                out.append(f"variant {axis}={one}")
    if "controls" in capabilities:
        known = capabilities.get("controls") or []
        out += [f"control {one}" for one in control_ids(step) if one not in known]
    return out


def _manual(step, missing, target):
    """The step as a person walks it: the same case, without the names this adapter cannot be asked to run."""
    out = {k: v for k, v in step.items() if k not in ("action", "wait", "check", "variants")}
    out["mode"] = "manual"
    reason = ", ".join(missing)
    out["expect"] = (f"{step['expect']} (manual: {reason})" if reason.startswith("batch setup")
                     else f"{step['expect']} (manual: needs {reason}, which {target} does not advertise)")
    return out


def _used(steps):
    actions, checks = set(), set()
    for step in steps:
        if step.get("action"):
            actions.add(step["action"]["name"])
        if step.get("check"):
            checks.add(step["check"]["name"])
    return {"actions": sorted(actions), "checks": sorted(checks)}


def runnable(script, capabilities):
    """`{"script": <the narrowed gui-test/1 script>, "manual": [...], "kept": n, "converted": n}`.

    `manual` lists every step that became manual, in script order, each with the batch, the part it sits in and the
    capabilities it is missing - the list a person reads to know what the adapter could not do.
    """
    view = copy.deepcopy(script)
    target = view.get("target") or "the adapter"
    view["variants"] = {axis: kept for axis, values in (script.get("variants") or {}).items()
                        if (kept := [v for v in values
                                     if v in ((capabilities.get("variants") or {}).get(axis) or [])])}
    manual = []

    def convert(batch, part, step, missing, out):
        out.append(_manual(step, missing, target))
        manual.append({"batch": batch["id"], "part": part, "id": step["id"], "missing": missing})

    for batch in view["batches"]:
        for part in ("setup", "steps", "teardown"):
            if part not in batch:
                continue
            out = []
            for step in batch[part]:
                missing = missing_capabilities(step, capabilities)
                if missing:
                    convert(batch, part, step, missing, out)
                else:
                    out.append(step)
            batch[part] = out
        # A batch whose setup the adapter cannot run has nothing behind its automated steps: the runner checks each
        # step's precondition and fails the batch when it does not hold, so those steps cannot be asked of it either.
        stuck = [s["id"] for s in batch.get("setup") or [] if s.get("mode") == "manual"]
        if stuck:
            missing = [f"batch setup {', '.join(stuck)} is manual, so nothing established the state this step asserts"]
            for part in ("setup", "steps", "teardown"):
                out = []
                for step in batch.get(part) or []:
                    if step.get("mode") == "manual":
                        out.append(step)
                    else:
                        convert(batch, part, step, missing, out)
                batch[part] = out
    kept = [(part, s) for b in view["batches"] for part in ("setup", "steps", "teardown")
            for s in (b.get(part) or []) if s.get("mode") == "automated"]
    # `requires` is what setup and steps use, never the teardown: that is the rule the doc check enforces.
    view["requires"] = _used([s for part, s in kept if part != "teardown"])
    return {"script": view, "manual": manual, "kept": len(kept), "converted": len(manual)}
