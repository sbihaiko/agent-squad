#!/bin/bash
# seconds: 1.38
# Regression test for vendor-guard.sh: the step the watch cycle runs every cycle to re-vendor the personal skills when
# they drifted from vendor/skills/VERSIONS.json. The build is the costly step, so the guard keys its input -- the
# personal skills, the vendor tree and VERSIONS.json -- in $SG_STATE and returns at once when the key is unchanged
# (issue #568).
# usage: bash test-vendor-guard.sh [path/to/vendor-guard.sh]
# Hermetic: every inherited SG_* is dropped, the personal skill is a throwaway home, and the fork is a throwaway repo.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/vendor-guard.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G() { git -c user.name=t -c user.email=t@t "$@"; }
repo=$(cd "$d/../../.." && pwd)                 # squad/, which build_vendor_skills.py imports for a `skill:` source

S=$T/s; f=$T/f; st=$T/st; calls=$T/calls; home=$T/home
mkdir -p "$S" "$st" "$f/scripts" "$f/config" "$f/.venv/bin" "$home/.claude/skills/demo/scripts"
cp "$src" "$S/vendor-guard.sh"
cat > "$S/env.sh" <<EOF
export SG_SQUAD=$f SG_STATE=$st
EOF
ln -s "$repo/squad" "$f/squad"
cp "$repo/scripts/build_vendor_skills.py" "$f/scripts/build_vendor_skills.py"
# The python the guard runs logs every call, so the test can tell the key read from the costly build.
realpy=$(command -v python3)
cat > "$f/.venv/bin/python3" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >> "$calls"
exec "$realpy" "\$@"
EOF
chmod +x "$f/.venv/bin/python3"
cat > "$f/config/vendor-skills.yaml" <<EOF
skills:
  - {name: demo, source: skill:demo}
EOF
printf -- '---\nname: demo\n---\nbody v1\n' > "$home/.claude/skills/demo/SKILL.md"
printf 'echo one\n' > "$home/.claude/skills/demo/scripts/run.sh"
run() { SQUAD_SKILL_HOME=$home bash "$S/vendor-guard.sh" 2>&1; }
snap() { ( cd "$f/vendor/skills" && find . -type f | LC_ALL=C sort | while read -r x; do printf '%s %s\n' "$(shasum -a 256 "$x" | cut -d' ' -f1)" "$x"; done ); }
built() { /usr/bin/grep -qxF "$f/scripts/build_vendor_skills.py" "$calls"; }   # a call with no --key: the costly build

# The fork starts with its vendor copy in step: the build is the step the guard runs, not something this fixture fakes.
: > "$calls"
SQUAD_SKILL_HOME=$home "$f/.venv/bin/python3" "$f/scripts/build_vendor_skills.py" >/dev/null 2>&1
G init -q -b local/next "$f"; G -C "$f" add -A; G -C "$f" commit -qm v1

# --- the first pass keeps the key and runs the build ------------------------------------------------------------
: > "$calls"
out=$(run); rc=$?
[ "$rc" = 0 ] && ok "the guard exits 0" || ko "the guard exited $rc: $out"
[ -z "$out" ] && ok "an in-step vendor: the guard says nothing" || ko "the first pass spoke: $out"
built && ok "the first pass runs the build (the costly step)" || ko "the first pass did not build: $(cat "$calls")"

# --- unchanged key: the guard returns at once, and the build does not run ---------------------------------------
: > "$calls"; h=$(G -C "$f" rev-parse HEAD); before=$(snap)
out=$(run); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "unchanged key: the guard says nothing" || ko "an unchanged key spoke (exit $rc): $out"
built && ko "unchanged key still ran the build" || ok "unchanged key: the build (the costly step) did not run"
[ "$(G -C "$f" rev-parse HEAD)" = "$h" ] && [ "$before" = "$(snap)" ] && ok "unchanged key: the guard makes no write" || ko "unchanged key wrote into the fork"

# --- changed key: the guard re-vendors, commits what it built, and keeps the new key -----------------------------
want=$(printf '%s\n%s\n%s' '---' 'name: demo' 'body v2')
printf '%s\n' "$want" > "$home/.claude/skills/demo/SKILL.md"
: > "$calls"; h0=$(G -C "$f" rev-parse HEAD)
out=$(run); rc=$?
[ "$rc" = 0 ] && ok "a changed key exits 0" || ko "the guard exited $rc: $out"
built && ok "changed key: the build runs" || ko "changed key did not build: $(cat "$calls")"
[ "$(cat "$f/vendor/skills/skills/demo/SKILL.md")" = "$want" ] && ok "changed key: the vendor copy follows the personal skill" || ko "the vendor copy was not rebuilt: $(cat "$f/vendor/skills/skills/demo/SKILL.md")"
case "$out" in *VENDOR-GUARD*"re-vendored"*) ok "changed key: the guard commits and says so";; *) ko "the re-vendor was not reported: $out";; esac
[ "$(G -C "$f" rev-parse HEAD)" != "$h0" ] && ok "changed key: the re-vendor lands as a commit" || ko "no commit was made: $out"
: > "$calls"; out=$(run)
[ -z "$out" ] && ! built && ok "after a re-vendor the new key is kept: the next pass skips" || ko "the pass did not skip after a re-vendor (build calls: $(cat "$calls")): $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-vendor-guard: all checks green" || { echo "test-vendor-guard: failed"; exit 1; }
