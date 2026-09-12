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
import run_joint as rj                                          # noqa: E402


def analyse(cols, npz, era_years=0, min_rows=300, window=21, use_curve=True,
            sample_pct=100.0, seed=11, codes=None):
    """codes: bracket.packCodes' whole-pack codes (computed here when not
    given). The board number per base course, the fronts of the races and
    the rows per cell are read off the WHOLE pack; only the bracket runs
    on the athlete sample, and on its track rows."""
    if codes is None or "_cell" not in cols:
        try:
            cols, codes = bk.packCodes(cols, npz, era_years)
        except ValueError as exc:
            raise SystemExit(str(exc))
    keys, n_base = codes["keys"], codes["n_base"]
    cell_keys = [str(k) for k in codes["cell_keys"]]
    n_cell = len(cell_keys)
    base_of_cell = np.asarray(codes["base_of_cell"], dtype=np.int64)
    is_out = np.array([k.startswith("TF:") and k.endswith(":out") for k in keys], dtype=bool)
    delta = np.asarray(npz["delta_anchored"] if "delta_anchored" in npz else npz["delta"],
                       dtype=np.float64)
    if delta.size != n_cell:
        raise SystemExit(f"the solve file has {delta.size:,} difficulties and the pack "
                         f"maps to {n_cell:,} cells; pass the era width the solve used")
    # ★ THE PUBLISHED NUMBER PER BASE COURSE: its latest solved era with
    #   rows, counted over the whole pack (a sample would miss thin eras)
    cell_all = np.asarray(cols["_cell"]).astype(np.int64)
    rows_per_cell = np.bincount(cell_all[cell_all >= 0], minlength=n_cell)
    era_of_cell = np.array([int(k.rpartition("@e")[2]) if "@e" in k else 0 for k in cell_keys],
                           dtype=np.int64)
    board = np.full(n_base, np.nan)
    has = np.flatnonzero(rows_per_cell > 0)
    if has.size:
        order = np.lexsort((era_of_cell[has], base_of_cell[has]))
        hs = has[order]
        bs = base_of_cell[hs]
        last = np.flatnonzero(np.r_[bs[1:] != bs[:-1], True])
        board[bs[last]] = delta[hs[last]]
    # the front of every race from the WHOLE field (a sample's top five is
    # not the race's), through the solve's ratings; one sort of the pack
    race_all = np.asarray(cols["_race"]).astype(np.int64)
    rating_all = bk.ratingOnRows(cols, npz, codes)
    front_race = (js.raceFront(rating_all, race_all, codes["n_race"])
                  if rating_all is not None else np.full(codes["n_race"], np.nan))
    cols = dict(cols); cols["_front"] = front_race[race_all]
    if sample_pct < 100:
        keep = bk.athleteSample(cols, sample_pct, seed)
        print(f"[tracks] {int(keep.sum()):,} rows of {keep.size:,}: {sample_pct:g}% of "
              f"athletes, whole athletes", flush=True)
        cols = bk.subsetCols(cols, keep)
    # the bracket per row: track rows only (the bracket is within a sport)
    tf = np.asarray(cols["sport"]).astype(np.int64) == 1
    print(f"[tracks] {int(tf.sum()):,} track rows of {tf.size:,}; bracketing them",
          flush=True)
    cols = bk.subsetCols(cols, tf)
    res_tf = bk.bracketRows(cols, npz, window=window, use_curve=use_curve,
                            season=cols["_season"], n_season=codes["n_season"])
    br = res_tf["bracket"]
    course = np.asarray(cols["course"]).astype(np.int64)
    print("[tracks] fronts, classes and the per-track table", flush=True)
    front_row = np.asarray(cols["_front"], dtype=np.float64)
    mclass = (np.asarray(cols["meet_class"]).astype(np.int64) if "meet_class" in cols
              else np.zeros(course.size, dtype=np.int64))
    sel = (course >= 0) & is_out[np.maximum(course, 0)] & np.isfinite(br)
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
    big_mask = np.zeros(n_base, dtype=bool); big_mask[big] = True
    m_big = sel & big_mask[np.maximum(course, 0)]
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
    ap.add_argument("--min-rows", type=int, default=100,
                    help="bracketed rows a track needs (in the sample)")
    ap.add_argument("--window", type=float, default=21.0)
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--sample-pct", type=float, default=25.0,
                    help="percent of athletes (whole athletes; default 25)")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    cols, npz = bk.loadInputs(args.pack, args.npz)
    if npz is None:
        sys.exit(f"no solve file at {args.npz}")
    print(f"[tracks] {np.asarray(cols['norm']).size:,} rows loaded", flush=True)
    r = analyse(cols, npz, era_years=args.era_years, min_rows=args.min_rows,
                window=args.window, use_curve=not args.no_curve,
                sample_pct=args.sample_pct, seed=args.seed)
    report(r, show=args.show)


if __name__ == "__main__":
    main()
