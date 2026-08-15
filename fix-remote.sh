#!/bin/bash
# Quick recovery script for the Pi remote API.
# 
# The remote API keeps dying because it's running via nohup instead of systemd.
# This script installs it as a proper systemd service so it auto-restarts.
#
# SSH into the Pi and run:
#   bash ~/surrogate/fix-remote.sh

set -e

echo "=== Fixing Remote API ==="

# Kill any lingering processes
pkill -f "python3.*remote.py" 2>/dev/null || true
sleep 1

# Install systemd service
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
sudo systemctl enable surrogate-remote
sudo systemctl start surrogate-remote

sleep 2
echo ""
sudo systemctl status surrogate-remote --no-pager -l | head -10
echo ""

# Test it
curl -s http://localhost:8080 && echo " ← Remote API OK" || echo " ← Remote API FAILED"
echo ""
echo "Done. JARVIS should now have remote access via Tailscale Funnel."
