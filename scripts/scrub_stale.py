# Project: xc-predictor / scripts
# File:    scrub_stale.py
# Purpose: Kill the stale-rating class found 2026-08-27 after the big
#          rebuild: wheelchair divisions and nuked/wrong-distance races
#          still showed ratings EVERYWHERE.
#
#     python scripts/scrub_stale.py            # report (read-only)
#     python scripts/scrub_stale.py --write    # scrub
#
# ★ THE MECHANISM. The backfill's copy-mode merge correctly NULLs
#   normalized_time for every skipped row (wheelchair, dropped division,
#   result-drop, dedup twin) -- but the merge carries speed_rating across
#   UNTOUCHED, and nothing anywhere clears a rating whose normalized time
#   died. Race pages display results.speed_rating directly and the board
#   build selects on it, so every nuked row kept its pre-nuke rating
#   through every rebuild. The guards gated NEW writes; the OLD values
#   were never scrubbed.
#
# ★ THE INVARIANT, now enforced here and (permanently) at fill_ratings:
#   NO NORMALIZED TIME => NO RATING.
#
# The belt passes also NULL both columns for the skip classes directly
# (wheelchair all three seams, dropped divisions, result drops, dedup
# twins), which covers update-mode backfills that never NULLed nt at all.

import argparse
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "backfill")

from database import getConn                          # noqa: E402
import psycopg2.extras                                # noqa: E402

_WHEEL = "wheelchair|seated|ambulator"


# Report mode runs the SAME updates and rolls back at the end -- the
# counts are exact, and nothing persists until --write commits.
def _step(cur, label, sql, params=(), write=False):
    cur.execute(sql, params)
    n = cur.rowcount
    print(f"    {n:>10,}  {label}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    w = args.write
    print(f"\n  STALE-RATING SCRUB ({'WRITING' if w else 'report only'}):\n")

    import corrections

    with getConn() as conn, conn.cursor() as cur:
        total = 0

        # ---- 1. THE INVARIANT: no normalized time => no rating --------- #
        for t in ("results", "results_tf"):
            total += _step(cur, f"{t}: rating with no normalized_time",
                           f"UPDATE {t} SET speed_rating = NULL "
                           f"WHERE normalized_time IS NULL "
                           f"AND speed_rating IS NOT NULL", write=w)

        # ---- 2. BELT: the skip classes, both columns ------------------- #
        # wheelchair, anet XC (division title on meets)
        total += _step(cur, "XC anet wheelchair divisions",
                       f"""UPDATE results SET normalized_time = NULL,
                               speed_rating = NULL
                           FROM meets m
                           WHERE m.div_id = results.div_id
                             AND m.meet_id = results.meet_id
                             AND m.source = results.source
                             AND m.division ~* '{_WHEEL}'
                             AND (results.normalized_time IS NOT NULL
                                  OR results.speed_rating IS NOT NULL)""",
                       write=w)
        # wheelchair, tfrrs XC (blob division titles)
        from backfill_normalize import _loadTfrrsBlobDistances
        blob = _loadTfrrsBlobDistances(cur)
        import re as _re
        wrx = _re.compile(_WHEEL, _re.IGNORECASE)
        wheel_pairs = [(m, d) for (m, d), info in blob.items()
                       if info.get("div_name") and wrx.search(info["div_name"])]
        if wheel_pairs:
            cur.execute("CREATE TEMP TABLE _wc(meet_id bigint, div_id bigint)")
            psycopg2.extras.execute_values(
                cur, "INSERT INTO _wc VALUES %s", wheel_pairs,
                page_size=5000)
            total += _step(cur, "XC tfrrs wheelchair divisions",
                           """UPDATE results SET normalized_time = NULL,
                                  speed_rating = NULL
                              FROM _wc
                              WHERE results.source = 'tfrrs'
                                AND results.meet_id = _wc.meet_id
                                AND results.div_id = _wc.div_id
                                AND (results.normalized_time IS NOT NULL
                                     OR results.speed_rating IS NOT NULL)""",
                           write=w)
        # wheelchair, TF (event name on the row)
        total += _step(cur, "TF wheelchair events",
                       f"""UPDATE results_tf SET normalized_time = NULL,
                               speed_rating = NULL
                           WHERE event_short ~* '{_WHEEL}'
                             AND (normalized_time IS NOT NULL
                                  OR speed_rating IS NOT NULL)""",
                       write=w)

        # dropped (nuked) divisions, per sport
        for sport, t in (("XC", "results"), ("TF", "results_tf")):
            drops = sorted(corrections._DISTANCE_DROP_BY_SPORT.get(sport,
                                                                   set()))
            if not drops:
                continue
            cur.execute("DROP TABLE IF EXISTS _dd")
            cur.execute("CREATE TEMP TABLE _dd(meet_id bigint, div_id bigint)")
            psycopg2.extras.execute_values(
                cur, "INSERT INTO _dd VALUES %s", drops, page_size=5000)
            total += _step(cur, f"{sport} nuked divisions "
                                f"({len(drops):,} divs)",
                           f"""UPDATE {t} SET normalized_time = NULL,
                                   speed_rating = NULL
                               FROM _dd
                               WHERE {t}.meet_id = _dd.meet_id
                                 AND {t}.div_id = _dd.div_id
                                 AND ({t}.normalized_time IS NOT NULL
                                      OR {t}.speed_rating IS NOT NULL)""",
                           write=w)

        # result drops (post-amnesty)
        for sport, t in (("XC", "results"), ("TF", "results_tf")):
            rids = sorted(corrections._RESULT_DROP_BY_SPORT.get(sport, set()))
            if not rids:
                continue
            cur.execute("DROP TABLE IF EXISTS _rd")
            cur.execute("CREATE TEMP TABLE _rd(result_id bigint PRIMARY KEY)")
            psycopg2.extras.execute_values(
                cur, "INSERT INTO _rd VALUES %s ON CONFLICT DO NOTHING",
                [(i,) for i in rids], page_size=10000)
            total += _step(cur, f"{sport} result drops ({len(rids):,} ids)",
                           f"""UPDATE {t} SET normalized_time = NULL,
                                   speed_rating = NULL
                               FROM _rd
                               WHERE {t}.result_id = _rd.result_id
                                 AND ({t}.normalized_time IS NOT NULL
                                      OR {t}.speed_rating IS NOT NULL)""",
                           write=w)

        # dedup twins: the tfrrs copy of a result that also exists on anet
        total += _step(cur, "XC dedup twins (tfrrs copies)",
                       """UPDATE results r SET normalized_time = NULL,
                              speed_rating = NULL
                          WHERE r.source = 'tfrrs'
                            AND r.person_id IS NOT NULL
                            AND r.canon_meet_id IS NOT NULL
                            AND (r.normalized_time IS NOT NULL
                                 OR r.speed_rating IS NOT NULL)
                            AND EXISTS (
                                SELECT 1 FROM results a
                                WHERE a.person_id = r.person_id
                                  AND a.canon_meet_id = r.canon_meet_id
                                  AND a.source = 'anet')""",
                       write=w)

        if w:
            conn.commit()
            print(f"\n  SCRUBBED {total:,} rows. Now rerun rankings + "
                  "boards:\n    .\\run_pipeline.ps1 -From 10")
        else:
            conn.rollback()
            print(f"\n  {total:,} rows would be scrubbed (exact -- same "
                  "updates, rolled back).\n  Rerun with --write.")


if __name__ == "__main__":
    main()
