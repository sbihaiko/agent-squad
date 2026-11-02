#!/bin/bash
# usage: profile-check.sh <profile>   The one check a new profile runs once (#763). It names each setup gap, not just the
# first: the profile parses; SG_REPO, SG_OWNER, SG_PROJECT, SG_CHECKOUT and SG_BASE are set and are not the example's
# placeholders; the checkout's origin is SG_REPO; each gate, e2e and nightly command resolves; the board carries the shipped definition's vocabulary
# (board_define.py --check, a read); a dry frontier pass (AF_DRY=1) runs clean; the repository holds the labels the loop and
# /to-tickets use, and each missing one is named (creating it is a GitHub write, the owner's). Both passes run on a scratch state directory, so the loop's own state is never written. It reads
# GitHub through the loop's one door (ghcall.sh) and writes nothing. Exit 0 when clean, 1 when a gap is named.
# The hub loads profiles only at start: after a new profile passes, run `hubctl.sh --restart`.
here="$(cd "$(dirname "$0")" && pwd)"
name=${1:?usage: profile-check.sh <profile>}
dir=${SG_PROFILES_DIR:-$here/profiles}; f=$dir/$name.sh
[ -f "$f" ] || { echo "profile $name: no such profile ($f)"; exit 1; }
bash -n "$f" 2>/dev/null || { echo "profile $name: does not parse ($f)"; exit 1; }
# shellcheck disable=SC1090
. "$f" >/dev/null 2>&1 || { echo "profile $name: does not parse ($f)"; exit 1; }
: "${SG_LABELS_REQUIRED:=bug ready-for-agent ready-for-human needs-triage needs-info wontfix}"
gaps=0; gap() { echo "profile $name: $1"; gaps=$((gaps + 1)); }
for v in SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE; do [ -n "${!v:-}" ] || gap "$v is not set"; done
case "${SG_REPO:-}" in owner/repo) gap "SG_REPO is the example placeholder owner/repo";; ""|*/*/*) ;; */?*) ;; *) gap "SG_REPO '$SG_REPO' is not owner/name";; esac
[ "${SG_OWNER:-}" = owner ] && gap "SG_OWNER is the example placeholder owner"
if [ -n "${SG_CHECKOUT:-}" ]; then
  if [ -d "$SG_CHECKOUT" ]; then
    origin=$(git -C "$SG_CHECKOUT" remote get-url origin 2>/dev/null | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
    [ "$origin" = "${SG_REPO:-}" ] || gap "SG_CHECKOUT origin is '${origin:-none}', not ${SG_REPO:-}"
  else gap "SG_CHECKOUT $SG_CHECKOUT is no directory"; fi
fi
# Each gate, e2e and nightly command the profile names must resolve: its first word is a command, or a file run from the checkout.
for v in SG_FAST_GATE_CMD SG_LOCAL_GATE_CMD SG_LOCAL_GATE_RERUN_CMD SG_PREPUSH_CMD SG_E2E_CMD SG_NIGHTLY_PARALLEL SG_NIGHTLY_SERIAL \
  SG_SETTLE_RUFF SG_SETTLE_MYPY SG_SETTLE_PYTEST; do
  c=${!v:-}; [ -n "$c" ] || continue; w=${c%% *}
  case "$w" in */*) (cd "${SG_CHECKOUT:-.}" 2>/dev/null && [ -x "$w" ]) || gap "$v '$c': $w is no executable file in the checkout";;
    *) command -v "$w" >/dev/null 2>&1 || gap "$v '$c': $w is no command";; esac
done
# #770: the board vocabulary and a dry frontier pass, each on a scratch state directory, so no check writes the loop's state.
if [ -n "${SG_OWNER:-}" ] && [ -n "${SG_PROJECT:-}" ] && [ "${SG_REPO:-}" != owner/repo ]; then
  scratch=$(mktemp -d "${TMPDIR:-/tmp}/profile-check.XXXXXX"); trap 'rm -rf "$scratch"' EXIT
  bc=${SG_PF_BOARD_CHECK:-python3 $here/board_define.py}
  out=$(SG_STATE="$scratch/board" $bc --definition "$here/board-definition.json" --check "$SG_OWNER" "$SG_PROJECT" 2>&1); rc=$?
  case $rc in 0) ;; 1) gap "board vocabulary: $(printf '%s\n' "$out" | /usr/bin/grep -vE '^(read:|dry run)' | tr '\n' ';' | sed 's/;$//')";;
    *) gap "board vocabulary unreadable: $(printf '%s\n' "$out" | tail -n 1 | sed 's/^board_define\.py: board unreadable: //')";; esac
  if [ -n "${SG_FRONTIER_LABEL:-}" ]; then
    out=$(cd "${SG_CHECKOUT:-.}" 2>/dev/null && SG_PROFILE="$name" AF_DRY=1 SG_STATE="$scratch/frontier" bash ${SG_PF_FRONTIER:-$here/autofrontier.sh} 2>&1); rc=$?
    [ "$rc" = 0 ] || gap "dry frontier pass: exit $rc: $(printf '%s' "$out" | tail -n 1)"
  fi
fi
case "${SG_REPO:-}" in */?*)
  [ "$SG_REPO" = owner/repo ] && SG_REPO=""
  if [ -n "$SG_REPO" ]; then
    # shellcheck disable=SC1091
    . "$here/ghcall.sh"
    if have=$(gh_pages "repos/$SG_REPO/labels?per_page=100" -q '.[].name' 2>&1); then
      for l in $SG_LABELS_REQUIRED; do printf '%s\n' "$have" | /usr/bin/grep -qxF "$l" || gap "label missing: $l"; done
    else gap "labels unreadable: $(echo $have)"; fi
  fi;;
esac
[ "$gaps" = 0 ] || { echo "profile $name: $gaps gap(s)"; exit 1; }
echo "profile $name: ok (restart the hub with hubctl.sh --restart to load it)"
