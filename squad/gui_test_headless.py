"""Where the headless GUI case may build (#1414): never into the tree the real-app cases launch."""
from pathlib import Path


class SharedTree(Exception):
    pass


def headless_repo(env):
    """The MESENAI_HEADLESS_REPO checkout, refused when MESEN_GUI_BINARY / MESENAI_APP lives inside it:
    the headless case builds the UI there and would rewrite the binary a later case launches."""
    repo = Path(env["MESENAI_HEADLESS_REPO"]).expanduser()
    for name in ("MESEN_GUI_BINARY", "MESENAI_APP"):
        if env.get(name) and Path(env[name]).expanduser().resolve().is_relative_to(repo.resolve()):
            raise SharedTree(f"MESENAI_HEADLESS_REPO {repo} is the checkout {name} comes from; "
                             "point it at a separate tree (e.g. ~/wt-e2e-headless) so the headless build never rewrites the launched binary")
    return repo
