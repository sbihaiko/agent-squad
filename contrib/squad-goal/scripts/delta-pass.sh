#!/bin/bash
# usage: delta-pass.sh   Carries the full pass in $SG_STATE/gate-status-full across the delta to the dev tip (ADR-0156, #946).
# The carry needs P (that full pass) to be an ancestor of the dev tip T, at most 25 changed paths, no binary change, and every
# changed path covered by a line of delta-guards.txt (glob, TAB, command id). Each other case refuses, runs nothing and writes
# no pass record. The suite runs in a clean tree at T under the full-proof-delta lock, in try mode: a held lock exits 73 and runs nothing.
# SG_DELTA_SUITE replaces the whole suite (ruff, mypy, the fast battery and the guards); a test names its own commands with it.
# The record: $SG_STATE/gate-status-delta, `<epoch> TAB delta TAB <pass|fail> TAB <P> TAB <T> TAB <sha256 of the changed-file list>`.
# The delta is the proof delivery keeps when the full suite cannot finish inside its bound (ADR-0163, ADR-0164): delivery
# outranks the proof, so the delta answers for the tip instead of holding the promotion, and it never answers for a red.
d=$(cd "$(dirname "$0")" && pwd)
[ -n "${SG_STATE:-}" ] || { echo "delta-pass: SG_STATE is not set" >&2; exit 2; }
state=$SG_STATE; mkdir -p "$state"
tree=${SG_DELTA_TREE:-$state/delta-tree}; remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
refuse() { echo "delta-pass: $1" >&2; exit 1; }
hash_list() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }
if [ "${1:-}" = --hold ]; then   # inside the lock: clean checkout at the tip, the suite, the record
  T=$2; P=$3; h=$4
  g checkout -q -f --detach "$T" && g clean -q -ffdx || refuse "the delta tree could not check out $T"
  out=$state/delta-suite.log
  if (cd "$tree" && bash -c "$SG_DELTA_RUN") > "$out" 2>&1; then res=pass; else res=fail; fi
  rec=$state/gate-status-delta; tmp=$rec.$$
  printf '%s\tdelta\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$res" "$P" "$T" "$h" > "$tmp" && mv "$tmp" "$rec"
  echo "delta-pass: $res: $T carries $P (log: $out)"
  [ "$res" = pass ]; exit $?
fi
IFS=$'\t' read -r _ kind res P _ < "$state/gate-status-full" 2>/dev/null
[ "$kind" = full ] && [ "$res" = pass ] || refuse "no full pass: $state/gate-status-full holds no full pass to carry"
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" 2>&1) || refuse "fetch failed: $(printf '%s' "$o" | tail -1)"
T=$(g rev-parse origin/dev) || exit 1
g cat-file -e "$P^{commit}" 2>/dev/null || g fetch -q origin "$P" 2>/dev/null
g merge-base --is-ancestor "$P" "$T" 2>/dev/null || refuse "the full pass $P is not an ancestor of the dev tip $T"
changed=$(g diff --name-only "$P..$T" | LC_ALL=C sort)
n=$(printf '%s\n' "$changed" | awk 'NF' | wc -l | tr -d ' ')
[ "$n" -le 25 ] || refuse "the delta has $n changed paths; the carry takes at most 25"
binary=$(g diff --numstat "$P..$T" | awk -F'\t' '$1 == "-" || $2 == "-"' | wc -l | tr -d ' ')
[ "$binary" = 0 ] || refuse "the delta holds a binary change; the carry takes none"
guards=()
uncov=()
while IFS= read -r p; do
  [ -n "$p" ] || continue
  hit=""
  while IFS=$'\t' read -r glob cmd; do
    case "$glob" in ""|\#*) continue;; esac
    if [[ "$p" == $glob ]]; then hit=1; [ "$cmd" = SELF ] && cmd=$p; guards+=("$cmd"); fi
  done < "$d/delta-guards.txt"
  [ -n "$hit" ] || uncov+=("$p")
done <<< "$changed"
if [ "${#uncov[@]}" -gt 0 ]; then
  list=$(printf '%s, ' "${uncov[@]}"); list=${list%, }
  refuse "uncovered paths: $list; each needs a line in delta-guards.txt; the full proof is needed"
fi
[ -z "$(g status --porcelain --untracked-files=no)" ] || refuse "the delta tree is dirty; it must be clean before the proof"
. "$d/caps.sh"
py=$(sg_gate_python)
suite=${SG_DELTA_SUITE:-"$py -m ruff check squad tests && $py -m mypy squad && $py -m pytest -p no:deepeval -m 'not heavy' -n ${SG_PROMOTE_WORKERS:-$(sg_battery_workers)}"}
if [ -z "${SG_DELTA_SUITE:-}" ]; then
  while IFS= read -r c; do
    case "$c" in *.py) suite="$suite && $py -m pytest -p no:deepeval $c";; *) suite="$suite && bash $c";; esac
  done < <(printf '%s\n' "${guards[@]:-}" | awk 'NF' | LC_ALL=C sort -u)
  # The assembled suite is what the map decides; a test reads it here instead of running ruff, mypy and the battery.
  [ -n "${SG_DELTA_PRINT_SUITE:-}" ] && { printf '%s\n' "$suite"; exit 0; }
fi
if [ -z "${SG_DELTA_SUITE:-}" ]; then
  miss=$(sg_missing_tool "$py")
  [ -z "$miss" ] || refuse "$py holds no $miss, so the suite would answer a red of the tip that is a red of the proof"
fi
h=$(printf '%s\n' "$changed" | hash_list)
SG_DELTA_RUN=$suite SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SG_DELTA_HOLD_SECONDS:-10800} \
  bash "$d/serial.sh" full-proof-delta bash "$0" --hold "$T" "$P" "$h" 2>"$state/delta-pass.err"; rc=$?
[ $rc = 73 ] && { echo "delta-pass: another proof holds the lock; skipped, not queued" >&2; exit 73; }
cat "$state/delta-pass.err" >&2; exit $rc
