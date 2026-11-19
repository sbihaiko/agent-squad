#!/bin/bash
# seconds: 1.16
# Table test for profile-check.sh (#763): a new profile runs one check, and each setup gap is named before the loop misbehaves.
# The profile parses, names a real repository, board and checkout whose origin is that repository, and the repository holds
# the labels the loop and /to-tickets use. Offline: throwaway profiles and checkouts, a stub reader for the label list.
# usage: bash test-profile-check.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/p" "$T/co"; git -C "$T/co" init -q; git -C "$T/co" remote add origin git@github.com:acme/emu.git
all='[{"name":"bug"},{"name":"ready-for-agent"},{"name":"ready-for-human"},{"name":"ready-for-HAL9K"},{"name":"needs-triage"},{"name":"needs-info"},{"name":"wontfix"}]'
cat > "$T/reader" <<R
#!/bin/bash
echo "\$1" >> "$T/routes"; cat "$T/labels"
R
chmod +x "$T/reader"
# stubs for the board vocabulary check and the dry frontier pass: each logs its argv and state dir, and answers $T/<name>.rc
for s in board frontier; do cat > "$T/$s" <<S
#!/bin/bash
echo "\$* state=\${SG_STATE:-} dry=\${AF_DRY:-}" >> "$T/$s.log"; cat "$T/$s.out" 2>/dev/null; exit \$(cat "$T/$s.rc" 2>/dev/null || echo 0)
S
chmod +x "$T/$s"; done
good() { cat > "$T/p/emu.sh" <<P
: "\${SG_REPO:=acme/emu}"; : "\${SG_OWNER:=acme}"; : "\${SG_PROJECT:=3}"; : "\${SG_CHECKOUT:=$T/co}"; : "\${SG_BASE:=dev}"
P
}
run() { env -u SG_REPO -u SG_OWNER -u SG_PROJECT -u SG_CHECKOUT -u SG_BASE SG_PROFILES_DIR="$T/p" SG_STATE="$T/state" \
  SG_GHREAD="$T/reader" SG_PF_BOARD_CHECK="$T/board" SG_PF_FRONTIER="$T/frontier" SG_FRONTIER_LABEL=go bash "$d/profile-check.sh" "$@" 2>&1; }
# row <name> <want rc> <text the output must hold>
row() { local out rc; out=$(run emu); rc=$?
  [ "$rc" = "$2" ] && case "$out" in *"$3"*) true;; *) false;; esac && ok "$1" || ko "$1: rc $rc, want $2 and '$3'; got: $out"; }
good; echo "$all" > "$T/labels"; row "a complete profile passes" 0 "profile emu: ok"
echo '[{"name":"bug"},{"name":"ready-for-agent"},{"name":"wontfix"}]' > "$T/labels"
row "a missing label is named" 1 "label missing: ready-for-human"
row "every missing label is named" 1 "label missing: needs-info"
echo '[{"name":"bug"},{"name":"ready-for-agent"},{"name":"ready-for-human"},{"name":"needs-triage"},{"name":"needs-info"},{"name":"wontfix"}]' > "$T/labels"
row "a missing ready-for-HAL9K label is named" 1 "label missing: ready-for-HAL9K"
good; printf 'SG_LABELS_REQUIRED=bug\n' >> "$T/p/emu.sh"
echo '[{"name":"bug"},{"name":"ready-for-agent"},{"name":"ready-for-human"},{"name":"needs-triage"}]' > "$T/labels"
row "a missing ready-for-HAL9K label is named even when a profile narrows the required list" 1 "label missing: ready-for-HAL9K"
# #1696: the triage labels are read through the bootstrap, so a profile that narrows SG_LABELS_REQUIRED names a missing
# ready-for-agent or ready-for-human all the same, with the same 'label missing:' prefix.
echo '[{"name":"bug"},{"name":"needs-info"},{"name":"wontfix"},{"name":"ready-for-human"},{"name":"ready-for-HAL9K"},{"name":"needs-triage"}]' > "$T/labels"
row "a missing ready-for-agent label is named through the bootstrap" 1 "label missing: ready-for-agent"
echo '[{"name":"bug"},{"name":"needs-info"},{"name":"wontfix"},{"name":"ready-for-agent"},{"name":"ready-for-HAL9K"},{"name":"needs-triage"}]' > "$T/labels"
row "a missing ready-for-human label is named through the bootstrap" 1 "label missing: ready-for-human"
echo "$all" > "$T/labels"
printf ': "${SG_REPO:=owner/repo}"\n' > "$T/p/emu.sh"; row "the example placeholder is refused" 1 "SG_REPO"
good; printf 'SG_PROJECT=\n' >> "$T/p/emu.sh"; row "an unset board is named" 1 "SG_PROJECT"
good; git -C "$T/co" remote set-url origin git@github.com:acme/other.git; row "a checkout of another repository is named" 1 "SG_CHECKOUT"
git -C "$T/co" remote set-url origin git@github.com:acme/emu.git
good; printf 'SG_NIGHTLY_SERIAL="nosuchtool-770 -q"\n' >> "$T/p/emu.sh"; row "a nightly command that does not resolve is named" 1 "SG_NIGHTLY_SERIAL"
good; printf 'SG_LOCAL_GATE_CMD="./nosuch-gate.sh"\n' >> "$T/p/emu.sh"; row "a gate command that does not resolve is named" 1 "SG_LOCAL_GATE_CMD"
good; printf 'SG_LOCAL_GATE_CMD="./gate.sh"; SG_E2E_CMD="true"\n' >> "$T/p/emu.sh"; printf '#!/bin/sh\n' > "$T/co/gate.sh"; chmod +x "$T/co/gate.sh"
row "gate commands that resolve in the checkout pass" 0 "profile emu: ok"
# #770: the board vocabulary is checked against the shipped definition, and a dry frontier pass runs; neither writes
good; rm -f "$T/gate.sh" "$T"/*.log
echo "MISSING option Doing" > "$T/board.out"; echo 1 > "$T/board.rc"; row "a board that drifts from the shipped definition is named" 1 "board vocabulary: MISSING option Doing"
rm -f "$T/board.out"; echo 0 > "$T/board.rc"
printf 'Traceback (most recent call last):\nRuntimeError: gh: API rate limit exceeded\nboard_define.py: board unreadable: gh: API rate limit exceeded\n' > "$T/board.out"; echo 2 > "$T/board.rc"
row "a refused board read is named in one line" 1 "board vocabulary unreadable: gh: API rate limit exceeded"
rm -f "$T/board.out"; echo 0 > "$T/board.rc"
grep -q -- "--definition $d/board-definition.json --check acme 3" "$T/board.log" && ok "the board check reads the shipped definition for the profile's board" || ko "board check argv: $(cat "$T/board.log")"
echo "boom" > "$T/frontier.out"; echo 3 > "$T/frontier.rc"; row "a dry frontier pass that fails is named" 1 "dry frontier pass: exit 3: boom"
rm -f "$T/frontier.out"; echo 0 > "$T/frontier.rc"
grep -q "dry=1" "$T/frontier.log" && ! grep -q "state=$T/state" "$T/frontier.log" && ! grep -q "state=$T/state" "$T/board.log" \
  && ok "the frontier pass is dry, and neither check writes the loop's state" || ko "dry pass: $(cat "$T/frontier.log" "$T/board.log")"
printf 'if then\n' > "$T/p/emu.sh"; row "a profile that does not parse is named" 1 "does not parse"
out=$(run nosuch); [ $? = 1 ] && case "$out" in *nosuch*) true;; *) false;; esac && ok "an unknown profile is named" || ko "unknown profile: $out"
exit $fail
