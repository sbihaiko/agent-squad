"""Find a borrowed skill by name (ADR-0129).

A skill this repo borrows lives outside it: in `~/.claude/skills`, or in a plugin that `claude plugin update` moves
to a new versioned folder. A hard-coded path breaks on the first upgrade, move or removal. This module looks the
skill up by name: the personal copy first (Claude Code ranks it above a plugin), then the skills the newest install
of a trusted plugin lists in its `plugin.json`. `~/.agents/skills` is not read: Claude Code reaches it only through
the links in `~/.claude/skills`.

`SQUAD_SKILL_HOME` replaces the home directory, for tests.

    python3 -m squad.skillpath NAME            print the SKILL.md path; exit 1 when nothing resolves
    python3 -m squad.skillpath NAME --all      every copy, in order, with its source
    python3 -m squad.skillpath --check         drift report for every borrowed skill; exit 1 on a missing one
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"learnings", ".git", "node_modules", "__pycache__"}    # left out of a copy and of its hash
SKIP_FILES = {"install.sh", ".DS_Store"}
HARNESS = "harness"
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


@dataclass(frozen=True)
class Hit:
    name: str
    path: Path          # the SKILL.md file
    kind: str           # personal | plugin
    detail: str = ""    # plugin name and version for a plugin hit

    @property
    def folder(self) -> Path:
        return self.path.parent


def home() -> Path:
    return Path(os.environ.get("SQUAD_SKILL_HOME") or Path.home())


def _version_key(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", v or ""))


def _plugin_installs(base: Path, cwd: Path) -> list[tuple[str, str, Path]]:
    """(plugin, version, install path) for each plugin install that applies to `cwd`, newest first."""
    try:
        plugins = json.loads((base / ".claude" / "plugins" / "installed_plugins.json").read_text())["plugins"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = []
    for key, entries in plugins.items():
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict) or not e.get("installPath"):
                continue
            scope_path = e.get("projectPath")
            if e.get("scope") in ("project", "local") and (not scope_path or Path(scope_path).resolve() != cwd.resolve()):
                continue     # a project or local install applies to that project only
            path = Path(e["installPath"])
            if path.is_dir():
                out.append((key.split("@")[0], str(e.get("version") or ""), path))
    out.sort(key=lambda t: _version_key(t[1]), reverse=True)
    return out


def _listed(install: Path) -> list[Path]:
    """The skill folders a plugin install loads: the `skills` of its plugin.json, else `skills/*`."""
    try:
        entries = json.loads((install / ".claude-plugin" / "plugin.json").read_text()).get("skills")
    except (OSError, ValueError, AttributeError):
        entries = None
    roots = [install / e for e in entries if isinstance(e, str)] if isinstance(entries, list) else [install / "skills"]
    out = []
    for r in roots:
        if (r / "SKILL.md").is_file():
            out.append(r)
        elif r.is_dir():
            out += [c for c in sorted(r.iterdir()) if (c / "SKILL.md").is_file()]
    return out


def _usable(f: Path) -> bool:
    """A SKILL.md that exists (a dangling link is no file) and says something."""
    try:
        return f.is_file() and bool(f.read_text().strip())
    except OSError:
        return False


def trusted_plugins(root: Path = ROOT) -> list[str]:
    return list(load_manifest(root).get("plugins", []))


def resolve_all(name: str, base: Path | None = None, cwd: Path | None = None, plugins: list[str] | None = None) -> list[Hit]:
    """Every copy of skill `name`, in the order Claude Code prefers: the personal copy, then one copy per trusted plugin.

    Not a hit: a dangling link, an empty SKILL.md, a skill the plugin's plugin.json does not list (so `deprecated/` and
    `in-progress/` stay out), an older install of a plugin that has a newer one (a skill the newer one dropped is gone),
    and an untrusted plugin. A door runs the skill's text with the door's authority, so a plugin that merely ships a
    skill of the same name must not be read.
    """
    if not NAME.fullmatch(name):
        return []
    base = base or home()
    cwd = cwd or Path.cwd()
    plugins = trusted_plugins() if plugins is None else plugins
    hits = []
    personal = base / ".claude" / "skills" / name / "SKILL.md"
    if _usable(personal):
        hits.append(Hit(name, personal, "personal"))
    done = set()
    for plugin, version, install in _plugin_installs(base, cwd):
        if plugin not in plugins or plugin in done:
            continue
        done.add(plugin)                     # the newest install only
        found = [f for f in _listed(install) if f.name == name and _usable(f / "SKILL.md")]
        if found:
            hits.append(Hit(name, found[0] / "SKILL.md", "plugin", f"{plugin} {version}".strip()))
    return hits


def resolve(name: str, base: Path | None = None, cwd: Path | None = None, plugins: list[str] | None = None) -> Hit | None:
    hits = resolve_all(name, base, cwd, plugins)
    return hits[0] if hits else None


def tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if p.is_file() and not (set(rel.parts[:-1]) & SKIP_DIRS) and p.name not in SKIP_FILES:
            h.update(str(rel).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def project_skills(root: Path = ROOT) -> set[str]:
    d = root / ".agents" / "skills"
    return {p.name for p in d.iterdir() if (p / "SKILL.md").exists()} if d.is_dir() else set()


def load_manifest(root: Path = ROOT) -> dict:
    return yaml.safe_load((root / "config" / "external-skills.yaml").read_text())


def door_targets(root: Path = ROOT) -> set[str]:
    """The skill each `agent-*` door opens: its name without the prefix."""
    return {n.removeprefix("agent-") for n in project_skills(root) if n.startswith("agent-") and n != "agent-squad-dashboard"}


def vendored(root: Path = ROOT) -> set[str]:
    """The skills `config/vendor-skills.yaml` copies by name (`skill:NAME`)."""
    cfg = yaml.safe_load((root / "config" / "vendor-skills.yaml").read_text())["skills"]
    return {str(s["source"])[6:] for s in cfg if str(s["source"]).startswith("skill:")}


def borrowed(root: Path = ROOT) -> set[str]:
    """Every skill the repo reads from outside itself: the doors' targets, the vendored sources and the manifest's references."""
    return door_targets(root) | vendored(root) | set(load_manifest(root).get("referenced", []))


def registered(root: Path = ROOT) -> set[str]:
    """Every command name the docs may use: project skills, borrowed skills, and the harness's own commands."""
    m = load_manifest(root)
    return project_skills(root) | borrowed(root) | set(m.get(HARNESS, [])) | set(m.get("optional", []))


def _day(f: Path) -> str:
    """The day a SKILL.md was last written: the quickest sign of which copy is newer."""
    return time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime))


def check(root: Path = ROOT, base: Path | None = None, cwd: Path | None = None, plugins: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(problems, warnings).

    A problem: a skill a door opens or the vendor builder copies resolves nowhere. A warning: a skill the docs name but
    no door opens resolves nowhere, or a personal copy differs from the plugin copy it shadows.
    """
    problems: list[str] = []
    warnings: list[str] = []
    hard = door_targets(root) | vendored(root)
    for name in sorted(borrowed(root)):
        hits = resolve_all(name, base, cwd, trusted_plugins(root) if plugins is None else plugins)
        if not hits:
            (problems if name in hard else warnings).append(f"{name}: not found in ~/.claude/skills or a trusted plugin's install")
            continue
        first, rest = hits[0], hits[1:]
        if first.kind == "personal" and rest and tree_hash(first.folder) != tree_hash(rest[0].folder):
            warnings.append(f"{name}: the personal copy ({_day(first.path)}) differs from the {rest[0].detail} copy ({_day(rest[0].path)}) and shadows it")
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m squad.skillpath", description=(__doc__ or "").split("\n")[0])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--all", action="store_true", help="every copy, in order")
    ap.add_argument("--check", action="store_true", help="drift report for every borrowed skill")
    a = ap.parse_args(argv)
    if a.check:
        problems, warnings = check()
        for p in problems:
            print(f"missing: {p}", file=sys.stderr)
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        print(f"borrowed skills: {len(borrowed())} checked, {len(problems)} missing, {len(warnings)} warnings")
        return 1 if problems else 0
    if not a.name:
        ap.error("a skill name or --check")
    hits = resolve_all(a.name)
    if not hits:
        print(f"skill '{a.name}' not found in ~/.claude/skills or a trusted plugin's install: it was removed or renamed upstream. "
              "Do not improvise its steps; say so and stop that skill.", file=sys.stderr)
        return 1
    for h in hits if a.all else hits[:1]:
        print(f"{h.path}\t{h.kind}\t{h.detail}" if a.all else h.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
