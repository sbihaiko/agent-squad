"""A fake GitHub runner for the run command's bug doors (#1249): it answers the REST calls a run makes and files through
the checkout's `report-bug.sh`, so the whole filing path runs with no network and no test ever touches the real board.

The run command reaches it as `--github-runner tests.gui_test_github_fake:shell`; the board is module state, so a test
that drove a run reads the same issues and comments the run left behind.
"""
import json
import re
import subprocess

ISSUES = []    # {"number", "title", "body", "url"}, in the order they were filed
COMMENTS = []  # {"number", "body"}, one per comment a run added to an issue it found


def reset():
    """An empty board, so a case starts from a GitHub holding nothing."""
    del ISSUES[:]
    del COMMENTS[:]


def _query(args):
    """The search query of a `gh api -f q=...` call."""
    return next((a[len("q="):] for a in args if a.startswith("q=")), "")


def _field(args, name):
    """The value of a `-f <name>=<value>` field, whatever it carries."""
    return next((a[len(name) + 1:] for a in args if a.startswith(name + "=")), "")


def shell(args, **kw):
    """The shell a run's bug doors call: `gh api` is answered here, everything else runs for real.

    A filing goes through the caller's own `scripts/report-bug.sh` stand-in — that helper is the one route a run's bug
    takes — and the issue it created lands on the board, so the next run's search finds it.
    """
    if args and args[0] == "gh":
        joined = " ".join(args)
        if "search/issues" in joined:
            found = re.search(r'in:title "(.*)"', _query(args))
            key = found.group(1) if found else ""
            hits = [i for i in ISSUES if i["title"].startswith(key)]
            return json.dumps({"items": [{"number": i["number"], "title": i["title"], "html_url": i["url"]}
                                         for i in hits]})
        if "/comments" in joined:
            COMMENTS.append({"number": int(re.search(r"/issues/(\d+)/comments", joined).group(1)),
                             "body": _field(args, "body")})
            return "{}"
        raise AssertionError(f"the fake GitHub was asked for something it does not answer: {args}")
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    number = 1000 + len(ISSUES) + 1
    url = next((w for w in out.split() if w.startswith("http")), f"https://example.invalid/bugs/{number}")
    ISSUES.append({"number": number, "title": args[1], "body": args[2], "url": url})
    return out
