"""The hub's source as one text: hub.py and the theme files it loads, in the order it loads them (#2742).

The hub is one module namespace composed from theme files: hub.py holds the imports, the loader, the server and the route
table, and every theme file holds one subject's code. A test that reads the hub's source wants all of it, so it reads the
same code whether a name sits in hub.py or in a theme. The read order is the hub's load order, which is the order the code
had before the split, so an assertion that slices from one line to another still reads the lines between them.
"""
import pathlib
import re

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "contrib/squad-goal" / "scripts"
HUB = SCRIPTS / "hub.py"
SERVER_MARK = "# ---------- the hub server ----------"
_THEME_RE = re.compile(r'^\s*"([\w.]+)",$', re.M)


def _read(path):
    """Read a file without going through Path.read_text: the test harness serves the hub's composed source there."""
    with path.open(encoding="utf-8") as f:
        return f.read()


def themes():
    """The theme module names, in hub.py's load order."""
    text = _read(HUB)
    block = text.split("_THEMES = (", 1)[1].split(")", 1)[0]
    return _THEME_RE.findall(block)


def hub_source():
    """hub.py and every theme module in load order: the one text every source-reading test reads."""
    text = _read(HUB)
    head, _, server = text.partition(SERVER_MARK)
    return head + "".join(_read(SCRIPTS / (name + ".py")) for name in themes()) + SERVER_MARK + server
