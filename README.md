# SURROGATE — JARVIS Voice Terminal

A Raspberry Pi 5 voice assistant that extends Hatch JARVIS into the physical world.

## Hardware
- Raspberry Pi 5 (8GB)
- Anker PowerConf S330 USB speakerphone (mic + speaker)
- MicroSD card with Raspberry Pi OS Lite (64-bit)

## Architecture
```
  [You] → "Hey JARVIS" wake word → Record speech → Whisper STT
                                                        ↓
                                                  Process query
                                                        ↓
                                            Piper TTS → Speaker
```

## Components
| Component | Implementation | Notes |
|-----------|---------------|-------|
| Wake Word | OpenWakeWord — built-in "hey jarvis" model | Open source, no API key |
| Speech-to-Text | faster-whisper (base model, int8) | Runs locally on CPU |
| Text-to-Speech | Piper TTS (en_US-lessac-medium) | Runs locally |
| Remote API | Python HTTP server on port 8080 via Tailscale Funnel | For Hatch access |

## Setup

### Prerequisites
- Raspberry Pi OS Lite 64-bit
- Tailscale installed and Funnel enabled
- Anker PowerConf S330 plugged into USB

### Install
```bash
cd ~/surrogate
./install.sh
```

No API keys required — all components are fully open source.

### Configure
Edit `config.yaml` to adjust wake word sensitivity, audio device, or Whisper model size.

### Run
```bash
# Manual
cd ~/surrogate && . venv/bin/activate && python3 main.py

# Or via systemd (starts on boot)
sudo systemctl start surrogate
sudo systemctl status surrogate
```

### Remote API
The remote API runs on port 8080 and is exposed via Tailscale Funnel.
It allows Hatch JARVIS to execute commands on the Pi remotely.

```bash
# Check status
sudo systemctl status surrogate-remote

# Test
curl -X POST https://raspberrypi.tailadbc7b.ts.net/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SURROGATE_KEY" \
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
├── config.yaml          # Configuration (audio, model settings)
├── install.sh           # One-command setup
├── deploy.sh            # Update & restart services
├── requirements.txt     # Python dependencies
├── models/
│   └── piper/
│       ├── voice.onnx       # Piper TTS model
│       └── voice.onnx.json  # Piper TTS config
└── venv/                # Python virtual environment
```

## Deployment
To deploy updated code to the Pi:
```bash
# From the Pi
cd ~/surrogate && bash deploy.sh

# Or remotely via Hatch
# (uses the remote API to push files and run deploy.sh)
```

## Future
- **Hatch bridge**: forward transcribed queries to Hatch JARVIS, speak the response
- **Proactive alerts**: Hatch pushes notifications → Pi speaks them
- **Exercise logging**: say "JARVIS, log 85 pushups" and it logs to the workout sheet
- **Multi-room**: additional Pi units in different rooms
