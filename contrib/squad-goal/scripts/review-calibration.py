#!/usr/bin/env python3
"""Report the Debt/nit-only rate in a labeled review sample."""

from __future__ import annotations

import json
import sys
from pathlib import Path


DEBT_NIT_ONLY = "debt_nit_only"
LABELS = {"real_defect", DEBT_NIT_ONLY}


def report(path: Path) -> str:
    data = json.loads(path.read_text())
    samples = data["samples"]
    labels = [sample["label"] for sample in samples]
    if not labels:
        raise ValueError("the labeled sample is empty")
    if set(labels) - LABELS:
        raise ValueError("the labeled sample has an unknown label")
    debt_nit_only = labels.count(DEBT_NIT_ONLY)
    rate = debt_nit_only / len(labels) * 100
    return f"labeled={len(labels)} debt_nit_only={debt_nit_only} rate={rate:.1f}%"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <labeled-sample.json>", file=sys.stderr)
        return 2
    try:
        print(report(Path(argv[1])))
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(f"review calibration: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
