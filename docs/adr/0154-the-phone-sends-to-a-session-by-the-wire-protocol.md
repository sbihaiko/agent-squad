# ADR-0154: The phone sends to a session by the wire protocol

## Status

Accepted.

## Context

The phone reads a live Claude Code session through the hub (ADR-0152). A person on the phone also needs to send one message to that session. The session accepts a message from another local process over its messaging socket. The protocol is not a public API and a Claude Code update can change it; it is documented in `docs/design/session-wire-protocol.md`.

A message sent this way reaches the session as a message from another session. A default-mode or plan-mode session runs the tools its own allow rules permit, so a phone message has the authority of any other local session of the same user.

## Decision

1. **The hub writes the protocol itself.** It sends the auth line and the message line over the session's socket. No Claude client code runs. Guard: `tests/test_session_send.py`.
2. **Sending is off until the profile sets it.** `tailnet.terminals_send` is a `bool` in the project file, false by default. The grant holds for every allowed login of the profile; no login has its own exception. The `tailnet` block stays outside the resume hash (ADR-0152, rule 6). Guard: `tests/test_tailnet_settings.py`, `tests/test_session_send.py`.
3. **A tailnet send needs an allowed tailnet login, the tailnet Origin, and a JSON body; a loopback send needs the hub's own Origin and a JSON body** (ADR-0152 rule 9). The body has exactly three fields: the session id, the text and a client message id. The hub builds the envelope and the sender name. Guard: `tests/test_session_send.py`.
4. **The recorded process is the only target.** At send time the hub finds the session again, and the socket peer pid must be the recorded pid. The hub never filters a session by its permission mode, because the record holds none. Guard: `tests/test_session_send.py`.
5. **A send is reserved before it is written.** One exclusive file per profile, session and client message id is created before any byte is written. A failure to create it refuses the send. A reservation that never reached a final state reads as uncertain, and a repeated id sends nothing. Reservations are kept 24 hours. Guard: `tests/test_session_send.py`.
6. **Limits.** A message is at most 4,000 characters. The socket deadline is 5 s. Guard: `tests/test_session_send.py`.
7. **A send has three outcomes.** Sent: the session socket accepted the message. Uncertain: the send was interrupted. Refused: the send failed. The protocol answers nothing after a send, so the hub claims no held, delivered or expired state. Guard: `tests/test_session_send.py`, `tests/test_hub_mobile_terminals.py`.
8. **The sending surface says what the message is.** The overlay's composer states that the message arrives as a message from another session, never as the owner's typed input or approval. The phone page carries no composer. Guard: `tests/test_hub_overlay.py`.

## Consequences

A message from the phone has the authority of any local session of the same user, bounded by that session's own allow rules. The owner accepts that authority for default and plan mode.

A protocol change in Claude Code can break the send. The send then refuses or reads as uncertain; it does not fall back to another path.
