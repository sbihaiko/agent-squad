"""Build dashboard/agent-squad-run-dashboard.html: one self-contained file with no server.

The schema, the gate registry, the rule constants, and a snapshot of the served lists are read from the
engine and the repo, never typed by hand. The built page is committed; `test_the_built_dashboard_is_current` fails when it is stale.
Run: python3 scripts/build_dashboard.py [--home]
--home adds the skills in ~/.claude/skills to the snapshot. The committed page never uses it, because a home folder differs per machine.
"""
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from squad import graph  # noqa: E402
from squad.gates import GATE_REGISTRY  # noqa: E402

DASH = ROOT / "dashboard"
OUT = DASH / "agent-squad-run-dashboard.html"
DESIGN = ROOT / "docs" / "design"
FIXTURES = ("five-phases", "router")


def _skill_dirs(base):
    return sorted(p.name for p in base.iterdir() if (p.is_dir() or p.is_symlink()) and not p.name.startswith(".")) if base.is_dir() else []


def served_lists(home=False):
    """Suggestions for tools, skills, models, and efforts. A snapshot of what the repo uses; unknown values are never blocked."""
    cfg = yaml.safe_load((ROOT / "config" / "agent-defaults.yaml").read_text())
    tools, models, efforts = set(graph.WRITE_TOOLS), set(), set()
    opts = cfg.get("options", {})
    for src in [opts, *cfg.get("roles", {}).values()]:
        tools.update(src.get("allowed_tools") or [])
        tools.update(src.get("disallowed_tools") or [])
    models.update(m for m in (opts.get("model"), opts.get("fallback_model")) if m)
    efforts.update(e for e in (opts.get("effort"),) if e)
    skills = set(_skill_dirs(ROOT / "vendor" / "skills" / "skills")) | set(_skill_dirs(ROOT / ".claude" / "skills"))
    sources = ["config/agent-defaults.yaml", "vendor/skills/skills", ".claude/skills", *FIXTURES]
    if home:
        skills |= set(_skill_dirs(Path.home() / ".claude" / "skills"))
        sources.append("~/.claude/skills")
    for f in FIXTURES:
        for n in json.loads((DESIGN / f"{f}.graph.json").read_text())["nodes"]:
            tools.update(n.get("tools", []))
            if n.get("model"):
                models.add(n["model"])
            if n.get("effort"):
                efforts.add(n["effort"])
            skills.update(s["name"] if isinstance(s, dict) else s for s in n.get("skills", []))
            for key, bag in (("models", models), ("efforts", efforts), ("tools", tools)):
                bag.update((n.get("adapt") or {}).get(key) or [])
    lists = {"tools": sorted(tools), "skills": sorted(skills), "models": sorted(models), "efforts": sorted(efforts), "sources": sources}
    lists["hash"] = hashlib.sha256(json.dumps(lists, sort_keys=True).encode()).hexdigest()
    return lists


def engine_data():
    """What the core needs from the engine: the schema, the registry, and the rule constants."""
    return {
        "schema": json.loads(graph.SCHEMA_PATH.read_text()),
        "kinds": list(graph.KINDS),
        "builtin_facts": sorted(graph.BUILTIN_FACTS),
        "sees_forbidden": sorted(graph.SEES_FORBIDDEN),
        "write_tools": sorted(graph.WRITE_TOOLS),
        "forbidden_name": graph.FORBIDDEN_NAME.pattern,
        "forbidden_instrument": graph.FORBIDDEN_INSTRUMENT.pattern,
        "gate_registry": GATE_REGISTRY,
    }


# Dependency order: a file comes after every file it uses. make.js is the entry and comes last.
CORE_ORDER = ("canon.js", "figures.js", "record.js", "live.js", "failures.js", "children.js", "state.js", "totals.js", "replay.js", "groups.js", "stats.js", "schema.js", "edits.js", "layout.js", "review.js", "history.js", "sources.js", "control.js", "script.js", "make.js")
CORE_DIR = DASH / "core"


def core_bundle():
    """The one script text of the dashboard core: every file of dashboard/core/ in CORE_ORDER, `</script` escaped.
    The page inlines it and the node runner evaluates it, so the two cannot differ."""
    text = '"use strict";\n' + "".join((CORE_DIR / f).read_text() + "\n" for f in CORE_ORDER)
    return text.replace("</script", "<\\/script")


# The page script: one file per concern in dashboard/page/, in source order (a later file may use an earlier one at load time).
PAGE_ORDER = ("shell.js", "graph.js", "panel.js", "edit.js", "menu.js", "review.js", "run.js", "cards.js", "save.js", "chrome.js")
PAGE_DIR = DASH / "page"
PAGE_CSS = DASH / "page.css"
PAGE_HTML = DASH / "page.html"


def page_bundle():
    """The one page script: every file of dashboard/page/ in PAGE_ORDER, `</script` escaped. One scope, as the old inline script had."""
    text = '"use strict";\n' + "".join((PAGE_DIR / f).read_text() + "\n" for f in PAGE_ORDER)
    return text.replace("</script", "<\\/script")


def page_sources():
    """Every page source file, the markup and the CSS first: what the origin guard reads."""
    return [PAGE_HTML, PAGE_CSS, *(PAGE_DIR / f for f in PAGE_ORDER)]


def page_template():
    """The page with its CSS and script filled in, and the four data placeholders left. What the page tests read."""
    return PAGE_HTML.read_text().replace("/*CSS*/", PAGE_CSS.read_text()).replace("/*PAGE*/", page_bundle())


def _inline(obj):
    return json.dumps(obj, sort_keys=True).replace("</", "<\\/")


def build(home=False):
    html = PAGE_HTML.read_text()
    example = json.loads((DESIGN / "five-phases.graph.json").read_text())
    core = core_bundle()
    for key, value in (("/*ENGINE*/", _inline(engine_data())), ("/*EXAMPLE*/", _inline(example)), ("/*SERVED*/", _inline(served_lists(home))), ("/*CORE*/", core), ("/*CSS*/", PAGE_CSS.read_text()), ("/*PAGE*/", page_bundle())):
        assert html.count(key) == 1, key
        html = html.replace(key, value)
    return html


if __name__ == "__main__":
    OUT.write_text(build("--home" in sys.argv))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")
