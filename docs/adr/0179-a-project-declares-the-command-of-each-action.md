# ADR-0179: A project declares the command of each action, and the executor runs it outside the session

## Status

Accepted.

## Context

The operator session answers a Slack message and never runs a command. A role may grant an action, and the action
must run somewhere. Two risks follow. A command built from the text of a message lets a sender run anything the
session can run. A command run inside the session inherits the session's kernel bound and its write grant.

The roles already hold a closed action vocabulary, and `squad/slackexecutor.py` already checks the sender, the role,
the grant and the arguments against the policy decision.

## Decision

1. **The request is one outbox line.** A message that asks for an action makes the session append one request line to
   the outbox of the line's channel: the reserved `kind` (`action-request`), the epoch second it writes the line, the
   action id, the arguments, the sender, the channel and the inbox line id. The reserved `kind` is the sole
   discriminator, so an answer line that carries a request's fields by accident is still an answer. The outbox is the
   one path the session's kernel bound lets it write, so the request travels with the answers.
2. **The executor runs outside the session.** `squad/slackactions.py` runs in the daemon's process, which is outside
   every operator session and outside the session's sandbox. It reads the request, asks `squad/slackpolicy.decide` for
   the sender's decision, and hands the request to `squad/slackexecutor.execute`, which checks it whole: the sender, the
   role, the request's action against the actions the role grants, the arguments against the ones the action takes, and
   the deploy environment against the role's deploy list. The decision grants the role's whole action set, never one
   action a message names, so the text of a message adds no grant of its own. The pass reads
   only the outbox of a channel a project owns and never the DM outbox, so a line in any other outbox never runs. A
   request older than a small window is stale and never runs, so a line a crashed pass left behind cannot act on a
   channel long after its message. The line's `at` is the session's number, so the daemon reads it against its own
   clock: a line more than a small tolerance in the future never runs. The daemon keeps its own time of every reading
   of an outbox, and every pass renews it, a pass that finds no new byte included, so a quiet outbox never ages the
   requests that follow. A line the daemon finds past a place it read longer than the window ago has no age the daemon
   can vouch for, so it never runs, and neither does a line past a kept place that carries no time of its reading. A
   first read and a read of a rotated outbox take a bounded tail, and each writes one audit line that names the byte
   range it did not read and the count, so no request is dropped without a record.
3. **The command comes from the project, never from a message.** The committed `slack.yaml` declares one command per
   action id under `project.actions`, and the project identity declares the deploy command. The executor builds no
   command from message text, no argument of a request reaches a command line, and the declared command runs as one argv
   with no shell, so a declared value that names `;`, `|`, `$(` or a backtick is refused. Every declared action id gets its
   command source, so a declared request action runs its command too. An action with no declared command has no source,
   and the executor refuses it by name. A declared value that is not one argv keeps its source and its reason, and the
   executor refuses the action as an invalid source, so the refusal never reads as a missing command.
4. **The action vocabulary stays closed.** `project.actions` accepts only the ids the role vocabulary names, so a
   project never declares an owner-only action. The deploy list of the role bounds the environment, and the project
   identity's own environment bounds it too: a request that names another environment is refused. Production stays with
   the owner.
5. **Every run is recorded and answered.** One reply leaves in the thread of the message, naming the grant when the
   request is refused. The audit holds one line per run and one line per refused request, in the same file, carrying the
   time, the sender, the role, the channel, the action, the exit code and one error that explains the exit (an empty
   error on a success). A request is run once, by a ledger beside the state, and the ledger's key is the parsed request
   (the action, the arguments, the sender, the channel and the source), never the raw line, so a line with another field
   never runs the same request twice. The thread a reply reaches is no part of the key: the ledger records the reply and
   the thread it reached, so two lines that differ only in their thread run the command once and each thread still gets
   its reply. A reply that cannot be written is recorded in the audit and kept for the next pass, so the reply still
   leaves and the command never runs again. A kept reply goes only to the outbox of a channel a project owns; one whose
   channel a project no longer names waits for the window from its own time, then is dropped, and each such refusal is
   one log line. The ledger line carries the audit row's fields before the run, and every audit row carries the key,
   so a crash that cuts a run short still gets its row from the next pass, naming the unfinished command.

## Consequences

A sender's text never becomes a command, and a command never runs with the session's permissions. A project that
wants an action answers with one reviewed line in its own history. A person reads every action run in one audit file.

## Guard

`tests/test_slack_actions.py`, `tests/test_slack_policy.py`, `tests/test_voiced.py`.
