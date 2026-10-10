"""A failed gui-test run opens one bug with its evidence; a clean run leaves nothing on GitHub (#1190, ADR-0150).

Screenshots are stored on a dedicated evidence branch of the target repo (see `evidence_store`), never as Actions
artifacts: those expire and do not render in an issue. Captures reach the store only through the privacy gate.

The bug says what failed (#1249): its title is the script, the first failed step and that failure in plain words, so a
board full of red runs reads as a list of failures instead of a wall of `N failed step(s)`. And the same failure is
never filed twice: a run whose first failed step and failure match an open bug comments on that bug with its own run id,
build and evidence (`find_open_bug_with`, `comment_on_bug_with`), and the results file records the issue it belongs to.
"""
import base64
import contextlib
import json
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


def _json(text):
    return json.loads(text or "{}")


def evidence_store(repo, branch=EVIDENCE_BRANCH, runner=_sh):
    """Return store(path, name) -> URL that renders inline in an issue of `repo`.

    One REST contents PUT per image onto `branch`; the `?raw=true` blob URL renders for anyone who can see the repo.
    The contents API refuses a branch the repo does not have ("Branch <branch> not found"), so the first refused push
    makes the branch as an orphan ref (a root commit over an empty tree, ADR-0135 rule 1) and is retried once.
    """
    def api(method, path, body=None):
        args = ["gh", "api", "--method", method, f"repos/{repo}/{path}"]
        return runner(args + (["--input", "-"] if body is not None else []),
                      **({"input": json.dumps(body)} if body is not None else {}))

    def orphan_branch():
        """A root commit over a one-file tree: the evidence branch shares no history with the default branch.

        The one file is the branch's own README (GitHub refuses an empty tree), so a person who finds the branch reads
        what it holds and why.
        """
        readme = "gui-test evidence: screenshots of failed and needs-review steps (ADR-0135).\n"
        blob = _json(api("POST", "git/blobs", {"content": readme, "encoding": "utf-8"}))["sha"]
        tree = _json(api("POST", "git/trees", {"tree": [
            {"path": "README.md", "mode": "100644", "type": "blob", "sha": blob}]}))["sha"]
        commit = _json(api("POST", "git/commits", {"message": f"gui-test evidence branch {branch}", "tree": tree}))["sha"]
        try:
            api("POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
        except subprocess.CalledProcessError as ex:   # another run made it first: the ref is all that mattered
            if "already exists" not in f"{ex.stderr}{ex.output}":
                raise

    def store(path, name):
        content = base64.b64encode(Path(path).read_bytes()).decode()
        put = ["gh", "api", "--method", "PUT", f"repos/{repo}/contents/{name}",
               "-f", f"message=gui-test evidence {name}", "-f", f"branch={branch}", "-f", f"content={content}"]
        try:
            runner(put)
        except subprocess.CalledProcessError as ex:
            said = f"{ex.stderr}{ex.output}"
            if "wasn't supplied" in said:   # the capture is already on the branch: an update needs its blob sha
                sha = _json(runner(["gh", "api", f"repos/{repo}/contents/{name}?ref={branch}"]))["sha"]
                runner(put + ["-f", f"sha={sha}"])
            elif branch in said:            # the branch itself is missing
                orphan_branch()
                runner(put)
            else:
                raise
        return f"https://github.com/{repo}/blob/{branch}/{name}?raw=true"
    return store


def file_bug_with(repo_root, runner=None):
    """Return file_bug(title, body, priority) that goes through the repo's scripts/report-bug.sh."""
    sh = runner or _sh

    def file_bug(title, body, priority=None):
        args = [f"{repo_root}/scripts/report-bug.sh", title, body] + ([priority] if priority else [])
        out = sh(args)
        return next((w for w in out.split() if w.startswith("http")), None)
    return file_bug


def find_open_bug_with(repo, runner=None):
    """Return find_open_bug(key) -> the open issue whose title carries `key`, or None.

    GitHub's search is asked for the key as an exact phrase in the title and the answer is filtered on the prefix, so
    a run finds the bug of its own failure and never the bug of a neighbouring one.
    """
    sh = runner or _sh

    def find_open_bug(key):
        phrase = '"' + key.replace('"', "'") + '"'  # the key is a title, never a JSON literal: escapes do not search
        out = sh(["gh", "api", "-X", "GET", "search/issues", "-f",
                  f"q=repo:{repo} is:issue is:open in:title {phrase}"])
        hit = next((i for i in json.loads(out or "{}").get("items", [])
                    if str(i.get("title", "")).startswith(key)), None)
        return {"number": hit["number"], "url": hit["html_url"], "title": hit["title"]} if hit else None
    return find_open_bug


def comment_on_bug_with(repo, runner=None):
    """Return comment_on_bug(issue, body): one comment on `issue` (a `find_open_bug` hit), so every run of a failure
    that already has a bug is on that bug's record instead of in a second issue."""
    sh = runner or _sh

    def comment_on_bug(issue, body):
        sh(["gh", "api", "--method", "POST", f"repos/{repo}/issues/{issue['number']}/comments", "-f", f"body={body}"])
    return comment_on_bug


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


def failure_words(verdict):
    """The failure a verdict states, in plain words: `failed (expected X, observed Y)` -> `expected X, observed Y`."""
    if verdict.startswith("failed (") and verdict.endswith(")"):
        return verdict[len("failed ("):-1]
    return verdict.removeprefix("failed").strip() or verdict


def failure_key(script_name, first):
    """What makes two runs the same bug: the script, the first failed step and its failure — never the count."""
    return f"{script_name}: {first['id']} — {failure_words(first['verdict'])}"


def bug_title(script_name, failed):
    """The bug's title: the script, the first failed step and its failure, `(+N more steps)` for the ones after it.

    Two failures never share a title unless they are the same failure, which is what makes a board of these readable.
    """
    title = failure_key(script_name, failed[0])
    return title + (f" (+{len(failed) - 1} more steps)" if len(failed) > 1 else "")


def _comment_body(title, failed, results, run, parts):
    """What a comment on an open bug says: this run's id, the build it ran and the same evidence a fresh bug carries."""
    head = [f"Another run hit this same failure — {title}.", ""]
    if run:
        head += [f"- Run: `{run.get('run_id')}` on `{run.get('platform')}` (build `{run.get('build_sha')}`)",
                 f"- {len(failed)} of {len(results)} steps failed in this run", ""]
    return "\n".join(head + parts)


def report_failures_detail(script, results, store, file_bug, approvals_path, priority="P1",
                           find_open_bug=None, comment_on_bug=None, run=None):
    """File the one bug a red run leaves, or attach the run to the open bug for the same failure (#1249).

    Return `{"title", "key", "url", "existing", "lookup_failed"}` or None when no step failed. `existing` is True when
    the run commented on an open bug instead of opening one, and `url` is the issue it belongs to either way. A look-up
    that cannot run (no `gh`, no network) is carried in `lookup_failed` and the run files its bug: a failure nobody
    recorded is worse than a bug that may repeat.
    """
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
    failed = [results[i] for i in bad]
    key, title = failure_key(script["name"], failed[0]), bug_title(script["name"], failed)
    if find_open_bug and comment_on_bug:
        try:
            twin = find_open_bug(key)
        except (subprocess.SubprocessError, OSError, ValueError) as ex:
            twin, lookup_failed = None, f"the look-up for an open bug failed: {ex}"
        else:
            lookup_failed = None
        if twin:
            try:  # an issue found but a comment that cannot be posted is a run to report, not a failure to swallow
                comment_on_bug(twin, _comment_body(twin["title"], failed, results, run, parts))
            except (subprocess.SubprocessError, OSError, ValueError) as ex:
                lookup_failed = f"the comment on its open bug {twin['url']} failed: {ex}"
            else:
                return {"title": twin["title"], "key": key, "url": twin["url"], "existing": True,
                        "lookup_failed": None}
    else:
        lookup_failed = None
    body = f"{len(bad)} of {len(results)} steps failed in `{script['name']}`.\n\n" + "\n\n".join(parts)
    return {"title": title, "key": key, "url": file_bug(title, body, priority), "existing": False,
            "lookup_failed": lookup_failed}


def report_failures(script, results, store, file_bug, approvals_path, priority="P1", **doors):
    """The URL of the bug this run belongs to (filed or attached to), or None when no step failed.

    The one-bug-per-run door of #1190; `report_failures_detail` is the same call and also says which of the two
    happened, and takes the doors that keep a failure from being filed twice (#1249).
    """
    detail = report_failures_detail(script, results, store, file_bug, approvals_path, priority=priority, **doors)
    return detail["url"] if detail else None
