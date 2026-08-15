#!/usr/bin/env python3
"""
SURROGATE Bridge — ntfy.sh relay for Hatch JARVIS.

The Pi subscribes to an ntfy command topic via streaming JSON.
When a command arrives, it executes it and posts the result
to a response topic. This lets JARVIS send commands to the Pi
even when the Pi is behind NAT/firewall — the Pi reaches OUT.

Architecture:
  JARVIS  --POST cmd-->  ntfy.sh cmd topic  --stream-->  Pi bridge
  JARVIS  <--poll rsp--  ntfy.sh rsp topic  <--POST---   Pi bridge
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# ntfy helpers (stdlib only — no extra deps)
# ---------------------------------------------------------------------------

NTFY_BASE = "https://ntfy.sh"


def publish(topic: str, payload: dict, timeout: float = 15):
    """Publish a JSON message to an ntfy topic."""
    url = f"{NTFY_BASE}/{topic}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except Exception as e:
        log.error("Failed to publish to %s: %s", topic, e)
        return None


def stream_subscribe(topic: str, callback, stop_event: threading.Event):
    """
    Subscribe to an ntfy topic via JSON streaming.
    Calls callback(msg_dict) for every incoming message.
    Reconnects automatically on failure.
    """
    url = f"{NTFY_BASE}/{topic}/json"
    backoff = 1
    last_id = None

    while not stop_event.is_set():
        try:
            # Use 'since' to avoid replaying old messages on reconnect
            stream_url = url
            if last_id:
                stream_url = f"{url}?since={last_id}"
            else:
                # Only get new messages (not cached history)
                stream_url = f"{url}?since=all&poll=1"
                # First, drain any stale messages
                try:
                    drain_req = urllib.request.Request(stream_url)
                    with urllib.request.urlopen(drain_req, timeout=5) as resp:
                        for line in resp:
                            line = line.decode().strip()
                            if line:
                                try:
                                    msg = json.loads(line)
                                    if msg.get("id"):
                                        last_id = msg["id"]
                                except json.JSONDecodeError:
                                    pass
                except Exception:
                    pass

                # Now subscribe for new messages only
                stream_url = f"{url}?since=all"

            log.info("Connecting to ntfy stream: %s", topic)
            req = urllib.request.Request(stream_url)
            resp = urllib.request.urlopen(req, timeout=90)

            backoff = 1  # reset on successful connect

            # Read lines as they arrive
            while not stop_event.is_set():
                line = resp.readline()
                if not line:
                    # Connection closed by server — reconnect
                    log.warning("Stream connection closed, reconnecting...")
                    break
                line = line.decode().strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Track message ID for reconnect
                if msg.get("id"):
                    last_id = msg["id"]

                # Only process actual messages (skip keepalives / open events)
                if msg.get("event", "message") != "message":
                    continue

                if "message" not in msg:
                    continue

                try:
                    callback(msg)
                except Exception as e:
                    log.exception("Error processing message: %s", e)

        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if stop_event.is_set():
                break
            log.warning("Stream error: %s — retrying in %ds", e, backoff)
            stop_event.wait(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def execute_command(cmd: str, timeout: int = 300) -> dict:
    """Execute a shell command and return result dict."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out", "code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1}


# ---------------------------------------------------------------------------
# Main bridge loop
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    bridge_cfg = config.get("bridge", {})

    cmd_topic = bridge_cfg.get("cmd_topic")
    rsp_topic = bridge_cfg.get("rsp_topic")
    api_key = bridge_cfg.get("api_key", "")

    if not cmd_topic or not rsp_topic:
        log.error("Bridge not configured — set bridge.cmd_topic and bridge.rsp_topic in config.yaml")
        sys.exit(1)

    log.info("SURROGATE Bridge starting")
    log.info("  Command topic:  %s", cmd_topic)
    log.info("  Response topic: %s", rsp_topic)

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        log.info("Shutting down bridge...")
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    def on_command(msg):
        """Process an incoming command message from ntfy."""
        try:
            # The ntfy message body is a JSON string inside msg["message"]
            payload = json.loads(msg["message"])
        except (json.JSONDecodeError, KeyError):
            log.warning("Invalid command message: %s", msg)
            return

        cmd_id = payload.get("id", "unknown")
        cmd_type = payload.get("type", "exec")
        auth = payload.get("api_key", "")

        # Verify API key if configured
        if api_key and auth != api_key:
            log.warning("Unauthorized command (id=%s)", cmd_id)
            publish(rsp_topic, {
                "id": cmd_id,
                "error": "unauthorized",
                "code": -1,
            })
            return

        if cmd_type == "exec":
            command = payload.get("command", "")
            timeout = payload.get("timeout", 300)
            log.info("Executing command (id=%s): %s", cmd_id, command[:100])

            result = execute_command(command, timeout)
            result["id"] = cmd_id

            log.info("Command result (id=%s): code=%s, stdout=%d bytes",
                     cmd_id, result["code"], len(result.get("stdout", "")))

            publish(rsp_topic, result)

        elif cmd_type == "ping":
            log.info("Ping received (id=%s)", cmd_id)
            publish(rsp_topic, {"id": cmd_id, "pong": True, "code": 0})

        elif cmd_type == "speak":
            # Future: speak text through TTS
            text = payload.get("text", "")
            log.info("Speak request (id=%s): %s", cmd_id, text[:100])
            # For now, execute piper + aplay
            result = execute_command(
                f'echo "{text}" | /home/jarvis/surrogate/venv/bin/piper '
                f'--model /home/jarvis/surrogate/models/piper/voice.onnx '
                f'--output_file /tmp/speak.wav && '
                f'aplay -D plughw:0,0 -q /tmp/speak.wav',
                timeout=30,
            )
            result["id"] = cmd_id
            publish(rsp_topic, result)

        else:
            log.warning("Unknown command type: %s (id=%s)", cmd_type, cmd_id)
            publish(rsp_topic, {
                "id": cmd_id,
                "error": f"unknown command type: {cmd_type}",
                "code": -1,
            })

    # Subscribe and process commands
    stream_subscribe(cmd_topic, on_command, stop_event)
    log.info("Bridge shut down cleanly")


if __name__ == "__main__":
    main()
