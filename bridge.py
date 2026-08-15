#!/usr/bin/env python3
"""
SURROGATE Bridge — ntfy.sh relay for Hatch JARVIS.

Simple polling approach: checks ntfy command topic every few seconds,
executes commands, posts results to response topic.

No external dependencies beyond stdlib + pyyaml (already installed).
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

NTFY_BASE = "https://ntfy.sh"

# Hardcoded config (avoids pyyaml dependency)
CMD_TOPIC = "surrogate-cmd-d47386fb4b228ce0"
RSP_TOPIC = "surrogate-rsp-d47386fb4b228ce0"
API_KEY = "jarvis-surrogate-2026"
POLL_INTERVAL = 10  # seconds (ntfy.sh free tier rate-limits at ~250 req/hr)


def ntfy_publish(topic, payload, timeout=15, retries=3):
    """Publish JSON payload to an ntfy topic with retry on rate-limit."""
    url = f"{NTFY_BASE}/{topic}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = (attempt + 1) * 5
                log.warning("Rate limited, retrying in %ds...", wait)
                time.sleep(wait)
            else:
                log.error("Publish failed: %s", e)
                return False
        except Exception as e:
            log.error("Publish failed: %s", e)
            return False
    return False


def ntfy_poll(topic, since="30s", timeout=10):
    """Poll ntfy topic for recent messages. Returns list of message dicts."""
    url = f"{NTFY_BASE}/{topic}/json?poll=1&since={since}"
    req = urllib.request.Request(url)
    messages = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode().strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("event", "message") == "message" and "message" in msg:
                        messages.append(msg)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.debug("Poll error (may be transient): %s", e)
    return messages


def execute_command(cmd, timeout=300):
    """Execute a shell command. Returns result dict."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"stdout": r.stdout, "stderr": r.stderr, "code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "timeout", "code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1}


def handle_command(payload):
    """Process a command payload and publish the result."""
    cmd_id = payload.get("id", "unknown")
    cmd_type = payload.get("type", "exec")
    auth = payload.get("api_key", "")

    if API_KEY and auth != API_KEY:
        log.warning("Unauthorized command (id=%s)", cmd_id)
        ntfy_publish(RSP_TOPIC, {"id": cmd_id, "error": "unauthorized", "code": -1})
        return

    if cmd_type == "exec":
        command = payload.get("command", "")
        log.info("exec (id=%s): %s", cmd_id, command[:100])
        result = execute_command(command, payload.get("timeout", 300))
        result["id"] = cmd_id
        ntfy_publish(RSP_TOPIC, result)

    elif cmd_type == "ping":
        log.info("ping (id=%s)", cmd_id)
        ntfy_publish(RSP_TOPIC, {"id": cmd_id, "pong": True, "code": 0})

    elif cmd_type == "speak":
        text = payload.get("text", "")
        log.info("speak (id=%s): %s", cmd_id, text[:80])
        # Use full path to venv piper binary
        piper_bin = "/home/jarvis/surrogate/venv/bin/piper"
        # Play 500ms silence first to wake USB speaker, then the actual speech
        result = execute_command(
            f'python3 -c "'
            f'import numpy as np, wave, tempfile; '
            f's=np.zeros(int(22050*0.5),dtype=np.int16); '
            f'p=tempfile.mktemp(suffix=\\".wav\\"); '
            f'w=wave.open(p,\\"wb\\"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); '
            f'w.writeframes(s.tobytes()); w.close(); '
            f'import subprocess; subprocess.run([\\"aplay\\",\\"-D\\",\\"plughw:0,0\\",\\"-q\\",p])'
            f'" && '
            f'echo {json.dumps(text)} | {piper_bin} '
            f'--model /home/jarvis/surrogate/models/piper/voice.onnx '
            f'--output_file /tmp/speak.wav 2>/dev/null && '
            f'aplay -D plughw:0,0 -q /tmp/speak.wav',
            timeout=30,
        )
        result["id"] = cmd_id
        ntfy_publish(RSP_TOPIC, result)

    else:
        log.warning("Unknown type: %s (id=%s)", cmd_type, cmd_id)
        ntfy_publish(RSP_TOPIC, {"id": cmd_id, "error": f"unknown: {cmd_type}", "code": -1})


def main():
    log.info("SURROGATE Bridge starting (poll mode)")
    log.info("  CMD topic: %s", CMD_TOPIC)
    log.info("  RSP topic: %s", RSP_TOPIC)
    log.info("  Poll interval: %ds", POLL_INTERVAL)

    # Announce startup
    ntfy_publish(RSP_TOPIC, {"id": "startup", "event": "bridge_online", "code": 0})

    running = True
    def handle_signal(signum, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Track processed message IDs to avoid duplicates
    seen_ids = set()
    # Start with current time to skip stale messages
    last_poll = str(int(time.time()))

    log.info("Bridge online — listening for commands")

    while running:
        try:
            messages = ntfy_poll(CMD_TOPIC, since=last_poll)
            for msg in messages:
                msg_id = msg.get("id", "")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)
                # Keep seen_ids from growing unbounded
                if len(seen_ids) > 1000:
                    seen_ids = set(list(seen_ids)[-500:])

                try:
                    payload = json.loads(msg["message"])
                    handle_command(payload)
                except (json.JSONDecodeError, KeyError) as e:
                    log.warning("Bad message: %s", e)

            # Update poll timestamp to avoid re-processing
            last_poll = str(int(time.time()) - POLL_INTERVAL)

        except Exception as e:
            log.error("Poll loop error: %s", e)

        time.sleep(POLL_INTERVAL)

    log.info("Bridge shut down")


if __name__ == "__main__":
    main()
