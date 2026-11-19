#!/bin/bash
# seconds: 1.75
# One state directory per repository, and one shared directory for what must not split (address.sh, env.sh). Two profiles
# whose repositories carry the same issue number each see only their own markers, live-run list, queued requests and
# queued comments. The provider limit markers and the quota cache are one per machine: a limit one loop writes is the
# limit every loop reads. The board cache is one per owner and project. The health agent is one launchd entry per
# repository, so each supervisor is watched and removing one leaves the other. Offline: throwaway profiles, stub gh and
# launchctl. usage: bash test-state-split.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME=$T/home SG_STATE_BASE=$T/base SG_PROFILES_DIR=$T/profiles SG_SQUAD=$T/squad GHLOG=$T/gh.log
mkdir -p "$HOME" "$T/profiles" "$T/bin" "$T/coa" "$T/cob" "$T/coc"; : > "$GHLOG"
mk() { printf ': "${SG_REPO:=%s}"; : "${SG_OWNER:=%s}"; : "${SG_PROJECT:=%s}"; : "${SG_CHECKOUT:=%s}"\n' "$2" "$3" "$4" "$5" > "$T/profiles/$1.sh"; }
mk a acme/a acme 4 "$T/coa"; mk b acme/b acme 4 "$T/cob"; mk c zed/c zed 9 "$T/coc"
printf '#!/bin/bash\nprintf "gh %%s\\n" "$*" >> "$GHLOG"; echo 1\n' > "$T/bin/gh"
printf '#!/bin/bash\nprintf "launchctl %%s\\n" "$*" >> "$GHLOG"\n' > "$T/bin/launchctl"; chmod +x "$T/bin/"*; export PATH="$T/bin:$PATH"
# what env.sh resolves for one profile, as KEY=value lines
ctx() { (cd "$T"; SG_PROFILE=$1 . "$d/env.sh" >/dev/null 2>&1 || exit 1; printf 'SG_STATE=%s\nSG_SHARED=%s\nSG_BOARD_CACHE=%s\n' "$SG_STATE" "${SG_SHARED:-}" "${SG_BOARD_CACHE:-}"); }
key() { ctx "$1" | sed -n "s/^$2=//p"; }
sa=$(key a SG_STATE); sb=$(key b SG_STATE)
[ -n "$sa" ] && [ -n "$sb" ] && [ "$sa" != "$sb" ] && case "$sa$sb" in "$T/base/a$T/base/b") true;; *) false;; esac \
  && ok "1 each repository has its own directory under the machine state directory" || ko "1 state dirs: a='$sa' b='$sb'"
# 1) the loop of each repository queues work for its own issue 7, through its own env.sh
for p in a b; do (cd "$T"; SG_PROFILE=$p . "$d/env.sh" >/dev/null 2>&1; mkdir -p "$SG_STATE/auto"; touch "$SG_STATE/auto/queued-7"
  echo "$T/co$p/runs/run-$p" >> "$SG_STATE/active-runs.txt"; echo "req of $p" > "$SG_STATE/req/fix-7.txt"; echo "comment of $p" > "$SG_STATE/cq/7-x.md"); done
for p in a b; do o=$([ $p = a ] && echo b || echo a); s=$(key $p SG_STATE)
  [ "$(cat "$s/active-runs.txt")" = "$T/co$p/runs/run-$p" ] && ok "1 $p sees only its own live-run list" || ko "1 $p live-run list: $(tr '\n' ' ' < "$s/active-runs.txt")"
  [ "$(cat "$s/req/fix-7.txt")" = "req of $p" ] && ok "1 $p sees only its own queued request for #7" || ko "1 $p request: $(cat "$s/req/fix-7.txt")"
  [ "$(cat "$s/cq/7-x.md")" = "comment of $p" ] && ok "1 $p sees only its own queued comment for #7" || ko "1 $p comment: $(cat "$s/cq/7-x.md")"
  rm -f "$(key $o SG_STATE)/auto/queued-7"; [ -e "$s/auto/queued-7" ] && ok "1 $p keeps its marker when $o clears its own" || ko "1 $o clearing its marker cleared $p's"
  touch "$(key $o SG_STATE)/auto/queued-7"; done
# 1) the comment flusher posts each queued comment to the repository whose directory holds it
touch -t "$(date -v-60S +%Y%m%d%H%M)" "$sa/cq/7-x.md" "$sb/cq/7-x.md"; bash "$d/cqflush.sh" --once >/dev/null 2>&1
grep -q 'repos/acme/a/issues/7/comments' "$GHLOG" && grep -q 'repos/acme/b/issues/7/comments' "$GHLOG" \
  && ok "1 the comment of each #7 is posted to its own repository" || ko "1 comment flush: $(grep issues "$GHLOG" | tr '\n' '|')"
# 2) env.sh makes nothing at the top of the machine state directory but the repository's own directory and the shared ones
extra=$(cd "$T/base" && ls -A | grep -vx 'a\|b\|c\|_shared\|_boards'); [ -z "$extra" ] && ok "2 nothing else is written at the top of the machine state directory" || ko "2 stray entries at the top: $extra"
# 3) one shared directory: a limit one loop writes is read by every loop
ha=$(key a SG_SHARED); hb=$(key b SG_SHARED); hc=$(key c SG_SHARED)
[ -n "$ha" ] && [ "$ha" = "$hb" ] && [ "$ha" = "$hc" ] && [ "$ha" != "$sa" ] && ok "3 every profile reads one shared directory ($ha)" || ko "3 shared dirs: a='$ha' b='$hb' c='$hc'"
[ -n "$ha" ] && [ -d "$ha" ] && ok "3 env.sh makes the shared directory" || ko "3 no shared directory made: '$ha'"
# 4) the board cache: one per owner and project
ba=$(key a SG_BOARD_CACHE); bb=$(key b SG_BOARD_CACHE); bc=$(key c SG_BOARD_CACHE)
[ -n "$ba" ] && [ "$ba" = "$bb" ] && ok "4 two repositories on one owner and project share one board cache" || ko "4 a='$ba' b='$bb'"
[ -n "$bc" ] && [ "$bc" != "$ba" ] && ok "4 another owner and project has its own board cache" || ko "4 c='$bc' a='$ba'"
case "$ba" in "$sa"/*|"$sb"/*) ko "4 the board cache sits in one repository's directory: $ba";; *) ok "4 the board cache sits in no repository's directory";; esac
# 5) one health agent per repository
(cd "$T"; SG_PROFILE=a bash "$d/health-install.sh" >/dev/null 2>&1); (cd "$T"; SG_PROFILE=b bash "$d/health-install.sh" >/dev/null 2>&1)
la=$(grep -l '<string>a</string>' "$HOME/Library/LaunchAgents/"*.plist 2>/dev/null); lb=$(grep -l '<string>b</string>' "$HOME/Library/LaunchAgents/"*.plist 2>/dev/null)
[ -n "$la" ] && [ -n "$lb" ] && [ "$la" != "$lb" ] && ok "5 each of two supervisors has its own health agent" || ko "5 agents: $(ls "$HOME/Library/LaunchAgents" 2>/dev/null | tr '\n' ' ')"
(cd "$T"; SG_PROFILE=a bash "$d/health-install.sh" uninstall >/dev/null 2>&1)
[ -n "$lb" ] && [ -e "$lb" ] && [ ! -e "$la" ] && ok "5 removing one repository's agent leaves the other's" || ko "5 after removing a: $(ls "$HOME/Library/LaunchAgents" 2>/dev/null | tr '\n' ' ')"
grep -q "<string>$sb/health.out</string>" "$lb" 2>/dev/null && ok "5 the agent logs into its repository's directory" || ko "5 agent b logs elsewhere"
rm -rf "$T"; exit $fail
