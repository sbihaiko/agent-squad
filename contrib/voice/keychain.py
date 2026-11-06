"""The one reader for the voice secrets in the macOS Keychain."""
import subprocess

BOT_TOKEN = "agent-squad-voice-slack-bot-token"
APP_TOKEN = "agent-squad-voice-slack-app-token"
GEMINI_KEY = "agent-squad-voice-gemini-key"
ENTRIES = (BOT_TOKEN, APP_TOKEN, GEMINI_KEY)


class SecretMissing(Exception):
    pass


def has_secret(name: str) -> bool:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", name],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def read_secret(name: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", name, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SecretMissing(name)
    return result.stdout.rstrip("\n")
