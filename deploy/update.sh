#!/usr/bin/env bash
# Redeploy after pulling new code. Runs as the app user (no full sudo needed after install.sh).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
git pull --ff-only || true
(cd backend && .venv/bin/pip install -q -e .)
(cd frontend && npm ci --no-audit --no-fund --loglevel=error && npm run build)
sudo /usr/bin/rsync -a --delete "$REPO/frontend/dist/" /var/www/fppcc/
sudo /usr/bin/systemctl restart fppcc    # runs alembic upgrade head on start
sleep 2; curl -sf http://127.0.0.1/api/health && echo " updated"
