"""SPEC 505 ticket 1: the resolver seam guard and the two byte-identical copies.

The seam is a convention, not a scanner in production code: `squad/modelreg.py` and its shipped copy are the only
files that move a model id through a prefix operation. The scan covers every eligible shipped script and dashboard
file, and a prefix operation outside those two files fails, header or no header: the header line
`modelreg: seam-clean` claims a file is clean, it never grants a permit. A bare `claude:` object key is not a match.

`LEGACY` records the callers that predate the seam and keep their own parsing until their own landing
(ADR-0147, Consequences). It is keyed by repo-relative path, so a new file with the same name is not covered, and
a test proves every entry still parses: an entry leaves the map in the landing that moves it to the resolver.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAIRS = [
    (ROOT / "config/models.registry.json", ROOT / "contrib/squad-goal/scripts/registry.json"),
    (ROOT / "squad/modelreg.py", ROOT / "contrib/squad-goal/scripts/modelreg.py"),
]
ROOTS = [ROOT / "contrib/squad-goal/scripts", ROOT / "dashboard", ROOT / "scripts"]
HEADER = re.compile(r"modelreg:\s*seam-clean")

# The seam itself: the resolver and its shipped copy. A prefix operation here is the seam, not a caller.
SEAM = {"squad/modelreg.py", "contrib/squad-goal/scripts/modelreg.py"}

# Shipped callers that predate the seam and keep their own parsing until their own landing (ADR-0147,
# Consequences). Keyed by repo-relative path; a name is never added, and an entry leaves the set when its
# landing moves it to the resolver.
# The hub is one program: hub.py compiles its hub_theme_*.py modules into its own namespace (#2742), so the one legacy
# entry that names the hub covers the theme files its code moved into.
HUB_THEMES = {str(p.relative_to(ROOT)) for p in (ROOT / "contrib/squad-goal/scripts").glob("hub_theme_*.py")}

LEGACY = {
    "contrib/squad-goal/scripts/hub.py": "the panel spells review models and validates the add-box shape",
    "contrib/squad-goal/scripts/launch.py": "the launch gate reads the off switch and the spent providers",
    "contrib/squad-goal/scripts/provkey.py": "the Qwen key gate names an id the registry does not carry",
    "contrib/squad-goal/scripts/mkreq.sh": "the request template names the [1m] suffix in prose",
}

# The prefix operations the seam replaces. Each names the operation, never the model catalogue.
PATTERNS = [
    ("startswith a model prefix", re.compile(
        r"startswith\s*\(\s*[\(\[]?\s*(?:'|\")(?:claude-|claude:|grok-|gemini-|gpt-)")),
    ("removeprefix a model prefix", re.compile(
        r"removeprefix\s*\(\s*(?:'|\")(?:claude-|claude:|grok-|gemini-|gpt-)")),
    ("shell prefix strip", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*#claude:")),
    ("case arm on a model prefix", re.compile(r"(?:^|[\s(])(?:claude:|grok-|gemini-|gpt-)\*")),
    ("a context-suffix literal", re.compile(r"\[1m\]")),
    ("js prefix test", re.compile(r"(?:startsWith|indexOf)\s*\(\s*(?:'|\")(?:claude-|claude:|grok-|gemini-|gpt-)")),
]


def has_header(text):
    return any(HEADER.search(line) for line in text.splitlines()[:5])


def scan_text(text):
    """The violations in one file's text: (line number, line, pattern name)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in PATTERNS:
            if rx.search(line):
                out.append((n, line.strip(), name))
    return out


def scan_file(path):
    """Every violation in one file. The header never grants a permit."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    return scan_text(text)


def unpermitted(paths, root=ROOT):
    """The violations outside the resolver seam and the recorded legacy debt."""
    out = []
    for p in paths:
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        if rel in SEAM or rel in LEGACY or rel in HUB_THEMES:
            continue
        out += [f"{rel}:{n}: {why}: {line}" for n, line, why in scan_file(p)]
    return out


def _scanned(path):
    if path.suffix not in (".py", ".sh", ".js"):
        return False
    return not (path.name.startswith("test-") or path.name.startswith("test_"))


def scanned_files():
    for root in ROOTS:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.is_file() and _scanned(p):
                yield p


def test_the_registry_and_the_resolver_are_byte_identical_copies():
    for source, shipped in PAIRS:
        assert source.is_file(), source
        assert shipped.is_file(), shipped
        assert source.read_bytes() == shipped.read_bytes(), f"{shipped} drifted from {source}"


def test_the_grok_marker_rule_is_a_byte_identical_copy():
    """#1417: the resolver imports grokmark from beside itself, so squad/ carries the same file the scripts ship."""
    assert (ROOT / "squad/grokmark.py").read_bytes() == (ROOT / "contrib/squad-goal/scripts/grokmark.py").read_bytes()


@pytest.mark.parametrize("line,why", [
    ('    return m.startswith("claude-")', "startswith a model prefix"),
    ("    return m.removeprefix('claude:')", "removeprefix a model prefix"),
    ('    x="${m#claude:}"', "shell prefix strip"),
    ("  case $m in claude:*|grok-*) ;; esac", "case arm on a model prefix"),
    ("  case $m in grok-*) ;; esac", "case arm on a model prefix"),
    ("  const id = m.replace('[1m]', '');", "a context-suffix literal"),
    ('  if (m.startsWith("claude-")) {}', "js prefix test"),
])
def test_a_header_claim_does_not_permit_a_prefix_parser(tmp_path, line, why):
    p = tmp_path / ("x.sh" if line.lstrip().startswith(("x=", "case")) else "x.py")
    p.write_text("# header\n# modelreg: seam-clean\n" + line + "\n")
    hits = scan_file(p)
    assert hits, f"the scan missed {why}"
    assert any(why == h[2] for h in hits), hits


def test_a_file_without_the_header_is_still_enforced(tmp_path):
    p = tmp_path / "legacy.py"
    p.write_text('return m.startswith("claude-")\n')
    assert scan_file(p), "an unmarked parser passed the guard"


def test_an_unmarked_new_parser_fails_the_guard(tmp_path):
    p = tmp_path / "newparser.py"
    p.write_text('def f(m):\n    return m.startswith("claude-")\n')
    assert unpermitted([p]), "a new parser with no header or legacy mark passed the guard"


def test_a_bare_claude_key_is_not_a_match(tmp_path):
    (tmp_path / "a.py").write_text('# modelreg: seam-clean\nSUBS = {"claude": {"plan": "max"}}\n')
    (tmp_path / "b.sh").write_text('# modelreg: seam-clean\ncase $sub in\n  claude) echo ok;;\nesac\n')
    assert scan_file(tmp_path / "a.py") == []
    assert scan_file(tmp_path / "b.sh") == []


def test_no_eligible_file_parses_a_model_prefix_outside_the_seam():
    assert unpermitted(scanned_files()) == []


def test_each_legacy_entry_still_parses_a_prefix():
    """A stale entry hides a hole: the set leaves an entry only when its landing moves it to the resolver."""
    live = {str(p.relative_to(ROOT)) for p in scanned_files() if scan_file(p)}
    stale = sorted(set(LEGACY) - live)
    assert stale == [], "a legacy entry no longer parses a prefix; remove it: " + ", ".join(stale)


def test_the_seam_is_the_resolver_and_its_shipped_copy():
    assert SEAM == {str(p.relative_to(ROOT)) for pair in PAIRS[1:] for p in pair}
    assert SEAM.isdisjoint(LEGACY)
