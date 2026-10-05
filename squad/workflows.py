"""Workflows by id, for the plugin commands (`squad editor`, `squad start`).

A workflow is a graph file `docs/squad/workflows/<id>.graph.json`, under the directory the command runs in (the project that uses the
plugin, not the plugin's own copy). The editor saves there. With no id, a run uses the dynamic
workflow: the owner's `dynamic` file when it exists, else the router graph in `docs/design/`.
"""
import copy
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBDIR = Path("docs") / "squad" / "workflows"
DYNAMIC_ID = "dynamic"
DYNAMIC_SEED = ROOT / "docs" / "design" / "router.graph.json"
SUFFIX = ".graph.json"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")   # no separator and no leading dot: an id never leaves the folder

KINDS = {
    "work": "The owner made this request. Read it and CLAUDE.md. Do not change any file. Decide what work it needs, and ask for the children that do it.",
    "task": "This is one task. Read it and CLAUDE.md. Do not change any file. Ask for the fewest children that finish this task and nothing more.",
}


class WorkflowError(Exception):
    pass


def default_dir():
    return Path.cwd() / SUBDIR


def ids(folder=None):
    folder = Path(folder or default_dir())
    return sorted(p.name[:-len(SUFFIX)] for p in folder.glob("*" + SUFFIX)) if folder.is_dir() else []


def _path(workflow, folder=None):
    if not _ID.match(workflow):
        raise WorkflowError(f"{workflow!r} is not a workflow id: use letters, digits, '.', '_' and '-'")
    return Path(folder or default_dir()) / (workflow + SUFFIX)


def resolve(workflow=None, folder=None, seed=DYNAMIC_SEED):
    """The graph file for a workflow id. No id: the dynamic workflow."""
    if workflow is None or workflow == DYNAMIC_ID:
        mine = _path(DYNAMIC_ID, folder)
        return mine if mine.is_file() else Path(seed)
    path = _path(workflow, folder)
    if not path.is_file():
        known = ", ".join(ids(folder)) or "none yet"
        raise WorkflowError(f"no workflow {workflow!r} in {folder or default_dir()} (known: {known})")
    return path


def for_editor(workflow=None, folder=None, seed=DYNAMIC_SEED):
    """The graph file the editor works on. The dynamic workflow is copied into the save folder first, so a save never edits the seed."""
    if workflow is None or workflow == DYNAMIC_ID:
        mine = _path(DYNAMIC_ID, folder)
        if not mine.is_file():
            mine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(seed, mine)
        return mine
    Path(folder or default_dir()).mkdir(parents=True, exist_ok=True)
    return resolve(workflow, folder, seed)


def entry_agent(doc):
    """The agent that reads the request: the entry of the first dispatch node, else the first agent node."""
    nodes = {n["name"]: n for n in doc["nodes"]}
    for n in doc["nodes"]:
        if n["kind"] == "dispatch" and n.get("entry") in nodes:
            return nodes[n["entry"]]
    for n in doc["nodes"]:
        if n["kind"] == "agent":
            return n
    raise WorkflowError("the workflow has no agent node to read the request")


def with_request(doc, text, kind="work"):
    """A copy of the graph whose entry agent carries the request. Its own instructions stay, first."""
    if not text.strip():
        raise WorkflowError(f"a {kind} needs text")
    g = copy.deepcopy(doc)
    node = entry_agent(g)
    own = node.get("instructions")
    node["instructions"] = (own + "\n\n" if own else "") + f"{KINDS[kind]}\n\n{kind.capitalize()}:\n{text.strip()}"
    return g


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise WorkflowError(f"cannot read {path}: {e}") from e
