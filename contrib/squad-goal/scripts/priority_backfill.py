"""The Priority an open card gets when it carries none (#1982): a loop-bug urgent, a bug high, any other card low.

A card with a value keeps it, so a second run writes nothing, and an interrupted run resumes at the cards left.
"""


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
