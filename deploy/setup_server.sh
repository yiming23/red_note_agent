#!/usr/bin/env bash
# One-time server initialisation for a fresh Ubuntu 22.04 droplet.
# Run as root: bash deploy/setup_server.sh
set -euo pipefail

REPO_URL="${REPO_URL:-}"   # set before running, e.g. https://github.com/youruser/xhs-agent
DEPLOY_DIR="/opt/xhs-agent"

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: set REPO_URL before running this script"
  echo "  export REPO_URL=https://github.com/youruser/xhs-agent"
  exit 1
fi

echo "=== [1/6] apt packages ==="
apt-get update -qq
apt-get install -y -qq git curl

echo "=== [2/6] install uv ==="
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "=== [3/6] clone repo ==="
if [[ -d "$DEPLOY_DIR/.git" ]]; then
  echo "Repo already cloned, pulling latest"
  git -C "$DEPLOY_DIR" pull origin main
else
  git clone "$REPO_URL" "$DEPLOY_DIR"
fi

echo "=== [4/6] install dependencies & migrate DB ==="
cd "$DEPLOY_DIR"
uv sync --frozen
uv run alembic upgrade head

echo "=== [5/6] install systemd services ==="
cp deploy/xhs-scheduler.service /etc/systemd/system/
cp deploy/xhs-bot.service        /etc/systemd/system/
systemctl daemon-reload
systemctl enable xhs-scheduler xhs-bot
systemctl start  xhs-scheduler xhs-bot

echo "=== [6/6] nightly image cleanup cron (03:00 UTC) ==="
CRON_LINE="0 3 * * * root cd $DEPLOY_DIR && uv run python scripts/cleanup_old_images.py >> /var/log/xhs-cleanup.log 2>&1"
CRON_FILE="/etc/cron.d/xhs-cleanup"
echo "$CRON_LINE" > "$CRON_FILE"
chmod 644 "$CRON_FILE"

echo ""
echo "Done! Check service status with:"
echo "  systemctl status xhs-scheduler xhs-bot"
echo ""
echo "IMPORTANT: copy your .env file to $DEPLOY_DIR/.env before the services will run correctly."
