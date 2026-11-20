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


COMMENTED = """# Project settings for the fixture repository.
# Owner ruling: five runs live at once.
caps:
  runs: 5                           # owner ruling: five runs at once
  children: 12
  gates: 3                          # owner ruling: three gate slots
limits:
  max_budget_usd: 30                # owner ruling: a child may spend 30 USD
"""
TAILNET_ENV = "SQUAD_TAILNET_HOST=box.example.ts.net\nSQUAD_TAILNET_ALLOW=owner@example.com\n"


def settings_file(tmp_path, text=COMMENTED):
    f = P.path_for(tmp_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def test_a_save_of_one_cap_changes_only_the_line_it_sets(tmp_path):
    f = settings_file(tmp_path)
    assert P.set_key(tmp_path, "caps.runs", "4") is True
    before, after = COMMENTED.splitlines(), f.read_text().splitlines()
    assert len(after) == len(before)
    assert [i for i, (a, b) in enumerate(zip(before, after)) if a != b] == [3]
    assert after[3] == "  runs: 4                           # owner ruling: five runs at once"


def test_a_save_keeps_every_comment_and_adds_a_new_key_at_the_end_of_its_section(tmp_path):
    f = settings_file(tmp_path)
    assert P.set_keys(tmp_path, [("caps.gates", "2"), ("options.model", "opus")]) is True
    after = f.read_text()
    for line in COMMENTED.splitlines():
        if "#" in line:
            assert line[line.index("#"):] in after
    assert "  children: 12" in after.splitlines()
    assert after.splitlines()[-2:] == ["options:", "  model: opus"]
    assert yaml.safe_load(after) == {"caps": {"runs": 5, "children": 12, "gates": 2},
                                     "limits": {"max_budget_usd": 30}, "options": {"model": "opus"}}


def test_a_save_with_tailnet_keys_in_env_writes_no_tailnet_key_to_the_tracked_file(tmp_path):
    f = settings_file(tmp_path)
    (tmp_path / ".env").write_text(TAILNET_ENV)
    assert P.set_key(tmp_path, "caps.runs", "4") is True
    text = f.read_text()
    assert "tailnet" not in text and "box.example.ts.net" not in text and "owner@example.com" not in text


def test_a_first_save_with_tailnet_keys_in_env_creates_the_file_with_the_one_key(tmp_path):
    (tmp_path / ".env").write_text(TAILNET_ENV)
    P.set_key(tmp_path, "caps.runs", "4")
    assert P.path_for(tmp_path).read_text() == "caps:\n  runs: 4\n"


def test_a_reset_of_one_cap_removes_only_its_line_and_writes_no_env_tailnet_key(tmp_path):
    f = settings_file(tmp_path)
    (tmp_path / ".env").write_text(TAILNET_ENV)
    assert P.reset_key(tmp_path, "caps.runs") is True
    assert f.read_text().splitlines() == [line for i, line in enumerate(COMMENTED.splitlines()) if i != 3]
