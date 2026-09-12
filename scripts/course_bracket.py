#!/usr/bin/env python3
"""
course_bracket.py -- how much slower did people run HERE than in their own
races a few weeks either side, race day by race day, against what the
model booked for the same day.

    scripts/course_bracket.py --key "XC:12345" --key "TF:loc:99"
    scripts/course_bracket.py --key "XC:12345" --window 21 --top 0.25 --era-years 2
    scripts/venue_check.py --search Ultimook        # to find the key first

Run from the PROJECT ROOT. Reads the pack and the solve's npz; no database
(--names looks the canonical ids up if one is reachable).

★ WHAT IT MEASURES (owner, 2026-09-12: "look at normalized time at the top
  x% at a race; for each person, compare it to what they ran the past
  couple of weeks and next couple of weeks; that's how hard the course
  is, after you account for fitness"). For every row at the venue: its
  log normalized time minus the mean of the SAME athlete-season's log
  normalized times at OTHER venues in the same sport within +-window
  days. Averaged over the race day, optionally over the top fraction of
  the field by rating. Model-free apart from the normalisation, and
  fitness cancels because the bracket is a few weeks wide. Positive =
  slower here than in their own bracket.

★ WHAT IT COMPARES IT TO. The model's number for the same day: the
  board difficulty of the cell (its era's, under --era-years), the
  race-day term, and the field-strength term, in the same log units. The
  bracket should sit near board + day (the field term is not in a
  rating, so a stacked day's bracket is expected to sit BELOW board +
  day by about the field term). Where it does not, the row says which
  piece is off, and the by-year table says whether the venue moved.

⚠ THE BRACKET OF A NATIONAL FINAL IS OTHER FINALS. Its runners' races
  three weeks either side are regionals and state meets, also peaked, so
  the bracket is "slower than at their regional", which is what was
  asked. Read it as that.
"""
import argparse
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                        # noqa: E402
import pair_engine as pe                                        # noqa: E402
import run_joint as rj                                          # noqa: E402


def bracket(cols, npz, match, window=28, top=0.0, era_years=0,
            same_sport=True, min_rows=5):
    """Per race day at every base cell whose key contains one of `match`
    (case-insensitive): the bracket measurement and the model's terms.
    Returns {base key: {"races": [row dicts], "by_year": [...]}}."""
    keys = [str(k) for k in cols["course_keys"]]
    course = np.asarray(cols["course"]).astype(np.int64)
    days = np.asarray(cols["days"]).astype(np.float64)
    year = np.asarray(cols["year"]).astype(np.int64)
    sport = np.asarray(cols["sport"]).astype(np.int64)
    ln = np.log(np.asarray(cols["norm"], dtype=np.float64))
    ath_raw = np.asarray(cols["athlete"]).astype(np.int64)
    season, n_season = pe.athleteSeasonCodes(ath_raw, year)
    race, n_race = rj.raceCodes(course, days)
    # the solve's cells: (course, era) under --era-years
    n_cells = len(keys)
    group = np.zeros(n_cells, dtype=np.int64)
    if era_years:
        ok = course >= 0
        group[np.unique(course[ok])] = 0
        (cell, _n_new, _g, cell_keys, _p, _w, _e) = rj.eraCells(
            course, year, era_years, n_cells, group, keys)
    else:
        cell, cell_keys = course, keys
    cell_keys = [str(k) for k in cell_keys]
    npz_keys = [str(k) for k in npz["course_keys"]] if "course_keys" in npz else None
    if npz_keys is not None and npz_keys != cell_keys:
        raise SystemExit(f"the npz has {len(npz_keys):,} cells and the pack "
                         f"gives {len(cell_keys):,} with --era-years "
                         f"{era_years}; pass the era width the solve used")
    delta = np.asarray(npz["delta_anchored"] if "delta_anchored" in npz
                       else npz["delta"], dtype=np.float64)
    u = (np.asarray(npz["race_effect"], dtype=np.float64)
         if "race_effect" in npz else None)
    if u is not None and u.size != n_race:
        u = None
    rating = (np.asarray(npz["rating"], dtype=np.float64)
              if "rating" in npz and np.asarray(npz["rating"]).size == n_season
              else None)
    # the field term per race, as the solve applied it
    field_row = None
    if (rating is not None and "importance" in npz
            and str(np.asarray(npz.get("importance_kind", ["field"]))[0]) == "field"):
        pool_of_raw, pool_names = rj.poolCodes(cols["athlete_keys"])
        idx = np.where(course >= 0, pool_of_raw[ath_raw] * 2 + sport, -1)
        coef = np.asarray(npz["importance"], dtype=np.float64)
        centre = (np.asarray(npz["field_centre"], dtype=np.float64)
                  if "field_centre" in npz else None)
        w, _s, _c = js.fieldStrength(rating[season], race, n_race, np.maximum(idx, 0),
                                     idx >= 0, coef.size, centre=centre)
        field_row = np.where(idx >= 0, w * coef[np.maximum(idx, 0)], 0.0)

    want = [m.lower() for m in match]
    base_ids = [i for i, k in enumerate(keys) if any(m in k.lower() for m in want)]
    out = {}
    # rows by athlete-season, then day; each season's run is [start, end)
    # ! ARRAYS, NOT A DICT BUILT IN A LOOP. The first cut indexed the full
    #   51M-row array inside a comprehension over every athlete-season and
    #   never finished (2026-09-12).
    order = np.lexsort((days, season))
    so = season[order]
    starts = np.flatnonzero(np.r_[True, so[1:] != so[:-1]])
    ends = np.r_[starts[1:], order.size]
    start_of = np.zeros(n_season, dtype=np.int64)
    end_of = np.zeros(n_season, dtype=np.int64)
    start_of[so[starts]] = starts
    end_of[so[starts]] = ends
    for b in base_ids:
        rows = np.flatnonzero(course == b)
        if rows.size == 0:
            continue
        br = np.full(rows.size, np.nan)
        for j, r in enumerate(rows):
            s, e = int(start_of[season[r]]), int(end_of[season[r]])
            idx_o = order[s:e]
            m = ((np.abs(days[idx_o] - days[r]) <= window) & (course[idx_o] != b)
                 & (course[idx_o] >= 0))
            if same_sport:
                m &= sport[idx_o] == sport[r]
            if m.any():
                br[j] = ln[r] - ln[idx_o[m]].mean()
        races = []
        for rid in np.unique(race[rows]):
            in_race = rows[race[rows] == rid]
            if in_race.size < min_rows:
                continue
            bj = br[np.isin(rows, in_race)]
            got = np.isfinite(bj)
            rr = rating[season[in_race]] if rating is not None else None
            front = np.nan
            if rr is not None:
                front = float(np.sort(rr)[::-1][:js.FIELD_TOP_K].mean())
            rec = {"race": int(rid), "year": int(year[in_race[0]]),
                   "days_ago": float(days[in_race[0]]),
                   "cell_key": cell_keys[int(cell[in_race[0]])],
                   "n": int(in_race.size), "n_bracketed": int(got.sum()),
                   "front": front,
                   "depth": float(np.mean(rr)) if rr is not None else np.nan,
                   "bracket": float(bj[got].mean()) if got.any() else np.nan,
                   "bracket_top": np.nan,
                   "board": float(delta[int(cell[in_race[0]])]),
                   "day": float(u[rid]) if u is not None else np.nan,
                   "field": (float(field_row[in_race].mean())
                             if field_row is not None else np.nan)}
            if top and rating is not None:
                k = max(int(math.ceil(top * in_race.size)), 1)
                top_rows = np.argsort(-rr)[:k]
                bt = bj[top_rows]
                if np.isfinite(bt).any():
                    rec["bracket_top"] = float(bt[np.isfinite(bt)].mean())
            races.append(rec)
        by_year = []
        for yr in sorted({r["year"] for r in races}):
            rs = [r for r in races if r["year"] == yr and np.isfinite(r["bracket"])]
            if not rs:
                continue
            n = sum(r["n_bracketed"] for r in rs)
            by_year.append({"year": yr, "races": len(rs), "n": n,
                            "bracket": sum(r["bracket"] * r["n_bracketed"] for r in rs) / max(n, 1),
                            "board": sum(r["board"] * r["n"] for r in rs) / max(sum(r["n"] for r in rs), 1),
                            "day": (sum(r["day"] * r["n"] for r in rs) / max(sum(r["n"] for r in rs), 1)
                                    if u is not None else np.nan)})
        slope = np.nan
        if len(by_year) >= 3:
            ys = np.array([b["year"] for b in by_year], dtype=np.float64)
            bs = np.array([b["bracket"] for b in by_year])
            ws = np.array([b["n"] for b in by_year], dtype=np.float64)
            slope = float(np.polyfit(ys, bs, 1, w=np.sqrt(ws))[0])
        out[keys[b]] = {"races": races, "by_year": by_year, "slope_per_year": slope}
    return out


def _pct(v):
    return "      " if not np.isfinite(v) else f"{100 * v:+6.2f}"


def report(result, names=None, top=0.0):
    for key, res in result.items():
        name = names.get(key.split(":")[1], "") if names else ""
        print(f"\n{key}  {name}")
        print(f"  {'year':>5} {'days ago':>9} {'era':>5} {'rows':>6} {'brkt':>6} "
              f"{'front':>6} {'depth':>6} "
              f"{'bracket':>8} {'top' + (f'{int(100 * top)}%' if top else ''):>7} "
              f"{'board':>7} {'day':>7} {'field':>7} {'board+day':>10}")
        for r in sorted(res["races"], key=lambda r: -r["days_ago"]):
            era = r["cell_key"].rpartition("@e")[2] if "@e" in r["cell_key"] else "-"
            fr = f"{r['front']:6.1f}" if np.isfinite(r["front"]) else "      "
            dp = f"{r['depth']:6.1f}" if np.isfinite(r["depth"]) else "      "
            print(f"  {r['year']:>5} {r['days_ago']:>9.0f} {era:>5} {r['n']:>6,} "
                  f"{r['n_bracketed']:>6,} {fr} {dp} "
                  f"{_pct(r['bracket']):>8} {_pct(r['bracket_top']):>7} "
                  f"{_pct(r['board']):>7} {_pct(r['day']):>7} {_pct(r['field']):>7} "
                  f"{_pct(r['board'] + (r['day'] if np.isfinite(r['day']) else 0.0)):>10}")
        if res["by_year"]:
            print(f"  by year:   {'year':>5} {'races':>6} {'rows':>7} {'bracket':>8} "
                  f"{'board':>7} {'day':>7}")
            for b in res["by_year"]:
                print(f"             {b['year']:>5} {b['races']:>6} {b['n']:>7,} "
                      f"{_pct(b['bracket']):>8} {_pct(b['board']):>7} {_pct(b['day']):>7}")
            if np.isfinite(res["slope_per_year"]):
                print(f"  bracket trend: {100 * res['slope_per_year']:+.2f}% per year "
                      f"(+ = the venue is getting slower relative to its runners' "
                      f"other races)")
        print("  read: bracket = slower here than the same people's other races in "
              "the window (log %, + = harder); board + day is what the model "
              "booked for the day; a stacked day's bracket sits below it by "
              "about the field term, which is not in a rating. front = mean "
              "rating of the day's top five, depth = mean rating of the field.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults = rj.buildParser()
    ap.add_argument("--pack", default=defaults.get_default("pack"))
    ap.add_argument("--npz", default=defaults.get_default("out"))
    ap.add_argument("--key", action="append", default=[],
                    help="substring of a cell key (XC:<canonical id> or "
                         "TF:loc:<id>); repeatable")
    ap.add_argument("--venue", action="append", default=[],
                    help="part of a canonical course NAME (needs the database "
                         "for the name list); repeatable. 'Glendoveer', 'Balboa'")
    ap.add_argument("--window", type=float, default=28.0,
                    help="days either side for the same athlete's other races")
    ap.add_argument("--top", type=float, default=0.0,
                    help="also the top fraction of each race's field by rating")
    ap.add_argument("--era-years", type=int, default=0,
                    help="the era width the solve used (XCP_ERA_YEARS), if any")
    ap.add_argument("--any-sport", action="store_true",
                    help="bracket against the athlete's other races in either sport")
    ap.add_argument("--names", action="store_true",
                    help="look canonical ids up in the database for display")
    args = ap.parse_args()
    if not args.key and not args.venue:
        ap.error("give --key or --venue")
    names = None
    if args.names or args.venue:
        try:
            from speed_ratings_db import loadCanonicalNames
            names = loadCanonicalNames()          # {canonical id as text: name}
        except Exception as exc:                                 # noqa: BLE001
            if args.venue:
                sys.exit(f"--venue needs the database for the name list: "
                         f"{type(exc).__name__}: {exc}")
            print(f"(no names: {type(exc).__name__}: {exc})")
    match = list(args.key)
    for text in args.venue:
        hits = [cid for cid, nm in (names or {}).items()
                if text.lower() in str(nm).lower()]
        if not hits:
            sys.exit(f"no canonical course name contains {text!r}")
        for cid in hits:
            print(f"  --venue {text!r}: {names[cid]}  ->  XC:{cid}:")
            match.append(f"XC:{cid}:")
    cols = pe.loadPack(args.pack)
    npz = dict(np.load(args.npz, allow_pickle=False))
    res = bracket(cols, npz, match, window=args.window, top=args.top,
                  era_years=args.era_years, same_sport=not args.any_sport)
    if not res:
        sys.exit("no cell key matched")
    report(res, names, top=args.top)


if __name__ == "__main__":
    main()
