"""A failed gui-test run opens one bug with its evidence; a clean run leaves nothing on GitHub (#1190, ADR-0150).

Screenshots are stored on a dedicated evidence branch of the target repo (see `evidence_store`), never as Actions
artifacts: those expire and do not render in an issue. Captures reach the store only through the privacy gate.

The bug says what failed (#1249): its title is the script, the first failed step and that failure in plain words, so a
board full of red runs reads as a list of failures instead of a wall of `N failed step(s)`. And the same failure is
never filed twice: a run whose first failed step and failure match an open bug comments on that bug with its own run id,
build and evidence (`find_open_bug_with`, `comment_on_bug_with`), and the results file records the issue it belongs to.
"""
import base64
import json
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
            comment_on_bug(twin, _comment_body(twin["title"], failed, results, run, parts))
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
