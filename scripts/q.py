# Project: xc-predictor
# File:    scripts/q.py
# Purpose: run ONE read-only query from the command line, so ad-hoc checks
#          never fight PowerShell quoting again. SELECT-only, always rolls
#          back, prints column names then rows.
#
# USAGE (PowerShell double quotes outside, ordinary single quotes inside):
#   python scripts\q.py "SELECT count(*) FROM results WHERE source='anet'"

import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts\\q.py \"SELECT ...\"")
    sql = " ".join(sys.argv[1:]).strip()
    if not sql.lower().startswith(("select", "with")):
        sys.exit("read-only runner: query must start with SELECT/WITH")
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        print([d[0] for d in cur.description])
        for row in cur.fetchmany(50):        # cap output; refine SQL for more
            print(row)
        conn.rollback()


if __name__ == "__main__":
    main()