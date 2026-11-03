"""Compare and apply the chains of models.json (#1261, ADR-0140 clause 7, ADR-0147 clause 11).

diff   the chains whose head or order differs between the repository file and the live file.
apply  copy the repository chains into the live file: atomic, with a backup, every other key kept.
A hand-run publish and the hub call diff. The per-cycle pass never does. Only apply writes the live file.

usage: models_chains.py diff <repo models.json> <live models.json>   one line per differing chain; exit 0
       models_chains.py apply <repo models.json> <live models.json>  prints the backup path
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path


def chains_of(doc):
    """{chain: [id, ...]} of a v2 file, else of the v1 `<chain>_models` lists."""
    if isinstance(doc.get("chains"), dict):
        return {k: [e.get("id") if isinstance(e, dict) else e for e in v] for k, v in doc["chains"].items()}
    return {k[:-7]: list(v) for k, v in doc.items() if k.endswith("_models") and isinstance(v, list)}


def diff(repo_doc, live_doc):
    """One {chain, repo, live} per chain whose id list differs; a chain missing on one side reads as empty."""
    r, l = chains_of(repo_doc), chains_of(live_doc)
    return [{"chain": k, "repo": r.get(k, []), "live": l.get(k, [])}
            for k in sorted(set(r) | set(l)) if r.get(k, []) != l.get(k, [])]


def line(d):
    return f"chain {d['chain']} differs: repository [{', '.join(d['repo'])}] live [{', '.join(d['live'])}]"


def diff_files(repo, live):
    """The difference of two files; empty when either cannot be read."""
    try:
        return diff(json.loads(Path(repo).read_text()), json.loads(Path(live).read_text()))
    except (OSError, ValueError, AttributeError):
        return []


def _atomic_write(path, text):
    tmp = path.with_name(f"{path.name}.new.{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


def apply(repo, live, now=None):
    """Replace only `chains` in the live file; return the backup path.
    The backup sits in <live skill>/backups, never in scripts/, where live-guard reads a .bak-* as clutter."""
    repo, live = Path(repo), Path(live)
    try:
        rdoc, ldoc = json.loads(repo.read_text()), json.loads(live.read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"models-apply: cannot read a models.json: {e}")
    if not isinstance(rdoc.get("chains"), dict):
        sys.exit("models-apply: the repository file has no chains")
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = live.resolve().parent.parent / "backups" / f"models.json.{stamp}.bak"
    backup.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(backup, live.read_text())
    ldoc["chains"] = rdoc["chains"]
    _atomic_write(live, json.dumps(ldoc, indent=2) + "\n")
    return backup


def main(argv):
    if len(argv) != 4 or argv[1] not in ("diff", "apply"):
        sys.exit(__doc__)
    if argv[1] == "diff":
        for d in diff_files(argv[2], argv[3]):
            print(line(d))
    else:
        print(apply(argv[2], argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
