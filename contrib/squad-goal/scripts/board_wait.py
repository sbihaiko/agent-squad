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


def dispatch_state(board, stood, called):
    """(column, Wait) of one card a dispatch asked for (#1903, ADR-0165).

    A settled card write reaches its column with the reason `run start`; the run's first call clears the reason and
    moves no column. A pending or dropped write holds the card where it stood, and the Wait names that refusal."""
    if not board: return stood, Wait("unknown", "no board fact")
    state = board.get("state")
    if state != "written": return stood, Wait("unknown", f"the card write is {state}")
    return board.get("column"), Wait("none" if called else "run start", None)


def dispatch_of(card):
    """(column, Wait) of one card a dispatch asked for, or None when its facts carry no board fact (#2128).

    The card names the dispatch's board fact in `board`, its column in `column` and the run's first call in `called`."""
    if not card.get("record"): return None
    board = card.get("board")
    if not board: return None
    return dispatch_state(board, card.get("column"), bool(card.get("called")))


def wait_of(card):
    if not card.get("record"): return Wait("unknown", "record")
    labels = card.get("labels", [])
    for label, name in HOLDS:
        if label in labels: return Wait(name, None)
    dispatch = dispatch_of(card)
    if dispatch: return dispatch[1]
    if card.get("run_live"): return Wait("live run", None)
    if "queued" not in card: return Wait("unknown", "queued")
    queued = card["queued"]
    if queued is None: return Wait("none", None)
    if queued not in WAITS: return Wait("unknown", "queued")
    return Wait(queued, None)


def _pending(binding, cards):
    """Each card on the board whose Wait differs from the carried value, as (issue, item id, Wait)."""
    carried = binding.read_waits()
    on_board = binding.items()
    pending = []
    for issue, facts in sorted(cards.items(), key=lambda kv: int(kv[0])):
        if issue not in on_board: continue
        answer = wait_of(facts)
        if carried.get(issue) != answer.name: pending.append((issue, on_board[issue], answer))
    return pending


def sync_waits(binding, cards, batch=BATCH):
    """Send each card's Wait only where it differs from the carried value, at most `batch` writes per call.
    Returns {issue: Wait} for the writes made."""
    written = {}
    for issue, item, answer in _pending(binding, cards)[:batch]:
        binding.write_wait(item, answer.name)
        written[issue] = answer
    return written


def wait_pass(binding, cards, batch=BATCH):
    """Send every changed Wait of one pass, in batches of at most `batch` writes. Returns the batches, each {issue: Wait}."""
    pending = _pending(binding, cards)
    batches = []
    for start in range(0, len(pending), batch):
        chunk = pending[start:start + batch]
        for _, item, answer in chunk: binding.write_wait(item, answer.name)
        batches.append({issue: answer for issue, _, answer in chunk})
    return batches
