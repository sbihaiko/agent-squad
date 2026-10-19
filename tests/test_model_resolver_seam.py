"""SPEC 505 ticket 1: the resolver seam guard and the two byte-identical copies.

The seam is a convention, not a scanner in production code: a file that moves a model id through a prefix operation
carries the header line `modelreg: seam-clean`, and this test enforces it there. A caller ticket adds the header to
its own file, so no shared allowlist is edited in parallel. A bare `claude:` object key is not a match.
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
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    return scan_text(text) if has_header(text) else []


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


@pytest.mark.parametrize("line,why", [
    ('    return m.startswith("claude-")', "startswith a model prefix"),
    ("    return m.removeprefix('claude:')", "removeprefix a model prefix"),
    ('    x="${m#claude:}"', "shell prefix strip"),
    ("  case $m in claude:*|grok-*) ;; esac", "case arm on a model prefix"),
    ("  case $m in grok-*) ;; esac", "case arm on a model prefix"),
    ("  const id = m.replace('[1m]', '');", "a context-suffix literal"),
    ('  if (m.startsWith("claude-")) {}', "js prefix test"),
])
def test_a_header_file_that_parses_a_prefix_is_a_violation(tmp_path, line, why):
    p = tmp_path / ("x.sh" if line.lstrip().startswith(("x=", "case")) else "x.py")
    p.write_text("# header\n# modelreg: seam-clean\n" + line + "\n")
    hits = scan_file(p)
    assert hits, f"the scan missed {why}"
    assert any(why == h[2] for h in hits), hits


def test_a_file_without_the_header_is_still_enforced(tmp_path):
    p = tmp_path / "legacy.py"
    p.write_text('return m.startswith("claude-")\n')
    assert scan_file(p), "an unmarked parser passed the guard"


def unpermitted(paths):
    """The violations outside the resolver seam and the recorded legacy debt."""
    out = []
    for p in paths:
        for n, line, why in scan_file(p):
            out.append(f"{p.name}:{n}: {why}: {line}")
    return out


def test_an_unmarked_new_parser_fails_the_guard(tmp_path):
    p = tmp_path / "newparser.py"
    p.write_text('def f(m):\n    return m.startswith("claude-")\n')
    assert unpermitted([p]), "a new parser with no header or legacy mark passed the guard"


def test_no_eligible_file_parses_a_model_prefix_outside_the_seam():
    assert unpermitted(scanned_files()) == []


def test_a_bare_claude_key_is_not_a_match(tmp_path):
    (tmp_path / "a.py").write_text('# modelreg: seam-clean\nSUBS = {"claude": {"plan": "max"}}\n')
    (tmp_path / "b.sh").write_text('# modelreg: seam-clean\ncase $sub in\n  claude) echo ok;;\nesac\n')
    assert scan_file(tmp_path / "a.py") == []
    assert scan_file(tmp_path / "b.sh") == []


def test_no_header_carrying_file_in_the_tree_parses_a_model_prefix():
    bad = []
    for p in scanned_files():
        for n, line, why in scan_file(p):
            bad.append(f"{p.relative_to(ROOT)}:{n}: {why}: {line}")
    assert bad == [], "\n".join(bad)
