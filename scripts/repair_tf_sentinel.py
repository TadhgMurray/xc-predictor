# Project: xc-predictor / scripts
# File:    repair_tf_sentinel.py
# Purpose: Clear anet TF's non-finish sentinel -- stored as a 20,000-second
#          "time" -- out of results_tf.
#
#     python scripts/repair_tf_sentinel.py            # dry run: counts only
#     python scripts/repair_tf_sentinel.py --apply    # clear them
#
# Run from the PROJECT ROOT.
#
# ⚠ WHAT WENT WRONG (owner, 2026-09-25). anet TF sends SortInt in milliseconds
#   and writes a non-finish as 20,000,000. Both savers kept anything under
#   100,000,000, so a DNF became 20,000 s: "5:33:20" on a Diamond League mile,
#   PR/SR flags, a 1.7 rating, and a five-and-a-half-hour race in the fits.
#   The savers now know (result_status.timeFromSortInt); this clears the rows
#   already stored.
#
# ★ WHAT A ROW BECOMES. What the savers now write for a non-finish:
#   time_seconds NULL, mark = its status letters (the column the race page
#   reads), and no normalized_time or speed_rating. Where anet sent no letters
#   (status NULL) the mark is 'DNF' -- it started the heat list and has no
#   time; the feed did not say which kind of non-finish.
#
# ! REVERSIBLE. Every cleared row's old values go to tf_sentinel_repair first,
#   in the same transaction as its batch.
#
# ! Only the rows move -- the season tables, boards and ratings built from
#   them catch up on the next pipeline run (the backfill now skips the
#   sentinel too, so they do not come back).

import sys
import time
import argparse

sys.path.insert(0, "scripts")
from database import getConn                       # noqa: E402
from result_status import TF_SENTINEL              # noqa: E402

BATCH = 5000


def _columns(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = %s AND table_schema = current_schema()""",
                (table,))
    return {r[0] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="clear the rows (default: count them only)")
    ap.add_argument("--value", type=float, default=TF_SENTINEL,
                    help="the stored sentinel to clear (default %(default)g); "
                         "the dry run lists the candidates")
    ap.add_argument("--meet", type=int,
                    help="also print every running row over an hour at "
                         "this meet_id -- the race you saw it on")
    args = ap.parse_args()
    value = args.value

    with getConn() as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "results_tf")
            status = "status" if "status" in cols else "NULL::text"
            # ⚠ FIRST RUN FOUND NOTHING AT 20,000 (owner, 2026-09-25), though a
            #   page showed 5:33:20. So the dry run says what IS there: every
            #   running "time" over two hours, grouped by value. A sentinel is
            #   one value repeated thousands of times; a real slow race is not.
            if not args.apply:
                t0 = time.time()
                print("[sentinel] running rows over 2 hours, by value "
                      "(a full scan):", flush=True)
                cur.execute(f"""
                    SELECT time_seconds::float8, COALESCE(source, '?'),
                           COALESCE({status}, '(none)'), count(*),
                           count(speed_rating)
                    FROM   results_tf
                    WHERE  time_seconds > 7200
                      AND  COALESCE(is_field::int, 0) = 0
                    GROUP  BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25
                """)
                print(f"  {'time_seconds':>14} {'source':<7} {'status':<8} "
                      f"{'rows':>9} {'rated':>8}")
                for t, src, st, n, rated in cur.fetchall():
                    print(f"  {t:>14.3f} {src:<7} {st:<8} {n:>9,} {rated:>8,}")
                print(f"  ({time.time() - t0:.0f}s)")
                if args.meet:
                    cur.execute(f"""
                        SELECT result_id, event_id, div_id, source,
                               time_seconds::float8, mark, {status},
                               speed_rating
                        FROM   results_tf
                        WHERE  meet_id = %s AND time_seconds > 3600
                    """, (args.meet,))
                    print(f"[sentinel] meet {args.meet}, rows over an hour:")
                    for row in cur.fetchall():
                        print("  ", row)
            t0 = time.time()
            print(f"[sentinel] scanning results_tf for time_seconds = "
                  f"{value:g} (a full scan -- minutes on the whole table)",
                  flush=True)
            cur.execute(f"""
                SELECT result_id FROM results_tf
                WHERE  time_seconds = %s AND COALESCE(is_field::int, 0) = 0
            """, (value,))
            ids = [r[0] for r in cur.fetchall()]
            print(f"[sentinel] {len(ids):,} rows ({time.time() - t0:.0f}s)")
            if not ids:
                return

            cur.execute(f"""
                SELECT COALESCE(source, '?'), COALESCE({status}, '(none)'),
                       left(date::text, 4), count(*),
                       count(speed_rating)
                FROM   results_tf WHERE result_id = ANY(%s)
                GROUP  BY 1, 2, 3 ORDER BY 3 DESC, 4 DESC LIMIT 40
            """, (ids,))
            print(f"  {'source':<7} {'status':<8} {'year':<5} {'rows':>9} {'rated':>9}")
            for src, st, yr, n, rated in cur.fetchall():
                print(f"  {src:<7} {st:<8} {yr or '?':<5} {n:>9,} {rated:>9,}")

            if not args.apply:
                print("[sentinel] dry run -- nothing changed; --apply clears them")
                return

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tf_sentinel_repair (
                    result_id       bigint PRIMARY KEY,
                    time_seconds    real,
                    mark            text,
                    normalized_time real,
                    speed_rating    real,
                    repaired_at     timestamptz DEFAULT now())
            """)
            conn.commit()

            norm = "normalized_time" if "normalized_time" in cols else "NULL::real"
            set_norm = ", normalized_time = NULL" if "normalized_time" in cols else ""
            done = 0
            for i in range(0, len(ids), BATCH):
                chunk = ids[i:i + BATCH]
                cur.execute(f"""
                    INSERT INTO tf_sentinel_repair
                           (result_id, time_seconds, mark, normalized_time,
                            speed_rating)
                    SELECT result_id, time_seconds, mark, {norm}, speed_rating
                    FROM   results_tf
                    WHERE  result_id = ANY(%s) AND time_seconds = %s
                    ON CONFLICT (result_id) DO NOTHING
                """, (chunk, value))
                cur.execute(f"""
                    UPDATE results_tf
                    SET    time_seconds = NULL,
                           mark         = COALESCE(mark, {status}, 'DNF'),
                           speed_rating = NULL{set_norm}
                    WHERE  result_id = ANY(%s) AND time_seconds = %s
                """, (chunk, value))
                done += cur.rowcount
                conn.commit()
                print(f"[sentinel] cleared {done:,} / {len(ids):,}", flush=True)
            print(f"[sentinel] done in {time.time() - t0:.0f}s; old values in "
                  f"tf_sentinel_repair")


if __name__ == "__main__":
    main()
