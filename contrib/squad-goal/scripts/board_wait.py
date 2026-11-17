"""The Wait field: what a card waits for, one closed value per card (#1902, ADR-0144, ADR-0170).

A hold label answers its own value and never a wait of work. A card with no record answers unknown, never none.
"""
from collections import namedtuple

WAITS = (
    "none", "run start", "run slot", "gate slot", "wave entry", "review slot", "fix round",
    "live run", "owner", "dependency", "not startable", "unknown")

HOLDS = (("frozen:owner", "owner"), ("frozen:dependency", "dependency"), ("not-startable", "not startable"))

BATCH = 25

Wait = namedtuple("Wait", "name missing")


def wait_of(card):
    if not card.get("record"): return Wait("unknown", "record")
    labels = card.get("labels", [])
    for label, name in HOLDS:
        if label in labels: return Wait(name, None)
    if card.get("run_live"): return Wait("live run", None)
    if "queued" not in card: return Wait("unknown", "queued")
    queued = card["queued"]
    if queued is None: return Wait("none", None)
    if queued not in WAITS: return Wait("unknown", "queued")
    return Wait(queued, None)


def sync_waits(binding, cards, batch=BATCH):
    """Send each card's Wait only where it differs from the carried value, at most `batch` writes per call.
    Returns {issue: Wait} for the writes made."""
    carried = binding.read_waits()
    on_board = binding.items()
    written = {}
    for issue, facts in sorted(cards.items(), key=lambda kv: int(kv[0])):
        if issue not in on_board: continue
        answer = wait_of(facts)
        if carried.get(issue) == answer.name: continue
        if len(written) >= batch: break
        binding.write_wait(on_board[issue], answer.name)
        written[issue] = answer
    return written
