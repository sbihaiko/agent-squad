"""The Priority an open card gets when it carries none (#1982): a loop-bug urgent, a bug high, any other card low.

A card with a value keeps it, so a second run writes nothing, and an interrupted run resumes at the cards left.
The same rule is the birth Priority every issue writer sets (#1978): a spec or a task low, a bug high, a loop-bug urgent.

usage: python3 priority_backfill.py <label>...   print the birth Priority of an issue with these labels
"""
import sys


def target(labels):
    if any(str(label).startswith("loop-bug") for label in labels): return "urgent"
    return "high" if "bug" in labels else "low"


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


if __name__ == "__main__":
    print(target(sys.argv[1:]))
