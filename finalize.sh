#!/bin/bash
# SURROGATE Recovery / Finalize Script
# Run this on the Pi after pasting your Picovoice access key below.
#
# Usage: 
#   ssh jarvis@raspberrypi.local
#   bash ~/surrogate/finalize.sh YOUR_PICOVOICE_KEY

set -e

PICOVOICE_KEY="${1:-}"

echo "=== SURROGATE Finalize ==="

# 1. Kill any lingering ad-hoc remote.py processes
echo "[1/6] Cleaning up old processes..."
pkill -f "python3.*remote.py" 2>/dev/null || true
sleep 1

# 2. Install systemd service for remote API
echo "[2/6] Installing surrogate-remote service..."
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

# 3. Install systemd service for voice pipeline
echo "[3/6] Installing surrogate voice service..."
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
Environment=PICOVOICE_ACCESS_KEY=${PICOVOICE_KEY}
ExecStart=/home/jarvis/surrogate/venv/bin/python3 /home/jarvis/surrogate/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 4. Update config.yaml with Picovoice key
if [ -n "$PICOVOICE_KEY" ]; then
    echo "[4/6] Setting Picovoice key in config..."
    cd /home/jarvis/surrogate
    python3 -c "
import yaml
with open('config.yaml') as f:
    cfg = yaml.safe_load(f)
cfg['picovoice_access_key'] = '${PICOVOICE_KEY}'
with open('config.yaml', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
print('Key set in config.yaml')
"
else
    echo "[4/6] SKIPPED: No Picovoice key provided"
    echo "  Get one at https://console.picovoice.ai/"
    echo "  Re-run: bash ~/surrogate/finalize.sh YOUR_KEY"
fi

# 5. Reload and start services
echo "[5/6] Starting services..."
sudo systemctl daemon-reload
sudo systemctl enable surrogate-remote surrogate
sudo systemctl restart surrogate-remote

if [ -n "$PICOVOICE_KEY" ]; then
    sudo systemctl restart surrogate
    echo "[6/6] Voice pipeline starting..."
    sleep 3
    sudo systemctl status surrogate --no-pager | head -15
else
    echo "[6/6] Voice pipeline NOT started (need Picovoice key first)"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Remote API:    sudo systemctl status surrogate-remote"
echo "Voice pipeline: sudo systemctl status surrogate"
echo "View logs:      journalctl -u surrogate -f"
echo ""

# Verify remote API is accessible
sleep 2
curl -s http://localhost:8080 2>/dev/null && echo "Remote API: OK" || echo "Remote API: FAILED"
