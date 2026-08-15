#!/usr/bin/env python3
"""Audio utilities for SURROGATE voice pipeline."""

import os
import subprocess
import tempfile
import wave
import numpy as np


def record_until_silence(
    sample_rate=16000,
    channels=1,
    silence_threshold=500,
    silence_duration=1.5,
    max_seconds=15,
    device="plughw:0,0",
):
    """Record audio from mic until silence is detected. Returns WAV file path."""
    chunk_duration = 0.1  # 100ms chunks
    chunk_samples = int(sample_rate * chunk_duration)
    silence_chunks_needed = int(silence_duration / chunk_duration)

    # Start arecord process
    proc = subprocess.Popen(
        [
            "arecord",
            "-D", device,
            "-f", "S16_LE",
            "-r", str(sample_rate),
            "-c", str(channels),
            "-t", "raw",
            "-q",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    frames = []
    silence_count = 0
    max_chunks = int(max_seconds / chunk_duration)
    speech_started = False

    try:
        for _ in range(max_chunks):
            data = proc.stdout.read(chunk_samples * 2 * channels)
            if not data:
                break
            frames.append(data)

            # Calculate RMS
            samples = np.frombuffer(data, dtype=np.int16)
            rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))

            if rms > silence_threshold:
                silence_count = 0
                speech_started = True
            else:
                silence_count += 1

            if speech_started and silence_count >= silence_chunks_needed:
                break
    finally:
        proc.terminate()
        proc.wait()

    if not frames:
        return None

    # Write to WAV
    audio_data = b"".join(frames)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data)
    return tmp.name


def play_wav(wav_path, device="plughw:0,0", lead_silence_ms=500):
    """Play a WAV file through the speaker with optional lead-in silence.
    
    USB speakers often drop the first few hundred ms while waking from
    low-power mode. Prepending a short silence avoids cutting off the start.
    """
    if lead_silence_ms > 0:
        # Generate and play a brief silence to wake the speaker
        silence_samples = int(22050 * lead_silence_ms / 1000)
        silence_data = (np.zeros(silence_samples) * 0).astype(np.int16)
        silence_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        with wave.open(silence_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(silence_data.tobytes())
        try:
            subprocess.run(
                ["aplay", "-D", device, "-q", silence_path],
                check=True, timeout=5,
            )
        except Exception:
            pass
        finally:
            os.unlink(silence_path)

    subprocess.run(
        ["aplay", "-D", device, "-q", wav_path],
        check=True,
        timeout=30,
    )


def text_to_speech(text, model_path, output_path=None):
    """Convert text to speech using Piper TTS. Returns path to WAV file."""
    import os
    import shutil

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        output_path = tmp.name

    # Find piper binary: check venv first, then system PATH
    venv_piper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "piper")
    piper_bin = venv_piper if os.path.isfile(venv_piper) else (shutil.which("piper") or "piper")

    subprocess.run(
        [piper_bin, "--model", model_path, "--output_file", output_path],
        input=text.encode(),
        check=True,
        capture_output=True,
        timeout=30,
    )
    return output_path


def generate_beep(frequency=880, duration=0.15, sample_rate=22050):
    """Generate a short beep WAV file. Returns path."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Apply envelope to avoid click
    envelope = np.ones_like(t)
    fade = int(sample_rate * 0.01)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[-fade:] = np.linspace(1, 0, fade)
    wave_data = (np.sin(2 * np.pi * frequency * t) * envelope * 16000).astype(np.int16)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(wave_data.tobytes())
    return tmp.name
