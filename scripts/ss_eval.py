"""
ss_eval.py -- the state-space engine (engine/statespace.py) on the REAL
pack: what it measures the XC and indoor levels to be, how it scores
against the current engine on the same held-out rows, and its ratings for
the LACCTiC / Kailo comparisons. Reads the pack file (and a holdout dump)
only: no database, nothing written but its own files.

    # everything, after scripts/scorecard.py has dumped the engine's rows
    python scripts/ss_eval.py --mode all \\
        --base-dump engine/data/scorecard/ladder_logs/try_holdout.npz
    python scripts/ss_eval.py --mode gap          # the levels only
    python scripts/ss_eval.py --mode ratings      # ratings_<date>.npz only

★ GAP (review §4.4). Fitted on every rated row of the last --seasons-back
  seasons: the XC and indoor levels against the flat outdoor gauge, per
  level, with their slope in ability; the bridges that measure them (rows,
  runners, venues, and how many of those venues are raced at other times);
  the same fit without each kind of bridge (if the kinds disagree, the gap
  is not determined, and that is the finding); and the tilt's likelihood
  profile. Nothing here is asserted but the gauge.
★ SCORE. The forward holdout on exactly the rows the current engine's dump
  scored (run_joint's own athlete sample and window), paired, race-
  bootstrapped (forward_holdout.pairedLines), then the same code held to
  the current engine's skeleton (rung A) on the same rows.
★ RATINGS. Per result: the performance with course, day, race and the XC
  (or indoor) level removed -- the time it is worth on a flat outdoor track
  at 5000 m, on the day, in seconds: no forecast, no calibration -- and the
  rating on the pool's scale. scripts/lacctic_compare.py --ours-npz reads
  it in place of the published ratings.
★ ONE PERSON, ONE SCALE. The pack keys an athlete as (person, pool); here
  the person is the unit and every row moves onto 5000 m with its pool's
  own curve (normalize_distance.anchorShift, as model/feature_extraction
  .toCommonScale does), so a senior's spring and a freshman's fall are one
  trajectory.
⚠ NOT YET IN IT: altitude, the course eras' walk (--era-years only
  splits), weather beyond what normalized_time already carries.
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
OUT_DIR = os.path.join(REPO, "engine", "data", "ss")
COLS = ("result_id", "athlete", "course", "norm", "days", "sport", "doy",
        "year", "dist_m", "meet_id", "div_id")
TERMS = ("peak", "recovery", "event", "mu_by_level")
KAPPA_PROFILE = (0.0, 0.15, 0.31, 0.45, 0.6)


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
    print(f"[ss] pack {path}: {cols['norm'].size:,} rows in {time.time() - t0:.0f}s",
          flush=True)
    return cols


def anchorShifts(pool_of_raw):
    """log of each (pool, sport)'s move onto 5000 m, once per pair."""
    import normalize_distance as nd
    names, inv = np.unique(np.asarray(pool_of_raw), return_inverse=True)
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


class Rows:
    """The pack, keyed for the engine: every per-file-row array the modes
    need, and build(use) -> (D, idx) for any subset of file rows."""

    def __init__(self, cols, era_years=0):
        self.cols = cols
        self.n = cols["norm"].size
        self.keep = (cols["course"] >= 0) & (cols["norm"] > 0)
        self.dates, self.pack_date, _ = fh.rowDates(cols)
        self.season = fh.seasonOf(self.dates).astype(np.int64)
        keys = cols["athlete_keys"]
        self.person_of_raw = fh.personCodes(keys)
        self.pool_of_raw = np.array([str(k[1]) for k in keys])
        self.sport = np.asarray(cols["sport"]).astype(np.int64)
        shift_tab, pool_code_raw = anchorShifts(self.pool_of_raw)
        self.raw = np.asarray(cols["athlete"]).astype(np.int64)
        self.lshift = shift_tab[pool_code_raw[self.raw], self.sport]
        self.y5 = np.log(np.maximum(cols["norm"], 1e-9)) + self.lshift
        ck = np.asarray(cols["course_keys"]).astype(str)
        self.course_keys = ck
        crs = np.asarray(cols["course"]).astype(np.int64)
        self.crs = crs
        is_in = np.char.endswith(ck, ":in")
        is_xc = np.char.startswith(ck, "XC:")
        safe = np.maximum(crs, 0)
        self.group = np.where(is_xc[safe], ss.GROUP_XC,
                              np.where(is_in[safe], ss.GROUP_IN, ss.GROUP_OUT))
        self.venue = venueOfCell(ck)[safe]
        self.dint = self.dates.astype(np.int64)
        self.cell = crs if not era_years else denseKey(crs, self.season // era_years)
        self.meet = np.asarray(cols["meet_id"]).astype(np.int64)
        self.div = np.asarray(cols["div_id"]).astype(np.int64)
        self.dist = np.asarray(cols.get("dist_m", np.zeros(self.n)), dtype=np.float64)

    def build(self, use):
        idx = np.flatnonzero(use)
        order = np.lexsort((self.dint[idx], self.person_of_raw[self.raw[idx]]))
        idx = idx[order]
        _, pdense = np.unique(self.person_of_raw[self.raw[idx]], return_inverse=True)
        pools = self.pool_of_raw[self.raw[idx]]
        lv_names, lv = np.unique(np.char.partition(pools, "|")[:, 0], return_inverse=True)
        cell = self.cell[idx]
        D = {
            "person": pdense.reshape(-1).astype(np.int64),
            "t": self.dint[idx].astype(np.float64),
            "season": self.season[idx],
            "lv": lv.reshape(-1).astype(np.int64),
            "phase": (self.dates[idx] - fh.seasonStart(self.season[idx]))
            .astype(np.int64).astype(np.float64),
            "y": self.y5[idx],
            "xc": (self.group[idx] == ss.GROUP_XC).astype(np.int64),
            "group": self.group[idx].astype(np.int64),
            "cell": denseKey(cell),
            "day": denseKey(self.venue[idx], self.dint[idx]),
            "race": denseKey(np.where(self.meet[idx] >= 0, self.meet[idx], -1 - cell),
                             np.where(self.meet[idx] >= 0, self.div[idx], 0),
                             self.dint[idx]),
            "dist_m": self.dist[idx],
            "lv_names": [str(x) for x in lv_names],
        }
        D["starts"] = ss.segments(D["person"])
        print(f"[ss] {idx.size:,} rows, {D['starts'].size - 1:,} people, "
              f"{D['cell'].max() + 1:,} cells, {D['day'].max() + 1:,} venue-days, "
              f"{D['race'].max() + 1:,} races, levels {D['lv_names']}", flush=True)
        return D, idx

    def recent(self, seasons_back):
        last = int(self.season[self.keep].max())
        return self.keep & (self.season >= last - seasons_back)


def fitLine(R, t0):
    return (f"[ss] fit {time.time() - t0:.0f}s: sigma {np.sqrt(R['sigma2']):.4f}  walk/30d "
            f"{np.sqrt(R['q_day'] * 30):.4f}  season trend sd {np.sqrt(R['s_slope2']):.4f}/yr  "
            f"seam sd {np.sqrt(R['q_jump']):.4f}  grass sd {np.sqrt(R['s_beta2']):.4f}  "
            f"tau {np.sqrt(R['tau2']).round(4)}  day sd {np.sqrt(R['s_w2']).round(4)}  "
            f"race sd {np.sqrt(R['s_v2']).round(4)}  tilt {R['kappa']:.2f}")


def gapLines(R, D, label=""):
    """The levels per level of the pool, in percent of time, with the GLS se
    (an under-statement: it holds the courses fixed; the bridge jackknife
    below is the honest spread), the slope in ability, peak and recovery."""
    names = D["lv_names"]
    out = []
    gl = R["gap_by_level"]
    cnt = {g: np.bincount(D["lv"][D["group"] == g], minlength=len(names))
           for g in (ss.GROUP_XC, ss.GROUP_IN)}
    out.append(f"[ss] {label}levels against the flat outdoor gauge (+ = slower than track), "
               f"for a runner at the median ({np.exp(R['C']):.0f} s 5000 m equivalent):")
    for nm, g in (("mu_XC", ss.GROUP_XC), ("mu_in", ss.GROUP_IN)):
        if nm not in gl:
            continue
        b, se = gl[nm]
        parts = [f"{names[L]} {100 * b[L]:+.2f}% (±{100 * se[L]:.2f}, {cnt[g][L]:,} rows)"
                 for L in range(len(names)) if cnt[g][L] > 0]
        out.append(f"[ss]   {'XC    ' if g == ss.GROUP_XC else 'indoor'}  " + "  ".join(parts))
    out.append(f"[ss]   slope in ability (per unit log time, + = the gap grows for "
               f"slower runners): XC {R['lam'][ss.GROUP_XC]:+.3f}  indoor "
               f"{R['lam'][ss.GROUP_IN]:+.3f}; i.e. 10% slower adds "
               f"{100 * 0.0953 * R['lam'][ss.GROUP_XC]:+.2f}% to the XC gap")
    bl = R["blocks"]
    out.append(f"[ss]   season's last race (taper) {100 * R['b'][bl['peak'][0]]:+.2f}%   "
               f"first races after a 21-45 / 45-90 day break "
               f"{100 * R['b'][bl['recovery'][0]]:+.2f}% / {100 * R['b'][bl['recovery'][1]]:+.2f}%")
    return out


def modeGap(rows, a):
    D, idx = rows.build(rows.recent(a.seasons_back))
    tr = np.ones(D["y"].size, dtype=bool)
    t0 = time.time()
    R = ss.fitGLS(D, tr, outer=a.outer, terms=TERMS, verbose=True)
    print(fitLine(R, t0))
    for line in gapLines(R, D):
        print(line)
    t1 = time.time()
    masks = ss.bridgeMasks(D)
    print(f"\n[ss] the bridges ({time.time() - t1:.0f}s); 'raced otherwise' = venues also "
          "raced outside the bridge, which is what lets a bridge pin the gap")
    for line in ss.bridgeReport(D, masks):
        print("[ss]   " + line)
    if a.jackknife:
        print("\n[ss] the same fit WITHOUT each kind of bridge (if these disagree, the gap "
              "is not determined by the data -- read the spread, not the best one)")
        for k in ss.BRIDGE_KINDS:
            if not masks[k].any():
                continue
            E = ss.subsetRows(D, ~masks[k])
            t2 = time.time()
            Rk = ss.fitGLS(E, np.ones(E["y"].size, dtype=bool), outer=a.jack_outer, terms=TERMS)
            print(f"[ss]  -- without {k} ({int(masks[k].sum()):,} rows, {time.time() - t2:.0f}s)")
            for line in gapLines(Rk, E):
                print("  " + line)
    if a.kappa_profile:
        print("\n[ss] the tilt: training scale by stated tilt (lower = the data prefer it)")
        for k in KAPPA_PROFILE:
            Rk = ss.fitGLS(D, tr, outer=a.jack_outer, terms=TERMS, kappa0=k)
            print(f"[ss]   tilt {k:.2f}: sigma {np.sqrt(Rk['sigma2']):.6f}  XC gap "
                  f"{100 * Rk['gap_ref'][ss.GROUP_XC]:+.2f}%", flush=True)
    return R, D, idx


def modeScore(rows, a):
    base = dict(np.load(a.base_dump, allow_pickle=False))
    keep = rows.keep.copy()
    pct = a.sample_pct if a.sample_pct is not None else float(base["sample_pct"][0])
    seed = a.sample_seed if a.sample_seed is not None else int(base["sample_seed"][0])
    if pct < 100:
        # ! run_joint's own draw, line for line, so the rows are the same
        ath = np.asarray(rows.cols["athlete"])
        uniq = np.unique(ath[keep])
        rng = np.random.default_rng(seed)
        picked = uniq[rng.random(uniq.size) < pct / 100.0]
        keep &= np.isin(ath, picked)
        print(f"[ss] SAMPLE {pct:g}% seed {seed}: {int(keep.sum()):,} rows")
    if str(base["window"][0]):
        frm, until = (np.datetime64(str(base["window"][0]), "D"),
                      np.datetime64(str(base["window"][1]), "D"))
    else:
        w = fh.forwardWindow(rows.cols)
        frm, until = w["from"], w["until"]
    s_from = int(fh.seasonOf(np.array([frm]))[0])
    use = keep & (rows.dates < until) & (rows.season >= s_from - a.seasons_back)
    train = use & (rows.dates < frm)
    print(f"[ss] window {frm} .. {until}: train {int(train.sum()):,}  "
          f"scored {int((use & ~train).sum()):,}")
    D, idx = rows.build(use)
    tr = train[idx]
    out = {}
    for name, fn in (("state-space", lambda: ss.fitGLS(D, tr, outer=a.outer, terms=TERMS,
                                                       verbose=True)),
                     ("rung A (today's skeleton)",
                      lambda: ss.fit(D, tr, outer=a.outer, dynamic=False, nested=False,
                                     heavy=False, fix_mu={ss.GROUP_XC: 0.0583}))):
        t0 = time.time()
        R = fn()
        print(fitLine(R, t0) if "q_day" in R else f"[ss] fit {time.time() - t0:.0f}s")
        pred = np.full(rows.n, np.nan)
        pred[idx] = R["pred"] - rows.lshift[idx]
        trained = np.bincount(D["person"], weights=tr.astype(float),
                              minlength=D["starts"].size - 1) > 0
        known = np.zeros(rows.n, dtype=bool)
        known[idx] = trained[D["person"]]
        r = np.asarray(base["row"]).astype(np.int64)
        ours = dict(base)
        ours["pred"] = pred[r]
        ours["covered"] = np.asarray(base["covered"], dtype=bool) & known[r] & \
            np.isfinite(ours["pred"])
        both = dict(base)
        both["covered"] = ours["covered"]
        print(f"\n[ss] {name} against the current engine on the rows both predicted "
              f"({int(ours['covered'].sum()):,} of {int(np.asarray(base['covered']).sum()):,})")
        for line in fh.pairedLines(both, ours):
            print(line)
        out[name] = ours
    os.makedirs(OUT_DIR, exist_ok=True)
    np.savez(os.path.join(OUT_DIR, "ss_fwd.npz"), **out["state-space"])
    return out


def modeRatings(rows, a, fitted=None):
    if fitted is None:
        D, idx = rows.build(rows.recent(a.seasons_back))
        t0 = time.time()
        R = ss.fitGLS(D, np.ones(D["y"].size, dtype=bool), outer=a.outer, terms=TERMS,
                      verbose=True)
        print(fitLine(R, t0))
    else:
        R, D, idx = fitted
    perf = R["perf"]                         # log seconds, flat outdoor 5000 m, on the day
    five_k = np.exp(perf)
    pools = rows.pool_of_raw[rows.raw[idx]]
    perf_pool = perf - rows.lshift[idx]      # back onto the pool's own anchor
    # the pool's scale: 100 at the pool's median performance of runners with
    # 3+ races in a season (the boards' anchor is a mean; this is its robust twin)
    names, pinv = np.unique(pools, return_inverse=True)
    pinv = pinv.reshape(-1)
    ps = D["person"] * 64 + (D["season"] % 64)
    _, psi, psn = np.unique(ps, return_inverse=True, return_counts=True)
    voter = psn[psi.reshape(-1)] >= 3
    pm = np.array([np.exp(np.median(perf_pool[(pinv == i) & voter]))
                   if ((pinv == i) & voter).any() else np.exp(np.median(perf_pool[pinv == i]))
                   for i in range(names.size)])
    rating = 100.0 * pm[pinv] / np.exp(perf_pool)
    cell_sd = np.sqrt(np.maximum(R["cell_var"], 0))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"ratings_{str(rows.pack_date)}.npz")
    np.savez(path, result_id=np.asarray(rows.cols["result_id"])[idx].astype(np.int64),
             sport=rows.sport[idx].astype(np.int8), five_k_s=five_k.astype(np.float64),
             rating=rating.astype(np.float32), pool=pools.astype(str),
             date=rows.dates[idx].astype("datetime64[D]").astype(str),
             course_key=rows.course_keys[rows.crs[idx]].astype(str),
             course=R["cell"][D["cell"]].astype(np.float32),
             course_sd=cell_sd[D["cell"]].astype(np.float32),
             gap=(R["G"]).astype(np.float32),
             pool_names=names.astype(str), pool_means=pm)
    print(f"\n[ss] ratings -> {path}: {idx.size:,} results; 5K column = seconds on a flat "
          f"outdoor 5000 m, that day (exp of the performance), no forecast")
    for i, nm in enumerate(names):
        m = pinv == i
        if m.sum() >= 1000:
            print(f"[ss]   {nm:12s} {int(m.sum()):>10,} rows  median 5K "
                  f"{np.median(five_k[m]) // 60:.0f}:{np.median(five_k[m]) % 60:04.1f}  "
                  f"pool mean {pm[i]:.1f} s on its anchor")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO,
                    help="the checkout to read the engine, artifacts and pack from")
    ap.add_argument("--pack", default=PACK)
    ap.add_argument("--mode", default="all", choices=("all", "gap", "score", "ratings"))
    ap.add_argument("--base-dump", help="run_joint's XCP_HOLDOUT_DUMP (forward), for score")
    ap.add_argument("--sample-pct", type=float, help="default: the dump's")
    ap.add_argument("--sample-seed", type=int, help="default: the dump's")
    ap.add_argument("--seasons-back", type=int, default=6)
    ap.add_argument("--outer", type=int, default=30)
    ap.add_argument("--jack-outer", type=int, default=15)
    ap.add_argument("--no-jackknife", dest="jackknife", action="store_false")
    ap.add_argument("--kappa-profile", action="store_true")
    ap.add_argument("--era-years", type=int, default=0)
    a = ap.parse_args()

    rows = Rows(loadPack(a.pack), a.era_years)
    fitted = None
    if a.mode in ("all", "gap"):
        print("\n================ GAP ================")
        fitted = modeGap(rows, a)
    if a.mode in ("all", "score"):
        if not a.base_dump:
            ap.error("score needs --base-dump")
        print("\n================ SCORE ================")
        modeScore(rows, a)
    if a.mode in ("all", "ratings"):
        print("\n================ RATINGS ================")
        modeRatings(rows, a, fitted)


if __name__ == "__main__":
    main()
