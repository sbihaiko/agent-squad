"""The free-slot count pays one Python startup, and that startup stays light (#556).

`autobug.sh` asks the machine-wide ceiling once a pass by importing `squad.caps`. `squad.options` imported
`squad.graph` at module import, which loads `jsonschema` (about 0.7 s on this machine) into every such startup.
The import is the cost the profile named, so this guard reads the ceiling in a fresh interpreter and asserts that
the graph validator never loaded.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_free_slot_import_does_not_load_the_graph_validator():
    code = (
        "import sys\n"
        "from squad import caps\n"
        "caps.ceiling('.')\n"
        "print('jsonschema' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", "the free-slot import pulled the graph validator (jsonschema) in"
