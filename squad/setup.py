"""`squad setup`: plan, and with --apply add, the default ADRs of a project (#191, spec #174).

It only adds. A file that exists is never overwritten, so a second run changes nothing. ADR numbers start at 0000
and follow the project's own numbers; nothing is renumbered.
"""
import os
import re
import subprocess
from pathlib import Path

ADR_DIR = Path("docs/adr")
ADR_0000 = "0000-adrs-are-guides.md"
INDEX = "README.md"

ADR_0000_BODY = """## Context

An ADR records why {name} took a decision. The next change then starts from that reasoning. It does not rediscover it.

A project that treats every past decision as binding becomes locked into old rules. Old rules slow development and delay delivery.

## Decision

1. **An ADR is a guide for the evolution of {name}. It must not stand in the way.** The code, the facts, or the product direction can outgrow an ADR. Then update, rewrite, or delete that ADR in the change that outgrows it. The commit states the fact that forced the change.
2. **Git keeps the history.** Do not keep a superseded ADR alive for reference. An amendment rewrites the ADR it amends. An ADR that replaces another deletes it in the same commit. Citations that named the old ADR move in that commit.
3. **`CLAUDE.md` follows the same rule.** Keep it current in the change that makes it stale.
4. **The owner's own words outrank every ADR.**
5. **Follow a guide while it stands.** You are free to change an ADR. You are never free to ignore it. A red guard is still a defect.
6. **ADRs and instruction files are never ledgers.** They hold no dates, counts, statuses, issue numbers, run ids or amendment logs. A changed decision is rewritten in place.
"""

GUARD_NONE = "None yet. This rule is a draft until a named guard test exists."

_DEFAULT_ADRS = [
    ("a-failing-gate-is-a-defect", "A failing gate is a defect; a gate is a stop condition", """## Context

{name} uses gates: checks that stop work when they fail. A gate that is retuned until it passes no longer protects anything.

## Decision

1. **A failing gate is a defect.** Find the cause. Do not retune the expectation to make it pass.
2. **A gate is a stop condition, never a target.** Do not optimize work to satisfy the gate. Verify the instrument first.
3. Re-anchor an expectation only with evidence that the old one was wrong.
"""),
    ("a-red-test-is-fixed", "A red test is fixed, whoever broke it", """## Context

A red test in {name} blocks trust in the suite. An agent that finds a red test it did not cause can skip it. Then the suite rots.

## Decision

1. **A red test is fixed, whoever broke it.** Do not skip, delete, or mark it expected-to-fail to get past it.
2. Run the suite with `{test_command}` before every commit.
3. If the cause is outside the change, fix it in its own commit, or open a ticket in {tracker} and say so.
"""),
    ("commits-and-pushes-are-automatic", "Commits and pushes are automatic", """## Context

{name} works with agents. Asking for leave after every change slows each change and adds no safety.

## Decision

1. **Commit and push after every change. Do not ask.**
2. Each commit holds one change and a message that states why.
3. The owner can stop this rule at any time. Then the rule is rewritten or deleted (ADR-0000).
"""),
    ("language-of-the-record", "Everything written is {language}", """## Context

{name} has one language for its record. Mixed languages make search and review hard.

## Decision

1. **Everything written in {name} is {language}**: code, comments, docs, ADRs, commit messages, tickets.
2. Write short, plain sentences. One idea per sentence.
3. The owner can talk in another language. The record stays in {language}.
"""),
    ("where-decisions-go", "Where decisions go", """## Context

A decision with no fixed place gets lost, or lands in `CLAUDE.md` and turns it into a ledger.

## Decision

1. A durable engineering decision goes in an ADR in `docs/adr/`.
2. A decision for one ticket goes in a comment on that ticket in {tracker}.
3. A one-off fact or grant goes in a memory file. `MEMORY.md` is the index.
4. Anything else goes in the commit message.
5. `CLAUDE.md` holds rules and pointers only. It never holds a status, a date, or a count.
"""),
]


def _adr_set(opts):
    """The (slug, title, body) templates to add: ADR-0000 first, then the defaults the options keep."""
    skip = set()
    if not opts.get("auto_commit", True):
        skip.add("commits-and-pushes-are-automatic")
    if not opts.get("language"):
        skip.add("language-of-the-record")
    out = [("adrs-are-guides", "ADRs are guides, not locks", ADR_0000_BODY)]
    out += [t for t in _DEFAULT_ADRS if t[0] not in skip]
    return out


def _render(title, body, fields):
    text = f"# ADR-{{num}}: {title}\n\n## Status\n\nAccepted.\n\n" + body.rstrip("\n") + f"\n\n## Guard\n\n{GUARD_NONE}\n"
    return text.format(**fields)


INDEX_HEAD = """# Architecture Decision Records

Decisions for {name}. Numbers are stable, so cross-references hold. A number is never reused or renumbered.

## Index

| ADR | Decision |
|---|---|
"""


def _title(path):
    lines = path.read_text().splitlines()
    m = re.match(r"#\s*(?:ADR-\d+:\s*)?(.*)", lines[0]) if lines else None
    return (m.group(1) if m else "").strip() or path.stem


def _existing(adr_dir):
    return sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")) if adr_dir.is_dir() else []


def _index_text(name, adr_dir, new):
    """One row per ADR file: the ones that exist and the ones this run adds."""
    rows = {p.name: _title(p) for p in _existing(adr_dir)}
    rows.update(new)
    body = "".join(f"| [{f[:4]}]({f}) | {t} |\n" for f, t in sorted(rows.items()))
    return INDEX_HEAD.format(name=name) + body


CLAUDE = "CLAUDE.md"
AGENTS = "AGENTS.md"
POINTER = "See CLAUDE.md.\n"
SECTIONS = ["Purpose", "Layout", "Rules", "Where decisions go", "Agent skills"]


def _guard(text):
    m = re.search(r"^## Guard\s*\n+(.*?)(?=\n## |\Z)", text, re.S | re.M)
    return " ".join(m.group(1).split()) if m else GUARD_NONE


def _bullets(rules):
    lines = []
    for fname, title, guard in rules:
        link = f"[ADR-{fname[:4]}]({ADR_DIR / fname})"
        lines.append(f"- **{title}** ({link}). Guard: {guard}\n")
    return "".join(lines)


def _section_text(name, root_name, rules):
    if name == "Purpose":
        return f"## Purpose\n\n{root_name}: one line on what this project is for. Everything written is in one language; see the ADRs.\n"
    if name == "Layout":
        return ("## Layout\n\n```\nCLAUDE.md   this file: rules and pointers (AGENTS.md links here)\nGLOSSARY.md  domain terms\n"
                "docs/adr/   decisions; index in docs/adr/README.md\ndocs/agents/ issue tracker, triage labels, domain-doc rules\n```\n")
    if name == "Rules":
        return "## Rules\n\n" + _bullets(rules)
    if name == "Where decisions go":
        return ("## Where decisions go\n\n- A durable engineering decision: an ADR in `docs/adr/`.\n"
                "- A decision for one ticket: a comment on that ticket.\n- A one-off fact or grant: a memory file. `MEMORY.md` is the index.\n"
                "- Anything else: the commit message.\n")
    return "## Agent skills\n\nSee `docs/agents/` for the issue tracker, triage labels and domain docs.\n"


def _claude_plan(root, rules):
    path = root / CLAUDE
    if not path.exists():
        text = "# CLAUDE.md\n\nGuidance for agents in this repository. Rules and pointers only.\n\n"
        text += "\n".join(_section_text(n, root.name, rules) for n in SECTIONS)
        return [("create", CLAUDE, text)]
    have = path.read_text()
    heads = {h.strip().lower() for h in re.findall(r"^#{1,6}\s+(.*)$", have, re.M)}
    out = []
    missing = [n for n in SECTIONS if n.lower() not in heads]
    if missing:
        add = "\n".join(_section_text(n, root.name, rules) for n in missing)
        out.append(("append", CLAUDE, ("" if have.endswith("\n\n") else "\n" if have.endswith("\n") else "\n\n") + add))
    if "rules" not in missing:
        gone = [f"ADR-{f[:4]}" for f, _, _ in rules if f"ADR-{f[:4]}" not in have and f not in have]
        if gone:
            out.append(("report", CLAUDE, "Rules has no line for " + ", ".join(gone) + "; the file is not changed"))
    return out


def _agents_plan(root):
    path = root / AGENTS
    if path.is_symlink():
        return [("keep", AGENTS, "")] if os.readlink(path) == CLAUDE else [("report", AGENTS, f"is a symlink to {os.readlink(path)}, not to {CLAUDE}; not replaced")]
    if path.exists():
        text = path.read_text()
        same = text == POINTER or ((root / CLAUDE).exists() and text == (root / CLAUDE).read_text())
        return [("keep", AGENTS, "")] if same else [("report", AGENTS, f"exists with text that differs from {CLAUDE}; not replaced")]
    return [("link", AGENTS, CLAUDE)]


def _entry_files(root, rules):
    return _claude_plan(root, rules) + _agents_plan(root)


GUARD_INSTALL = Path("contrib") / "squad-goal" / "scripts" / "install-hooks.sh"


def guard_installer():
    """The tracked script that arms the commit guard (ADR-0177), found beside this package, or None."""
    path = Path(__file__).resolve().parent.parent / GUARD_INSTALL
    return path if path.is_file() else None


def _guard_plan(root):
    """Arm the commit guard in a repository, so a `slack.local.yaml` never enters a commit. A target outside a repository
    holds no hook to arm, and a checkout that carries no installer reports that instead of guessing a path."""
    if not (root / ".git").exists():
        return [("report", "commit guard", "not a repository; nothing to arm")]
    if guard_installer() is None:
        return [("report", "commit guard", f"no {GUARD_INSTALL}; run `squad slack check` by hand")]
    return [("arm", "commit guard", str(guard_installer()))]


def arm_guard(root):
    """Run the installer in the repository at root. Return its combined output; the guard's own rules decide whether the
    hook is written, so a foreign hook is reported and never overwritten."""
    installer = guard_installer()
    if installer is None:
        return f"commit guard: no {GUARD_INSTALL}"
    proc = subprocess.run(["bash", str(installer), str(root)], capture_output=True, text=True)
    return (proc.stdout + proc.stderr).strip() or f"commit guard: installer exited {proc.returncode}"


def plan(target, **opts):
    """Return a list of (action, relative path, text): action is "create", "keep", "append" (text is added to the file),
    "link" (AGENTS.md as a symlink to CLAUDE.md; text is the target) or "report" (text says why nothing changes).

    opts: language (str or None: the language rule is added only when set), test_command, tracker,
    auto_commit (bool, default True: False skips the commit rule).
    """
    root = Path(target).resolve()
    adr = root / ADR_DIR
    fields = {"name": root.name, "language": opts.get("language") or "",
              "test_command": opts.get("test_command") or "the project's test command",
              "tracker": opts.get("tracker") or "the issue tracker"}
    have = _existing(adr)
    nxt = max([int(p.name[:4]) for p in have] + [0]) + 1
    out, new, rules = [], {}, []
    for slug, title, body in _adr_set(opts):
        match = [p for p in have if p.name[5:-3] == slug] or ([p for p in have if p.name.startswith("0000-")] if slug == "adrs-are-guides" else [])
        if match:
            out.append(("keep", str(ADR_DIR / match[0].name), ""))
            rules.append((match[0].name, _title(match[0]), _guard(match[0].read_text())))
            continue
        if slug == "adrs-are-guides":
            num = "0000"
        else:
            num = f"{nxt:04d}"
            nxt += 1
        fname = f"{num}-{slug}.md"
        title = title.format(**fields)
        out.append(("create", str(ADR_DIR / fname), _render(title, body, {**fields, "num": num})))
        new[fname] = title
        rules.append((fname, title, GUARD_NONE))
    if (adr / INDEX).exists():
        out.append(("keep", str(ADR_DIR / INDEX), ""))
    else:
        out.append(("create", str(ADR_DIR / INDEX), _index_text(root.name, adr, new)))
    out += _entry_files(root, rules)
    out += _guard_plan(root)
    return out + _memory_plan(root, opts.get("memory_dir"))


MEMORY_INDEX = "MEMORY.md"
INDEX_NOTE = "<!-- One line per memory: - [Title](file.md) - hook. One file per fact; frontmatter type: user, feedback, project or reference. -->\n"


def memory_dir(root):
    """The project's memory directory: SQUAD_MEMORY_DIR, else the Claude Code project folder for the target (#196)."""
    env = os.environ.get("SQUAD_MEMORY_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root)) / "memory"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "fact"


def _memory_plan(root, mdir):
    """Plan the memory index (created when absent, never overwritten) and one proposed file per ledger line of CLAUDE.md (#196).

    Paths are absolute: the memory directory is not in the project. A "propose" entry is written only on approval."""
    mdir = Path(mdir) if mdir else memory_dir(root)
    index = mdir / MEMORY_INDEX
    out = [("keep", str(index), "")] if index.exists() else [("create", str(index), "# Memory index\n\n" + INDEX_NOTE)]
    for n, line in ledger_lines(root):
        fname = f"project_{_slug(line)}.md"
        if (mdir / fname).exists():
            continue
        fact = line.strip().lstrip("-* ").strip()
        text = f"---\nname: {fact[:60]}\ndescription: {fact[:100]}\ntype: project\n---\n\n{fact}\n\nMoved from {CLAUDE} line {n}.\n"
        out.append(("propose", str(mdir / fname), text))
    return out


def apply(target, **opts):
    """Write the planned files that do not exist. Return the relative paths written.

    A proposed memory file is written only when opts["approve_memory"] is true."""
    root = Path(target).resolve()
    written = []
    for action, rel, text in plan(root, **opts):
        path = root / rel
        if action == "append" and path.exists():
            with open(path, "a") as f:
                f.write(text)
            written.append(rel)
            continue
        if action == "link" and not (path.exists() or path.is_symlink()):
            try:
                os.symlink(text, path)
            except (OSError, NotImplementedError):
                path.write_text(POINTER)   # no symlinks here: a one-line pointer instead
                print(f"squad: cannot link {rel}; wrote a one-line pointer to {text}")
            written.append(rel)
            continue
        if action == "propose" and opts.get("approve_memory") and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            title = re.search(r"^name: (.*)$", text, re.M).group(1)
            with open(path.parent / MEMORY_INDEX, "a") as f:
                f.write(f"- [{title}]({path.name}) - moved from {CLAUDE}\n")
            written.append(rel)
            continue
        if action == "arm":
            print(f"squad: {arm_guard(root)}")
            written.append(rel)
            continue
        if action != "create" or path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x") as f:   # "x" fails on an existing file: never an overwrite
            f.write(text)
        written.append(rel)
    return written


_LINK = re.compile(r"\[ADR-(\d{4})\]\(([^)]*)\)")
_GUARD_PATH = re.compile(r"`((?:[\w.-]+/)+[\w.-]+\.\w+)`")


LEDGER_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}(?:, \d{4})?\b")
LEDGER_NOW = re.compile(r"\bcurrently\b|\bas of\b|\bat the moment\b|\bright now\b", re.I)
LEDGER_STATUS = re.compile(r"\b(?:done|open|closed|blocked|in progress|merged|shipped|resolved|pending|wip|landed|not started)\b", re.I)
TICKET = re.compile(r"(?:^|[\s(])#\d+\b")


def claude_limits(root):
    """The two CLAUDE.md limits (#195): plugin defaults, then the project settings file. PROVISIONAL; see config/agent-defaults.yaml."""
    from squad import defaults, project_settings
    cfg = dict(defaults.load().get("claude_md") or {})
    if project_settings.path_for(root).is_file():
        cfg.update(project_settings.load(root)[0].get("claude_md") or {})
    return int(cfg.get("max_line_width", 300)), int(cfg.get("max_section_lines", 40))


def ledger_lines(root):
    """The (line number, text) of each CLAUDE.md line that holds an event, a status or a "now": a candidate memory file (#196)."""
    path = root / CLAUDE
    if not path.is_file():
        return []
    return [(n, line) for n, line in enumerate(path.read_text().splitlines(), 1)
            if LEDGER_DATE.search(line) or LEDGER_NOW.search(line) or (TICKET.search(line) and LEDGER_STATUS.search(line))]


def ledger(root):
    """Return the lines of CLAUDE.md that read as a ledger, not as a rule or a pointer (#195)."""
    path = root / CLAUDE
    if not path.is_file():
        return []
    width, limit = claude_limits(root)
    out, head, count, sections = [], None, 0, []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if re.match(r"#{1,6}\s", line):
            sections.append((head, count))
            head, count = (n, line.lstrip("# ").strip()), 0
        elif line.strip():
            count += 1
        if len(line) > width:
            out.append(f"{CLAUDE} line {n}: {len(line)} characters, over {width}; a rule or a pointer is short")
        if LEDGER_DATE.search(line):
            out.append(f"{CLAUDE} line {n}: a dated sentence; an event goes to a memory file or the commit message")
        if LEDGER_NOW.search(line):
            out.append(f'{CLAUDE} line {n}: a "currently" or "as of" phrase; it goes stale')
        if TICKET.search(line) and LEDGER_STATUS.search(line):
            out.append(f"{CLAUDE} line {n}: a ticket status next to an issue number; the tracker holds status")
    sections.append((head, count))
    return out + [f'{CLAUDE} section "{h[1]}" (line {h[0]}): {c} lines, over {limit}' for h, c in sections if h and c > limit]


def check(target):
    """Return the drift between the rule ADRs and CLAUDE.md as a list of messages; empty means in step (#194).
    It also reports ledger content in CLAUDE.md (`ledger`, #195).

    A rule ADR is an ADR with a Guard section. Each needs a CLAUDE.md bullet: a line with its ADR link and a
    "Guard:" part. A bullet drifts when its link does not resolve, it has no guard, or a guard path does not exist.
    """
    root = Path(target).resolve()
    rules = {p.name[:4]: p for p in _existing(root / ADR_DIR) if re.search(r"^## Guard\s*$", p.read_text(), re.M)}
    path = root / CLAUDE
    lines = path.read_text().splitlines() if path.is_file() else []
    out, seen = [], set()
    if rules and not path.is_file():
        out.append(f"{CLAUDE} is missing")
    for line in lines:
        for num, link in _LINK.findall(line):
            if not (root / link).is_file():
                out.append(f"{CLAUDE}: ADR-{num} link {link} does not resolve")
                continue
            seen.add(num)
            if "Guard:" not in line:
                out.append(f"{CLAUDE}: the bullet for ADR-{num} names no guard")
            for g in _GUARD_PATH.findall(line.partition("Guard:")[2]):
                if not (root / g).exists():
                    out.append(f"{CLAUDE}: the guard {g} of ADR-{num} does not exist")
    out += [f"ADR-{n} ({p.name}) has no bullet in {CLAUDE}" for n, p in rules.items() if n not in seen]
    return out + ledger(root)
