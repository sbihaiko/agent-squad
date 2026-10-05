"""The gates registry and the deterministic checks (ADR-0100 rule 2, ADR-0111 rule 7).

The registry is the one source. The graph validator and the editor read it; a graph document
lists the ids it uses and they must match. A check reads artifacts and facts only. It never
calls a model, and its thresholds never change at run time (ADR-0012).
"""
import os
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch

from squad.procs import clean_env

GATE_REGISTRY = {
    "spec-shape": [],
    "ac-command-shape": ["duplicate_warn_ratio"],
    "plan-semantics": ["max_work_items"],
    "spend-floor": ["min_remaining_usd"],
    "scope": ["allowed_generated"],
    "security": ["sensitive_paths"],
    "ac-verify": ["commands_from"],
    "visual-guard": [],
    "learning-check": [],
}


EVIDENCE_TAIL = 1500      # characters kept from the end of a failed command's output


@dataclass
class GateResult:
    ok: bool
    findings: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)    # finding -> the tail of the output that explains it; the record keeps it, no prompt reads it


@dataclass
class GateInput:
    """What a check may read: the artifacts in scope and the registered facts."""
    artifacts: dict
    facts: dict
    cwd: str = "."


def _diff(inp):
    return inp.artifacts.get("diff") or {}


def spec_shape(_params, inp):
    """The spec artifact holds `ac_commands`, inside `spec`, as a non-empty list of non-empty strings (ticket #53)."""
    spec = inp.artifacts.get("spec")
    if spec is None:
        return GateResult(False, ["spec_missing"])
    if not isinstance(spec, dict):
        return GateResult(False, ["spec_not_an_object"])
    if "ac_commands" not in spec:
        # the store exposes the inner object, so look for a node output that holds ac_commands beside it
        beside = any(isinstance(v, dict) and v is not spec and "spec" in v and "ac_commands" in v for v in inp.artifacts.values())
        return GateResult(False, ["ac_commands_beside_spec" if beside else "ac_commands_missing"])
    cmds = spec["ac_commands"]
    if not isinstance(cmds, list) or not all(isinstance(c, str) and c.strip() for c in cmds):
        return GateResult(False, ["ac_commands_not_a_list_of_strings"])
    return GateResult(bool(cmds), [] if cmds else ["ac_commands_empty"])


def ac_command_shape(params, inp):
    cmds = (inp.artifacts.get("spec") or {}).get("ac_commands", [])
    if not cmds:
        return GateResult(False, ["no_ac_commands"])
    dup = 1 - len(set(cmds)) / len(cmds)
    return GateResult(dup <= params["duplicate_warn_ratio"], [] if dup <= params["duplicate_warn_ratio"] else ["duplicate_ac_commands"])


def plan_semantics(params, inp):
    wi = inp.artifacts.get("work_items")
    items = wi if isinstance(wi, list) else (wi or {}).get("items", [])
    bad = []
    if not items:
        bad.append("no_work_items")
    if len(items) > params["max_work_items"]:
        bad.append("too_many_work_items")
    return GateResult(not bad, bad)


def spend_floor(params, inp):
    left = inp.facts.get("run_spend_remaining")
    ok = left is not None and left >= params["min_remaining_usd"]
    return GateResult(ok, [] if ok else ["spend_floor"])


def scope(params, inp):
    bad = [f["path"] for f in _diff(inp).get("files", []) if f.get("generated") and f["path"] not in params["allowed_generated"]]
    return GateResult(not bad, [f"generated_file:{p}" for p in bad])


def security(params, inp):
    paths = [f["path"] for f in _diff(inp).get("files", [])]
    bad = [p for p in paths if any(fnmatch(p, s) or s in p.split("/") for s in params["sensitive_paths"])]
    return GateResult(not bad, [f"sensitive_path:{p}" for p in bad])


def ac_verify(params, inp):
    """Run the acceptance commands that the named artifact lists, in the workspace. A command is code, not a model."""
    src = params["commands_from"]
    cmds = []
    for name in [src] if isinstance(src, str) else src:     # the first artifact that lists commands wins
        cmds = (inp.artifacts.get(name) or {}).get("ac_commands", [])
        if cmds:
            break
    if not cmds:
        return GateResult(False, ["no_ac_commands"])
    bad, evidence = [], {}
    for c in cmds:
        try:
            r = subprocess.run(c, shell=True, cwd=inp.cwd, capture_output=True, timeout=120, env=clean_env(os.environ))
            if r.returncode != 0:
                bad.append(c)
                evidence[f"ac_failed:{c}"] = (r.stdout + r.stderr).decode("utf-8", "replace").strip()[-EVIDENCE_TAIL:]
        except subprocess.TimeoutExpired:
            bad.append(c)
            evidence[f"ac_failed:{c}"] = "timeout after 120 s"
    return GateResult(not bad, [f"ac_failed:{c}" for c in bad], evidence)


def visual_guard(_params, inp):
    shots = (inp.artifacts.get("visual_actor") or {}).get("screenshots", [])
    return GateResult(bool(shots), [] if shots else ["no_screenshots"])


def learning_check(_params, inp):
    n = inp.facts.get("blocking_learnings") or 0
    return GateResult(n == 0, [] if n == 0 else [f"blocking_learnings:{n}"])


CHECKS = {
    "spec-shape": spec_shape, "ac-command-shape": ac_command_shape, "plan-semantics": plan_semantics, "spend-floor": spend_floor,
    "scope": scope, "security": security, "ac-verify": ac_verify, "visual-guard": visual_guard,
    "learning-check": learning_check,
}
assert set(CHECKS) == set(GATE_REGISTRY)


def run_check(check_id, params, inp):
    return CHECKS[check_id](params, inp)
