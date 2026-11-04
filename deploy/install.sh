#!/usr/bin/env bash
# One-time VM setup for FPP Command Center (Ubuntu 24.04+). Safe to re-run.
#   sudo deploy/install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(stat -c %U "$REPO")"
ENV_FILE=/etc/fppcc/fppcc.env

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
echo "==> repo $REPO, service user $APP_USER"

echo "==> apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql nginx python3-venv python3-pip nodejs npm rsync curl >/dev/null

echo "==> PostgreSQL database"
systemctl enable --now postgresql
mkdir -p /etc/fppcc
if [[ ! -f $ENV_FILE ]]; then
    DB_PASS="$(openssl rand -hex 24)"
    sed "s/CHANGE_ME/$DB_PASS/" "$REPO/deploy/fppcc.env.example" > "$ENV_FILE"
else
    DB_PASS="$(sed -n 's|.*//fppcc:\([^@]*\)@.*|\1|p' "$ENV_FILE")"
fi
chown root:"$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fppcc') THEN
    CREATE ROLE fppcc LOGIN PASSWORD '$DB_PASS';
  ELSE
    ALTER ROLE fppcc PASSWORD '$DB_PASS';
  END IF;
END \$\$;
SQL
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='fppcc'" | grep -q 1 \
    || sudo -u postgres createdb -O fppcc fppcc

echo "==> backend venv"
sudo -u "$APP_USER" bash -c "cd '$REPO/backend' && { [[ -x .venv/bin/pip ]] || python3 -m venv --clear .venv; } \
    && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e ."

echo "==> frontend build"
sudo -u "$APP_USER" bash -c "cd '$REPO/frontend' && npm ci --no-audit --no-fund --loglevel=error && npm run build"
mkdir -p /var/www/fppcc
rsync -a --delete "$REPO/frontend/dist/" /var/www/fppcc/

echo "==> systemd service"
sed -e "s|__USER__|$APP_USER|g" -e "s|__REPO__|$REPO|g" "$REPO/deploy/fppcc.service" > /etc/systemd/system/fppcc.service
systemctl daemon-reload
systemctl enable fppcc
systemctl restart fppcc

echo "==> nginx"
cp "$REPO/deploy/nginx-fppcc.conf" /etc/nginx/sites-available/fppcc
ln -sf /etc/nginx/sites-available/fppcc /etc/nginx/sites-enabled/fppcc
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl enable --now nginx
systemctl reload nginx

echo "==> sudoers rule so $APP_USER can redeploy without a password"
cat > /etc/sudoers.d/fppcc-deploy <<SUDO
$APP_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart fppcc, /usr/bin/systemctl status fppcc, /usr/bin/rsync -a --delete $REPO/frontend/dist/ /var/www/fppcc/, /usr/bin/journalctl -u fppcc *
SUDO
chmod 440 /etc/sudoers.d/fppcc-deploy
visudo -cq

echo "==> Tailscale Serve (HTTPS on the tailnet -> nginx :80)"
if tailscale serve --bg --https=443 http://127.0.0.1:80 >/dev/null 2>&1; then
    tailscale serve status | head -5
else
    echo "   tailscale serve failed — enable HTTPS certificates in the Tailscale admin console (DNS page), then re-run:"
    echo "   sudo tailscale serve --bg --https=443 http://127.0.0.1:80"
fi

echo "==> waiting for API"
for _ in $(seq 1 30); do curl -sf http://127.0.0.1/api/health >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1/api/health && echo && echo "Done. Open the Tailscale URL above (or http://$(hostname -I | awk '{print $1}')/)."
