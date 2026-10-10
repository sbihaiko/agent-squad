"""`squad config set`, `reset` and `init` write the project file with only the keys the owner changed."""
import pytest
import yaml

from squad import cli
from squad import project_settings as P
from squad.options import OptionError


def run(tmp_path, *args):
    return cli.main(["config", "--target", str(tmp_path), *args])


def data(tmp_path):
    return yaml.safe_load(P.path_for(tmp_path).read_text())


def test_set_writes_one_key(tmp_path):
    assert run(tmp_path, "set", "limits.max_stalls", "40") == 0
    assert data(tmp_path) == {"limits": {"max_stalls": 40}}
    assert run(tmp_path, "set", "options.model", "opus") == 0
    assert data(tmp_path) == {"limits": {"max_stalls": 40}, "options": {"model": "opus"}}


@pytest.mark.parametrize("key,value,word", [
    ("limits.nope", "1", "limits.nope"),
    ("options.api_key", "x", "credential"),
    ("limits.max_stalls", "0", "out of range"),
    ("limits.context_tokens", "99999", "100000"),
])
def test_set_refuses(tmp_path, capsys, key, value, word):
    assert run(tmp_path, "set", key, value) == 2
    assert word in capsys.readouterr().err
    assert not P.path_for(tmp_path).exists()


def test_reset_keeps_the_others(tmp_path):
    run(tmp_path, "set", "limits.max_stalls", "40")
    run(tmp_path, "set", "limits.deadline_seconds", "60")
    run(tmp_path, "set", "options.model", "opus")
    assert run(tmp_path, "reset", "limits.max_stalls") == 0
    assert data(tmp_path) == {"limits": {"deadline_seconds": 60}, "options": {"model": "opus"}}
    run(tmp_path, "reset", "options.model")
    assert data(tmp_path) == {"limits": {"deadline_seconds": 60}}


def test_second_set_changes_nothing(tmp_path):
    run(tmp_path, "set", "limits.max_stalls", "40")
    f = P.path_for(tmp_path)
    before, mtime = f.read_text(), f.stat().st_mtime_ns
    assert P.set_key(tmp_path, "limits.max_stalls", "40") is False
    assert f.read_text() == before and f.stat().st_mtime_ns == mtime


def test_init_round_trips(tmp_path):
    assert run(tmp_path, "init") == 0
    loaded, record = P.load(tmp_path)
    assert loaded == {} and record["sha256"]
    run(tmp_path, "set", "run.max_parallel", "3")
    assert run(tmp_path, "init") == 0   # an existing file is kept
    assert P.load(tmp_path)[0] == {"run": {"max_parallel": 3}}


def test_role_key(tmp_path):
    run(tmp_path, "set", "roles.critic.model", "opus")
    assert data(tmp_path) == {"roles": {"critic": {"model": "opus"}}}
    run(tmp_path, "reset", "roles.critic.model")
    assert data(tmp_path) == {}


def test_reset_unknown_key_refused(tmp_path):
    with pytest.raises(OptionError):
        P.reset_key(tmp_path, "limits.nope")
