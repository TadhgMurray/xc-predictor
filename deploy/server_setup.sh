#!/usr/bin/env bash
# Project: xc-predictor / deploy
# File:    server_setup.sh
# Purpose: One-paste bootstrap for a fresh Ubuntu 24.04 server (ReliableSite
#          5950X, 2026-08). Run as root. Idempotent enough to re-run.
#
#   0. FIRST, on a bare box:   apt-get update -q && apt-get install -y git
#      This script installs git -- but you need git to CLONE this script,
#      so a fresh server cannot bootstrap itself. Install it, clone, then:
#
#   1. On the server:   export XCP_DB_PASSWORD='<new strong password>'
#                       export XCP_REPO_URL='https://<token>@github.com/TadhgMurray/xc-predictor'
#                       git clone "$XCP_REPO_URL" /srv/xc-predictor
#                       bash /srv/xc-predictor/deploy/server_setup.sh
#   2. From home:       scp xc_predictor.dump root@<ip>:/srv/
#   3. On the server:   bash deploy/server_restore.sh /srv/xc_predictor.dump
#
# The site then serves on port 80. TLS: point the domain's A record at the
# server, then run:  certbot --nginx -d racecast.com -d www.racecast.com

set -euo pipefail

# ! A BARE SERVER HAS NO git AND NO apt CERTAINTY. Check both before
#   touching anything, so the failure names its own fix instead of
#   surfacing as "No such file or directory" three commands later.
if ! command -v apt-get >/dev/null; then
    echo "this script is for Debian/Ubuntu (no apt-get here)." >&2
    echo "check: cat /etc/os-release" >&2
    exit 1
fi
if ! command -v git >/dev/null; then
    echo "git is missing -- run:  apt-get update -q && apt-get install -y git" >&2
    exit 1
fi

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
# ! PRIVATE REPO. Set XCP_REPO_URL to a tokened URL first:
#     GitHub -> Settings -> Developer settings -> fine-grained token,
#     read-only on TadhgMurray/xc-predictor, then
#     export XCP_REPO_URL='https://<token>@github.com/TadhgMurray/xc-predictor'
#   (or skip: scp the repo to /srv/xc-predictor yourself and re-run.)
if [ ! -d /srv/xc-predictor ]; then
    if [ -n "${XCP_REPO_URL:-}" ]; then
        git clone "$XCP_REPO_URL" /srv/xc-predictor
    else
        echo "no /srv/xc-predictor and no XCP_REPO_URL set -- clone or scp" \
             "the repo, then re-run" >&2
        exit 1
    fi
fi
cd /srv/xc-predictor && git pull --ff-only || true
# ! THE SERVICE USER MUST OWN THE CHECKOUT. app.py opens
#   racecast/errors.log with a logging.FileHandler at IMPORT time, and
#   Python writes __pycache__ beside every module -- both inside the
#   repo. Cloned as root, the xcp workers die on PermissionError before
#   the app object exists, which surfaces only as "Worker failed to
#   boot" with the real cause buried above gunicorn's own traceback.
chown -R xcp:xcp /srv/xc-predictor
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
# ! WORKING DIRECTORY IS THE REPO ROOT, NOT racecast/. app.py does
#   sys.path.insert(0, "scripts") -- a RELATIVE path, resolved against
#   the working directory -- so from racecast/ it looks for
#   racecast/scripts, which does not exist, and every worker dies with
#   ModuleNotFoundError: No module named 'database'. The owner runs the
#   dev server from the repo root, which is why it only fails here.
WorkingDirectory=/srv/xc-predictor
EnvironmentFile=/etc/xc-predictor.env
ExecStart=/srv/venv/bin/gunicorn -w 8 --timeout 60 \
    --pythonpath /srv/xc-predictor/racecast,/srv/xc-predictor/scripts,/srv/xc-predictor/engine \
    -b 127.0.0.1:8000 app:app
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
