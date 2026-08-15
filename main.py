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

import numpy as np
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
    """Initialize OpenWakeWord detector with 'hey jarvis' model."""
    import importlib.resources
    from openwakeword.model import Model

    oww_cfg = config.get("wake_word", {})
    model_name = oww_cfg.get("model", "hey_jarvis")
    threshold = oww_cfg.get("threshold", 0.5)
    enable_speex = oww_cfg.get("enable_speex", True)

    log.info("Initializing OpenWakeWord (model=%s, threshold=%.2f)...", model_name, threshold)

    # Find bundled model file (e.g. hey_jarvis_v0.1.onnx)
    import openwakeword
    oww_pkg_dir = os.path.dirname(openwakeword.__file__)
    models_dir = os.path.join(oww_pkg_dir, "resources", "models")
    model_path = None
    for fname in os.listdir(models_dir):
        if fname.startswith(model_name) and fname.endswith(".onnx"):
            model_path = os.path.join(models_dir, fname)
            break

    if not model_path:
        log.error("OpenWakeWord model '%s' not found in %s", model_name, models_dir)
        sys.exit(1)

    log.info("Using model file: %s", model_path)
    model = Model(
        wakeword_model_paths=[model_path],
        enable_speex_noise_suppression=enable_speex,
    )

    log.info("Wake word detector ready (model=%s)", model_name)
    return model, threshold


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
    oww_model, oww_threshold = init_wake_word(config)
    whisper_model = init_whisper(config)

    # Audio capture settings for wake word detection
    # OpenWakeWord needs 16kHz 16-bit mono PCM, in 80ms frames (1280 samples)
    import subprocess

    SAMPLE_RATE = 16000
    FRAME_MS = 80
    FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)  # 1280 samples
    FRAME_BYTES = FRAME_SAMPLES * 2  # 16-bit = 2 bytes per sample
    device = audio_cfg.get("device", "plughw:0,0")

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

    # Start continuous audio capture for wake word detection
    log.info("Listening for wake word 'Hey JARVIS'...")
    mic_proc = subprocess.Popen(
        [
            "arecord",
            "-D", device,
            "-f", "S16_LE",
            "-r", str(SAMPLE_RATE),
            "-c", "1",
            "-t", "raw",
            "-q",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    try:
        while running:
            data = mic_proc.stdout.read(FRAME_BYTES)
            if not data or len(data) < FRAME_BYTES:
                break

            # Convert to int16 numpy array for OpenWakeWord
            audio_frame = np.frombuffer(data, dtype=np.int16)

            # Get wake word prediction
            prediction = oww_model.predict(audio_frame)

            # Check if any model scored above threshold
            for model_name, score in prediction.items():
                if score > oww_threshold:
                    log.info(">>> Wake word detected! (model=%s, score=%.3f)", model_name, score)

                    # Stop mic capture for wake word
                    mic_proc.terminate()
                    mic_proc.wait()

                    # Reset the model's internal state
                    oww_model.reset()

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
                        device=device,
                    )

                    if wav_path is None:
                        log.warning("No speech detected")
                    else:
                        # Transcribe
                        log.info("Transcribing...")
                        text = transcribe(whisper_model, wav_path)
                        os.unlink(wav_path)

                        if not text:
                            log.warning("Empty transcription")
                        else:
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

                    # Resume listening — restart mic capture
                    play_wav(ready_beep_path)
                    log.info("Listening for wake word 'Hey JARVIS'...")
                    mic_proc = subprocess.Popen(
                        [
                            "arecord",
                            "-D", device,
                            "-f", "S16_LE",
                            "-r", str(SAMPLE_RATE),
                            "-c", "1",
                            "-t", "raw",
                            "-q",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    break  # break inner for-loop, continue outer while

    except KeyboardInterrupt:
        pass
    finally:
        mic_proc.terminate()
        mic_proc.wait()
        # Clean up temp files
        for p in [beep_path, ready_beep_path]:
            try:
                os.unlink(p)
            except OSError:
                pass
        log.info("SURROGATE shut down cleanly")


if __name__ == "__main__":
    main()
