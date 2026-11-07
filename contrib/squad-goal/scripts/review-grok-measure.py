#!/usr/bin/env python3
"""Measure the grok debt-only review exemption from the loop's state; read only, no model is called."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CHANGES = "VERDICT: CHANGES"
TIMEOUT_EXIT = 142


def _rows(path: Path) -> list[dict] | None:
    """The ledger's rows, or None when the file is absent or unreadable; a broken line is skipped."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _attempts(state: Path) -> tuple[list[tuple[str, str, str, int]], str | None]:
    """(model, effort, seconds, exit code) per try, and the file that answered; effort is '' when unrecorded."""
    path = state / "review-attempts.tsv"
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        path = state / "review-times.tsv"
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return [], None
        out = []
        for line in lines:
            fields = line.split("\t")
            if len(fields) >= 5 and fields[4].strip().lstrip("-").isdigit():
                out.append((fields[2], "", fields[3], int(fields[4])))
        return out, path.name
    out = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) >= 6 and fields[5].strip().lstrip("-").isdigit():
            out.append((fields[2], fields[3], fields[4], int(fields[5])))
    return out, path.name


_MODELREG: Any = None


def _registry() -> Any:
    """The registry reader beside this script, loaded once: the one resolver of a review route (ADR-0147)."""
    global _MODELREG
    if _MODELREG is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import modelreg
        _MODELREG = modelreg
    return _MODELREG


def _is_grok(model: str) -> bool:
    """The vendor of one reviewer through the registry's own resolver, never a copy of an id list here.

    Read from the CLI row: a reviewer whose review lane is the no-tools gateway call (#1373) has no runner on that row,
    and its CLI row still names the vendor's program. A stale resolver that takes no route is no vendor, never a crash."""
    reg = _registry()
    try:
        return bool(reg.review_route(model, route="cli").runner == "grok")
    except (reg.RegistryError, KeyError, IndexError):
        return False        # the documented registry errors: a model it cannot resolve has no known vendor, so it is not
                            # the exempt one. Any other exception is a real bug and surfaces, never a silent "no vendor".


def _shadow(row: dict) -> bool:
    """A shadow line is the next chain entry's answer on grok's head (#1403), never a published review."""
    return row.get("shadow") is True


def _counts(row: dict) -> tuple[int, int] | None:
    blocking, debt = row.get("blocking"), row.get("debt")
    if isinstance(blocking, int) and isinstance(debt, int):
        return blocking, debt
    return None


def _debt_only(row: dict) -> bool:
    counts = _counts(row)
    return counts is not None and _is_grok(row.get("model", "")) \
        and row.get("verdict") == CHANGES and counts[0] == 0 and counts[1] >= 1


def _rate(part: int, whole: int) -> str | None:
    return f"{part / whole * 100:.1f}%" if whole else None


def measure(state: Path) -> dict:
    rows = _rows(state / "review-ledger.jsonl")
    tries, source = _attempts(state)
    # the files whose absence left a field null: the ledger, and the two try records when neither answers
    unavailable = ([] if rows is not None else ["review-ledger.jsonl"]) \
        + ([] if source else ["review-attempts.tsv", "review-times.tsv"])
    out: dict = {"unavailable": unavailable}

    if rows is None:
        out.update({"reviews": None, "grok_reviews": None, "grok_debt_only": None, "debt_only_rate": None,
                    "debt_only_by_model": None, "unattributed_rows": None, "grok_fix_rounds": None,
                    "grok_fix_round_verdict_changes": None, "grok_fix_round_kept_changes": None,
                    "shadow_reviews": None})
    else:
        # a shadow line answers on grok's head, so it is measured by _paired and never counted as a review of a model
        published = [r for r in rows if not _shadow(r)]
        attributed = [r for r in published if _counts(r) is not None]
        grok = [r for r in attributed if _is_grok(str(r.get("model", "")))]
        by_model: dict[str, dict[str, int]] = {}
        for r in attributed:
            slot = by_model.setdefault(str(r.get("model", "?")), {"reviews": 0, "debt_only": 0})
            slot["reviews"] += 1
            slot["debt_only"] += 1 if _debt_only(r) else 0
        fix_rounds = changed = kept = 0
        previous: dict[str, dict] = {}
        for r in attributed:
            key = str(r.get("key", ""))
            before = previous.get(key)
            previous[key] = r
            if before is None or not _debt_only(before) or not r.get("fix_round") or r.get("head") == before.get("head"):
                continue
            fix_rounds += 1
            if r.get("verdict") != before.get("verdict"):
                changed += 1
            else:
                kept += 1
        out.update({
            "reviews": len(published),
            "shadow_reviews": len(rows) - len(published),
            "grok_reviews": len(grok),
            "grok_debt_only": sum(1 for r in grok if _debt_only(r)),
            "debt_only_rate": _rate(sum(1 for r in grok if _debt_only(r)), len(grok)),
            "debt_only_by_model": by_model,
            "unattributed_rows": len(published) - len(attributed),
            "grok_fix_rounds": fix_rounds,
            "grok_fix_round_verdict_changes": changed,
            "grok_fix_round_kept_changes": kept,
        })

    grok_tries = [t for t in tries if _is_grok(t[0])]
    out.update({
        "attempts_from": source,
        "grok_attempts": len(grok_tries) if source else None,
        "grok_timeouts": sum(1 for t in grok_tries if t[3] == TIMEOUT_EXIT) if source else None,
        "grok_fallthroughs": sum(1 for t in grok_tries if t[3] != 0) if source else None,
        "grok_low_effort_attempts": sum(1 for t in grok_tries if t[1] == "low") if source == "review-attempts.tsv" else None,
    })

    out.update(_paired(rows))
    out["decision"] = _decision(rows, out)
    return out


def _paired(rows: list[dict] | None) -> dict:
    """A head that grok called blocking and another model approved or shadow-reviewed: the Blocking findings the next
    entry would have missed, counted in findings on both paths. A shadow line reports its own Blocking count (#1403), so
    the count it did not file is the miss (`max(0, grok - shadow)`); a count alone cannot align findings one by one, so
    the difference is a floor on a head where the shadow filed more. The legacy pair has no shadow count: the approving
    model filed none, so every grok Blocking finding on that head is the miss."""
    if rows is None:
        return {"paired_heads": None, "next_entry_catch": None, "next_entry_approved_unflagged": None,
                "next_entry_catch_reason": "unavailable: no review-ledger.jsonl in the state directory"}
    heads: dict[tuple, dict] = {}
    for r in rows:
        if _counts(r) is None:
            continue
        head = heads.setdefault((r.get("key"), r.get("head")), {"models": {}, "shadow": []})
        if _shadow(r):
            head["shadow"].append(r)
        else:
            head["models"].setdefault(r.get("model", "?"), []).append(r)
    pairs = catch = unflagged = 0
    for head in heads.values():
        models, shadow = head["models"], head["shadow"]
        grok_blocking = max(((_counts(r) or (0, 0))[0] for m, rs in models.items() if _is_grok(m)
                             for r in rs if r.get("verdict") == CHANGES), default=0)
        if shadow and grok_blocking >= 1:
            # one sample answers for one head: the grok Blocking findings the shadow entry did not file are the miss
            pairs += 1
            shadow_blocking = max((_counts(r) or (0, 0))[0] for r in shadow)
            catch += max(0, grok_blocking - shadow_blocking)
            continue
        if len(models) < 2:
            continue
        pairs += 1
        flagged = grok_blocking >= 1
        approved = any(not _is_grok(m) and any(r.get("verdict") == "VERDICT: APPROVE" for r in rs)
                       for m, rs in models.items())
        catch += grok_blocking if flagged and approved else 0
        unflagged += 1 if approved and not flagged else 0
    return {
        "paired_heads": pairs,
        "next_entry_catch": catch if pairs else None,
        "next_entry_approved_unflagged": unflagged if pairs else None,
        "next_entry_catch_reason": None if pairs else
        "unavailable: no two models reviewed one head and no shadow reviewed a grok Blocking head; "
        "review.sh saves only the winning answer per branch",
    }


def _decision(rows: list[dict] | None, out: dict) -> dict:
    """One rule over the counts: where the exemption belongs, as far as the state can say."""
    if rows is None:
        return {"exemption": "pending", "reason": "the ledger is absent, so no grok review is measured", "models": []}
    by_model = out.get("debt_only_by_model") or {}
    grok = {m: v for m, v in by_model.items() if _is_grok(m)}
    flagged = sorted(m for m, v in grok.items() if v["debt_only"] > 0)
    if not flagged:
        return {"exemption": "pending", "reason": "no grok review landed on debt only, so the exemption has no case yet",
                "models": []}
    if len(grok) < 2:
        return {"exemption": "pending",
                "reason": f"only {flagged[0]} has a grok review; vendor against per-model needs a second grok model",
                "models": flagged}
    if len(flagged) == len(grok):
        return {"exemption": "vendor-name",
                "reason": f"every grok model with a review files debt-only CHANGES ({', '.join(flagged)}): the habit is the vendor's",
                "models": flagged}
    return {"exemption": "per-model",
            "reason": f"only {', '.join(flagged)} of {', '.join(sorted(grok))} files debt-only CHANGES, "
                      "so the registry flag should name it",
            "models": flagged}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <state-dir>", file=sys.stderr)
        return 2
    state = Path(argv[1])
    if not state.is_dir():
        print(f"review grok measure: {state} is not a directory", file=sys.stderr)
        return 2
    try:
        print(json.dumps(measure(state), sort_keys=True))
    except ImportError as error:
        print(f"review grok measure: the registry reader beside this script did not load: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
