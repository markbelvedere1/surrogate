#!/bin/bash
# SURROGATE Deploy Script
# Deploys updated code and installs/restarts all services.
# Run from the Pi: bash ~/surrogate/deploy.sh
#
# Called automatically by auto-update.sh when new code is pulled from GitHub.

set -e

SURROGATE_DIR="/home/jarvis/surrogate"
cd "$SURROGATE_DIR"

echo "=== SURROGATE Deploy ==="
echo "$(date): Starting deploy"

# 1. Stop existing services gracefully
echo "[1/6] Stopping services..."
sudo systemctl stop surrogate 2>/dev/null || true
sudo systemctl stop surrogate-bridge 2>/dev/null || true
# Keep surrogate-remote running if possible (don't kill our own update mechanism)

# Kill any ad-hoc processes (but not remote.py)
pkill -f "python3.*main.py" 2>/dev/null || true
pkill -f "python3.*bridge.py" 2>/dev/null || true
sleep 1

# 2. Update Python deps
echo "[2/6] Updating Python dependencies..."
. venv/bin/activate

# Remove old Porcupine packages if present
pip uninstall -y pvporcupine pvrecorder 2>/dev/null || true

# Install current requirements
pip install -r requirements.txt -q 2>&1 | tail -5

# 3. Download OpenWakeWord models if needed
echo "[3/6] Checking OpenWakeWord models..."
python3 -c "
import openwakeword
openwakeword.utils.download_models()
print('Models ready')
" 2>&1 | tail -2

# 4. Ensure Piper voice model exists
echo "[4/6] Checking Piper TTS model..."
mkdir -p models/piper
if [ ! -f models/piper/voice.onnx ]; then
    echo "  Downloading Piper voice model..."
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx -O models/piper/voice.onnx
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json -O models/piper/voice.onnx.json
    echo "  Done"
else
    echo "  Voice model present"
fi

# 5. Install systemd services
echo "[5/6] Installing systemd services..."

sudo tee /etc/systemd/system/surrogate.service > /dev/null << 'EOF'
[Unit]
Description=SURROGATE Voice Pipeline
After=network.target sound.target surrogate-bridge.service
Wants=network.target surrogate-bridge.service

[Service]
Type=simple
User=jarvis
Group=jarvis
WorkingDirectory=/home/jarvis/surrogate
ExecStart=/home/jarvis/surrogate/venv/bin/python3 /home/jarvis/surrogate/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/surrogate-bridge.service > /dev/null << 'EOF'
[Unit]
Description=SURROGATE ntfy Bridge (command relay)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=jarvis
Group=jarvis
WorkingDirectory=/home/jarvis/surrogate
ExecStart=/home/jarvis/surrogate/venv/bin/python3 /home/jarvis/surrogate/bridge.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/surrogate-remote.service > /dev/null << 'EOF'
[Unit]
Description=SURROGATE Remote Command API (local HTTP)
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

# 6. Reload and start
echo "[6/6] Starting services..."
sudo systemctl daemon-reload
sudo systemctl enable surrogate-remote surrogate-bridge surrogate

sudo systemctl restart surrogate-remote
sleep 1
sudo systemctl restart surrogate-bridge
sleep 2
sudo systemctl restart surrogate

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Service status:"
for svc in surrogate-remote surrogate-bridge surrogate; do
    STATUS=$(sudo systemctl is-active $svc 2>/dev/null || echo "unknown")
    echo "  $svc: $STATUS"
done
echo ""
echo "Logs: journalctl -u surrogate-bridge -f"
echo "      journalctl -u surrogate -f"
