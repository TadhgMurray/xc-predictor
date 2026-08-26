#!/usr/bin/env bash
# Project: xc-predictor / deploy
# File:    server_setup.sh
# Purpose: One-paste bootstrap for a fresh Ubuntu 24.04 server (ReliableSite
#          5950X, 2026-08). Run as root. Idempotent enough to re-run.
#
#   1. On the server:   export XCP_DB_PASSWORD='<new strong password>'
#                       bash deploy/server_setup.sh
#   2. From home:       scp xc_predictor.dump root@<ip>:/srv/
#   3. On the server:   bash deploy/server_restore.sh /srv/xc_predictor.dump
#
# The site then serves on port 80. TLS: point the domain's A record at the
# server, then run:  certbot --nginx -d racecast.com -d www.racecast.com

set -euo pipefail

if [ -z "${XCP_DB_PASSWORD:-}" ]; then
    echo "set XCP_DB_PASSWORD first:  export XCP_DB_PASSWORD='...'" >&2
    exit 1
fi

echo "== packages =="
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
    postgresql postgresql-contrib nginx git \
    python3-pip python3-venv certbot python3-certbot-nginx ufw

echo "== firewall =="
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

echo "== postgres =="
systemctl enable --now postgresql
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='xc_predictor'" \
    | grep -q 1 || sudo -u postgres createdb xc_predictor
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '${XCP_DB_PASSWORD}'"
# ★ TUNING FOR ONE BIG DB ON A 128 GB BOX. Conservative-but-real numbers;
#   the defaults assume a 1 GB VPS and cripple everything.
PGCONF=$(ls /etc/postgresql/*/main/postgresql.conf | head -1)
grep -q "xc-predictor tuning" "$PGCONF" || cat >> "$PGCONF" <<'EOF'

# ---- xc-predictor tuning (server_setup.sh) ----
shared_buffers = 16GB
effective_cache_size = 96GB
work_mem = 256MB
maintenance_work_mem = 4GB
max_wal_size = 8GB
random_page_cost = 1.1
EOF
systemctl restart postgresql

echo "== app =="
id -u xcp &>/dev/null || useradd -m -s /bin/bash xcp
if [ ! -d /srv/xc-predictor ]; then
    git clone https://github.com/TadhgMurray/xc-predictor /srv/xc-predictor
fi
cd /srv/xc-predictor && git pull
python3 -m venv /srv/venv 2>/dev/null || true
/srv/venv/bin/pip install -q --upgrade pip
/srv/venv/bin/pip install -q flask gunicorn psycopg2-binary numpy scipy torch \
    --extra-index-url https://download.pytorch.org/whl/cpu

echo "== env + service =="
cat > /etc/xc-predictor.env <<EOF
XCP_DB_HOST=127.0.0.1
XCP_DB_PORT=5432
XCP_DB_NAME=xc_predictor
XCP_DB_USER=postgres
XCP_DB_PASSWORD=${XCP_DB_PASSWORD}
EOF
chmod 600 /etc/xc-predictor.env

cat > /etc/systemd/system/xc-predictor.service <<'EOF'
[Unit]
Description=xc-predictor site
After=postgresql.service

[Service]
User=xcp
WorkingDirectory=/srv/xc-predictor/racecast
EnvironmentFile=/etc/xc-predictor.env
ExecStart=/srv/venv/bin/gunicorn -w 8 --timeout 60 -b 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable xc-predictor

echo "== nginx =="
cat > /etc/nginx/sites-available/xc-predictor <<'EOF'
server {
    listen 80 default_server;
    server_name _;
    location /static/ {
        alias /srv/xc-predictor/racecast/static/;
        expires 7d;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF
ln -sf /etc/nginx/sites-available/xc-predictor /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo
echo "DONE. Next: scp the dump to /srv/, then run deploy/server_restore.sh"
