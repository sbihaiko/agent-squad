#!/bin/bash
# seconds: 0.15
# The overlay's screenshot (#903) captures with a vendored static library: its version, source and hash are recorded beside
# it, and the hub serves it once on every page that carries the overlay. The phone page carries no overlay since #2490, so
# it carries no copy of the library.
# usage: bash test-hub-screenshot.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); export HUB_SRC=${1:-$d/hub.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
lib="$d/vendor-html2canvas-1.4.1.min.js"; rec="$d/vendor-html2canvas.VENDOR"
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }

[ -f "$lib" ] && ok "the vendored library is a file" || ko "the vendored library is missing: $lib"
[ -f "$rec" ] && ok "the vendored library has a record" || ko "the record is missing: $rec"
want=$(sed -n 's/^sha256: //p' "$rec" 2>/dev/null)
got=$(shasum -a 256 "$lib" 2>/dev/null | cut -d' ' -f1)
[ -n "$want" ] && [ "$got" = "$want" ] && ok "the library hash matches its record" || ko "the library hash is $got, the record says $want"
ver=$(sed -n 's/^version: //p' "$rec" 2>/dev/null)
[ -n "$ver" ] && /usr/bin/grep -q "html2canvas $ver " "$lib" && ok "the library banner names the recorded version" || ko "the library banner does not name version $ver"
src=$(sed -n 's/^source: //p' "$rec" 2>/dev/null)
[ -n "$src" ] && ok "the record names a source ($src)" || ko "the record names no source"

out=$(python3 - <<'PY' 2>&1
import importlib.util, os, pathlib, tempfile
here = pathlib.Path(os.environ["HUB_SRC"]).parent
lib = here / "vendor-html2canvas-1.4.1.min.js"
os.environ.update(SG_REPO="x/y", SG_OWNER="x", SG_PROJECT="4", SG_CHECKOUT=tempfile.mkdtemp(), SG_HUB_DIR=tempfile.mkdtemp(), SG_STATE=tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
hub.loop_caps = lambda: None
banner = lib.read_text().split("\n", 2)[1][:80]
page = hub.served_page()
n = page.count(banner)
print(("ok   " if n == 1 else "FAIL ") + f"the hub page carries the vendored library once (found {n})")
print(("ok   " if hub.MOBILE_PAGE.count(banner) == 0 else "FAIL ") + "the mobile page carries no vendored library: its overlay left it with the session controls")
PY
)
echo "$out"; echo "$out" | /usr/bin/grep -q "^FAIL" && fail=1
[ "$fail" = 0 ] && echo "PASS hub-screenshot" || { echo "FAIL hub-screenshot"; exit 1; }
