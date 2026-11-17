#!/bin/bash
# #1417: the grok-limited marker expires after SG_GROK_LIMIT_TTL; one review probes grok under a kernel lock (fd 8).
# usage: bash test-review-grok-probe.sh [path/to/review.sh]
src=${1:-$(dirname "$0")/review.sh}; D=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SHARED="$T/st" SG_CAP_POLL=0.2; M="$SG_SHARED/grok-limited"
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"
for f in loopbug.sh revformat.py ghcall.sh drainpolicy.py ghread.py modelreg.py review-timeouts.py registry.json grokmark.py; do cp "$D/$f" "$T/s/" 2>/dev/null; done; : > "$T/s/codex-reset.py"
printf '{"review_models":["grok-4.6","claude:ok"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
. "$D/address.sh"
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *Accept*compare*) printf 'diff --git a/f b/f\n+x\n';;
  *compare*) printf '{"status":"ahead","files":[{"filename":"UI/a.cs"}]}\n';;
  *commits/*) echo '{"sha":"aaa111"}';;
  *issues/*) echo '{"number":8,"title":"the issue","body":"the acceptance criteria"}';;
  *) echo "stub";;
esac
J
printf '#!/bin/bash\ncat >/dev/null; echo "VERDICT: APPROVE"\n' > "$T/bin/claude"
# grok stub: counts its calls; GROK_MODE=402 answers a balance error, ok answers a verdict; GROK_SLEEP delays it
cat > "$T/bin/grok" <<J
#!/bin/bash
echo x >> "$T/grokcalls"; sleep "\${GROK_SLEEP:-0}"
[ "\${GROK_MODE:-402}" = ok ] && { echo "VERDICT: APPROVE"; exit 0; }
echo "Error: 402 Payment Required: Grok Build usage balance exhausted" >&2; exit 1
J
chmod +x "$T/bin/"*; export PATH="$T/bin:$PATH"
calls() { if [ -e "$T/grokcalls" ]; then wc -l < "$T/grokcalls" | tr -d " "; else echo 0; fi; }
reset() { rm -f "$T/st/review-"* "$T/st/cq/"* "$T/grokcalls" "$M"; }
rev() { bash "$T/s/review.sh" --branch "${2:-fix/1-x}" "${1:-8}" 2>/dev/null; }
old=$(( $(date +%s) - 90000 ))

reset; date +%s > "$M"; out=$(rev)
[ "$(calls)" = 0 ] && case "$out" in model=claude:ok*) true;; *) false;; esac && ok "1 a fresh marker skips grok" || ko "1 fresh marker: calls=$(calls) '$out'"

reset; echo "$old" > "$M"; rev >/dev/null; n=$(calls); new=$(cat "$M" 2>/dev/null)
[ "$n" = 1 ] && [ "${new:-0}" -gt "$old" ] && ok "2 an expired marker lets one try through; its 402 renews the epoch" || ko "2 expired: calls=$n marker=$new"
/usr/bin/grep -q 'next probe' "$T/st/cq/"*mquota* 2>/dev/null && ! /usr/bin/grep -q 'owner removes' "$T/st/cq/"*mquota* 2>/dev/null && ok "2b the MODEL_QUOTA line names the next probe time" || ko "2b wording: $(cat "$T/st/cq/"*mquota* 2>/dev/null)"

reset; echo "$old" > "$M"; export GROK_SLEEP=3; rev 8 fix/1-x >/dev/null & p1=$!; rev 9 fix/2-y >/dev/null & p2=$!; wait $p1 $p2; unset GROK_SLEEP
[ "$(calls)" = 1 ] && ok "3 two concurrent reviews on an expired marker make one grok try" || ko "3 concurrent: calls=$(calls)"

reset; echo "$old" > "$M"; GROK_MODE=ok rev >/dev/null
[ ! -e "$M" ] && [ "$(calls)" = 1 ] && ok "4 a real answer removes the marker" || ko "4 real answer: marker=$(cat "$M" 2>/dev/null) calls=$(calls)"

python3 "$T/s/grokmark.py" state "$SG_SHARED" 2>/dev/null | /usr/bin/grep -q . || ko "4b grokmark.py state prints nothing"
reset; echo "$old" > "$M"; export GROK_SLEEP=6 GROK_MODE=402
rev 8 fix/1-x >/dev/null & rp=$!; sleep 2; pkill -KILL -P $rp 2>/dev/null; kill -KILL $rp 2>/dev/null; wait $rp 2>/dev/null
rev 9 fix/2-y >/dev/null; n=$(calls)
[ "$n" = 1 ] && ok "5 killing only the holder keeps the lock: no second probe" || ko "5 holder killed: calls=$n"
sleep 6; unset GROK_SLEEP; echo "$old" > "$M"; rm -f "$T/st/review-"*; rev 9 fix/2-y >/dev/null
[ "$(calls)" = 2 ] && ok "5b the lock frees when the last holder exits" || ko "5b after the child: calls=$(calls)"

reset; export SG_GROK_LIMIT_TTL=2; echo "$old" > "$M"; rev >/dev/null; e1=$(cat "$M"); sleep 3; rm -f "$T/st/review-"*; rev >/dev/null; e2=$(cat "$M")
[ "$(calls)" = 2 ] && [ "$e2" -gt "$e1" ] && ok "6 two expiry cycles renew twice" || ko "6 cycles: calls=$(calls) $e1 $e2"
unset SG_GROK_LIMIT_TTL

reset; echo "$old" > "$M"; jq '.model_retry_budget=3' "$T/s/models.json" > "$T/m" && mv "$T/m" "$T/s/models.json"; rev >/dev/null
[ "$(calls)" = 1 ] && ok "7 a retry budget above one still breaks on a 402" || ko "7 budget 3: calls=$(calls)"
# 8: a 402 with no marker on disk (another review cleared it) still writes under the lock, and the lock is free once the try is over
reset; echo "$old" > "$M"; export GROK_SLEEP=1; GROK_MODE=402
cat > "$T/bin/claude" <<J
#!/bin/bash
cat >/dev/null; (cd "$T/s" && python3 -c "import grokmark;print(grokmark.lock_held('$SG_SHARED'))") > "$T/lockseen"; echo "VERDICT: APPROVE"
J
rev >/dev/null; unset GROK_SLEEP
[ "$(cat "$T/lockseen" 2>/dev/null)" = False ] && ok "8 fd 8 is closed after the grok try: a later model holds no lock" || ko "8 lock seen by the next model: $(cat "$T/lockseen" 2>/dev/null)"
rm -f "$M"; python3 - "$T/s/grokmark.py" "$SG_SHARED" <<'PY'
import fcntl, os, subprocess, sys
f = open(os.path.join(sys.argv[2], "grok-probe.lock"), "a"); fcntl.flock(f, fcntl.LOCK_EX)
r = subprocess.run([sys.executable, sys.argv[1], "renew", sys.argv[2]], env=dict(os.environ, SG_GROK_LOCK_BOUND="0.3"))
sys.exit(0 if r.returncode == 1 and not os.path.exists(os.path.join(sys.argv[2], "grok-limited")) else 1)
PY
[ $? = 0 ] && ok "9 a renew with no marker waits for the probe lock and writes nothing past its bound" || ko "9 renew wrote without the lock"
[ "$(python3 "$T/s/grokmark.py" when 0 | cut -c1-2)" -ge 19 ] && ok "10 the next probe time is formatted by python" || ko "10 when"
rm -rf "$T"; exit $fail
