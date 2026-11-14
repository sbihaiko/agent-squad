#!/bin/bash
# Guard for triage-labels.sh (#1673): the label bootstrap creates a missing triage label and refreshes the rest, idempotently,
# and its --check names each missing label without a write. The vocabulary doc's first table names the labels it creates.
# Offline: a stub gh that keeps the repository's labels in a file, and a stub reader for the label list.
# usage: bash test-triage-labels.sh
d=$(cd "$(dirname "$0")" && pwd); doc=$d/../../../docs/agents/triage-labels.md
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/state"; : > "$T/writes"; : > "$T/labels"
cat > "$T/bin/gh" <<GH
#!/bin/bash
case "\$*" in
  *"-X POST"*) echo "POST" >> "$T/writes"; for a in "\$@"; do case "\$a" in name=*) echo "\${a#name=}" >> "$T/labels";; esac; done;;
  *"-X PATCH"*) echo "PATCH \$*" >> "$T/writes";;
esac
echo '{}'
GH
chmod +x "$T/bin/gh"
cat > "$T/reader" <<RD
#!/bin/bash
jq -R -s 'split("\n")|map(select(.!=""))|map({name:.})' "$T/labels"
RD
chmod +x "$T/reader"
run() { env PATH="$T/bin:$PATH" SG_REPO=acme/emu SG_STATE="$T/state" SG_GHREAD="$T/reader" bash "$d/triage-labels.sh" "$@" 2>&1; }
posts() { /usr/bin/grep -c '^POST' "$T/writes"; }
patches() { /usr/bin/grep -c '^PATCH' "$T/writes"; }
writes() { wc -l < "$T/writes" | tr -d ' '; }

out=$(run); rc=$?
[ "$rc" = 0 ] && [ "$(posts)" = 3 ] && [ "$(patches)" = 0 ] && ok "an empty repository gets the three triage labels" || ko "apply on an empty repository: rc $rc, $(posts) create(s), $(patches) refresh(es): '$out'"
[ "$(sort "$T/labels" | uniq -d | wc -l | tr -d ' ')" = 0 ] && [ "$(wc -l < "$T/labels" | tr -d ' ')" = 3 ] && ok "each label is created once" || ko "labels after apply: $(tr '\n' ' ' < "$T/labels")"
/usr/bin/grep -qxF ready-for-agent "$T/labels" && /usr/bin/grep -qxF ready-for-human "$T/labels" && /usr/bin/grep -qxF ready-for-HAL9K "$T/labels" && ok "the bootstrap creates ready-for-agent, ready-for-human and ready-for-HAL9K" || ko "the created labels: $(tr '\n' ' ' < "$T/labels")"

: > "$T/writes"; out=$(run); rc=$?
[ "$rc" = 0 ] && [ "$(posts)" = 0 ] && [ "$(patches)" = 3 ] && ok "a second apply refreshes the three labels and creates none" || ko "second apply: rc $rc, $(posts) create(s), $(patches) refresh(es): '$out'"
[ "$(wc -l < "$T/labels" | tr -d ' ')" = 3 ] && ok "the second apply makes no duplicate" || ko "duplicate after the second apply: $(tr '\n' ' ' < "$T/labels")"

: > "$T/writes"; out=$(run --check); rc=$?
[ "$rc" = 0 ] && [ "$(writes)" = 0 ] && ok "--check passes on a complete repository and writes nothing" || ko "--check on a complete repository: rc $rc, writes $(writes): '$out'"

/usr/bin/grep -vxF ready-for-HAL9K "$T/labels" > "$T/labels.part"; mv "$T/labels.part" "$T/labels"
: > "$T/writes"; out=$(run --check); rc=$?
[ "$rc" = 1 ] && printf '%s' "$out" | /usr/bin/grep -qxF 'label missing: ready-for-HAL9K' && ok "--check names the missing label" || ko "--check did not name the missing label: rc $rc: '$out'"
[ "$(writes)" = 0 ] && ok "--check writes nothing when a label is missing" || ko "--check wrote a label"

out=$(run); rc=$?
[ "$rc" = 0 ] && [ "$(wc -l < "$T/labels" | tr -d ' ')" = 3 ] && ok "apply creates the one missing label" || ko "apply after a missing label: rc $rc: '$out'"

docnames=$(awk '/^\| Label /{t=1} t && /^\|/{print; next} t{exit}' "$doc" | /usr/bin/grep -o '`[^`]*`' | tr -d '`' | sort)
: > "$T/labels"; botnames=$(run --check | sed -n 's/^label missing: //p' | sort)
[ -n "$docnames" ] && [ "$docnames" = "$botnames" ] && ok "the vocabulary doc names the labels the bootstrap creates" || ko "doc and bootstrap disagree: doc '$(echo $docnames)' bootstrap '$(echo $botnames)'"
exit $fail
