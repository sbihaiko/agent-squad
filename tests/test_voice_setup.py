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
])
SPEC_EVENTS = ["message.im"]
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
