"""A failed gui-test run opens one bug with its evidence; a clean run leaves nothing on GitHub (#1190, ADR-0150).

Screenshots are stored on a dedicated evidence branch of the target repo (see `evidence_store`), never as Actions
artifacts: those expire and do not render in an issue. Captures reach the store only through the privacy gate.
"""
import base64
import contextlib
import os
import subprocess
import tempfile
import time
from pathlib import Path

from squad.gui_test_gate import is_released

EVIDENCE_BRANCH = "gui-test-evidence"
EVIDENCE_FAMILIES = ("failed", "needs review")   # the verdicts a reader must look at; everything else uploads nothing
WITHHELD = "withheld"                            # what a step records when the gate does not release its capture
STALE_LOCK = 600.0                               # a lock file this old is one a dead run left behind


def evidence_lock(repo, branch=EVIDENCE_BRANCH, root=None):
    """The lock file every run on this machine shares, per repo and branch: pushes are serialized, not interleaved."""
    return Path(root or tempfile.gettempdir()) / f"gui-test-evidence-{repo.replace('/', '-')}-{branch}.lock"


@contextlib.contextmanager
def push_lock(path, timeout=60.0, poll=0.01, stale=STALE_LOCK):
    """Hold `path` exclusively, so two runs never push to the evidence branch at once.

    The lock is a file created exclusively, so it serializes processes and not only threads and needs no server. A lock
    file older than `stale` seconds is one a run that died left behind: it is taken over rather than blocking forever.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - Path(path).stat().st_mtime > stale:
                    Path(path).unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"another run holds {path}")
            time.sleep(poll)
    try:
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            Path(path).unlink()


def upload_evidence(results, store, approvals_path, lock_path=None):
    """Give every failed or needs-review capture its evidence URL; a passed or pending step uploads nothing.

    A capture the privacy gate does not release is recorded `withheld` and the run goes on: the gate never blocks a
    run. Returns a new list of results with `evidence` set on the step that has one; the capture keeps its local path,
    so the results file names both where the image came from and where a reader can look at it. With a `lock_path`
    every push holds `push_lock` on it, so concurrent runs cannot collide on the evidence branch.
    """
    out = []
    for r in results:
        r = dict(r)
        cap = r.get("capture")
        if cap is not None and r["verdict"].startswith(EVIDENCE_FAMILIES):
            if is_released(r, approvals_path):
                with push_lock(lock_path) if lock_path else contextlib.nullcontext():
                    r["evidence"] = store(cap["path"], f"{r['id']}-{cap['sha256'][:12]}.png")
            else:
                r["evidence"] = WITHHELD
        out.append(r)
    return out


def _sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def evidence_store(repo, branch=EVIDENCE_BRANCH, runner=_sh):
    """Return store(path, name) -> URL that renders inline in an issue of `repo`.

    One REST contents PUT per image onto `branch`; the `?raw=true` blob URL renders for anyone who can see the repo.
    """
    def store(path, name):
        content = base64.b64encode(Path(path).read_bytes()).decode()
        runner(["gh", "api", "--method", "PUT", f"repos/{repo}/contents/{name}",
                "-f", f"message=gui-test evidence {name}", "-f", f"branch={branch}", "-f", f"content={content}"])
        return f"https://github.com/{repo}/blob/{branch}/{name}?raw=true"
    return store


def file_bug_with(repo_root, runner=_sh):
    """Return file_bug(title, body, priority) that goes through the repo's scripts/report-bug.sh."""
    def file_bug(title, body, priority=None):
        args = [f"{repo_root}/scripts/report-bug.sh", title, body] + ([priority] if priority else [])
        out = runner(args)
        return next((w for w in out.split() if w.startswith("http")), None)
    return file_bug


def _image(label, result, store, approvals, steps_seen):
    cap = result["capture"] if result else None
    if cap is None:
        return f"{label}: no screenshot (the step took none)"
    if not is_released(result, approvals):
        return f"{label}: screenshot held pending approval"
    sha = cap["sha256"]
    if sha not in steps_seen:
        steps_seen[sha] = store(cap["path"], f"{result['id']}-{sha[:12]}.png")
    return f"{label}:\n\n![{result['id']}]({steps_seen[sha]})"


def report_failures(script, results, store, file_bug, approvals_path, priority="P1"):
    """File one bug listing every failed step; return its URL, or None (nothing stored, nothing filed) when none failed."""
    bad = [i for i, r in enumerate(results) if r["verdict"].startswith("failed")]
    if not bad:
        return None
    steps = {s["id"]: s for b in script["batches"] for s in b["setup"] + b["steps"] + b["teardown"]}
    seen, parts = {}, []
    for i in bad:
        r = results[i]
        parts.append("\n".join([
            f"### `{r['id']}`",
            f"- **Expected:** {steps[r['id']].get('expect', '(none stated)')}",
            f"- **Observed:** {r['verdict']}",
            "",
            _image("Failed step", r, store, approvals_path, seen),
            "",
            _image("Previous step", results[i - 1] if i else None, store, approvals_path, seen),
        ]))
    title = f"GUI test `{script['name']}`: {len(bad)} failed step{'s' if len(bad) > 1 else ''}"
    body = f"{len(bad)} of {len(results)} steps failed in `{script['name']}`.\n\n" + "\n\n".join(parts)
    return file_bug(title, body, priority)
