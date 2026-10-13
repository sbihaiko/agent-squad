#!/usr/bin/env python3
"""Classify a completed GitHub Actions check from its job and check-run records."""
import json
import sys


def cannot_run(job, check):
    """A failed check with no reported job steps is an account or platform stop, not a code verdict."""
    return (
        isinstance(job, dict)
        and isinstance(check, dict)
        and job.get("status") == "completed"
        and check.get("conclusion") in {"failure", "startup_failure"}
        and isinstance(job.get("steps"), list)
        and not job["steps"]
    )


def reason(check):
    """The platform annotation, when it supplied one."""
    output = check.get("output") if isinstance(check, dict) else None
    values = [output.get(key) for key in ("title", "summary", "text")] if isinstance(output, dict) else []
    return " ".join(" ".join(str(value).split()) for value in values if value).strip()


def main():
    try:
        job, check = json.load(sys.stdin)
    except (ValueError, TypeError):
        return 2
    if cannot_run(job, check):
        print("cannot-run\t" + reason(check))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
