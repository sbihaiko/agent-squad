# Session wire protocol (cross-session messages)

How a plain local process delivers one message to a live Claude Code session, so the message arrives as from another session. Read from Claude Code 2.1.312 on macOS by the #834 probe. The protocol is not a public API; a Claude Code update can change it. `peerProtocol: 1` in the record names this version.

Probe: `contrib/squad-goal/scripts/probe-session-send.py`.

## Session record

- Folder: `~/.claude/sessions/`.
- `<pid>.json`: one record for each live session. Fields used: `pid`, `sessionId`, `messagingSocketPath` (for example `/tmp/cc-socks/<pid>.sock`), `procStart`, `status` (`busy`, `idle`), `name`, `kind`, `entrypoint`, `peerProtocol`, `peerFeatures`, `pidDomain`.
- `<pid>.<sha256-hex>.key`: the inbox key, mode 0600. The hex is the SHA-256 of the absolute socket path (`path.resolve`, no symlink resolution). The file is JSON: `peerToken` (32 hex chars, 16 random bytes), `procStart`, `pidDomain`. Never print, log or commit `peerToken`.
- `version` is the Claude Code version string. `startedAt` is in milliseconds and is written a few seconds after the process starts.
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

The receiver can hold a peer message for its user's approval. Hold causes: a `crossSessionInbound: "hold"` setting (explicit, managed, repo), `mode-mismatch` (the sender asserts another permission class), and `no-mode-asserted` (the sender asserts no mode while the receiver bypasses permissions). A held message waits for approval; it can expire, be refused or be dropped. The web owner has no terminal to approve a hold, so the hub never sends a frame that can be held: every frame it writes carries a `from_mode` equal to the receiver's own mode.

The hub reads the receiver's mode live first: the last `permissionMode` line of its transcript, searched back through the file; then the mode its argv names. Only when that read answers nothing does the hub use its own durable record, `$SG_STATE/session-sends/<profile>/modes/<sessionId>.json`. The record holds the mode, and the `pid` and `startedAt` of the live registry record it was taken from. A record that names another pid or start belongs to another instance of the id: the hub drops it, and never answers with it. A live answer replaces the record, so a session cycled to another mode is never asserted with its old one. The hub writes the record when it first sees the session, and each later sighting or send marks it seen; a record no sighting or send has touched for 24 hours is purged with the send reservations.

The argv read alone is not reliable for an interactive session. Claude Code sets its process title to `claude` on the interactive path (unless `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` is set), and on macOS a title change replaces the argv that `ps -ww -o args=` shows, so a flag given at start may no longer be visible. A transcript tail can also grow past the mode line. The record answers for both, and a send it answers is never held.

When neither the live read nor the record answers, the mode is unknown. The hub then refuses the send, and the reason tells the web owner the next step: send once from the session's terminal, or start the session with `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` set. The panel shows that reason, and no frame the receiver would hold is written.

The status route (`/api/status`) names the build this hub loaded: the short SHA-256 and mtime of `hub.py`, `session_send.py` and `sessions.py`, and when the hub started. A process that predates an edit reports the old hashes there.

The probe target ran in auto mode with no `from_mode`, and nothing was held.

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

## Evidence (#834 hub send)

- Sender: the hub's own module, `session_send.send`, called in-process with a profile that matches only the target folder. The probe script was not used.
- Target: a disposable interactive session, started with `env -i` (HOME, PATH, TERM, USER only) in a `mktemp -d` folder under a Python pty, killed after the send. No other session was addressed.
- The first try was refused: the registry left out every real record, because it read an invented shape. Fixed in `sessions.py`; the test fixtures now use the real shape.
- After the fix: `send` returned `sent`; a repeat with the same client id returned `sent` and wrote nothing.
- The transcript entry: `type: user`, `origin: {kind: peer, msg_id: <the client id>}`.
- No token value was printed or logged. The folder, its transcript and its trust entry in the Claude user config were removed after.
