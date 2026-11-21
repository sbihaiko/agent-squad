"""The tailnet block is personal: it lives in the project's gitignored `.env`, never in the tracked `docs/squad/defaults.yaml`."""
import subprocess

import pytest
import yaml

from squad import project_settings as ps
from squad.options import OptionError


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env\n")
    return tmp_path


def test_set_writes_a_tailnet_key_to_the_env_file_and_never_the_tracked_file(repo):
    ps.set_key(repo, "tailnet.host", "box.tail1.ts.net")
    ps.set_key(repo, "tailnet.allow", '["me@example.com", "you@example.com"]')
    env = (repo / ".env").read_text()
    assert "SQUAD_TAILNET_HOST=box.tail1.ts.net" in env and "SQUAD_TAILNET_ALLOW=me@example.com,you@example.com" in env
    assert "tailnet" not in (ps.path_for(repo).read_text() if ps.path_for(repo).is_file() else "")


def test_load_reads_the_tailnet_block_from_the_env_file(repo):
    (repo / ".env").write_text("OTHER=1\nSQUAD_TAILNET_HOST=box.tail1.ts.net\nSQUAD_TAILNET_ALLOW=me@example.com\nSQUAD_TAILNET_LOOP_WRITE=true\n")
    data, _ = ps.load(repo)
    assert data["tailnet"] == {"host": "box.tail1.ts.net", "allow": ["me@example.com"], "loop_write": True}


def test_load_ignores_a_retired_terminal_variable(repo):
    """The hub hosts no terminal (#2492): `.env` names no terminal grant, and a stored line for one is ignored."""
    (repo / ".env").write_text("SQUAD_TAILNET_HOST=box.tail1.ts.net\nSQUAD_TAILNET_TERMINALS_READ=true\n"
                               "SQUAD_TAILNET_TERMINALS_ATTACH=true\nSQUAD_TAILNET_TERMINALS_SEND=true\n")
    assert ps.env_tailnet(repo) == {"host": "box.tail1.ts.net"}
    data, _ = ps.load(repo)
    assert data["tailnet"] == {"host": "box.tail1.ts.net"}


def test_the_env_file_wins_over_a_tailnet_block_left_in_the_tracked_file(repo):
    ps.path_for(repo).parent.mkdir(parents=True)
    ps.path_for(repo).write_text(yaml.safe_dump({"tailnet": {"host": "old.ts.net", "allow": ["old@example.com"]}}))
    (repo / ".env").write_text("SQUAD_TAILNET_ALLOW=me@example.com\n")
    data, _ = ps.load(repo)
    assert data["tailnet"] == {"host": "old.ts.net", "allow": ["me@example.com"]}


def test_reset_removes_the_env_line_and_keeps_the_others(repo):
    (repo / ".env").write_text("OTHER=1\nSQUAD_TAILNET_HOST=box.tail1.ts.net\n")
    assert ps.reset_key(repo, "tailnet.host")
    assert (repo / ".env").read_text() == "OTHER=1\n"


def test_set_refuses_when_git_would_track_the_env_file(repo):
    (repo / ".gitignore").write_text("")
    with pytest.raises(OptionError, match="gitignore"):
        ps.set_key(repo, "tailnet.host", "box.tail1.ts.net")
    assert not (repo / ".env").exists()


def test_a_bad_env_value_is_refused_by_name(repo):
    (repo / ".env").write_text("SQUAD_TAILNET_ALLOW=not-an-email\n")
    with pytest.raises(OptionError, match="tailnet.allow"):
        ps.load(repo)


def test_loop_write_reads_from_the_env_file_as_a_bool_only(repo):
    (repo / ".env").write_text("SQUAD_TAILNET_LOOP_WRITE=true\n")
    data, _ = ps.load(repo)
    assert data["tailnet"]["loop_write"] is True
    assert ps.env_tailnet(repo) == {"loop_write": True}


def test_loop_write_set_key_writes_the_env_name(repo):
    ps.set_key(repo, "tailnet.loop_write", "true")
    assert "SQUAD_TAILNET_LOOP_WRITE=true" in (repo / ".env").read_text()


def test_profiler_write_reads_from_the_env_file_as_a_bool_only(repo):
    (repo / ".env").write_text("SQUAD_TAILNET_PROFILER_WRITE=true\n")
    data, _ = ps.load(repo)
    assert data["tailnet"]["profiler_write"] is True
    assert ps.env_tailnet(repo) == {"profiler_write": True}
