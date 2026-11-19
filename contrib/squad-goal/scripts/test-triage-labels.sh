#!/bin/bash
# seconds: 1.57
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

# The doc's triage section, read by its own heading, never the first table the file happens to hold: the floor and lane
# tables below are the dispatch vocabulary and name labels the bootstrap does not create (#1696).
docnames=$(awk '/^The triage vocabulary is /{t=1; next} t && /^\|/{f=1; print; next} f{exit}' "$doc" | /usr/bin/grep -o '`[^`]*`' | tr -d '`' | sort)
: > "$T/labels"; botnames=$(run --check | sed -n 's/^label missing: //p' | sort)
[ -n "$docnames" ] && [ "$docnames" = "$botnames" ] && ok "the vocabulary doc names the labels the bootstrap creates" || ko "doc and bootstrap disagree: doc '$(echo $docnames)' bootstrap '$(echo $botnames)'"
# The doc's floor table, read by its own heading, names exactly the labels the one skip-list library adds to a lane whatever
# the profile says (#1696): the dispatch vocabulary the doc's prose lets an operator apply and remove.
docfloor=$(awk '/^The holds are a floor/{t=1; next} t && /^\|/{f=1; print; next} f{exit}' "$doc" | /usr/bin/grep -o '`[^`]*`' | tr -d '`' | sort)
codefloor=$( ( . "$d/skiplabels.sh"; SG_FRONTIER_SKIP_LABELS=; sg_skip_labels frontier ) | tr ',' '\n' | sort -u)
[ -n "$docfloor" ] && [ "$docfloor" = "$codefloor" ] && ok "the doc's floor table names the floor labels both dispatch lanes add" || ko "doc floor and code floor disagree: doc '$(echo $docfloor)' code '$(echo $codefloor)'"
# One source for the triage labels: profile-check reads them through the bootstrap's --check, never a list of its own (#1696).
/usr/bin/grep -q 'triage-labels\.sh" --check' "$d/profile-check.sh" && ok "profile-check reads the triage labels through the bootstrap's --check" || ko "profile-check keeps a triage label list of its own instead of reading the bootstrap"
required=$(sed -n 's/.*SG_LABELS_REQUIRED:=\([^"}]*\).*/\1/p' "$d/profile-check.sh" | head -1)
twice=; for n in $docnames; do case " $required " in *" $n "*) twice="$twice $n";; esac; done
[ -z "$twice" ] && ok "profile-check keeps no second list of the triage labels" || ko "profile-check names the triage labels in SG_LABELS_REQUIRED:$twice"
run_arm=$(awk '/^  run\)/{t=1; next} t && /^  [a-z-]+\)/{exit} t' "$d/watchd.sh")
printf '%s\n' "$run_arm" | /usr/bin/grep -q 'triage-labels\.sh' && ok "the watchd run, the path every loop driver takes, runs the label bootstrap" || ko "the watchd run does not run the label bootstrap, so a loop started through it creates none of the triage labels"
exit $fail
