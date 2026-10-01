#!/usr/bin/env python3
"""
integrity_check.py -- read every page of every table and say whether any is
damaged (Postgres' amcheck, verify_heapam). Exit 1 if one is.

    /srv/venv/bin/python scripts/integrity_check.py              # every table
    /srv/venv/bin/python scripts/integrity_check.py --tables results_tf,results

★ WHY (owner, 2026-09-30: "THis is the 2nd time I've had data get messed up
  on this server"). Block 491889 of results_tf went bad, and nothing said so
  until fifteen pipeline steps failed on DataCorrupted four hours in, and the
  backup's pg_dump failed on the same page. The server's RAM has no error
  correction and the kernel logged machine-check errors, so it can happen
  again. This reads every page once, in the pipeline's first step: a bad
  block stops the run BEFORE anything reads it (a damaged row is either an
  error or, worse, silently wrong), and says which table and which block.

★ ONE SEGMENT AT A TIME. Postgres keeps a table in 1 GB segment files; each
  is checked as its own statement (startblock/endblock), which prints
  progress on the big tables and keeps each statement short.

READ-ONLY (AccessShareLock, the lock a SELECT takes -- the site keeps
serving). Needs the amcheck extension (created if absent) and a superuser
or a role granted EXECUTE on verify_heapam; the pipeline's is postgres.

IF IT FAILS -- the recipe that worked on 2026-09-30 (docs/ISSUES-RUNNING.md):
  1. BEFORE any REINDEX, which destroys the evidence: which rows were on
     the block, from the indexes, and their meets back in the scrape queue:
       runuser -u postgres -- /srv/venv/bin/python scripts/lost_rows.py \\
           --table <table> --block <block> --requeue tfrrs
  2. rewrite the table without the damaged page (locks the table):
       runuser -u postgres -- psql -d xc_predictor -c \\
           "SET zero_damaged_pages = on; VACUUM FULL <table>"
  3. a plain VACUUM (ANALYZE) of the same table -- the site keeps serving:
       runuser -u postgres -- psql -d xc_predictor -c "VACUUM (ANALYZE) <table>"
     ! NOT OPTIONAL (2026-10-01). VACUUM FULL leaves the rewritten table
       with an empty visibility map, and every query that would have
       answered from an index alone visits the table row by row instead:
       pipeline step 01a ran 3 hours and never finished, twice. One plain
       VACUUM rebuilds the map, ANALYZE the planner's statistics.
  4. this script again: 0 problems.
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                  # noqa: E402

# the damaged blocks listed per table; the count is always the whole count
_SHOW = 20


def tablesToCheck(cur, only):
    """(name, pages) of every table with storage, biggest first, or of the
    named ones. Partitioned parents hold no pages; their partitions are
    tables of their own and are listed."""
    cur.execute("""
        SELECT c.oid::regclass::text, pg_relation_size(c.oid) / current_setting('block_size')::int
        FROM   pg_class c
        JOIN   pg_namespace n ON n.oid = c.relnamespace
        WHERE  c.relkind IN ('r', 'm')
          AND  c.relpersistence <> 't'
          AND  n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND  n.nspname NOT LIKE 'pg_toast%%'
        ORDER  BY 2 DESC, 1""")
    rows = cur.fetchall()
    if only:
        want = {t.strip() for t in only.split(",") if t.strip()}
        rows = [r for r in rows if r[0] in want or r[0].split(".")[-1] in want]
        missing = want - {r[0] for r in rows} - {r[0].split(".")[-1] for r in rows}
        if missing:
            sys.exit(f"[integrity] no such table: {', '.join(sorted(missing))}")
    return rows


def checkRange(cur, name, start, end):
    """[(block, offset, column, message)] for blocks start..end of a table.

    ★ A PAGE TOO BROKEN TO READ IS AN ERROR, NOT A ROW. verify_heapam
      reports a bad tuple as a row, but a page whose header is garbage
      ("invalid page in block 491889", 2026-09-30) or whose tuples name
      transactions that never existed stops the whole statement. So a
      range that errors is split in half and each half checked again, down
      to the one block; that block is the problem, with the error as its
      message, and the rest of the table is still checked."""
    import psycopg2
    try:
        # check_toast: a value stored out of line is read too;
        # on_error_stop off: every problem on the table, not the first
        cur.execute("""
            SELECT blkno, offnum, attnum, msg
            FROM   verify_heapam(%s::regclass, on_error_stop := false,
                                 check_toast := true, skip := 'none',
                                 startblock := %s, endblock := %s)""",
                    (name, start, end))
        return cur.fetchall()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        raise                                   # the connection, not the data
    except psycopg2.DatabaseError as exc:
        if start == end:
            msg = (exc.pgerror or str(exc)).strip().splitlines()[0]
            return [(start, None, None, msg.removeprefix("ERROR:").strip())]
    mid = (start + end) // 2
    return checkRange(cur, name, start, mid) + checkRange(cur, name, mid + 1, end)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", default="", help="comma-separated; default every table")
    a = ap.parse_args()

    t_all = time.time()
    with getConn() as conn:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS amcheck")
        cur.execute("SELECT current_setting('block_size')::bigint,"
                    " pg_size_bytes(current_setting('segment_size'))")
        bsz, seg_bytes = cur.fetchone()
        seg = seg_bytes // bsz
        tables = tablesToCheck(cur, a.tables)
        total_pages = sum(p for _t, p in tables)
        print(f"[integrity] {len(tables)} tables, {total_pages:,} pages "
              f"({total_pages * bsz / 2**30:,.1f} GB), {seg:,} pages a segment")

        bad = {}                     # table -> {block: [count, first message]}
        for name, pages in tables:
            if pages == 0:
                continue
            t0 = time.time()
            blocks = {}
            for start in range(0, pages, seg):
                end = min(start + seg, pages) - 1
                for blk, off, att, msg in checkRange(cur, name, start, end):
                    first = blocks.setdefault(blk, [0, None])
                    first[0] += 1
                    if first[1] is None:
                        first[1] = ((f"offset {off} " if off is not None else "")
                                    + (f"column {att} " if att is not None else "") + msg)
                if pages > seg:
                    print(f"  {name}: {end + 1:,} / {pages:,} pages"
                          + (f"   {len(blocks)} DAMAGED BLOCKS so far" if blocks else ""),
                          flush=True)
            el = time.time() - t0
            if blocks:
                bad[name] = blocks
                n = sum(c for c, _m in blocks.values())
                print(f"  ✗ {name}: {len(blocks)} damaged block(s), {n} problem(s) "
                      f"({pages:,} pages, {el:.0f}s)")
                for blk in sorted(blocks)[:_SHOW]:
                    c, msg = blocks[blk]
                    print(f"      block {blk}: {c} problem(s), first: {msg}")
                if len(blocks) > _SHOW:
                    print(f"      ... {len(blocks) - _SHOW} more blocks")
            elif pages > seg or el >= 1:
                print(f"  ✓ {name} ({pages:,} pages, {el:.0f}s)")

    el = time.time() - t_all
    if bad:
        print("\n[integrity] ✗ DAMAGED: "
              + ", ".join(f"{t} ({len(b)} block(s))" for t, b in bad.items()) + f". {el:.0f}s.")
        print("  Do NOT reindex first -- the indexes are the only record of what was on"
              " these blocks.\n  1. what was lost, and its meets back in the scrape queue"
              " (drop --requeue to only look):")
        for t, b in bad.items():
            for blk in sorted(b)[:_SHOW]:
                print(f"       runuser -u postgres -- /srv/venv/bin/python scripts/lost_rows.py"
                      f" --table {t} --block {blk} --requeue tfrrs")
        print("  2. rewrite each table without the damaged pages (locks the table):")
        for t in bad:
            print(f"       runuser -u postgres -- psql -d xc_predictor -c"
                  f" \"SET zero_damaged_pages = on; VACUUM FULL {t}\"")
        print("  3. then a plain vacuum of each, or later steps crawl (the site keeps serving):")
        for t in bad:
            print(f"       runuser -u postgres -- psql -d xc_predictor -c"
                  f" \"VACUUM (ANALYZE) {t}\"")
        print("  4. this script again, then the pipeline.")
        sys.exit(1)
    print(f"\n[integrity] ✓ every page of {len(tables)} tables reads clean ({el:.0f}s)")

if __name__ == "__main__":
    main()
