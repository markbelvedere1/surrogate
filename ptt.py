#!/usr/bin/env python3
"""
SURROGATE — Push-to-Talk Voice Terminal (v2: Two-Mode Button Interface)

Button behavior on the Anker PowerConf S330 play button:
  Quick press (< 1.5s) = Mailbox mode — deliver queued alerts
  Long press  (≥ 1.5s) = Talk mode — record, transcribe, send to Hatch, speak response

Audio cues:
  - Long press threshold crossed: sustained tone (660 Hz, 0.4s) — "you're in talk mode"
  - On release (talk mode): listen beep — "start speaking"
  - Short press release: double-beep — "checking mailbox"
"""

import json
import logging
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ptt] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ptt")

# Button config — Anker PowerConf S330 on /dev/input/event1
INPUT_DEVICE = "/dev/input/event1"
BUTTON_CODE = 164  # KEY_PLAYPAUSE
LONG_PRESS_THRESHOLD = 1.5  # seconds


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def init_whisper(config):
    """Initialize faster-whisper STT model."""
    from faster_whisper import WhisperModel

    whisper_cfg = config.get("whisper", {})
    model_name = whisper_cfg.get("model", "base")
    device = whisper_cfg.get("device", "cpu")
    compute_type = whisper_cfg.get("compute_type", "int8")

    log.info("Loading Whisper model '%s'...", model_name)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    log.info("Whisper model loaded")
    return model


def transcribe(model, wav_path):
    """Transcribe a WAV file using faster-whisper. Returns text."""
    segments, info = model.transcribe(wav_path, beam_size=3, language="en")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text


def process_query(text, config):
    """Send voice query to Hatch via ntfy and wait for spoken response."""
    import urllib.error
    import urllib.request
    import uuid

    hatch_cfg = config.get("hatch", {})
    bridge_cfg = config.get("bridge", {})

    if not hatch_cfg.get("enabled", False):
        return f"I heard you say: {text}"

    query_id = str(uuid.uuid4())[:8]
    rsp_topic = bridge_cfg.get("rsp_topic", "")
    cmd_topic = bridge_cfg.get("cmd_topic", "")

    if not rsp_topic or not cmd_topic:
        log.error("Bridge topics not configured")
        return "Sorry, I'm not fully configured yet."

    # Publish voice query to response topic (Hatch hook polls this)
    query_payload = json.dumps({
        "type": "voice_query",
        "id": query_id,
        "text": text,
    }).encode()

    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{rsp_topic}",
            data=query_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        log.info("Voice query published (id=%s)", query_id)
    except Exception as e:
        log.error("Failed to publish voice query: %s", e)
        return "Sorry, I couldn't reach my brain right now. Try again in a moment."

    # Wait for Hatch to respond via the command topic
    response_id = f"resp-{query_id}"
    max_wait = 90
    poll_interval = 2
    start_time = time.time()
    since = str(int(start_time))

    log.info("Waiting for Hatch response (id=%s)...", response_id)

    while time.time() - start_time < max_wait:
        time.sleep(poll_interval)
        try:
            poll_req = urllib.request.Request(
                f"https://ntfy.sh/{cmd_topic}/json?poll=1&since={since}"
            )
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                for line in resp:
                    line = line.decode().strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        if msg.get("event", "message") != "message":
                            continue
                        inner = json.loads(msg.get("message", "{}"))
                        if inner.get("id") == response_id and inner.get("type") == "speak":
                            response_text = inner.get("text", "")
                            log.info("Got Hatch response: %s", response_text[:80])
                            return response_text
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            log.debug("Poll error: %s", e)

    log.warning("Timed out waiting for Hatch response")
    return "Sorry, I'm taking too long to think. Try asking again."


def wait_for_button_action(device_path, key_code, long_press_threshold=1.5,
                            tone_path=None, play_device="plughw:0,0"):
    """Wait for button press and determine short vs long press.

    Measures time between key-down and key-up events. When the hold
    duration crosses long_press_threshold, plays a sustained tone so
    the user knows they've entered talk mode.

    Returns:
        "mailbox" for quick press  (< long_press_threshold)
        "talk"    for long press   (>= long_press_threshold)
    """
    fd = os.open(device_path, os.O_RDONLY)
    try:
        # Wait for key down (EV_KEY, value=1)
        while True:
            data = os.read(fd, 24)
            if len(data) < 24:
                continue
            _sec, _usec, ev_type, ev_code, ev_value = struct.unpack("llHHi", data)
            if ev_type == 1 and ev_code == key_code and ev_value == 1:
                break

        press_start = time.time()
        tone_played = False

        # Wait for key up, watching for threshold crossing
        while True:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                data = os.read(fd, 24)
                if len(data) == 24:
                    _sec, _usec, ev_type, ev_code, ev_value = struct.unpack(
                        "llHHi", data
                    )
                    # Key released (value=0) — ignore auto-repeat (value=2)
                    if ev_type == 1 and ev_code == key_code and ev_value == 0:
                        duration = time.time() - press_start
                        mode = "talk" if duration >= long_press_threshold else "mailbox"
                        log.info(
                            "Button held %.2fs → %s mode", duration, mode
                        )
                        return mode

            # Play sustained tone when threshold is crossed
            if not tone_played and time.time() - press_start >= long_press_threshold:
                tone_played = True
                log.info("Long-press threshold crossed — playing talk tone")
                if tone_path:
                    subprocess.Popen(
                        ["aplay", "-D", play_device, "-q", tone_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
    finally:
        os.close(fd)


def main():
    config = load_config()
    audio_cfg = config.get("audio", {})
    piper_cfg = config.get("piper", {})
    device = audio_cfg.get("device", "plughw:0,0")

    from audio_utils import (
        generate_beep,
        generate_double_beep,
        generate_sustained_tone,
        play_wav,
        record_until_silence,
        text_to_speech,
    )

    # Pre-generate audio cues
    listen_beep = generate_beep(frequency=880, duration=0.15)
    ready_beep = generate_beep(frequency=660, duration=0.1)
    error_beep = generate_beep(frequency=330, duration=0.3)
    mailbox_beep = generate_double_beep(frequency=880, duration=0.1, gap=0.08)
    talk_tone = generate_sustained_tone(frequency=660, duration=0.4)

    # Initialize Whisper
    whisper_model = init_whisper(config)

    # Graceful shutdown
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        log.info("Shutting down...")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Announce ready
    log.info("=== SURROGATE Push-to-Talk v2 online ===")
    log.info(
        "Quick press = mailbox | Long press (%.1fs) = talk",
        LONG_PRESS_THRESHOLD,
    )
    startup_wav = text_to_speech(
        "JARVIS online. Quick press for alerts. Long press to talk.",
        piper_cfg.get("model_path", "models/piper/voice.onnx"),
    )
    play_wav(startup_wav)
    os.unlink(startup_wav)

    log.info(
        "Waiting for button press on %s (code %d)...",
        INPUT_DEVICE,
        BUTTON_CODE,
    )

    while running:
        try:
            # Wait for button press and determine mode
            action = wait_for_button_action(
                INPUT_DEVICE,
                BUTTON_CODE,
                long_press_threshold=LONG_PRESS_THRESHOLD,
                tone_path=talk_tone,
                play_device=device,
            )

            if action == "mailbox":
                # ── Mailbox Mode ──────────────────────────────
                log.info("Mailbox mode (short press)")
                play_wav(mailbox_beep)

                # Stub: no alert queue yet — speak placeholder
                alert_wav = text_to_speech(
                    "No new alerts.",
                    piper_cfg.get("model_path", "models/piper/voice.onnx"),
                )
                play_wav(alert_wav)
                os.unlink(alert_wav)

                play_wav(ready_beep)
                log.info("Ready — waiting for button press...")

            elif action == "talk":
                # ── Talk Mode ─────────────────────────────────
                log.info("Talk mode (long press)")

                # Play listen beep to signal "start speaking"
                play_wav(listen_beep)

                # Record until silence
                log.info("Recording... (speak now)")
                wav_path = record_until_silence(
                    sample_rate=audio_cfg.get("sample_rate", 16000),
                    channels=audio_cfg.get("channels", 1),
                    silence_threshold=audio_cfg.get("silence_threshold", 500),
                    silence_duration=audio_cfg.get("silence_duration", 1.5),
                    max_seconds=audio_cfg.get("max_record_seconds", 15),
                    device=device,
                )

                if wav_path is None:
                    log.warning("No speech detected")
                    play_wav(error_beep)
                    log.info("Ready — waiting for button press...")
                    continue

                # Transcribe
                log.info("Transcribing...")
                text = transcribe(whisper_model, wav_path)
                os.unlink(wav_path)

                if not text:
                    log.warning("Empty transcription")
                    play_wav(error_beep)
                    log.info("Ready — waiting for button press...")
                    continue

                log.info("Heard: %s", text)

                # Speak acknowledgment
                ack_text = f"I heard: {text}. Working on it."
                log.info("Speaking acknowledgment: %s", ack_text)
                ack_wav = text_to_speech(
                    ack_text,
                    piper_cfg.get("model_path", "models/piper/voice.onnx"),
                )
                play_wav(ack_wav)
                os.unlink(ack_wav)

                # Process query and get response
                response = process_query(text, config)
                log.info("Response: %s", response)

                # Speak response
                response_wav = text_to_speech(
                    response,
                    piper_cfg.get("model_path", "models/piper/voice.onnx"),
                )
                play_wav(response_wav)
                os.unlink(response_wav)

                play_wav(ready_beep)
                log.info("Ready — waiting for button press...")

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("Error in main loop: %s", e, exc_info=True)
            time.sleep(1)

    # Cleanup pre-generated audio cues
    for p in [listen_beep, ready_beep, error_beep, mailbox_beep, talk_tone]:
        try:
            os.unlink(p)
        except OSError:
            pass
    log.info("SURROGATE shut down cleanly")


if __name__ == "__main__":
    main()
