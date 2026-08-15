#!/bin/bash
# SURROGATE Deploy Script — robust, minimal
# Called by auto-update.sh when new code is pulled.

SURROGATE_DIR="/home/jarvis/surrogate"
cd "$SURROGATE_DIR"

echo "=== SURROGATE Deploy ==="
echo "$(date): Starting deploy"

# 1. Ensure venv exists
if [ ! -d venv ]; then
    echo "[1] Creating venv..."
    python3 -m venv venv
fi
. venv/bin/activate

# 2. Install deps (non-fatal — bridge has zero external deps)
echo "[2] Installing dependencies..."
pip install -r requirements.txt -q 2>&1 | tail -5 || echo "WARN: some pip packages failed (bridge still works)"

# 3. Install systemd services
echo "[3] Installing systemd services..."

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
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 4. Reload and start
echo "[4] Starting services..."
sudo systemctl daemon-reload
sudo systemctl enable surrogate-remote surrogate-bridge surrogate 2>/dev/null || true

# Kill stale processes
pkill -f "python3.*bridge.py" 2>/dev/null || true
sleep 1

sudo systemctl restart surrogate-remote 2>/dev/null || echo "WARN: surrogate-remote failed"
sudo systemctl restart surrogate-bridge 2>/dev/null || echo "WARN: surrogate-bridge failed"
# Voice loop stays stopped until push-to-talk is ready
sudo systemctl stop surrogate 2>/dev/null || true
sudo systemctl disable surrogate 2>/dev/null || true

echo ""
echo "=== Deploy complete ==="
for svc in surrogate-remote surrogate-bridge surrogate; do
    STATUS=$(sudo systemctl is-active $svc 2>/dev/null || echo "unknown")
    echo "  $svc: $STATUS"
done
echo ""
