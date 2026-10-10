"""A failed gui-test run opens one bug with its evidence; a clean run leaves nothing on GitHub (#1190, ADR-0150).

Screenshots are stored on a dedicated evidence branch of the target repo (see `evidence_store`), never as Actions
artifacts: those expire and do not render in an issue. Captures reach the store only through the privacy gate.
"""
import base64
import subprocess
from pathlib import Path

from squad.gui_test_gate import is_released

EVIDENCE_BRANCH = "gui-test-evidence"


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
