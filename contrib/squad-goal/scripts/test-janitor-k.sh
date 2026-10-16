#!/bin/bash
# Regression test for janitor rule K: a `disp-<n>` marker whose request log says `Nothing ran` (10 min old, no live run on the
# issue) is dropped like rule E does for `verdict failed`, so autofrontier/autobug dispatch the issue again.
# usage: bash test-janitor-k.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *issues/601*) echo open;;
  *search/issues*) echo 0;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
echo req-x > "$T/st/auto/disp-601"; touch -t 202001010000 "$T/st/auto/disp-601"
echo "Nothing ran: vendor/skills is not clean" > "$T/st/req-x.log"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" 2>&1)
[ ! -e "$T/st/auto/disp-601" ] && ok "K: disp marker of a refused start dropped" || ko "K disp marker kept: $o"
rm -rf "$T"; exit $fail
