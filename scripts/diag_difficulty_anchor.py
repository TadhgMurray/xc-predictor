"""diag_difficulty_anchor.py -- is course_difficulties anchored where the
conversions page thinks it is?

    /srv/venv/bin/python scripts/diag_difficulty_anchor.py

★ THE QUESTION, IN ONE LINE. joint_golive anchors every difficulty so the
  AVERAGE OUTDOOR TRACK IS 0.0, which puts an ordinary cross country
  course at about +7% (difficulty_view.py's header says exactly that, and
  records the last time something quietly undid it). conversions.venueEffect
  is built for that anchor:

      base = tilt(rating) * (log1p(difficulty) + anchor_shift)

  With a track-anchored +7% XC course and anchor_shift -0.0583 that gives
  XC about 7% slower than a track at the same rating -- the grass cost,
  which is the right answer. With an XC course stored near 0.0 instead, the
  same line gives XC about 5.8% FASTER than a track, and every named-course
  conversion on the page is wrong by the whole sport gap.

★ WHY THE OTHER DIAGNOSTIC MISSED IT. diag_conversion_gain passes
  chosen=None, which takes venueEffect's UNNAMED branch (base = med, from
  engine_scale, correctly track-anchored). The page names a course, so it
  takes the branch above. The unnamed path was measured and agreed; the
  named path is the one in front of readers.

! IT READS, IT DOES NOT WRITE. Two medians and a subtraction.
"""
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                   # noqa: E402

from database import getConn                             # noqa: E402

MEDIANS_SQL = """
    SELECT
      percentile_cont(0.5) WITHIN GROUP (ORDER BY difficulty)
        FILTER (WHERE canonical_id IS NOT NULL)                AS xc_median,
      count(*) FILTER (WHERE canonical_id IS NOT NULL)         AS xc_n,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY difficulty)
        FILTER (WHERE course_name LIKE 'TF:loc:%%')            AS tf_median,
      count(*) FILTER (WHERE course_name LIKE 'TF:loc:%%')     AS tf_n
    FROM course_difficulties
    WHERE difficulty IS NOT NULL
"""

# A few courses whose real character is not in doubt, so the numbers can be
# read against something other than a statistic.
NAMED_SQL = """
    SELECT cd.canonical_id, cd.distance_m, cd.difficulty,
           min(cc.course_name) AS course_name
    FROM   course_difficulties cd
    JOIN   course_canonical cc ON cc.canonical_id = cd.canonical_id
    WHERE  cd.canonical_id IS NOT NULL
      AND  cd.distance_m BETWEEN 4900 AND 5100
      AND  (cc.course_name ILIKE %(a)s OR cc.course_name ILIKE %(b)s
            OR cc.course_name ILIKE %(c)s)
    GROUP BY cd.canonical_id, cd.distance_m, cd.difficulty
    ORDER BY cd.difficulty DESC
    LIMIT 25
"""


def main():
    import joint_solve as js
    gap = js.XC_TRACK_GAP

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(MEDIANS_SQL)
            m = cur.fetchone()
            named = []
            try:
                cur.execute(NAMED_SQL, {"a": "%Holmdel%", "b": "%Woodward%",
                                        "c": "%WakeMed%"})
                named = cur.fetchall()
            except Exception as exc:                     # noqa: BLE001
                conn.rollback()
                print(f"  (named-course lookup skipped: {exc})")

    xc, tf = m["xc_median"], m["tf_median"]
    print(f"\ncourse_difficulties, as stored:")
    print(f"  median XC course   {100 * float(xc):+7.3f}%   over {m['xc_n']:,} cells")
    print(f"  median TF venue    {100 * float(tf):+7.3f}%   over {m['tf_n']:,} cells")
    measured = float(xc) - float(tf)
    print(f"  XC - TF            {100 * measured:+7.3f}%   "
          f"expected {100 * gap:+.3f}% (joint_solve.XC_TRACK_GAP)\n")

    if abs(measured - gap) < 0.015:
        print("  ✓ the table IS track-anchored. An ordinary XC course carries")
        print("    the grass cost, venueEffect's `log1p(d) + anchor_shift` is")
        print("    getting what it expects, and the conversion bug is NOT here.")
    else:
        off = measured - gap
        print(f"  ⚠ THE TABLE IS NOT TRACK-ANCHORED. XC sits {100 * off:+.2f} points")
        print("    from where venueEffect assumes it is, so every NAMED-course")
        print("    conversion is wrong by about that much -- XC reading fast.")
        print("    The unnamed path (base = med, from engine_scale) is unaffected,")
        print("    which is why the aggregate checks looked clean.")
        print()
        print("    Two places it can be fixed, and they are not equivalent:")
        print("      joint_golive  -- write the anchored value, so difficulty")
        print("                       means one thing everywhere (preferred;")
        print("                       difficulty_view and the course pages read")
        print("                       this table too, and they are showing the")
        print("                       same number).")
        print("      venueEffect   -- stop adding anchor_shift. Fixes the")
        print("                       conversion alone and leaves every other")
        print("                       reader of the table still wrong.")

    # ---- the table the CONVERSIONS PAGE actually uses ------------------ #
    #
    # ⚠ /api/course_search reads course_distances, NOT course_difficulties,
    #   and /api/convert feeds that client-supplied number straight into the
    #   math ("difficulty": c.get("difficulty")). Nothing in the repository
    #   WRITES course_distances -- it is read in three places and rebuilt by
    #   no pipeline step -- so it holds whatever anchor was current when it
    #   was last populated. If its median sits near 0 while
    #   course_difficulties sits near +6%, every named-course XC conversion
    #   is off by the whole grass cost, and only XC: the TF targets are
    #   "a typical track" and never touch this table.
    try:
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c2:
                c2.execute("""
                    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY difficulty)
                             AS med,
                           count(*) AS n
                    FROM   course_distances
                    WHERE  difficulty IS NOT NULL
                """)
                cdm = c2.fetchone()
        med2 = float(cdm["med"]) if cdm and cdm["med"] is not None else None
        print(f"\ncourse_distances (what /api/course_search serves the page):")
        if med2 is None:
            print("  empty or absent")
        else:
            print(f"  median difficulty  {100 * med2:+7.3f}%   over {cdm['n']:,} rows")
            drift = float(xc) - med2
            print(f"  vs course_difficulties median XC {100 * float(xc):+7.3f}%"
                  f"   ->  {100 * drift:+.2f} points apart")
            if abs(drift) > 0.015:
                print("\n  ⚠ THESE ARE THE SAME COURSES ON DIFFERENT ANCHORS, and the")
                print("    page is using the WRONG one. course_difficulties is rebuilt")
                print("    every run; course_distances is written by nothing in the")
                print("    repo. Fix /api/course_search to serve the rebuilt table.")
            else:
                print("\n  the two agree; course_distances is not the problem.")
    except Exception as exc:                             # noqa: BLE001
        print(f"\n  (course_distances check skipped: {exc})")

    if named:
        print("\n  a few 5k courses, as stored:")
        for r in named:
            print(f"    {(r['course_name'] or '?')[:34]:<34} "
                  f"{100 * float(r['difficulty']):+7.2f}%")
        print("\n  Holmdel is a hard course and WakeMed a fast one. Track-anchored,")
        print("  expect roughly +8% and +2%; anchored on the average XC course")
        print("  instead, expect roughly +2% and -3%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
