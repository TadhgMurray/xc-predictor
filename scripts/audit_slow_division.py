#!/usr/bin/env python3
# ======================================================================
# audit_slow_division.py -- WHY did the inversion want this race longer?
# ======================================================================
#
# THE QUESTION
# ------------
# 163697/678411 is a women's mile. The field runs ~6:08. Correctly
# labelled 1609m. The REFUTED rule drafted it to 3000 -- it saw a field
# 62% slower than its athletes' ratings and concluded "must be longer".
#
# But "slower than their ratings" has two innocent explanations and one
# guilty one, and physics cannot tell them apart:
#
#   VENUE   the whole field is slow HERE because the course/day is slow.
#           The ratings are right; the race is just hard. Drafting a
#           longer distance to explain it is the BUG. (0.9: a container
#           reading slow is a MEET effect, not a label.)
#
#   BASELINE the athletes' ratings are built partly from OTHER distances
#           (3000s, 1500s) so their baseline pace is wrong, making this
#           mile look anomalous. The ratings are the problem, not the race.
#
#   REAL    the times really are corrupt/impossible. (Already ruled out
#           for 163697 by the page -- 5:43 winner is a real schoolgirl mile.)
#
# This script pulls, for every runner in a division:
#   - time here, normalized_time here, speed_rating here
#   - their OTHER races: how many, at what distances, how they normalize
# so we can SEE whether the "slow" is the venue (all runners slow here,
# normal elsewhere) or the baseline (their elsewhere is a different event).
#
# Read-only. SELECTs only. Never writes.
#
# ======================================================================

import argparse
import sys
sys.path.insert(0, "engine")

# streamResults / the driver live here; the engine imports from it, so its
# connection helper is the canonical one -- reuse it rather than opening a
# second pool with different settings.
from speed_ratings_db import _connect          # same pool the engine uses


# ----------------------------------------------------------------------
# 1. DISCOVER THE ATHLETE-RATINGS TABLE
# ----------------------------------------------------------------------
# Purpose : find the table holding per-athlete speed_rating without
#           hardcoding a name I have not verified.
# Arguments:
#   cur -- a live cursor.
# Output  : (table_name, key_column) -- the table and the column that
#           joins back to a result's person_id.
#
# WHY DISCOVER: the engine writes {(person_id, pool): speed_rating} via
# buildAthleteRatings, but the DESTINATION table name is in
# speed_ratings_db, which I have not read. Querying information_schema for
# "a table with both a person-ish key and a speed_rating column" finds it
# by SHAPE. A guessed name that is subtly wrong throws at runtime; this
# fails loudly here, with the candidates printed, if the shape is ambiguous.
# ----------------------------------------------------------------------
def _findRatingsTable(cur):
    cur.execute("""
        SELECT table_name
        FROM information_schema.columns
        WHERE column_name = 'speed_rating'
        GROUP BY table_name
    """)
    tables = [r[0] for r in cur.fetchall()]

    # of those, keep the ones ALSO carrying a person key -- that excludes
    # results_xc/results_tf (which have speed_rating but are per-RESULT, not
    # per-athlete) and leaves the athlete-ratings table.
    out = []
    for t in tables:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s AND column_name IN ('person_id', 'pool')
        """, (t,))
        cols = {r[0] for r in cur.fetchall()}
        if 'person_id' in cols:
            out.append((t, cols))

    if len(out) != 1:
        print("  [discover] ambiguous or missing ratings table; candidates:")
        for t, cols in out:
            print(f"    {t}  ({', '.join(sorted(cols))})")
        raise SystemExit("resolve the ratings table name and pass --ratings")
    return out[0][0]


# ----------------------------------------------------------------------
# 2. THE DIVISION'S RUNNERS  (the "slow here?" half)
# ----------------------------------------------------------------------
# Purpose : every runner in one meet/div, with the numbers the engine used.
# Arguments:
#   cur, table  -- cursor, results table for the sport.
#   ratings     -- the discovered ratings table.
#   meet, div   -- the division.
# Output  : list of dict rows.
#
# The LEFT JOIN is deliberate: a runner with no rating yet (fewer than
# MIN_RACES) still ran this race and still counts toward "is the field
# slow". INNER JOIN would silently drop the newest athletes and bias the
# picture toward the well-established -- the exact class of silent drop
# 0.14 warns about.
# ----------------------------------------------------------------------
def _divisionRunners(cur, table, ratings, meet, div):
    cur.execute(f"""
        SELECT r.person_id,
               r.time_seconds,
               r.normalized_time,
               r.speed_rating          AS rating_here,
               a.speed_rating          AS rating_overall,
               a.n_races               AS n_races
        FROM {table} r
        LEFT JOIN {ratings} a ON a.person_id = r.person_id
        WHERE r.meet_id = %s AND r.div_id = %s
        ORDER BY r.time_seconds
    """, (meet, div))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ----------------------------------------------------------------------
# 3. ONE RUNNER'S OTHER RACES  (the "slow ELSEWHERE too?" half)
# ----------------------------------------------------------------------
# Purpose : the same athlete's races OUTSIDE this meet, so we can see the
#           baseline this race is being judged against.
# Arguments:
#   cur, table, person, meet -- exclude the meet we are auditing.
# Output  : dict summary -- count, and the min/median/max normalized_time
#           of their OTHER races.
#
# If their other races normalize to roughly the SAME value as here, the
# field is not slow -- the rating is just optimistic (BASELINE). If their
# other races are much FASTER-normalizing, this venue is genuinely slow
# (VENUE) and the draft was wrong to blame distance.
# ----------------------------------------------------------------------
def _otherRaces(cur, table, person, meet):
    cur.execute(f"""
        SELECT COUNT(*)                              AS n,
               MIN(normalized_time)                  AS nmin,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY normalized_time) AS nmed,
               MAX(normalized_time)                  AS nmax
        FROM {table}
        WHERE person_id = %s AND meet_id <> %s
          AND normalized_time IS NOT NULL
    """, (person, meet))
    row = cur.fetchone()
    return {"n": row[0], "nmin": row[1], "nmed": row[2], "nmax": row[3]}


# ----------------------------------------------------------------------
# 4. FORMATTING
# ----------------------------------------------------------------------
def _fmt(x, nd=1):
    return "----" if x is None else f"{x:.{nd}f}"


def _report(runners, otherByPerson):
    print(f"\n{'person_id':>20} {'time':>7} {'norm_here':>9} {'rate_here':>9} "
          f"{'rate_all':>8} {'nR':>3} | {'other_med':>9} {'ratio':>6}")
    print("-" * 90)

    slow_here = 0
    for r in runners:
        o = otherByPerson.get(r["person_id"], {})
        # ratio > 1 means this race normalized SLOWER than the athlete's
        # other races -- the signature of a slow venue, not a long course.
        ratio = None
        if o.get("nmed") and r["normalized_time"]:
            ratio = float(r["normalized_time"]) / float(o["nmed"])
            if ratio > 1.10:
                slow_here += 1

        print(f"{r['person_id']:>20} {_fmt(r['time_seconds']):>7} "
              f"{_fmt(r['normalized_time']):>9} {_fmt(r['rating_here'],0):>9} "
              f"{_fmt(r['rating_overall'],0):>8} {_fmt(r['n_races'],0):>3} | "
              f"{_fmt(o.get('nmed')):>9} {_fmt(ratio,2):>6}")

    n = len(runners)
    print("-" * 90)
    print(f"\n  {slow_here}/{n} runners normalized >10% SLOWER here than in "
          f"their other races.")
    print( "  many slow-here  -> VENUE: the course/day was slow; the 3000 draft\n"
           "                     mistook a hard race for a long one. PARDON.")
    print( "  few  slow-here  -> BASELINE: their ratings come from other events;\n"
           "                     the mile only LOOKS slow. Different fix.\n")


# ----------------------------------------------------------------------
# 5. MAIN
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Audit why a division drafted longer.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--ratings", default=None,
                    help="athlete-ratings table; auto-discovered if omitted")
    args = ap.parse_args()

    table = "results_tf" if args.sport == "TF" else "results_xc"

    conn = _connect()
    cur = conn.cursor()

    ratings = args.ratings or _findRatingsTable(cur)
    print(f"results table: {table}   ratings table: {ratings}")

    runners = _divisionRunners(cur, table, ratings, args.meet, args.div)
    if not runners:
        print("no rows for that meet/div -- check div_id (tfrrs local vs canon).")
        return

    # one _otherRaces call per runner; a division is tens of athletes, not
    # thousands, so N+1 queries here is fine and keeps each query trivially
    # readable. (If this is ever run over a whole cluster, batch it then.)
    otherByPerson = {}
    for r in runners:
        if r["person_id"] is not None:
            otherByPerson[r["person_id"]] = _otherRaces(
                cur, table, r["person_id"], args.meet)

    _report(runners, otherByPerson)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()