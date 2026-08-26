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
# Extended 2026-08-27 from the 477 GB audit: every REVIEW table was grepped
# against the codebase; the ones the SITE serves from, the scrapers queue
# in, or a step READS without rebuilding moved here.
_PRODUCTION = {
    "results", "results_tf", "meets", "meets_tf", "meets_tfrrs",
    "athletes", "ranking_results", "athlete_season", "team_season",
    "school_identity", "person_home_state", "grade_fix",
    "athlete_season_level", "pro_athlete_season", "college_first_season",
    "dist_override", "dist_drop", "dist_override_snap",
    "course_canonical", "course_difficulties", "course_boards",
    "athlete_ratings", "tfrrs_meet_geometry", "meet_extras",
    "weather_grid", "person_split", "reports",
    # site-serving (app/panels/search/predict read these)
    "search_index", "athlete_agg", "athlete_named", "athlete_school",
    "meet_agg_xc", "course_distances", "course_rank",
    "course_common_distance", "homepage_panels", "homepage_recent",
    "homepage_meta", "div_distance", "pool_means", "weather",
    "grade_untrusted", "upperclass_first_season",
    # scraper queues + recovery state (losing these loses scrape progress)
    "meet_queue", "tf_recovery_queue", "tf_scraped_events",
    "meets_tf_meta",
    # identity/link provenance the dedup and merge tooling reads
    "entity_links", "athlete_meet_match", "fanout_resolutions",
    # step INPUTS not rebuilt by the step that reads them
    "person_baseline", "ncaa_first_season", "school_level_graph",
}

# ★ REBUILT BY A PIPELINE STEP (verified DROP/CREATE in the named file).
#   Excluded from the DUMP -- the server's first full run recreates them --
#   but NOT dropped locally: tonight's local pipeline may still read them.
_REGENERABLE = {
    "allraces": "grade_sanity (04)",
    "gradekind": "grade_sanity (04)",
    "gradenorm": "grade_sanity (04)",
    "barefix": "grade_sanity (04)",
    "pair_result_rating": "pair_write_results (08)",
    "pair_athlete": "pair_write (08)",
    "pair_athlete_season": "pair_write (08)",
    "pair_course_difficulty": "pair_write (08)",
    "results_tf_speed_rating_backup": "pair_golive (08)",
    "results_rating_pretilt": "apply_tilt (09)",
    "venue_lift": "venue_slope",
    "slope_obs": "slope_fit",
    "cell_slope": "slope_fit",
    "loc_exp": "location_merge", "loc_attrs": "location_merge",
    "loc_pts": "location_merge", "loc_pair": "location_merge",
    "loc_label": "location_merge", "loc_canon": "location_merge",
    "loc_edge": "location_merge", "loc_merge_pair": "location_merge",
    "venue_years": "location_merge",
    "course_pts": "course_merge", "course_canon": "course_merge",
    "meet_team_index": "level_graph", "race_level": "season_level",
    "ovr_ratings": "audit_overrides",
}

# ⚠ DEAD: referenced NOWHERE in current code (grep over racecast/, engine/,
#   backfill/, scripts/, model/ on 2026-08-27). Old vintages of long-renamed
#   machinery: pre-dedup undo snapshots, superseded grade/gender passes.
#   Droppable AND dump-excluded.
_DEAD = {
    "results_tf_predup", "results_tf_predup2", "results_predup",
    "racekinds", "grade_obs", "node_obs", "grade_vote", "grade_vote2",
    "person_year_level", "athlete_years", "person_races",
    "season_baseline", "athlete_state", "race_grade_level",
    "rating_shift", "implied_dist", "school_state", "school_level",
    "grade_fix2", "cd_pre_formv2", "course_difficulties_prefix",
    "div_inferred", "athletes_gender_bak3", "gender_fix", "gender_fix2",
    "gender_fix3", "schools_seen", "meets_raincourse_bak2",
    "tf_event_map", "dist_override_keys",
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

        prod, droppable, regen, review = [], [], [], []
        for name, size in tables:
            if name in _PRODUCTION:
                prod.append((name, size))
                continue
            if name in _DEAD:
                droppable.append((name, size,
                                  "referenced nowhere in current code"))
                continue
            if name in _REGENERABLE:
                regen.append((name, size, _REGENERABLE[name]))
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

        print(f"\n  REGENERABLE ({sum(s for _, s, _ in regen) / 1e9:,.1f} "
              "GB) -- a pipeline step rebuilds these; excluded from the "
              "dump,\n  kept locally:")
        for n, s, by in regen:
            print(f"    {gb(s)}  {n}   [rebuilt by {by}]")

        print(f"\n  REVIEW ({sum(s for _, s in review) / 1e9:,.1f} GB) -- "
              "needs your eyes before anything happens to it:")
        for n, s in review:
            print(f"    {gb(s)}  {n}")

        excluded = ([n for n, _, _ in droppable]
                    + [n for n, _, _ in regen])
        kept = total - sum(s for _, s, _ in droppable) \
            - sum(s for _, s, _ in regen)
        print(f"\n  DUMP COMMAND (ships {kept / 1e9:,.1f} GB live; "
              "compresses further):\n")
        flags = " ".join(f'-T "{n}"' for n in excluded)
        print(f"    pg_dump -Fc -Z 6 {flags} -f xc_predictor.dump "
              "xc_predictor\n")

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
