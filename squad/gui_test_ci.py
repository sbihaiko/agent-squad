"""CI verdict for a gui-test run (#1186): red on a missing native core or a failed step, green otherwise.

`needs review` and `pending` steps never fail the job, but the summary lists each one so a reviewer sees them.
"""
import os
import sys


def ci_verdict(results, core_available):
    """Return (exit_code, markdown_summary). An unavailable core is red: it must never degrade to a silent skip."""
    if not core_available:
        return 1, "## GUI test run: RED\n\nThe native core is unavailable; the run did not execute (never a skip)."
    bad = [r for r in results if r["verdict"].startswith("failed")]
    open_ = [r for r in results if r["verdict"] in ("needs review", "pending")]
    lines = [f"## GUI test run: {'RED' if bad else 'GREEN'}", ""]
    lines += [f"- failed: {r['id']} ({r['verdict']})" for r in bad]
    lines += [f"- {r['verdict']}: {r['id']}" for r in open_]
    lines.append(f"\n{len(results)} steps, {len(bad)} failed, {len(open_)} not decided.")
    return (1 if bad else 0), "\n".join(lines)


def write_summary(summary):
    """Append to the job summary when running under GitHub Actions; always echo to stdout."""
    print(summary)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(summary + "\n")


def main(results, core_available):
    code, summary = ci_verdict(results, core_available)
    write_summary(summary)
    sys.exit(code)
