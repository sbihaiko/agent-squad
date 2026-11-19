#!/bin/bash
# seconds: 1.20
# Regression test for #1491: one parser reads the issues a commit message names. A `Refs #N` inside an inline backtick span, or
# inside a fenced block, is text and names no issue; on a line with an odd backtick count the text after the last backtick is
# text. Every other `Refs #N` counts: a subject "(Refs #5)", a mid-line "write. Refs #9", several per line. The five callers
# (commit_refs, child_of, refs_of, the landing's per-commit list, landed_key) call the parser by name, and no parse regex stays
# outside it. Real commits in a throwaway repo; no GitHub call.
# usage: bash test-wave-refparse.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; W="$d/wave.sh"; P=refs_in_message
# fn <name>: the text of one top-level function of wave.sh.
fn() { awk -v n="$1" '$0 ~ "^" n "\\(\\) *\\{" { p = 1 } p { print } p && /^}/ { exit }' "$W"; }
# names <message>: the issues the parser names, on one line.
names() { (eval "$(fn $P)"; printf '%s\n' "$1" | $P 2>/dev/null | sort -un | tr '\n' ' '); }

[ -n "$(fn $P)" ] && ok "parser $P exists" || ko "parser $P is missing in wave.sh"

m=$(printf 'fix x\n\nThe body quotes `Refs #9` as an example.\nRefs #42\n')
[ "$(names "$m")" = "42 " ] && ok "inline span names nothing, final Refs #42 counts" || ko "span: '$(names "$m")'"
m=$(printf 'fix x\n\n```\nRefs #9\n```\nRefs #7\n')
[ "$(names "$m")" = "7 " ] && ok "fenced block names nothing" || ko "fence: '$(names "$m")'"
[ "$(names 'fix x (Refs #5)')" = "5 " ] && ok "subject (Refs #5) names 5" || ko "subject: '$(names 'fix x (Refs #5)')'"
[ "$(names 'a. Refs #3 Refs #4')" = "3 4 " ] && ok "two per line name both" || ko "two: '$(names 'a. Refs #3 Refs #4')'"
[ "$(names 'write. Refs #93')" = "93 " ] && ok "mid-line Refs counts" || ko "mid: '$(names 'write. Refs #93')'"
[ "$(names 'a `b Refs #8')" = "" ] && ok "odd backtick count: text after the last one is text" || ko "odd: '$(names 'a `b Refs #8')'"
[ "$(names 'Refs #6 then `x`')" = "6 " ] && ok "Refs before a span counts" || ko "before: '$(names 'Refs #6 then `x`')'"

# landed_key rejects a commit whose only Refs #9 sits in backticks.
mkdir -p "$T/co"; $G init -q -b dev "$T/co"; cg() { $G -C "$T/co" -c user.email=a@b -c user.name=t "$@"; }
echo x > "$T/co/f"; cg add f; cg commit -q -m init; $G -C "$T/co" update-ref refs/remotes/origin/dev HEAD
echo y > "$T/co/f"; cg add f; cg commit -q -m 'quote only' -m 'It quotes `Refs #9` here.'
$G -C "$T/co" update-ref refs/remotes/origin/dev HEAD
r=$(SG_CHECKOUT="$T/co" base=dev bash -c "G=$G; eval \"\$(awk '/^$P\\(\\) *\\{/{p=1} p{print} p&&/^}/{p=0}' '$W')\"; eval \"\$(awk '/^landed_key\\(\\) *\\{/{p=1} p{print} p&&/^}/{p=0}' '$W')\"; landed_key 9")
[ -z "$r" ] && ok "landed_key 9 rejects a backtick-only Refs #9" || ko "landed_key 9 returned '$r'"
echo z > "$T/co/f"; cg add f; cg commit -q -m 'real (Refs #9)'; $G -C "$T/co" update-ref refs/remotes/origin/dev HEAD
r=$(SG_CHECKOUT="$T/co" base=dev bash -c "G=$G; eval \"\$(awk '/^$P\\(\\) *\\{/{p=1} p{print} p&&/^}/{p=0}' '$W')\"; eval \"\$(awk '/^landed_key\\(\\) *\\{/{p=1} p{print} p&&/^}/{p=0}' '$W')\"; landed_key 9")
[ "$r" = "$($G -C "$T/co" rev-parse HEAD)" ] && ok "landed_key 9 keeps a real Refs #9" || ko "landed_key 9 real: '$r'"

# call sites: each caller invokes the parser by name; no parse regex outside it.
for f in commit_refs child_of refs_of landed_key; do
  fn $f | /usr/bin/grep -q "$P" && ok "$f calls $P" || ko "$f does not call $P"
done
/usr/bin/grep -nE "$P" "$W" | /usr/bin/grep -qE 'refs=" \$\(' && ok "the landing per-commit lookup calls $P" || ko "the salvage lookup (refs=) does not call $P"
out=$(awk -v n="$P" '$0 ~ "^" n "\\(\\) *\\{" { s = 1 } !s { print } s && /^}/ { s = 0 }' "$W" \
  | /usr/bin/grep -E '[Rr]efs[: ]*#' | /usr/bin/grep -vE '^[[:space:]]*#|--grep' | /usr/bin/grep -E '(^|[^[:alnum:]_-])(grep|sed|awk)[[:space:]]|=~')
[ -z "$out" ] && ok "no parse regex outside $P (the --grep prefilter of landed_key excepted)" || ko "parse regex outside $P: $out"
exit $fail
