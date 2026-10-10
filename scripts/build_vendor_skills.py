"""Build vendor/skills: the local plugin of researched skills that agent nodes load.

Usage: python3 scripts/build_vendor_skills.py [--check] [--config FILE] [--out DIR]
Copies each skill in config/vendor-skills.yaml (a `skill:NAME` source is found by name) into <out>/skills/<name>/, writes the plugin manifest, and writes
VERSIONS.json with a tree hash per skill. `--check` builds nothing: it exits 1 if a source differs from the copy, or
the copy differs from VERSIONS.json. Update the copy when a skill changes, and commit the result.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from squad import skillpath  # noqa: E402

SKIP_DIRS = skillpath.SKIP_DIRS
SKIP_FILES = skillpath.SKIP_FILES


def files(src):
    src = Path(src).resolve()
    out = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_file() and not (set(rel.parts[:-1]) & SKIP_DIRS) and p.name not in SKIP_FILES:
            out.append(rel)
    return out


def tree_hash(base, rels):
    h = hashlib.sha256()
    for r in rels:
        h.update(str(r).encode() + b"\0" + (Path(base) / r).read_bytes() + b"\0")
    return h.hexdigest()


def resolve(src):
    """A `skill:NAME` source is found by name; anything else is a path."""
    if str(src).startswith("skill:"):
        hit = skillpath.resolve(str(src)[6:])
        return hit.folder if hit else ROOT / "no-such-skill" / str(src)[6:]
    p = Path(src).expanduser()
    return p if p.is_absolute() else ROOT / p


def build(config=ROOT / "config" / "vendor-skills.yaml", out=ROOT / "vendor" / "skills", check=False):
    """Return (problems, versions). With check, nothing is written."""
    out, problems, versions = Path(out), [], {}
    skills = yaml.safe_load(Path(config).read_text())["skills"]
    for s in skills:
        src = resolve(s["source"])
        if not (src / "SKILL.md").exists():
            problems.append(f"{s['name']}: no SKILL.md at {src}")
            continue
        rels = files(src)
        versions[s["name"]] = {"source": s["source"], "sha256": tree_hash(src, rels), "files": len(rels)}
        dest = out / "skills" / s["name"]
        if check:
            have = files(dest) if dest.exists() else []
            if have != rels or (have and tree_hash(dest, have) != versions[s["name"]]["sha256"]):
                problems.append(f"{s['name']}: the copy differs from its source")
            continue
        shutil.rmtree(dest, ignore_errors=True)
        for r in rels:
            (dest / r).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / r, dest / r)
    if check:
        recorded = json.loads((out / "VERSIONS.json").read_text()) if (out / "VERSIONS.json").exists() else {}
        problems += [f"{n}: VERSIONS.json is out of date" for n in versions if recorded.get(n, {}).get("sha256") != versions[n]["sha256"]]
        return problems, versions
    if problems:
        return problems, versions
    (out / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (out / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "agent-squad-skills", "version": "1", "description": "Skills that agent-squad nodes load. Built by scripts/build_vendor_skills.py."}, indent=2) + "\n")
    (out / "VERSIONS.json").write_text(json.dumps(versions, indent=2, sort_keys=True) + "\n")
    return [], versions


def main(argv):
    check = "--check" in argv
    kw = {}
    for flag in ("--config", "--out"):
        if flag in argv:
            kw[flag[2:]] = argv[argv.index(flag) + 1]
    problems, versions = build(check=check, **kw)
    for p in problems:
        print(f"vendor skills: {p}", file=sys.stderr)
    if not problems:
        print(f"vendor skills: {len(versions)} skills {'match' if check else 'built'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
