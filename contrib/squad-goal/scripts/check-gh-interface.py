#!/usr/bin/env python3
"""No live script reaches GitHub directly: the reads and the writes go through the one interface (#8, ADR-0277).

The interface is the GitHub reader the loop already has -- `ghread.py`, whose outcome contract is an answer, a refusal
carrying GitHub's own message and request id, or a failure before GitHub reached a verdict -- reached through the shell
door `ghcall.sh` (`gh_api` for a read, `gh_get` for a read answered with another representation, `gh_send` for a
write). A script that keeps its own call site throws the contract away: `gh api` writes the error body to *stdout* and
exits non-zero, and it does not honour `-q` on a failed request, so `gh api … 2>/dev/null` hands the words of a rate
limit back where a JSON list was expected.

The revision of #8 adds the half with the worse consequence: a **write** behind the interface's back. A refused read
looks like an answer; a refused write looks like success -- `gh api -X PATCH … && touch moved` marks the card moved
while GitHub refused it, and the loop reads that marker as state. So a direct write is refused exactly as a direct read
is, and the script and the route are named either way.

Only *code* counts as a reach, and the two are tellable apart because a command substitution is code again: in
`open=" $(gh api repos/o/r/pulls)"` the read is a call, while in a prompt that says `read with gh pr view` it is prose.
So a shell line is masked -- every single-quoted and double-quoted region blanked in place, with `$( … )` and backtick
substitutions left standing -- and the reach is a `gh` token in command position on what is left: at the start of a
line, or after `;`, `|`, `&`, `(`, `$`, a backtick, a brace, or `then`/`do`/`else`. A `#` that starts a word ends the
line. In a Python module the reach is the argv itself (`["gh", "api", …]`) or a command string. The interface files are
exempt by name: an interface is by definition where the transport is called.

Migration budget (`gh-direct-calls.txt`, beside this file): the call sites that have not moved yet, one line per
script, `<script> <reaches>`, and a script absent from the file is allowed none. It is a budget and not an exemption
list: a script above its line is refused, and a script *below* its line is reported as `SHRINK` so the number cannot be
left standing after the caller moved. Each migrated caller lowers its own line, and the day the file holds nothing the
rule is exactly the one the ticket asks for -- every reach through the door, and no grandfather list at all.

    usage: check-gh-interface.py [tree]      exit 0 = within budget, 1 = a script reaches GitHub over its budget
           check-gh-interface.py --ledger [tree]   print the budget as the tree stands (one `<script> <reaches>` a line)
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

# The files allowed to call `gh`: the interface (the module and its shell door), and this check's own pair.
INTERFACE = {"ghread.py", "ghcall.sh", "check-gh-interface.py", "check-gh-interface.sh"}

# A suite is a `gh` stub serving the argv `gh` gets: spelling a direct call there is how a refusal is proven.
SUITE_PREFIX = "test-"

# The migration budget, beside this check: `<script> <reaches>` a line, `#` starts a comment.
LEDGER_NAME = "gh-direct-calls.txt"

# A `gh` token in command position: the reach is a command, not a word inside a message. Whitespace alone is not a
# separator here -- `read with git show x, gh pr view` is prose -- so the separators are named.
REACH = re.compile(r"(?:^|[;|&()`${}!]|\b(?:then|do|else|elif)\s)gh\s+([A-Za-z][A-Za-z-]*)([^\n]*)")

PY_ARGV = re.compile(r"""["']gh["']\s*,\s*["']([A-Za-z-]+)["']((?:\s*,\s*[^,\]]+)?)""")
PY_CMD = re.compile(r"""["']gh\s+([A-Za-z-]+)([^"']*)["']""")

METHOD = re.compile(r"^(?:-X|--method)(?:[=\s]+([A-Za-z]+))?$")
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# The verbs that write without a method flag. `gh api graphql` writes only when its query is a mutation.
WRITE_VERBS = {
    ("pr", "comment"), ("pr", "merge"), ("pr", "close"), ("pr", "reopen"), ("pr", "create"), ("pr", "edit"),
    ("pr", "ready"), ("pr", "review"), ("issue", "comment"), ("issue", "close"), ("issue", "reopen"),
    ("issue", "create"), ("issue", "edit"), ("issue", "delete"), ("issue", "transfer"), ("issue", "pin"),
    ("issue", "unpin"), ("workflow", "run"), ("workflow", "enable"), ("workflow", "disable"), ("label", "create"),
    ("label", "edit"), ("label", "delete"), ("label", "clone"), ("project", "item-add"), ("project", "item-edit"),
    ("project", "item-archive"), ("project", "item-delete"), ("project", "item-create"), ("run", "rerun"),
    ("run", "cancel"), ("run", "delete"), ("release", "create"), ("release", "edit"), ("release", "delete"),
    ("release", "upload"), ("cache", "delete"),
}
# The flags whose value is the next token, so the route is not mistaken for the flag's value.
VALUE_FLAGS = {"-q", "--jq", "-f", "--field", "-F", "--raw-field", "-H", "--header", "--input", "-p"}


def mask(line: str) -> str:
    """The shell code of one line, in place: quoted regions blanked, `$( … )` and backticks left standing as code.

    Blanking in place rather than removing keeps every index, so a match on the masked line points at the real column.
    An unbalanced quote ends nothing: the rest of the line is prose, which is the safe way round for a line a shell
    would refuse to run anyway.
    """
    out = list(line)
    stack = ["code"]   # code | sq | dq | sub
    i = 0
    while i < len(line):
        c, state = line[i], stack[-1]
        if state == "sq":
            out[i] = " "
            if c == "'":
                stack.pop()
            i += 1
        elif state == "dq":
            if c == "\\":
                out[i] = " "
                i += 2
            elif c == '"':
                stack.pop()
                out[i] = " "
                i += 1
            elif (c == "$" and line[i + 1 : i + 2] == "(") or c == "`":
                stack.append("sub")
                i += 2 if c == "$" else 1
            else:
                out[i] = " "
                i += 1
        elif c == "#" and (i == 0 or line[i - 1] in " \t;|&("):
            for j in range(i, len(line)):
                out[j] = " "
            break
        elif c == "\\":
            i += 2
        elif c in "'\"":
            stack.append("sq" if c == "'" else "dq")
            out[i] = " "
            i += 1
        elif (c == "$" and line[i + 1 : i + 2] == "(") or c == "`":
            stack.append("sub")
            i += 2 if c == "$" else 1
        elif state == "sub" and c == ")":
            stack.pop()
            i += 1
        else:
            i += 1
    return "".join(out)


def words(fragment: str) -> list[str]:
    """The words of one command, quotes removed. `shlex` wants balanced quotes; a half-quoted script is still a script."""
    try:
        return shlex.split(fragment)
    except ValueError:
        return fragment.split()


def api_route(rest: str) -> tuple[str, str]:
    """A `gh api` call's route and the method it carries (`""` for a plain read)."""
    toks = words(rest)
    method = ""
    i = 0
    while i < len(toks):
        t = toks[i]
        if not t.startswith("-"):
            return t, method
        m = METHOD.match(t)
        if m and m.group(1):
            method = m.group(1).upper()
            i += 1
        elif m and i + 1 < len(toks):
            method = toks[i + 1].upper()
            i += 2
        else:
            i += 2 if (i + 1 < len(toks) and "=" not in t and t in VALUE_FLAGS) else 1
    return "", method


def shell_reaches(line: str) -> list[tuple[str, str, str]]:
    """Every reach on one shell line as (kind, route, detail); kind is `read` or `write`.

    The *masked* line says where the call is -- prose is not a call site -- while the *original* line says what the
    route is, because a route worth naming is usually quoted and quoting is exactly what the mask blanks.
    """
    found: list[tuple[str, str, str]] = []
    for m in REACH.finditer(mask(line)):
        verb, rest = m.group(1), line[m.end(1) :]
        if verb == "api":
            route, method = api_route(rest)
            if method in WRITE_METHODS:
                found.append(("write", route, method))
            elif route == "graphql":
                found.append(("write" if re.search(r"\bmutation\b", line, re.I) else "read", "graphql", ""))
            else:
                found.append(("read", route, method))
            continue
        sub = rest.split()[0] if rest.split() and not rest.split()[0].startswith("-") else ""
        kind = "write" if (verb, sub) in WRITE_VERBS else "read"
        found.append((kind, " ".join(x for x in (verb, sub) if x), ""))
    return found


def python_reaches(line: str) -> list[tuple[str, str, str]]:
    """Every reach on one Python line: the argv of a `gh` subprocess (`["gh", "api", …]`) or a `gh …` command string.

    A module reaches GitHub by building the argv, so the argv is the reach: the token `"gh"` opening an argument list,
    and the quoted words after it up to the end of that list, are the call. Naming the route keeps the words as they
    are written -- `f"repos/{repo}/issues/{n}"` names the route the module reads, which is what a reader needs.
    """
    found: list[tuple[str, str, str]] = []
    pieces = re.findall(r'''"[^"]*"|'[^']*'|[^"']+''', line)
    for i, piece in enumerate(pieces):
        quoted = piece[:1] in "\"'" and piece[-1:] == piece[:1]
        body = piece[1:-1] if quoted else piece
        if quoted and re.match(r"^gh\s", body):   # a command string: `subprocess.run("gh api …", shell=True)`
            verb = body.split()[1] if len(body.split()) > 1 else ""
            rest = body.split(None, 2)[2] if len(body.split(None, 2)) > 2 else ""
            kind = "write" if (verb, rest.split()[0] if rest.split() else "") in WRITE_VERBS else "read"
            route, method = api_route(rest) if verb == "api" else (" ".join(filter(None, (verb, rest.split()[0] if rest.split() else ""))), "")
            if verb == "api" and method in WRITE_METHODS:
                kind = "write"
            found.append((kind, route or "(the route is computed at run time)", method))
            continue
        if body != "gh" or not quoted:
            continue
        before = pieces[i - 1].rstrip() if i else ""
        if before and before[-1] not in "([,":
            continue   # a word in prose, not an argument position
        argv: list[str] = []
        for p in pieces[i + 1 :]:
            if p[:1] in "\"'" and p[-1:] == p[:1]:
                argv.append(p[1:-1])            # an element of the argv: the route lives here
            elif "]" in p or ")" in p:
                break                            # the call ends here
            # anything else is a separator, an f-string's prefix or prose between the words
        verb = argv[0] if argv else ""
        rest = " ".join(argv[1:]) if verb == "api" else " ".join(argv[1:2])
        if verb == "api":
            route, method = api_route(rest)
            found.append(("write" if method in WRITE_METHODS else "read", route or "(the route is computed at run time)", method))
        else:
            kind = "write" if (verb, rest) in WRITE_VERBS else "read"
            found.append((kind, " ".join(filter(None, (verb, rest))), ""))
    return found


def scan(tree: Path) -> list[tuple[str, str]]:
    """Every reach in the tree's loop scripts, as (script name, the one line that reports it)."""
    live = tree / "contrib/squad-goal/scripts"
    out: list[tuple[str, str]] = []
    for f in sorted([*live.glob("*.sh"), *live.glob("*.py")]):
        if f.name in INTERFACE or f.name.startswith(SUITE_PREFIX):
            continue
        reaches = shell_reaches if f.suffix == ".sh" else python_reaches
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), start=1):
            for kind, route, detail in reaches(line):
                named = f"{route} ({detail})" if detail and route else (route or "(the route is computed at run time)")
                door = "gh_send" if kind == "write" else "gh_api"
                out.append(
                    (f.name, f"  {f.name}:{n} {kind} {named} -- through {door} (contrib/squad-goal/scripts/ghcall.sh)")
                )
    return out


def ledger(tree: Path) -> dict[str, int]:
    """The migration budget: each script that still reaches GitHub directly to the reaches it may still have.

    A script the file does not name is allowed none, so a live script that starts reading GitHub on its own is refused
    from its first line -- the budget grows for nobody.
    """
    f = tree / "contrib/squad-goal/scripts" / LEDGER_NAME
    budget: dict[str, int] = {}
    if not f.is_file():
        return budget
    for line in f.read_text(errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, count = line.partition(" ")
        budget[name] = int(count.strip() or 0)
    return budget


def report(tree: Path) -> list[tuple[str, str]]:
    """Every reach over the budget, as (script name, the line that reports it), with a SHRINK notice on stderr."""
    reaches = scan(tree)
    budget = ledger(tree)
    by_script: dict[str, list[str]] = {}
    for name, line in reaches:
        by_script.setdefault(name, []).append(line)
    for name, allowed in sorted(budget.items()):
        left = allowed - len(by_script.get(name, []))
        if left > 0:
            print(
                f"SHRINK: {name} is down to {len(by_script.get(name, []))} direct reach(es); "
                f"lower its line in {LEDGER_NAME} to that number (the budget only shrinks).",
                file=sys.stderr,
            )
    return [(name, line) for name, lines in by_script.items() if len(lines) > budget.get(name, 0) for line in lines]


def main(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent
    args = [a for a in argv[1:] if not a.startswith("-")]
    tree = Path(args[0]).resolve() if args else here.parents[1]
    if not (tree / "contrib/squad-goal/scripts").is_dir():
        print(f"ERROR: no contrib/squad-goal/scripts under {tree}", file=sys.stderr)
        return 1
    if "--ledger" in argv:
        counts: dict[str, int] = {}
        for name, _ in scan(tree):
            counts[name] = counts.get(name, 0) + 1
        for name in sorted(counts):
            print(f"{name} {counts[name]}")
        return 0
    offences = report(tree)
    if not offences:
        print(
            "ok: no live script reaches GitHub over its budget "
            f"({len(ledger(tree))} script(s) still on the migration budget in {LEDGER_NAME})"
        )
        return 0
    for name in dict.fromkeys(n for n, _ in offences):
        lines = [line for n, line in offences if n == name]
        print(f"OFFENDS: {name}: {len(lines)} direct GitHub reach(es) bypass the interface")
        print("\n".join(lines))
    print(
        "ERROR: the scripts above call `gh` themselves (ADR-0277). A caller with its own call site reads a refusal as an "
        "answer and retries straight through a cooldown; a caller with its own write reads a refusal as success. Move each "
        'one onto the door: `. "$(dirname "$0")/ghcall.sh"`, then `gh_api <route> [-q <jq filter>]` for a read and '
        "`gh_send <gh api args>` for a write.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
