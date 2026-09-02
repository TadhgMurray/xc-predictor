"""diag_xc_bridges.py -- how thin is the cross-state linkage, per sport?
READ ONLY, and it streams nothing: every number is a server-side aggregate
over ranking_results, which is indexed, so the database does the GROUP BY
and Python prints a few dozen rows.

    python scripts/diag_xc_bridges.py
    python scripts/diag_xc_bridges.py --pool hs_m --since 2015
    python scripts/diag_xc_bridges.py --sql

THE QUESTION. At ridge 0 the XC-versus-track level of one region against
another is pinned only by athletes who race the SAME sport in two regions in
one academic year; every dual-sport athlete's own XC-to-track contrast goes
into beta and pins nothing across regions. So the question is how many
athlete-seasons bridge two states in XC, who they are, and how that compares
with track. A bridge count in the hundreds, concentrated in the top rating
band, is a level held up by NXN and Foot Locker qualifiers and nothing else.

! STATE IS THE MEET'S STATE, not the athlete's. ranking_results.state is
  where the race was run, which is exactly the thing a bridge is about: an
  athlete-season with rows in two states raced in two states.

⚠ ranking_results holds board rows only, so this is linkage AMONG RATED
  ROWS. The solve sees a little more (it packs from results directly), but
  the pack applies the same pace band, so the difference is small.
"""

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# One row per athlete-season per sport: how many states it raced in, its
# home state (the mode), its best rating. Everything below reads this.
_AY_SQL = """
DROP TABLE IF EXISTS br_ay;
CREATE TEMP TABLE br_ay AS
SELECT person_id, sport, year,
       count(DISTINCT state)                     AS n_states,
       mode() WITHIN GROUP (ORDER BY state)      AS home,
       count(*)                                  AS n_races,
       max(speed_rating)                         AS best
FROM   ranking_results
WHERE  pool = %(pool)s
  AND  state IS NOT NULL
  AND  speed_rating IS NOT NULL
  AND  year >= %(since)s
GROUP  BY person_id, sport, year;
CREATE INDEX ON br_ay (sport, home);
ANALYZE br_ay;
"""

# The headline: per sport, athlete-seasons, how many bridge, and how the
# bridging ones are distributed by rating band against everyone.
_HEADLINE_SQL = """
SELECT sport,
       count(*)                                          AS athlete_seasons,
       count(*) FILTER (WHERE n_states >= 2)             AS bridged,
       count(*) FILTER (WHERE n_states >= 2 AND best >= 130) AS bridged_130,
       count(*) FILTER (WHERE best >= 130)               AS all_130,
       count(*) FILTER (WHERE n_states >= 2 AND best < 110) AS bridged_u110,
       count(*) FILTER (WHERE best < 110)                AS all_u110
FROM   br_ay
GROUP  BY sport
ORDER  BY sport
"""

# Per home state, per sport: how many of its athlete-seasons ever raced out
# of state, and how many distinct other states they reached. A state whose
# bridged share is under a percent, reaching two other states, has its level
# resting on a handful of trips.
_STATE_SQL = """
WITH out_states AS (
    SELECT a.person_id, a.sport, a.year, a.home,
           count(DISTINCT k.state) FILTER (WHERE k.state <> a.home) AS n_out
    FROM   br_ay a
    JOIN   ranking_results k
             ON k.person_id = a.person_id AND k.sport = a.sport
            AND k.year = a.year AND k.pool = %(pool)s
    WHERE  a.n_states >= 2
    GROUP  BY 1, 2, 3, 4
),
per_state AS (
    SELECT sport, home,
           count(*)                                   AS athlete_seasons,
           count(*) FILTER (WHERE n_states >= 2)      AS bridged
    FROM   br_ay
    GROUP  BY sport, home
),
reach AS (
    SELECT o.sport, o.home, count(DISTINCT k.state) AS states_reached
    FROM   out_states o
    JOIN   ranking_results k
             ON k.person_id = o.person_id AND k.sport = o.sport
            AND k.year = o.year AND k.pool = %(pool)s
    WHERE  k.state <> o.home
    GROUP  BY o.sport, o.home
)
SELECT p.sport, p.home, p.athlete_seasons, p.bridged,
       round(100.0 * p.bridged / NULLIF(p.athlete_seasons, 0), 2) AS pct,
       COALESCE(r.states_reached, 0)                               AS reached
FROM   per_state p
LEFT   JOIN reach r ON r.sport = p.sport AND r.home = p.home
WHERE  p.athlete_seasons >= %(min_ays)s
ORDER  BY p.sport, p.athlete_seasons DESC
"""

# The strongest state-to-state links, per sport: how many athlete-seasons
# raced in both. This is the actual bridge count the solve has to work with.
_PAIRS_SQL = """
WITH st AS (
    SELECT DISTINCT k.person_id, k.sport, k.year, k.state
    FROM   ranking_results k
    JOIN   br_ay a ON a.person_id = k.person_id AND a.sport = k.sport
                  AND a.year = k.year
    WHERE  k.pool = %(pool)s AND a.n_states >= 2 AND k.state IS NOT NULL
)
SELECT x.sport, x.state AS s1, y.state AS s2, count(*) AS athlete_seasons
FROM   st x
JOIN   st y ON y.person_id = x.person_id AND y.sport = x.sport
           AND y.year = x.year AND y.state > x.state
GROUP  BY 1, 2, 3
ORDER  BY 1, 4 DESC
"""

# The specific pairs the Mt. SAC question turns on.
_FOCUS_PAIRS = (("CA", "IL"), ("CA", "TX"), ("CA", "NY"), ("CA", "OR"),
                ("CA", "AZ"), ("IL", "WI"), ("CA", "UT"))


def main():
    ap = argparse.ArgumentParser(
        description="Cross-state linkage per sport, from aggregates only.")
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--since", type=int, default=2010,
                    help="first academic year counted (default 2010)")
    ap.add_argument("--min-ays", type=int, default=2000, dest="min_ays",
                    help="states with fewer athlete-seasons are not listed")
    ap.add_argument("--top", type=int, default=15,
                    help="strongest state pairs to list per sport")
    ap.add_argument("--sql", action="store_true")
    args = ap.parse_args()
    params = {"pool": args.pool, "since": args.since,
              "min_ays": args.min_ays}

    if args.sql:
        for name, sql in (("AY", _AY_SQL), ("HEADLINE", _HEADLINE_SQL),
                          ("STATE", _STATE_SQL), ("PAIRS", _PAIRS_SQL)):
            print(f"\n-- {name}\n{sql.strip()}")
        print(f"\n-- params {params}")
        return 0

    from database import getConn
    from psycopg2.extras import RealDictCursor

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL work_mem = '1GB'")
            print(f"\n  aggregating athlete-seasons ({args.pool}, "
                  f"{args.since}+)...")
            cur.execute(_AY_SQL, params)
            cur.execute(_HEADLINE_SQL)
            head = cur.fetchall()
            cur.execute(_STATE_SQL, params)
            states = cur.fetchall()
            cur.execute(_PAIRS_SQL, params)
            pairs = cur.fetchall()
            conn.rollback()

    print("\n" + "=" * 74)
    print("  BRIDGES: athlete-seasons that raced ONE sport in two or more states")
    print("=" * 74)
    print(f"    {'sport':<6}{'athlete-yrs':>13}{'bridged':>9}{'pct':>7}"
          f"{'130+ all':>10}{'130+ br':>9}{'pct':>7}{'<110 all':>10}"
          f"{'<110 br':>9}{'pct':>7}")
    print("    " + "-" * 86)
    for h in head:
        n, b = h["athlete_seasons"], h["bridged"]
        print(f"    {h['sport']:<6}{n:>13,}{b:>9,}{100.0 * b / max(n, 1):>7.2f}"
              f"{h['all_130']:>10,}{h['bridged_130']:>9,}"
              f"{100.0 * h['bridged_130'] / max(h['all_130'], 1):>7.2f}"
              f"{h['all_u110']:>10,}{h['bridged_u110']:>9,}"
              f"{100.0 * h['bridged_u110'] / max(h['all_u110'], 1):>7.2f}")
    print("\n    READ: XC bridged well under TF bridged, and concentrated in "
          "130+, means the XC level of one\n          state against another "
          "rests on national-meet qualifiers; the average athlete pins "
          "nothing.\n")

    print("=" * 74)
    print("  PER HOME STATE: share of athlete-seasons that raced out of state")
    print("=" * 74)
    by_sport = {}
    for s in states:
        by_sport.setdefault(s["sport"], []).append(s)
    for sport in sorted(by_sport):
        print(f"\n  {sport}")
        print(f"    {'state':<7}{'athlete-yrs':>13}{'bridged':>9}{'pct':>7}"
              f"{'states reached':>16}")
        print("    " + "-" * 52)
        for s in by_sport[sport]:
            print(f"    {s['home']:<7}{s['athlete_seasons']:>13,}"
                  f"{s['bridged']:>9,}{float(s['pct'] or 0):>7.2f}"
                  f"{s['reached']:>16}")

    print("\n" + "=" * 74)
    print("  STRONGEST STATE PAIRS, and the pairs the Mt. SAC question turns on")
    print("=" * 74)
    by_sport = {}
    for p in pairs:
        by_sport.setdefault(p["sport"], []).append(p)
    for sport in sorted(by_sport):
        rows = by_sport[sport]
        print(f"\n  {sport}: top {args.top}")
        for p in rows[:args.top]:
            print(f"    {p['s1']}-{p['s2']:<5}{p['athlete_seasons']:>8,}")
        lookup = {(p["s1"], p["s2"]): p["athlete_seasons"] for p in rows}
        print(f"  {sport}: focus pairs")
        for a, b in _FOCUS_PAIRS:
            key = (a, b) if a < b else (b, a)
            print(f"    {a}-{b:<5}{lookup.get(key, 0):>8,}")
    print("\n    An XC pair in the low hundreds over the whole span is the "
          "entire evidence the solve has\n    for those two states' relative "
          "XC level. Compare the same pair under TF.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
