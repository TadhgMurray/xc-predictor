# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/audit_distance_overrides.py
# Purpose: find hand-written distance overrides that the RATINGS CONTRADICT.
#
#   WHY THIS EXISTS
#   ---------------
#   Foot Locker Midwest Regionals, UW Parkside, 2018-11-24. Three boys'
#   divisions are pinned to 6000 m in _DISTANCE_OVERRIDES_XC; the girls'
#   division is pinned to 5000. Foot Locker Regionals are 5000 m for both.
#
#   The override wins over meets.distance (it is FIRST in the resolution
#   chain), so the row normalised as a 6000 m race:
#
#       trace at 5000 m -> factor 0.982    (matches the girls' 0.98857)
#       trace at 6000 m -> factor 0.80506  (matches the stored 0.80512)
#
#   Cole Hocker's 16:03 came out at 158.3 -- higher than any athlete-SEASON
#   in the corpus, and the whole all-time board became this one race.
#
#   ★ AND THE ROOT CAUSE IS NOT THE OVERRIDE. The venue snaps to a Lake
#     Michigan grid cell, where a reanalysis product correctly reports soil
#     moisture ~= 0 because there is no soil. Race day had 2.98 mm of rain at
#     5.8 C and the video shows mud. So the weather correction saw dry ground
#     on a muddy course, the field looked slow, a detector flagged the
#     division, and someone "fixed" it by pinning the distance longer --
#     which made everyone look fast again.
#
#     A WRONG DISTANCE CANCELLING A MISSING MUD CORRECTION. Two errors
#     stacked into a number nothing downstream could trace back.
#
#   ⚠ THE POINT OF THIS SCRIPT IS THE OTHER 2,368. If three overrides could
#     be confidently wrong and produce the most extreme ratings in the
#     corpus without anything noticing, the rest need auditing too -- and
#     they can be audited automatically, because a wrong distance makes a
#     whole field beat their own careers, which is measurable.
#
#   THE TEST: for each overridden division, compare every runner to their
#   OWN career average across both sports. A field-wide shift cannot be
#   talent, weather, or a fast day -- those move the front of the field, not
#   the median. It is the ruler.

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "engine"),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  THRESHOLDS
# ------------------------------------------------------------------ #

# A division must have this many rated runners with a usable baseline
# before its median means anything.
MIN_FIELD = 15

# Flag when the median runner is this far from their own career average.
# The corpus-wide median-gap histogram decays geometrically to about +8 and
# then flattens into a shelf -- that shelf is the ruler-error population.
FLAG_GAP = 8.0

# An athlete needs a baseline distinct from the race being tested.
MIN_BASELINE_RACES = 2


# ------------------------------------------------------------------ #
# CHUNK 1 -- LOAD THE OVERRIDES
# ------------------------------------------------------------------ #

def loadOverrides():
    """
    [(meet_id, div_id, distance), ...] from corrections.py.

    Imported, not re-typed. corrections.py is the single source of truth for
    what the pipeline actually applies; a copy here would drift the moment
    someone edits one and not the other.
    """
    import corrections as c
    out = []
    for name in ("_DISTANCE_OVERRIDES_XC", "_DISTANCE_OVERRIDES_TF"):
        d = getattr(c, name, None)
        if not d:
            continue
        sport = "XC" if name.endswith("XC") else "TF"
        for (meet_id, div_id), dist in d.items():
            out.append((sport, int(meet_id), int(div_id), float(dist)))
        print(f"    {name}: {len(d):,} entries")
    return out


# ------------------------------------------------------------------ #
# CHUNK 2 -- BASELINE
# ------------------------------------------------------------------ #

def buildBaseline(cur):
    """
    person_baseline: each person's career average rating, BOTH sports.

    ★ CAREER-WIDE AND CROSS-SPORT ON PURPOSE. A season-scoped, XC-only
      baseline misses exactly the populations that need checking: middle
      schoolers with one race a season, and championship fields whose
      runners appear once. Career + both sports takes the corpus from ~1
      usable race per MS athlete-season to ~9 per person.

    ⚠ THE BASELINE CONTAINS THE RACE BEING TESTED, which pulls a runner's
      average toward that race and SHRINKS the measured gap. Every number
      here is therefore an understatement, never an invention.
    """
    cur.execute("DROP TABLE IF EXISTS person_baseline")
    cur.execute("""
        CREATE TABLE person_baseline AS
        SELECT person_id, avg(speed_rating) AS career_avg, count(*) AS n_races
        FROM (
            SELECT person_id, speed_rating FROM results
            WHERE person_id IS NOT NULL AND speed_rating BETWEEN 40 AND 200
            UNION ALL
            SELECT person_id, speed_rating FROM results_tf
            WHERE person_id IS NOT NULL AND speed_rating BETWEEN 40 AND 200
        ) x GROUP BY person_id
    """)
    cur.execute("CREATE INDEX ON person_baseline (person_id)")
    cur.execute("ANALYZE person_baseline")
    cur.execute("SELECT count(*) FROM person_baseline")
    print(f"    {cur.fetchone()[0]:,} people with a career baseline")


def loadKeys(cur, rows):
    """
    Push the override keys into a temp table.

    ★ A TEMP TABLE, NOT A GIANT `IN (...)` OR A VALUES LIST. 2,371 tuples
      inline makes a statement most clients choke on, and the planner cannot
      use statistics on an inline list. executemany + ANALYZE gives a real
      relation to hash-join against.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_dist_override")
    cur.execute("""CREATE TEMP TABLE tmp_dist_override
                   (sport text, meet_id bigint, div_id bigint, dist real)""")
    cur.executemany(
        "INSERT INTO tmp_dist_override VALUES (%s,%s,%s,%s)", rows)
    cur.execute("CREATE INDEX ON tmp_dist_override (meet_id, div_id)")
    cur.execute("ANALYZE tmp_dist_override")


# ------------------------------------------------------------------ #
# CHUNK 3 -- THE AUDIT
# ------------------------------------------------------------------ #

def _auditSql(results_tbl, meets_tbl, sport, dist_col, venue_col):
    """
    One sport's audit, as SQL.

    ⚠ `dist_col` AND `venue_col` ARE PARAMETERISED BECAUSE THE TWO SCHEMAS
      DISAGREE, and they disagree in two separate places:
          meets     -> distance,         course_name
          meets_tf  -> distance_meters,  (NO course_name -- use location_id)
      Hardcoding either silently breaks the other sport, and it breaks as a
      run-time SQL error after the expensive part has already run.

    `med_gap` is the median of (race rating - that runner's career average)
    over the division. `p10` is the 10th percentile of the same quantity,
    and it is the discriminator:

        whole field shifted   p10 tracks the median  -> THE RULER
        only the front moved  p10 collapses to ~0    -> a strong field

    A distance error moves EVERYONE, so a real one shows both high.
    """
    return f"""
        WITH gapped AS (
            SELECT r.meet_id, r.div_id,
                   r.speed_rating - p.career_avg AS gap
            FROM   {results_tbl} r
            JOIN   tmp_dist_override o
                   ON o.meet_id = r.meet_id AND o.div_id = r.div_id
                  AND o.sport = '{sport}'
            JOIN   person_baseline p ON p.person_id = r.person_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  p.n_races >= {MIN_BASELINE_RACES}
        ),
        per_div AS (
            SELECT meet_id, div_id, count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS med_gap,
                   percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) AS p10
            FROM   gapped
            GROUP  BY 1, 2
            HAVING count(*) >= {MIN_FIELD}
        ),
        mts AS (
            SELECT meet_id, div_id,
                   min({dist_col})        AS table_dist,
                   min({venue_col})::text AS venue,
                   min(meet_name)         AS meet_name
            FROM   {meets_tbl}
            GROUP  BY 1, 2
        )
        SELECT o.meet_id, o.div_id, o.dist AS override_dist,
               m.table_dist,
               d.n,
               round(d.med_gap::numeric, 1),
               round(d.p10::numeric, 1),
               m.venue, m.meet_name
        FROM   per_div d
        JOIN   tmp_dist_override o
               ON o.meet_id = d.meet_id AND o.div_id = d.div_id
              AND o.sport = '{sport}'
        LEFT JOIN mts m
               ON m.meet_id = d.meet_id AND m.div_id = d.div_id
        WHERE  abs(d.med_gap) >= {FLAG_GAP}
        ORDER  BY abs(d.med_gap) DESC
    """


def classify(override_dist, table_dist, med_gap, p10):
    """
    Which KIND of problem a flagged row is. Three, and they need three
    different responses -- deleting all 315 lines would be wrong.

    ★ CLASS 1 -- 'wrong_override'. The override DISAGREES with the meets
      table, and the field ran fast/slow by about what that disagreement
      predicts. Foot Locker: override 6000, table 5000, gap +20.3. The
      table is right and the override is a typo. SAFE TO DELETE.

    ★ CLASS 3 -- 'other'. The override AGREES with the table, so the
      distance is not in dispute at all and the gap has some other cause.
      Deleting the override here changes NOTHING -- the same number comes
      straight back from meets. ~10% of the list. These are real anomalies
      the audit surfaced incidentally, and they belong on a different
      worklist.

    'no_table' is the remainder: no meets row to compare against, so the
    override cannot be adjudicated by this method either way.

    ⚠ SIGN AGREEMENT IS REQUIRED, NOT DECORATIVE. p10 must move the same way
      as the median, because that is what separates "the whole field
      shifted" (a ruler error, which moves everyone) from "the front of the
      field was strong" (which moves the median and leaves the tail alone).
      Belmont Plateau shows med +24.8 with p10 -5.5 -- a top-only anomaly
      wearing a ruler error's clothes.
    """
    if table_dist is None:
        return "no_table"
    if abs(override_dist - table_dist) < 1.0:
        return "other"
    if med_gap > 0 and p10 <= 0:
        return "top_only"
    if med_gap < 0 and p10 >= 0:
        return "top_only"
    return "wrong_override"


_BLOCK_MARK = "# ==== AUTO: distance override retractions"


def _findCorrections():
    """Path to the live corrections.py, from the module itself."""
    import corrections
    import inspect
    return inspect.getfile(corrections)


def applyFixes(rows, live=False):
    """
    Retract the class-1 overrides by APPENDING a pop() block.

    ★ APPEND, DO NOT REWRITE THE DICT LITERAL. corrections.py is ~1.45M
      lines and mentions _DISTANCE_OVERRIDES_XC in 24 places. Editing "the
      right one" means locating one of 24 blocks by regex inside a file
      every downstream module imports -- a lot of ways to corrupt it, and
      one missed `.update()` elsewhere would silently undo the edit.

      An appended block runs AFTER every one of those 24, whatever they are.
      It touches none of the existing lines, so no comment is lost. Undoing
      it is deleting the block.

    ★ .pop(k, None) IS IDEMPOTENT. Running this twice is a no-op instead of
      a KeyError, which matters because the audit is meant to be re-run
      after every engine pass.

    ⚠ EACH RETRACTED KEY CARRIES ITS EVIDENCE IN A TRAILING COMMENT -- the
      override, what meets says instead, the measured gap, and the field
      size. A deletion whose reason is not written down is one somebody
      re-adds in six months.
    """
    buckets = {}
    for r in rows:
        (mid, did, ovr, tbl_d, n, med, p10, venue, mname) = r
        buckets.setdefault(
            classify(ovr, tbl_d, float(med), float(p10)), []).append(r)

    print("\n" + "=" * 66)
    for k in ("wrong_override", "other", "top_only", "no_table"):
        print(f"  {k:<18}{len(buckets.get(k, [])):>5}")
    print("=" * 66)

    fix = buckets.get("wrong_override", [])
    _reportHeld(buckets)

    if not fix:
        print("\n  nothing to retract")
        return buckets

    import datetime
    stamp = datetime.date.today().isoformat()
    lines = [
        "",
        "",
        f"{_BLOCK_MARK}, {stamp} ====",
        "# Written by scripts/audit_distance_overrides.py --fix.",
        "#",
        "# Each key below is an override that DISAGREED with meets.distance,",
        "# and whose whole field then beat their own career averages by about",
        "# what that disagreement predicts. Both the median AND the 10th",
        "# percentile moved, which is what separates a ruler error (moves",
        "# everyone) from a strong field (moves the front only).",
        "#",
        "# Traced case: (156574, 652635) pinned Foot Locker Midwest boys to",
        "# 6000 m. The race is 5000. normalizeResult at 5000 gives factor",
        "# 0.982; at 6000 it gives 0.80506, and the database held 0.80512.",
        "# Cole Hocker's 16:03 came out at 158.3 -- above every athlete-season",
        "# in the corpus.",
        "#",
        "# Root cause was NOT the typo: the venue snaps to a Lake Michigan",
        "# grid cell where soil moisture correctly reads ~0 (no soil). Race",
        "# day had 2.98 mm rain at 5.8C and the course was muddy. The weather",
        "# correction saw dry ground, the field looked slow, and the distance",
        "# was pinned longer to compensate. A wrong distance cancelling a",
        "# missing mud correction.",
        "#",
        "# Appended rather than edited into the dict literal: this file",
        f"# declares _DISTANCE_OVERRIDES_XC in many places, and a block at the",
        "# end runs after all of them. Delete this block to revert.",
        "for _k in (",
    ]
    for (mid, did, ovr, tbl_d, n, med, p10, venue, mname) in fix:
        lines.append(f"    ({mid}, {did}),".ljust(28)
                     + f"# ov {ovr:.0f} -> meets {tbl_d:.0f}, "
                       f"gap {med:+.1f}, n={n}  {(venue or '?')[:30]}")
    lines += [
        "):",
        "    _DISTANCE_OVERRIDES_XC.pop(_k, None)",
        "del _k",
        f"# ==== END AUTO block {stamp} ====",
        "",
    ]
    block = "\n".join(lines)

    if not live:
        print(f"\n  DRY RUN -- would append {len(fix)} retractions. "
              f"Pass --write to apply.\n")
        print("\n".join(lines[:18]))
        print(f"    ... {len(fix)} keys ...")
        return buckets

    path = _findCorrections()
    bak = path + ".bak"
    if not os.path.exists(bak):
        import shutil
        shutil.copy2(path, bak)
        print(f"\n  backup: {bak}")
    else:
        print(f"\n  backup already exists, kept: {bak}")

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)
    print(f"  appended {len(fix)} retractions to {path}")

    # ★ VERIFY BY RE-IMPORTING, NOT BY TRUSTING THE WRITE. A key that was
    #   never in the dict pops silently, so "wrote 187 lines" is not
    #   evidence that 187 overrides are gone.
    import importlib
    import corrections
    before = len(corrections._DISTANCE_OVERRIDES_XC)
    importlib.reload(corrections)
    after = len(corrections._DISTANCE_OVERRIDES_XC)
    print(f"  _DISTANCE_OVERRIDES_XC: {before:,} -> {after:,} "
          f"({before - after:,} removed)")
    if before - after != len(fix):
        print(f"  ⚠ expected {len(fix)} removed. Some keys were not present"
              f" in the live dict -- harmless, but the count is the truth.")
    return buckets


def _reportHeld(buckets):
    """Class 3 and top-only, printed as their own worklists."""
    oth = buckets.get("other", [])
    if oth:
        print(f"\n  CLASS 3 -- override AGREES with the table "
              f"({len(oth)} rows). Retracting these changes NOTHING; the")
        print("  same distance comes straight back from meets. Different"
              " cause, separate worklist:\n")
        print(f"    {'meet':>8}{'div':>9}{'dist':>7}{'n':>5}"
              f"{'med':>7}{'p10':>7}  venue / meet")
        for (mid, did, ovr, tbl_d, n, med, p10, venue, mname) in oth:
            print(f"    {mid:>8}{did:>9}{ovr:>7.0f}{n:>5}{med:>7}{p10:>7}"
                  f"  {(venue or '?')[:26]} / {(mname or '')[:26]}")

    top = buckets.get("top_only", [])
    if top:
        print(f"\n  TOP-ONLY -- median moved, tail did not ({len(top)} rows)."
              f" A distance error moves EVERYONE, so these are not ruler")
        print("  errors. Held.\n")
        for (mid, did, ovr, tbl_d, n, med, p10, venue, mname) in top:
            print(f"    {mid:>8}{did:>9}{ovr:>7.0f}{n:>5}{med:>7}{p10:>7}"
                  f"  {(venue or '?')[:34]}")


def audit(cur, sport):
    # ★ meets_tf CARRIES AN EVENT DIMENSION -- 658,036 distinct
    #   (meet_id, div_id) pairs span 14.17M rows, a 21.5x fan-out. The `mts`
    #   CTE above collapses it to one row per pair BEFORE the join; joining
    #   raw would multiply every division's row count by ~20.
    if sport == "XC":
        tbl, mts, dcol, vcol = "results", "meets", "distance", "course_name"
    else:
        tbl, mts, dcol, vcol = ("results_tf", "meets_tf",
                                "distance_meters", "location_id")
    cur.execute(_auditSql(tbl, mts, sport, dcol, vcol))
    rows = cur.fetchall()

    cur.execute(f"SELECT count(*) FROM tmp_dist_override WHERE sport = '{sport}'")
    total = cur.fetchone()[0]

    print(f"\n[{sport}] {len(rows):,} of {total:,} overrides contradicted "
          f"by the ratings")
    if not rows:
        return rows
    print(f"    {'meet':>8}{'div':>9}{'ovr':>7}{'tbl':>7}{'n':>5}"
          f"{'med':>7}{'p10':>7}  venue / meet")
    for (mid, did, ovr, tbl_d, n, med, p10, venue, mname) in rows[:60]:
        t = f"{tbl_d:.0f}" if tbl_d else "-"
        print(f"    {mid:>8}{did:>9}{ovr:>7.0f}{t:>7}{n:>5}"
              f"{med:>7}{p10:>7}  {(venue or '?')[:26]} / {(mname or '')[:26]}")
    return rows


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(rebuild=True, fix=False, write=False):
    from database import getConn

    print("[audit] loading overrides from corrections.py...")
    rows = loadOverrides()
    if not rows:
        print("    none found -- check the constant names in corrections.py")
        return

    with getConn() as conn:
        with conn.cursor() as cur:
            if rebuild:
                print("\n[audit] building career baseline (both sports)...")
                buildBaseline(cur)
                conn.commit()

            print("\n[audit] loading override keys...")
            loadKeys(cur, rows)

            all_rows = []
            for sport in ("XC", "TF"):
                all_rows += audit(cur, sport)

            if fix and all_rows:
                applyFixes(all_rows, live=write)

            conn.rollback()      # temp tables only; nothing to persist

    if not fix:
        print("\n[audit] Each row above is an override the DATA disagrees"
              " with.")
        print("        Pass --fix to classify them and emit the deletions.")


if __name__ == "__main__":
    # --write implies --fix: applying without classifying is meaningless.
    _w = "--write" in sys.argv
    main(rebuild="--no-rebuild" not in sys.argv,
         fix=("--fix" in sys.argv or _w),
         write=_w)