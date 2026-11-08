"""Guards: the voice app manifest declares the spec's scopes and event, the setup wizard checks each Keychain entry without printing a value, and the secret reader returns a value without printing or logging it."""
import json
import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
MANIFEST = VOICE / "slack-app-manifest.json"
sys.path.insert(0, str(VOICE))

import keychain  # noqa: E402
import setup_wizard  # noqa: E402
import tts  # noqa: E402

from squad import skillpath  # noqa: E402

# The five pipeline scripts the repository carries so a probe can time this repo's copy (SKILL.md's contract).
VOICE_SCRIPTS = ["channels.py", "tts.py", "reply.py", "listen.py", "watch.py"]

SPEC_BOT_SCOPES = sorted([
    "chat:write",
    "channels:history",
    "channels:manage",
    "channels:read",
    "files:read",
    "files:write",
    "im:history",
    "im:read",
    "im:write",
    "users:read",
    "app_mentions:read",
])
SPEC_EVENTS = ["message.im", "app_mention", "message.channels"]
VALUE = "xoxb-FAKE-0123456789-NOT-A-REAL-TOKEN"

FAKE_SECURITY = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_SECURITY_LOG"
[ "$1" = "find-generic-password" ] || exit 2
name=""
want_value=0
while [ $# -gt 0 ]; do
  case "$1" in
    -s) name="$2"; shift ;;
    -w) want_value=1 ;;
  esac
  shift
done
entry="$FAKE_KEYCHAIN/$name"
if [ ! -f "$entry" ]; then
  echo "security: The specified item could not be found in the keychain." >&2
  exit 44
fi
if [ "$want_value" = "1" ]; then
  cat "$entry"
else
  echo "attributes: svce=\\"$name\\""
fi
exit 0
"""


@pytest.fixture
def fake_keychain(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    security = bin_dir / "security"
    security.write_text(FAKE_SECURITY)
    security.chmod(0o755)
    store = tmp_path / "keychain"
    store.mkdir()
    log = tmp_path / "security.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KEYCHAIN", str(store))
    monkeypatch.setenv("FAKE_SECURITY_LOG", str(log))

    def put(name: str, value: str = VALUE) -> None:
        (store / name).write_text(value + "\n")

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    return put, calls


def test_manifest_declares_exactly_the_spec_scopes_and_event():
    manifest = json.loads(MANIFEST.read_text())
    bot = manifest["oauth_config"]["scopes"]["bot"]
    assert sorted(bot) == SPEC_BOT_SCOPES
    assert len(bot) == len(set(bot))
    assert list(manifest["oauth_config"]["scopes"]) == ["bot"]
    assert manifest["settings"]["event_subscriptions"]["bot_events"] == SPEC_EVENTS
    assert manifest["settings"]["socket_mode_enabled"] is True
    # The messages tab is what lets the owner open a DM at all. A manifest carrying only bot_user is accepted by Slack,
    # the token answers auth.test and Socket Mode opens, and the owner still meets "Sending messages to this app has
    # been turned off" when he tries to write. The whole voice flow dies on a flag no scope names.
    app_home = manifest["features"]["app_home"]
    assert app_home["messages_tab_enabled"] is True, "the owner cannot open a DM without the messages tab"
    assert app_home["messages_tab_read_only_enabled"] is False, "a read-only tab refuses the owner's message"


def test_wizard_checks_each_entry_and_prints_no_value(fake_keychain, capsys):
    put, calls = fake_keychain
    put(keychain.BOT_TOKEN)

    rc = setup_wizard.main()

    out, err = capsys.readouterr()
    assert rc == 1
    for step in ("workspace", "manifest", "Install the app", "bot token", "app-level token", "Gemini"):
        assert step in out
    assert f"{keychain.BOT_TOKEN}: present" in out
    assert f"{keychain.APP_TOKEN}: missing" in out
    assert f"{keychain.GEMINI_KEY}: missing" in out
    assert VALUE not in out and VALUE not in err
    assert calls() and all("-w" not in line for line in calls())


def test_wizard_exits_zero_when_every_entry_is_present(fake_keychain, capsys):
    put, _ = fake_keychain
    for name in keychain.ENTRIES:
        put(name)

    assert setup_wizard.main() == 0
    assert VALUE not in capsys.readouterr().out


def test_reader_returns_value_without_printing_or_logging(fake_keychain, capsys, caplog):
    put, calls = fake_keychain
    put(keychain.BOT_TOKEN)

    with caplog.at_level(logging.DEBUG):
        value = keychain.read_secret(keychain.BOT_TOKEN)

    out, err = capsys.readouterr()
    assert value == VALUE
    assert VALUE not in out and VALUE not in err
    assert VALUE not in caplog.text
    assert calls()[-1].endswith("-w")


def test_reader_raises_naming_the_entry_when_missing(fake_keychain):
    with pytest.raises(keychain.SecretMissing, match=keychain.APP_TOKEN):
        keychain.read_secret(keychain.APP_TOKEN)


def global_skill(home: Path) -> Path:
    """A fake personal copy of the global skill, the fallback the resolver reads when the repo vendors nothing."""
    folder = home / ".claude" / "skills" / "slack-hal9k"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: slack-hal9k\n---\nv1\n")
    return folder


def test_each_script_resolves_to_the_vendored_copy_when_present(tmp_path):
    """A caller times the project's pipeline, so the copy beside keychain.py wins over the global skill's."""
    root = tmp_path / "repo"
    vendored = root / "contrib" / "voice"
    vendored.mkdir(parents=True)
    home = tmp_path / "home"
    skill = global_skill(home)
    for name in VOICE_SCRIPTS:
        (vendored / name).write_text(f"# vendored {name}\n")
        (skill / name).write_text(f"# global {name}\n")

    for name in VOICE_SCRIPTS:
        assert skillpath.voice_script(name, root=root, base=home, cwd=root, plugins=[]) == vendored / name


def test_a_script_the_repository_does_not_vendor_falls_back_to_the_global_skill(tmp_path):
    root = tmp_path / "repo"
    vendored = root / "contrib" / "voice"
    vendored.mkdir(parents=True)
    home = tmp_path / "home"
    skill = global_skill(home)
    (vendored / "channels.py").write_text("# vendored\n")
    (skill / "watch.py").write_text("# global\n")

    assert skillpath.voice_script("watch.py", root=root, base=home, cwd=root, plugins=[]) == skill / "watch.py"
    assert skillpath.voice_script("nope.py", root=root, base=home, cwd=root, plugins=[]) is None
    assert skillpath.voice_script("../keychain.py", root=root, base=home, cwd=root, plugins=[]) is None


def test_the_repository_resolves_each_script_from_contrib_voice():
    """The vendored pipeline is the one a caller in this repository gets, with no global skill in the way."""
    for name in VOICE_SCRIPTS:
        found = skillpath.voice_script(name)
        assert found == VOICE / name, f"{name} resolves to {found}"
        assert found.is_file()


def test_the_vendored_tts_reads_the_keychain_then_the_project_env_then_the_sibling(tmp_path, fake_keychain, monkeypatch):
    """One order, never a tracked file: the Keychain, the project's gitignored .env, the sibling project's .env."""
    put, _ = fake_keychain
    project = tmp_path / "project"
    project.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / ".env").write_text("GOOGLE_API_KEY=sibling-key\n")
    monkeypatch.chdir(project)

    assert tts.gemini_key("agent-squad", None, str(sibling)) == "sibling-key"
    (project / ".env").write_text("GOOGLE_API_KEY=project-key\n")
    assert tts.gemini_key("agent-squad", None, str(sibling)) == "project-key"
    put("agent-squad-gemini-key", "keychain-key")
    assert tts.gemini_key("agent-squad", None, str(sibling)) == "keychain-key"


def test_the_vendored_tts_never_reads_a_key_from_a_tracked_env(tmp_path, fake_keychain, monkeypatch):
    """A .env git tracks is a committed secret: the pipeline skips it and takes the sibling's, or nothing."""
    import subprocess

    _, _ = fake_keychain
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("GOOGLE_API_KEY=tracked-key\n")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", ".env"], check=True)
    monkeypatch.chdir(project)

    assert tts.gemini_key("agent-squad", None, None) is None
