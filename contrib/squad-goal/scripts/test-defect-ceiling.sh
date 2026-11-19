#!/bin/bash
# seconds: 6.19
# Regression test for the ceiling on the new issues ONE PASS files (#140, SPEC #117): a defect storm cannot starve a
# landing. The loop's one filer (autohunt.sh --file) files at most $SG_FILE_CEILING new issues for ONE pass -- the pass
# the caller names in SG_FILE_PASS, else the hunt that is running -- and a filing the ceiling held back is named with the
# fingerprint it would have filed, never dropped in silence. The filings spend through the loop's own accounting (the
# call log its one door writes, ghcall.sh), so a storm of defects cannot spend the calls a landing needs.
# Offline: stub gh, the real autohunt.sh beside ghcall.sh and ghread.py.
# usage: bash test-defect-ceiling.sh [path/to/autohunt.sh]
src=${1:-$(dirname "$0")/autohunt.sh}; here=$(cd "$(dirname "$src")" && pwd)
T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
[ -f "$src" ] || { echo "FAIL the filer exists: $src does not exist"; fail=1; }

# scene <name>: a loop sandbox -- the filer beside ghcall.sh and ghread.py (the one door to GitHub and the call log it
# appends), a stub env.sh, and a stub gh that numbers every issue it opens, records each POST and refuses a filing while
# $POSTFAIL exists.
scene() {
  D=$T/$1; S=$D/scripts
  mkdir -p "$S" "$D/st/auto" "$D/st/req" "$D/st/cq" "$D/home" "$D/bin"
  cp "$src" "$S/autohunt.sh"
  cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$S/"
  cat > "$S/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$D/st; SG_CHECKOUT=$D/co; SG_BASE=main
SG_CODING_RULES=rules; BOARD=true
E
  cat > "$D/bin/gh" <<'GH'
#!/bin/bash
echo "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *"--method POST"*)
    if [ -e "${POSTFAIL:-/dev/null}" ]; then
      printf '{"message":"API rate limit exceeded for user ID 1","status":"403"}'; exit 1
    fi
    echo "$*" >> "${GHPOST:-/dev/null}"
    for a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "${GHBODY:-/dev/null}";; esac; done
    n=$(cat "${GHNEXT:-/dev/null}" 2>/dev/null); [ -n "$n" ] || n=42; echo $((n+1)) > "${GHNEXT:-/dev/null}"
    echo "{\"number\":$n}";;
  *) echo '[]';;
esac
GH
  chmod +x "$D/bin/gh"
  export HOME=$D/home SG_STATE=$D/st GHLOG=$D/gh.log GHPOST=$D/ghpost GHBODY=$D/ghbody GHNEXT=$D/st/next POSTFAIL=$D/st/postfail
  : > "$GHPOST"; : > "$GHBODY"; : > "$GHLOG"; echo 41 > "$GHNEXT"; rm -f "$POSTFAIL" "$SG_STATE/api.log"
  unset SG_FILE_PASS SG_FILE_CEILING SG_GH_PASS SG_HUNT_FILE_CEILING
  export PATH="$D/bin:$PATH"
  printf 'repro\n' > "$D/body.md"
}
file() {   # file <name> [<fingerprint>]: one filing by the pass the sandbox names in SG_FILE_PASS
  local t=$1 fp=${2:-}
  if [ -n "$fp" ]; then bash "$S/autohunt.sh" --file "$t" "$D/body.md" --fingerprint "$fp" 2>&1
  else bash "$S/autohunt.sh" --file "$t" "$D/body.md" 2>&1; fi
}
posted() { wc -l < "$GHPOST" 2>/dev/null | tr -d ' '; }
ledger() { [ -f "$SG_STATE/auto/filed-$1" ] && { wc -l < "$SG_STATE/auto/filed-$1" | tr -d ' '; } || echo 0; }
storm() {   # storm <how many>: that many DISTINCT refusals, one pass
  local i; for i in $(seq 1 "$1"); do file "refused POST repos/o/r/issues/$i [fp$i]" "fp$i"; done
}

# --- the ceiling stops a storm, and names every skip ------------------------------------------------------------------
# 1) one pass files at most the ceiling, and every filing the ceiling held back is named with the fingerprint it would
#    have filed (#140)
scene storm; export SG_FILE_PASS=pass-1
s=$(storm 5)
[ "$(posted)" = 3 ] && ok "a storm of five distinct refusals files at most the declared ceiling of three" || ko "the storm filed $(posted) issue(s), not 3"
printf '%s\n' "$s" | $G -qF 'HUNT filed #41' && ok "the first refusal of the storm files one issue" || ko "first: '$s'"
printf '%s\n' "$s" | $G -qF 'HUNT filed #43' && ok "the third refusal still files, at the ceiling" || ko "third: '$s'"
printf '%s\n' "$s" | $G -qF 'HUNT hold fp4 refused POST repos/o/r/issues/4 [fp4]' && ok "the fourth refusal is held, named by the fingerprint it would have filed" || ko "fourth: '$s'"
printf '%s\n' "$s" | $G -qF 'HUNT hold fp5 refused POST repos/o/r/issues/5 [fp5]' && ok "the fifth refusal is held too, never dropped in silence" || ko "fifth: '$s'"
[ "$(printf '%s\n' "$s" | $G -c 'HUNT hold')" = 2 ] && ok "every skip of the storm is named, one line each" || ko "skips: '$s'"
printf '%s\n' "$s" | $G -qF 'HUNT filed #44' && ko "a held filing opened an issue anyway" || ok "a held filing opens no issue"
[ "$(ledger pass-1)" = 3 ] && ok "the pass's ledger holds exactly the issues it filed" || ko "ledger: $(cat "$SG_STATE/auto/filed-pass-1" 2>/dev/null)"

# 2) the ceiling is the one declared number: SG_FILE_CEILING, and the hunt request asks a child for that same number
scene ceiling; export SG_FILE_PASS=pass-c SG_FILE_CEILING=2
sc=$(storm 3)
[ "$(printf '%s\n' "$sc" | $G -c 'HUNT filed')" = 2 ] && ok "the ceiling is the declared SG_FILE_CEILING, not the default" || ko "SG_FILE_CEILING=2 filed $(storm 3 | $G -c 'HUNT filed')"
[ "$(ledger pass-c)" = 2 ] && ok "the pass's ledger stops at the declared ceiling" || ko "ledger: $(cat "$SG_STATE/auto/filed-pass-c" 2>/dev/null)"
scene declared; export SG_FILE_CEILING=2
AH_DRY=1 SG_HUNT_IDLE_MINUTES=0 SG_HUNT_COOLDOWN_MINUTES=0 bash "$S/autohunt.sh" >/dev/null 2>&1
body=$(cat "$SG_STATE/req/"*.body 2>/dev/null)
printf '%s' "$body" | $G -qF 'at most 2 bugs per hunt' && ok "the hunt request asks a child for the ceiling the filer enforces, from the one number" || ko "the request does not carry SG_FILE_CEILING: $($G -o 'at most [0-9]* bugs per hunt' <<<"$body")"
scene zero; export SG_FILE_PASS=pass-z SG_FILE_CEILING=0
z=$(storm 4)
[ "$(printf '%s\n' "$z" | $G -c 'HUNT filed')" = 3 ] && ok "a ceiling of zero is the default, never an unbounded filer" || ko "zero ceiling filed $(printf '%s\n' "$z" | $G -c 'HUNT filed')"
export SG_FILE_PASS=pass-j SG_FILE_CEILING=junk
j=$(storm 4)
[ "$(printf '%s\n' "$j" | $G -c 'HUNT filed')" = 3 ] && ok "a ceiling that is not a number is the default" || ko "junk ceiling filed $(printf '%s\n' "$j" | $G -c 'HUNT filed')"

# --- the ceiling is per pass, and the cause is what is counted ---------------------------------------------------------
# 3) another pass files its own ceiling: the bound is the pass's, never the loop's
scene passes; export SG_FILE_PASS=pass-a
storm 3 >/dev/null
export SG_FILE_PASS=pass-b
storm 3 >/dev/null
[ "$(posted)" = 6 ] && ok "a second pass files its own ceiling" || ko "the second pass filed $(posted) in all"
[ "$(ledger pass-a)" = 3 ] && [ "$(ledger pass-b)" = 3 ] && ok "each pass keeps its own ledger" || ko "ledgers: a=$(ledger pass-a) b=$(ledger pass-b)"

# 4) the cause is the key, never the occurrence: a repeated cause files once, and the repeat is named
scene dup; export SG_FILE_PASS=pass-d
a=$(file "refused POST repos/o/r/issues/7 [abc123]" abc123)
b=$(file "refused POST repos/o/r/issues/7 [abc123]" abc123)
[ "$(posted)" = 1 ] && ok "one pass files one cause once" || ko "the repeat filed: $(posted)"
printf '%s\n' "$a" | $G -qF 'HUNT filed #41' && ok "the first occurrence of the cause files the defect" || ko "first: '$a'"
printf '%s\n' "$b" | $G -qE 'HUNT dup #[0-9]+ abc123' && ok "the repeat is named with the issue and the fingerprint" || ko "repeat: '$b'"
[ "$(ledger pass-d)" = 1 ] && ok "a repeat spends no line of the pass's ledger" || ko "ledger: $(cat "$SG_STATE/auto/filed-pass-d" 2>/dev/null)"

# 5) a filing GitHub refused is no filing: named, and it spends no line of the pass's ledger (#1893, ADR-0277)
scene refused; export SG_FILE_PASS=pass-r; touch "$POSTFAIL"
s=$(file "refused PATCH repos/o/r/issues/9 [def456]" def456)
[ "$(posted)" = 0 ] && ok "a refused filing opens no issue" || ko "the refused filing opened $(posted)"
printf '%s\n' "$s" | $G -qF 'HUNT refused def456 refused PATCH repos/o/r/issues/9 [def456]' && ok "a refused filing is named with its fingerprint and its name" || ko "refused: '$s'"
[ "$(ledger pass-r)" = 0 ] && ok "a refused filing spends no line of the ceiling" || ko "ledger after a refusal: $(cat "$SG_STATE/auto/filed-pass-r" 2>/dev/null)"
rm -f "$POSTFAIL"
s=$(file "refused PATCH repos/o/r/issues/9 [def456]" def456)
printf '%s\n' "$s" | $G -qF 'HUNT filed #41' && ok "the cause the refused filing left unfiled files on the next try" || ko "retry: '$s'"

# --- the filing spends through the existing accounting (ADR-0277) ------------------------------------------------------
# 6) the POST goes through the one door, so the loop's call log carries it under the pass that spends it
scene log; export SG_FILE_PASS=pass-l SG_GH_PASS=autobug
file "refused POST repos/o/r/issues/8 [aa]" aa >/dev/null
line=$(tail -1 "$SG_STATE/api.log" 2>/dev/null)
case "$line" in *" REST repos/o/r/issues write done autobug") ok "the filing's write is one line of the loop's call log, under the pass that spends" ;;
  *) ko "the call log holds no line for the filing: '${line:-}'";; esac
[ "$(ledger pass-l)" = 1 ] && ok "the ceiling is the pass's own ledger, and the spend is the pass's own line" || ko "ledger under a named spend: $(cat "$SG_STATE/auto/filed-pass-l" 2>/dev/null)"

# 7) a filing that names no pass and no hunt is a pass of one: one call, one issue, nothing to hold back
scene nopass
s=$(storm 4)
[ "$(posted)" = 4 ] && ok "a filing with no pass is never held back by a ledger" || ko "no pass filed $(posted)"
printf '%s\n' "$s" | $G -qF 'HUNT hold' && ko "a passing filing was held: '$s'" || ok "a filing that names no pass is a pass of one"

rm -rf "$T"; exit $fail
