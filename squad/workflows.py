"""Workflows by id, for the plugin commands (`squad editor`, `squad start`).

A workflow is a graph file `docs/squad/workflows/<id>.graph.json`, under the directory the command runs in (the project that uses the
plugin, not the plugin's own copy). The editor saves there. With no id, a run uses the dynamic
workflow, called `default`: the owner's `default` file when it exists, else the owner's `dynamic` file (the old name, an alias), else the
router graph in `docs/design/`.
"""
import copy
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBDIR = Path("docs") / "squad" / "workflows"
DEFAULT_ID = "default"
DYNAMIC_ID = "dynamic"        # the old name of `default`: an alias
DYNAMIC_SEED = ROOT / "docs" / "design" / "router.graph.json"
SHIPPED = ROOT / "docs" / "workflows"      # workflows the plugin ships; a file of the same id in the project's folder wins
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
    mine = {p.name[:-len(SUFFIX)] for p in folder.glob("*" + SUFFIX)} if folder.is_dir() else set()
    shipped = {p.name[:-len(SUFFIX)] for p in SHIPPED.glob("*" + SUFFIX)} if SHIPPED.is_dir() else set()
    return sorted(mine | shipped)


def valid_id(name):
    """The one id rule (workflow ids, run names): letters, digits, '.', '_' and '-'; no leading dot, no separator."""
    return bool(_ID.match(name))


def _path(workflow, folder=None):
    if not valid_id(workflow):
        raise WorkflowError(f"{workflow!r} is not a workflow id: use letters, digits, '.', '_' and '-'")
    return Path(folder or default_dir()) / (workflow + SUFFIX)


def _unnamed(workflow):
    return workflow is None or workflow in (DEFAULT_ID, DYNAMIC_ID)


def _owner_file(folder):
    """The owner's file for the unnamed workflow: `default`, else `dynamic`; None when there is neither."""
    for wid in (DEFAULT_ID, DYNAMIC_ID):
        mine = _path(wid, folder)
        if mine.is_file():
            return mine
    return None


def resolve(workflow=None, folder=None, seed=DYNAMIC_SEED):
    """The graph file for a workflow id. No id, `default` or `dynamic`: the owner's file, else the seed."""
    if _unnamed(workflow):
        return _owner_file(folder) or Path(seed)
    path = _path(workflow, folder)
    if not path.is_file() and (SHIPPED / (workflow + SUFFIX)).is_file():
        return SHIPPED / (workflow + SUFFIX)
    if not path.is_file():
        known = ", ".join(ids(folder)) or "none yet"
        raise WorkflowError(f"no workflow {workflow!r} in {folder or default_dir()} (known: {known})")
    return path


def for_editor(workflow=None, folder=None, seed=DYNAMIC_SEED):
    """The graph file the editor works on. The unnamed workflow, and any new id, is copied from the seed into the save folder first, so a save never edits the seed."""
    if _unnamed(workflow):
        mine = _owner_file(folder)
        if mine is None:
            mine = _path(DEFAULT_ID, folder)
            mine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(seed, mine)
        return mine
    path = _path(workflow, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() and not (SHIPPED / (workflow + SUFFIX)).is_file():
        shutil.copyfile(seed, path)
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


def known_skills():
    from squad import skills
    return set(skills.load()) | {p.name[len("agent-"):] for p in (ROOT / ".agents" / "skills").glob("agent-*")}


def names_only_a_skill(text):
    """True when the whole request is one known skill name, with or without a slash: the router has no work item to find."""
    return text.strip().lstrip("/").lower() in known_skills()


def unrunnable_skills(doc, text):
    """The skills a request names with a slash (`/to-spec`) that this graph cannot run: a skill the project knows (vendored, or an
    `agent-` door) and no node of the graph allows. A run would otherwise end needs-human on its first answer."""
    from squad import skills
    known = known_skills()
    named = {m for m in re.findall(r"(?<![\w/.~-])/([a-z][a-z0-9-]+)", text) if m in known}
    return sorted(named - skills.graph_skills(doc))


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise WorkflowError(f"cannot read {path}: {e}") from e
