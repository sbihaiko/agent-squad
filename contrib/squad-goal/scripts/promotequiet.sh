#!/bin/bash
# usage: . promotequiet.sh; promote_quiet   The dev-to-stg promotion's debounce, in one place (#1837).
#
# The watch cycle reads it before its promotion step (ADR-0145 rule 3): the latest landing in landed.tsv must be older than
# the quiet window, so a burst of landings earns one proof window instead of one pass per landing. The wait is bounded: the
# first cycle the window held leaves its stamp in promote-defer, and a pass starts once that stamp is older than
# SG_PROMOTE_MAX_DEFER (default: twice the window), so a stream of landings that never quiets postpones the proof by that
# bound and no longer. A bound of 0 keeps no bound.
#
# The CI event hook reads the same rule (#1837). Without it, every green dev tip would promote at once, and each promotion
# moves the stg tip, which resets the stg-to-main quiet window and starves main. One definition, two callers (ADR-0149).
sg_mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
promote_quiet() { local q d last now started stamp
  q=${SG_PROMOTE_QUIET_SECONDS:-900}; case "$q" in ''|*[!0-9]*) q=900;; esac
  d=${SG_PROMOTE_MAX_DEFER:-$(( q * 2 ))}; case "$d" in ''|*[!0-9]*) d=$(( q * 2 ));; esac
  now=$(date +%s); stamp=$SG_STATE/promote-defer
  last=$(awk -F'\t' '$1 > m { m = $1 } END { printf "%d", m }' "$SG_STATE/landed.tsv" 2>/dev/null); last=${last:-0}
  [ $(( now - last )) -ge "$q" ] && { rm -f "$stamp" "$SG_STATE/promote-defer-said"; return 0; }   # the burst ended: the window is open again
  [ "$d" -gt 0 ] || return 1                                        # no bound named: a continuing stream keeps the step held
  [ -e "$stamp" ] || : > "$stamp"                                   # the first blocked cycle starts the bound: the stamp's mtime
  started=$(sg_mtime "$stamp"); case "$started" in ''|*[!0-9]*|0) started=$now;; esac
  [ $(( now - started )) -lt "$d" ] && return 1
  rm -f "$stamp"; return 0; }
# promote_defer_note: the one line a held promotion writes in the cycle log (#2147), once for one reason (the last landing and
# the window). A green dev tip that waits then reads as a wait on the quiet window, never as a promotion the loop did not see.
# The stamp holds that reason, so a cycle every 100 s adds no line while the reason stands, and a new landing earns a new one.
promote_defer_note() { local q last now sig said
  q=${SG_PROMOTE_QUIET_SECONDS:-900}; case "$q" in ''|*[!0-9]*) q=900;; esac
  last=$(awk -F'\t' '$1 > m { m = $1 } END { printf "%d", m }' "$SG_STATE/landed.tsv" 2>/dev/null); last=${last:-0}
  now=$(date +%s); sig="landing-${last}-window-${q}"; said=$SG_STATE/promote-defer-said
  [ "$(cat "$said" 2>/dev/null)" = "$sig" ] && return 0
  printf '%s' "$sig" > "$said"
  echo "$(date +%T) PROMOTE defer: the last landing $(( now - last )) s ago; the quiet window is $q s, so the dev tip waits for its proof" >> "$SG_STATE/cycle.log"; }
