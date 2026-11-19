#!/bin/bash
# seconds: 0.52
# `board.py --proof` carries the fact of an unproven landing on the issue's card (spec 1163, #1176): `unproven` while the
# proof obligation is open, `proof:red` when the settlement measured a defect, and neither on a green settlement or a
# proven landing. The label write is proven by the issue read that follows it, and a write the read does not carry is a
# READ FAILURE, named, never a fact the card carries (the same rule as a Status write, #105). A card already in the
# wanted state is not written again.
# usage: bash test-board-proof.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"
cp "$(dirname "$src")/issue_state.py" "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/"
labs() { tr ' ' '\n' < "$T/labs-7" 2>/dev/null | /usr/bin/grep . | sort | tr '\n' ' ' | sed 's/ $//'; }
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; D="TMPDIR"
echo "$a" >> "$D/gh.calls"
case "$a" in *"--input -"*) cat > "$D/last.in";; esac
case "$a" in
  *users/o*) echo '{"type":"Organization"}';;
  *"-X POST repos/x/y/issues/7/labels"*)            # add the labels the body names, unless DROP_WRITE says the read never carries it
    [ -e "$D/DROP_WRITE" ] && { echo '[]'; exit 0; }
    sed -n 's/.*"labels": \[\(.*\)\].*/\1/p' "$D/last.in" | tr ',' '\n' | sed 's/[" ]//g' | /usr/bin/grep . >> "$D/labs-7"
    echo '[]';;
  *"-X DELETE repos/x/y/issues/7/labels/proof%3Ared"*) sed -i '' '/^proof:red$/d' "$D/labs-7" 2>/dev/null; echo '[]';;
  *"-X DELETE repos/x/y/issues/7/labels/unproven"*)   sed -i '' '/^unproven$/d' "$D/labs-7" 2>/dev/null; echo '[]';;
  *repos/x/y/issues/7*)                             # one issue read: the labels it carries now
    printf '{"number":7,"state":"open","labels":['
    sep=""; while IFS= read -r l; do [ -n "$l" ] || continue; printf '%s{"name":"%s"}' "$sep" "$l"; sep=","; done < "$D/labs-7"
    printf ']}\n';;
  *) echo '{}';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }
: > "$T/labs-7"; : > "$T/gh.calls"

o=$(run --proof o 4 7 unproven 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(labs)" = unproven ] && ok "an unproven landing puts the fact on the card" || ko "unproven: rc $rc, labels '$(labs)', '$o'"
/usr/bin/grep -q -- '-X POST repos/x/y/issues/7/labels' "$T/gh.calls" && ok "the fact is a label write on the issue" || ko "no label write: $(cat "$T/gh.calls")"
: > "$T/gh.calls"; o=$(run --proof o 4 7 unproven 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(labs)" = unproven ] && ! /usr/bin/grep -q -- '-X POST' "$T/gh.calls" && ok "a card that already carries the fact is not written again" \
  || ko "repeated unproven: rc $rc, labels '$(labs)', calls '$(cat "$T/gh.calls")'"

o=$(run --proof o 4 7 red 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(labs)" = "proof:red unproven" ] && ok "a red settlement keeps the fact and holds the card" || ko "red: rc $rc, labels '$(labs)', '$o'"

o=$(run --proof o 4 7 green 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ -z "$(labs)" ] && ok "a green settlement clears the fact and the hold" || ko "green: rc $rc, labels '$(labs)', '$o'"
/usr/bin/grep -q -- '-X DELETE repos/x/y/issues/7/labels/proof%3Ared' "$T/gh.calls" && ok "the settlement deletes the hold label" || ko "no hold delete: $(cat "$T/gh.calls")"

: > "$T/gh.calls"; o=$(run --proof o 4 7 green 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ -z "$(labs)" ] && ! /usr/bin/grep -q -- '-X DELETE' "$T/gh.calls" && ok "a proven landing that carries no fact writes nothing" \
  || ko "proven landing: rc $rc, labels '$(labs)', calls '$(cat "$T/gh.calls")'"

# A write the next read does not carry is a read failure, named, and never a fact the card carries.
: > "$T/labs-7"; : > "$T/DROP_WRITE"
o=$(run --proof o 4 7 unproven 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"#7"*"does not carry"*) ok "a label the read does not carry is a read failure, named";; *) ko "read failure message: '$o'";; esac \
  || ko "unproven write that never landed: rc $rc, '$o'"
rm -f "$T/DROP_WRITE"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
