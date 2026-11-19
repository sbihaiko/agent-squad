#!/bin/bash
# seconds: 25.72
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

# 6. the live loop, driven by stub curl and stub ffprobe on PATH and by a fake repo whose runs/ tree is ignored:
#    ten clips listed, downloaded, transcribed, ten rows appended and exit 0.
B="$T/bin"; mkdir -p "$B"
cat > "$B/ffprobe" <<'STUB'
#!/bin/bash
echo 20.0
STUB
cat > "$B/curl" <<'STUB'
#!/bin/bash
out=""; url=""; data=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2;;
    -w) shift 2;;
    --data) data=$2; shift 2;;
    --config|-H|-X) shift 2;;
    http*) url=$1; shift;;
    *) shift;;
  esac
done
mime=${STUB_MIME:-audio/mp4}; ft=${STUB_FILETYPE:-m4a}; page1=${STUB_PAGE1:-10}
case "$url" in
  *slack.com/api/files.list*)
    next=""
    if [ -n "${STUB_PAGE1:-}" ]; then
      case "$url" in *cursor=*) from=$((page1 + 1)); to=10;; *) from=1; to=$page1; next=P2;; esac
    else
      from=1; to=$page1
    fi
    printf '{"ok":true,"files":['
    i=$from; while [ "$i" -le "$to" ]; do printf '{"id":"F%02d","mimetype":"%s","filetype":"%s"},' "$i" "$mime" "$ft"; i=$((i+1)); done
    printf '{"id":"IMG1","mimetype":"image/png","filetype":"png"}],"response_metadata":{"next_cursor":"%s"}}' "$next"
    ;;
  *files.slack.com*)
    case "$mime" in audio/*) ;; *) exit 1;; esac
    printf 'audio bytes' > "$out"; printf '0.40'
    ;;
  *generativelanguage*)
    if [ "$mime" = "audio/webm" ] && ! /usr/bin/grep -q '"mime_type": "audio/webm"' "${data#@}"; then
      printf '{"error":"stub: the body did not carry audio/webm"}' >&2; exit 1
    fi
    printf '{"candidates":[{"content":{"parts":[{"text":"x"}]}}]}\n'; printf '2.00'
    ;;
  *slack.com/api/files.info*)
    id=${url##*file=}
    printf '{"ok":true,"file":{"url_private_download":"https://files.slack.com/files-pri/T0-F0/%s","mimetype":"%s","filetype":"%s"}}' "$id" "$mime" "$ft"
    ;;
  *) printf '{"ok":false,"error":"stub saw %s"}' "$url" >&2; exit 1;;
esac
STUB
chmod +x "$B/curl" "$B/ffprobe"
R="$T/fakerepo"; mkdir -p "$R"; ( cd "$R" && git init -q && printf 'runs/\n' > .gitignore )
out=$(cd "$R" && PATH="$B:$PATH" GOOGLE_API_KEY="$sent" SLACK_BOT_TOKEN="$sent" bash "$P" --count 10 --dir runs/probe 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "6 a full live run exits 0" || ko "6 full live run: rc=$rc '$out'"
printf '%s\n' "$out" | /usr/bin/grep -qx 'clips 10' && ok "6b ten live clips are counted" || ko "6b live clip count: '$out'"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c '^F[0-9][0-9]	')" -eq 10 ] \
  && ok "6c ten rows carry the download and the transcription apart" || ko "6c live rows: '$out'"
printf '%s\n' "$out" | /usr/bin/grep -qx 'p90_total_s 2.40' && ok "6d the live total is download plus transcription" || ko "6d live p90: '$out'"
case "$out" in *"$sent"*) ko "6e the sentinel key reached the live output";; *) ok "6e no key value in the live output";; esac

# 7. a short live run prints no percentile and exits 3: a p90 over fewer clips is never the ten-clip answer.
out=$(cd "$R" && PATH="$B:$PATH" GOOGLE_API_KEY="$sent" SLACK_BOT_TOKEN="$sent" bash "$P" --count 11 --dir runs/probe 2>&1); rc=$?
[ "$rc" -eq 3 ] && ok "7 a short run exits 3" || ko "7 short run: rc=$rc '$out'"
case "$out" in *"p90_total_s"*) ko "7b a short run printed a percentile";; *) ok "7b a short run prints no percentile";; esac
case "$out" in *"measured 10 of 11"*) ok "7c the short run names the shortfall";; *) ko "7c shortfall line: '$out'";; esac

# 8. pagination: three audio ids on the first page and seven on the next still time ten clips.
out=$(cd "$R" && PATH="$B:$PATH" STUB_PAGE1=3 GOOGLE_API_KEY="$sent" SLACK_BOT_TOKEN="$sent" bash "$P" --count 10 --dir runs/probe 2>&1); rc=$?
[ "$rc" -eq 0 ] && printf '%s\n' "$out" | /usr/bin/grep -qx 'clips 10' \
  && ok "8 the list follows next_cursor to ten clips" || ko "8 pagination: rc=$rc '$out'"

# 9. a webm clip keeps its own container: the saved name and the inline mime_type both say webm.
rm -rf "$R/runs"
out=$(cd "$R" && PATH="$B:$PATH" STUB_MIME=audio/webm STUB_FILETYPE=webm GOOGLE_API_KEY="$sent" SLACK_BOT_TOKEN="$sent" bash "$P" --count 10 --dir runs/probe --keep-audio 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "9 a webm clip transcribes" || ko "9 webm clip: rc=$rc '$out'"
[ -s "$R/runs/probe/F01.webm" ] && ok "9b the download keeps the webm container" || ko "9b webm file: $(/bin/ls "$R/runs/probe" 2>&1)"
rm -rf "$R/runs"

# 10. a clip the probe cannot read prints skip with its reason instead of vanishing.
out=$(cd "$R" && PATH="$B:$PATH" STUB_MIME=image/png STUB_FILETYPE=png GOOGLE_API_KEY="$sent" SLACK_BOT_TOKEN="$sent" bash "$P" --file-ids IMG1 2>&1); rc=$?
[ "$rc" -eq 3 ] && case "$out" in *"skip IMG1"*) ok "10 an unreadable clip prints skip and why";; *) ko "10 skip line: '$out'";; esac \
  || ko "10 unreadable clip: rc=$rc '$out'"

[ "$fail" -eq 0 ] && { echo "PASS test-probe-gemini-transcribe"; exit 0; }
echo "FAIL test-probe-gemini-transcribe"; exit 1
