"""`squad slack check`: it reads the committed `slack.yaml` and the profile's `slack.local.yaml`, names the failing key in
each message, and exits non-zero on a defect (ADR-0177).

Guards: a defect in either file prints one `fail:` line that names the file and the key, and the exit code is 1; a valid
pair exits 0 and prints `ok`; a missing file is not a defect; a file that is not YAML is one defect, never a traceback.
"""
import yaml

from squad import cli

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}],
    "workspaces": [{"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k"}],
}
LOCAL = {
    "version": 1,
    "workspaces": [{"id": "T0CLIENT01", "label": "client-a", "class": "guest",
                    "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen"}]}],
}


def project(tmp_path, committed=None, local=None, name="proj"):
    """A project folder and a profile folder; a document of None writes no file."""
    target = tmp_path / "proj"
    (target / "docs" / "squad").mkdir(parents=True)
    profile = tmp_path / "profiles"
    profile.mkdir()
    if committed is not None:
        (target / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(committed))
    if local is not None:
        (profile / f"{name}.slack.local.yaml").write_text(yaml.safe_dump(local))
    return target, profile


def check(target, profile, capsys, *extra):
    rc = cli.main(["slack", "check", "--target", str(target), "--profile", str(profile), "--project", "proj", *extra])
    return rc, capsys.readouterr().out


def test_a_valid_pair_exits_zero(tmp_path, capsys):
    target, profile = project(tmp_path, COMMITTED, LOCAL)
    rc, out = check(target, profile, capsys)
    assert rc == 0
    assert "ok" in out


def test_a_committed_defect_names_the_file_and_the_key(tmp_path, capsys):
    bad = {**COMMITTED, "personas": [{**COMMITTED["personas"][0], "voice": "Nobody"}]}
    target, profile = project(tmp_path, bad, LOCAL)
    rc, out = check(target, profile, capsys)
    assert rc == 1
    assert "slack.yaml" in out and "personas[0].voice" in out


def test_a_local_defect_names_the_file_and_the_key(tmp_path, capsys):
    bad = {**LOCAL, "workspaces": [{**LOCAL["workspaces"][0], "label": "xoxb-leak"}]}
    target, profile = project(tmp_path, COMMITTED, bad)
    rc, out = check(target, profile, capsys)
    assert rc == 1
    assert "slack.local.yaml" in out and "workspaces[0].label" in out


def test_a_guest_channel_in_an_owned_workspace_exits_zero(tmp_path, capsys):
    """A local channel row carries `class: guest` under an owned workspace id, and the check reports no defect (#2770)."""
    committed = {**COMMITTED, "workspaces": [{**COMMITTED["workspaces"][0],
                                              "channels": [{"id": "C0PROJ0001", "name": "proj", "mode": "listen"}]}]}
    local = {"version": 1, "workspaces": [{"id": "T0TEAM0001", "label": "team",
                                           "channels": [{"id": "C0PROJ0001", "class": "guest"}]}]}
    target, profile = project(tmp_path, committed, local)
    rc, out = check(target, profile, capsys)
    assert rc == 0
    assert "ok" in out


def test_no_file_is_not_a_defect(tmp_path, capsys):
    target, profile = project(tmp_path)
    rc, out = check(target, profile, capsys)
    assert rc == 0
    assert "ok" in out


def test_a_file_that_is_not_yaml_is_one_named_defect(tmp_path, capsys):
    target, profile = project(tmp_path, COMMITTED, LOCAL)
    (target / "docs" / "squad" / "slack.yaml").write_text("personas: [\n")
    rc, out = check(target, profile, capsys)
    assert rc == 1
    assert "slack.yaml" in out and "YAML" in out


def test_a_flag_names_the_project_file_in_the_profile(tmp_path, capsys):
    target, profile = project(tmp_path, COMMITTED, None, name="zesty")
    bad = {**LOCAL, "workspaces": [{**LOCAL["workspaces"][0], "label": "xapp-leak"}]}
    (profile / "zesty.slack.local.yaml").write_text(yaml.safe_dump(bad))
    rc = cli.main(["slack", "check", "--target", str(target), "--profile", str(profile), "--project", "zesty"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "zesty.slack.local.yaml" in out


def test_a_run_from_a_subdirectory_reads_the_project_root(tmp_path, capsys):
    bad = {**COMMITTED, "roles": [{"id": "manager", "deploy": ["prod"]}]}
    target, profile = project(tmp_path, bad, LOCAL)
    (target / ".git").mkdir()
    sub = target / "src" / "deep"
    sub.mkdir(parents=True)
    rc = cli.main(["slack", "check", "--target", str(sub), "--profile", str(profile), "--project", "proj"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "roles[0].deploy" in out
