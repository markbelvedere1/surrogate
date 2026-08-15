#!/usr/bin/env python3
"""
SURROGATE Remote Command API
Allows Hatch JARVIS to execute commands on the Pi over Tailscale Funnel.
"""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

API_KEY = os.environ.get("SURROGATE_KEY", "")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default access logs
        pass

    def do_GET(self):
        """Health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "service": "surrogate-remote"}).encode())

    def do_POST(self):
        if self.headers.get("X-API-Key") != API_KEY:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        cmd = body.get("cmd", "")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=300
            )
            resp = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            resp = {"error": "timeout", "code": -1}
        except Exception as e:
            resp = {"error": str(e), "code": -1}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())


def main():
    port = int(os.environ.get("SURROGATE_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"SURROGATE Remote API listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
