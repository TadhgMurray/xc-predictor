"""
ss_eval.py -- the state-space prototype (engine/statespace.py) on the REAL
pack, scored on exactly the held-out rows the current engine's forward
holdout scored, paired, with the race-bootstrap SE (forward_holdout.
pairedLines). Reads the pack file and the holdout dump only: no database,
nothing written but its own dump.

    # 1. the current engine's forward holdout, its rows dumped (≈1 h a side)
    python scripts/scorecard.py --base production
    # 2. the prototype on the same sample, window and rows (minutes)
    python scripts/ss_eval.py \\
        --base-dump engine/data/scorecard/ladder_logs/base_holdout.npz

★ THE SAME ROWS. The athlete sample is run_joint's own draw (keep, then
  rng(seed).random over the unique athlete codes < pct), the window is the
  dump's, and a row counts only where BOTH engines predicted it. log errors
  are the same on either anchor, so the dump's pool-scale y is compared
  with the prototype's 5000 m scale through the row's own anchor shift.
★ ONE PERSON, ONE SCALE. The pack keys an athlete as (person, pool); here
  the person is the unit and every row moves onto 5000 m with its pool's
  own curve (normalize_distance.anchorShift, as model/feature_extraction
  .toCommonScale does), so a senior's spring and a freshman's fall are one
  trajectory.
⚠ WHAT THE PROTOTYPE LEAVES OUT: altitude, the track event offsets, the
  indoor level (fitted as a group level here, not asserted), the course
  eras' walk (--era-years only splits), the bracket engine and every go-live
  correction. A loss here may be one of those; a win is a win without them.
"""
import argparse
import ast
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo():
    """The checkout whose engine, artifacts and pack are read: --repo, else
    this file's own. ★ SO PRODUCTION'S CHECKOUT NEVER CHANGES BRANCH: copy
    statespace.py and this file to a scratch tree and point --repo at
    /srv/xc-predictor; statespace comes from the scratch tree, everything
    else (normalize_distance and its fitted curves) from the repo."""
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
        if a.startswith("--repo="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.path.abspath(os.path.join(HERE, ".."))


REPO = _repo()
sys.path[:0] = [os.path.join(HERE, "..", "engine"), os.path.join(REPO, "engine")]
import forward_holdout as fh                                    # noqa: E402
import statespace as ss                                         # noqa: E402

PACK = os.path.join(REPO, "engine", "data", "packed_XC_TF.npz")
COLS = ("result_id", "athlete", "course", "norm", "days", "sport", "doy",
        "year", "dist_m", "meet_id", "div_id")


def loadPack(path):
    """The pack's row arrays and key lists, without speed_ratings (which
    imports the database layer)."""
    t0 = time.time()
    with np.load(path, allow_pickle=False) as z:
        cols = {k: z[k] for k in z.files if k in COLS}
        if "__scalars__" in z.files:
            sc = ast.literal_eval(str(z["__scalars__"][0]))
            cols["athlete_keys"] = sc.get("athlete_keys")
            cols["course_keys"] = sc.get("course_keys")
        for k in ("athlete_keys", "course_keys"):
            if cols.get(k) is None and k in z.files:
                cols[k] = z[k]
    print(f"[ss] pack {path}: {cols['norm'].size:,} rows in {time.time() - t0:.0f}s")
    return cols


def anchorShifts(pool_of_raw, sport):
    """log of each row's move onto 5000 m: per (pool, sport) once."""
    import normalize_distance as nd
    raw_pool = np.asarray(pool_of_raw)
    names, inv = np.unique(raw_pool, return_inverse=True)
    out = np.zeros((names.size, 2))
    for i, nm in enumerate(names):
        for s, sn in ((0, "XC"), (1, "TF")):
            out[i, s] = np.log(nd.anchorShift(nd.ratedScalePool(str(nm)), sn))
    return out, inv.reshape(-1)


def venueOfCell(course_keys):
    """run_joint.venueOfCell, here so the database layer stays unimported:
    'XC:Morley:d4800' and 'XC:Morley:d5000' are one venue."""
    venue = []
    for k in course_keys:
        head, tag, dist = str(k).rpartition(":d")
        venue.append(head if tag and dist.isdigit() else str(k))
    _, inv = np.unique(np.array(venue), return_inverse=True)
    return inv.reshape(-1).astype(np.int64)


def denseKey(*parts):
    key = np.stack([np.asarray(p, dtype=np.int64) for p in parts], axis=1)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1).astype(np.int64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO,
                    help="the checkout to read the engine, artifacts and pack from")
    ap.add_argument("--pack", default=PACK)
    ap.add_argument("--base-dump", help="run_joint's XCP_HOLDOUT_DUMP (forward)")
    ap.add_argument("--sample-pct", type=float, help="default: the dump's")
    ap.add_argument("--sample-seed", type=int, help="default: the dump's")
    ap.add_argument("--seasons-back", type=int, default=6,
                    help="train on this many seasons before the window")
    ap.add_argument("--outer", type=int, default=25)
    ap.add_argument("--gap", type=float, default=0.0583,
                    help="the XC level, asserted for display (see the review §3.4)")
    ap.add_argument("--era-years", type=int, default=0)
    ap.add_argument("--static", action="store_true",
                    help="the current engine's shape in the same code (rung A)")
    ap.add_argument("--out", default=os.path.join(REPO, "engine", "data",
                                                  "ss", "ss_fwd.npz"))
    a = ap.parse_args()

    base = dict(np.load(a.base_dump, allow_pickle=False)) if a.base_dump else None
    cols = loadPack(a.pack)
    n = cols["norm"].size
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    pct = a.sample_pct if a.sample_pct is not None else (
        float(base["sample_pct"][0]) if base is not None else 100.0)
    seed = a.sample_seed if a.sample_seed is not None else (
        int(base["sample_seed"][0]) if base is not None else 11)
    if pct < 100:
        # ! run_joint's own draw, line for line, so the rows are the same
        ath = np.asarray(cols["athlete"])
        uniq = np.unique(ath[keep])
        rng = np.random.default_rng(seed)
        picked = uniq[rng.random(uniq.size) < pct / 100.0]
        keep &= np.isin(ath, picked)
        print(f"[ss] SAMPLE {pct:g}% seed {seed}: {int(keep.sum()):,} rows")

    dates, pack_date, agree = fh.rowDates(cols)
    if base is not None and str(base["window"][0]):
        frm, until = (np.datetime64(str(base["window"][0]), "D"),
                      np.datetime64(str(base["window"][1]), "D"))
    else:
        w = fh.forwardWindow(cols)
        frm, until = w["from"], w["until"]
    season = fh.seasonOf(dates).astype(np.int64)
    s_from = int(fh.seasonOf(np.array([frm]))[0])
    use = keep & (dates < until) & (season >= s_from - a.seasons_back)
    train = use & (dates < frm)
    scored = use & (dates >= frm)
    print(f"[ss] window {frm} .. {until}: train {int(train.sum()):,}  "
          f"scored {int(scored.sum()):,}  (seasons {s_from - a.seasons_back}..{s_from})")

    # people, pools, the 5000 m scale
    keys = cols["athlete_keys"]
    person_of_raw = fh.personCodes(keys)
    pool_of_raw = np.array([str(k[1]) for k in keys])
    sport = np.asarray(cols["sport"]).astype(np.int64)
    shift_tab, pool_code_raw = anchorShifts(pool_of_raw, sport)
    raw = np.asarray(cols["athlete"]).astype(np.int64)
    lshift = shift_tab[pool_code_raw[raw], sport]
    y5 = np.log(np.maximum(cols["norm"], 1e-9)) + lshift

    ck = np.asarray(cols["course_keys"]).astype(str)
    is_in = np.char.endswith(ck, ":in")
    is_xc = np.char.startswith(ck, "XC:")
    crs = np.asarray(cols["course"]).astype(np.int64)
    group = np.where(is_xc[crs], ss.GROUP_XC,
                     np.where(is_in[crs], ss.GROUP_IN, ss.GROUP_OUT))
    venue = venueOfCell(ck)[crs]
    dint = dates.astype(np.int64)
    cell = crs if not a.era_years else denseKey(crs, season // a.era_years)
    meet = np.asarray(cols["meet_id"]).astype(np.int64)
    div = np.asarray(cols["div_id"]).astype(np.int64)

    idx = np.flatnonzero(use)
    person = person_of_raw[raw[idx]]
    order = np.lexsort((dint[idx], person))
    idx = idx[order]
    _, pdense = np.unique(person_of_raw[raw[idx]], return_inverse=True)
    lv_names, lv = np.unique(np.char.partition(pool_of_raw[raw[idx]], "|")[:, 0],
                             return_inverse=True)
    D = {
        "person": pdense.reshape(-1).astype(np.int64),
        "t": dint[idx].astype(np.float64),
        "season": season[idx],
        "lv": lv.reshape(-1).astype(np.int64),
        "phase": (dates[idx] - fh.seasonStart(season[idx])).astype(np.int64).astype(np.float64),
        "y": y5[idx],
        "xc": (group[idx] == ss.GROUP_XC).astype(np.int64),
        "group": group[idx].astype(np.int64),
        "cell": denseKey(cell[idx]),
        "day": denseKey(venue[idx], dint[idx]),
        "race": denseKey(np.where(meet[idx] >= 0, meet[idx], -1 - cell[idx]),
                         np.where(meet[idx] >= 0, div[idx], 0), dint[idx]),
    }
    D["starts"] = ss.segments(D["person"])
    tr = train[idx]
    print(f"[ss] {idx.size:,} rows, {D['starts'].size - 1:,} people, "
          f"{D['cell'].max() + 1:,} cells, {D['day'].max() + 1:,} venue-days, "
          f"{D['race'].max() + 1:,} races, levels {list(lv_names)}")

    t0 = time.time()
    kw = (dict(dynamic=False, nested=False, heavy=False) if a.static else {})
    R = ss.fit(D, tr, outer=a.outer, fix_mu={ss.GROUP_XC: a.gap}, verbose=True, **kw)
    print(f"[ss] fit {time.time() - t0:.0f}s: kappa {R['kappa']:.3f}  sigma "
          f"{np.sqrt(R['sigma2']):.4f}  walk/30d {np.sqrt(R['q_day'] * 30):.4f}  "
          f"season trend sd {np.sqrt(R['s_slope2']):.4f}/yr  seam sd "
          f"{np.sqrt(R['q_jump']):.4f}  mu_in {R['mu'][ss.GROUP_IN]:+.4f}  "
          f"tau {np.sqrt(R['tau2']).round(4)}  day sd {np.sqrt(R['s_w2']).round(4)}  "
          f"race sd {np.sqrt(R['s_v2']).round(4)}")
    for L, nm in enumerate(lv_names):
        print(f"[ss]   {nm:12s} seam mean {R['jump'][L]:+.4f}")

    # predictions back to file order, on the pool scale the dump uses
    pred = np.full(n, np.nan)
    pred[idx] = R["pred"] - lshift[idx]
    trained_people = np.bincount(D["person"], weights=tr.astype(float),
                                 minlength=D["starts"].size - 1) > 0
    known = np.zeros(n, dtype=bool)
    known[idx] = trained_people[D["person"]]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)

    if base is None:
        m = scored & known
        d = {"y": np.log(cols["norm"][m]), "pred": pred[m],
             "covered": np.ones(int(m.sum()), dtype=bool),
             "group": D_race_file(D, idx, n)[m],
             "sport": sport[m]}
        for line in fh.scoreLines(d):
            print(line)
        np.savez(a.out, row=np.flatnonzero(m), pred=pred[m], y=d["y"],
                 group=d["group"], sport=d["sport"],
                 window=np.array([str(frm), str(until)]))
        return

    rows = np.asarray(base["row"]).astype(np.int64)
    ours = {k: v for k, v in base.items()}
    ours["pred"] = pred[rows]
    ours["covered"] = np.asarray(base["covered"], dtype=bool) & known[rows] \
        & np.isfinite(ours["pred"])
    np.savez(a.out, **ours)
    print(f"\n[ss] the current engine on its own rows:")
    for line in fh.scoreLines(base):
        print(line)
    print(f"\n[ss] the prototype on the same rows ({int(ours['covered'].sum()):,} "
          f"of {int(np.asarray(base['covered']).sum()):,} the engine covered):")
    for line in fh.scoreLines(ours):
        print(line)
    both = dict(base)
    both["covered"] = ours["covered"]
    print()
    for line in fh.pairedLines(both, ours):
        print(line)
    print(f"\n[ss] dump -> {a.out}")


def D_race_file(D, idx, n):
    """The prototype's race code per file row (for a run without a dump)."""
    out = np.full(n, -1, dtype=np.int64)
    out[idx] = D["race"]
    return out


if __name__ == "__main__":
    main()
