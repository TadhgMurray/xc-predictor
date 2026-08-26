# Project: xc-predictor / scripts
# File:    cull_db.py
# Purpose: Shrink the database before the Hetzner dump. Report-first:
#          every table lands in one of three buckets with its size, and
#          nothing is dropped until --drop --yes.
#
#     python scripts/cull_db.py                # the audit (read-only)
#     python scripts/cull_db.py --drop         # shows the DROP plan only
#     python scripts/cull_db.py --drop --yes   # executes it
#
# ★ THE DUMP DOES NOT NEED VACUUM. pg_dump reads live rows only, so
#   VACUUM FULL / pg_repack shrink LOCAL disk, not the transfer. Dropping
#   junk tables is what shrinks the upload; vacuum afterwards only if the
#   local box needs the space back.
#
# Buckets:
#   PRODUCTION  known site/pipeline tables -- never touched.
#   DROPPABLE   known regenerable/staging patterns (the SITE_NOTES list:
#               *_old undo copies, reb_* / ovr_shift staging, *_new shadow
#               leftovers, speed-rating backups). --drop targets ONLY these.
#   REVIEW      everything else -- needs eyes; listed with sizes, largest
#               first, so the 200 GB question answers itself.

import argparse
import re
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                          # noqa: E402

# The site + pipeline surface. A production table missing from this list
# costs nothing (it lands in REVIEW, never in DROPPABLE).
_PRODUCTION = {
    "results", "results_tf", "meets", "meets_tf", "meets_tfrrs",
    "athletes", "ranking_results", "athlete_season", "team_season",
    "school_identity", "person_home_state", "grade_fix",
    "athlete_season_level", "pro_athlete_season", "college_first_season",
    "dist_override", "dist_drop", "dist_override_snap",
    "course_canonical", "course_difficulties", "course_boards",
    "athlete_ratings", "tfrrs_meet_geometry", "meet_extras",
    "weather_grid", "person_split", "reports",
}

# Regenerable / staging / undo patterns, from SITE_NOTES' cleanup plan.
# ⚠ PATTERNS ARE DELIBERATELY NARROW. A pattern that might catch a real
#   table belongs in REVIEW, not here.
_DROP_RX = [
    (re.compile(r"^.*_old$"), "undo copy (the ALS engine recreates these; "
                              "02_drop_old clears them every run)"),
    (re.compile(r"^.*_new$"), "shadow-build leftover (a finished swap "
                              "renames these away; one lying around is a "
                              "crashed build)"),
    (re.compile(r"^reb_.*$"), "rebuild staging"),
    (re.compile(r"^ovr_shift.*$"), "override-shift staging"),
    (re.compile(r"^results_speed_rating.*(bak|backup|old|copy).*$"),
     "speed-rating backup"),
    (re.compile(r"^.*_bak(_.*)?$"), "explicit backup"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true",
                    help="plan (and with --yes execute) drops of the "
                         "DROPPABLE bucket")
    ap.add_argument("--yes", action="store_true",
                    help="actually execute the drops")
    args = ap.parse_args()

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname,
                   pg_total_relation_size(c.oid) AS bytes
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'm')
            ORDER BY bytes DESC""")
        tables = cur.fetchall()

        prod, droppable, review = [], [], []
        for name, size in tables:
            if name in _PRODUCTION:
                prod.append((name, size))
                continue
            why = next((w for rx, w in _DROP_RX if rx.match(name)), None)
            (droppable.append((name, size, why)) if why
             else review.append((name, size)))

        def gb(b):
            return f"{b / 1e9:8.2f} GB"

        total = sum(s for _, s in tables)
        print(f"\n  {len(tables)} tables, {total / 1e9:,.1f} GB total\n")

        print(f"  PRODUCTION ({sum(s for _, s in prod) / 1e9:,.1f} GB) -- "
              "never touched:")
        for n, s in prod:
            print(f"    {gb(s)}  {n}")

        print(f"\n  DROPPABLE ({sum(s for _, s, _ in droppable) / 1e9:,.1f} "
              "GB) -- known regenerable/staging:")
        if not droppable:
            print("    (none)")
        for n, s, w in droppable:
            print(f"    {gb(s)}  {n}   [{w}]")

        print(f"\n  REVIEW ({sum(s for _, s in review) / 1e9:,.1f} GB) -- "
              "needs your eyes before anything happens to it:")
        for n, s in review:
            print(f"    {gb(s)}  {n}")

        if args.drop and droppable:
            print("\n  ⚠ RUN ONLY WITH THE PIPELINE IDLE: a *_new table is "
                  "junk after a crash\n    but LIVE during a build -- "
                  "dropping it mid-run kills the shadow swap.")
            print("\n  DROP PLAN:")
            for n, s, _ in droppable:
                print(f"    DROP TABLE {n};   -- frees {gb(s).strip()}")
            if not args.yes:
                print("\n  plan only -- rerun with --drop --yes to execute.")
            else:
                for n, _, _ in droppable:
                    cur.execute(f'DROP TABLE IF EXISTS "{n}" CASCADE')
                    print(f"    dropped {n}")
                conn.commit()
                print(f"\n  DONE: {sum(s for _, s, _ in droppable) / 1e9:,.1f}"
                      " GB freed from the dump.")

    print("\n  THEN: pg_dump -Fc (custom format, compressed) for the "
          "upload. VACUUM FULL\n  is optional and only reclaims LOCAL "
          "disk -- the dump is already small.")


if __name__ == "__main__":
    main()
