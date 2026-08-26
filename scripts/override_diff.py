# Project: xc-predictor / scripts
# File:    override_diff.py
# Purpose: Name what a rebuild did to dist_override -- above all, what it LOST.
#
#     python scripts/override_diff.py --snapshot   # before the wipe/apply
#     python scripts/override_diff.py --report     # after dump_overrides
#
# ★ THE WIPE TRUSTS THE PASSES TO RE-PROPOSE WHAT WAS RIGHT, AND NOTHING
#   CHECKED. A -Reset run clears every regenerable override and judges the
#   corpus as if they were never written; a correct override whose division
#   the passes cannot see -- pass 0's own subject, a division whose evidence
#   deleted itself -- simply vanishes, and nothing anywhere says so. The
#   failure surfaces WEEKS later as a race page full of dashes: NLC Round #1
#   2025 at Ox Bow Park was page-verified to 5000 in July, wiped in the
#   2026-08-25 reset, re-proposed by nothing, and found by the owner on a
#   broken page. This tool exists so that class of loss is a NAMED LIST in
#   the night's log instead of a discovery.
#
# ⚠ REPORT ONLY, NEVER A GATE (check_override_coherence's rule). Overrides
#   legitimately churn in a reset -- pooled estimates move a rung, drops
#   supersede distances -- so a nonzero diff is a prompt to read, not a
#   fault. Exit code is 0 in every path; run_overrides must not die here.
#
# ! THE SNAPSHOT IS A REAL TABLE, NOT A TEMP. --snapshot and --report run in
#   different processes an hour apart; dist_override_snap persists between
#   them and is overwritten by the next --snapshot. It is one small table
#   (~5k rows) and never read by anything else.

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from database import getConn                          # noqa: E402

SNAP = "dist_override_snap"

# How many lost/moved rows to print in full. Everything is COUNTED either
# way; the cap only bounds the table, because a catastrophic wipe could
# lose thousands and the log still has to be readable.
SHOW = 40


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


def snapshot():
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {SNAP}")
        if _exists(cur, "dist_override"):
            cur.execute(f"CREATE TABLE {SNAP} AS SELECT * FROM dist_override")
            cur.execute(f"SELECT count(*) FROM {SNAP}")
            print(f"  snapshot: {cur.fetchone()[0]:,} dist_override rows "
                  f"copied to {SNAP}")
        else:
            # First-ever run: nothing to lose yet, but the report step still
            # needs a table to compare against.
            cur.execute(f"""CREATE TABLE {SNAP} (
                                meet_id bigint, div_id bigint,
                                distance double precision, dict_name text)""")
            print("  snapshot: dist_override does not exist yet; empty "
                  "snapshot written")
        conn.commit()


# One LATERAL per lost key. Lost sets should be near-empty -- that is the
# point of the tool -- and (meet_id, div_id) is indexed on both results
# tables, so even a bad night's few thousand keys stay minutes, not hours.
_LOST_SQL = """
    SELECT l.meet_id, l.div_id, l.distance AS was, l.dict_name,
           COALESCE(m.distance, x.distance, tf.distance_meters) AS stored,
           COALESCE(m.course_name, mt.venue_name, tf.meet_name) AS name,
           -- ! GREATEST, not COALESCE: a lateral count is 0, never NULL,
           --   so a TF division's rows would otherwise print as 0.
           GREATEST(rx.n, rt.n)                    AS n_rows,
           COALESCE(rx.nr, 0)                      AS n_rated,
           COALESCE(rx.d,  rt.d)                   AS date
    FROM   tmp_ovr_lost l
    LEFT   JOIN LATERAL (
        SELECT count(*) AS n, count(speed_rating) AS nr, min(date) AS d
        FROM   results r
        WHERE  r.meet_id = l.meet_id AND r.div_id = l.div_id
    ) rx ON TRUE
    LEFT   JOIN LATERAL (
        SELECT count(*) AS n, min(date) AS d
        FROM   results_tf r
        WHERE  r.meet_id = l.meet_id AND r.div_id = l.div_id
    ) rt ON TRUE
    LEFT   JOIN LATERAL (
        SELECT course_name, distance FROM meets
        WHERE  meets.meet_id = l.meet_id AND meets.div_id = l.div_id LIMIT 1
    ) m ON TRUE
    LEFT   JOIN LATERAL (
        SELECT distance FROM tmp_xc_tfrrs_dist t
        WHERE  t.meet_id = l.meet_id AND t.div_id = l.div_id LIMIT 1
    ) x ON TRUE
    LEFT   JOIN LATERAL (
        SELECT venue_name FROM meets_tfrrs
        WHERE  meets_tfrrs.meet_id = l.meet_id LIMIT 1
    ) mt ON TRUE
    LEFT   JOIN LATERAL (
        SELECT meet_name, distance_meters FROM meets_tf
        WHERE  meets_tf.meet_id = l.meet_id AND meets_tf.div_id = l.div_id
        LIMIT  1
    ) tf ON TRUE
    ORDER  BY GREATEST(rx.n, rt.n) DESC, l.meet_id, l.div_id
"""


def report():
    t0 = time.time()
    with getConn() as conn, conn.cursor() as cur:
        if not _exists(cur, SNAP):
            print("  no snapshot table -- run --snapshot before the "
                  "wipe/apply. Nothing to compare; skipping.")
            return
        if not _exists(cur, "dist_override"):
            print("  dist_override does not exist -- dump_overrides has not "
                  "run. Nothing to compare; skipping.")
            return

        cur.execute(f"""
            SELECT (SELECT count(*) FROM {SNAP}),
                   (SELECT count(*) FROM dist_override),
                   (SELECT count(*) FROM dist_override d
                    WHERE NOT EXISTS (SELECT 1 FROM {SNAP} s
                                      WHERE s.meet_id = d.meet_id
                                        AND s.div_id  = d.div_id)),
                   (SELECT count(*) FROM {SNAP} s
                    JOIN dist_override d ON d.meet_id = s.meet_id
                                        AND d.div_id  = s.div_id
                    WHERE round(s.distance::numeric, 1)
                       <> round(d.distance::numeric, 1))
        """)
        before, after, gained, moved = cur.fetchone()

        cur.execute(f"""
            DROP TABLE IF EXISTS tmp_ovr_lost;
            CREATE TEMP TABLE tmp_ovr_lost AS
                SELECT s.* FROM {SNAP} s
                WHERE NOT EXISTS (SELECT 1 FROM dist_override d
                                  WHERE d.meet_id = s.meet_id
                                    AND d.div_id  = s.div_id)
        """)
        cur.execute("SELECT count(*) FROM tmp_ovr_lost")
        lost = cur.fetchone()[0]

        print(f"  dist_override vs snapshot: {before:,} before -> "
              f"{after:,} after   (gained {gained:,}, moved {moved:,}, "
              f"lost {lost:,})")

        if moved:
            cur.execute(f"""
                SELECT s.meet_id, s.div_id, s.distance, d.distance
                FROM   {SNAP} s
                JOIN   dist_override d ON d.meet_id = s.meet_id
                                      AND d.div_id  = s.div_id
                WHERE  round(s.distance::numeric, 1)
                    <> round(d.distance::numeric, 1)
                ORDER  BY abs(ln(d.distance / NULLIF(s.distance, 0))) DESC
                LIMIT  %s
            """, (SHOW,))
            rows = cur.fetchall()
            print(f"\n  moved (top {len(rows)} by ratio; churn is normal in "
                  "a reset, big jumps deserve a look):")
            for meet, div, was, now in rows:
                print(f"    {meet}/{div:<10} {was:>7.0f} -> {now:<7.0f} "
                      f"(x{now / was:.2f})")

        if not lost:
            print("  lost: none -- every snapshotted override survived or "
                  "was re-proposed.")
            print(f"  done in {time.time() - t0:.0f}s")
            return

        # The stored label needs the tfrrs blob flattened; same temp table
        # every other tool here uses. Imported HERE so --snapshot never
        # depends on the ranking builder's import chain.
        from build_ranking_results import _XC_TFRRS_DIST_SQL
        cur.execute(_XC_TFRRS_DIST_SQL)
        cur.execute(_LOST_SQL)
        rows = cur.fetchall()

        # A lost override that MATCHED the stored value changed nothing:
        # the division normalizes to the same number with or without it.
        real, harmless = [], []
        for r in rows:
            was, stored = r[2], r[4]
            same = stored is not None and abs(float(stored) - float(was)) < 1
            (harmless if same else real).append(r)

        if harmless:
            n = len(harmless)
            print(f"\n  {n} lost entr{'y' if n == 1 else 'ies'} matched the "
                  "stored distance anyway -- nothing changes there.")
        if real:
            print(f"\n  WARNING: {len(real)} OVERRIDES LOST. These divisions "
                  "now normalize to a distance a")
            print("  correction said was wrong, and the passes did not "
                  "re-propose them. Restore by")
            print("  hand (paste into _DISTANCE_OVERRIDES_XC in "
                  "engine/corrections.py, rerun")
            print("  engine/dump_overrides.py) or confirm each was stale.\n")
            print(f"    {'meet/div':<16}{'was':>7}{'stored':>8}{'rows':>7}"
                  f"{'rated':>7}  name / date")
            for r in real[:SHOW]:
                meet, div, was, _dict, stored, name, n, nr, date = r
                st = f"{stored:.0f}" if stored is not None else "NONE"
                # ! A NULL stored distance means the override was the ONLY
                #   distance the division had: without it the backfill writes
                #   no normalized_time at all. keep-sole-source exists to
                #   prevent exactly this; seeing one here means it failed.
                tag = "  SOLE SOURCE" if stored is None else ""
                print(f"    {f'{meet}/{div}':<16}{was:>7.0f}{st:>8}{n:>7,}"
                      f"{nr:>7,}  {name or '?'} {date or ''}{tag}")
            if len(real) > SHOW:
                print(f"    ... and {len(real) - SHOW} more")
            print("\n  paste-ready:")
            for r in real[:SHOW]:
                meet, div, was = r[0], r[1], r[2]
                print(f"    ({meet}, {div}): {was:g},")
    print(f"  done in {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", action="store_true",
                   help="copy dist_override aside before the wipe/apply")
    g.add_argument("--report", action="store_true",
                   help="diff dist_override against the snapshot")
    args = ap.parse_args()
    if args.snapshot:
        snapshot()
    else:
        report()


if __name__ == "__main__":
    main()
