#!/bin/bash
# #1477: one probe times the authenticated Slack download and the gemini-2.5-flash transcription of one voice clip.
# usage: bash test-probe-gemini-transcribe.sh [path/to/probe-gemini-transcribe.sh]
# The suite calls no network: it checks the no-key refusal, that no key reaches any output, and the 90th percentile
# arithmetic on fixed sample timings.
src=${1:-$(dirname "$0")/probe-gemini-transcribe.sh}; D=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
P="$D/probe-gemini-transcribe.sh"
unset GOOGLE_API_KEY SLACK_BOT_TOKEN SG_GEMINI_KEY_SERVICE SG_SLACK_BOT_SERVICE
nosuch=no-such-keychain-service-1477

# 1. no key: the live probe refuses before any call, and names the missing key.
out=$(env -u GOOGLE_API_KEY SG_GEMINI_KEY_SERVICE="$nosuch" SG_SLACK_BOT_SERVICE="$nosuch" bash "$P" 2>&1); rc=$?
[ "$rc" -ne 0 ] && case "$out" in *"no Gemini key"*) true;; *) false;; esac \
  && ok "1 no Gemini key refuses the live probe" || ko "1 no key: rc=$rc '$out'"

# 2. no key in any output: a sentinel key in the environment never reaches stdout or stderr, even on a refusal.
sent=SENTINEL1477cafebabe
out=$(GOOGLE_API_KEY="$sent" SG_SLACK_BOT_SERVICE="$nosuch" bash "$P" 2>&1); rc=$?
case "$out" in *"$sent"*) ko "2 the sentinel key reached the output";; *) ok "2 no key value in the output";; esac
[ "$rc" -ne 0 ] && ok "2b a missing Slack token refuses the probe" || ko "2b missing Slack token: rc=$rc '$out'"

# 3. nearest-rank 90th percentile on ten fixed samples: the 9th smallest total, 11.00 s against 15.00 s.
S="$T/ten.tsv"
{
  printf '# clip\tlength_s\tdownload_s\ttranscribe_s\n'
  printf 'c1\t20.0\t1.0\t2.0\nc2\t20.0\t1.5\t2.5\nc3\t20.0\t2.0\t3.0\nc4\t20.0\t2.5\t3.5\nc5\t20.0\t3.0\t4.0\n'
  printf 'c6\t20.0\t3.5\t4.5\nc7\t20.0\t4.0\t5.0\nc8\t20.0\t4.5\t5.5\nc9\t20.0\t5.0\t6.0\nc10\t20.0\t6.0\t7.0\n'
} > "$S"
out=$(bash "$P" --from-samples "$S" 2>&1); rc=$?
printf '%s\n' "$out" | /usr/bin/grep -qx 'p90_total_s 11.00' && ok "3 p90 of the total is the 9th smallest, not an interpolation" || ko "3 p90 total: rc=$rc '$out'"
printf '%s\n' "$out" | /usr/bin/grep -qx 'p90_target_s 15.00' && ok "3b p90 of the target is 15.00 s for a 20 s clip" || ko "3b p90 target"
printf '%s\n' "$out" | /usr/bin/grep -qx 'p90_verdict holds' && ok "3c 11.00 s meets 15.00 s" || ko "3c verdict"
printf '%s\n' "$out" | /usr/bin/grep -qx 'clips 10' && ok "3d ten clips are counted" || ko "3d clip count"
printf '%s\n' "$out" | /usr/bin/grep -qE '^c9\s+20\.00\s+5\.00\s+6\.00\s+11\.00\s+15\.00' && ok "3e download and transcription are timed apart" || ko "3e row shape"

# 4. a 90th percentile over the target refutes the premise.
S2="$T/ten-slow.tsv"
{
  printf 'c1\t20.0\t4.0\t4.0\nc2\t20.0\t4.5\t4.5\nc3\t20.0\t5.0\t5.0\nc4\t20.0\t5.5\t5.5\nc5\t20.0\t6.0\t6.0\n'
  printf 'c6\t20.0\t6.5\t6.5\nc7\t20.0\t7.0\t7.0\nc8\t20.0\t7.5\t7.5\nc9\t20.0\t8.0\t8.0\nc10\t20.0\t9.0\t9.0\n'
} > "$S2"
out=$(bash "$P" --from-samples "$S2" 2>&1)
printf '%s\n' "$out" | /usr/bin/grep -qx 'p90_total_s 16.00' && printf '%s\n' "$out" | /usr/bin/grep -qx 'p90_verdict refuted' \
  && ok "4 a p90 over the target refutes the premise" || ko "4 refuted: '$out'"

# 5. the samples path takes no key and calls no network.
out=$(env -u GOOGLE_API_KEY SG_GEMINI_KEY_SERVICE="$nosuch" bash "$P" --from-samples "$S" 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "5 the samples path needs no key" || ko "5 samples without a key: rc=$rc '$out'"

[ "$fail" -eq 0 ] && { echo "PASS test-probe-gemini-transcribe"; exit 0; }
echo "FAIL test-probe-gemini-transcribe"; exit 1
