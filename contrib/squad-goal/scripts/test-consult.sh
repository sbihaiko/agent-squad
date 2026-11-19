#!/bin/bash
# seconds: 0.57
# consult.sh asks the first coding model that may run now, read from models.json as a v1 or a v2 chain (ADR-0147).
d="$(cd "$(dirname "$0")" && pwd)"; T=$(mktemp -d); fail=0; log=$T/log; : > "$log"
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
S=$T/scripts; mkdir -p "$S" "$T/bin" "$T/state" "$T/co" "$T/run"
cp "$d/consult.sh" "$d/modelreg.py" "$d/registry.json" "$S/"
printf 'export SG_STATE=%s SG_CHECKOUT=%s\n' "$T/state" "$T/co" > "$S/env.sh"
printf '#!/bin/bash\necho "GO: a valid task"\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf 'Edit scripts/x.py\n' > "$T/run/request.txt"
export PATH="$T/bin:$PATH" CONSULT_DRY=1
ask() { rm -f "$T/run/consult.done"; bash "$S/consult.sh" "$T/run" 2>"$T/err"; }

cp "$d/models.json" "$S/models.json"
# The repo file's first coding entry keeps an hours window, so the case reads which entry may run now through the shipped
# resolver instead of naming one: a night run and a day run start on different entries.
first_coding=$(cd "$S" && python3 -c 'import time, modelreg
e = modelreg.chain("models.json", "coding").entries
h = time.localtime().tm_hour
print(next((x.raw for x in e if modelreg.eligible(x, hour=h).ok), e[0].raw))')
out=$(ask)
case "$out" in *"($first_coding): GO"*) ok "the repo models.json (v2) asks its first coding model";;
  *) ko "v2 repo file asks: ${out:-nothing} $(cat "$T/err")";; esac

printf '{"schema_version":2,"chains":{"coding":[{"id":"gpt-5.6-terra","raw":"claude-gpt-5.6-terra","route":"gateway","on":false},{"id":"grok-4.7","raw":"claude-grok-4.7","route":"gateway"}]}}' > "$S/models.json"
out=$(ask)
case "$out" in *"(claude-grok-4.7): GO"*) ok "a v2 entry switched off is skipped";;
  *) ko "v2 off entry asked: ${out:-nothing} $(cat "$T/err")";; esac

printf '{"coding_models":["claude-sonnet-5-5"]}' > "$S/models.json"
out=$(ask)
case "$out" in *"(claude-sonnet-5-5): GO"*) ok "a v1 chain still asks its first model";;
  *) ko "v1 chain asks: ${out:-nothing} $(cat "$T/err")";; esac

printf '#!/usr/bin/env python3\nimport os, sys\nopen(os.environ["LAUNCHED"], "a").write(sys.argv[1] + "\\n")\n' > "$S/launch.py"
printf '#!/bin/bash\necho req-consult\n' > "$S/mkreq.sh"; chmod +x "$S/mkreq.sh"
mkdir -p "$T/state/auto"; : > "$T/state/auto/off"; rm -f "$T/run/consult.done" "$T/launched"
out=$(env -u CONSULT_DRY LAUNCHED="$T/launched" bash "$S/consult.sh" "$T/run" 2>"$T/err")
[ ! -s "$T/launched" ] && ok "with auto/off a GO answer relaunches nothing" || ko "auto/off relaunched: $(cat "$T/launched")"
[ "$(printf '%s\n' "$out" | grep -c .)" = 1 ] && printf '%s' "$out" | grep -q 'no run starts' && ok "with auto/off consult prints one skip line" || ko "auto/off consult printed: '$out'"

cat "$log"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
