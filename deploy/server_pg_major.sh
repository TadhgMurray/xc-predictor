#!/usr/bin/env bash
# Project: xc-predictor / deploy
# File:    server_pg_major.sh
# Purpose: Put a specific PostgreSQL major version on the server, from
#          the PGDG repository, and make it the cluster on port 5432.
#
#     export XCP_DB_PASSWORD='<the server db password>'
#     bash deploy/server_pg_major.sh 18
#
# ★ WHY THIS EXISTS. pg_restore cannot load a dump written by a NEWER
#   pg_dump, and pg_dump cannot dump from a NEWER server. So when the
#   workstation runs 18 and the server runs Ubuntu's 16, there is no
#   way to meet in the middle by re-dumping -- the SERVER has to move.
#   Ubuntu 24.04 ships 16; PGDG ships every current major for noble.
#
# ⚠ IT DROPS THE OLD CLUSTER, and refuses to if that cluster holds
#   anything but an empty database. Run it BEFORE the restore, never
#   after.

set -euo pipefail
TARGET="${1:?usage: server_pg_major.sh <major>   e.g. 18}"

if [ -z "${XCP_DB_PASSWORD:-}" ]; then
    echo "set XCP_DB_PASSWORD first:  export XCP_DB_PASSWORD='...'" >&2
    exit 1
fi

echo "== PGDG repository =="
apt-get install -yq curl ca-certificates gnupg
install -d /usr/share/postgresql-common/pgdg
curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc
CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc]" \
     "https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
     > /etc/apt/sources.list.d/pgdg.list
apt-get update -q

echo "== install PostgreSQL ${TARGET} =="
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
    "postgresql-${TARGET}" "postgresql-client-${TARGET}"

# ---- retire the old cluster, but only if it is empty ----------------- #
# ! THE GUARD IS THE POINT. Dropping a cluster with a restored database
#   in it silently throws away hours of upload. Anything past an empty
#   xc_predictor stops the script.
for OLD in $(pg_lsclusters -h | awk -v t="$TARGET" '$1 != t {print $1}'); do
    echo "== checking cluster ${OLD} =="
    SIZE=$(su postgres -c "psql --cluster ${OLD}/main -tAc \
        \"SELECT COALESCE(sum(pg_database_size(datname)),0) FROM pg_database \
          WHERE datname NOT IN ('template0','template1','postgres')\"" \
        2>/dev/null || echo 0)
    if [ "${SIZE:-0}" -gt 104857600 ]; then
        echo "REFUSING: cluster ${OLD} holds $((SIZE/1048576)) MB of user data." >&2
        echo "This script is for a FRESH server, before the restore." >&2
        exit 1
    fi
    echo "   cluster ${OLD} holds $((SIZE/1048576)) MB -- dropping it"
    pg_dropcluster --stop "${OLD}" main
done

echo "== put ${TARGET} on port 5432 =="
CONF="/etc/postgresql/${TARGET}/main/postgresql.conf"
sed -i "s/^#\?port *=.*/port = 5432/" "$CONF"

echo "== tuning (128 GB box) =="
grep -q "xc-predictor tuning" "$CONF" || cat >> "$CONF" <<'EOF'

# ---- xc-predictor tuning (server_pg_major.sh) ----
shared_buffers = 16GB
effective_cache_size = 96GB
work_mem = 256MB
maintenance_work_mem = 4GB
max_wal_size = 8GB
random_page_cost = 1.1
EOF
pg_ctlcluster "${TARGET}" main restart || systemctl restart "postgresql@${TARGET}-main"

echo "== database + password =="
su postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='xc_predictor'\"" \
    | grep -q 1 || su postgres -c "createdb xc_predictor"
su postgres -c "psql -c \"ALTER USER postgres PASSWORD '${XCP_DB_PASSWORD}'\""

echo
su postgres -c "psql -tAc 'SELECT version()'"
pg_lsclusters
echo
echo "DONE. PostgreSQL ${TARGET} is on 5432. Now:"
echo "  bash /srv/xc-predictor/deploy/server_restore.sh /srv/xc_predictor.dump"
