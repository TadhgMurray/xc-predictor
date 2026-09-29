#!/usr/bin/env python3
"""
switch_scorecard.py -- a switch against the baseline, in the two numbers the
owner decides from: held-out prediction error, and the top of the boards.

    # 1. held-out error, row for row, from two ladder rungs' dumps
    python scripts/switch_scorecard.py --holdout \
        engine/data/ladder_logs/base_holdout.npz \
        engine/data/ladder_logs/season-tie_holdout.npz

    # 2. the top of every pool's board, from two solve files
    python scripts/switch_scorecard.py --boards \
        engine/data/joint_base.npz engine/data/joint_tie.npz \
        [--pack engine/data/packed_XC_TF.npz] [--top 25] [--years 2025,2026] \
        [--person 29603084]

Run from the PROJECT ROOT. Reads files only; writes nothing.

★ WHY (owner, 2026-09-29: "try to be safe and test it"). The season tie
  (run_joint --season-tie) and the HS-scale tilt (--tilt-scale hs) are both
  OFF until a number says they help. The ladder's headline is one sd over
  every held-out row, and a switch that acts on a sixth of the rows can hide
  in it either way, so this reads the dumps ROW FOR ROW on the rows BOTH
  runs covered, split where each switch acts: the athlete-season's training
  rows (the tie moves thin seasons) and the pool (the HS tilt moves the
  non-HS pools). A paired difference with its standard error says whether
  the change is bigger than the noise of the split.

★ AND THE SCORECARD (2026-09-29, engine/forward_holdout.py). A dump written
  since then carries each row's race, and --holdout adds bias, median and
  p90 |error| and per-race head-to-head, with the paired change's SE from
  resampling WHOLE RACES -- the row-iid SE above it is too small by about
  the square root of a field. The forward split (run_joint --holdout-kind
  forward; scripts/scorecard.py) is the one to decide on.

⚠ WHAT THE BOARDS SECTION READS. The solve file's athlete-season rating is
  the ABILITY-based rating (athlete_ratings). The site's season boards read
  athlete_season.mean_rating, the 80th percentile of the season's
  PER-RESULT ratings (build_ranking_results._ATHLETE_SEASON_SQL). The HS
  tilt changes every per-result rating it touches, so the ability boards
  here are a fair proxy for it; the season tie does NOT reach a per-result
  rating except through the tilt, so its board change on the site is much
  smaller than the ability change printed here. Both are said again in the
  output.
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

import forward_holdout as fhm                                   # noqa: E402

SEASON_BUCKETS = ((1, 1), (2, 2), (3, 5), (6, None))    # for reading


def _bucketLabel(lo, hi):
    return f"{lo}" if lo == hi else (f"{lo}+" if hi is None else f"{lo}-{hi}")


def pairedLine(label, e_base, e_test):
    """sd of each, the relative change, and the paired mean change in squared
    error with its standard error (positive = the switch is worse)."""
    n = e_base.size
    d = e_test ** 2 - e_base ** 2
    se = d.std() / np.sqrt(max(n, 1))
    sb, st = e_base.std(), e_test.std()
    return (f"  {label:<22}{n:>10,}  {sb:.6f}  {st:.6f}  "
            f"{100 * (st / sb - 1):+7.2f}%   {d.mean():+.2e} ± {se:.1e}"
            f"{'  *' if abs(d.mean()) > 2 * se else ''}")


def compareHoldout(base, test, n_rep=None):
    """Lines comparing two XCP_HOLDOUT_DUMP files row for row. Pure."""
    out = []
    for key in ("kind", "sample_pct", "sample_seed"):
        if key in base and key in test and str(base[key][0]) != str(test[key][0]):
            out.append(f"  ⚠ the dumps differ in {key}: {base[key][0]} against "
                       f"{test[key][0]} -- NOT the same held-out rows")
    rb, rt = np.asarray(base["row"]), np.asarray(test["row"])
    common, ib, it = np.intersect1d(rb, rt, return_indices=True)
    cov = np.asarray(base["covered"])[ib] & np.asarray(test["covered"])[it]
    e_b = (np.asarray(base["y"]) - np.asarray(base["pred"]))[ib][cov]
    e_t = (np.asarray(test["y"]) - np.asarray(test["pred"]))[it][cov]
    out.append(f"  {int(cov.sum()):,} held-out rows covered by both "
               f"({common.size:,} in both dumps)")
    out.append(f"  {'':<22}{'rows':>10}  {'base sd':<8}  {'test sd':<8}  "
               f"{'change':>8}   paired d(err^2) ± row-iid se   (* = beyond 2 se; "
               f"the race-bootstrap SCORECARD below is the one to read)")
    out.append(pairedLine("all", e_b, e_t))
    if "season_train_rows" in base:
        n_tr = np.asarray(base["season_train_rows"])[ib][cov]
        out.append("  by the athlete-season's training rows (the season tie):")
        for lo, hi in SEASON_BUCKETS:
            m = (n_tr >= lo) & (True if hi is None else n_tr <= hi)
            if m.sum() >= 100:
                out.append(pairedLine(f"  {_bucketLabel(lo, hi)} rows", e_b[m], e_t[m]))
    else:
        out.append("  (the base dump predates season_train_rows: rerun its rung)")
    if "pool" in base:
        pool = np.asarray(base["pool"])[ib][cov]
        out.append("  by pool (the HS tilt):")
        for name in sorted(set(pool.tolist())):
            m = pool == name
            if m.sum() >= 100:
                out.append(pairedLine(f"  {name}", e_b[m], e_t[m]))
    # ★ THE SCORECARD (2026-09-29, engine/forward_holdout.py): bias, median
    #   and p90 |error| and head-to-head per race, on the same rows, the SE by
    #   resampling WHOLE RACES. The lines above treat rows as independent,
    #   and rows of one race share its day, field and course, so their SE is
    #   too small by about the square root of a field; read the stars below.
    if "group" in base and "group" in test:
        wb, wt = base.get("window"), test.get("window")
        if wb is not None and wt is not None and list(map(str, wb)) != list(map(str, wt)):
            out.append(f"  ⚠ the dumps' windows differ: {list(wb)} against "
                       f"{list(wt)} -- NOT the same held-out season")
        n_rows = rb.size
        al_b = {k: (np.asarray(v)[ib] if np.ndim(v) == 1 and np.size(v) == n_rows else v)
                for k, v in base.items()}
        al_t = {k: (np.asarray(v)[it] if np.ndim(v) == 1 and np.size(v) == rt.size else v)
                for k, v in test.items()}
        out += fhm.pairedLines(al_b, al_t, n_rep=n_rep)
    else:
        out.append("  (no race-bootstrap scorecard: a dump from before "
                   "2026-09-29 carries no race groups -- rerun its rung)")
    return out


def seasonIdentity(pack_path, n_season, split_ability=False):
    """Per athlete-season code: (person_id, year), rebuilt from the pack the
    way run_joint numbers them (pair_engine.athleteSeasonCodes over ALL
    rows, so a sampled solve file still indexes)."""
    import pair_engine as pe
    cols = pe.loadPack(pack_path, only=("athlete", "year", "sport"))
    ath = np.asarray(cols["athlete"]).astype(np.int64)
    yr = np.asarray(cols["year"]).astype(np.int64)
    codes, n = pe.athleteSeasonCodes(ath, yr, cols.get("sport") if split_ability else None)
    if n != n_season:
        sys.exit(f"the pack numbers {n:,} athlete-seasons and the file "
                 f"{n_season:,}: not the pack this file was solved from")
    raw = np.zeros(n, dtype=np.int64); raw[codes] = ath
    year = np.zeros(n, dtype=np.int64); year[codes] = yr
    keys = cols["athlete_keys"]
    person = np.array([str(keys[r][0]) for r in raw])
    return person, year


def boardTops(rating, n_races, pool, year, top=25, min_races=3):
    """{(pool, year): athlete-season codes of the top `top` by rating, with
    min_races races}. Pure."""
    ok = np.isfinite(rating) & (n_races >= min_races)
    out = {}
    key = pool.astype(np.int64) * 10_000 + year.astype(np.int64)
    for k in np.unique(key[ok]):
        idx = np.flatnonzero(ok & (key == k))
        order = idx[np.argsort(-rating[idx], kind="stable")][:top]
        out[(int(k // 10_000), int(k % 10_000))] = order
    return out


def compareBoards(base, test, person, year, pool_names, top=25, min_races=3,
                  years=None, movers=3):
    """Lines: per (pool, year) board, the overlap of the two tops, the median
    rank move and rating change of those in both, and the biggest movers."""
    rb, rt = np.asarray(base["rating"], np.float64), np.asarray(test["rating"], np.float64)
    n_r = np.asarray(base["n_races"])
    pool = np.asarray(base["athlete_pool"]).astype(np.int64)
    tb = boardTops(rb, n_r, pool, year, top, min_races)
    tt = boardTops(rt, n_r, pool, year, top, min_races)
    out = [f"  top {top} per (pool, year), athlete-seasons with {min_races}+ races "
           f"(the ABILITY rating; see the header for what the site's boards read)",
           f"  {'board':<18}{'overlap':>9}{'med |rank move|':>17}{'med change':>12}"
           f"{'max change':>12}   biggest movers (person: base -> test)"]
    for k in sorted(tb):
        p, y = k
        if years and y not in years:
            continue
        b_ids, t_ids = tb[k], tt.get(k, np.array([], dtype=np.int64))
        both = np.intersect1d(b_ids, t_ids)
        rank_b = {int(c): i for i, c in enumerate(b_ids)}
        rank_t = {int(c): i for i, c in enumerate(t_ids)}
        moves = [abs(rank_b[int(c)] - rank_t[int(c)]) for c in both]
        union = np.union1d(b_ids, t_ids)
        ch = rt[union] - rb[union]
        worst = union[np.argsort(-np.abs(ch))][:movers]
        name = f"{pool_names[p]} {y}"
        out.append(f"  {name:<18}{both.size:>5}/{top:<3}"
                   f"{(np.median(moves) if moves else float('nan')):>17.1f}"
                   f"{np.median(ch):>+12.2f}{ch[np.argmax(np.abs(ch))]:>+12.2f}   "
                   + "; ".join(f"{person[c]}: {rb[c]:.1f} -> {rt[c]:.1f}"
                               f" ({int(n_r[c])} races)" for c in worst))
    # by season size, over everyone: what the switch moved
    d = rt - rb
    ok = np.isfinite(d)
    out.append("  every athlete-season, by races (rating points, test - base):")
    for lo, hi in ((1, 1), (2, 2), (3, 5), (6, 9), (10, None)):
        m = ok & (n_r >= lo) & (True if hi is None else n_r <= hi)
        if m.any():
            out.append(f"    {_bucketLabel(lo, hi):>6} races {int(m.sum()):>11,}  "
                       f"median |change| {np.median(np.abs(d[m])):.2f}  p90 "
                       f"{np.percentile(np.abs(d[m]), 90):.2f}  mean {d[m].mean():+.2f}")
    return out


def personLines(base, test, person, year, pool_names, pid):
    """One person's athlete-seasons in both files: the case that started it
    (Jack Moretta, 29603084: a two-race 2026 XC season at 88.9). Pure."""
    idx = np.flatnonzero(person == str(pid))
    if not idx.size:
        return [f"  person {pid}: no athlete-season in this pack"]
    rb, rt = np.asarray(base["rating"]), np.asarray(test["rating"])
    n_r = np.asarray(base["n_races"])
    pool = np.asarray(base["athlete_pool"]).astype(np.int64)
    out = [f"  person {pid} (the ABILITY rating, per athlete-season):",
           f"    {'season':<18}{'races':>6}{'base':>8}{'test':>8}{'change':>8}"]
    for c in idx[np.argsort(year[idx], kind="stable")]:
        out.append(f"    {pool_names[pool[c]] + ' ' + str(year[c]):<18}{int(n_r[c]):>6}"
                   f"{rb[c]:>8.1f}{rt[c]:>8.1f}{rt[c] - rb[c]:>+8.1f}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdout", nargs=2, metavar=("BASE", "TEST"),
                    help="two XCP_HOLDOUT_DUMP files (ladder_logs/<rung>_holdout.npz)")
    ap.add_argument("--boards", nargs=2, metavar=("BASE", "TEST"),
                    help="two solve files written by run_joint --out")
    ap.add_argument("--pack", default=os.path.join(_ROOT, "engine", "data",
                                                   "packed_XC_TF.npz"))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-races", type=int, default=3)
    ap.add_argument("--years", default="",
                    help="comma list of season years to show (default all)")
    ap.add_argument("--person", action="append", default=[],
                    help="--boards: print this person's athlete-seasons in "
                         "both files (repeatable), e.g. 29603084")
    ap.add_argument("--boot", type=int, default=None,
                    help="--holdout: race-bootstrap resamples (default "
                         "forward_holdout.bootReps(): the SE to 5%% of itself)")
    a = ap.parse_args()
    if not (a.holdout or a.boards):
        ap.error("--holdout and/or --boards")
    if a.holdout:
        with np.load(a.holdout[0], allow_pickle=False) as b, \
                np.load(a.holdout[1], allow_pickle=False) as t:
            base = {k: b[k] for k in b.files}
            test = {k: t[k] for k in t.files}
        print(f"\nHELD OUT: {a.holdout[1]} against {a.holdout[0]}")
        for line in compareHoldout(base, test, n_rep=a.boot):
            print(line)
    if a.boards:
        with np.load(a.boards[0], allow_pickle=False) as b, \
                np.load(a.boards[1], allow_pickle=False) as t:
            base = {k: b[k] for k in b.files}
            test = {k: t[k] for k in t.files}
        for k in ("season_tie", "tilt_scale"):
            print(f"  {k}: base {base.get(k, ['?'])[0]}  test {test.get(k, ['?'])[0]}")
        if "rating" not in base or "rating" not in test:
            sys.exit("a solve file without ratings (run_joint writes them with "
                     "athlete pools)")
        split = bool(base.get("split_ability", [False])[0])
        person, year = seasonIdentity(a.pack, np.asarray(base["rating"]).size, split)
        years = {int(y) for y in a.years.split(",") if y.strip()} or None
        print(f"\nBOARDS: {a.boards[1]} against {a.boards[0]}")
        for line in compareBoards(base, test, person, year,
                                  [str(n) for n in base["pool_names"]],
                                  top=a.top, min_races=a.min_races, years=years):
            print(line)
        for pid in a.person:
            for line in personLines(base, test, person, year,
                                    [str(n) for n in base["pool_names"]], pid):
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
