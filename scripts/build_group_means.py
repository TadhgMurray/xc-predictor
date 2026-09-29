#!/usr/bin/env python3
"""
build_group_means.py -- every reference group's average, as a point on the
one ability scale (owner, 2026-09-29).

    /srv/venv/bin/python scripts/build_group_means.py            # print, write nothing
    /srv/venv/bin/python scripts/build_group_means.py --write    # table group_scale + JSON
    /srv/venv/bin/python scripts/build_group_means.py --since 2024

★ WHY. "I wonder how we can keep being able to see like this runner is x%
  better than the avg collegiate, or the avg DI runner." Under one scale a
  group's average is a point on the scale like any runner, so the question
  is a ratio:

      x% better than group G  =  HS-equivalent(athlete) / hs_ref(G) - 1

  and it is the SAME number as the own-pool form, own(athlete) / own_ref(G)
  - 1, when G sits inside the athlete's pool -- the factor cancels. A group
  is a pool (college_m), or a slice of one: a college division (D1, D2,
  D3, NAIA from the school's units, which come from college_directory), a
  conference, a state.

★ WHAT "THE GROUP'S AVERAGE" IS, DERIVED FROM THE RATING'S OWN DEFINITION.
  A rating is 100 * pool_mean / ability, with pool_mean the MEAN ABILITY
  (a time) over the pool's anchor athlete-seasons (>= 3 races). So a
  group's average runner is the one at the group's mean time, and
      rating(mean-time runner) = 100 pm / mean_i(100 pm / r_i)
                               = 1 / mean_i(1 / r_i)
  -- the HARMONIC mean of the members' ratings, not the arithmetic. The
  pool row is the self-check: its own_ref should read ~100, because that
  is what the go-live anchored (printed; a pool far from 100 means the
  season proxy below is not the engine's season).

  An athlete-season's rating here is the median of its rated results in
  ranking_results (the engine's season rating is its ability, which the
  per-result ratings scatter around), and a season needs 3 results -- the
  engine's anchor rule (pair_ratings.buildRatings min_races_anchor).

★ THE ONE-SCALE POINT IS own_ref x factor(pool): pool_view.repFactor, the
  factor every HS view uses -- under XCP_ONE_SCALE=1 the engine's own
  pm(hs_g) / pm(pool).

HOW THE SITE READS IT: pool_view.groupReference(pool, kind, group) returns
(hs_ref, own_ref, n); "x% better than the average D1 man" is
athlete_hs / hs_ref - 1 with (pool='college_m', kind='division', group='D1').
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("scripts", "engine", "racecast"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")

OUT_JSON = os.path.join(_ROOT, "engine", "data", "group_scale.json")
MIN_RESULTS = 3          # pair_ratings.buildRatings' min_races_anchor
MIN_SEASONS = 30         # a group's mean from fewer seasons is an anecdote:
                         # the harmonic mean's SE at ratings sd ~15 over n
                         # seasons is ~15/sqrt(n) points, 2.7 at 30 -- the
                         # size of the smallest gap worth printing as a %
KINDS = {
    # kind -> (column in ranking_results, which pools it slices)
    "division":   ("division",   ("college_",)),
    "conference": ("conference", ("college_",)),
    "state":      ("state",      ("hs_", "ms_")),
}

_SEASON_SQL = """
    SELECT person_id, pool, year,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS r,
           mode() WITHIN GROUP (ORDER BY division)   AS division,
           mode() WITHIN GROUP (ORDER BY conference) AS conference,
           mode() WITHIN GROUP (ORDER BY state)      AS state
    FROM   ranking_results
    WHERE  speed_rating > 0 AND pool IS NOT NULL
      AND  (%(since)s IS NULL OR year >= %(since)s)
    GROUP  BY person_id, pool, year
    HAVING count(*) >= %(min_n)s
"""


def groupRefs(seasons, factors, min_seasons=MIN_SEASONS):
    """seasons: iterable of dicts {pool, r, division, conference, state};
    factors: {pool: HS factor or None}. Returns a list of rows
    (kind, pool, group, n, own_ref, hs_ref) -- own_ref the harmonic mean of
    the members' own-pool ratings, hs_ref that times the pool's factor.
    PURE: the test drives it with no database."""
    acc = {}
    for s in seasons:
        pool, r = s["pool"], float(s["r"])
        if not pool or r <= 0:
            continue
        keys = [("pool", pool, pool)]
        for kind, (col, prefixes) in KINDS.items():
            v = s.get(col)
            if v and any(pool.startswith(p) for p in prefixes):
                keys.append((kind, pool, str(v)))
        for k in keys:
            a = acc.setdefault(k, [0, 0.0])
            a[0] += 1
            a[1] += 1.0 / r
    rows = []
    for (kind, pool, grp), (n, inv) in sorted(acc.items()):
        if n < min_seasons:
            continue
        own = n / inv
        f = factors.get(pool)
        rows.append((kind, pool, grp, n, own, own * f if f else None))
    return rows


def _fetch(since):
    from database import getConn
    import psycopg2.extras
    with getConn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_SEASON_SQL, {"since": since, "min_n": MIN_RESULTS})
        return cur.fetchall()


def _write(rows):
    from datetime import date
    from database import getConn
    today = date.today().isoformat()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS group_scale_new")
        cur.execute("""
            CREATE TABLE group_scale_new (
                kind text NOT NULL, pool text NOT NULL, grp text NOT NULL,
                n_seasons integer NOT NULL, own_ref real NOT NULL,
                hs_ref real, last_updated text,
                PRIMARY KEY (kind, pool, grp))
        """)
        cur.executemany(
            "INSERT INTO group_scale_new VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(k, p, g, n, o, h, today) for k, p, g, n, o, h in rows])
        cur.execute("DROP TABLE IF EXISTS group_scale")
        cur.execute("ALTER TABLE group_scale_new RENAME TO group_scale")
        conn.commit()
    with open(OUT_JSON + ".tmp", "w") as f:
        json.dump({"rows": rows, "written": today}, f)
    os.replace(OUT_JSON + ".tmp", OUT_JSON)
    print(f"  group_scale: {len(rows):,} rows written (table + {OUT_JSON})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--since", type=int, default=None,
                    help="academic years from this one on (default: all, as "
                         "the engine's career pool mean)")
    args = ap.parse_args()
    import pool_view
    import conversions
    seasons = _fetch(args.since)
    pools = sorted({s["pool"] for s in seasons if s["pool"]})
    factors = {p: pool_view.repFactor(p, "XC") for p in pools}
    rows = groupRefs(seasons, factors)
    print(f"  {len(seasons):,} athlete-seasons with >= {MIN_RESULTS} rated results"
          f"{f' since {args.since}' if args.since else ''}; one-scale "
          f"{'ON (engine means)' if pool_view._oneScale() else 'off (sampled constants)'}")
    print(f"\n  {'kind':<11}{'pool':<12}{'group':<22}{'seasons':>9}{'own ref':>9}"
          f"{'HS ref':>8}   HS 5K (xc / track)")
    for kind, pool, grp, n, own, hs in rows:
        if kind == "conference" and n < 200:
            continue                               # the table has them all
        five = ""
        g = pool.rsplit("_", 1)[-1]
        if hs and g in ("m", "f"):
            try:
                fk = conversions.fiveKForHsRating(hs, g)
                if fk:
                    five = f"{_mmss(fk['xc'])} / {_mmss(fk['track'])}"
            except Exception:                      # noqa: BLE001
                five = ""
        flag = "  <- self-check, expect ~100" if kind == "pool" else ""
        print(f"  {kind:<11}{pool:<12}{grp[:21]:<22}{n:>9,}{own:>9.1f}"
              f"{(f'{hs:.1f}' if hs else '-'):>8}   {five}{flag}")
    if args.write:
        _write(rows)
    else:
        print("\n  (--write to store group_scale)")


def _mmss(s):
    if not s:
        return "-"
    return f"{int(s // 60)}:{s % 60:04.1f}"


if __name__ == "__main__":
    main()
