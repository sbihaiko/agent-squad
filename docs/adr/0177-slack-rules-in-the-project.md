# ADR-0177: A project commits its Slack rules and the profile keeps what cannot be public

## Status

Accepted. This ADR replaces decisions 3 and 5 of [ADR-0154](0154-the-owner-talks-to-the-operator-through-slack.md), and it
restricts decision 2 of ADR-0154 to `owned` Slack workspaces. It keeps decisions 1, 4 and 6 of ADR-0154.

## Context

ADR-0154 gives the operator one Slack conversation, in one dedicated workspace. A project needs its own rules: several
Slack workspaces, several channels, a class per workspace, a
persona, and roles. A workspace of the team can hold the agent-squad app, and a workspace of a client cannot. A public
repository never holds a client team id, a client channel id, or a user id. A role is a grant, so it belongs to the record
of the project, where a person reviews its commit.

## Decision

1. **Two files, one public and one local.** A project commits `docs/squad/slack.yaml`, beside the project settings file. The
   profile folder holds `<project>.slack.local.yaml`, where the hub keeps the project's Slack use today, and that file never
   enters a repository. The local file adds a `guest` workspace, a `guest` channel, and the members of a role; the merge of
   the two files never lets it widen the actions of a role, change a persona, or remove the authorship mark.
2. **A workspace has a class, and a channel may take one.** `owned` installs the agent-squad app, and `voiced` opens one
   Socket Mode connection per app. `guest` installs no bot, and a scheduled session reads the channel through the
   claude.ai connector. A channel row in the local file may carry `class: guest`, and the channel's class wins over its
   workspace's class, so the owner tests the guest path in an owned test workspace. That channel takes no bot transport:
   `voiced` posts nothing in it, though the bot of its workspace is a member, and the guest poller reads it through the
   connector. The class decides the transport, and the committed file names no channel class.
3. **The policy module has one home.** The rules of both files and the decision for one event live in
   `squad/slackpolicy.py`. A process that reads or writes a Slack file imports it: `squad` for `squad slack check`, and
   the hub for the settings it writes. `voiced` (`contrib/voice/voiced.py`) takes the voice and the language from the
   same module when it reads a Slack file. That file reads no Slack file today: it holds every Slack call in the
   `channels` module it imports, and it names no policy module. It imports this module on the day it reads a file, so the
   voice and the language never disagree with the hub. No other file holds a copy of a rule. The module is pure: the caller passes the
   clock and the network answer, and the module reads neither.
4. **A defect names its key.** The validator returns one message per failing key, and the message names the file that
   holds the key and the key path. `squad slack check` reads both files at the project root, also from a subdirectory,
   reads each other project's local file on the machine, prints each message, and exits non-zero when a key fails. A file
   the reader cannot open is one defect, never a silent skip. A person repairs the key that the message names.
5. **A token never enters a file.** The validator refuses a value in token format (`xoxb-`, `xapp-`, `xoxp-`, `tskey-`) in
   every string of both files, the `tone` included, and in every other project's local file on the machine. An app names
   the Keychain entries of its two tokens, and no key is named `secret`, in any of those files. The keyword refusal of the
   settings rule holds for a key name, never for the free text of a `tone`.
6. **A persona has a safe name, a Gemini voice and a bounded tone.** The `name` holds only ASCII letters, digits, space,
   period, hyphen and underscore, with at most 32 characters, so no name breaks the mark line or injects Slack formatting.
   The `voice` is one of the Gemini TTS voices, and the `tone` has at most 300 characters. Both files of the project hold
   their personas, roles and languages to these rules.
7. **A language is one of three tags.** The value is a BCP 47 tag among `pt-BR`, `en-US` and `es-ES` after normalization:
   the reader maps `pt_br` and `pt-br` to `pt-BR`, and `es` to `es-ES`. The validator refuses any other tag.
8. **`prod` stays with the owner.** A role declares its deploy environments, and the validator refuses `prod` in any role.
   A deploy to production is never a role's standing grant.
9. **A client id stays out of the committed file.** The validator refuses a team id or a channel id in `slack.yaml` when the
   local file declares it as `guest`. The connector reaches one guest Slack workspace at a time, summed over every project
   on the machine, so the validator refuses every guest workspace after the first and names the file that holds it. A
   workspace that holds a guest channel counts as a guest workspace for this bound. A commit
   guard refuses any file whose basename ends in `slack.local.yaml` inside a repository, in either case of the suffix: the
   name decides, so the file never enters a tree at all. The guard is armed by a tracked step, never by hand alone:
   `squad setup --apply` runs `contrib/squad-goal/scripts/install-hooks.sh` in the repository it prepares, and that script
   installs the guard's `pre-commit` hook. After a clone, that one command makes a commit of a local file fail.
10. **Slack is the wake path in an `owned` workspace.** The session never asks Slack for a message of an `owned` workspace:
    a Monitor on the inbox wakes it. Polling exists only in a `guest` workspace, where the connector is the only transport.
11. **A role is the owner's written grant.** A role in the committed file grants the actions it lists, in an `owned` channel,
    and it never grants another owner-only action. The user ids of each role live in the local file.

## Consequences

A project carries its Slack rules in its own history, and a client's ids and its people stay on the machine. A person who
edits a file gets one message per broken key, so a broken edit is repaired without a search. A mistaken token in an edit is
refused before it reaches a remote.

The validator holds the format rules alone. A reader that needs the effective language, persona and roles of one channel
gets them from the same module, so a second copy of a rule never disagrees with this one.

## Guard

`tests/test_slack_policy.py`, `tests/test_cli_slack_check.py`, `tests/test_hub_slack_events.py` (the editor of the
project events and of each channel's `announce` list), `tests/test_setup.py` (the tracked step that arms the
guard), `contrib/squad-goal/scripts/test-commit-guard.sh`.
