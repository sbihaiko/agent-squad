#!/usr/bin/env python3
"""A loopback CONNECT proxy that relays one target and refuses every other (#1656).

The operator session reaches the model through HTTPS_PROXY=http://127.0.0.1:<port>, so its sandbox allows network out to
localhost only. The proxy runs outside the sandbox and relays only its one target, host and port; any other CONNECT is
answered 403 and recorded in `refusals` and in the log, so a denial is visible.

usage: model_proxy.py --allow HOST:PORT --scope SCOPE --refusals PATH
Prints `port <n>` once it listens, then serves until it is ended.
"""
import argparse
import json
import socket
import socketserver
import sys
import threading
import time


class ModelProxy:
    def __init__(self, allowed, log_path, scope=""):
        self.allowed = allowed
        self.log_path = log_path
        self.scope = scope
        self.refusals = []
        self._lock = threading.Lock()
        self._server = None

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        proxy = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                proxy.serve(self.rfile, self.wfile, self.connection)

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False

    def serve(self, rfile, wfile, client):
        request = rfile.readline().decode("latin-1").split()
        if not request:
            return
        while rfile.readline() not in (b"\r\n", b"\n", b""):
            continue
        if len(request) < 2 or request[0] != "CONNECT":
            self.refuse(" ".join(request), wfile, "405 Method Not Allowed")
            return
        target = request[1]
        host, _, port = target.rpartition(":")
        if (host, port) != (self.allowed[0], str(self.allowed[1])):
            self.refuse(target, wfile, "403 Forbidden")
            return
        try:
            upstream = socket.create_connection(self.allowed, timeout=10)
        except OSError:
            wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        wfile.flush()
        relay(client, upstream)
        upstream.close()

    def refuse(self, target, wfile, status):
        line = json.dumps({"at": time.strftime('%Y-%m-%dT%H:%M:%S'), "scope": self.scope, "target": target})
        with self._lock:
            self.refusals.append(target)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        wfile.write(f"HTTP/1.1 {status}\r\n\r\n".encode())
        wfile.flush()


def relay(a, b):
    def pump(src, dst):
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    back = threading.Thread(target=pump, args=(b, a), daemon=True)
    back.start()
    pump(a, b)
    back.join()


def main(argv=None):
    ap = argparse.ArgumentParser(description="a loopback model proxy for one operator session (#1658)")
    ap.add_argument("--allow", required=True, help="the one HOST:PORT the proxy relays")
    ap.add_argument("--scope", required=True)
    ap.add_argument("--refusals", required=True, help="the JSON-lines file each refusal is appended to")
    a = ap.parse_args(argv)
    host, _, port = a.allow.rpartition(":")
    with ModelProxy((host, int(port)), a.refusals, scope=a.scope) as proxy:
        print(f"port {proxy.port}", flush=True)
        threading.Event().wait()


if __name__ == "__main__":
    sys.exit(main())
