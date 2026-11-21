#!/bin/bash
# seconds: 0.09
# Guard for #923 and #1599: the phone holds the loop control, and one loop belongs to one project. The Now strip
# aggregates the hub's loop fact and one row per project opens that project's loop; the Loops list names every project and
# the change log across them; the loop page writes to one project only, through that project's own route. The primary
# carries the state: Cancel drain while draining (Stop now under •••), and Start refused with the count while a loop-bug is
# open. The change log names who, from which page and when; the switch has no Save and no all-loops switch.
# Static: the markup, the page's contract with the hub route, and the rule module. No browser, no server.
# usage: bash test-hub-mobile-loop.sh [path/to/hub-mobile.html] [path/to/hub-mobile-loop.js]
d=$(cd "$(dirname "$0")" && pwd); f=${1:-$d/hub-mobile.html}; js=${2:-$d/hub-mobile-loop.js}
python3 - "$f" "$js" <<'PY'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
r = open(sys.argv[2], encoding="utf-8").read()
fail = 0
def check(label, ok):
    global fail
    print(("ok   " if ok else "FAIL ") + label)
    fail |= not ok
def has(txt, i): return f'id={i}' in txt or f'id="{i}"' in txt
# The strip (rule 8): the aggregate over every project and one row per project. No single unnamed switch.
strip = ("loops-strip", "loops-open", "loops-agg", "loops-msg")
check("the Now strip has its aggregate and its status line, and no row of its own", all(has(t, i) for i in strip) and "loops-rows" not in t)
check("the strip is a button that opens the Loops list", 'openLayer("loops")' in t)
check("a project row opens that project's loop", re.search(r'openLayer\("loop", \{ slug:', t) is not None)
check("a row names its project, so no switch is unnamed", re.search(r'b\.setAttribute\("data-loop-project", p\.slug \|\| ""\)', t) is not None)
# The Loops list: every project and the change log across them; no switch for all loops at once.
loops = ("loops", "loops-list", "loops-log", "loops-backtop", "loops-state")
check("the Loops list has its projects, its change log and its Back", all(has(t, i) for i in loops))
check("the Loops list keeps its one Back at the top and none in a footer", "data-topback=loops" in t and "loops-backact" not in t)
check("the Loops list draws every project from the one fact", '$("loops-list").replaceChildren(...loopItems(ps))' in t)
check("the change log spans projects and names each one", re.search(r'e\.project \|\| "this project"', t) is not None)
# The loop page: one action row with one primary, Stop now under •••, the confirm that names what it kills.
page = ("looppage", "loop-title", "loop-state", "loop-facts", "loop-primary", "loop-secondary", "loop-dots", "loop-menu",
        "loop-menu-stop", "loop-confirm", "loop-confirm-text", "loop-confirm-go", "loop-confirm-no", "loop-reason",
        "loop-backtop")
check("the loop page has its state, its facts, its action row, its menu and its confirm", all(has(t, i) for i in page))
check("the loop page keeps its one Back at the top and none in its action row", "data-topback=loop" in t and "loop-backact" not in t)
check("the loop page reads the hub's one loop route", re.search(r'call\("/api/loop"(, *fresh)?\)', t) is not None)
check("a loop page writes to one project's own route", re.search(r'"http|"/api/loop" : "/" \+ loopSlug \+ "/api/loop"', t) is not None
      or '"/" + loopSlug + "/api/loop"' in t)
check("the write names the phone as its source", 'client: "phone"' in t)
check("the write is a POST with a JSON body", re.search(r'fetch\(path, \{ method: "POST", headers: \{ "Content-Type": "application/json" \}', t) is not None)
check("the primary carries one of the six states", 'prim.setAttribute("data-state", v.state)' in t)
check("Stop now sits under the dots menu", re.search(r'\$\("loop-dots"\)\.onclick = \(\) => \{ \$\("loop-menu"\)\.hidden', t) is not None)
check("Stop now opens a confirm before any write", re.search(r'\$\("loop-menu-stop"\)\.onclick = \(\) => \{[^}]*loop-confirm"\)\.hidden = false', t, re.S) is not None)
check("the confirm names the live runs and gates it kills", 'Stop now kills: ' in t and 'loopLiveNames(loopHere() && loopHere().live)' in t)
check("the confirm is the second ask: its own button writes the stop", 'loopAct("stop-now", { confirm: true })' in t)
check("the change log shows who, which page and when", '$("loops-log")' in t and 'loopChangeLine(e)' in t)
check("a failed start is still shown", "v.failed" in t and "The last start failed" in t)
check("the reason line is drawn by loopReason", '$("loop-reason").textContent = v.state === "refused" ? v.sub : ""' in t)
check("the phone has loopReason", re.search(r"^const loopReason = .*;$", t, re.M) is not None)
# The rule module: the aggregate and the one primary, pure and free of any write.
check("the aggregate counts on, off and draining", 'const loopAggregate = projects =>' in r and 'parts.push(c.on + " on")' in r)
check("the primary offers Cancel drain while draining", re.search(r'state === "draining"\) return \{[^}]*label: "Cancel drain"', r) is not None)
code = "\n".join(l for l in r.splitlines() if not l.lstrip().startswith("//"))
check("the primary offers Drain while on, never Stop now", re.search(r'state === "on"\) return \{[^}]*label: "Drain"', code) is not None and "Stop now" not in code)
check("an off loop with loop-bugs refuses the start and names the count", re.search(r'if \(bugs\) return \{ state: "refused"', r) is not None and '" loop-bug"' in r)
check("the refusal offers the loop-bugs as its secondary", 'secondary: "Loop-bugs"' in r)
check("Stop now is offered only while the loop runs", re.search(r'const loopStopNowOffered = .*state === "on" \|\| p\.state === "draining"', r) is not None)
check("the rule module reads no route and writes nothing", "fetch(" not in r and "POST" not in r)
block = t.split('<article id=looppage', 1)[1].split('</article>', 1)[0] if '<article id=looppage' in t else ""
check("the loop page has no Save control", block and not re.search(r"\bsave\b", block, re.I))
check("the loop page offers no all-loops switch", "data-loop-all" not in t and not re.search(r'loop-all', t))
check("the rule module has no Save control", not re.search(r"\bsave\b", r, re.I))
sys.exit(fail)
PY
rc=$?
[ "$rc" = 0 ] && echo "hub mobile loop: all ok" || echo "hub mobile loop: FAILED"
exit "$rc"
