"""Build vendor/skills: the local plugin of researched skills that agent nodes load.

Usage: python3 scripts/build_vendor_skills.py [--check] [--key] [--config FILE] [--out DIR]
Copies each skill in config/vendor-skills.yaml (a `skill:NAME` source is found by name) into <out>/skills/<name>/, writes the plugin manifest, and writes
VERSIONS.json with a tree hash per skill. After copying a skill, it appends config/vendor-skills-patches/<name>.md to
the copy's SKILL.md when that patch file exists, so the repository's own rules survive `npx skills update`; the tree
hash covers the appended text. `--check` builds nothing: it exits 1 if a source differs from the copy, or
the copy differs from VERSIONS.json. `--key` builds nothing: it prints one digest of everything the build reads (the
config, the patches, the personal skills, the vendor tree and VERSIONS.json), so a guard can skip the build when nothing changed.
Update the copy when a skill changes, and commit the result.
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
PATCH_DIR = ROOT / "config" / "vendor-skills-patches"


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


def patch_for(name, patches=None):
    """The repository's rules for one skill, appended to its vendored SKILL.md; empty when there is no patch file."""
    p = Path(patches) / f"{name}.md" if patches is not None else PATCH_DIR / f"{name}.md"
    return p.read_text() if p.exists() else ""


def with_patch(data, patch):
    """The bytes a copy holds: the source file, then the patch after one blank line."""
    return data + b"\n" + patch.encode() if patch else data


def patched_tree_hash(src, rels, patch):
    """The tree hash of the copy the build writes: every source file, with the patch appended to SKILL.md."""
    h = hashlib.sha256()
    for r in rels:
        data = (Path(src) / r).read_bytes()
        if Path(r) == Path("SKILL.md"):
            data = with_patch(data, patch)
        h.update(str(r).encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def resolve(src):
    """A `skill:NAME` source is found by name; anything else is a path."""
    if str(src).startswith("skill:"):
        hit = skillpath.resolve(str(src)[6:])
        return hit.folder if hit else ROOT / "no-such-skill" / str(src)[6:]
    p = Path(src).expanduser()
    return p if p.is_absolute() else ROOT / p


def build(config=ROOT / "config" / "vendor-skills.yaml", out=ROOT / "vendor" / "skills", check=False, patches=None):
    """Return (problems, versions). With check, nothing is written."""
    out, problems, versions = Path(out), [], {}
    skills = yaml.safe_load(Path(config).read_text())["skills"]
    for s in skills:
        src = resolve(s["source"])
        if not (src / "SKILL.md").exists():
            problems.append(f"{s['name']}: no SKILL.md at {src}")
            continue
        rels = files(src)
        patch = patch_for(s["name"], patches)
        sha = patched_tree_hash(src, rels, patch)
        versions[s["name"]] = {"source": s["source"], "sha256": sha, "files": len(rels)}
        dest = out / "skills" / s["name"]
        if check:
            have = files(dest) if dest.exists() else []
            if have != rels or (have and tree_hash(dest, have) != sha):
                problems.append(f"{s['name']}: the copy differs from its source")
            continue
        shutil.rmtree(dest, ignore_errors=True)
        for r in rels:
            (dest / r).parent.mkdir(parents=True, exist_ok=True)
            if Path(r) == Path("SKILL.md"):
                (dest / r).write_bytes(with_patch((src / r).read_bytes(), patch))
            else:
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


def guard_key(config=ROOT / "config" / "vendor-skills.yaml", out=ROOT / "vendor" / "skills", patches=None):
    """One digest of everything the build reads: the config, the patches, the personal skills' sources, the vendor tree and VERSIONS.json.

    vendor-guard.sh runs this every cycle and skips the build when the digest equals the one it kept in $SG_STATE.
    """
    out = Path(out)
    h = hashlib.sha256()

    def feed(label, data):
        h.update(label.encode() + b"\0" + data + b"\0")

    feed("config", Path(config).read_bytes())
    for s in yaml.safe_load(Path(config).read_text())["skills"]:
        src = resolve(s["source"])
        rels = files(src) if src.exists() else None
        feed("patch:" + s["name"], patch_for(s["name"], patches).encode())
        feed("source:" + s["name"], tree_hash(src, rels).encode() if rels is not None else b"missing")
    rels = [p.relative_to(out) for p in sorted(out.rglob("*"))
            if p.is_file() and p.relative_to(out).as_posix() != "VERSIONS.json"]
    feed("vendor", tree_hash(out, rels).encode())
    versions = out / "VERSIONS.json"
    feed("versions", versions.read_bytes() if versions.exists() else b"missing")
    return h.hexdigest()


def main(argv):
    check = "--check" in argv
    kw = {}
    for flag in ("--config", "--out"):
        if flag in argv:
            kw[flag[2:]] = argv[argv.index(flag) + 1]
    if "--key" in argv:
        print(guard_key(**kw))
        return 0
    problems, versions = build(check=check, **kw)
    for p in problems:
        print(f"vendor skills: {p}", file=sys.stderr)
    if not problems:
        print(f"vendor skills: {len(versions)} skills {'match' if check else 'built'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
