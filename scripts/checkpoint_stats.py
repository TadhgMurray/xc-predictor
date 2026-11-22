#!/usr/bin/env python3
"""scripts/checkpoint_stats.py -- how hard the run made Postgres checkpoint.

    checkpoint_stats.py --save FILE        # at the run's start
    checkpoint_stats.py --since FILE       # at its end: the run's own numbers

★ WHY (2026-11-22, owner: "get really experimental"). The pipeline rewrites
  tables of 20-70 GB (05's merges, 08's results rebuilds, 10's rankings).
  With max_wal_size = 8GB (deploy/server_setup.sh) Postgres has to
  checkpoint every 8 GB of WAL, and the first change to every page after a
  checkpoint writes the whole page into the WAL again (full_page_writes).
  If most of the run's checkpoints are REQUESTED (forced by WAL size)
  rather than TIMED, the bulk steps are paying that cost over and over;
  deploy/pg_bulk_tuning.sh raises the limits. This prints the evidence:
  checkpoints timed vs requested, the WAL written, and its full-page images.

Read-only. Postgres 17+ keeps the counters in pg_stat_checkpointer; older
servers in pg_stat_bgwriter. pg_stat_wal has the WAL totals on 14+.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def snapshot(cur):
    out = {}
    cur.execute("SELECT current_setting('server_version_num')::int")
    ver = cur.fetchone()[0]
    if ver >= 170000:
        cur.execute("SELECT num_timed, num_requested, write_time, sync_time, buffers_written "
                    "FROM pg_stat_checkpointer")
    else:
        cur.execute("SELECT checkpoints_timed, checkpoints_req, checkpoint_write_time, "
                    "checkpoint_sync_time, buffers_checkpoint FROM pg_stat_bgwriter")
    t, r, wt, st, bw = cur.fetchone()
    out.update(timed=int(t), requested=int(r), write_ms=float(wt), sync_ms=float(st),
               buffers=int(bw))
    try:
        cur.execute("SELECT wal_records, wal_fpi, wal_bytes FROM pg_stat_wal")
        rec, fpi, byt = cur.fetchone()
        out.update(wal_records=int(rec), wal_fpi=int(fpi), wal_bytes=int(byt))
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
    for name in ("max_wal_size", "checkpoint_timeout", "wal_compression"):
        cur.execute("SELECT current_setting(%s)", (name,))
        out[name] = cur.fetchone()[0]
    return out


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--save")
    g.add_argument("--since")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        now = snapshot(cur)
        conn.rollback()
    if a.save:
        with open(a.save, "w") as fh:
            json.dump(now, fh)
        return 0
    try:
        with open(a.since) as fh:
            was = json.load(fh)
    except (OSError, ValueError):
        print("  checkpoints: no start snapshot")
        return 0
    d = {k: now[k] - was[k] for k in now if isinstance(now[k], (int, float)) and k in was}
    if d.get("timed", 0) < 0 or d.get("requested", 0) < 0:
        print("  checkpoints: counters were reset during the run")
        return 0
    gb = d.get("wal_bytes", 0) / 2**30
    fpi = d.get("wal_fpi", 0)
    recs = d.get("wal_records", 0)
    print(f"  checkpoints this run: {d['requested']} requested (WAL size, or a CHECKPOINT command), {d['timed']} on the "
          f"timer; {d['write_ms'] / 1000:,.0f} s writing, {d['sync_ms'] / 1000:,.0f} s syncing "
          f"(max_wal_size {now['max_wal_size']}, checkpoint_timeout {now['checkpoint_timeout']}, "
          f"wal_compression {now['wal_compression']})")
    if gb:
        print(f"  WAL this run: {gb:,.1f} GB, {recs:,} records, {fpi:,} full-page images"
              + (f" ({100 * fpi / recs:.1f}% of records)" if recs else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
