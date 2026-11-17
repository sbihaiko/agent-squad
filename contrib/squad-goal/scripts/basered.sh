#!/bin/bash
# basered.sh (#2056): the base-red dispatch hold. Sourced by autofrontier.sh after ghcall.sh.
#
# The source is the loop's own record, $SG_STATE/gate-history (gate.sh): the full-scope checks of a landing or a wave tip
# (stage `landing` or `tip`, scope `full`) whose outcome is pass or fail. An `unproven` check measured nothing, so it never
# sets the hold and never lifts it (ADR-0164); a delta check or a child gate is no full result on the base tip.
# A base red is the two newest such checks, both red, on two different trees, at the same failing reason: a red that one wave
# alone carries is that wave's, and the landing removes it; the same test red on two trees is red in the base they share.
# A green full check newer than the red lifts the hold.
#
# The red files one bug, once per red, through the loop's one bug door (autohunt.sh --file, with the startable label), and
# the hold names that bug. $SG_STATE/auto/base-red-bug keeps `<fingerprint> TAB <issue> TAB <newest red epoch> TAB <state>`.
# The hold lifts when that bug is closed; the same red stays lifted until a red newer than the close is measured.
# While the hold stands, the frontier dispatches nothing; the bug lane (autobug.sh) never reads it, so a bug still dispatches.
# SG_BASE_RED_HOLD=0 turns the hold off. SG_BASE_RED_FILE_CMD replaces the filer (a test names a stub).
#
# sg_base_red_hold: prints one line and returns 0 while the hold stands; returns 1 otherwise. A missing or unreadable
# history never holds.

# sg_base_red_read: the base red, as `<newest epoch> TAB <sha9> TAB <sha9> TAB <reason>`; exit 1 when the record shows none.
sg_base_red_read() {
  local h="${SG_STATE:?sg_base_red_read: no state directory}/gate-history"
  [ -r "$h" ] || return 1
  awk -F'\t' '$7 == "full" && ($14 == "tip" || $14 == "landing") && ($8 == "pass" || $8 == "fail") { n++; o[n] = $8; s[n] = $4; r[n] = $9; e[n] = $1 }
    END { if (n < 2 || o[n] != "fail" || o[n-1] != "fail" || s[n] == s[n-1] || r[n] != r[n-1] || r[n] == "" || r[n] == "-") exit 1
          printf "%s\t%s\t%s\t%s\n", e[n], substr(s[n-1], 1, 9), substr(s[n], 1, 9), r[n] }' "$h"
}

sg_base_red_hold() {
  [ "${SG_BASE_RED_HOLD:-1}" = 0 ] && return 1
  local red ep s1 s2 why fp m="$SG_STATE/auto/base-red-bug" mfp mnum mep mst out num st line
  red=$(sg_base_red_read) || return 1
  IFS=$'\t' read -r ep s1 s2 why <<< "$red"
  fp=$(printf '%s' "$why" | cksum | cut -d' ' -f1)
  mfp=; mnum=; mep=; mst=
  [ -r "$m" ] && IFS=$'\t' read -r mfp mnum mep mst < "$m"
  if [ "$mfp" = "$fp" ] && [ "$mst" = closed ] && [ "${mep:-0}" -ge "$ep" ] 2>/dev/null; then return 1; fi   # its bug closed, and no newer red
  if [ "$mfp" != "$fp" ] || [ "$mst" = closed ] || [ -z "$mnum" ]; then
    local body; body=$(mktemp "${TMPDIR:-/tmp}/basered.XXXXXX") || return 1
    printf 'The base carries a measured red (#2056): two full checks of the base tip, on two different trees (%s and %s), were red at the same place.\n\n```\n%s\n```\n\nThe frontier holds new dispatches until this bug closes or a full check of the base tip is green.\n' "$s1" "$s2" "$why" > "$body"
    out=$(SG_FILED_BY=basered SG_FILED_LABELS="${SG_BUG_LABEL:-bug},${SG_FRONTIER_LABEL:-ready-for-agent}" \
      ${SG_BASE_RED_FILE_CMD:-bash "$(dirname "${BASH_SOURCE[0]}")/autohunt.sh" --file} "the base carries a measured red: ${why#the base battery failed at }" "$body" --fingerprint "basered-$fp" 2>/dev/null)
    rm -f "$body"
    num=$(printf '%s\n' "$out" | sed -nE 's/^HUNT (filed|dup) #([0-9]+).*/\2/p' | head -1)
    [ -n "$num" ] || num=$(printf '%s\n' "$out" | /usr/bin/grep -oE '#[0-9]+' | head -1 | tr -d '#')
    mnum=$num; mst=open
    mkdir -p "$SG_STATE/auto"; printf '%s\t%s\t%s\t%s\n' "$fp" "${mnum:-}" "$ep" open > "$m.$$" && mv -f "$m.$$" "$m"
  elif [ -n "$mnum" ]; then
    st=$(gh_api "repos/$SG_REPO/issues/$mnum" -q .state 2>/dev/null) || st=
    if [ "$st" = closed ]; then
      printf '%s\t%s\t%s\t%s\n' "$fp" "$mnum" "$ep" closed > "$m.$$" && mv -f "$m.$$" "$m"
      return 1
    fi
  fi
  line="$(date -u '+%F %T') DISPATCH HOLD: the base carries a measured red (${why}; red at $s1 and $s2); bug #${mnum:-unfiled} owns it; only bugs dispatch until it closes or a full check is green"
  printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
  return 0
}
