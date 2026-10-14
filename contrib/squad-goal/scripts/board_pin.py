#!/usr/bin/env python3
"""board_pin.py [--published <file>]: is the pinned copy of the board definition the published one (ADR-0141)?
Prints one `drift:` line and exits 1 when the published definition differs from the pinned copy, and exits 0 when it is
the same. With no --published it reads the published repository's board.json over REST (gh api), never a clone."""
import argparse, base64, json, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from board_columns import pin_drift

PUBLISHED = "repos/sbihaiko/squad-board/contents/board.json"

ap = argparse.ArgumentParser(); ap.add_argument("--published"); a = ap.parse_args()
if a.published: text = pathlib.Path(a.published).read_text()
else:
    door = ["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).resolve().parent / "ghcall.sh"), "gh_api", PUBLISHED]
    r = subprocess.run(door, capture_output=True, text=True)   # through the door's read (ADR-0277), never a call site of this script's
    if r.returncode: print(f"cannot read the published definition: {(r.stderr or r.stdout).strip()}"); sys.exit(2)
    text = base64.b64decode(json.loads(r.stdout)["content"]).decode()
report = pin_drift(text)
print("\n".join(report) if report else "the pinned copy is the published definition")
sys.exit(1 if report else 0)
