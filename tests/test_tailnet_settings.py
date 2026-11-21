"""The tailnet block of the project settings file: its keys, its defaults, and its place outside the resume hash.

Guards: `tailnet.host` and `tailnet.allow` validate and name their key on a refusal; no file or no block loads an empty host and an
empty list; `squad config set` writes and reads both keys; a change only to the block keeps the recorded hash and a resume passes;
a change to any other key still refuses; a record with the old raw-bytes hash is compared by that method.
"""
import hashlib
import json
import pathlib

import pytest

from squad import cli, defaults, launch, record
from squad import project_settings as P
from squad.adapter import ScriptedAdapter
from squad.engine import ResumeRefused
from squad.graph import from_doc
from squad.options import OptionError
from tests.test_resume import wait_doc

BASE = "limits:\n  max_stalls: 4\n"
BLOCK = "tailnet:\n  host: mac-mini.tail1234.ts.net\n  allow:\n    - operator@example.com\n"
BOTH = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}


def settings(target, text):
    f = P.path_for(target)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


async def started(target):
    eng = launch.launch(from_doc(wait_doc()), target / "r", adapter=ScriptedAdapter(BOTH, repeat_last=True), target=target)
    assert (await eng.run()).exit_code == 10


def resumed(target):
    return launch.resume_engine(from_doc(wait_doc()), target / "r", adapter=ScriptedAdapter(BOTH, repeat_last=True), target=target)


def events(target):
    return record.read(target / "r" / "record.jsonl")


def test_valid_tailnet_values_have_no_defect():
    assert P.defects({"tailnet": {"host": "mac-mini.tail1234.ts.net", "allow": ["operator@example.com"]}}) == []


@pytest.mark.parametrize("data,key", [
    ({"tailnet": {"host": 5}}, "tailnet.host"),
    ({"tailnet": {"host": ""}}, "tailnet.host"),
    ({"tailnet": {"allow": "operator@example.com"}}, "tailnet.allow"),
    ({"tailnet": {"allow": ["not-an-email"]}}, "tailnet.allow"),
    ({"tailnet": {"auth_key": "x"}}, "tailnet.auth_key"),
    ({"tailnet": {"host": "tskey-auth-kABC123"}}, "tailnet.host"),
    ({"tailnet": {"allow": ["secret@example.com"]}}, "tailnet.allow"),
])
def test_a_bad_tailnet_key_or_value_is_refused_naming_the_key(data, key):
    with pytest.raises(OptionError, match=key):
        P.validate(data)


def test_a_credential_shaped_tailnet_key_or_value_is_refused_as_a_credential():
    with pytest.raises(OptionError, match="credential"):
        P.validate({"tailnet": {"auth_key": "x"}})
    with pytest.raises(OptionError, match="credential"):
        P.validate({"tailnet": {"host": "tskey-auth-kABC123"}})


def test_no_file_or_no_block_loads_an_empty_host_and_an_empty_list(tmp_path):
    cfg = defaults.load()
    for data in (P.load(tmp_path)[0], {"limits": {"max_stalls": 4}}):
        merged, _ = P.overlay(cfg, data)
        assert not merged["tailnet"]["host"] and merged["tailnet"]["allow"] == []


def test_config_set_writes_both_keys_and_reads_them_back(tmp_path):
    assert cli.main(["config", "--target", str(tmp_path), "set", "tailnet.host", "mac-mini.tail1234.ts.net"]) == 0
    assert cli.main(["config", "--target", str(tmp_path), "set", "tailnet.allow", '["operator@example.com"]']) == 0
    assert P.load(tmp_path)[0] == {"tailnet": {"host": "mac-mini.tail1234.ts.net", "allow": ["operator@example.com"]}}


def test_a_change_only_to_the_tailnet_block_keeps_the_hash(tmp_path):
    f = settings(tmp_path, BASE)
    before = P.digest(f)
    f.write_text(BASE + BLOCK)
    assert P.digest(f) == before
    f.write_text(BASE + BLOCK.replace("mac-mini", "mac-studio"))
    assert P.digest(f) == before


def test_the_tailnet_block_is_left_out_of_the_hash_wherever_it_sits(tmp_path):
    f = settings(tmp_path, "limits:\n  max_stalls: 4\nrun:\n  max_parallel: 2\n")
    before = P.digest(f)
    f.write_text("tailnet:\n  host: mac-mini.tail1234.ts.net\n\nlimits:\n  max_stalls: 4\nrun:\n  max_parallel: 2\n")
    assert P.digest(f) == before
    f.write_text("limits:\n  max_stalls: 4\ntailnet:\n  host: mac-mini.tail1234.ts.net\n  allow: []\nrun:\n  max_parallel: 2\n")
    assert P.digest(f) == before


def test_a_change_to_another_key_changes_the_hash(tmp_path):
    f = settings(tmp_path, BASE + BLOCK)
    before = P.digest(f)
    f.write_text(BASE.replace("4", "5") + BLOCK)
    assert P.digest(f) != before


@pytest.mark.anyio
async def test_a_resume_passes_after_only_the_tailnet_block_changed(tmp_path):
    settings(tmp_path, BASE)
    await started(tmp_path)
    settings(tmp_path, BASE + BLOCK)
    await resumed(tmp_path).resume(answer="pass")
    assert any(e["event"] == "resume" for e in events(tmp_path))


@pytest.mark.anyio
async def test_a_resume_refuses_after_another_key_changed_next_to_the_tailnet_block(tmp_path):
    settings(tmp_path, BASE + BLOCK)
    await started(tmp_path)
    settings(tmp_path, BASE.replace("4", "5") + BLOCK)
    with pytest.raises(ResumeRefused, match="project settings file .* changed"):
        await resumed(tmp_path).resume(answer="pass")


@pytest.mark.anyio
async def test_a_record_with_the_old_raw_hash_is_compared_by_the_old_method(tmp_path):
    f = settings(tmp_path, BASE)
    await started(tmp_path)
    path = tmp_path / "r" / "record.jsonl"
    lines = path.read_text().splitlines()
    start = json.loads(lines[0])
    start["project_settings"]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    start.pop("project_settings_method", None)
    path.write_text("\n".join([json.dumps(start), *lines[1:]]) + "\n")
    settings(tmp_path, BASE)
    await resumed(tmp_path).resume(answer="pass")
    settings(tmp_path, BASE + BLOCK)
    with pytest.raises(ResumeRefused, match="project settings file .* changed"):
        await resumed(tmp_path).resume(answer="pass")


@pytest.mark.parametrize("key", ["terminals_read", "terminals_attach", "terminals_send"])
def test_the_three_retired_terminal_keys_validate_and_are_ignored(key):
    """The hub hosts no terminal (#2492): a project file that still carries one is accepted, whatever its value."""
    P.validate({"tailnet": {key: True}})
    P.validate({"tailnet": {key: "left as it was"}})
    assert f"tailnet.{key}" not in P.CATALOG
    assert f"tailnet.{key}" not in P.ENV_KEYS


def test_a_key_beside_the_retired_ones_is_still_refused():
    with pytest.raises(OptionError, match="tailnet.terminals_other"):
        P.validate({"tailnet": {"terminals_other": True}})


def test_the_shipped_defaults_name_no_terminal_grant():
    text = (pathlib.Path(__file__).resolve().parent.parent / "config" / "agent-defaults.yaml").read_text()
    block = text.split("tailnet:", 1)[1].split("\nwatch:", 1)[0]
    for key in ("terminals_read", "terminals_attach", "terminals_send"):
        assert key not in block


def test_loop_write_takes_true_or_false_and_names_its_key_otherwise():
    P.validate({"tailnet": {"loop_write": True}})
    P.validate({"tailnet": {"loop_write": False}})
    with pytest.raises(OptionError, match="tailnet.loop_write"):
        P.validate({"tailnet": {"loop_write": "yes"}})


def test_the_shipped_default_leaves_the_tailnet_loop_switch_read_only():
    text = (pathlib.Path(__file__).resolve().parent.parent / "config" / "agent-defaults.yaml").read_text()
    assert "loop_write: false" in text.split("tailnet:", 1)[1].split("\nwatch:", 1)[0]


def test_profiler_write_is_its_own_grant_off_by_default():
    """The Loop tab's switch (#1743) has its own grant: the loop-write grant is not widened (ADR-0162)."""
    P.validate({"tailnet": {"profiler_write": True}})
    P.validate({"tailnet": {"profiler_write": False}})
    with pytest.raises(OptionError, match="tailnet.profiler_write"):
        P.validate({"tailnet": {"profiler_write": "yes"}})
    assert P.ENV_KEYS["tailnet.profiler_write"] == "SQUAD_TAILNET_PROFILER_WRITE"
    text = (pathlib.Path(__file__).resolve().parent.parent / "config" / "agent-defaults.yaml").read_text()
    assert "profiler_write: false" in text.split("tailnet:", 1)[1].split("\nwatch:", 1)[0]
