#!/bin/bash
# SURROGATE Deploy Script
# Deploys updated code to the Pi and installs services.
# Run from the Pi: bash ~/surrogate/deploy.sh
# Or remotely via the remote API.

set -e

echo "=== SURROGATE Deploy ==="

# 1. Stop existing services gracefully
echo "[1/5] Stopping existing services..."
sudo systemctl stop surrogate 2>/dev/null || true
sudo systemctl stop surrogate-remote 2>/dev/null || true

# Kill any ad-hoc processes
pkill -f "python3.*main.py" 2>/dev/null || true
pkill -f "python3.*remote.py" 2>/dev/null || true
sleep 1

# 2. Update Python deps
echo "[2/5] Updating Python dependencies..."
cd /home/jarvis/surrogate
. venv/bin/activate

# Remove old Porcupine packages if present
pip uninstall -y pvporcupine pvrecorder 2>/dev/null || true

# Install current requirements
pip install -r requirements.txt -q

# Install speex noise suppression if not present
pip install speexdsp-ns 2>/dev/null || true

# Download OpenWakeWord models
echo "[3/5] Downloading OpenWakeWord models..."
python3 -c "
import openwakeword
openwakeword.utils.download_models()
print('Models ready')
"

# 4. Install systemd services
echo "[4/5] Installing systemd services..."
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

# 5. Reload and start
echo "[5/5] Starting services..."
sudo systemctl daemon-reload
sudo systemctl enable surrogate-remote surrogate
sudo systemctl start surrogate-remote
sleep 2
sudo systemctl start surrogate

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Status:"
sudo systemctl status surrogate-remote --no-pager -l | head -5
echo "---"
sudo systemctl status surrogate --no-pager -l | head -5
echo ""
echo "Logs: journalctl -u surrogate -f"
