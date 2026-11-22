#!/usr/bin/env python3
"""A stdio MCP server that stands in for the claude.ai Slack connector in the real-CLI probe (#2716).

It names the connector's read tool and its two write tools, and it reaches no network and no Slack. Every call it runs
answers one text line, `FAKE-SLACK-CALLED <json>`, with the tool name and the arguments it received. So the probe reads
from the session's own events whether a call reached the connector, and with which arguments: a call that a hook denied
never reaches this server and never carries the line.

usage: fake_slack_mcp.py   (the CLI starts it from --mcp-config; it speaks newline-delimited JSON-RPC on stdin/stdout)
"""
import json
import sys

CALLED = "FAKE-SLACK-CALLED"
TOOLS = ("slack_read_channel", "slack_send_message", "slack_send_message_draft")
SCHEMA = {"type": "object", "additionalProperties": True,
          "properties": {"channel": {"type": "string"}, "channel_id": {"type": "string"},
                         "thread_ts": {"type": "string"}, "text": {"type": "string"},
                         "oldest": {"type": "string"}, "limit": {"type": "integer"}}}


def answer(request):
    """The JSON-RPC answer to one request, or None for a notification."""
    method = request.get("method")
    if "id" not in request:
        return None
    rid = request["id"]
    if method == "initialize":
        version = (request.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": version, "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-slack", "version": "1"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        tools = [{"name": name, "description": f"Probe stand-in for the Slack connector's {name}.",
                  "inputSchema": SCHEMA} for name in TOOLS]
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
    if method == "tools/call":
        params = request.get("params") or {}
        line = json.dumps({"tool": params.get("name"), "arguments": params.get("arguments") or {}}, sort_keys=True)
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"{CALLED} {line}"}]}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no method {method}"}}


def main(stdin=sys.stdin, stdout=sys.stdout):
    for raw in stdin:
        try:
            request = json.loads(raw)
        except ValueError:
            continue
        reply = answer(request) if isinstance(request, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
