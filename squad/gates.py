"""The gates registry and the deterministic checks.

The registry is the one source. The graph validator and the editor read it; a graph document
lists the ids it uses and they must match. A check reads artifacts and facts only. It never
calls a model, and its thresholds never change at run time.
"""
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from squad import git, workcmd

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
    "review-command": ["argv", "deadline"],
}


@dataclass
class GateResult:
    ok: bool
    findings: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)    # finding -> the output that explains it; the record keeps it, no prompt reads it
    counts: dict = field(default_factory=dict)      # command -> the counts of the last pytest summary it printed (#80); a reading, never a threshold


@dataclass
class GateInput:
    """What a check may read: the artifacts in scope and the registered facts."""
    artifacts: dict
    facts: dict
    cwd: str = "."


def _diff(inp):
    return inp.artifacts.get("diff") or {}


def spec_shape(_params, inp):
    """The spec artifact holds `ac_commands`, inside `spec`, as a non-empty list of non-empty strings."""
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


def held_out_tree_missing(c, tree):
    """True when a git-history command has no child tree to read: none recorded, or the recorded one is gone (#862).
    The one rule both the final gate and the landing check apply: it fails the command, never runs it in the target."""
    return workcmd.reads_history(c) and (tree is None or not Path(tree).is_dir())


def suite_runs(params, inp):
    """The (command, tree) pairs a suite gate would run, and whether the artifact that names them is a dispatch.

    The one resolver `ac_verify` and the verdict memo share, so a memo's key states exactly what the gate runs. The first
    artifact `commands_from` names that lists commands wins; no runs is no suite.
    """
    src = params["commands_from"]
    for name in [src] if isinstance(src, str) else src:
        art = inp.artifacts.get(name) or {}
        # a dispatch names, for each command, the tree of the child that wrote it (#862); a dispatch with no ac_runs names none (#867)
        runs = [tuple(r) for r in art["ac_runs"]] if "ac_runs" in art else [(c, None) for c in art.get("ac_commands", [])]
        if runs:
            return runs, ("ac_runs" in art or "ledger" in art)     # an older engine wrote a dispatch with no ac_runs (#867): still a dispatch
    return [], False


def suite_key(check_id, params, inp):
    """The identity of the suite a gate stage would run, or None when the stage runs no suite: only a suite gate is memoed.

    Two stages with one key run the same commands over the same trees, so one green verdict answers for both. Any doubt --
    no git tree, or no commands -- is no key, and the stage runs its check.
    """
    if check_id != "ac-verify":
        return None
    runs, _dispatch = suite_runs(params, inp)
    return json.dumps([list(r) for r in runs], separators=(",", ":")) if runs else None


def tree_key(cwd):
    """The tree a verdict is kept for: (its commit, a digest of its uncommitted state). None outside a git tree.

    A pass over uncommitted work must never answer for the commit, so the digest is part of the key and is empty only
    for a tree git reports clean at that commit.
    """
    sha = git.head(cwd)
    if sha is None:
        return None
    status = git.run(["status", "--porcelain=v1", "-uall"], cwd).stdout or ""
    diff = git.run(["diff", "HEAD"], cwd).stdout or ""
    if not status.strip() and not diff.strip():
        return sha, ""
    parts = [status, diff]
    for line in status.splitlines():     # an untracked file counts by its contents, not only by its name
        if line.startswith("?? "):
            f = Path(cwd) / line[3:]
            try:
                parts.append(f.read_text(errors="replace") if f.is_file() else "")
            except OSError:
                parts.append("<unreadable>")
    return sha, hashlib.sha256("\0".join(parts).encode()).hexdigest()


def green_verdict(events, sha, dirt, suite):
    """The first green verdict this run recorded for one tree and one suite, or None.

    `events` is this run's own record, so another run's verdict is never read. Only `pass` is a hit: a red never answers
    for a later stage.
    """
    for ev in events:
        if (ev.get("event") == "gate_result" and ev.get("outcome") == "pass"
                and ev.get("sha") == sha and ev.get("dirt") == dirt and ev.get("suite") == suite):
            return ev
    return None


def ac_verify(params, inp):
    """Run the acceptance commands that the named artifact lists, in the workspace. A command is code, not a model.

    The evidence of a failure is whatever `workcmd.run` kept, so this gate and the landing check record the same thing
    for the same command (ADR-0100 rule 10). A command that printed a pytest summary reports its counts beside the
    outcome (#80): the share of the suite that passed is progress a reader can see while the gate is still red. The
    counts never enter the verdict -- the exit code alone decides that (ADR-0019).

    Routing (#862): a git-history command runs only in the tree its child wrote; every other command runs only in the target.
    No fallback: a red result is never replaced by another tree's green (ADR-0018).
    """
    runs, dispatch = suite_runs(params, inp)
    if not runs:
        return GateResult(False, ["no_ac_commands"])
    findings, evidence, counts = [], {}, {}
    for c, tree in dict.fromkeys(runs):     # each (command, tree) runs once, however many children named it
        history = dispatch and workcmd.reads_history(c)
        label = f"{c} @{Path(tree).name}" if history and tree else c     # a history command's result names the tree it ran in
        if not history:
            r = workcmd.run(c, inp.cwd)     # the target: the merged head is what every other command must hold on
        elif held_out_tree_missing(c, tree):
            findings.append(f"child_tree_missing:{label}")     # never a silent run in the target: that is the false red of #862
            evidence[f"child_tree_missing:{label}"] = f"child worktree not found: {tree}" if tree else "no child worktree recorded"
            continue
        else:
            r = workcmd.run(c, tree)
        if r.counts:
            counts[label] = r.counts
        if not r.ok:
            kind = "malformed" if r.malformed else "ac_failed"     # prose in a command is a bad check, not a failed one
            findings.append(f"{kind}:{label}")
            evidence[f"{kind}:{label}"] = r.evidence
    return GateResult(not findings, findings, evidence, counts)


def visual_guard(_params, inp):
    shots = (inp.artifacts.get("visual_actor") or {}).get("screenshots", [])
    return GateResult(bool(shots), [] if shots else ["no_screenshots"])


def learning_check(_params, inp):
    n = inp.facts.get("blocking_learnings") or 0
    return GateResult(n == 0, [] if n == 0 else [f"blocking_learnings:{n}"])


REVIEW_NONE_EXIT = 3       # the one exit a review command gives for "no review here": the child lands as if no gate were named
REVIEW_FIX_EXIT = 4        # "fix": the review asks for changes the run may fix in a next entry of the child (#570); never given unless offered
REVIEW_RETRY_EXIT = 5      # "try again": the review got no verdict; the run tries again within the gate's deadline (#635)
REVIEW_PLACEHOLDERS = ("run_id", "child", "issue", "worktree", "base")
FINDINGS_MAX = 65536       # characters of a review's stdout the record keeps; a longer output keeps its head and names the cut
TERM_GRACE_S = 2.0


def review_argv(template, values):
    """Fill each `{name}` placeholder of the argv template. A brace that names no placeholder stays as it is."""
    out = []
    for arg in template:
        for k in REVIEW_PLACEHOLDERS:
            arg = arg.replace("{" + k + "}", str(values.get(k, "")))
        out.append(arg)
    return out


def _read(f):
    f.seek(0)
    text = f.read().decode(errors="replace")
    return text if len(text) <= FINDINGS_MAX else text[:FINDINGS_MAX] + f"\n[{len(text) - FINDINGS_MAX} more characters cut]\n"


def _stop(proc):
    """End the command's whole process group: TERM, a grace, then KILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=TERM_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            continue


def review_command(argv, cwd, deadline, tick=60.0, on_tick=None, env=None):
    """Run one review command in `cwd` with a deadline (ADR-0051). Call `on_tick(elapsed)` at least once per `tick` seconds
    while it runs, so its run's record moves. Exit 0 approves, REVIEW_NONE_EXIT is "no review here", REVIEW_FIX_EXIT asks
    for a fix round, REVIEW_RETRY_EXIT asks for another try (no verdict), any other exit holds. `env` adds variables to the command's environment (the round and the fix offer).
    Return the review as the record keeps it: command, elapsed, exit code, verdict, findings (stdout) and a stderr tail."""
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, start_new_session=True,
                                    env={**os.environ, **env} if env else None)
        except OSError as x:
            return {"command": argv, "elapsed": 0.0, "exit_code": None, "verdict": "hold", "findings": "", "stderr": f"review_not_started: {x}"}
        code = None
        while code is None:
            left = deadline - (time.monotonic() - t0)
            if left <= 0:
                _stop(proc)
                break
            try:
                code = proc.wait(timeout=min(tick, left))
            except subprocess.TimeoutExpired:
                if on_tick and time.monotonic() - t0 < deadline:
                    on_tick(round(time.monotonic() - t0, 3))
        verdict = ("deadline" if code is None else "approve" if code == 0 else "no_review" if code == REVIEW_NONE_EXIT
                   else "fix" if code == REVIEW_FIX_EXIT else "retry" if code == REVIEW_RETRY_EXIT else "hold")
        return {"command": argv, "elapsed": round(time.monotonic() - t0, 3), "exit_code": code, "verdict": verdict,
                "findings": _read(out), "stderr": _read(err)[-1500:]}


def review_check(params, inp):
    """The review gate as a plain check: the dispatch runs it through `review_command` with its placeholders and progress."""
    r = review_command(review_argv(params["argv"], {"worktree": inp.cwd}), inp.cwd, params["deadline"])
    ok = r["verdict"] in ("approve", "no_review")
    return GateResult(ok, [] if ok else [f"review_{r['verdict']}"], {} if ok else {f"review_{r['verdict']}": r["findings"]})


CHECKS = {
    "spec-shape": spec_shape, "ac-command-shape": ac_command_shape, "plan-semantics": plan_semantics, "spend-floor": spend_floor,
    "scope": scope, "security": security, "ac-verify": ac_verify, "visual-guard": visual_guard,
    "learning-check": learning_check, "review-command": review_check,
}
assert set(CHECKS) == set(GATE_REGISTRY)


def run_check(check_id, params, inp):
    return CHECKS[check_id](params, inp)
