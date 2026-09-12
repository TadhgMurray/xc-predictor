#!/usr/bin/env python3
"""
track_variance.py -- why do outdoor tracks differ in difficulty? From the
pack and the solve file; no database and no rerun.

    scripts/track_variance.py --era-years 2
    scripts/track_variance.py --era-years 2 --min-rows 500 --window 21

Every 400 m track is the same 400 m, so a spread of several percent in
their difficulties is not the surface. The candidates are what each track
HOSTS: paced invitationals with strong fronts, tactical championship
finals, and its weather. Per outdoor track cell this prints the board
number beside the same-athlete bracket of its rows (engine/bracket.py,
curve-corrected), the mean front of its races and the share of its rows
at championship-class meets (the pack's name-based meet_class, a
diagnostic column). Then, pooled WITHIN tracks so the track itself cancels:
how much slower a championship-class row runs than an ordinary row at the
same track, and how much faster a row in a stronger field runs. Then the
correlations across tracks of the board number with the mix. If the mix
explains the spread, the tracks are not different, their meets are.
"""
import argparse
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
import pair_engine as pe                                        # noqa: E402
import run_joint as rj                                          # noqa: E402


def analyse(cols, npz, era_years=0, min_rows=300, window=21, use_curve=True):
    keys = [str(k) for k in cols["course_keys"]]
    n_base = len(keys)
    is_out = np.array([k.startswith("TF:") and k.split("@", 1)[0].endswith(":out")
                       for k in keys], dtype=bool)
    course = np.asarray(cols["course"]).astype(np.int64)
    year = np.asarray(cols["year"]).astype(np.int64)
    # the published number per base cell: its latest solved era
    group = np.zeros(n_base, dtype=np.int64)
    if era_years:
        (cell, _n, _g, cell_keys, _p, _w, _e) = rj.eraCells(course, year, era_years,
                                                             n_base, group, keys)
    else:
        cell, cell_keys = course, keys
    cell_keys = [str(k) for k in cell_keys]
    if "course_keys" in npz and [str(k) for k in npz["course_keys"]] != cell_keys:
        raise SystemExit("the solve file's cells do not match the pack with this "
                         "--era-years; pass the width the solve used")
    delta = np.asarray(npz["delta_anchored"] if "delta_anchored" in npz else npz["delta"],
                       dtype=np.float64)
    rows_per_cell = np.bincount(cell[cell >= 0], minlength=len(cell_keys))
    board = np.full(n_base, np.nan)
    base_of = np.array([int(k.partition("@e")[2]) if "@e" in k else -1 for k in cell_keys])
    bmap = {}
    for i, k in enumerate(cell_keys):
        if rows_per_cell[i] == 0:
            continue
        b = k.partition("@e")[0]
        e = base_of[i]
        if b not in bmap or e > bmap[b][0]:
            bmap[b] = (e, i)
    key_to_base = {k: i for i, k in enumerate(keys)}
    for b, (_e, i) in bmap.items():
        if b in key_to_base:
            board[key_to_base[b]] = delta[i]
    # the bracket per row
    res = bk.bracketRows(cols, npz, window=window, use_curve=use_curve)
    br = res["bracket"]; season = res["season"]
    # the front per race, from the solve's ratings
    race, n_race = rj.raceCodes(course, cols["days"])
    rating = None
    if "rating" in npz and np.asarray(npz["rating"]).size == res["n_season"]:
        rating = np.asarray(npz["rating"], dtype=np.float64)[season]
    front_race = js.raceFront(rating, race, n_race) if rating is not None else np.full(n_race, np.nan)
    front_row = front_race[race]
    mclass = (np.asarray(cols["meet_class"]).astype(np.int64) if "meet_class" in cols
              else np.zeros(course.size, dtype=np.int64))
    sport = np.asarray(cols["sport"]).astype(np.int64)
    sel = (course >= 0) & is_out[np.maximum(course, 0)] & (sport == 1) & np.isfinite(br)
    cnt = np.bincount(course[sel], minlength=n_base)
    big = np.flatnonzero((cnt >= min_rows) & is_out & np.isfinite(board))
    if big.size == 0:
        raise SystemExit("no outdoor track cell with that many bracketed rows")
    def cell_mean(v, m):
        s = np.bincount(course[m], weights=v[m], minlength=n_base)
        k = np.bincount(course[m], minlength=n_base)
        return np.where(k > 0, s / np.maximum(k, 1), np.nan)
    mean_br = cell_mean(br, sel)
    mean_front = cell_mean(np.nan_to_num(front_row, nan=100.0), sel)
    champ_share = cell_mean((mclass >= 2).astype(np.float64), sel)
    league_share = cell_mean((mclass == 1).astype(np.float64), sel)
    # within-track deviations, pooled
    dev = br - mean_br[np.maximum(course, 0)]
    fdev = np.nan_to_num(front_row, nan=100.0) - mean_front[np.maximum(course, 0)]
    m_big = sel & np.isin(course, big)
    by_class = {}
    for c in range(4):
        m = m_big & (mclass == c)
        if m.sum() >= 1000:
            by_class[c] = (int(m.sum()), float(dev[m].mean()),
                           float(dev[m].std() / np.sqrt(m.sum())))
    terc = np.full(course.size, -1)
    if m_big.any():
        q1, q2 = np.percentile(fdev[m_big], [33.3, 66.7])
        terc[m_big] = np.digitize(fdev[m_big], [q1, q2])
    by_terc = {}
    for t in range(3):
        m = m_big & (terc == t)
        if m.sum() >= 1000:
            by_terc[t] = (int(m.sum()), float(fdev[m].mean()), float(dev[m].mean()),
                          float(dev[m].std() / np.sqrt(m.sum())))
    # across tracks
    X = np.column_stack([mean_front[big] - 100.0, champ_share[big], league_share[big]])
    yb = board[big]
    def corr(a, b):
        return float(np.corrcoef(a, b)[0, 1])
    across = {"n_tracks": int(big.size), "sd_board": float(np.std(yb)),
              "sd_bracket": float(np.nanstd(mean_br[big])),
              "corr_board_bracket": corr(yb, mean_br[big]),
              "corr_board_front": corr(yb, mean_front[big]),
              "corr_board_champ": corr(yb, champ_share[big])}
    A = np.column_stack([np.ones(big.size), X])
    coef, *_ = np.linalg.lstsq(A, yb, rcond=None)
    pred = A @ coef
    across["r2_mix"] = float(1.0 - np.var(yb - pred) / np.var(yb))
    across["coef"] = coef
    table = sorted(((board[i], i) for i in big), key=lambda t: t[0])
    per_track = [(keys[i], int(cnt[i]), float(board[i]), float(mean_br[i]),
                  float(mean_front[i]), float(champ_share[i])) for _b, i in table]
    return dict(by_class=by_class, by_terc=by_terc, across=across, per_track=per_track)


def report(r, names=None, show=15):
    a = r["across"]
    pct = lambda v: f"{100 * v:+6.2f}"
    print(f"\noutdoor tracks with enough bracketed rows: {a['n_tracks']:,}")
    print(f"  spread of the board number across them (sd): {100 * a['sd_board']:.2f}%   "
          f"of the same-athlete bracket: {100 * a['sd_bracket']:.2f}%")
    print(f"  corr(board, bracket) {a['corr_board_bracket']:+.2f}   corr(board, mean front) "
          f"{a['corr_board_front']:+.2f}   corr(board, championship share) {a['corr_board_champ']:+.2f}")
    c = a["coef"]
    print(f"  board ~ front + championship share + league share: R2 {a['r2_mix']:.2f}; "
          f"per 10 points of front {100 * 10 * c[1]:+.2f}%, all-championship "
          f"{100 * c[2]:+.2f}%, all-league {100 * c[3]:+.2f}%")
    print("\n  WITHIN a track (the track cancels), the same-athlete bracket by the "
          "meet's name class:")
    lab = {0: "ordinary", 1: "league", 2: "qualifier", 3: "final"}
    for k, (n, m, se) in sorted(r["by_class"].items()):
        print(f"    {lab[k]:<10} {n:>10,} rows   {pct(m)}%  (se {100 * se:.2f})")
    print("  and by the race's front relative to the track's usual front:")
    for k, (n, f, m, se) in sorted(r["by_terc"].items()):
        print(f"    tercile {k} {n:>10,} rows   front {f:+5.1f} pts   {pct(m)}%  (se {100 * se:.2f})")
    print(f"\n  the {show} easiest and {show} hardest tracks on the board:")
    print(f"    {'board':>7} {'bracket':>8} {'front':>6} {'champ':>6} {'rows':>8}  track")
    rows = r["per_track"]
    for item in rows[:show] + [None] + rows[-show:]:
        if item is None:
            print("    ...")
            continue
        key, n, b, br, fr, ch = item
        name = (names or {}).get(key.split(":")[2] if key.startswith("TF:loc:") else "", "")
        print(f"    {pct(b)} {pct(br)} {fr:6.1f} {100 * ch:5.0f}% {n:>8,}  {key}  {name}")
    print("\n  read: if the within-track rows by class and by front are flat, the "
          "field is not why tracks differ; if the R2 of the mix is high, it is.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    d = rj.buildParser()
    ap.add_argument("--pack", default=d.get_default("pack"))
    ap.add_argument("--npz", default=d.get_default("out"))
    ap.add_argument("--era-years", type=int, default=0)
    ap.add_argument("--min-rows", type=int, default=300)
    ap.add_argument("--window", type=float, default=21.0)
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()
    cols = pe.loadPack(args.pack)
    npz = dict(np.load(args.npz, allow_pickle=False))
    r = analyse(cols, npz, era_years=args.era_years, min_rows=args.min_rows,
                window=args.window, use_curve=not args.no_curve)
    report(r, show=args.show)


if __name__ == "__main__":
    main()
