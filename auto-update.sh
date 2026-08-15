#!/bin/bash
# SURROGATE Auto-Update Script
# Runs via cron every 5 minutes. Pulls latest code from GitHub,
# and if anything changed, copies to ~/surrogate/ and runs deploy.sh.
#
# crontab entry:
#   */5 * * * * /home/jarvis/surrogate-git/auto-update.sh >> /tmp/surrogate-update.log 2>&1

set -e

GIT_DIR="/home/jarvis/surrogate-git"
DEPLOY_DIR="/home/jarvis/surrogate"
LOG="/tmp/surrogate-update.log"

cd "$GIT_DIR"

BEFORE=$(git rev-parse HEAD)
git pull --ff-only origin main -q 2>/dev/null || {
    echo "$(date): git pull failed" >> "$LOG"
    exit 1
}
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ]; then
    echo "$(date): New code detected ($BEFORE → $AFTER)"

    # Copy all relevant files (not .git, not __pycache__)
    for ext in py yaml sh txt md; do
        cp -f "$GIT_DIR"/*.$ext "$DEPLOY_DIR/" 2>/dev/null || true
    done

    # Run deploy
    cd "$DEPLOY_DIR"
    bash deploy.sh >> "$LOG" 2>&1

    echo "$(date): Deploy complete ($AFTER)"
fi
