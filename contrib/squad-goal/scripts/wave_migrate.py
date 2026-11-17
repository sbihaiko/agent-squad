"""Migrate the `wave:NN` labels to approved native edges, then remove the labels (#1989).

The migration infers no edge from a wave number: a label is an order, not a proof of need. The dry run lists every open
card that carries a wave label and proposes the edges the card's own body declares ("Blocked by #12"). It writes nothing.
Only an approved input file names the edges it writes, and each one goes through edge_write, so the deadlock validator
passes it and the write reads back. A label is removed only after every approved edge reads back, and only when the
owner's own grant is on the command line: the code never sets it.

usage:
  python3 wave_migrate.py <owner/repo>                              dry run: list the cards and the proposed edges
  python3 wave_migrate.py <owner/repo> --approved <file>            write the approved edges
  python3 wave_migrate.py <owner/repo> --approved <file> --owner-grant   write, then remove the wave labels
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge_write  # noqa: E402
import ghread  # noqa: E402

WAVE = re.compile(r"wave:(\d+)$")
BLOCKED_BY = re.compile(r"^[ \t]*blocked by:?[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
REF = re.compile(r"#(\d+)")
READ_BACK = ("written", "exists")   # a write that read back, or an edge that already read back


def _names(labels):
    return [label.get("name") if isinstance(label, dict) else label for label in labels or []]


def wave_of(labels):
    """The card's wave number, or None. A card with more than one wave label takes the lowest."""
    found = [int(m.group(1)) for label in _names(labels) for m in [WAVE.match(str(label))] if m]
    return min(found) if found else None


def declared_blockers(body):
    """The issue numbers a card's own body declares after "Blocked by", in the order they appear."""
    found = []
    for match in BLOCKED_BY.finditer(body or ""):
        for ref in REF.findall(match.group(1)):
            if int(ref) not in found:
                found.append(int(ref))
    return found


def open_wave_cards(repo, read=None):
    """Every open card of the repository that carries a wave label, ordered by number."""
    res = (read or ghread.read)(f"repos/{repo}/issues?state=open&per_page=100")
    if not res.is_answer:
        raise edge_write.GhFailed(res.message or res.detail or f"the read of the open issues of {repo} {res.kind}")
    cards = []
    for node in res.answer or []:
        if str(node.get("state") or "").lower() != "open" or not isinstance(node.get("number"), int):
            continue
        labels = _names(node.get("labels"))
        wave = wave_of(labels)
        if wave is None:
            continue
        cards.append({"number": node["number"], "title": node.get("title") or "", "body": node.get("body") or "",
                      "wave": wave, "labels": [label for label in labels if WAVE.match(str(label))]})
    return sorted(cards, key=lambda card: card["number"])


def propose(cards):
    """The edges the cards declare for the owner to approve. A wave number proposes nothing, and a self-edge never."""
    return [{"blocked": card["number"], "blocker": blocker,
             "evidence": f"the body of #{card['number']} declares #{blocker}"}
            for card in cards for blocker in declared_blockers(card["body"]) if blocker != card["number"]]


def read_approved(path):
    """The edge pairs of the approved file: a JSON list of {"blocked", "blocker"} objects or of [blocked, blocker]."""
    pairs = []
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        blocked, blocker = (item["blocked"], item["blocker"]) if isinstance(item, dict) else item
        pairs.append((int(blocked), int(blocker)))
    return pairs


def _write(repo, blocked, blocker):
    try:
        return edge_write.add_edge(repo, blocked, blocker)
    except edge_write.WriteUnconfirmed as exc:
        return {"status": "unconfirmed", "blocked": blocked, "blocker": blocker, "reasons": [str(exc)]}
    except edge_write.GhFailed as exc:
        return {"status": "failed", "blocked": blocked, "blocker": blocker, "reasons": [str(exc)]}


def remove_wave_labels(repo, cards, write=None):
    """Remove every wave label the cards carry. A removal that is not answered stops the list where it stands."""
    write = write or ghread.write
    removed = []
    for card in sorted(cards, key=lambda card: card["number"]):
        for label in card["labels"]:
            res = write(f"repos/{repo}/issues/{card['number']}/labels/{label}", "DELETE")
            if res.kind != ghread.DONE:
                raise edge_write.GhFailed(res.message or res.detail or f"the removal of {label} on #{card['number']} {res.kind}")
            removed.append({"number": card["number"], "label": label})
    return removed


def migrate(repo, approved=None, owner_grant=False, read=None):
    """List the wave-labelled cards and their proposals; write only the approved edges; remove labels last."""
    cards = open_wave_cards(repo, read=read)
    result = {"mode": "dry-run" if approved is None else "write", "cards": cards, "proposed": propose(cards),
              "writes": [], "removal": "dry-run", "removed": []}
    if approved is None:
        return result
    for blocked, blocker in read_approved(approved):
        result["writes"].append(_write(repo, blocked, blocker))
    if not all(entry.get("status") in READ_BACK for entry in result["writes"]):
        result["removal"] = "skipped"
        return result
    if not owner_grant:
        result["removal"] = "held-for-grant"
        return result
    result["removed"] = remove_wave_labels(repo, cards)
    result["removal"] = "done"
    return result


def main(argv):
    args = list(argv)
    if not args:
        print(__doc__.strip().splitlines()[-3].strip(), file=sys.stderr)
        return 2
    repo, approved, grant = args.pop(0), None, False
    while args:
        arg = args.pop(0)
        if arg == "--approved" and args:
            approved = args.pop(0)
        elif arg == "--owner-grant":
            grant = True
        else:
            print(f"unknown argument: {arg}", file=sys.stderr)
            return 2
    result = migrate(repo, approved=approved, owner_grant=grant)
    print(json.dumps(result))
    return 0 if all(entry.get("status") in READ_BACK for entry in result["writes"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
