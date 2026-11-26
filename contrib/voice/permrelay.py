"""The permission relay of a full turn (ADR-0180, SPEC 2964).

The CLI calls `mcp__squadrelay__approve` for each permission a full turn needs. The tool records one request in the state
folder and waits for the owner's answer. `voiced` posts each request to the owner's DM and reads the owner's reply with
`decide`. A request with no answer after ten minutes is denied. Every request, approval, denial and timeout writes one
audit line that holds no tool input.

A bounded session's tool gate asks the same relay for a call its profile denies (#2972): `call` opens the request and
waits for the answer, and a denial, a timeout or a full sender returns one short reason for the member's thread.

usage: permrelay.py serve --state DIR --owner ID --dm CHANNEL --workspace ID --sender ID --folder PATH
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager

TIMEOUT_SECONDS = 600
MAX_OPEN_PER_SENDER = 3
POLL_SECONDS = 1
SERVER_NAME = "squadrelay"
TOOL_NAME = "approve"
PROMPT_TOOL = f"mcp__{SERVER_NAME}__{TOOL_NAME}"
STATE_FILE = "relay.json"
LOCK_FILE = "relay.lock"
AUDIT_FILE = "audit.jsonl"
DENIED_MESSAGE = "The owner did not approve this call."
TIMEOUT_MESSAGE = "The owner did not answer in time, so the call was denied."
BUSY_MESSAGE = "The relay holds three open requests for this session, so the call was denied."


def call_hash(tool_name, tool_input, cwd):
    body = json.dumps({"tool": tool_name, "input": tool_input, "cwd": cwd}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def describe(tool_name, tool_input, cwd, digest):
    return (f"Permission request from the project session.\n"
            f"Tool: {tool_name}\n"
            f"Input: {json.dumps(tool_input, sort_keys=True)}\n"
            f"Folder: {cwd}\n"
            f"Reply `approve {digest}` or `deny {digest}`. No answer in {TIMEOUT_SECONDS // 60} minutes is a denial.")


def parse_answer(text):
    match = re.fullmatch(r"(approve|deny) ([0-9a-f]{64})", (text or "").strip())
    return (match.group(1), match.group(2)) if match else None


def verdict_payload(verdict, tool_input):
    if verdict == "allow":
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny", "message": DENIED_MESSAGE}


class Relay:
    def __init__(self, directory, owner, dm_channel, workspace, now=time.time, timeout=TIMEOUT_SECONDS):
        self.directory = str(directory)
        self.owner = owner
        self.dm_channel = dm_channel
        self.workspace = workspace
        self.now = now
        self.timeout = timeout
        self.state_path = os.path.join(self.directory, STATE_FILE)
        self.audit_path = os.path.join(self.directory, AUDIT_FILE)
        os.makedirs(self.directory, exist_ok=True)   # a bounded session's hook opens its request before any daemon pass

    @contextmanager
    def _locked(self):
        with open(os.path.join(self.directory, LOCK_FILE), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, rows):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, sort_keys=True)
        os.replace(tmp, self.state_path)

    def _audit(self, event, **fields):
        row = {"time": int(self.now()), "event": event, **fields}
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def _settle(self, rows):
        changed = False
        for rid, row in rows.items():
            if row["status"] == "open" and self.now() - row["created"] >= self.timeout:
                row["status"] = "timeout"
                self._audit("timeout", id=rid, hash=row["hash"], sender=row["sender"], tool=row["tool"])
                changed = True
        return changed

    def request(self, sender, tool_name, tool_input, cwd):
        digest = call_hash(tool_name, tool_input, cwd)
        with self._locked():
            rows = self._load()
            self._settle(rows)
            open_count = sum(1 for row in rows.values() if row["sender"] == sender and row["status"] == "open")
            if open_count >= MAX_OPEN_PER_SENDER:
                self._audit("refused", sender=sender, tool=tool_name, hash=digest)
                self._save(rows)
                return {"status": "refused", "hash": digest}
            rid = uuid.uuid4().hex
            rows[rid] = {"hash": digest, "sender": sender, "tool": tool_name, "status": "open",
                         "created": self.now(), "sent": False, "channel": self.dm_channel,
                         "workspace": self.workspace,
                         "text": describe(tool_name, tool_input, cwd, digest)}
            self._save(rows)
            self._audit("request", id=rid, hash=digest, sender=sender, tool=tool_name)
        return {"status": "pending", "id": rid, "hash": digest}

    def unsent(self):
        with self._locked():
            rows = self._load()
            if self._settle(rows):
                self._save(rows)
        return [dict(row, id=rid) for rid, row in rows.items() if row["status"] == "open" and not row["sent"]]

    def mark_sent(self, rid):
        with self._locked():
            rows = self._load()
            if rid in rows:
                rows[rid]["sent"] = True
                self._save(rows)

    def abandon(self, rid, reason):
        """Deny a request the daemon cannot post: no DM reaches the owner, so no answer can ever arrive."""
        with self._locked():
            rows = self._load()
            row = rows.get(rid)
            if not row or row["status"] != "open":
                return False
            row["status"] = "denied"
            self._save(rows)
            self._audit("undeliverable", id=rid, hash=row["hash"], sender=row["sender"], tool=row["tool"],
                        reason=reason)
        return True

    def open_requests(self):
        """Every open request of the relay, posted or not, so a reader never misses one an earlier turn marked sent."""
        with self._locked():
            rows = self._load()
            if self._settle(rows):
                self._save(rows)
        return [dict(row, id=rid) for rid, row in rows.items() if row["status"] == "open"]

    def decide(self, user, channel, workspace, verb, digest):
        """The owner's answer to one request: `approved`, `denied`, `none` or `refused`.

        The workspace the answer names must be the workspace that asked for the request: one state folder holds the
        requests of every full turn, so the row carries its own workspace (#2970 review)."""
        if user != self.owner or channel != self.dm_channel or verb not in ("approve", "deny"):
            self._audit("refused_answer", hash=digest)
            return "refused"
        with self._locked():
            rows = self._load()
            self._settle(rows)
            named = [(row["created"], rid) for rid, row in rows.items()
                     if row["status"] == "open" and row["hash"] == digest]
            # A bounded session's request records the workspace of its channel. A session with no known workspace
            # records none, and the owner's DM answer in any workspace answers it (#2972).
            open_rows = [(created, rid) for created, rid in named
                         if rows[rid].get("workspace") in (workspace, "")]
            if named and not open_rows:
                self._save(rows)
                self._audit("refused_answer", hash=digest)
                return "refused"
            if not open_rows:
                self._save(rows)
                return "none"
            _, rid = min(open_rows)
            row = rows[rid]
            approved = verb == "approve"
            row["status"] = "approved" if approved else "denied"
            self._save(rows)
            self._audit("approval" if approved else "denial", id=rid, hash=digest, sender=row["sender"],
                        tool=row["tool"])
        return "approved" if approved else "denied"

    def verdict(self, rid):
        """The answer the waiting call gets: `pending`, `allow` or `deny`.

        An approval is one shot: the reading that consumes it allows the call, and every later reading of the same
        request denies. A `used`, `denied`, `timeout` or missing request denies."""
        with self._locked():
            rows = self._load()
            changed = self._settle(rows)
            row = rows.get(rid)
            status = row["status"] if row else "denied"
            was_approved = status == "approved"
            if was_approved:
                row["status"] = "used"
                changed = True
            if changed:
                self._save(rows)
        if status == "open":
            return "pending"
        return "allow" if was_approved else "deny"

    def status(self, rid):
        """The status of one request: `open`, `approved`, `denied`, `timeout`, `used`, or `missing`."""
        with self._locked():
            rows = self._load()
            if self._settle(rows):
                self._save(rows)
        row = rows.get(rid)
        return row["status"] if row else "missing"

    def call(self, sender, tool_name, tool_input, cwd, poll=POLL_SECONDS, sleep=time.sleep):
        """One denied call of a bounded session (#2972): open one request, then wait for the owner's answer.

        The answer is `{"verdict": "allow"|"deny", "reason": ..., "id": ..., "hash": ...}`. An approval carries no
        reason and allows the call once. A denial, a timeout and a sender with three open requests carry one short
        reason, which the session gives the member in the thread. A refusal by the sender cap opens no request, so
        `id` is None."""
        req = self.request(sender, tool_name, tool_input, cwd)
        if req["status"] == "refused":
            return {"verdict": "deny", "reason": BUSY_MESSAGE, "id": None, "hash": req["hash"]}
        verdict = self.verdict(req["id"])
        while verdict == "pending":
            sleep(poll)
            verdict = self.verdict(req["id"])
        if verdict == "allow":
            return {"verdict": "allow", "reason": "", "id": req["id"], "hash": req["hash"]}
        reason = TIMEOUT_MESSAGE if self.status(req["id"]) == "timeout" else DENIED_MESSAGE
        return {"verdict": "deny", "reason": reason, "id": req["id"], "hash": req["hash"]}


def mcp_config(python, script, state, owner, dm, workspace, folder, timeout=None):
    args = [script, "serve", "--state", state, "--owner", owner, "--dm", dm, "--workspace", workspace,
            "--folder", folder]
    if timeout is not None:
        args += ["--timeout", str(timeout)]
    return {"mcpServers": {SERVER_NAME: {"command": python, "args": args}}}


def tool_list():
    schema = {"type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"},
                                               "tool_use_id": {"type": "string"}},
              "required": ["tool_name", "input"]}
    return {"tools": [{"name": TOOL_NAME, "description": "Asks the owner in Slack before a permission is used.",
                       "inputSchema": schema}]}


def handle_rpc(relay, folder, message, poll=POLL_SECONDS):
    method = message.get("method")
    rid = message.get("id")
    if method == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER_NAME, "version": "1"}}
    elif method == "tools/list":
        result = tool_list()
    elif method == "tools/call":
        args = message["params"]["arguments"]
        tool_input = args["input"]
        req = relay.request(relay.owner, args["tool_name"], tool_input, folder)
        if req["status"] == "refused":
            verdict = "deny"
        else:
            verdict = relay.verdict(req["id"])
            while verdict == "pending":
                time.sleep(poll)
                verdict = relay.verdict(req["id"])
        payload = verdict_payload(verdict, tool_input)
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    else:
        return None
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve(relay, folder, stdin=sys.stdin, stdout=sys.stdout, poll=POLL_SECONDS):
    for line in stdin:
        if not line.strip():
            continue
        response = handle_rpc(relay, folder, json.loads(line), poll)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="The permission relay's MCP server for one full turn.")
    sub = parser.add_subparsers(dest="command", required=True)
    serving = sub.add_parser("serve")
    for name in ("state", "owner", "dm", "workspace", "folder"):
        serving.add_argument(f"--{name}", required=True)
    serving.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    relay = Relay(args.state, owner=args.owner, dm_channel=args.dm, workspace=args.workspace, timeout=args.timeout)
    serve(relay, args.folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
