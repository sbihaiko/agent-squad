#!/usr/bin/env python3
"""Read one streamed Messages answer on stdin and print the CLI's JSON shape that review.sh reads (#1373).

The text, its usage and a subtype: success only when the stream ended with end_turn. A stream cut before its
message_stop, an error event, or a stop other than end_turn is an error answer, and it keeps the usage read so far.
"""
import json
import sys


def main():
    usage, parts, stop, subtype, done = {}, [], None, None, False
    for line in sys.stdin:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:])
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "message_start":
            usage.update(ev.get("message", {}).get("usage", {}))
        elif kind == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            parts.append(ev["delta"].get("text", ""))
        elif kind == "message_delta":
            stop = ev.get("delta", {}).get("stop_reason")
            usage.update(ev.get("usage", {}))
        elif kind == "message_stop":
            done = True
        elif kind == "error":
            subtype = "error_" + ev.get("error", {}).get("type", "unknown")
    if subtype is None:
        subtype = "error_stream" if not done else ("success" if stop == "end_turn" else "error_" + str(stop))
    json.dump({"result": "".join(parts), "usage": usage, "subtype": subtype, "is_error": subtype != "success"}, sys.stdout)


if __name__ == "__main__":
    main()
