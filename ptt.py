#!/usr/bin/env python3
"""
SURROGATE — Push-to-Talk Voice Terminal
Button press -> Record -> Transcribe -> Send to Hatch -> Speak response

Uses the Anker PowerConf S330 mute/play button (evdev key code 164)
as the push-to-talk trigger. Press once to start recording, speak,
then silence detection ends the recording automatically.
"""

import json
import logging
import os
import signal
import sys
import tempfile
import threading
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
    max_wait = 45
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


def wait_for_button_press(device_path, key_code):
    """Block until the specified button is pressed (key down event)."""
    import struct

    with open(device_path, "rb") as f:
        while True:
            data = f.read(24)
            if len(data) < 24:
                continue
            _sec, _usec, ev_type, ev_code, ev_value = struct.unpack("llHHi", data)
            # type=1 is EV_KEY, value=1 is key down
            if ev_type == 1 and ev_code == key_code and ev_value == 1:
                return


def main():
    config = load_config()
    audio_cfg = config.get("audio", {})
    piper_cfg = config.get("piper", {})
    device = audio_cfg.get("device", "plughw:0,0")

    from audio_utils import (
        generate_beep,
        play_wav,
        record_until_silence,
        text_to_speech,
    )

    # Pre-generate beeps
    listen_beep = generate_beep(frequency=880, duration=0.15)
    ready_beep = generate_beep(frequency=660, duration=0.1)
    error_beep = generate_beep(frequency=330, duration=0.3)

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
    log.info("=== SURROGATE Push-to-Talk online ===")
    startup_wav = text_to_speech(
        "JARVIS online. Press the button to talk.",
        piper_cfg.get("model_path", "models/piper/voice.onnx"),
    )
    play_wav(startup_wav)
    os.unlink(startup_wav)

    log.info("Waiting for button press on %s (code %d)...", INPUT_DEVICE, BUTTON_CODE)

    while running:
        try:
            # Wait for button press
            wait_for_button_press(INPUT_DEVICE, BUTTON_CODE)
            log.info(">>> Button pressed!")

            # Play listen beep
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
                continue

            # Transcribe
            log.info("Transcribing...")
            text = transcribe(whisper_model, wav_path)
            os.unlink(wav_path)

            if not text:
                log.warning("Empty transcription")
                play_wav(error_beep)
                continue

            log.info("Heard: %s", text)

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

            # Ready for next press
            play_wav(ready_beep)
            log.info("Ready — waiting for button press...")

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("Error in main loop: %s", e, exc_info=True)
            time.sleep(1)

    # Cleanup
    for p in [listen_beep, ready_beep, error_beep]:
        try:
            os.unlink(p)
        except OSError:
            pass
    log.info("SURROGATE shut down cleanly")


if __name__ == "__main__":
    main()
