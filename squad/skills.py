"""The skill versions of a run (ADR-0130). This module owns the hash rule. Record, envelope, replay and display call it.

A skill's version is its tree hash in `vendor/skills/VERSIONS.json` (the `sha256` field, from `skillpath.tree_hash`).

    load(vendor=VENDOR)           {name: {"hash", "source"}} for every vendored skill; {} when VERSIONS.json is missing
    hash_of(name, vendor=VENDOR)  the hash of one skill, or "" when it has none
    graph_skills(doc)             the skill names a graph can load: node `skills` and dispatch `allow.skills`
    snapshot(doc, vendor=VENDOR)  `load` limited to graph_skills(doc): the `skills` map of run_start
    commit(vendor=VENDOR)         the git commit that last changed `vendor`, or None
    commit_label(commit)          the commit as text for a message
    dirty(vendor=VENDOR)          what blocks a start (#140): a changed, staged or untracked path, a failed git status, or a skill with wrong bytes
"""
import json
import subprocess
from pathlib import Path

from squad.skillpath import ROOT, tree_hash

__all__ = ["dirty", "VENDOR", "tree_hash", "load", "hash_of", "graph_skills", "snapshot", "commit", "commit_label"]

VENDOR = ROOT / "vendor" / "skills"


def load(vendor: Path = VENDOR) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads((Path(vendor) / "VERSIONS.json").read_text())
    except (OSError, ValueError):
        return {}
    return {name: {"hash": v["sha256"], "source": v.get("source", "")} for name, v in raw.items()}


def hash_of(name: str, vendor: Path = VENDOR) -> str:
    return load(vendor).get(name, {}).get("hash", "")


def graph_skills(doc: dict) -> set[str]:
    """Every skill name in a node's `skills` or a dispatch node's `allow.skills` (an entry is a name or {name, version})."""
    out: set[str] = set()
    for n in doc.get("nodes", []):
        for s in [*(n.get("skills") or []), *((n.get("allow") or {}).get("skills") or [])]:
            out.add(s["name"] if isinstance(s, dict) else s)
    return out


def snapshot(doc: dict, vendor: Path = VENDOR) -> dict[str, dict[str, str]]:
    have = load(vendor)
    return {n: have[n] for n in sorted(graph_skills(doc)) if n in have}


def commit(vendor: Path = VENDOR) -> str | None:
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%H", "--", "."], cwd=vendor, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip() or None


def commit_label(commit: str | None) -> str:
    return f"vendor commit {commit}" if commit else "no commit recorded"


def dirty(vendor: Path = VENDOR) -> list[str]:
    """Reasons a run must not start on this vendor copy. Empty when clean. Outside a git repo only the byte check runs."""
    vendor, out = Path(vendor), []
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=vendor, capture_output=True, check=True)
        in_repo = True
    except (OSError, subprocess.CalledProcessError):
        in_repo = False
    if in_repo:
        try:
            r = subprocess.run(["git", "status", "--porcelain", "-uall", "--", "."], cwd=vendor, capture_output=True, text=True, check=True)
            out += [f"{ln[3:]} ({'untracked' if ln.startswith('??') else 'changed'})" for ln in r.stdout.splitlines()]
        except (OSError, subprocess.CalledProcessError):
            out.append("git status failed")
    for name, v in load(vendor).items():
        folder = vendor / "skills" / name
        if not folder.is_dir() or tree_hash(folder) != v["hash"]:
            out.append(f"skill {name}: bytes differ from VERSIONS.json")
    return out
