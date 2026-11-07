#!/bin/bash
# usage: probe-gemini-transcribe.sh [--count N] [--file-ids id[,id...]] [--dir DIR] [--keep-audio]
#        probe-gemini-transcribe.sh --from-samples FILE
#
# Times the two halves of one voice turn: the authenticated Slack download and the gemini-2.5-flash transcription.
# It prints timings only: one row per clip with the clip length, the download seconds, the transcription seconds, the total
# and the target 0.5 x clip length + 5 s, then the nearest-rank 90th percentile of the total and of the target and the
# verdict. It never prints a transcript and never prints a key value (the keys travel in curl config files, never in argv).
#
# The keys come from the environment first, then from the macOS Keychain by service name: GOOGLE_API_KEY or the service
# SG_GEMINI_KEY_SERVICE (default agent-squad-voice-gemini-key), and SLACK_BOT_TOKEN or SG_SLACK_BOT_SERVICE (default
# agent-squad-voice-slack-bot-token). The live probe refuses to run without them. --from-samples computes the same
# arithmetic from a fixed file (`clip<TAB>length_s<TAB>download_s<TAB>transcribe_s` per line, `#` for a comment), needs no
# key and calls no network: it is the self-check the suite runs.
#
# Audio is written only under --dir (default runs/probe-gemini-transcribe), which the caller must leave git-ignored; the
# probe refuses a directory git does not ignore and deletes the audio before it exits unless --keep-audio is given.
# Exit 0 when every requested clip is timed, 2 when a key, a tool or the directory is missing, 3 when fewer clips than
# requested could be measured.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
count=10; ids=""; dir=""; keep=0; samples=""
while [ $# -gt 0 ]; do
  case "$1" in
    --count) count=${2:-}; shift 2;;
    --file-ids) ids=${2:-}; shift 2;;
    --dir) dir=${2:-}; shift 2;;
    --keep-audio) keep=1; shift;;
    --from-samples) samples=${2:-}; shift 2;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "probe-gemini-transcribe: unknown option $1" >&2; exit 2;;
  esac
done
die() { echo "probe-gemini-transcribe: $*" >&2; exit 2; }

# The percentile arithmetic and the table live in one python step, so the live probe and --from-samples print the same shape.
stats() {
  python3 - "$1" <<'PY'
import math, sys
rows = []
for ln in open(sys.argv[1]):
    ln = ln.strip()
    if not ln or ln.startswith('#'):
        continue
    c, l, d, t = ln.split('\t')
    l, d, t = float(l), float(d), float(t)
    rows.append((c, l, d, t, d + t, 0.5 * l + 5.0))
if not rows:
    print('clips 0')
    sys.exit(3)
def p90(values):
    values = sorted(values)
    return values[math.ceil(0.9 * len(values)) - 1]
print('clip\tlength_s\tdownload_s\ttranscribe_s\ttotal_s\ttarget_s')
for r in rows:
    print('%s\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f' % r)
total = p90([r[4] for r in rows]); target = p90([r[5] for r in rows])
print('p90_total_s %.2f' % total)
print('p90_target_s %.2f' % target)
print('p90_verdict %s' % ('holds' if total <= target else 'refuted'))
print('clips %d' % len(rows))
PY
}

if [ -n "$samples" ]; then
  [ -r "$samples" ] || die "cannot read the samples file $samples"
  stats "$samples"
  exit $?
fi

# --- live probe ------------------------------------------------------------------------------------------------------------
gemini_key=${GOOGLE_API_KEY:-}
if [ -z "$gemini_key" ]; then
  svc=${SG_GEMINI_KEY_SERVICE:-agent-squad-voice-gemini-key}
  gemini_key=$(security find-generic-password -s "$svc" -w 2>/dev/null) || gemini_key=""
fi
[ -n "$gemini_key" ] || die "no Gemini key: set GOOGLE_API_KEY or add it to the Keychain service ${SG_GEMINI_KEY_SERVICE:-agent-squad-voice-gemini-key}"
slack=${SLACK_BOT_TOKEN:-}
if [ -z "$slack" ]; then
  svc=${SG_SLACK_BOT_SERVICE:-agent-squad-voice-slack-bot-token}
  slack=$(security find-generic-password -s "$svc" -w 2>/dev/null) || slack=""
fi
[ -n "$slack" ] || die "no Slack bot token: set SLACK_BOT_TOKEN or add it to the Keychain service ${SG_SLACK_BOT_SERVICE:-agent-squad-voice-slack-bot-token}"
for t in curl ffprobe python3 git; do command -v "$t" >/dev/null 2>&1 || die "missing tool $t"; done

dir=${dir:-$PWD/runs/probe-gemini-transcribe}
git check-ignore -q -- "$dir/audio.m4a" || die "the audio directory $dir is not git-ignored; pass --dir inside an ignored tree"
mkdir -p "$dir" || die "cannot create $dir"

# A key never reaches argv or the output: each one travels in a private curl config file, removed at exit.
umask 077; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
printf 'header = "x-goog-api-key: %s"\n' "$gemini_key" > "$tmp/gemini.cfg"
printf 'header = "Authorization: Bearer %s"\n' "$slack" > "$tmp/slack.cfg"
unset gemini_key slack

slack_get() { curl -s --config "$tmp/slack.cfg" "https://slack.com/api/$1"; }

if [ -z "$ids" ]; then
  list=$(slack_get 'files.list?count=200' | python3 -c "
import json, sys
d = json.load(sys.stdin)
if not d.get('ok'):
    sys.exit('%s' % d.get('error', 'slack refused'))
for f in d.get('files', []):
    if str(f.get('mimetype', '')).startswith('audio/'):
        print(f['id'])
") || die "cannot list Slack files: $list"
  ids=$(printf '%s\n' "$list" | head -n "$count" | tr '\n' ' ' | sed 's/ $//')
fi
[ -n "$ids" ] || die "no audio file is reachable in the Slack workspace"

tsv="$tmp/samples.tsv"; : > "$tsv"; n=0
for id in $ids; do
  [ "$n" -lt "$count" ] || break
  url=$(slack_get "files.info?file=$id" | python3 -c "
import json, sys
d = json.load(sys.stdin)
f = d.get('file') or {}
print(f.get('url_private_download') or f.get('url_private') or '')
") || continue
  [ -n "$url" ] || continue
  audio="$dir/$id.m4a"
  dl=$(curl -s -L --config "$tmp/slack.cfg" -o "$audio" -w '%{time_total}' "$url") || continue
  [ -s "$audio" ] || continue
  len=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$audio" 2>/dev/null)
  [ -n "$len" ] || continue
  python3 - "$audio" "$tmp/body.json" <<'PY'
import base64, json, sys
data = base64.b64encode(open(sys.argv[1], 'rb').read()).decode()
body = {"contents": [{"parts": [
    {"text": "Transcribe this audio exactly in its own language. Reply with the transcript only."},
    {"inline_data": {"mime_type": "audio/mp4", "data": data}}]}]}
open(sys.argv[2], 'w').write(json.dumps(body))
PY
  tr=$(curl -s -X POST --config "$tmp/gemini.cfg" -H 'Content-Type: application/json' \
        --data @"$tmp/body.json" -w '\n%{time_total}' \
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
  [ -n "$tr" ] || continue
  secs=${tr##*$'\n'}
  printf '%s' "${tr%$'\n'*}" > "$tmp/resp.json"
  have=$(python3 - "$tmp/resp.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(1 if (d.get('candidates') or [{}])[0].get('content', {}).get('parts') else 0)
except Exception:
    print(0)
PY
)
  [ "$have" = 1 ] || continue
  printf '%s\t%s\t%s\t%s\n' "$id" "$len" "$dl" "$secs" >> "$tsv"
  n=$((n + 1))
done

[ "$keep" -eq 1 ] || rm -f "$dir"/*.m4a
stats "$tsv"; rc=$?
[ "$n" -lt "$count" ] && { echo "measured $n of $count requested clips" >&2; exit 3; }
exit $rc
