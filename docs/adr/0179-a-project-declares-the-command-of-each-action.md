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
   the outbox of the line's channel: the action id, the arguments, the sender, the channel and the inbox line id. The
   outbox is the one path the session's kernel bound lets it write, so the request travels with the answers.
2. **The executor runs outside the session.** `squad/slackactions.py` runs in the daemon's process, which is outside
   every operator session and outside the session's sandbox. It reads the request, asks `squad/slackpolicy.decide` for
   the sender's decision, and hands the request to `squad/slackexecutor.execute`, which checks it whole.
3. **The command comes from the project, never from a message.** The committed `slack.yaml` declares one command per
   action id under `project.actions`, and the project identity declares the deploy command. The executor builds no
   command from message text, and no argument of a request reaches a command line. An action with no declared command
   has no source, and the executor refuses it by name.
4. **The action vocabulary stays closed.** `project.actions` accepts only the ids the role vocabulary names, so a
   project never declares an owner-only action. The deploy list of the role bounds the environment, and production
   stays with the owner.
5. **Every run is recorded and answered.** One reply leaves in the thread of the message, naming the grant when the
   request is refused. One audit line records the time, the sender, the role, the channel, the action and the exit
   code. A request is run once, by a ledger beside the state.

## Consequences

A sender's text never becomes a command, and a command never runs with the session's permissions. A project that
wants an action answers with one reviewed line in its own history. A person reads every action run in one audit file.

## Guard

`tests/test_slack_actions.py`, `tests/test_slack_policy.py`, `tests/test_voiced.py`.
