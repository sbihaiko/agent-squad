"""Walks the owner through the voice workspace, app and Keychain secrets. Reads no value."""
import sys

from keychain import BOT_TOKEN, APP_TOKEN, GEMINI_KEY, has_secret

MANIFEST = "contrib/voice/slack-app-manifest.json"


def store_command(name: str) -> str:
    return f'security add-generic-password -a "$USER" -s {name} -w'


def main() -> int:
    print("Voice setup. The owner completes each step; the agent creates no workspace, app or token.")
    print()
    print("1. Create a workspace in Slack, dedicated to this conversation. Not the company workspace.")
    print(f"2. Create the app from the manifest {MANIFEST}: api.slack.com/apps, Create New App, From a manifest.")
    print("3. Install the app to the workspace.")
    secrets = (
        ("4. Store the bot token (OAuth & Permissions, starts with xoxb-).", BOT_TOKEN),
        ("5. Store the app-level token (Basic Information, App-Level Tokens, scope connections:write, starts with xapp-).", APP_TOKEN),
        ("6. Store the Gemini API key.", GEMINI_KEY),
    )
    missing = 0
    for step, name in secrets:
        print(step)
        print(f"     {store_command(name)}")
        present = has_secret(name)
        print(f"     {name}: {'present' if present else 'missing'}")
        missing += not present
    print()
    if missing:
        print(f"{missing} Keychain entr{'y' if missing == 1 else 'ies'} missing.")
        return 1
    print("All Keychain entries present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
