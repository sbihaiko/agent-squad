# Session wire protocol (cross-session messages)

How a plain local process delivers one message to a live Claude Code session, so the message arrives as from another session. Read from Claude Code 2.1.312 on macOS by the #834 probe. The protocol is not a public API; a Claude Code update can change it. `peerProtocol: 1` in the record names this version.

Probe: `contrib/squad-goal/scripts/probe-session-send.py`.

## Session record

- Folder: `~/.claude/sessions/`.
- `<pid>.json`: one record for each live session. Fields used: `pid`, `sessionId`, `messagingSocketPath` (for example `/tmp/cc-socks/<pid>.sock`), `procStart`, `status` (`busy`, `idle`), `name`, `kind`, `entrypoint`, `peerProtocol`, `peerFeatures`, `pidDomain`.
- `<pid>.<sha256-hex>.key`: the inbox key, mode 0600. The hex is the SHA-256 of the absolute socket path (`path.resolve`, no symlink resolution). The file is JSON: `peerToken` (32 hex chars, 16 random bytes), `procStart`, `pidDomain`. Never print, log or commit `peerToken`.
- A session started with `CLAUDE_CODE_CHILD_SESSION` in its environment writes a key but no `<pid>.json`. Start a target with a clean environment.

## Find and verify the peer

1. Read every `<digits>.json`; select the record with the given `sessionId`.
2. Make sure the recorded `pid` is alive.
3. Read the key file named from `pid` and `messagingSocketPath`. Refuse a key file that others can read.
4. Connect an `AF_UNIX` stream socket to `messagingSocketPath`.
5. Read the socket peer pid (macOS: `getsockopt(SOL_LOCAL=0, LOCAL_PEERPID=2)`; Linux: `SO_PEERCRED`). Refuse to write when it is not the recorded `pid`. Claude's own client also refuses a peer of another uid, and a peer whose start time differs from `procStart` (a recycled pid).

## Framing

UTF-8, one JSON object per line, each line ends with `\n`. The client writes its lines, half-closes, and waits for the server to close. The inbox reads no reply frame on this path. Claude's own client sets a 5 s deadline. The inbox closes a connection that sends no complete line in 30 s, and a line over its line cap.

Line 1, auth:

```json
{"type":"auth","token":"<peerToken, redacted>"}
```

Line 2, the message:

```json
{"type":"user","message":{"role":"user","content":"<text>"},
 "from_name":"squad-hub","msg_id":"<uuid>","priority":"next"}
```

- `message.content` must be a non-empty string.
- `priority`: `next` (default) queues it; `now` processes it at once.
- `from`: optional reply address, shape `uds:<socket path>`. A process with no inbox omits it; the envelope then shows `from: unknown`.
- `from_name`: optional display name. A claim, not an identity.
- `from_mode`: optional sender permission mode.
- `msg_id`: optional; recorded in the message origin.
- `type: "control"` lines (`rename`, `notify_when_idle`, `peer_message_status`, and others) exist; the hub does not need them.

## Envelope and sender

The session queues the text as a meta user turn. The transcript (`~/.claude/projects/<cwd-slug>/<sessionId>.jsonl`) records it with `isMeta: true` and `origin: {kind: "peer", from, verifiedPeerPid, msg_id}`. `verifiedPeerPid` is the sending process pid, read by the server from the socket. The model sees "Another Claude session sent a message:" followed by the text and the peer-safety note. Other transcripts show it as `<cross-session-message from="..." from-name="..." from-mode="...">`.

## Permission mode and holds

The receiver can hold a peer message for its user's approval. Hold causes: a `crossSessionInbound: "hold"` setting (explicit, managed, repo), `mode-mismatch` (the sender asserts another permission class), and `no-mode-asserted` (the sender asserts no mode while the receiver bypasses permissions). A held message waits for approval; it can expire, be refused or be dropped. A hub that sends to a session in `bypassPermissions` must expect a hold unless it asserts a matching `from_mode`. The probe target ran in auto mode with no `from_mode`, and nothing was held.

## Auth on macOS

On macOS the inbox runs with auth optional. The CLI logs "peers will send unauthenticated (accepted: auth is optional on this platform)". A connection with a wrong token, or no auth line, still delivers. A good token marks the sender class `peer`. Send the key anyway: auth can become required (the CLI says "auth line REQUIRED" for an inbox that requires it), and then an unauthenticated line closes the connection.

## Errors

| Case | Result |
|---|---|
| No record for the session id | probe: `no session record` |
| Recorded pid gone | `ProcessLookupError` |
| Key missing or bad | probe: `key file unreadable` |
| Socket peer pid differs | probe refuses to write |
| No socket file | `FileNotFoundError` (no inbox) |
| Over 5 s | probe: `deadline of 5 s passed` |
| Auth required, bad token | server closes the connection, message dropped |
| Over the rate limit or line cap | server drops it |

## Evidence (#834 probe)

- Target: a disposable interactive session (Haiku), started with a clean environment in `/tmp/probe834` under a detached `screen`, killed after the probe. No other session was addressed.
- `probe-session-send.py <sessionId> "PROBE-834: reply with the single word PONG834."` printed `sent msg_id=...`.
- The session screen showed "Another Claude session sent a message: PROBE-834 ..." and the reply `PONG834`.
- The transcript entry: `type: user`, `isMeta: true`, `origin: {kind: peer, from: unknown, verifiedPeerPid: <probe pid>, msg_id: <the sent msg_id>}`.
- A second connection with a 32-zero token and no `from` was also delivered: auth is not enforced on macOS.
- Token values were never printed; only field names and lengths (`peerToken` 32, `procStart` 24, `pidDomain` 6).
- Side effect: the trust prompt for `/tmp/probe834` was accepted, which adds that folder to the Claude user config.
