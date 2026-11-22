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
    unproven: list = field(default_factory=list)    # the findings nothing was measured for: the check could not run (ADR-0164, #1825)

    @property
    def outcome(self):
        """The three outcomes of ADR-0164: a measured red is `fail`, nothing measured is `unproven`, else `pass`.

        A finding nothing was measured for never fails the gate, and it never passes it either: the record says
        `unproven`, so the run carries the obligation the entry leaves.
        """
        if not self.ok:
            return "fail"
        return "unproven" if self.unproven else "pass"


@dataclass
class GateInput:
    """What a check may read: the artifacts in scope, the registered facts, and the run option a check names.

    `fix_run` is the run's own recorded option (ADR-0106): the run repairs a work branch, so every acceptance command proves in
    the tree its child wrote (#1374)."""
    artifacts: dict
    facts: dict
    cwd: str = "."
    fix_run: bool = False


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


def held_out_tree_missing(c, tree, fix_run=False):
    """True when a held-out command has no child tree to read: none recorded, or the recorded one is gone (#862).

    A git-history command is held out, and so is every command of a fix run (#1374): the fix is on the branch its child checked
    out, never in the target cut from the base. The one rule both the final gate and the landing check apply: it fails the
    command, never runs it in the target."""
    return (fix_run or workcmd.reads_history(c)) and (tree is None or not Path(tree).is_dir())


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


def stage_commands(check_id, params, inp, values=None):
    """The (argv, tree) pairs a gate stage is about to run, in the order it runs them; () when it runs no shell command.

    The identity of a suite is what the stage runs, never the id of its check. `ac-verify` runs each command through
    `/bin/sh -c`; `review-command` runs its argv as it stands. Two gate stages of one run that meet the same pairs over
    the same tree run one suite, whatever checks name them.
    """
    if check_id == "ac-verify":
        runs = list(dict.fromkeys(suite_runs(params, inp)[0]))     # `ac_verify` runs each pair once, so the key holds each once
        return tuple((("/bin/sh", "-c", c), tree) for c, tree in runs)
    if check_id == "review-command":
        argv = review_argv(params["argv"], values or {"worktree": inp.cwd})     # the same values `review_check` fills
        return ((tuple(argv), None),)
    return ()


def suite_key(check_id, params, inp, values=None):
    """The identity of the suite a gate stage would run, or None when the stage runs no shell command.

    Two stages with one key run the same commands over the same trees, so one green verdict answers for both. Any doubt --
    no command, or no git tree -- is no key, and the stage runs its check. A fix run's `ac-verify` stage keeps no key: its
    commands prove in its children's trees, and the one tree key is the target, which cannot answer for them (#1374).
    """
    cmds = stage_commands(check_id, params, inp, values)
    if check_id == "ac-verify" and inp.fix_run:
        return None
    return json.dumps([[list(argv), tree] for argv, tree in cmds], separators=(",", ":")) if cmds else None


def _porcelain_entries(raw):
    """The (code, path) pairs of a `status --porcelain=v1 -z` stream, with no quoting.

    Each entry is `XY <path>`. A rename or copy follows its new path with its old path as a separate token, which is
    skipped. The `-z` form prints a path as it is, so a name with a space, a tab or a non-ASCII character reads whole.
    """
    tokens, out, i = raw.split("\0"), [], 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) < 4:
            continue
        out.append((tok[:2], tok[3:]))
        if "R" in tok[:2] or "C" in tok[:2]:
            i += 1
    return out


def tree_key(cwd):
    """The tree a verdict is kept for: (its commit, a digest of its uncommitted state). None outside a git tree, or for
    a dirty state the key cannot read.

    A pass over uncommitted work must never answer for the commit, so the digest is part of the key and is empty only
    for a tree git reports clean at that commit. An untracked path counts by its contents: the path comes from
    `porcelain -z`, so a quoted name is read as the file it is. A dirty path the key cannot read -- a directory, or a
    file it may not open -- is no key at all, and the stage runs its check instead of reusing a verdict.
    """
    sha = git.head(cwd)
    if sha is None:
        return None
    try:
        st = git.run(["status", "--porcelain=v1", "-z", "-uall"], cwd)
        diff = git.run(["diff", "HEAD"], cwd)
    except (OSError, UnicodeDecodeError):
        return None
    if st.returncode != 0 or diff.returncode != 0:
        return None                            # a status or a diff that failed is a state the key cannot account for
    status, difftext = st.stdout or "", diff.stdout or ""
    if not status and not difftext:
        return sha, ""
    parts = [status, difftext]
    for code, path in _porcelain_entries(status):
        if code != "??":
            continue
        f = Path(cwd) / path
        try:
            if f.is_symlink():
                parts.append(os.readlink(f))    # an untracked link counts by its target, never by what it points at
            elif not f.is_file():
                return None                     # a directory git does not expand: the key cannot read its state
            else:
                parts.append(f.read_text(errors="replace"))
        except OSError:
            return None                         # an unreadable path is no key: the stage runs its check
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


def memo(check_id, params, inp, cwd, events, values=None):
    """The (key, tree, hit) a gate stage reads before it runs: `hit` is the green verdict it reuses, or None.

    `key` and `tree` are None when the stage is not memoed. A stage records a `gate_result` with them after it runs, and
    a later stage of the same run reads its hit here.
    """
    key = suite_key(check_id, params, inp, values)
    tree = tree_key(cwd) if key else None
    hit = green_verdict(events, tree[0], tree[1], key) if tree else None
    return key, tree, hit


def ac_verify(params, inp):
    """Run the acceptance commands that the named artifact lists, in the workspace. A command is code, not a model.

    The evidence of a failure is whatever `workcmd.run` kept, so this gate and the landing check record the same thing
    for the same command (ADR-0100 rule 10). A command that printed a pytest summary reports its counts beside the
    outcome (#80): the share of the suite that passed is progress a reader can see while the gate is still red. The
    counts never enter the verdict -- the exit code alone decides that (ADR-0019).

    Routing (#862, #1374): a git-history command runs only in the tree its child wrote, and so does every command of a fix
    run's dispatch: its work is on the branch the child checked out, and its target is cut from the base. Every other command,
    and every command of a run that is not a fix run, runs only in the target.
    No fallback: a red result is never replaced by another tree's green (ADR-0018).

    A `malformed` command (prose in it, a program that cannot start, or a `-k`/`-m` expression pytest refused, #2765) is recorded and answers unproven for that command:
    nothing ran, so nothing was proved false, and a run whose findings are all malformed is not failed for them (ADR-0164,
    delivery outranks the proof). So does an `unproven` command: a gate that answered its unproven exit with no summary
    line measured nothing (#1825). Both are recorded in `unproven`, so the gate result says `unproven` and never a silent
    pass. One measured `ac_failed` still fails the gate (#1803, #1825).
    """
    runs, dispatch = suite_runs(params, inp)
    if not runs:
        return GateResult(False, ["no_ac_commands"])
    findings, failing, unproven, evidence, counts = [], [], [], {}, {}
    for c, tree in dict.fromkeys(runs):     # each (command, tree) runs once, however many children named it
        held_out = dispatch and (inp.fix_run or workcmd.reads_history(c))
        label = f"{c} @{Path(tree).name}" if held_out and tree else c     # a held-out command's result names the tree it ran in
        if not held_out:
            r = workcmd.run(c, inp.cwd)     # the target: the merged head is what every other command must hold on
        elif held_out_tree_missing(c, tree, inp.fix_run):
            findings.append(f"child_tree_missing:{label}")     # never a silent run in the target: that is the false red of #862
            evidence[f"child_tree_missing:{label}"] = f"child worktree not found: {tree}" if tree else "no child worktree recorded"
            failing.append(f"child_tree_missing:{label}")      # a missing tree is a defect of the run, never an unproven check
            continue
        else:
            r = workcmd.run(c, tree)
        if r.counts:
            counts[label] = r.counts
        if not r.ok:
            kind = "malformed" if r.malformed else "unproven" if r.unproven else "ac_failed"   # the typed flag decides, never the text
            findings.append(f"{kind}:{label}")
            evidence[f"{kind}:{label}"] = r.evidence
            if kind == "ac_failed":
                failing.append(f"{kind}:{label}")   # only a measured failure holds the run (#1803, #1825)
            else:
                unproven.append(f"{kind}:{label}")
    return GateResult(not failing, findings, evidence, counts, unproven)


def visual_guard(_params, inp):
    shots = (inp.artifacts.get("visual_actor") or {}).get("screenshots", [])
    return GateResult(bool(shots), [] if shots else ["no_screenshots"])


def learning_check(_params, inp):
    n = inp.facts.get("blocking_learnings") or 0
    return GateResult(n == 0, [] if n == 0 else [f"blocking_learnings:{n}"])


REVIEW_NONE_EXIT = 3       # the one exit a review command gives for "no review here": the child lands as if no gate were named
REVIEW_FIX_EXIT = 4        # "fix": the review asks for changes the run may fix in a next entry of the child (#570); never given unless offered
REVIEW_RETRY_EXIT = 5      # "try again": the review got no verdict; the run tries again within the gate's deadline (#635)
REVIEW_NOCHANGE_PROBE_EXIT = 7   # "no change, by design": a probe child whose deliverable is a report has no tree change to review (#1574); a code child never gets it
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
    for a fix round, REVIEW_RETRY_EXIT asks for another try (no verdict), REVIEW_NOCHANGE_PROBE_EXIT is a probe with nothing to review, a signal ends the command `killed`, any other exit holds. `env` adds variables to the command's environment (the round and the fix offer).
    Return the review as the record keeps it: command, elapsed, exit code, verdict, findings (stdout) and a stderr tail;
    a killed review also names its `signal`."""
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
                   else "fix" if code == REVIEW_FIX_EXIT else "retry" if code == REVIEW_RETRY_EXIT
                   else "no_change" if code == REVIEW_NOCHANGE_PROBE_EXIT
                   else "killed" if code < 0 else "hold")
        return {"command": argv, "elapsed": round(time.monotonic() - t0, 3), "exit_code": code, "verdict": verdict,
                **({"signal": -code} if verdict == "killed" else {}),      # a negative code is the signal that ended it
                "findings": _read(out), "stderr": _read(err)[-1500:]}


def review_check(params, inp):
    """The review gate as a plain check: the dispatch runs it through `review_command` with its placeholders and progress."""
    r = review_command(review_argv(params["argv"], {"worktree": inp.cwd}), inp.cwd, params["deadline"])
    ok = r["verdict"] in ("approve", "no_review", "no_change")
    return GateResult(ok, [] if ok else [f"review_{r['verdict']}"], {} if ok else {f"review_{r['verdict']}": r["findings"]})


CHECKS = {
    "spec-shape": spec_shape, "ac-command-shape": ac_command_shape, "plan-semantics": plan_semantics, "spend-floor": spend_floor,
    "scope": scope, "security": security, "ac-verify": ac_verify, "visual-guard": visual_guard,
    "learning-check": learning_check, "review-command": review_check,
}
assert set(CHECKS) == set(GATE_REGISTRY)


def run_check(check_id, params, inp):
    return CHECKS[check_id](params, inp)
