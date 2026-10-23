#!/bin/bash
# Table test for profile-check.sh (#763): a new profile runs one check, and each setup gap is named before the loop misbehaves.
# The profile parses, names a real repository, board and checkout whose origin is that repository, and the repository holds
# the labels the loop and /to-tickets use. Offline: throwaway profiles and checkouts, a stub reader for the label list.
# usage: bash test-profile-check.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/p" "$T/co"; git -C "$T/co" init -q; git -C "$T/co" remote add origin git@github.com:acme/emu.git
all='[{"name":"bug"},{"name":"ready-for-agent"},{"name":"ready-for-human"},{"name":"needs-triage"},{"name":"needs-info"},{"name":"wontfix"}]'
cat > "$T/reader" <<R
#!/bin/bash
echo "\$1" >> "$T/routes"; cat "$T/labels"
R
chmod +x "$T/reader"
good() { cat > "$T/p/emu.sh" <<P
: "\${SG_REPO:=acme/emu}"; : "\${SG_OWNER:=acme}"; : "\${SG_PROJECT:=3}"; : "\${SG_CHECKOUT:=$T/co}"; : "\${SG_BASE:=dev}"
P
}
run() { env -u SG_REPO -u SG_OWNER -u SG_PROJECT -u SG_CHECKOUT -u SG_BASE SG_PROFILES_DIR="$T/p" SG_STATE="$T/state" \
  SG_GHREAD="$T/reader" bash "$d/profile-check.sh" "$@" 2>&1; }
# row <name> <want rc> <text the output must hold>
row() { local out rc; out=$(run emu); rc=$?
  [ "$rc" = "$2" ] && case "$out" in *"$3"*) true;; *) false;; esac && ok "$1" || ko "$1: rc $rc, want $2 and '$3'; got: $out"; }
good; echo "$all" > "$T/labels"; row "a complete profile passes" 0 "profile emu: ok"
echo '[{"name":"bug"},{"name":"ready-for-agent"},{"name":"wontfix"}]' > "$T/labels"
row "a missing label is named" 1 "label missing: ready-for-human"
row "every missing label is named" 1 "label missing: needs-info"
echo "$all" > "$T/labels"
printf ': "${SG_REPO:=owner/repo}"\n' > "$T/p/emu.sh"; row "the example placeholder is refused" 1 "SG_REPO"
good; printf 'SG_PROJECT=\n' >> "$T/p/emu.sh"; row "an unset board is named" 1 "SG_PROJECT"
good; git -C "$T/co" remote set-url origin git@github.com:acme/other.git; row "a checkout of another repository is named" 1 "SG_CHECKOUT"
git -C "$T/co" remote set-url origin git@github.com:acme/emu.git
good; printf 'SG_NIGHTLY_SERIAL="nosuchtool-770 -q"\n' >> "$T/p/emu.sh"; row "a nightly command that does not resolve is named" 1 "SG_NIGHTLY_SERIAL"
good; printf 'SG_LOCAL_GATE_CMD="./nosuch-gate.sh"\n' >> "$T/p/emu.sh"; row "a gate command that does not resolve is named" 1 "SG_LOCAL_GATE_CMD"
good; printf 'SG_LOCAL_GATE_CMD="./gate.sh"; SG_E2E_CMD="true"\n' >> "$T/p/emu.sh"; printf '#!/bin/sh\n' > "$T/co/gate.sh"; chmod +x "$T/co/gate.sh"
row "gate commands that resolve in the checkout pass" 0 "profile emu: ok"
printf 'if then\n' > "$T/p/emu.sh"; row "a profile that does not parse is named" 1 "does not parse"
out=$(run nosuch); [ $? = 1 ] && case "$out" in *nosuch*) true;; *) false;; esac && ok "an unknown profile is named" || ko "unknown profile: $out"
exit $fail
