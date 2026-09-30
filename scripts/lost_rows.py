#!/usr/bin/env python3
"""
lost_rows.py -- which rows sat on a table block that had to be zeroed, read
back from the table's own indexes, and (optionally) their meets re-queued.

    runuser -u postgres -- /srv/venv/bin/python scripts/lost_rows.py \\
        --dsn dbname=xc_predictor --table results_tf --block 491889
    ... --requeue tfrrs            # put their meets back in meet_queue (scraped = 0)

★ WHY (owner, 2026-09-30: "I'd prefer to chase so we can fix on next
  scrape"). A damaged heap block was zeroed (zero_damaged_pages + VACUUM),
  so its rows cannot be read from the table any more. The indexes were NOT
  rebuilt, and every index entry still holds the key it was made from plus
  the heap address (block, offset) it points at. So each btree index whose
  first column is an integer gives back that column for the lost rows:
  idx_results_tf_meet -> meet_id, the primary key -> result_id,
  idx_results_tf_person -> person_id. A re-scrape of those meets writes the
  rows again.

! RUN IT BEFORE ANY REINDEX of the table: a rebuilt index no longer has the
  entries. Needs the pageinspect extension (created if absent), so it runs
  as the postgres superuser. Read-only except --requeue.
"""
import argparse
import collections
import struct
import sys


def decodeKey(data_hex, typname):
    """The first key column of a btree tuple from bt_page_items' hex data."""
    raw = bytes(int(b, 16) for b in data_hex.split())
    if typname == "int8" and len(raw) >= 8:
        return struct.unpack("<q", raw[:8])[0]
    if typname == "int4" and len(raw) >= 4:
        return struct.unpack("<i", raw[:4])[0]
    if typname == "int2" and len(raw) >= 2:
        return struct.unpack("<h", raw[:2])[0]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default="dbname=xc_predictor")
    ap.add_argument("--table", default="results_tf")
    ap.add_argument("--block", type=int, required=True)
    ap.add_argument("--requeue", choices=("tfrrs", "anet"),
                    help="set these meets back to scraped = 0 in meet_queue for that feed")
    ap.add_argument("--sport", default=None, help="meet_queue sport (default: TF for results_tf, XC for results)")
    a = ap.parse_args()
    import psycopg2
    conn = psycopg2.connect(a.dsn)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS pageinspect")
    conn.commit()
    lo, hi = f"({a.block},0)", f"({a.block + 1},0)"

    # every valid btree index on the table, with its first column and type
    cur.execute("""
        SELECT i.indexrelid::regclass::text, att.attname, t.typname
        FROM   pg_index i
        JOIN   pg_class c  ON c.oid = i.indexrelid
        JOIN   pg_am am    ON am.oid = c.relam AND am.amname = 'btree'
        JOIN   pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = i.indkey[0]
        JOIN   pg_type t   ON t.oid = att.atttypid
        WHERE  i.indrelid = %s::regclass AND i.indisvalid
          AND  t.typname IN ('int2', 'int4', 'int8')""", (a.table,))
    indexes = cur.fetchall()
    if not indexes:
        sys.exit(f"no valid integer-keyed btree index on {a.table}")
    print(f"[lost] {a.table} block {a.block}: reading {len(indexes)} index(es)")

    found = {}
    for idx, col, typ in indexes:
        if col in found:
            continue
        cur.execute("SELECT pg_relation_size(%s) / current_setting('block_size')::int", (idx,))
        npages = cur.fetchone()[0]
        # leaf pages only; a posting-list tuple (deduplication) carries its
        # heap addresses in `tids`, a plain one in `htid`
        cur.execute(f"""
            SELECT p.data
            FROM   generate_series(1, %s - 1) b
            CROSS  JOIN LATERAL bt_page_stats(%s, b::int) s
            CROSS  JOIN LATERAL bt_page_items(%s, b::int) p
            WHERE  s.type = 'l'
              AND  EXISTS (SELECT 1 FROM unnest(COALESCE(p.tids, ARRAY[p.htid])) t
                           WHERE t >= %s::tid AND t < %s::tid)""",
                    (npages, idx, idx, lo, hi))
        vals = collections.Counter(decodeKey(r[0], typ) for r in cur.fetchall())
        found[col] = vals
        print(f"  {idx} ({col}, {npages:,} pages): {sum(vals.values())} entries point "
              f"at the block, {len(vals)} distinct {col}")
        conn.rollback()

    for col, vals in found.items():
        shown = ", ".join(f"{v}" + (f" x{n}" if n > 1 else "") for v, n in sorted(
            vals.items(), key=lambda kv: (kv[0] is None, kv[0])))
        print(f"\n  {col}: {shown}")

    meets = sorted(v for v in found.get("meet_id", {}) if v is not None)
    if a.requeue and meets:
        sport = a.sport or ("TF" if a.table == "results_tf" else "XC")
        cur.execute("""
            INSERT INTO meet_queue (meet_id, sport, source, scraped)
            SELECT m, %s, %s, 0 FROM unnest(%s::bigint[]) m
            ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0""",
                    (sport, a.requeue, meets))
        conn.commit()
        print(f"\n[lost] {len(meets)} {a.requeue} {sport} meets set back to scraped = 0 "
              f"in meet_queue: the next scrape rewrites their rows")
    elif meets:
        print(f"\n[lost] {len(meets)} meets. --requeue tfrrs (or anet) puts them back in "
              f"the scrape queue. Negative result_ids above are tfrrs rows.")


if __name__ == "__main__":
    main()
