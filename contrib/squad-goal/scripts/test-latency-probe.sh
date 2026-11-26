#!/bin/sh
# seconds: 2.26
# Guard: issue #2940. contrib/voice/latency_probe.py, beside operators.py it measures, prints one line per reading.
#
# A stand-in CLI follows the probe's own brief in shell, so this suite measures the probe with no model, no key and no
# network. The stand-in polls the inbox and answers its ts, exactly as the brief asks a live session to do. A probe
# that returns before its session answers, that prints no numeric reading, or that passes a late Monitor write, fails
# here. The kernel bound, the real `claude` and the <1 s target of a live session are proven by the probe itself, never
# by this suite.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
probe="$here/../../voice/latency_probe.py"
PY="${PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
T=$(mktemp -d "${TMPDIR:-/tmp}/latency-probe.XXXXXX")
stub="$T/claude-stand-in"
cleanup() {
  grep -rhoE '"pid":[ ]*[0-9]+,"text":"armed"' "$T" 2>/dev/null | sed -n 's/.*"pid":[ ]*\([0-9][0-9]*\).*/\1/p' |
    while read -r p; do kill "$p" 2>/dev/null; done
  rm -rf "$T"
}
trap cleanup EXIT INT TERM

cat > "$stub" <<'STUB'
#!/bin/sh
# A stand-in for `claude --bg`: it follows latency_probe.py's brief with no model. `stop` ends the poller the
# `--bg` call left behind, read from the armed line's pid.
INBOX=${LATENCY_PROBE_INBOX:?}
OUTBOX=${LATENCY_PROBE_OUTBOX:?}
if [ "$1" = "stop" ]; then
  pid=$(tail -n 20 "$OUTBOX" 2>/dev/null | sed -n 's/.*"pid":[ ]*\([0-9][0-9]*\).*/\1/p' | tail -n 1)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null
  exit 0
fi
# The session's own poller: one process, so `stop` ends it whole.
(
  n=0
  while :; do
    total=$(wc -l < "$INBOX" 2>/dev/null | tr -d ' ' || echo 0)
    if [ "${total:-0}" -gt "$n" ]; then
      line=$(sed -n "$((n + 1))p" "$INBOX")
      ts=$(printf '%s' "$line" | sed -n 's/.*"ts":[ ]*"\([^"]*\)".*/\1/p')
      printf '{"thread_ts":"%s","text":"pong"}\n' "$ts" >> "$OUTBOX"
      n=$total
    fi
    sleep 0.02
  done
) >/dev/null 2>&1 &
mpid=$!
printf '{"pid":%s,"text":"armed"}\n' "$mpid" >> "$OUTBOX"
echo deadbeef
exit 0
STUB
chmod +x "$stub"

out=$("$PY" "$probe" --state "$T/state" --work "$T/work" --cli "$stub" --no-sandbox \
      --target 1 --deadline 30 --timeout 30 2>&1) || fail "the probe exited non-zero: $(printf '%s' "$out" | tail -3)"
printf '%s\n' "$out" | grep -Eq '^monitor: [0-9]+\.[0-9]{3}s ' || fail "no monitor reading: $out"
printf '%s\n' "$out" | grep -Eq '^session: [0-9]+\.[0-9]{3}s ' || fail "no session reading: $out"

# A session that never answers is a failed probe: no answer means exit 1, never a green with no measurement.
silent="$T/claude-silent"
printf '#!/bin/sh\nif [ "$1" = stop ]; then exit 0; fi\necho cafebabe\nexit 0\n' > "$silent"
chmod +x "$silent"
"$PY" "$probe" --state "$T/state2" --work "$T/work2" --cli "$silent" --no-sandbox \
     --target 1 --deadline 1 --timeout 1 >/dev/null 2>&1 && fail "a session that never answers passed the probe"

# The real CLI never runs unwrapped: the bound is the measurement's premise.
"$PY" "$probe" --state "$T/state3" --work "$T/work3" --cli claude --no-sandbox \
     >/dev/null 2>&1 && fail "the probe ran the real CLI with no kernel bound"

# A session that arms its poller and then fails to start leaves no poller behind: the probe fails before its stop.
armed_fail="$T/claude-armed-then-fail"
sed 's/^echo deadbeef$/exit 1/' "$stub" > "$armed_fail"
chmod +x "$armed_fail"
"$PY" "$probe" --state "$T/state4" --work "$T/work4" --cli "$armed_fail" --no-sandbox \
     --target 1 --deadline 1 --timeout 1 >/dev/null 2>&1 && fail "a session that never started passed the probe"
poller=$(grep -rhoE '"pid":[ ]*[0-9]+,"text":"armed"' "$T/state4" | sed -n 's/.*"pid":[ ]*\([0-9][0-9]*\).*/\1/p' | tail -n 1)
[ -n "$poller" ] || fail "the stand-in never armed a poller"
kill -0 "$poller" 2>/dev/null && fail "a poller outlived a probe that failed before its stop"

echo "test-latency-probe: ok"
