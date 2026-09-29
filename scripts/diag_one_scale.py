#!/usr/bin/env python3
"""
diag_one_scale.py -- the distance curve by POOL (live) against the curve by
ABILITY (engine/data/distance_ability.pkl), before anything is switched.

    /srv/venv/bin/python scripts/diag_one_scale.py                 # A: curves only, no database
    /srv/venv/bin/python scripts/diag_one_scale.py --holdout       # + B: held-out 5K -> 8K/10K
    /srv/venv/bin/python scripts/diag_one_scale.py --boards        # + C: the top of the boards
    /srv/venv/bin/python scripts/diag_one_scale.py --fivek         # + D: ratings as 5K times
    /srv/venv/bin/python scripts/diag_one_scale.py --all

★ WHY (owner, 2026-09-29, approved behind switches: "try to be safe and
  test it"). 28:34 over Gans Creek's 10000 m read HS-equivalent 156.6 /
  150.0 / 147.6 as hs_m / college_m / ms_m. The four sections answer the
  four questions the switch has to pass before it is turned on:

  A  THE SAME RUN, EVERY LABEL. For a run at 3k/5k/8k/10k, the HS-equivalent
     as each pool relative to the high-school pool:
         HS(pool) / HS(hs) = [F_hs(d) / F_hs(5k)] / [F_pool(d) / F_pool(5k)]
     (pool_view's formula with the pool constants cancelled -- they are
     exact under XCP_ONE_SCALE). By ability every same-gender pool shares
     one family, so the ratio is 1 by construction; the section prints both
     so the size of what disappears is on the page. Artifacts only.
     ★ WITH THE PER-POOL RESIDUAL (2026-09-29, the adopted setting) the
     label comes back by design, by exactly the residual the fitter
     applied: the "abil+res" column and the table under each gender print
     its size (the owner's dry run: ~0.2% high school vs college, a few %
     for middle school at 3k and 10k).
  B  DOES IT PREDICT BETTER? Same-athlete pairs (the fitters' cache: <= 21
     days apart), 20% held out by a fixed seed. The ability family is
     refitted on the other 80% HERE; the live pool curves were fitted on
     ALL pairs, test ones included -- the comparison is tilted toward the
     baseline, so a win for the ability curve is conservative. Each
     longer race is predicted from the shorter one alone (your 5K, what is
     your 8K?), with each model's own calendar offset. Median error and
     median |error|, per pool and transition, for THREE models on the same
     held-out pairs: the pool curves, the ability curve alone, and the
     ability curve with the per-pool residual (its joint fit, refitted on
     the same 80%) -- then one verdict line per pool.
  C  THE BOARDS. The top of each pool's XC board for a season, re-priced to
     first order without a re-solve: a row's normalized_time moves by
         rho = [F_pool(d) / F_pool(5k)] / F_ability(t, d)
     its own-pool rating by rho / median(rho over the pool) (the pool mean
     re-anchors), and its HS-equivalent by rho / median(rho over the HS
     pool). Ranks inside the pool before and after. F_ability is the
     adopted setting: with the per-pool residual when the artifact has it
     (--no-residual for the curve alone).
  D  RATINGS AS 5K TIMES. conversions.fiveKForHsRating: an HS-equivalent as
     a 5K on an average XC course and on a track, both genders, and each
     pool's own 100 -- "HS-equivalent 150 = 14:xx on the track".

READ-ONLY. It writes nothing, switches nothing, and evaluates both curve
families from their files, whatever XCP_DISTANCE_BY says.
"""
import argparse
import math
import os
import pickle
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("engine", "scripts", "racecast"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")

import distance_ability as DA                                # noqa: E402

POOL_ART = os.path.join(_ROOT, "engine", "data", "distance_spline.pkl")
ABILITY_ART = os.path.join(_ROOT, "engine", "data", "distance_ability.pkl")
DISTANCES = (3000.0, 5000.0, 8000.0, 10000.0)
POOLS = ("ms", "hs", "college", "pro")
HOLDOUT_FRAC = 0.2
HOLDOUT_SEED = 20260929


def mmss(s):
    if s is None:
        return "-"
    return f"{int(s // 60)}:{s % 60:04.1f}"


# ------------------------------------------------------------------ #
# THE TWO CURVE FAMILIES, EVALUATED FROM THEIR FILES
# ------------------------------------------------------------------ #

def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def _evalPot(entry, ld):
    """normalize_distance._evalDistancePotential, called rather than copied."""
    import normalize_distance as nd
    return nd._evalDistancePotential(entry, ld)


def poolEntry(art, pool, sport):
    """normalize_distance._distancePotentialEntry on an explicit artifact."""
    pools = art["pools"]
    e = pools.get(f"{pool}|{sport}") or art.get("global_by_sport", {}).get(sport)
    return e or pools.get(pool) or art["global"]


def poolRel(art, pool, sport, d):
    """F_pool(d) / F_pool(5000): the pool curve's 5K-equivalent multiplier
    (its anchor cancels)."""
    e = poolEntry(art, pool, sport)
    return math.exp(_evalPot(e, math.log(5000.0)) - _evalPot(e, math.log(d)))


def poolInSpan(art, pool, sport, d):
    e = poolEntry(art, pool, sport)
    sp = e.get("span")
    return bool(sp) and sp[0] * 0.99 <= d <= sp[1] * 1.01


def abilityRel(art, pool, sport, t, d, residual=False):
    """T5 / t by ability: the shared curve, or with `residual` the family's
    joint fit with this pool's applied residual (distance_ability)."""
    fam = DA.family(art, pool, sport, residual=residual)
    return None if fam is None else DA.factorForTime(fam, t, d, pool)


# ------------------------------------------------------------------ #
# A -- THE SAME RUN UNDER EVERY LABEL
# ------------------------------------------------------------------ #

def sectionA(pool_art, ab_art, sport="XC"):
    print("\nA. THE SAME RUN, EVERY LABEL: HS-equivalent as <pool> over HS-equivalent "
          "as hs (1.000 = the label changes nothing)")
    print("   baseline = the live per-pool curves; * = the pool's curve is extrapolated "
          "at that distance")
    for g in ("m", "f"):
        fam = DA.family(ab_art, f"hs_{g}", sport) if ab_art else None
        if fam is not None:
            ref = fam["ref_5k"]
            grid = [("fast", ref * math.exp(fam["a_lo"])), ("HS median", ref),
                    ("slow", ref * math.exp(fam["a_hi"]))]
        else:
            # no family yet: the pool curves do not depend on the runner, so
            # one row per distance says everything the baseline has to say
            grid = [("any runner", None)]
        print(f"\n   {sport} {'boys/men' if g == 'm' else 'girls/women'} "
              f"(runners by their 5K equivalent on the ability family)")
        hdr = "".join(f"{p:>12}" for p in POOLS if p != "hs")
        print(f"   {'run':<24}{'5K eq':>8}{hdr}{'spread':>9}{'ability':>9}{'abil+res':>10}")
        res_rows = []
        for label, t5 in grid:
            for d in DISTANCES:
                t = (math.exp(DA.inverseLog(fam, math.log(t5), math.log(d / 5000.0)))
                     if fam is not None else None)
                hs_rel = poolRel(pool_art, f"hs_{g}", sport, d)
                cells, vals = [], [1.0]
                for p in POOLS:
                    if p == "hs":
                        continue
                    pool = f"{p}_{g}"
                    r = hs_rel / poolRel(pool_art, pool, sport, d)
                    vals.append(r)
                    star = "" if poolInSpan(pool_art, pool, sport, d) else "*"
                    cells.append(f"{r:>11.4f}{star or ' '}")
                hs_star = "" if poolInSpan(pool_art, f"hs_{g}", sport, d) else " (hs*)"
                spread = 100 * (max(vals) / min(vals) - 1)
                ab = abr = "-"
                if ab_art and t:
                    rs = [abilityRel(ab_art, f"{p}_{g}", sport, t, d) for p in POOLS]
                    rs = [v for v in rs if v]
                    ab = f"{100 * (max(rs) / min(rs) - 1):.2f}%" if rs else "-"
                    if DA.hasResidual(ab_art):
                        rr = {p: abilityRel(ab_art, f"{p}_{g}", sport, t, d, residual=True)
                              for p in POOLS + ("elem",)}
                        rr = {p: v for p, v in rr.items() if v}
                        if rr:
                            abr = f"{100 * (max(rr.values()) / min(rr.values()) - 1):.2f}%"
                            res_rows.append((label, t, d, t5, rr))
                print(f"   {label[:10]:<10}{mmss(t):>7} {d / 1000:>4.0f}k{hs_star:<6}"
                      f"{mmss(t5):>8}{''.join(cells)}{spread:>8.2f}%{ab:>9}{abr:>10}")
        if res_rows:
            # ★ THE LABEL THE RESIDUAL BRINGS BACK, pool by pool: HS(pool) /
            #   HS(hs) = F_hs(t, d) / F_pool(t, d) on the joint fit -- 1 at
            #   5k for everyone, and exactly the applied residual elsewhere
            fam_j = DA.family(ab_art, f"hs_{g}", sport, residual=True)
            applied = (fam_j or {}).get(DA.APPLIED_KEY) or {}
            print("   with the per-pool residual (applied: "
                  + (", ".join(f"{p} {v:+.4f}" for p, v in sorted(applied.items()))
                     or "none") + "):")
            others = [p for p in POOLS + ("elem",) if p != "hs"]
            print(f"   {'run':<24}{'5K eq':>8}" + "".join(f"{p:>12}" for p in others))
            for label, t, d, t5, rr in res_rows:
                cells = "".join(
                    f"{rr['hs'] / rr[p]:>12.4f}" if p in rr and "hs" in rr else f"{'-':>12}"
                    for p in others)
                print(f"   {label[:10]:<10}{mmss(t):>7} {d / 1000:>4.0f}k{'':<6}"
                      f"{mmss(t5):>8}{cells}")
    # the owner's own example, conversion by conversion
    t, d = 28 * 60 + 34.0, 10000.0
    print(f"\n   the report's run, {mmss(t)} over {d:.0f} m (XC): 10000 -> 5000 multiplier")
    for p in ("hs_m", "college_m", "ms_m", "pro_m"):
        base = poolRel(pool_art, p, sport, d)
        ab = abilityRel(ab_art, p, sport, t, d) if ab_art else None
        ext = "" if poolInSpan(pool_art, p, sport, d) else " (extrapolated)"
        tail = f"   by ability x{ab:.4f}" if ab else ""
        if ab_art and DA.hasResidual(ab_art):
            abr = abilityRel(ab_art, p, sport, t, d, residual=True)
            tail += f"   + residual x{abr:.4f}" if abr else ""
        print(f"     {p:<10} pool curve x{base:.4f}{ext:<15}{tail}")


# ------------------------------------------------------------------ #
# B -- HELD-OUT CROSS-DISTANCE PREDICTION
# ------------------------------------------------------------------ #

# transitions the owner asked about, shorter leg -> longer leg, with a
# window of +-2% around each named distance (course fuzz; the pair guard
# already needs >= 5% between the legs)
TRANSITIONS = {
    "XC": [(3000, 5000), (4000, 5000), (5000, 6000), (5000, 8000),
           (5000, 10000), (6000, 8000), (8000, 10000)],
    "TF": [(800, 1600), (1600, 3200), (1500, 3000), (1600, 5000),
           (3200, 5000), (5000, 10000)],
}


def _near(d, target):
    return abs(d / target - 1.0) <= 0.02 or (target == 1600 and abs(d / 1609.34 - 1) <= 0.02) \
        or (target == 3200 and abs(d / 3218.69 - 1) <= 0.02)


def predictBaseline(pool_art, pool, sport, t_in, d_in, d_out, later):
    """log t_out from the pool curve and its calendar eps (raw orientation:
    the later race is faster by eps -- fit_distance_exponent's matched
    contrasts, sf = fair - eps, lf = fair + eps)."""
    e = poolEntry(pool_art, pool, sport)
    eps = float(e.get("eps") or 0.0)
    y = _evalPot(e, math.log(d_out)) - _evalPot(e, math.log(d_in))
    return math.log(t_in) + y + (-eps if later else eps)


def predictAbility(fam, t_in, d_in, d_out, later, pool=None):
    """By ability: the input leg's 5K equivalent, back out at d_out. With a
    joint fit and the row's pool, the pool's residual on both legs."""
    l5 = DA.forwardLog(fam, math.log(t_in), math.log(d_in / 5000.0), pool)
    lo = DA.inverseLog(fam, l5, math.log(d_out / 5000.0), pool)
    return lo + (fam["calendar"] if later else -fam["calendar"])


MODELS = ("pool", "ability", "abil+res")


def verdict(pool, stats):
    """One line per pool: which model predicts its held-out races best
    (median |error|), and which is least biased."""
    have = [m for m in MODELS if m in stats]
    best = min(have, key=lambda m: stats[m][1])
    least = min(have, key=lambda m: abs(stats[m][0]))
    cells = " / ".join(f"{m} {100 * stats[m][1]:.2f}" for m in have)
    bias = " / ".join(f"{100 * stats[m][0]:+.2f}" for m in have)
    return (f"   {pool:<12} MdAE {cells} -> {best.upper()}; bias {bias} -> "
            f"least biased {least}")


def _stats(errs):
    import numpy as np
    e = np.asarray(errs)
    return (float(np.median(e)), float(np.median(np.abs(e))),
            float(np.percentile(np.abs(e), 90)))


def sectionB(pool_art, ab_art, fresh=False):
    import numpy as np
    import fit_distance_ability as F
    print("\nB. HELD-OUT: predict the LONGER race from the shorter (same athlete, "
          "<= 21 days)")
    xc, tf = F.loadPairs(fresh)
    out = {}
    for sport, arr in (("XC", xc), ("TF", tf)):
        n = len(arr["d1"])
        rng = np.random.default_rng(HOLDOUT_SEED)
        test = rng.random(n) < HOLDOUT_FRAC
        out[sport] = (F.subset(arr, ~test), F.subset(arr, test))
    degrees = degrees_joint = None
    if ab_art:
        degrees = {k: tuple(e["degree"]) for k, e in ab_art["families"].items()}
        degrees_joint = {k: tuple(e[DA.JOINT_KEY]["degree"])
                         for k, e in ab_art["families"].items()
                         if e.get(DA.JOINT_KEY) is not None}
    print(f"   refitting the ability families, and their joint fits with the per-pool "
          f"residual, on the 80% ({'the artifact' if degrees else 'CV'}'s degrees)...")
    art = F.fitAll(out["XC"][0], out["TF"][0], verbose=False, degrees=degrees,
                   agreement=False, degrees_joint=degrees_joint)
    for k, e in sorted(art["families"].items()):
        j = e.get(DA.JOINT_KEY)
        if j is not None:
            print(f"   {k}: residual applied on the 80%: "
                  + (", ".join(f"{p} {v:+.4f}" for p, v in sorted(j[DA.APPLIED_KEY].items()))
                     or "none"))
    for sport in ("XC", "TF"):
        te = out[sport][1]
        rows = {}
        for i in range(len(te["d1"])):
            d1, d2, t1, t2 = te["d1"][i], te["d2"][i], te["t1"][i], te["t2"][i]
            pool = str(te["pool"][i]).split("|")[0]
            fam = DA.family(art, pool, sport)
            fam_j = DA.family(art, pool, sport, residual=True)
            if fam is None:
                continue
            # the shorter leg is the input; `later` = the output is race 2
            if d1 <= d2:
                t_in, d_in, t_out, d_out, later = t1, d1, t2, d2, True
            else:
                t_in, d_in, t_out, d_out, later = t2, d2, t1, d1, False
            eb = math.log(t_out) - predictBaseline(pool_art, pool, sport, t_in, d_in, d_out, later)
            ea = math.log(t_out) - predictAbility(fam, t_in, d_in, d_out, later)
            er = math.log(t_out) - predictAbility(fam_j, t_in, d_in, d_out, later, pool)
            keys = [(pool, "all")]
            for a, b in TRANSITIONS[sport]:
                if _near(d_in, a) and _near(d_out, b):
                    keys.append((pool, f"{a}->{b}"))
            for k in keys:
                rows.setdefault(k, ([], [], []))
                rows[k][0].append(eb)
                rows[k][1].append(ea)
                rows[k][2].append(er)
        print(f"\n   {sport}: {len(te['d1']):,} held-out pairs. error = log(actual / "
              f"predicted), in % of time")
        print(f"   {'pool':<12}{'transition':<13}{'n':>8}   {'pool curves bias/MdAE/p90':>26}"
              f"   {'ability bias/MdAE/p90':>24}   {'abil+res bias/MdAE/p90':>24}   better")
        verdicts = []
        for (pool, tr), errs in sorted(rows.items(),
                                       key=lambda kv: (kv[0][0], kv[0][1] != "all", kv[0][1])):
            if len(errs[0]) < 30:
                continue
            st = dict(zip(MODELS, (_stats(e) for e in errs)))
            win = min(MODELS, key=lambda m: st[m][1])
            print(f"   {pool:<12}{tr:<13}{len(errs[0]):>8,}   "
                  + "      ".join(f"{100 * st[m][0]:>+7.2f} {100 * st[m][1]:>6.2f} "
                                  f"{100 * st[m][2]:>6.2f}" for m in MODELS)
                  + f"      {win}")
            if tr == "all":
                verdicts.append(verdict(pool, st))
        print(f"\n   {sport} verdict per pool (all transitions; MdAE and bias in %):")
        for line in verdicts:
            print(line)


# ------------------------------------------------------------------ #
# C -- THE TOP OF THE BOARDS, RE-PRICED TO FIRST ORDER
# ------------------------------------------------------------------ #

_BOARD_SQL = """
    SELECT rr.person_id, COALESCE(a.name, '?') AS name, rr.speed_rating,
           r.time_seconds, COALESCE(dov.distance, m.distance) AS distance
    FROM   ranking_results rr
    JOIN   results r ON r.result_id = rr.result_id
    LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
    LEFT JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                     AND m.source = r.source
    LEFT JOIN athletes a ON a.athlete_id = rr.person_id
    WHERE  rr.pool = %(pool)s AND rr.sport = 'XC' AND rr.year = %(year)s
      AND  rr.speed_rating > 0 AND r.time_seconds > 0
    ORDER  BY rr.speed_rating DESC
    LIMIT  %(n)s
"""

_SAMPLE_SQL = """
    SELECT r.time_seconds, COALESCE(dov.distance, m.distance) AS distance
    FROM   ranking_results rr TABLESAMPLE SYSTEM (%(pct)s)
    JOIN   results r ON r.result_id = rr.result_id
    LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
    LEFT JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                     AND m.source = r.source
    WHERE  rr.pool = %(pool)s AND rr.sport = 'XC' AND r.time_seconds > 0
    LIMIT  20000
"""


def _rho(pool_art, ab_art, pool, t, d, residual=True):
    ab = abilityRel(ab_art, pool, "XC", t, d, residual=residual)
    if not ab:
        return None
    return poolRel(pool_art, pool, "XC", d) / ab


def sectionC(pool_art, ab_art, year=None, top=25, pct=0.5, residual=True):
    import numpy as np
    from database import getConn
    import pool_view
    residual = residual and DA.hasResidual(ab_art)
    print("\nC. THE TOP OF THE XC BOARDS, re-priced to first order (no re-solve; "
          f"see the header); by ability {'WITH' if residual else 'without'} the "
          f"per-pool residual")
    with getConn() as conn, conn.cursor() as cur:
        if year is None:
            cur.execute("SELECT max(year) FROM ranking_results WHERE sport = 'XC'")
            year = cur.fetchone()[0]
        med = {}
        for pool in ("hs_m", "hs_f", "college_m", "college_f"):
            cur.execute(_SAMPLE_SQL, {"pool": pool, "pct": pct})
            rh = [_rho(pool_art, ab_art, pool, float(t), float(d), residual)
                  for t, d in cur.fetchall() if t and d]
            rh = [v for v in rh if v]
            med[pool] = float(np.median(rh)) if rh else None
            print(f"   {pool}: median rho over {len(rh):,} sampled rows = "
                  f"{med[pool] if med[pool] else float('nan'):.4f}")
        for pool in ("hs_m", "college_m", "hs_f", "college_f"):
            g = pool.rsplit("_", 1)[-1]
            s_pool, s_hs = med.get(pool), med.get(f"hs_{g}")
            if not (s_pool and s_hs):
                continue
            factor = pool_view.repFactor(pool, "XC") or 1.0
            cur.execute(_BOARD_SQL, {"pool": pool, "year": year, "n": top * 8})
            # a board shows each athlete's best row: the old board's best OLD
            # row, the new board's best NEW row (not always the same race)
            old_best, new_best = {}, {}
            for pid, name, r, t, d in cur.fetchall():
                if not d:
                    continue
                rho = _rho(pool_art, ab_art, pool, float(t), float(d), residual)
                if not rho:
                    continue
                own_new = float(r) * rho / s_pool
                if pid not in old_best or float(r) > old_best[pid][1]:
                    old_best[pid] = (name, float(r), float(r) * factor, float(d))
                if pid not in new_best or own_new > new_best[pid][0]:
                    new_best[pid] = (own_new, float(r) * factor * rho / s_hs)
            rows = [(pid, (ob[0], ob[1], new_best[pid][0], ob[2], new_best[pid][1], ob[3]))
                    for pid, ob in old_best.items()]
            old_rank = {pid: i + 1 for i, (pid, _v) in
                        enumerate(sorted(rows, key=lambda kv: -kv[1][1]))}
            new_rank = {pid: i + 1 for i, (pid, _v) in
                        enumerate(sorted(rows, key=lambda kv: -kv[1][2]))}
            print(f"\n   {pool} XC {year}: own rating old -> new, HS-equivalent old -> new, "
                  f"rank old -> new (factor x{factor:.4f} today)")
            moved = []
            for pid, (name, r, own_new, hs_old, hs_new, d) in sorted(
                    rows, key=lambda kv: old_rank[kv[0]])[:top]:
                moved.append(abs(new_rank[pid] - old_rank[pid]))
                print(f"     {old_rank[pid]:>3} -> {new_rank[pid]:<3} {name[:26]:<27}"
                      f"{r:>7.1f} -> {own_new:<7.1f} HS {hs_old:>6.1f} -> {hs_new:<6.1f}"
                      f" ({d:.0f} m)")
            if moved:
                print(f"     median |rank move| in the top {top}: {np.median(moved):.0f}, "
                      f"max {max(moved)}")


# ------------------------------------------------------------------ #
# D -- RATINGS AS 5K TIMES
# ------------------------------------------------------------------ #

def sectionD():
    import conversions as C
    print("\nD. RATINGS AS 5K TIMES (conversions.fiveKForRating: average XC course / "
          "typical outdoor track)")
    for g, label in (("m", "boys"), ("f", "girls")):
        cells = []
        for r in (100, 120, 140, 150, 160, 170):
            fk = C.fiveKForHsRating(r, g)
            if fk:
                cells.append(f"{r}: {mmss(fk['xc'])} / {mmss(fk['track'])}")
        print(f"   HS-equivalent, {label}: " + ";  ".join(cells))
    print("   each pool's own 100 (the pool's average runner):")
    for pool in ("ms_m", "hs_m", "college_m", "ms_f", "hs_f", "college_f"):
        fk = C.fiveKForRating(100, pool)
        if fk:
            print(f"     {pool:<10} {mmss(fk['xc'])} XC / {mmss(fk['track'])} track")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--boards", action="store_true")
    ap.add_argument("--fivek", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--fresh", action="store_true", help="re-stream the pairs")
    ap.add_argument("--ability", default=ABILITY_ART)
    ap.add_argument("--no-residual", action="store_true",
                    help="C: re-price with the ability curve alone")
    args = ap.parse_args()
    pool_art = _load(POOL_ART)
    ab_art = _load(args.ability)
    if pool_art is None:
        raise SystemExit(f"no {POOL_ART}")
    if ab_art is None:
        print(f"  ! no {args.ability}: fit it first (engine/fit_distance_ability.py); "
              f"section A shows the baseline alone, B fits its own")
    else:
        print(f"  ability artifact: fitted {ab_art.get('fitted')}, families "
              f"{sorted(ab_art['families'])}")
        if DA.hasResidual(ab_art):
            print("  per-pool residual applied: " + "; ".join(
                f"{k} " + (", ".join(f"{p} {v:+.4f}" for p, v in sorted(r.items())) or "none")
                for k, r in sorted((ab_art.get("residual_applied") or {}).items())))
        else:
            print("  ! this artifact predates the per-pool residual: refit it "
                  "(engine/fit_distance_ability.py) for the abil+res columns")
    sectionA(pool_art, ab_art)
    if args.holdout or args.all:
        sectionB(pool_art, ab_art, fresh=args.fresh)
    if (args.boards or args.all) and ab_art:
        sectionC(pool_art, ab_art, year=args.year, top=args.top,
                 residual=not args.no_residual)
    if args.fivek or args.all:
        sectionD()


if __name__ == "__main__":
    main()
