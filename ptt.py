#!/usr/bin/env python3
"""
SURROGATE — Push-to-Talk Voice Terminal (v3: Double-Tap Button Interface)

Button behavior on the Anker PowerConf S330 play button (code 164):
  Single tap  = Mailbox mode — deliver queued alerts
  Double tap  = Talk mode — record, transcribe, send to Hatch, speak response

The Anker's capacitive touch buttons send key-down + key-up nearly
simultaneously (0.00s apart).  Long presses produce NO events at all.
So we detect taps (key-down events) and count them.

Audio cues:
  - Double tap detected: sustained tone (660 Hz, 0.4s) — "you're in talk mode"
  - After tone: listen beep — "start speaking"
  - Single tap: double-beep — "checking mailbox"
"""

import json
import logging
import os
import select
import signal
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
DOUBLE_TAP_WINDOW = 0.6  # seconds to wait for a second tap


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


def wait_for_button_action(device_path, key_code,
                           double_tap_window=0.6):
    """Wait for button tap(s) and determine single vs double tap.

    The Anker PowerConf S330's capacitive buttons send key-down and
    key-up events nearly simultaneously (0.00s hold time).  Long
    presses produce NO events at all.  So we count key-down events
    (taps) instead of measuring hold duration.

    Algorithm:
      1. Wait for first key-down (first tap).
      2. Wait up to *double_tap_window* seconds for a second key-down.
      3. Second tap within the window → "talk" (double-tap).
         No second tap → "mailbox" (single tap).

    Returns:
        "mailbox" for single tap
        "talk"    for double tap
    """
    from evdev import InputDevice, ecodes

    dev = InputDevice(device_path)
    try:
        # ── Drain stale/buffered events ──
        while True:
            r, _, _ = select.select([dev], [], [], 0)
            if r:
                for _ in dev.read():
                    pass
            else:
                break

        log.info("Waiting for button press on %s (code %d)...",
                 device_path, key_code)

        # ── Wait for first tap (key-down, value=1) ──
        while True:
            r, _, _ = select.select([dev], [], [], 1.0)
            if r:
                for event in dev.read():
                    if (event.type == ecodes.EV_KEY
                            and event.code == key_code
                            and event.value == 1):
                        log.debug("First tap detected")
                        # ── Wait for possible second tap ──
                        deadline = time.time() + double_tap_window
                        while time.time() < deadline:
                            remaining = deadline - time.time()
                            if remaining <= 0:
                                break
                            r2, _, _ = select.select(
                                [dev], [], [], min(remaining, 0.05)
                            )
                            if r2:
                                for ev2 in dev.read():
                                    if (ev2.type == ecodes.EV_KEY
                                            and ev2.code == key_code
                                            and ev2.value == 1):
                                        log.info(
                                            "Double tap detected → talk mode"
                                        )
                                        return "talk"
                        # Timeout — no second tap
                        log.info("Single tap detected → mailbox mode")
                        return "mailbox"
    finally:
        dev.close()


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
    log.info("=== SURROGATE Push-to-Talk v3 online ===")
    log.info("Single tap = mailbox | Double tap = talk")
    startup_wav = text_to_speech(
        "JARVIS online. Single tap for alerts. Double tap to talk.",
        piper_cfg.get("model_path", "models/piper/voice.onnx"),
    )
    play_wav(startup_wav)
    os.unlink(startup_wav)

    while running:
        try:
            # Wait for button tap(s) and determine mode
            action = wait_for_button_action(
                INPUT_DEVICE,
                BUTTON_CODE,
                double_tap_window=DOUBLE_TAP_WINDOW,
            )

            if action == "mailbox":
                # ── Mailbox Mode ──────────────────────────────
                log.info("Mailbox mode (single tap)")
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
                log.info("Talk mode (double tap)")

                # Play sustained tone to confirm talk mode
                play_wav(talk_tone)

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
