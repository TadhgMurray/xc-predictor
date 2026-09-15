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

import bracket as bk                                            # noqa: E402
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402


def bracket(cols, npz, match, window=28, top=0.0, era_years=0,
            same_sport=True, min_rows=5, use_curve=True, subset=True, codes=None,
            race_sat=5.0, min_voters=3):
    """Per race day at every base cell whose key contains one of `match`
    (case-insensitive): the bracket measurement and the model's terms.
    The bracket is engine/bracket.py's: the same athlete-season's other
    rows in the sport within the window at other base cells, with the
    form curve taken out of every row when the solve file has one.
    codes: bracket.packCodes' whole-pack codes (computed here when not
    given): the race ids, season codes and the solve file's cells are
    numbered over the whole pack, so the venue subset below still indexes
    the file's day terms, ratings and difficulties.
    Returns {base key: {"races": [row dicts], "by_year": [...]}}."""
    if codes is None or "_cell" not in cols:
        try:
            cols, codes = bk.packCodes(cols, npz, era_years)
        except ValueError as exc:
            raise SystemExit(str(exc))
    if npz is not None and "bracket_race_sat" in npz:
        race_sat = float(np.asarray(npz["bracket_race_sat"]).reshape(-1)[0])
    keys, n_base = codes["keys"], codes["n_base"]
    want = [m.lower() for m in match]
    base_ids = [i for i, k in enumerate(keys) if any(m in k.lower() for m in want)]
    n_race, n_season = codes["n_race"], codes["n_season"]
    cell_keys = [str(k) for k in codes["cell_keys"]]
    if subset and base_ids:
        # ! ONLY THE ATHLETE-SEASONS THAT RACED THE VENUE, with all their rows:
        #   a bracket is within an athlete-season, so nothing else in the
        #   corpus can change it, and the sorts below run on thousands of
        #   rows instead of fifty million (2026-09-12: "way too long").
        is_venue = np.zeros(n_base, dtype=bool); is_venue[base_ids] = True
        c0 = np.asarray(cols["course"]).astype(np.int64)
        at_venue = (c0 >= 0) & is_venue[np.maximum(c0, 0)]
        cols = bk.subsetCols(cols, bk.rowsOfSeasons(cols, at_venue))
    course = np.asarray(cols["course"]).astype(np.int64)
    days = np.asarray(cols["days"]).astype(np.float64)
    year = np.asarray(cols["year"]).astype(np.int64)
    sport = np.asarray(cols["sport"]).astype(np.int64)
    ath_raw = np.asarray(cols["athlete"]).astype(np.int64)
    rows_all = bk.bracketRows(cols, npz, window=window, use_curve=use_curve,
                              season=cols["_season"], n_season=n_season)
    ln = rows_all["z"]                    # log time less the athlete's curve point
    season = rows_all["season"]
    race = np.asarray(cols["_race"]).astype(np.int64)
    cell = np.asarray(cols["_cell"]).astype(np.int64)
    if era_years and (cell[course >= 0] < 0).any():
        n_bad = int((cell[course >= 0] < 0).sum())
        print(f"  ({n_bad:,} rows fall in a (course, era) the solve did not "
              f"key; they carry no board number)")
    delta = np.asarray(npz["delta_anchored"] if "delta_anchored" in npz
                       else npz["delta"], dtype=np.float64)
    if delta.size != len(cell_keys):
        raise SystemExit(f"the solve file has {delta.size:,} difficulties and the "
                         f"pack maps to {len(cell_keys):,} cells; pass the era "
                         f"width the solve used")
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
        pool_of_raw = codes["pool_of_raw"]
        idx = np.where(course >= 0, pool_of_raw[ath_raw] * 2 + sport, -1)
        coef = np.asarray(npz["importance"], dtype=np.float64)
        centre = (np.asarray(npz["field_centre"], dtype=np.float64)
                  if "field_centre" in npz else None)
        w, _s, _c = js.fieldStrength(rating[season], race, n_race, np.maximum(idx, 0),
                                     idx >= 0, coef.size, centre=centre)
        field_row = np.where(idx >= 0, w * coef[np.maximum(idx, 0)], 0.0)

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
        ref = np.full(rows.size, np.nan)      # the model's board + day of the OTHER races
        ref_d = np.full(rows.size, np.nan)    # the board alone: what the engine subtracts
        for j, r in enumerate(rows):
            s, e = int(start_of[season[r]]), int(end_of[season[r]])
            idx_o = order[s:e]
            m = ((np.abs(days[idx_o] - days[r]) <= window) & (course[idx_o] != b)
                 & (course[idx_o] >= 0))
            if same_sport:
                m &= sport[idx_o] == sport[r]
            if m.any():
                o = idx_o[m]
                br[j] = ln[r] - ln[o].mean()      # == rows_all["bracket"][r] when same_sport
                oc = cell[o]
                ok_o = oc >= 0
                if ok_o.any():
                    ref[j] = float(np.mean(delta[oc[ok_o]]
                                           + (u[race[o][ok_o]] if u is not None else 0.0)))
                    ref_d[j] = float(np.mean(delta[oc[ok_o]]))
        # ★ THE ENGINE'S READING OF EACH ROW (2026-09-13): the bracket over
        #   the tilt, plus the board of the races it was measured against.
        #   That is exactly what bracket_engine votes with -- (z - a_local)/h
        #   with a_local read off the same references less h * D -- so the
        #   race's mean over its top-half voters is the number the engine
        #   put into the cell, and the cell block below says what the
        #   priors did with it.
        h_row = np.ones(rows.size)
        if rating is not None:
            r_clip = np.clip(np.nan_to_num(rating[season[rows]], nan=100.0),
                             js.TILT_RATING_LO, js.TILT_RATING_HI)
            h_row = 1.0 + js.TILT_K * (r_clip - 100.0) / 10.0
        reading = br / h_row + ref_d
        races = []
        for rid in np.unique(race[rows]):
            in_race = rows[race[rows] == rid]
            if in_race.size < min_rows:
                continue
            sel = np.isin(rows, in_race)
            bj = br[sel]
            rj_ = ref[sel]
            got = np.isfinite(bj)
            rr = rating[season[in_race]] if rating is not None else None
            front = np.nan
            if rr is not None:
                front = float(np.sort(rr)[::-1][:js.FIELD_TOP_K].mean())
            c0 = int(cell[in_race[0]])
            # the engine's voters: the top half of the race by rating, with
            # a level to compare to; their mean reading and applied tilt
            n_vote, h_vote, read = 0, np.nan, np.nan
            if rr is not None:
                k_v = max(int(math.ceil(0.5 * in_race.size)), 1)
                top_v = np.argsort(-rr)[:k_v]
                rv = reading[sel][top_v]
                fv = np.isfinite(rv)
                n_vote = int(fv.sum())
                if n_vote:
                    read = float(rv[fv].mean())
                    h_vote = float(h_row[sel][top_v][fv].mean())
            rec = {"race": int(rid), "year": int(year[in_race[0]]),
                   "days_ago": float(days[in_race[0]]),
                   "cell_key": cell_keys[c0] if c0 >= 0 else keys[b],
                   "n": int(in_race.size), "n_bracketed": int(got.sum()),
                   "front": front,
                   "depth": float(np.mean(rr)) if rr is not None else np.nan,
                   "bracket": float(bj[got].mean()) if got.any() else np.nan,
                   # what the model booked for the races the bracket is
                   # measured against: bracket + ref is the bracket on the
                   # board's own scale, to read against board + day
                   "ref": float(rj_[got].mean()) if got.any() else np.nan,
                   "bracket_top": np.nan,
                   "voters": n_vote, "h": h_vote, "reading": read,
                   "race_weight": (n_vote / (n_vote + race_sat)
                                   if n_vote >= min_voters else 0.0),
                   "board": float(delta[c0]) if c0 >= 0 else np.nan,
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
                            "implied": sum((r["bracket"] + r["ref"]) * r["n_bracketed"] for r in rs) / max(n, 1),
                            "board": sum(r["board"] * r["n"] for r in rs) / max(sum(r["n"] for r in rs), 1),
                            "day": (sum(r["day"] * r["n"] for r in rs) / max(sum(r["n"] for r in rs), 1)
                                    if u is not None else np.nan)})
        slope = np.nan
        if len(by_year) >= 3:
            ys = np.array([b["year"] for b in by_year], dtype=np.float64)
            bs = np.array([b["bracket"] for b in by_year])
            ws = np.array([b["n"] for b in by_year], dtype=np.float64)
            slope = float(np.polyfit(ys, bs, 1, w=np.sqrt(ws))[0])
        out[keys[b]] = {"races": races, "by_year": by_year, "slope_per_year": slope,
                        "engine": engineCells(npz, cell_keys, keys[b], delta)}
    return out


def engineCells(npz, cell_keys, base_key, delta):
    """★ THE SHRINKAGE CHAIN, PER CELL OF THE VENUE (owner, 2026-09-13:
    Foot Locker against Glendoveer -- "what's going on is the question").
    From the solve file written under --difficulty bracket: the era's own
    vote-mean of its races (raw), its votes and races; the course's
    history after the group prior (base) and the votes behind it; the era
    after the era prior; the (sport, era) pin and the recentring taken
    off; the published number. Empty when the file is not the engine's."""
    need = ("bracket_cell_raw", "bracket_votes", "bracket_races_per_cell",
            "bracket_base", "bracket_base_votes", "bracket_pin", "bracket_shift",
            "bracket_cell_fit")
    if npz is None or any(k not in npz for k in need):
        return []
    raw = np.asarray(npz["bracket_cell_raw"], dtype=np.float64)
    if raw.size != len(cell_keys):
        return []
    votes = np.asarray(npz["bracket_votes"], dtype=np.float64)
    races = np.asarray(npz["bracket_races_per_cell"])
    base = np.asarray(npz["bracket_base"], dtype=np.float64)
    base_votes = np.asarray(npz["bracket_base_votes"], dtype=np.float64)
    pin = np.asarray(npz["bracket_pin"], dtype=np.float64)
    shift = np.asarray(npz["bracket_shift"], dtype=np.float64)
    fit = np.asarray(npz["bracket_cell_fit"], dtype=np.float64)
    # the per-sport course scale (2026-09-15), 1.0 on a state without it
    scale = (np.asarray(npz["bracket_scale"], dtype=np.float64) if "bracket_scale" in npz
             and np.asarray(npz["bracket_scale"]).size == len(cell_keys) else np.ones(len(cell_keys)))
    place = (np.asarray(npz["bracket_place"], dtype=np.int64) if "bracket_place" in npz
             and np.asarray(npz["bracket_place"]).size == len(cell_keys) else None)
    place_size = np.bincount(place[place >= 0], minlength=int(place.max()) + 1) if place is not None and (place >= 0).any() else None
    prior_races = float(np.asarray(npz.get("bracket_prior_races", [2.0])).reshape(-1)[0])
    kg = np.asarray(npz.get("bracket_prior_group", []), dtype=np.float64).reshape(-1)
    names = [str(x) for x in np.asarray(npz.get("bracket_prior_group_names", [])).reshape(-1)]
    mu = np.asarray(npz.get("mu", [0.0, 0.0]), dtype=np.float64).reshape(-1)
    out = []
    for c, k in enumerate(cell_keys):
        bare = k.split("@", 1)[0]
        if bare != base_key:
            continue
        g = 0 if bare.startswith("XC:") else (2 if bare.endswith(":in") else 1)
        k_g = float(kg[g]) if kg.size > g else np.nan
        era = (raw[c] * votes[c] + prior_races * base[c]) / (votes[c] + prior_races)
        pl = int(place[c]) if place is not None else -1
        out.append({"cell_key": k, "races": int(races[c]), "votes": float(votes[c]),
                    "place": pl, "place_cells": int(place_size[pl]) if pl >= 0 else 0,
                    "raw": float(raw[c]), "base": float(base[c]),
                    "base_votes": float(base_votes[c]), "group": names[g] if g < len(names) else "",
                    "prior_group": k_g, "prior_races": prior_races,
                    "era": float(era), "pin": float(pin[c]), "shift": float(shift[c]),
                    "fit": float(fit[c]), "scale": float(scale[c]),
                    "level": float(mu[1] if bare.startswith("TF:") else mu[0]),
                    "published": float(delta[c])})
    return out


def _pct(v):
    return "      " if not np.isfinite(v) else f"{100 * v:+6.2f}"


def report(result, names=None, top=0.0):
    for key, res in result.items():
        name = names.get(key.split(":")[1], "") if names else ""
        print(f"\n{key}  {name}")
        engine = any(np.isfinite(r.get("reading", np.nan)) for r in res["races"])
        print(f"  {'year':>5} {'days ago':>9} {'era':>5} {'rows':>6} {'brkt':>6} "
              f"{'front':>6} {'depth':>6} "
              f"{'bracket':>8} {'top' + (f'{int(100 * top)}%' if top else ''):>7} "
              f"{'ref':>7} {'implied':>8} "
              + (f"{'voters':>7} {'h':>6} {'reading':>8} " if engine else "")
              + f"{'board':>7} {'day':>7} {'field':>7} {'board+day':>10}")
        for r in sorted(res["races"], key=lambda r: -r["days_ago"]):
            era = r["cell_key"].rpartition("@e")[2] if "@e" in r["cell_key"] else "-"
            fr = f"{r['front']:6.1f}" if np.isfinite(r["front"]) else "      "
            dp = f"{r['depth']:6.1f}" if np.isfinite(r["depth"]) else "      "
            eng = ""
            if engine:
                hv = f"{r['h']:6.3f}" if np.isfinite(r.get("h", np.nan)) else "      "
                eng = f"{r.get('voters', 0):>7,} {hv} {_pct(r.get('reading', np.nan)):>8} "
            print(f"  {r['year']:>5} {r['days_ago']:>9.0f} {era:>5} {r['n']:>6,} "
                  f"{r['n_bracketed']:>6,} {fr} {dp} "
                  f"{_pct(r['bracket']):>8} {_pct(r['bracket_top']):>7} "
                  f"{_pct(r['ref']):>7} {_pct(r['bracket'] + r['ref']):>8} "
                  + eng
                  + f"{_pct(r['board']):>7} {_pct(r['day']):>7} {_pct(r['field']):>7} "
                  f"{_pct(r['board'] + (r['day'] if np.isfinite(r['day']) else 0.0)):>10}")
        if res.get("engine"):
            print("  the engine's arithmetic (--difficulty bracket), per (course, era) cell:")
            print(f"    {'cell':<28} {'races':>5} {'votes':>6} {'raw':>7} "
                  f"{'history':>8} {'(votes':>7} {'prior)':>7} {'place':>9} {'era':>7} {'pin':>7} "
                  f"{'recentre':>9} {'scale':>6} {'level':>7} {'published':>10}")
            for e in res["engine"]:
                pl = (f"#{e['place']}({e['place_cells']})" if e.get("place", -1) >= 0 else "-")
                print(f"    {e['cell_key']:<28} {e['races']:>5} {e['votes']:>6.2f} "
                      f"{_pct(e['raw']):>7} {_pct(e['base']):>8} {e['base_votes']:>7.2f} "
                      f"{e['prior_group']:>7.2f} {pl:>9} {_pct(e['era']):>7} {_pct(-e['pin']):>7} "
                      f"{_pct(-e['shift']):>9} {e.get('scale', 1.0):>6.3f} {_pct(e['level']):>7} "
                      f"{_pct(e['published']):>10}")
            print("    read: raw = the era's vote-weighted mean of its races' readings "
                  "(each race weighs voters/(voters+sat)); history = every era of the "
                  "course pulled toward its group's average by `prior` races' worth; "
                  "place = the cells this course rests on first (#id, how many) when the "
                  "pack carries coordinates; "
                  "era = (raw x votes + 2 x history) / (votes + 2); then the (sport, "
                  "era) pin and the recentring come off and the sport level goes on. "
                  "A course whose raw reading is right but whose published number is "
                  "not is thin: few races, or its meets keyed under several ids "
                  "(scripts/meet_cells.py --meet).")
        if res["by_year"]:
            print(f"  by year:   {'year':>5} {'races':>6} {'rows':>7} {'bracket':>8} "
                  f"{'implied':>8} {'board':>7} {'day':>7} {'board+day':>10}")
            for b in res["by_year"]:
                print(f"             {b['year']:>5} {b['races']:>6} {b['n']:>7,} "
                      f"{_pct(b['bracket']):>8} {_pct(b['implied']):>8} "
                      f"{_pct(b['board']):>7} {_pct(b['day']):>7} "
                      f"{_pct(b['board'] + (b['day'] if np.isfinite(b['day']) else 0.0)):>10}")
            if np.isfinite(res["slope_per_year"]):
                print(f"  bracket trend: {100 * res['slope_per_year']:+.2f}% per year "
                      f"(+ = the venue is getting slower relative to its runners' "
                      f"other races)")
        print("  read: bracket = slower here than the same people's other races in "
              "the window (log %, + = harder). Those other races have their own "
              "difficulty: ref is the board + day the model gave them, and "
              "implied = bracket + ref is the bracket on the board's scale. "
              "voters / h / reading: the bracket engine's top-half voters, the "
              "tilt applied to them, and their mean reading = bracket / h + the "
              "board of their references (no day) -- the number the engine put "
              "into the cell. Read "
              "implied against board + day; the gap between them is what the "
              "model and the runners disagree about for that day (the field "
              "term is not in a rating, so a stacked day's implied can sit "
              "below board + day by about it). front = mean rating of the day's "
              "top five, depth = mean rating of the field.")


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
    ap.add_argument("--no-curve", action="store_true",
                    help="compare raw log times; by default the solve file's form "
                         "curve is taken out of every row first")
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
            print(f"  --venue {text!r}: no canonical course name contains it; skipped")
            continue
        for cid in hits:
            print(f"  --venue {text!r}: {names[cid]}  ->  XC:{cid}:")
            match.append(f"XC:{cid}:")
    if not match:
        sys.exit("nothing to look up: no --key and no --venue matched")
    cols, npz = bk.loadInputs(args.pack, args.npz)
    if npz is None:
        sys.exit(f"no solve file at {args.npz}")
    print(f"[bracket] {np.asarray(cols['norm']).size:,} rows loaded; coding the "
          f"corpus once (half a minute)", flush=True)
    res = bracket(cols, npz, match, window=args.window, top=args.top,
                  era_years=args.era_years, same_sport=not args.any_sport,
                  use_curve=not args.no_curve)
    if not res:
        sys.exit("no cell key matched")
    report(res, names, top=args.top)


if __name__ == "__main__":
    main()
