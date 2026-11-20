"""Margin of error of a warm-started joint solve (2026-11-20).

MEASUREMENT ONLY -- docs/MOE-WARM-START-2026-11-20.md. Uses the test-only
warm-start hook in joint_solve.solveJoint (D.warm_start + XCP_WARM_START);
nothing here touches the database or the shipped solve.

    MOE_SCALE=0.25 python -I scripts/moe_warm_start.py           # 1M rows
    MOE_HARD=1 MOE_SCALE=0.25 python -I scripts/moe_warm_start.py


Night 1: a production-shaped synthetic corpus, solved cold.
Night 2: night 1 + one new meet weekend (~0.7% of rows) + a few corrected
rows. Night 2 is solved cold (the reference) and warm from night 1.
"""
import json
import os
import sys
import time

import numpy as np

ROOT = os.environ.get("XCP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import joint_solve as js  # noqa: E402

# MOE_HARD=1: sparser linkage (99% in region, 10% two-sport) and season
# links between consecutive athlete-seasons of one person (3 a person)
HARD = os.environ.get("MOE_HARD", "0") == "1"
SCALE = float(os.environ.get("MOE_SCALE", "0.5"))
OUT = os.environ.get("MOE_OUT", os.path.join(__import__("tempfile").gettempdir(), "moe_warm_start"))
os.makedirs(OUT, exist_ok=True)


def universe(n=4_000_000, n_ath=500_000, n_cell=30_000, n_race=250_000,
             n_pool=8, seed=1):
    """Night 2's rows, in global ids (synth.build's generator)."""
    rng = np.random.default_rng(seed)
    ath = np.sort(rng.integers(0, n_ath, n))
    race_cell = rng.integers(0, n_cell, n_race)
    group = rng.integers(0, 2, n_cell)
    if os.environ.get("MOE_CLUSTER", "1") == "1":
        # ★ PRODUCTION-SHAPED LINKAGE: athletes race in their own region
        #   (95%) and most in one sport (70%), so the graph has weak
        #   cross-region and XC-to-track links -- the slow directions the
        #   real solve fights. An unclustered draw is an expander and CG
        #   converges in a handful of steps, which says nothing.
        n_reg = max(4, n_cell // 150)
        reg_cell = rng.integers(0, n_reg, n_cell)
        key_race = reg_cell[race_cell] * 2 + group[race_cell]
        by = np.argsort(key_race, kind="stable")
        starts = np.searchsorted(key_race[by], np.arange(2 * n_reg + 1))
        reg_ath = rng.integers(0, n_reg, n_ath)
        sport_ath = rng.integers(0, 2, n_ath)
        both = rng.random(n_ath) < (0.1 if HARD else 0.3)
        r_row = np.where(rng.random(n) < (0.99 if HARD else 0.95), reg_ath[ath], rng.integers(0, n_reg, n))
        s_row = np.where(both[ath], rng.integers(0, 2, n), sport_ath[ath])
        k = r_row * 2 + s_row
        cnt = starts[k + 1] - starts[k]
        k = np.where(cnt > 0, k, k ^ 1)          # empty (region, sport): other sport
        cnt = starts[k + 1] - starts[k]
        race = by[starts[k] + (rng.random(n) * cnt).astype(np.int64)]
    else:
        race = rng.integers(0, n_race, n)
    cell = race_cell[race]
    pool_of_ath = rng.integers(0, n_pool, n_ath)
    day = rng.integers(1, 366, n).astype(float)
    sport_row = group[cell]
    first = (rng.random(n) < 0.1).astype(float)
    dist = np.where(sport_row == 1, rng.integers(-1, 20, n), -1)
    lz = rng.normal(size=n) * 0.3
    tz = rng.normal(size=n) * 0.3
    alt_cell = np.where(rng.random(n_cell) < 0.1, rng.random(n_cell), 0.0)
    imp = np.where(rng.random(n) < 0.2, rng.integers(0, 12, n), -1)
    ind = (rng.random(n_cell) < 0.05) & (group == 1)
    sub = rng.integers(0, 3, n)
    effk = rng.integers(0, 3, n)
    a = rng.normal(0, 0.15, n_ath)
    if HARD:
        # seasons of one person (3 a person, the link's pairs) share a level
        # and move by a few percent a year, as the link term assumes
        a = rng.normal(0, 0.15, n_ath // 3 + 1)[np.arange(n_ath) // 3] + rng.normal(0, 0.03, n_ath)
    y = (a[ath] - 0.02 * group[cell] + rng.normal(0, 0.05, n_cell)[cell]
         + rng.normal(0, 0.02, n_race)[race] + rng.normal(0, 0.03, n)
         + np.where(rng.random(n) < 0.05, rng.exponential(0.05, n), 0.0))
    # the new meet weekend: whole races, run on one weekend (day 300/301)
    new_race = np.zeros(n_race, dtype=bool)
    rows_per_race = np.bincount(race, minlength=n_race)
    order = rng.permutation(n_race)
    target = 0.007 * n
    acc = np.cumsum(rows_per_race[order])
    new_race[order[:int(np.searchsorted(acc, target)) + 1]] = True
    new_row = new_race[race]
    day = np.where(new_row, 300.0 + (race % 2), day)
    # corrected rows: night 1 carried a wrong time (typo / wrong event),
    # night 2 the right one -- 0.05% of rows
    corr = (~new_row) & (rng.random(n) < 0.0005)
    y_night1 = y.copy()
    y_night1[corr] += rng.choice([-1, 1], corr.sum()) * rng.uniform(0.03, 0.25, corr.sum())
    return dict(ath=ath, race=race, cell=cell, group=group, pool_of_ath=pool_of_ath,
                day=day, first=first, dist=dist, lz=lz, tz=tz, alt_cell=alt_cell,
                imp=imp, ind=ind, sub=sub, effk=effk, y2=y, y1=y_night1,
                new_row=new_row, corr=corr, n_pool=n_pool)


# ★ RACE SIZE DECIDES HOW FAST THE OUTER LOOP CONVERGES: sigma_u2's EM
#   update moves at the rate the races identify their day. MOE_RPR rows a
#   race (production: dozens to hundreds), MOE_RPC races a cell.
RPR = float(os.environ.get("MOE_RPR", "40"))
RPC = float(os.environ.get("MOE_RPC", "8"))


def sizes(s):
    n_race = int(4_000_000 * s / RPR)
    return max(int(n_race / RPC), 50), n_race


def compact(ids):
    u, inv = np.unique(ids, return_inverse=True)
    return u, inv.astype(np.int64)


def night(U, rows, y):
    """A Design over the given rows, ids compacted; returns (y, D, maps)."""
    ath_g, ath = compact(U["ath"][rows])
    cell_g, cell = compact(U["cell"][rows])
    race_g, race = compact(U["race"][rows])
    n_ath, n_cell, n_race = ath_g.size, cell_g.size, race_g.size
    group = U["group"][cell_g]
    sport_row = group[cell]
    s = sport_row - 0.5
    cnt = np.bincount(ath, minlength=n_ath)
    sbar = np.bincount(ath, weights=s, minlength=n_ath) / np.maximum(cnt, 1)
    sc = s - sbar[ath]
    pool_of_ath = U["pool_of_ath"][ath_g]
    lz = U["lz"][rows]
    lzc = lz - (np.bincount(ath, weights=lz, minlength=n_ath) / np.maximum(cnt, 1))[ath]
    link = None
    if HARD:
        g0 = ath_g[:-1]
        g1 = ath_g[1:]
        same = (g0 // 3 == g1 // 3)
        k0 = np.flatnonzero(same)
        link = (k0, k0 + 1, np.ones(k0.size))
    D = js.Design(ath, cell, race, group_of_cell=group, sc=sc, link=link,
                  pool_row=pool_of_ath[ath], day=U["day"][rows],
                  first=U["first"][rows], n_ath=n_ath, n_cell=n_cell,
                  n_race=n_race, n_pool=U["n_pool"], dist=U["dist"][rows], n_e=20,
                  # lz centred within the athlete-season (logDistCentered);
                  # no tz: the form trend is off unless XCP_FORM_TREND > 0
                  lz=lzc, tz=None,
                  alt=U["alt_cell"][U["cell"][rows]], dist_banded=True,
                  imp=U["imp"][rows], n_imp=12, ind=U["ind"][cell_g])
    _, subr = compact(race * 3 + U["sub"][rows])
    D.subrace = subr
    D.n_subrace = int(subr.max()) + 1
    D.subrace_from = "proxy"
    D.effort_group = (sport_row * 3 + U["effk"][rows]).astype(np.int64)
    D.effort_group_names = [f"g{i}" for i in range(6)]
    maps = dict(ath_g=ath_g, cell_g=cell_g, race_g=race_g, rows=rows,
                pool_of_ath=pool_of_ath)
    return np.ascontiguousarray(y[rows]), D, maps


CG_LOG = []
_cg = js.conjugateGradient


def _cgLogged(rhs, *a, **k):
    x, it = _cg(rhs, *a, **k)
    CG_LOG.append((rhs.size, it))
    return x, it


js.conjugateGradient = _cgLogged


def solve(y, D, maps, n_outer=5, mode="", tol=1e-8, warm=None):
    js.CG_TOL = tol
    js.CG_TOL_OUTER = tol
    os.environ["XCP_WARM_START"] = mode
    D.warm_start = warm
    CG_LOG.clear()
    t = time.time()
    out = js.solveJoint(y, design=D, athlete_pool=maps["pool_of_ath"],
                        n_outer=n_outer, n_probe=0, verbose=False)
    dt = time.time() - t
    iters = [it for sz, it in CG_LOG if sz == D.n_total]
    js.CG_TOL = js.CG_TOL_OUTER = 1e-8
    D.warm_start = None
    return out, dt, iters


def warmFrom(out1, m1, y2, D2, m2):
    """Night 1's solution mapped onto night 2's indexing (by global keys)."""
    D1n = out1["_D"]
    th1 = out1["theta"]
    th = np.zeros(D2.n_total)

    def remap(g1, g2, src, n2, fill=0.0):
        pos = np.searchsorted(g1, g2)
        pos = np.minimum(pos, g1.size - 1)
        hit = g1[pos] == g2
        dst = np.full(n2, fill)
        dst[hit] = src[pos[hit]]
        return dst, hit

    a, ahit = remap(m1["ath_g"], m2["ath_g"], th1[D1n.o_a:D1n.o_d], D2.n_ath)
    d, _ = remap(m1["cell_g"], m2["cell_g"], th1[D1n.o_d:D1n.o_u], D2.n_cell)
    u, _ = remap(m1["race_g"], m2["race_g"], th1[D1n.o_u:D1n.o_mu], D2.n_race)
    # a new athlete starts at the mean of their rows less the warm course
    newa = ~ahit
    if newa.any():
        grp = D2.group_row
        mu = np.zeros(D2.n_group)
        mu[1:] = th1[D1n.o_mu:D1n.o_beta] if D1n.n_mu else 0.0
        r = y2 - (mu[grp] + d[D2.cell] + u[D2.race])
        s = np.bincount(D2.athlete, weights=r, minlength=D2.n_ath)
        c = np.bincount(D2.athlete, minlength=D2.n_ath)
        a = np.where(newa, s / np.maximum(c, 1), a)
    th[D2.o_a:D2.o_d] = a
    th[D2.o_d:D2.o_u] = d
    th[D2.o_u:D2.o_mu] = u
    # global blocks: same size both nights
    for o1, e1, o2, e2 in ((D1n.o_mu, D1n.o_beta, D2.o_mu, D2.o_beta),
                           (D1n.o_c, D1n.o_g, D2.o_c, D2.o_g),
                           (D1n.o_k, D1n.n_total, D2.o_k, D2.n_total)):
        assert e1 - o1 == e2 - o2, "a global block changed size"
        th[o2:e2] = th1[o1:e1]
    # per-athlete blocks besides a: beta, slope g, trend q
    for (o1, e1, o2, e2) in ((D1n.o_beta, D1n.o_c, D2.o_beta, D2.o_c),
                             (D1n.o_g, D1n.o_q, D2.o_g, D2.o_q),
                             (D1n.o_q, D1n.o_k, D2.o_q, D2.o_k)):
        if e2 > o2:
            assert e1 - o1 == D1n.n_ath and e2 - o2 == D2.n_ath
            th[o2:e2] = remap(m1["ath_g"], m2["ath_g"], th1[o1:e1], D2.n_ath)[0]
    warm = {"theta": th}
    # per row: by global row id (rows is a sorted index into the universe)
    r1, r2 = m1["rows"], m2["rows"]
    pos = np.minimum(np.searchsorted(r1, r2), r1.size - 1)
    old = r1[pos] == r2
    n_races = np.bincount(D2.athlete, minlength=D2.n_ath)
    rating = js.ratingsFromAbility(a, m2["pool_of_ath"], n_races,
                                   int(m2["pool_of_ath"].max()) + 1)
    h_new = js.tiltRows(rating[D2.athlete])
    amp_new = js.amplitudeFromRating(rating[D2.athlete])
    warm["w"] = np.where(old, out1["weights"][pos], 1.0)
    warm["h"] = np.where(old, out1["h"][pos], h_new)
    warm["amp"] = np.where(old, out1["amp"][pos], amp_new)
    warm["tau2"] = out1["tau2"]
    warm["sigma_u2"] = out1["sigma_u2"]
    warm["sigma2"] = out1["sigma2"]
    warm["curve_sd2"] = (None if out1.get("curve_sd") is None
                         else np.asarray(out1["curve_sd"]) ** 2)
    off = out1.get("race_day_div_offset")
    warm["div_off"] = None if off is None else np.where(old, off[pos], 0.0)
    warm["rating"] = rating
    return warm


def views(out, D, y):
    """What the site shows: athlete-season rating (points), per-row rating
    (points), course difficulty (%)."""
    a = out["ability_raw"] if "ability_raw" in out else out["ability"]
    rating = out["rating"]
    eff = out["h"] * (out["delta"][D.cell] + out["race_effect"][D.race])
    row = rating[D.athlete] * np.exp(a[D.athlete] - (y - eff))
    cd = 100.0 * np.expm1(out["delta"])
    return rating, row, cd


def stats(x, y, name):
    d = np.abs(np.asarray(x) - np.asarray(y))
    return {"what": name, "max": float(d.max()), "p999": float(np.percentile(d, 99.9)),
            "p99": float(np.percentile(d, 99)), "median": float(np.median(d)),
            "n_ge_005": int((d >= 0.05).sum()), "n": int(d.size)}


def compare(A, B, D, y):
    ra, rowa, ca = views(A, D, y)
    rb, rowb, cb = views(B, D, y)
    keep = A["n_races"] >= 1
    return [stats(ra[keep], rb[keep], "athlete-season rating (pts)"),
            stats(rowa, rowb, "row rating (pts)"),
            stats(ca, cb, "course difficulty (% pts)")]


def main():
    s = SCALE
    U = universe(n=int(4_000_000 * s), n_ath=int(500_000 * s),
                 n_cell=sizes(s)[0], n_race=sizes(s)[1])
    rows2 = np.arange(U["ath"].size)
    rows1 = np.flatnonzero(~U["new_row"])
    y1, D1, m1 = night(U, rows1, U["y1"])
    y2, D2, m2 = night(U, rows2, U["y2"])
    print(f"night1 {y1.size:,} rows, {D1.n_total:,} unknowns; night2 {y2.size:,} rows "
          f"(+{U['new_row'].sum():,} new = {100 * U['new_row'].mean():.2f}%, "
          f"{U['corr'].sum():,} corrected), {D2.n_total:,} unknowns", flush=True)
    res = {}

    def run(tag, y, D, m, **k):
        # ! A FRESH DESIGN EVERY SOLVE: solveJoint re-bands and re-calibrates
        #   the distance classes ON the design (rebandDist / calibrateDist),
        #   so a reused D starts the next "cold" solve from the last one's
        #   bands -- that, not threading, is what made a repeat differ.
        if D is D2:
            y, D, m = night(U, rows2, U["y2"])
        elif D is D1:
            y, D, m = night(U, rows1, U["y1"])
        out, dt, it = solve(y, D, m, **k)
        print(f"{tag}: {dt:.1f}s, CG iters per pass {it}", flush=True)
        res[tag] = {"time": dt, "iters": it}
        return out

    o1 = run("night1 cold", y1, D1, m1)
    o1["_D"] = D1
    warm = warmFrom(o1, m1, y2, D2, m2)
    ref = run("night2 cold", y2, D2, m2)
    ref2 = run("night2 cold repeat", y2, D2, m2)
    cases = {
        "cold repeat": ref2,
        "cold outer 6": run("night2 cold outer6", y2, D2, m2, n_outer=6),
        "cold tol 1e-10": run("night2 cold tol1e-10", y2, D2, m2, tol=1e-10),
        "warm theta, 5 outer": run("warm theta 5", y2, D2, m2, mode="theta", warm=warm),
        "warm theta, 5 outer, tol 1e-10": run("warm theta 5 tol1e-10", y2, D2, m2,
                                              mode="theta", warm=warm, tol=1e-10),
    }
    for k in (1, 2, 3, 5, 6):
        cases[f"warm full, {k} outer"] = run(f"warm full {k}", y2, D2, m2,
                                              n_outer=k, mode="full", warm=warm)
    cases["warm full, 2 outer, tol 1e-10"] = run("warm full 2 tol1e-10", y2, D2, m2,
                                                  n_outer=2, mode="full", warm=warm,
                                                  tol=1e-10)
    # how far is the cold 5-outer answer from the cold fixed point?
    far = run("night2 cold outer10", y2, D2, m2, n_outer=10)
    cases["cold outer 10"] = far
    # bit-identity of the cold repeat
    same = all(np.array_equal(ref[k], ref2[k]) for k in ("theta", "rating", "delta"))
    print("cold repeat bit-identical:", same, flush=True)
    res["cold_bit_identical"] = same
    res["vs_cold5"] = {}
    for name, o in cases.items():
        res["vs_cold5"][name] = compare(o, ref, D2, y2)
        print(f"\n== {name} vs night-2 cold (5 outer, tol 1e-8)")
        for st in res["vs_cold5"][name]:
            print(f"   {st['what']:<30} max {st['max']:.4f}  p99.9 {st['p999']:.4f}  "
                  f"p99 {st['p99']:.5f}  median {st['median']:.2e}  "
                  f">=0.05: {st['n_ge_005']:,} of {st['n']:,}")
    # each against the far fixed point too
    res["vs_cold10"] = {}
    for name in ("cold repeat", "cold outer 6", "warm full, 2 outer", "warm full, 3 outer",
                 "warm full, 5 outer", "warm theta, 5 outer"):
        res["vs_cold10"][name] = compare(cases[name], far, D2, y2)
        print(f"\n== {name} vs cold 10 outer")
        for st in res["vs_cold10"][name]:
            print(f"   {st['what']:<30} max {st['max']:.4f}  p99.9 {st['p999']:.4f}  "
                  f"median {st['median']:.2e}  >=0.05: {st['n_ge_005']:,}")
    # night1 vs night2 cold: how much one night's new data moves things
    with open(os.path.join(OUT, f"moe_{SCALE}{'_hard' if HARD else ''}.json"), "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
