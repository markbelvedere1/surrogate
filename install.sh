#!/bin/bash
# SURROGATE install script
# Run: cd ~/surrogate && ./install.sh

set -e

echo "=== SURROGATE Installer ==="

# System packages
echo "[1/6] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq alsa-utils python3-pip python3-venv python3-dev git \
    libportaudio2 libsndfile1 ffmpeg libspeexdsp-dev > /dev/null 2>&1

# Python venv
echo "[2/6] Setting up Python environment..."
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Download OpenWakeWord models
echo "[3/6] Downloading OpenWakeWord models..."
python3 -c "
import openwakeword
openwakeword.utils.download_models()
print('Models downloaded')
"

# Piper voice model
echo "[4/6] Downloading Piper TTS voice model..."
mkdir -p models/piper
if [ ! -f models/piper/voice.onnx ]; then
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx -O models/piper/voice.onnx
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json -O models/piper/voice.onnx.json
fi

# ALSA default
echo "[5/6] Configuring audio..."
cat > ~/.asoundrc << 'EOF'
defaults.pcm.card 0
defaults.pcm.device 0
defaults.ctl.card 0
EOF

# Set volume
amixer -c 0 set PCM 80% > /dev/null 2>&1 || true

# Systemd services
echo "[6/6] Installing systemd services..."
sudo tee /etc/systemd/system/surrogate.service > /dev/null << 'EOF'
[Unit]
Description=SURROGATE Voice Pipeline
After=network.target sound.target
Wants=network.target

[Service]
Type=simple
User=jarvis
Group=jarvis
WorkingDirectory=/home/jarvis/surrogate
ExecStart=/home/jarvis/surrogate/venv/bin/python3 /home/jarvis/surrogate/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/surrogate-remote.service > /dev/null << 'EOF'
[Unit]
Description=SURROGATE Remote Command API
After=network.target

[Service]
Type=simple
User=jarvis
Group=jarvis
WorkingDirectory=/home/jarvis/surrogate
Environment=SURROGATE_KEY=jarvis-surrogate-2026
Environment=SURROGATE_PORT=8080
ExecStart=/home/jarvis/surrogate/venv/bin/python3 /home/jarvis/surrogate/remote.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable surrogate-remote surrogate

echo ""
echo "=== Installation complete ==="
echo ""
echo "No API keys needed! OpenWakeWord is fully open-source."
echo ""
echo "Start services:"
echo "  sudo systemctl start surrogate-remote"
echo "  sudo systemctl start surrogate"
echo ""
echo "Or run manually:"
echo "  cd ~/surrogate && . venv/bin/activate && python3 main.py"
echo ""
echo "View logs:"
echo "  journalctl -u surrogate -f"
