# SURROGATE — JARVIS Voice Terminal

A Raspberry Pi 5 voice assistant that extends Hatch JARVIS into the physical world.

## Hardware
- Raspberry Pi 5 (8GB)
- Anker PowerConf S330 USB speakerphone (mic + speaker)
- MicroSD card with Raspberry Pi OS Lite (64-bit)

## Architecture
```
  [You] → "JARVIS" wake word → Record speech → Whisper STT
                                                    ↓
                                              Process query
                                                    ↓
                                        Piper TTS → Speaker
```

## Components
| Component | Implementation | Runs |
|-----------|---------------|------|
| Wake Word | Porcupine (Picovoice) — built-in "jarvis" keyword | Local |
| Speech-to-Text | faster-whisper (base model, int8) | Local |
| Text-to-Speech | Piper TTS (en_US-lessac-medium) | Local |
| Remote API | Python HTTP server on port 8080 via Tailscale Funnel | Local |

## Setup

### Prerequisites
- Raspberry Pi OS Lite 64-bit
- Tailscale installed and Funnel enabled
- Anker PowerConf S330 plugged into USB

### 1. Get a Picovoice Access Key
1. Go to [console.picovoice.ai](https://console.picovoice.ai/)
2. Sign up for a free account
3. Copy your Access Key
4. Paste it into `config.yaml` under `picovoice_access_key`

### 2. Install
```bash
cd ~/surrogate
./install.sh
```

### 3. Configure
Edit `config.yaml` and set your `picovoice_access_key`.

### 4. Run
```bash
# Manual
cd ~/surrogate && . venv/bin/activate && python3 main.py

# Or via systemd (starts on boot)
sudo systemctl start surrogate
sudo systemctl status surrogate
```

### 5. Remote API
The remote API runs on port 8080 and is exposed via Tailscale Funnel.
It allows Hatch JARVIS to execute commands on the Pi remotely.

```bash
# Check status
sudo systemctl status surrogate-remote

# Test
curl -X POST https://raspberrypi.tailadbc7b.ts.net/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: jarvis-surrogate-2026" \
  -d '{"cmd": "hostname"}'
```

## Services
- `surrogate.service` — Main voice pipeline (wake word → STT → TTS)
- `surrogate-remote.service` — Remote command API (port 8080)

## File Structure
```
~/surrogate/
├── main.py              # Main voice pipeline loop
├── audio_utils.py       # Audio recording, playback, TTS helpers
├── remote.py            # Remote command API server
├── config.yaml          # Configuration (API keys, audio settings)
├── install.sh           # One-command setup script
├── requirements.txt     # Python dependencies
├── models/
│   └── piper/
│       ├── voice.onnx       # Piper TTS model
│       └── voice.onnx.json  # Piper TTS config
└── venv/                # Python virtual environment
```

## Future
- Hatch bridge: forward transcribed queries to Hatch JARVIS, speak the response
- Proactive alerts: Hatch pushes notifications → Pi speaks them
- Multi-room: additional Pi units in different rooms
