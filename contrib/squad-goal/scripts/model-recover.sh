#!/bin/bash
# Recover a review model that a timeout streak took out of the chain (#749): probe the lane the model itself reviews on,
# and clear the streak only on a real answer. An omission is a claim about a lane, never a verdict about a model, so the
# probe runs before the window ends and reports its cause when the model still fails.
# usage: model-recover.sh <model> [--timeout S]
set -u
here=$(cd "$(dirname "$0")" && pwd)
m=${1:?usage: model-recover.sh <model> [--timeout S]}
tmo=60; [ "${2:-}" = "--timeout" ] && tmo=${3:-60}
state=${SG_STATE:?model-recover.sh needs SG_STATE; source env.sh first}
cfg=$here/models.json
f="$state/review-timeouts.json"
bound=$(jq -r '.review_timeout_bound // 3' "$cfg" 2>/dev/null); win=$(jq -r '.review_timeout_omit_seconds // 21600' "$cfg" 2>/dev/null)
bound=${bound:-3}; win=${win:-21600}
why=$(python3 "$here/review-timeouts.py" "$f" omitted "$m" --bound "$bound" --window "$win" 2>/dev/null) || {
  echo "MODEL NOT OMITTED $m: no live timeout streak"; exit 0; }
IFS=$'\t' read -r rt rn wire tools <<< "$(python3 "$here/modelreg.py" review-route "$m" 2>/dev/null)"
[ -n "${rt:-}" ] || { echo "MODEL NOT RECOVERED $m: the registry gives no route"; exit 1; }
ask='Answer with the single word: ok'
t1=$(date +%s)
if [ "$tools" = 0 ] || [ "$rt" != cli ]; then
  # the no-tools lane: one direct call to the gateway, thinking off, exactly as the review pass makes it
  body=$(jq -Rn --arg m "${wire:-$m}" --arg q "$ask" '{model:$m, max_tokens:64, thinking:{type:"disabled"}, messages:[{role:"user", content:$q}]}')
  ans=$(curl -sS -m "$tmo" "${ANTHROPIC_BASE_URL:-http://localhost:8016}/v1/messages" \
    -H 'x-api-key: gateway' -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
    -d "$body" 2>/dev/null | jq -r '[.content[]? | select(.type=="text") | .text] | join("")' 2>/dev/null)
else
  ans=$(claude -p --output-format json --strict-mcp-config --model "${wire:-$m}" --effort low \
    --tools Read Grep Glob Bash < <(printf '%s' "$ask") 2>/dev/null \
    | jq -r 'select(type=="object") | .result // ""' 2>/dev/null)
fi
secs=$(( $(date +%s) - t1 ))
case "${ans:-}" in
  *ok*|*OK*|*Ok*) python3 "$here/review-timeouts.py" "$f" clear "$m" --bound "$bound" --window "$win" >/dev/null 2>&1
    echo "MODEL RECOVERED $m in ${secs}s on $rt/${rn:-direct}: the streak is cleared, and the next chain tries it"; exit 0;;
esac
echo "MODEL NOT RECOVERED $m in ${secs}s on $rt/${rn:-direct}: ${why}; the answer was empty or failed, so the streak stands"
exit 1
