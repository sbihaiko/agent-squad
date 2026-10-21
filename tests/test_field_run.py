"""Field run on a throwaway repo (2026-10-06): what a first-time user hit, each pinned by a test."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from squad import cli, launch

ROOT = Path(__file__).resolve().parent.parent


def test_start_without_the_sdk_exits_2_with_the_install_hint_and_makes_no_run_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)       # an import of it now fails, as on a bare `pip install agent-squad`
    runs = tmp_path / "runs"
    assert cli.main(["start", "--runs-dir", str(runs), "--target", str(tmp_path), "add a function"]) == cli.USAGE
    err = capsys.readouterr().err
    assert "claude-agent-sdk" in err and "--extra sdk" in err and "Traceback" not in err
    assert not runs.exists()


def test_start_prints_the_run_folder_as_an_absolute_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: SimpleNamespace())      # the SDK is present

    class Eng:
        run_id, run_dir = "r1", Path("runs/r1")

        async def run(self):
            return SimpleNamespace(message="ok", exit_code=0)
    monkeypatch.setattr(launch, "launch", lambda *a, **k: Eng())
    assert cli.main(["start", "--target", str(tmp_path), "add a function"]) == 0
    assert f"run folder: {tmp_path.resolve() / 'runs' / 'r1'}" in capsys.readouterr().out


def test_setup_check_says_ok_not_in_step(tmp_path, capsys):
    assert cli.main(["setup", "--target", str(tmp_path), "--apply", "--memory-dir", str(tmp_path / "mem")]) == 0
    capsys.readouterr()
    assert cli.main(["setup", "--target", str(tmp_path), "--check"]) == 0
    assert capsys.readouterr().out.strip() == "setup check: ok, CLAUDE.md and the rule ADRs agree"


def _seed_router():
    from squad.workflows import DYNAMIC_SEED
    return next(n for n in json.loads(DYNAMIC_SEED.read_text())["nodes"] if n["name"] == "router")


def test_the_router_tells_every_child_to_end_with_ac_commands():
    """The field run: the child landed a green change, and the final gate failed with `no_ac_commands`, because nothing asked for them."""
    for text in ((ROOT / "prompts" / "router.md").read_text(), _seed_router()["instructions"]):
        assert "`ac_commands`" in text and "no_ac_commands" in text and "end with" in text


def test_every_command_in_the_quickstart_parses_and_the_page_is_linked_and_short():
    import re
    import shlex
    text = (ROOT / "docs" / "quickstart.md").read_text()
    lines = [x for block in re.findall(r"```bash\n(.*?)```", text, re.S) for x in block.splitlines()]
    cmds = [x for x in lines if x.startswith("squad ")]
    assert len(cmds) >= 10 and any(c.startswith("squad work ") for c in cmds) and any(c.startswith("squad task ") for c in cmds)
    assert "no `work` command" not in text
    for line in cmds:
        try:
            cli.parser().parse_args(shlex.split(line)[1:])
        except SystemExit:
            raise AssertionError(f"the CLI rejects the quickstart command: {line}") from None
    assert len(text.splitlines()) < 80 and "docs/quickstart.md" in (ROOT / "README.md").read_text() + (ROOT / "CLAUDE.md").read_text()
    assert "docs/quickstart.md" in (ROOT / "README.md").read_text() and "docs/quickstart.md" in (ROOT / "CLAUDE.md").read_text()
