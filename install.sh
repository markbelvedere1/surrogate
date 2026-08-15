#!/bin/bash
# SURROGATE install script
# Run: cd ~/surrogate && ./install.sh

set -e

echo "=== SURROGATE Installer ==="

# System packages
echo "[1/5] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq alsa-utils python3-pip python3-venv python3-dev git \
    libportaudio2 libsndfile1 ffmpeg > /dev/null 2>&1

# Python venv
echo "[2/5] Setting up Python environment..."
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Piper voice model
echo "[3/5] Downloading Piper TTS voice model..."
mkdir -p models/piper
if [ ! -f models/piper/voice.onnx ]; then
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx -O models/piper/voice.onnx
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json -O models/piper/voice.onnx.json
fi

# ALSA default
echo "[4/5] Configuring audio..."
cat > ~/.asoundrc << 'EOF'
defaults.pcm.card 0
defaults.pcm.device 0
defaults.ctl.card 0
EOF

# Set volume
amixer -c 0 set PCM 80% > /dev/null 2>&1 || true

# Systemd services
echo "[5/5] Installing systemd services..."
sudo tee /etc/systemd/system/surrogate.service > /dev/null << EOF
[Unit]
Description=SURROGATE Voice Pipeline
After=network.target sound.target
Wants=network.target

[Service]
Type=simple
User=jarvis
Group=jarvis
WorkingDirectory=/home/jarvis/surrogate
Environment=PICOVOICE_ACCESS_KEY=
ExecStart=/home/jarvis/surrogate/venv/bin/python3 /home/jarvis/surrogate/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/surrogate-remote.service > /dev/null << EOF
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
sudo systemctl enable surrogate-remote
sudo systemctl enable surrogate

echo ""
echo "=== Installation complete ==="
echo ""
echo "IMPORTANT: Before starting the voice pipeline:"
echo "  1. Get a free Picovoice access key at https://console.picovoice.ai/"
echo "  2. Add it to config.yaml under picovoice_access_key"
echo "  3. Also update /etc/systemd/system/surrogate.service:"
echo "     sudo systemctl edit surrogate"
echo "     Add: Environment=PICOVOICE_ACCESS_KEY=your_key_here"
echo ""
echo "Then start:"
echo "  sudo systemctl start surrogate-remote"
echo "  sudo systemctl start surrogate"
echo ""
echo "Or run manually:"
echo "  cd ~/surrogate && . venv/bin/activate && python3 main.py"
