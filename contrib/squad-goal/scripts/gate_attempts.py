"""Gate-attempt journaling and its Gate Tries projection (#1255, ADR-0170).

The gate runner shell appends one `gate_attempt` record to the flow-sample journal
(board_flow_history.history_path) for every gate invocation that drew a verdict: the card, the
wave tip, the attempt ordinal, the verdict and the instant. A memoized green verdict (ADR-0168)
is no attempt: the fast-gate shell records a `gate_reuse` marker instead, and a deadline stop is an
`unproven` attempt like any other verdict.

The journal is the source and the card's Gate Tries number field is its projection. One attempt
identity is (card, tip, aid): the aid is a stable name the runner gives one logical invocation, so
an identity already recorded -- a crash between the journal append and the field write, then a
replay under a new process -- is never appended twice, whatever pid or clock the replay carries.
The gate runner writes its start marker before the gate, so a replay reuses the same aid. A caller
may name a chain of gates with one `aid` (a landing's retry chain reruns the same attempt): the
first record of that aid answers for the chain. An empty aid is the one attempt of a card on a tip.
The field only rises -- a rearm sets a floor, never a reset
(ADR-0149) -- and it is written only when the value changes (ADR-0144). Compaction of the flow-sample
journal keeps attempt records.

CLI (the gate runner shells call these; every call is silent on a missing journal parent):
    gate_attempts.py journal <board-cache-path> <repo>     the journal path beside the board cache
    gate_attempts.py record <journal> <card> <tip> <verdict> [aid] [now]
    gate_attempts.py reuse <journal> <card> <tip> <aid> [now]
    gate_attempts.py count <journal> <card>

The projection is board.py --gate-tries alone (issue_state.set_gate_tries): one implementation of
the only-rise and write-only-on-change rules (ADR-0144, ADR-0149), not a second one here.
"""
import importlib.util
import json
import pathlib
import sys
import time

_spec = importlib.util.spec_from_file_location("board_flow_history", pathlib.Path(__file__).resolve().parent / "board_flow_history.py")
_flow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flow)

ATTEMPT = "gate_attempt"
REUSE = "gate_reuse"
VERDICTS = ("pass", "fail", "unproven")


def journal_path(board_cache, repo):
    """The flow-sample journal one repository keeps beside the board cache (#1255): the one name
    board_flow_history gives it, so a gate and the flow pass never write two files."""
    return _flow.history_path(board_cache, repo)


def _read(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _append(path, rec):
    path = pathlib.Path(path)
    with path.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def _identity(rec):
    """One logical attempt's identity. A named aid -- a landing's retry chain, a runner's start
    marker -- names the attempt across every tip the chain reaches, so the ruff fix that commits
    before the re-gate never makes a second attempt. An unnamed attempt is the one attempt of that
    card on that tip."""
    aid = rec.get("aid")
    if aid and aid != "-":
        return (rec.get("n"), aid)
    return (rec.get("n"), rec.get("tip"), "-")


def attempts(path):
    """The attempt records, one per identity: a record read twice counts once. The identity is
    derived from stable inputs alone, so a replay under a new process -- a crash between the
    journal append and the field write -- meets the same identity and is never counted twice."""
    seen, out = set(), []
    for rec in _read(path):
        if rec.get("k") != ATTEMPT:
            continue
        ident = _identity(rec)
        if ident in seen:
            continue
        seen.add(ident)
        out.append(rec)
    return out


def reuse_markers(path):
    return [rec for rec in _read(path) if rec.get("k") == REUSE]


def record_attempt(path, card, tip, verdict, aid=None, now=None):
    """One attempt record. The identity is (card, tip, aid): the aid is a stable name the runner
    gives one logical invocation, never a pid or a clock, so a replay that reads the same journal
    meets the same identity and answers as the record itself, never appending twice. An empty aid
    is the one attempt of that card on that tip. The ordinal counts the attempts the card already
    holds on the tip, plus one; a re-run on a new tip starts its own sequence."""
    if verdict not in VERDICTS:
        raise ValueError(f"a gate attempt's verdict is pass, fail or unproven, not {verdict!r}")
    card = int(card)
    tip = str(tip or "-")
    aid = str(aid or "-")
    now = int(time.time() if now is None else now)
    held = attempts(path)
    ident = _identity({"n": card, "tip": tip, "aid": aid})
    for rec in held:
        if _identity(rec) == ident:
            return rec   # the identity already journaled answers: a replay appends nothing
    ords = [r.get("ord") for r in held if r.get("n") == card and r.get("tip") == tip]
    ordinal = (max(ords) if ords else 0) + 1
    return _append(path, {"k": ATTEMPT, "n": card, "tip": tip, "ord": ordinal, "aid": aid, "v": verdict, "t": now})


def record_reuse(path, card, tip, aid, now=None):
    """One memo-reuse marker: a reused green verdict (ADR-0168) is recorded, and counts no attempt.
    The marker is one per (card, tip, aid): a memo hit already journaled is answered by the marker
    itself, so repeated hits on one tip never grow the journal."""
    card = int(card)
    tip = str(tip or "-")
    aid = str(aid or "-")
    for rec in reuse_markers(path):
        if rec.get("n") == card and rec.get("tip") == tip and rec.get("aid") == aid:
            return rec
    return _append(path, {"k": REUSE, "n": card, "tip": tip, "aid": aid,
                          "t": int(time.time() if now is None else now)})


def counts(path):
    """{card: attempt count} replayed from the journal: distinct identities, reuse markers no count."""
    out = {}
    for rec in attempts(path):
        out[rec["n"]] = out.get(rec["n"], 0) + 1
    return out


def last_attempt(path, card, tip=None):
    """The latest attempt of the card, None when the journal holds none. `tip` names the wave: only
    attempts at that tip answer, so a red work-branch gate never holds another wave's card. The
    latest is the greatest (t, ord): the clock the record carries, then the ordinal within the tip,
    so an unproven attempt with a stopped clock still orders after the attempt before it."""
    card = int(card)
    held = [r for r in attempts(path) if r.get("n") == card and (tip is None or r.get("tip") == str(tip))]
    if not held:
        return None
    return max(held, key=lambda r: (int(r.get("t") or 0), int(r.get("ord") or 0)))


def start_marker(state_dir, card, tip):
    """The path of the start marker gate.sh writes before a gate runs and drops once the attempt is
    journaled: while it stands, a gate is live for the card at that tip (#1257). The name is the one
    gate.sh builds, so the live read and the write never drift apart."""
    return pathlib.Path(state_dir) / f"gate-attempt-{int(card)}-{str(tip)[:12]}.start"


def gate_live(state_dir, card, tip):
    """True when a gate runs for the card at that tip right now: the start marker stands."""
    try:
        return start_marker(state_dir, card, tip).exists()
    except OSError:
        return False


def gating(path, card, tip, live=False):
    """The gating fact one card's wave carries (#1257), read from the journal alone so a hub restart
    answers the same. Two sources: a gate live for the wave right now, or the wave's last attempt
    ended without a pass and no attempt was made since. A pass with none since is the way out; a
    reuse marker is no attempt, so it never lifts the red."""
    if live:
        return True
    last = last_attempt(path, card, tip)
    return last is not None and last.get("v") != "pass"


def main(argv):
    args = list(argv)
    cmd, args = args[0], args[1:]
    if cmd == "journal":
        print(journal_path(args[0], args[1]))
        return 0
    if cmd == "record":
        rec = record_attempt(args[0], args[1], args[2], args[3], args[4], args[5] if len(args) > 5 else None)
        print(rec["ord"])
        return 0
    if cmd == "reuse":
        record_reuse(args[0], args[1], args[2], args[3], args[4] if len(args) > 4 else None)
        return 0
    if cmd == "count":
        print(counts(args[0]).get(int(args[1]), 0))
        return 0
    print("usage: gate_attempts.py journal|record|reuse|count ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
