#!/usr/bin/env python3
"""
origin_health.py -- is this box fit to serve racecast.co? And how is the
replication between the two boxes doing?

    origin_health.py --serve              # HTTP on RC_HEALTH_PORT for Cloudflare's monitor
    origin_health.py --report             # people: role, lag, slots, disk; exit 1 on a problem
    origin_health.py --alert              # timer: --report, and mail the admins on a NEW problem
    origin_health.py --wait-caught-up     # on the primary: block until every standby has
                                          #   replayed everything written so far (--timeout 1800)

★ WHAT "HEALTHY" MEANS FOR THE MONITOR: the site answers here (gunicorn on
  127.0.0.1:8000, /robots.txt) AND this box's own Postgres answers a query.
  Replication lag does NOT make a box unhealthy: a standby whose replica is
  behind still serves (read_routing sends its reads to the primary past
  XCP_DB_READ_MAX_LAG_S), and when the primary is down a stale replica is
  the best copy there is. The lag is in the body for people.
★ --wait-caught-up IS FOR THE EDGE PURGE. Run it on the primary right before
  scripts/edge_purge.py: a purge before the standby has replayed a go-live
  would let the edge re-cache the OLD boards from the standby's site for
  another s-maxage. It returns at once when no standby is connected.

Connects to THIS box's cluster (127.0.0.1, the postgres user and the
password in /etc/xc-predictor.env) -- never the role file's XCP_DB_HOST, which
on a standby names the other box. Standard library + psycopg2 (/srv/venv).
"""
import argparse
import http.server
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request

STATE_DIR = "/var/lib/racecast-replica"


def _env(path):
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


# the files when this process may read them (root: --alert, --report), else
# what systemd handed it (the --serve unit runs as xcp with EnvironmentFile=)
RC = {**{k: v for k, v in os.environ.items() if k.startswith("RC_")},
      **_env(os.environ.get("RC_ENV", "/etc/racecast-replica.env"))}
XCP = {**{k: v for k, v in os.environ.items() if k.startswith("XCP_")},
       **_env(os.environ.get("XCP_ENV", "/etc/xc-predictor.env"))}


def _connect():
    import psycopg2
    return psycopg2.connect(host="127.0.0.1", port=int(RC.get("RC_PG_PORT", 5432)),
                            dbname=RC.get("RC_DB_NAME", "xc_predictor"), user="postgres",
                            password=XCP.get("XCP_DB_PASSWORD"),
                            connect_timeout=3, application_name="rc-health",
                            options="-c statement_timeout=5000")


def _q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def facts():
    """Everything --report and the monitor say, as one dict."""
    f = {"host": socket.gethostname(), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "problems": []}
    # the site
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/robots.txt", headers={"Host": "racecast.co"})
        with urllib.request.urlopen(req, timeout=5) as r:
            f["site"] = r.status
    except Exception as exc:                                      # noqa: BLE001
        f["site"] = f"down ({type(exc).__name__})"
        f["problems"].append("the site (gunicorn) does not answer here")
    # the database
    try:
        conn = _connect()
    except Exception as exc:                                      # noqa: BLE001
        f["db"] = f"down ({str(exc).splitlines()[0]})"
        f["problems"].append("this box's Postgres does not answer")
        return f
    try:
        with conn.cursor() as cur:
            rec = _q(cur, "SELECT pg_is_in_recovery()")[0][0]
            f["db"] = "ok"
            f["role"] = "standby" if rec else "primary"
            if rec:
                row = _q(cur, """
                    SELECT CASE WHEN pg_last_wal_receive_lsn() IS NOT DISTINCT FROM pg_last_wal_replay_lsn()
                                THEN 0 ELSE extract(epoch FROM now() - pg_last_xact_replay_timestamp()) END,
                           (SELECT status FROM pg_stat_wal_receiver),
                           pg_get_wal_replay_pause_state(),
                           pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn())""")[0]
                f["lag_s"] = round(float(row[0] or 0), 1)
                f["receiver"] = row[1] or "not connected"
                f["replay"] = row[2]
                f["replay_behind_bytes"] = int(row[3] or 0)
                if f["receiver"] != "streaming":
                    f["problems"].append(f"not streaming from the primary (receiver: {f['receiver']})")
                if f["replay"] != "not paused":
                    f["problems"].append(f"replay is {f['replay']} -- a setting below the primary's? "
                                         "standby_build.sh --settings")
                if f["lag_s"] > 1800:
                    f["problems"].append(f"{f['lag_s']:.0f} s behind")
            else:
                f["standbys"] = [
                    {"name": r[0], "state": r[1], "replay_lag_s": r[2], "behind_bytes": int(r[3] or 0)}
                    for r in _q(cur, """
                        SELECT application_name, state,
                               round(extract(epoch FROM replay_lag)::numeric, 1)::float,
                               pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)
                        FROM pg_stat_replication""")]
                f["slots"] = []
                for name, active, status, kept, safe in _q(cur, """
                        SELECT slot_name, active, wal_status,
                               pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn), safe_wal_size
                        FROM pg_replication_slots WHERE slot_type = 'physical'"""):
                    f["slots"].append({"name": name, "active": active, "wal_status": status,
                                       "kept_gb": round(float(kept or 0) / 2**30, 1),
                                       "safe_gb": None if safe is None else round(float(safe) / 2**30, 1)})
                    if status in ("unreserved", "lost"):
                        f["problems"].append(f"slot {name} is {status}: its standby needs "
                                             "standby_build.sh --rebuild")
                    elif not active:
                        f["problems"].append(f"slot {name} has no standby streaming "
                                             f"(it keeps {float(kept or 0) / 2**30:.1f} GB of WAL here)")
    finally:
        conn.close()
    du = shutil.disk_usage("/var/lib/postgresql")
    f["disk_free_gb"] = round(du.free / 2**30)
    if du.free < 0.10 * du.total:
        f["problems"].append(f"only {f['disk_free_gb']} GB free under /var/lib/postgresql")
    return f


def healthy():
    """(ok, facts) for the monitor: the site and the local database answer."""
    f = facts()
    return f.get("site") == 200 and f.get("db") == "ok", f


def serve(port):
    cache = {"at": 0.0, "val": (False, {})}
    lock = threading.Lock()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                         # noqa: N802
            with lock:
                if time.time() - cache["at"] > 5:
                    cache["val"], cache["at"] = healthy(), time.time()
                ok, f = cache["val"]
            body = json.dumps({"ok": ok, "role": f.get("role"), "lag_s": f.get("lag_s"),
                               "site": f.get("site"), "db": f.get("db")}).encode()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_HEAD = do_GET

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"[health] serving on :{port}", flush=True)
    srv.serve_forever()


def wait_caught_up(timeout):
    """On the primary: until every connected standby replays past the WAL
    position of the moment this was called. 0 caught up (or no standby), 1
    timed out, 2 not the primary."""
    conn = _connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        if _q(cur, "SELECT pg_is_in_recovery()")[0][0]:
            print("[health] this box is a standby; nothing to wait for")
            return 2
        target = _q(cur, "SELECT pg_current_wal_lsn()")[0][0]
        t0 = time.time()
        while True:
            rows = _q(cur, """SELECT application_name, pg_wal_lsn_diff(%s, replay_lsn)
                              FROM pg_stat_replication""", (target,))
            if not rows:
                print("[health] no standby connected; not waiting")
                return 0
            behind = {n: int(b or 0) for n, b in rows if b is None or b > 0}
            if not behind:
                print(f"[health] every standby has replayed {target} ({time.time() - t0:.0f} s)")
                return 0
            if time.time() - t0 > timeout:
                print(f"[health] gave up after {timeout} s; still behind: {behind}")
                return 1
            time.sleep(5)


def report(f):
    lines = [f"{f['host']}  {f['at']}  role={f.get('role', '?')}  site={f.get('site')}  db={f.get('db')}"]
    if f.get("role") == "standby":
        lines.append(f"  receiver={f.get('receiver')}  replay={f.get('replay')}  lag={f.get('lag_s')} s"
                     f"  received-not-replayed={f.get('replay_behind_bytes', 0) / 2**20:.0f} MB")
    for s in f.get("standbys", []):
        lines.append(f"  standby {s['name']}: {s['state']}, replay lag {s['replay_lag_s']} s, "
                     f"{s['behind_bytes'] / 2**20:.0f} MB behind")
    for s in f.get("slots", []):
        lines.append(f"  slot {s['name']}: {'active' if s['active'] else 'IDLE'}, {s['wal_status']}, "
                     f"keeps {s['kept_gb']} GB" + (f", {s['safe_gb']} GB to the cap" if s['safe_gb'] is not None else ""))
    if "disk_free_gb" in f:
        lines.append(f"  disk free: {f['disk_free_gb']} GB")
    lines += [f"  PROBLEM: {p}" for p in f["problems"]] or ["  no problems"]
    return "\n".join(lines)


def alert(f):
    """Mail the admins when the set of problems changes to a non-empty one,
    and again at most every 6 hours while it lasts."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "alert.json")
    try:
        last = json.load(open(path))
    except (OSError, ValueError):
        last = {"problems": [], "at": 0}
    now, probs = time.time(), sorted(f["problems"])
    due = probs and (probs != last.get("problems") or now - last.get("at", 0) > 6 * 3600)
    if probs != last.get("problems") or due:
        json.dump({"problems": probs, "at": now if due else last.get("at", 0)}, open(path, "w"))
    if not due:
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (os.path.join(root, "racecast"), os.path.join(root, "scripts")):
        sys.path.insert(0, p)
    os.environ.setdefault("XCP_DB_QUIET", "1")
    for k, v in XCP.items():
        os.environ.setdefault(k, v)
    import accounts
    to = sorted(accounts.adminEmails())
    if not to or not accounts.mailEnabled():
        print("[health] no admins or no mail provider -- not sent")
        return
    subject = f"[racecast] replica: {probs[0]}" + (f" (+{len(probs) - 1})" if len(probs) > 1 else "")
    text = report(f) + "\n\nRunbook: docs/REPLICA-PLAN.md\n"
    for addr in to:
        print(f"[health] mail {'sent' if accounts.sendMail(addr, subject, text) else 'FAILED'} to {addr}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--serve", action="store_true")
    g.add_argument("--report", action="store_true")
    g.add_argument("--alert", action="store_true")
    g.add_argument("--wait-caught-up", action="store_true")
    ap.add_argument("--port", type=int, default=int(RC.get("RC_HEALTH_PORT", 8099)))
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.serve:
        serve(a.port)
        return 0
    if a.wait_caught_up:
        try:
            return wait_caught_up(a.timeout)
        except Exception as exc:                                  # noqa: BLE001
            print(f"[health] cannot check the standbys ({exc}); not waiting")
            return 0
    f = facts()
    print(json.dumps(f, indent=1, default=str) if a.json else report(f))
    if a.alert:
        alert(f)
    return 1 if f["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
