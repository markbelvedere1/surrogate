#!/usr/bin/env python3
"""
SURROGATE Remote Command API
Allows Hatch JARVIS to execute commands on the Pi.
Serves on localhost — exposed via Tailscale Funnel when available.
"""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

API_KEY = os.environ.get("SURROGATE_KEY", "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # suppress access logs

    def _send_json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()

    def do_GET(self):
        self._send_json(200, {"status": "ok", "service": "surrogate-remote"})

    def do_POST(self):
        if self.headers.get("X-API-Key") != API_KEY:
            return self._send_json(401, {"error": "unauthorized"})

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        cmd = body.get("command") or body.get("cmd", "")

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=300,
            )
            self._send_json(200, {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "code": result.returncode,
            })
        except subprocess.TimeoutExpired:
            self._send_json(200, {"error": "timeout", "code": -1})
        except Exception as e:
            self._send_json(200, {"error": str(e), "code": -1})


def main():
    port = int(os.environ.get("SURROGATE_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"SURROGATE Remote API listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
