"""The Priority an open card gets when it carries none (#1982): a loop-bug urgent, a bug high, any other card low.

A card with a value keeps it, so a second run writes nothing, and an interrupted run resumes at the cards left.
The same rule is the birth Priority every issue writer sets (#1978): a spec or a task low, a bug high, a loop-bug urgent.
A spec's Priority reaches its open children (#1979): each child takes the higher of the spec's value and its own floor.

usage: python3 priority_backfill.py [--spec <name>] <label>...   print the birth Priority of an issue with these labels
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from priority_rank import LEVELS  # noqa: E402


def target(labels):
    if any(str(label).startswith("loop-bug") for label in labels): return "urgent"
    return "high" if "bug" in labels else "low"


def birth(labels, spec=None):
    """The Priority a new child starts at: its spec's Priority, or its own floor when that is higher."""
    if spec is not None and spec not in LEVELS: raise ValueError(f"not a Priority: {spec!r}")
    return max(spec or "low", target(labels), key=LEVELS.get)


def backfill(binding):
    """Write the target Priority of each open board card with no value. Returns {issue: name} for the writes made."""
    carried = binding.read_priorities()
    on_board = binding.items()
    written = {}
    for issue, labels in sorted(binding.open_issue_labels().items(), key=lambda kv: int(kv[0])):
        if issue in carried or issue not in on_board: continue
        name = target(labels)
        binding.write_priority(on_board[issue], name)
        written[issue] = name
    return written


def propagate(state, repo, spec, version, value, by):
    """Carry the spec's Priority to its open children at `version` (#1979). `by` maps each issue to its body, sub_issues,
    labels and state. Returns {child: recorded|duplicate|stale|held|ambiguous}. A child with more than one parent is refused."""
    from siblings import specs
    import issue_ledger
    if value not in LEVELS: raise ValueError(f"not a Priority: {value!r}")
    out = {}
    children = [n for n in by if n != spec and by[n]["state"] == "open" and spec in specs(n, by)]
    for n in sorted(children, key=int):
        if len(specs(n, by)) > 1:
            out[str(n)] = "ambiguous"
            continue
        key = f"{repo}#{n}"
        ledger = issue_ledger.read(key)
        if ledger["held"]:
            out[str(n)] = "held"
            continue
        seen = (ledger.get("priority") or {}).get("version")
        if seen is not None and version <= seen:
            out[str(n)] = "duplicate" if version == seen else "stale"
            continue
        want = max(value, target(by[n]["labels"]), key=LEVELS.get)
        state.set_priority(n, want)
        out[str(n)] = issue_ledger.priority(key, version, want)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    spec = args[1] if args[:1] == ["--spec"] else None
    print(birth(args[2:] if spec else args, spec))
