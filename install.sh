#!/bin/bash
# SURROGATE install script — first-time setup
# Run: cd ~/surrogate && ./install.sh

set -e

echo "=== SURROGATE Installer ==="

# System packages
echo "[1/7] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq alsa-utils python3-pip python3-venv python3-dev git \
    libportaudio2 libsndfile1 ffmpeg libspeexdsp-dev > /dev/null 2>&1

# Python venv
echo "[2/7] Setting up Python environment..."
if [ ! -d venv ]; then
    python3 -m venv venv
fi
. venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Download OpenWakeWord models
echo "[3/7] Downloading OpenWakeWord models..."
python3 -c "
import openwakeword
openwakeword.utils.download_models()
print('Models downloaded')
"

# Piper TTS binary (if not installed)
echo "[4/7] Checking Piper TTS..."
if ! command -v piper &>/dev/null; then
    pip install piper-tts -q
fi

# Piper voice model
echo "[5/7] Downloading Piper TTS voice model..."
mkdir -p models/piper
if [ ! -f models/piper/voice.onnx ]; then
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx -O models/piper/voice.onnx
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json -O models/piper/voice.onnx.json
fi

# ALSA default
echo "[6/7] Configuring audio..."
cat > ~/.asoundrc << 'EOF'
defaults.pcm.card 0
defaults.pcm.device 0
defaults.ctl.card 0
EOF
amixer -c 0 set PCM 80% > /dev/null 2>&1 || true

# Deploy (installs systemd services and starts everything)
echo "[7/7] Running deploy..."
bash deploy.sh

echo ""
echo "=== Installation complete ==="
echo ""
echo "No API keys required — all components are fully open source."
echo ""
echo "Services running:"
for svc in surrogate-remote surrogate-bridge surrogate; do
    STATUS=$(sudo systemctl is-active $svc 2>/dev/null || echo "unknown")
    echo "  $svc: $STATUS"
done
echo ""
echo "Say 'Hey JARVIS' to test the voice pipeline!"
echo "View logs: journalctl -u surrogate -f"
