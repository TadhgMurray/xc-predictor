#!/usr/bin/env python3
"""
build_pro_ability.py -- which athlete-seasons are fast enough to be pooled
professional. One row per ABLE (person, academic year).

    python engine/build_pro_ability.py --dry-run
    python engine/build_pro_ability.py --write

★ THE OWNER'S RULE (2026-09-22): "if they're sub 14:00? for men, or sub
  15:30? for women put in pro, otherwise trust grade."

★★ WHY IT EXISTS. The professional pool's AVERAGE member runs a 25:53
   5K-equivalent -- C(pro_m) = 1553.1 against C(hs_m) = 1211.5, measured
   through racecast/pool_view.py. The pool is not lightly contaminated, it
   is DOMINATED by people who are not professionals. Because speed_rating
   is pool-relative, every one of them drags the pool's 100-anchor, so the
   contamination does not merely mis-file those athletes -- it BENDS the
   rating of every genuine professional measured against them.

   Three separate doors to is_pro were found wrong in three days
   (build_team_pool.classify twice, pool_resolve's team_has_pros once).
   This table feeds a gate that sits DOWNSTREAM of all six routes, and of
   whichever one turns out to be wrong next. That is the whole argument for
   spending a table on it rather than fixing a fourth rule.

★ NECESSARY, NEVER SUFFICIENT. Membership here does not make anybody
  professional -- that would pool every good high schooler pro, the exact
  opposite of the owner's other instruction ("their season should still be
  hs"). pool_resolve's gate can only ever take is_pro AWAY.

⚠⚠⚠ THE STORED normalized_time CANNOT BE USED AND THIS IS THE TRAP.
    normalize_distance.targetFor gives every pool a DIFFERENT anchor
    distance -- 5000 for hs and pro, 8000 for college men, 6000 for
    college women, 3200 for middle school. The same stored seconds are a
    different performance in every pool, and testing a college man's
    stored number against 840 asks him for a 14:00 EIGHT thousand, which
    nobody alive has run. Every mark here is re-expressed on ONE curve,
    hs_<gender> at 5000m; see pool_resolve.ABILITY_CURVE_POOL.

! NOTHING IS REIMPLEMENTED. The rows come from speed_ratings_db.
  streamResults -- the loader the solve itself uses, with its band, its
  dedup, its wheelchair and field-event exclusions -- and the conversion
  from raw time is speed_ratings._scaleFactor, the same multiplier
  rescaleToPool identifies scales with. A second row filter here is
  precisely the failure pool_resolve's header exists to record.

! THE TABLE HOLDS ONLY THE ABLE, and absence therefore means "not able".
  A row per rated athlete-season would be ~29M rows and could not be held
  in a dict beside a 3.6GB pack. The cost of the shortening is that a
  season with NO rated mark at all is indistinguishable from a slow one --
  which is harmless, because such a season contributes no rated row to any
  pool's anchor, and the gate only ever demotes. What absence must NOT be
  allowed to mean is "the table was never built": loadProAbility returns
  None for an empty table, and pool_resolve treats None as no verdict.

Table:

    pro_ability_season (person_id, season, best_nt, gender)
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                   # noqa: E402
from season_year import seasonYearFromIso                      # noqa: E402
from pool_resolve import (PRO_ABILITY_5K, ABILITY_CURVE_POOL,  # noqa: E402
                          ABILITY_CURVE_SPORT)
from normalize_distance import poolBandFor                     # noqa: E402
from speed_ratings import _scaleFactor                         # noqa: E402
from speed_ratings_db import COLUMNS, streamResults            # noqa: E402

_I = {name: i for i, name in enumerate(COLUMNS)}

_DDL = """
    CREATE TABLE pro_ability_season (
        person_id  bigint  NOT NULL,
        season     int     NOT NULL,
        best_nt    real,
        gender     text,
        PRIMARY KEY (person_id, season)
    )"""

# The report's histogram edges, in 5K-equivalent seconds. Chosen so the two
# thresholds fall ON an edge -- a histogram whose bar straddles the bar it
# is drawn to explain is not evidence.
_BINS = (780.0, 840.0, 900.0, 930.0, 990.0, 1080.0, 1200.0, 1500.0, 1800.0)


def _clock(secs):
    if secs is None:
        return "--"
    m = int(secs) // 60
    return f"{m}:{secs - 60 * m:04.1f}"


def scan(min_time=200.0, max_time=6000.0, sports=("XC", "TF"), limit=None):
    """{(person_id, academic_year): (best_nt, gender)} over the ABLE, plus a
    histogram of every athlete-season's best.

    ! THE BEST IS A MINIMUM OVER BOTH SPORTS. An athlete-season is one
      season of one person's life; their cross-country and track marks are
      evidence about the same body, and the curve has already put both on
      the same scale. Taking them separately would let a 13:50 in October
      fail to vouch for the same athlete in April.
    """
    # ⚠⚠⚠ THE SANITY BAND, AND THE DRY RUN IS WHY IT IS HERE (2026-09-22).
    #     The first --dry-run's thirty fastest athlete-seasons were:
    #
    #         17494297  2023  F   0:00.2
    #         25737720  2010  F   0:12.5
    #
    #     A 0.2-second 5,000m equivalent is not a performance. Solving
    #     backwards, nt = t * k(d) reaches 0.195s at d = 4,700,000 metres
    #     and 12.1s at d = 100,000 -- so these rows carry a dist_m of 100km
    #     and 4,700km.
    #
    # ★ AND THEY ARE INVISIBLE UPSTREAM. streamResults bands the STORED
    #   normalized_time at 200-6000s, which they pass, because that number
    #   was written with a different distance than the one dist_m now
    #   carries. The disagreement only shows when the mark is re-expressed,
    #   which is exactly what this file does -- so this file is where it has
    #   to be caught.
    #
    # ! THE RAIL IS THE ENGINE'S OWN, NOT A NUMBER INVENTED HERE.
    #   normalize_distance.PACE_FLOOR is 0.12 s/m, "faster than any human
    #   over any distance", and poolBandFor multiplies it by the anchor:
    #   (600, 3600) at 5,000m. packResults keeps a row out of the SOLVE on
    #   the same band and the boards gate on it, so a mark this rejects is
    #   one the engine would not have stood behind either.
    #
    # ! THE ROW IS SKIPPED, NOT THE SEASON. One corrupt distance is one bad
    #   mark; the athlete's other races in that season still speak for them.
    band = {g: poolBandFor(ABILITY_CURVE_POOL[g], ABILITY_CURVE_SPORT)
            for g in ("M", "F")}
    best = {}
    n_rows = n_used = n_wild = 0
    wild = []
    for sport in sports:
        for batch in streamResults(sport, min_time, max_time):
            for row in batch:
                n_rows += 1
                gender = row[_I["gender"]]
                if gender not in ("M", "F"):
                    continue
                pid = row[_I["person_id"]]
                dist = row[_I["dist_m"]]
                t = row[_I["time_seconds"]]
                date = row[_I["date"]]
                if pid is None or not dist or not t or not date:
                    continue
                try:
                    ay = seasonYearFromIso(sport, str(date))
                except Exception:                            # noqa: BLE001
                    ay = None
                if ay is None:
                    continue
                f = _scaleFactor(float(dist), ABILITY_CURVE_POOL[gender],
                                 ABILITY_CURVE_SPORT)
                if not f:
                    continue
                nt = float(t) * f
                lo, hi = band[gender]
                if not (lo <= nt <= hi):
                    n_wild += 1
                    if len(wild) < 12:
                        wild.append((int(pid), sport, float(dist),
                                     float(t), nt))
                    continue
                n_used += 1
                key = (int(pid), int(ay))
                have = best.get(key)
                if have is None or nt < have[0]:
                    best[key] = (nt, gender)
            if limit and n_used >= limit:
                break
        if limit and n_used >= limit:
            break

    hist = {g: [0] * (len(_BINS) + 1) for g in ("M", "F")}
    able = {}
    for key, (nt, gender) in best.items():
        row = hist[gender]
        i = 0
        while i < len(_BINS) and nt >= _BINS[i]:
            i += 1
        row[i] += 1
        if nt < PRO_ABILITY_5K[gender]:
            able[key] = (nt, gender)
    return able, best, hist, n_rows, n_used, n_wild, wild


def report(able, best, hist, n_rows, n_used, n_wild, wild, show=0):
    print(f"\n  {n_rows:,} rows streamed, {n_used:,} usable "
          f"({len(best):,} athlete-seasons)")
    if n_wild:
        lo, hi = poolBandFor(ABILITY_CURVE_POOL["M"], ABILITY_CURVE_SPORT)
        print(f"\n  ⚠ {n_wild:,} rows rejected outside the engine's own pace "
              f"band ({_clock(lo)}-{_clock(hi)} as a 5K-equivalent;\n"
              f"    normalize_distance.PACE_FLOOR = 0.12 s/m, faster than any "
              f"human over any distance).\n"
              f"    These passed streamResults because their STORED "
              f"normalized_time is in range -- it was\n"
              f"    written with a different distance than dist_m now "
              f"carries. Worth chasing upstream:")
        print(f"      {'person':>10} {'sp':>3} {'dist_m':>12} {'time_s':>9}"
              f" {'5K-equiv':>10}")
        for pid, sp, dist, t, nt in wild:
            print(f"      {pid:>10} {sp:>3} {dist:>12,.0f} {t:>9.1f} "
                  f"{_clock(nt):>10}")
    print(f"\n  the bar: men {_clock(PRO_ABILITY_5K['M'])}, "
          f"women {_clock(PRO_ABILITY_5K['F'])} "
          f"-- 5K-equivalent on the {ABILITY_CURVE_POOL['M']}/"
          f"{ABILITY_CURVE_POOL['F']} {ABILITY_CURVE_SPORT} curve")

    print("\n  best 5K-equivalent per athlete-season")
    edges = ["under " + _clock(_BINS[0])]
    edges += [f"{_clock(_BINS[i])}-{_clock(_BINS[i + 1])}"
              for i in range(len(_BINS) - 1)]
    edges.append("over " + _clock(_BINS[-1]))
    print(f"      {'band':<18}{'men':>12}{'women':>12}")
    for i, label in enumerate(edges):
        mark = ""
        if i < len(_BINS) and _BINS[i] == PRO_ABILITY_5K["M"]:
            mark = "  <- men's bar"
        if i < len(_BINS) and _BINS[i] == PRO_ABILITY_5K["F"]:
            mark = "  <- women's bar"
        print(f"      {label:<18}{hist['M'][i]:>12,}{hist['F'][i]:>12,}{mark}")

    n_m = sum(1 for _nt, g in able.values() if g == "M")
    n_f = len(able) - n_m
    print(f"\n  ABLE: {len(able):,} athlete-seasons "
          f"({n_m:,} men, {n_f:,} women) -- {100.0 * len(able) / max(len(best), 1):.3f}%"
          f" of all rated athlete-seasons")
    print("    everyone else falls through to their grade, and where there is"
          "\n    no grade, no school level, no season verdict and no race"
          "\n    ceiling, pool_resolve returns None and the row goes unrated"
          "\n    (owner's ruling, 2026-09-22: \"drop them\").")

    if show:
        rows = sorted(able.items(), key=lambda kv: kv[1][0])[:show]
        print(f"\n  the {len(rows)} fastest:")
        print(f"      {'person':>10} {'season':>7} {'g':>2} {'5K-equiv':>10}")
        for (pid, ay), (nt, g) in rows:
            print(f"      {pid:>10} {ay:>7} {g:>2} {_clock(nt):>10}")


def write(able):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS pro_ability_season")
        cur.execute(_DDL)
        # ! execute_values, NOT executemany. executemany issues one
        #   round-trip per row; the able set is small by design but "small"
        #   here is still five or six figures, and a per-row round-trip
        #   turns a two-second write into several minutes on a pipeline the
        #   owner has already called too slow.
        from psycopg2.extras import execute_values
        execute_values(
            cur,
            "INSERT INTO pro_ability_season "
            "(person_id, season, best_nt, gender) VALUES %s",
            [(pid, ay, float(nt), g) for (pid, ay), (nt, g) in able.items()],
            page_size=10_000)
        cur.execute("CREATE INDEX ON pro_ability_season (season)")
        conn.commit()
    print(f"\n  wrote {len(able):,} rows to pro_ability_season.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    ap.add_argument("--write", action="store_true",
                    help="replace pro_ability_season (default: report only)")
    ap.add_argument("--dry-run", action="store_true", help="the default")
    ap.add_argument("--show", type=int, default=0,
                    help="also print the N fastest athlete-seasons")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N usable rows (a smoke test, never --write)")
    ap.add_argument("--sport", choices=("XC", "TF"), default=None)
    args = ap.parse_args(argv)
    if args.limit and args.write:
        ap.error("--limit builds a partial table; it may not be written")

    sports = (args.sport,) if args.sport else ("XC", "TF")
    able, best, hist, n_rows, n_used, n_wild, wild = scan(
        sports=sports, limit=args.limit or None)
    report(able, best, hist, n_rows, n_used, n_wild, wild, show=args.show)
    if args.write:
        write(able)
    else:
        print("\n  ! nothing was written (--write to build the table).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
