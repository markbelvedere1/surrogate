#!/usr/bin/env python3
"""
SURROGATE — JARVIS Voice Terminal
Main loop: Wake word -> Record -> Transcribe -> Respond -> Speak
"""

import logging
import os
import signal
import sys
import tempfile
import time

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("surrogate")


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def init_wake_word(config):
    """Initialize Porcupine wake word detector."""
    import pvporcupine
    from pvrecorder import PvRecorder

    access_key = config.get("picovoice_access_key", "") or os.environ.get(
        "PICOVOICE_ACCESS_KEY", ""
    )
    if not access_key:
        log.error(
            "Picovoice access key not set! Get a free key at https://console.picovoice.ai/"
        )
        log.error("Set it in config.yaml or as PICOVOICE_ACCESS_KEY env var")
        sys.exit(1)

    keyword = config.get("wake_word", "jarvis")
    porcupine = pvporcupine.create(
        access_key=access_key, keywords=[keyword]
    )
    recorder = PvRecorder(
        frame_length=porcupine.frame_length, device_index=-1
    )
    log.info(
        "Wake word detector ready (keyword=%r, device=%s)",
        keyword,
        recorder.selected_device,
    )
    return porcupine, recorder


def init_whisper(config):
    """Initialize faster-whisper STT model."""
    from faster_whisper import WhisperModel

    whisper_cfg = config.get("whisper", {})
    model_name = whisper_cfg.get("model", "base")
    device = whisper_cfg.get("device", "cpu")
    compute_type = whisper_cfg.get("compute_type", "int8")

    log.info("Loading Whisper model '%s' (this may take a moment)...", model_name)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    log.info("Whisper model loaded")
    return model


def transcribe(model, wav_path):
    """Transcribe a WAV file using faster-whisper. Returns text."""
    segments, info = model.transcribe(wav_path, beam_size=3, language="en")
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text


def process_query(text, config):
    """Process a voice query and return a response string.

    For now, this echoes back what was said. The Hatch bridge will replace this.
    """
    hatch_cfg = config.get("hatch", {})
    if hatch_cfg.get("enabled", False):
        # Future: send to Hatch JARVIS and get response
        pass

    # Echo mode — confirm we heard correctly
    return f"I heard you say: {text}"


def main():
    config = load_config()
    audio_cfg = config.get("audio", {})
    piper_cfg = config.get("piper", {})

    # Import audio utils
    from audio_utils import (
        generate_beep,
        play_wav,
        record_until_silence,
        text_to_speech,
    )

    # Pre-generate the acknowledgment beep
    beep_path = generate_beep(frequency=880, duration=0.15)
    ready_beep_path = generate_beep(frequency=660, duration=0.1)

    # Initialize components
    porcupine, recorder = init_wake_word(config)
    whisper_model = init_whisper(config)

    # Announce ready
    log.info("=== SURROGATE is online ===")
    startup_wav = text_to_speech(
        "JARVIS online. At your service.",
        piper_cfg.get("model_path", "models/piper/voice.onnx"),
    )
    play_wav(startup_wav)
    os.unlink(startup_wav)

    # Graceful shutdown
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        log.info("Shutting down...")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Main loop
    recorder.start()
    log.info("Listening for wake word 'JARVIS'...")

    try:
        while running:
            pcm = recorder.read()
            keyword_index = porcupine.process(pcm)

            if keyword_index >= 0:
                log.info(">>> Wake word detected!")
                recorder.stop()

                # Play acknowledgment beep
                play_wav(beep_path)

                # Record speech
                log.info("Recording... (speak now)")
                wav_path = record_until_silence(
                    sample_rate=audio_cfg.get("sample_rate", 16000),
                    channels=audio_cfg.get("channels", 1),
                    silence_threshold=audio_cfg.get("silence_threshold", 500),
                    silence_duration=audio_cfg.get("silence_duration", 1.5),
                    max_seconds=audio_cfg.get("max_record_seconds", 15),
                )

                if wav_path is None:
                    log.warning("No speech detected")
                    recorder.start()
                    continue

                # Transcribe
                log.info("Transcribing...")
                text = transcribe(whisper_model, wav_path)
                os.unlink(wav_path)

                if not text:
                    log.warning("Empty transcription")
                    recorder.start()
                    continue

                log.info("Heard: %s", text)

                # Process and respond
                response = process_query(text, config)
                log.info("Response: %s", response)

                # Speak response
                response_wav = text_to_speech(
                    response,
                    piper_cfg.get("model_path", "models/piper/voice.onnx"),
                )
                play_wav(response_wav)
                os.unlink(response_wav)

                # Resume listening
                play_wav(ready_beep_path)
                recorder.start()
                log.info("Listening for wake word 'JARVIS'...")

    except KeyboardInterrupt:
        pass
    finally:
        recorder.stop()
        porcupine.delete()
        # Clean up temp files
        for p in [beep_path, ready_beep_path]:
            try:
                os.unlink(p)
            except OSError:
                pass
        log.info("SURROGATE shut down cleanly")


if __name__ == "__main__":
    main()
