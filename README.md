# SURROGATE — JARVIS Voice Terminal

A Raspberry Pi 5 voice assistant that extends Hatch JARVIS into the physical world.
Say **"Hey JARVIS"** and it listens, transcribes, responds, and speaks.

## Hardware
- Raspberry Pi 5 (8GB)
- Anker PowerConf S330 USB speakerphone (mic + speaker)
- MicroSD card with Raspberry Pi OS Lite (64-bit)

## Architecture

```
                        ┌────────────┐
  "Hey JARVIS" ──────►  │  Pi: main  │ ──► Whisper STT ──► Process ──► Piper TTS ──► Speaker
                        └────────────┘

  ┌──────────┐   POST cmd    ┌───────────┐   stream    ┌──────────────┐
  │  JARVIS  │ ────────────► │  ntfy.sh  │ ──────────► │  Pi: bridge  │ ──► execute
  │  (Hatch) │ ◄──────────── │  (relay)  │ ◄────────── │              │ ◄── result
  └──────────┘   poll rsp    └───────────┘   POST rsp  └──────────────┘
```

**Key insight:** The Pi reaches OUT to ntfy.sh (a free pub/sub relay),
so no inbound ports, VPN, or Tailscale Funnel needed. Works behind any NAT.

## Components

| Component | Implementation | Notes |
|-----------|---------------|-------|
| Wake Word | OpenWakeWord — built-in "hey_jarvis" | Open source, no API key |
| Speech-to-Text | faster-whisper (base model, int8) | Runs locally on CPU |
| Text-to-Speech | Piper TTS (en_US-lessac-medium) | Runs locally |
| Command Bridge | ntfy.sh pub/sub relay | Pi polls; JARVIS pushes |
| Remote API | Python HTTP server on port 8080 | Local access (Tailscale optional) |

## Quick Start

### First Install
```bash
cd ~/surrogate
./install.sh
```

### Deploy Updates
Updates auto-deploy via cron (`auto-update.sh` runs every 5 min).
Manual deploy: `bash ~/surrogate/deploy.sh`

### Configuration
Edit `config.yaml`. Key settings:
- `bridge.cmd_topic` / `bridge.rsp_topic` — ntfy.sh topic names (pre-configured)
- `bridge.api_key` — shared secret for command auth
- `wake_word.threshold` — detection sensitivity (0-1)

No external API keys required — all components are fully open source.

## Services

| Service | Description | Logs |
|---------|-------------|------|
| `surrogate` | Voice pipeline (wake word → STT → TTS) | `journalctl -u surrogate -f` |
| `surrogate-bridge` | ntfy.sh command relay | `journalctl -u surrogate-bridge -f` |
| `surrogate-remote` | Local HTTP API (port 8080) | `journalctl -u surrogate-remote -f` |

```bash
# Status
sudo systemctl status surrogate surrogate-bridge surrogate-remote

# Start/stop
sudo systemctl start surrogate
sudo systemctl stop surrogate
```

## File Structure
```
~/surrogate/
├── main.py              # Voice pipeline: wake word → STT → response → TTS
├── bridge.py            # ntfy.sh command relay (Pi ↔ JARVIS)
├── audio_utils.py       # Audio recording, playback, TTS helpers
├── remote.py            # Local HTTP command API
├── config.yaml          # All configuration
├── deploy.sh            # Update deps & restart services
├── install.sh           # First-time setup
├── auto-update.sh       # Git pull + auto-deploy (cron)
├── requirements.txt     # Python dependencies
├── models/
│   └── piper/
│       ├── voice.onnx       # Piper TTS model
│       └── voice.onnx.json  # Piper TTS config
└── venv/                # Python virtual environment

~/surrogate-git/         # Git clone (auto-update pulls here)
```

## How the Bridge Works

1. JARVIS posts a command to the ntfy **command topic**
2. The Pi's bridge service streams that topic and picks up the command
3. Bridge executes the command via subprocess
4. Bridge posts the result to the ntfy **response topic**
5. JARVIS polls the response topic for the result

All traffic is outbound HTTPS from the Pi — no firewall rules, port forwarding,
or VPN required. The ntfy.sh relay is free, open-source, and requires no signup.

### Command Types
- `exec` — run a shell command, return stdout/stderr/exit code
- `ping` — connectivity check
- `speak` — speak text through the Pi's speaker via Piper TTS

## Auto-Deployment

A cron job runs every 5 minutes:
1. `git pull` from `markbelvedere1/surrogate`
2. If code changed, copy to `~/surrogate/` and run `deploy.sh`
3. Services restart automatically with new code

Push to GitHub → wait 5 min → Pi is updated. No SSH needed.
