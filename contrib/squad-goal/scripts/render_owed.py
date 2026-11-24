#!/usr/bin/env python3
"""A child of an issue that carries an "after" image owes a render (#2833, CLAUDE.md visual-inspection rule).

`render_owed(body, worktree)` is true when the issue body holds an "after" image and the child's worktree holds no render
evidence. An image is an "after" image when its alt text or its URL basename starts with `after`, or when it follows a
line that starts with `After` (a heading, a bold label or plain text). Render evidence is at least one PNG under
`.scratch/renders/` in the worktree: the child's one scratch folder (prompts/_contract.md). The rule reads that evidence,
never the child's status word, so a child that answers `pass` with no render owes it too.

`render_review()` is the loop's CHANGES review for a missing render, in the shape `revformat.py --shape` prints, with one
Blocking finding and a repro that exits non-zero while no render exists. childcc.py writes it before the wave entry, so
the existing fix round starts.
"""
import os, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # revformat sits beside this module

RENDERS = ".scratch/renders"
TAG = "no render under .scratch/renders/"   # the text that marks the loop's own render review, so a later pass knows it wrote it
_MD = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]*)")
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""\b(alt|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_LEAD = r"^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+|\|\s*)*(?:\*\*|__|\*|_)?\s*"
_AFTER_LINE = re.compile(_LEAD + r"after\b", re.I)
_BEFORE_LINE = re.compile(_LEAD + r"before\b", re.I)


def _images(line):
    """The (alt, url) of each markdown or <img> image on one line, in order."""
    got = [(alt, url) for alt, url in _MD.findall(line)]
    for tag in _IMG.findall(line):
        attrs = {m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None) for m in _ATTR.finditer(tag)}
        got.append((attrs.get("alt", ""), attrs.get("src", "")))
    return got


def _named_after(text):
    return text.strip().lower().startswith("after")


def after_image(body):
    """True when the body holds an "after" image. A line that starts with `Before` ends the section an `After` line opened."""
    under = False
    for line in (body or "").splitlines():
        if _AFTER_LINE.match(line): under = True
        elif _BEFORE_LINE.match(line): under = False
        for alt, url in _images(line):
            base = os.path.basename(url.split("?", 1)[0].split("#", 1)[0])
            if under or _named_after(alt) or _named_after(base): return True
    return False


def render_evidence(worktree):
    """True when the worktree holds at least one PNG under .scratch/renders/."""
    d = pathlib.Path(worktree) / RENDERS
    return d.is_dir() and any(p.is_file() and p.suffix.lower() == ".png" for p in d.rglob("*"))


def render_owed(body, worktree):
    return after_image(body) and not render_evidence(worktree)


def render_review():
    """The loop's CHANGES review for a missing render, rendered as revformat renders any answer."""
    import revformat
    item = {"location": "whole branch",
            "defect": f"the issue carries an after image, and the worktree holds {TAG}",
            "failure": "the visual inspection the issue asks for has no evidence, so the issue would close with its render unproven",
            "fix": ("Boot a throwaway hub on its own port, with a temporary SG_STATE, a temporary SG_HUB_DIR and a neutral"
                    " profile, as contrib/squad-goal/scripts/test-hub-e2e.py does. Render each changed screen through"
                    " ego-browser, compare it with the after image, and fix each defect. Save each final render as a PNG"
                    f" under {RENDERS}/ in this worktree."),
            revformat.REPRO: f"find {RENDERS} -name '*.png' 2>/dev/null | grep -q ."}
    return revformat.render({"verdict": "CHANGES", "blocking": [item], "debt": [], "resolved": []})
