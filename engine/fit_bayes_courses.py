#!/usr/bin/env python3
"""
fit_bayes_courses.py -- Slaney's model with POSTERIORS, on a subsample.

    python engine/fit_bayes_courses.py --selftest
    python engine/fit_bayes_courses.py --sport XC --pool hs_m --max-rows 120000 \
        --reference "crystal" --must-include "mt. sac" --must-include "woodward"

WHY THIS EXISTS
    Every dispute in this project is a question about whether two numbers differ
    by more than their error bars, and the production solver reports no error
    bars. Foot Locker against NXN at 5%. Mt. SAC's 5k at +4.0% against its
    3 mile's +10%. The window optimum at 30 or 45. Each was argued from
    mechanism because the numbers came without uncertainty, and the arguing lost
    four times.

    This fits the SAME MODEL on a subsample and reports, per course, a posterior
    mean and a credible interval. Then "are these two different?" has an answer.

★ THE MODEL IS SLANEY'S, IN LOGS (MalcolmSlaney/CrossCountryStats):

        race_time = (average - race_month*month_slope - student_year*year_slope)
                    * runner_ability * course_difficulty

    log y = mu + beta*doy + a[athlete-season] + d[course] + noise

    with his priors -- ability tight, courses loose -- and his gauge: ONE NAMED
    REFERENCE COURSE pinned to zero, not a weighted mean.

  ! student_year IS ABSORBED, NOT DROPPED. `a` is keyed per athlete-SEASON, so a
    runner improving across a career is a new unknown each year. That is
    strictly more general than his linear year_slope and needs no term; adding
    one would be collinear with the season key.

⚠ THREE DELIBERATE DIFFERENCES FROM HIS CODE, EACH FOR A STATED REASON.

  1. GIBBS, NOT PyMC's NUTS. He runs 36 chains x 2,000 samples through PyMC.
     Conditional on the variances this model is a GAUSSIAN LINEAR MODEL with
     normal priors, so every conditional posterior is conjugate and closed-form
     -- blocked Gibbs is EXACT here, not an approximation of NUTS, and it needs
     numpy alone. That matters twice: no new dependency on a box that has none,
     and it fits a subsample large enough to contain the courses under dispute.
     PyMC's sampler earns its keep on models with awkward geometry; this one has
     none.

  2. FITTED ON normalized_time, NOT RAW TIME. His difficulty is a raw time ratio
     at the course's own distance, so "Woodward Park (2.0)" and
     "Woodward Park (3.1)" are different numbers for the same ground and the gap
     between them is mostly the 1.1 miles. Ours has distance removed first, so
     d is terrain. compare_slaney.py already converts between the two and
     explains why forward is the right direction; this stays in OUR units so its
     intervals can be read against the published difficulties directly.

  3. A SUBSAMPLE, AND THAT IS THE POINT RATHER THAN A CONCESSION. 62.8M rows is
     ~900x his corpus. The disputed courses are Californian high-school
     cross-country, which IS his domain, so the subsample is the right size for
     the question, not a shrunken version of a bigger one.

! WHAT AGREEMENT WOULD AND WOULD NOT PROVE, and compare_slaney.py says this
  already: both models are two-way fixed effects on overlapping populations, so
  they share blind spots -- altitude, championship taper, anything constant
  within a venue. Agreement says the solver and the normalisation are sound. It
  cannot vindicate a term neither model has.
"""

import argparse
import math
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ★ HIS PRIORS, AND THE ORDERING IS THE INTERESTING PART. Ability sigma 0.25
#   against course sigma 1.0 -- TIGHTER ON RUNNERS THAN ON COURSES, which is the
#   reverse of our engine, where courses get three nested shrinkages and
#   levels() hands the athlete a plain mean. That his independent fit went the
#   other way is the external support for an athlete prior.
SIGMA_ABILITY = 0.25
SIGMA_COURSE = 1.0
# The noise variance is sampled, not stated: an inverse-gamma with a weak prior.
NOISE_A, NOISE_B = 2.0, 0.002       # ~ sd 0.03 in log time, loosely held
N_DRAWS, N_BURN = 1500, 500


# _gibbs
# Purpose:   Posterior draws for (a, d, beta, sigma) in
#                y = mu + beta*x + a[ai] + d[ci] + N(0, sigma^2)
#            with a ~ N(0, sa^2), d ~ N(0, sd^2), sigma^2 ~ InvGamma.
# Arguments: y; ai, ci -- integer codes; x -- the doy covariate, centred;
#            n_a, n_c; sa, sd; draws, burn, seed; ref -- course index pinned
#            to zero each sweep, or None.
# Output:    dict of posterior draw arrays.
#
# ★ WHY EVERY STEP IS A CLOSED FORM. Given d, beta and sigma, each a_i is a
#   normal posterior from its own rows only -- the design is one-hot, so the
#   athletes are conditionally independent of each other and the whole block
#   updates with two bincounts. Same for d. That is what makes an exact sampler
#   cheap here, and it is why this does not need NUTS.
#
# ! THE PIN IS APPLIED INSIDE THE LOOP, NOT AFTERWARDS. a and d are only
#   identified up to a constant -- add c to every d, subtract c from every a,
#   and the likelihood is unchanged. Sampling an unidentified pair lets the
#   chain random-walk along that ridge for ever and the intervals come out
#   meaninglessly wide. Subtracting d[ref] from d and adding it to a each sweep
#   removes the ridge while leaving the fit untouched, which is Slaney's
#   Crystal-Springs-=-1.0 done as a sampler step.
def _gibbs(y, ai, ci, x, n_a, n_c, sa=SIGMA_ABILITY, sd=SIGMA_COURSE,
           draws=N_DRAWS, burn=N_BURN, seed=0, ref=None, progress=False):
    rng = np.random.default_rng(seed)
    n = y.size
    a = np.zeros(n_a)
    d = np.zeros(n_c)
    beta = 0.0
    mu = float(y.mean())
    sig2 = max(float(y.var()), 1e-8)

    cnt_a = np.bincount(ai, minlength=n_a).astype(np.float64)
    cnt_c = np.bincount(ci, minlength=n_c).astype(np.float64)
    xx = float(x @ x) if n else 1.0

    keep = {"d": np.empty((draws, n_c)), "a_sd": np.empty(draws),
            "beta": np.empty(draws), "sigma": np.empty(draws),
            "mu": np.empty(draws)}
    for it in range(draws + burn):
        # --- athletes, all at once -------------------------------------
        r = y - mu - beta * x - d[ci]
        s = np.bincount(ai, weights=r, minlength=n_a)
        prec = cnt_a / sig2 + 1.0 / (sa * sa)
        a = s / sig2 / prec + rng.normal(size=n_a) / np.sqrt(prec)
        # --- courses, all at once --------------------------------------
        r = y - mu - beta * x - a[ai]
        s = np.bincount(ci, weights=r, minlength=n_c)
        prec = cnt_c / sig2 + 1.0 / (sd * sd)
        d = s / sig2 / prec + rng.normal(size=n_c) / np.sqrt(prec)
        # --- the gauge: one named course is zero -----------------------
        if ref is not None:
            shift = d[ref]
            d = d - shift
            a = a + shift
        # --- the season slope, and the intercept ----------------------
        r = y - mu - a[ai] - d[ci]
        prec_b = xx / sig2 + 1.0
        beta = float(r @ x) / sig2 / prec_b + rng.normal() / math.sqrt(prec_b)
        r = y - beta * x - a[ai] - d[ci]
        prec_m = n / sig2 + 1e-6
        mu = float(r.sum()) / sig2 / prec_m + rng.normal() / math.sqrt(prec_m)
        # --- the noise -------------------------------------------------
        resid = y - mu - beta * x - a[ai] - d[ci]
        shape = NOISE_A + 0.5 * n
        rate = NOISE_B + 0.5 * float(resid @ resid)
        sig2 = float(rate / rng.gamma(shape))
        if it >= burn:
            k = it - burn
            keep["d"][k] = d
            keep["a_sd"][k] = float(a.std())
            keep["beta"][k] = beta
            keep["sigma"][k] = math.sqrt(sig2)
            keep["mu"][k] = mu
        if progress and (it + 1) % 250 == 0:
            print(f"    draw {it + 1}/{draws + burn}  sigma {math.sqrt(sig2):.5f}",
                  flush=True)
    return keep


# _summarise
# Purpose:   Posterior mean, sd and a central interval per course, in PERCENT
#            of the reference course.
# Output:    a list of dicts, one per course with rows.
def _summarise(keep, course_names, counts, lo=2.5, hi=97.5):
    draws = keep["d"]
    out = []
    for c in range(draws.shape[1]):
        if counts[c] <= 0:
            continue
        col = draws[:, c]
        out.append(dict(
            course=course_names[c], n=int(counts[c]),
            mean=100.0 * (math.exp(float(col.mean())) - 1.0),
            sd=100.0 * float(col.std()),
            lo=100.0 * (math.exp(float(np.percentile(col, lo))) - 1.0),
            hi=100.0 * (math.exp(float(np.percentile(col, hi))) - 1.0)))
    return out


# differs
# Purpose:   The question this script exists to answer: do two courses differ?
# Arguments: keep; i, j -- course indices.
# Output:    dict(diff, lo, hi, p_positive).
# ★ ON THE DRAWS, NOT ON THE SUMMARIES. The difference of two posterior means
#   tells you nothing about whether they differ; the posterior OF THE DIFFERENCE
#   does, and it is available only because the draws are kept paired. Two
#   courses sharing athletes have correlated errors, so the interval on their
#   difference is much tighter than the two marginals suggest -- which is
#   exactly the Foot Locker / NXN case.
def differs(keep, i, j):
    col = keep["d"][:, i] - keep["d"][:, j]
    return dict(diff=100.0 * (math.exp(float(col.mean())) - 1.0),
                lo=100.0 * (math.exp(float(np.percentile(col, 2.5))) - 1.0),
                hi=100.0 * (math.exp(float(np.percentile(col, 97.5))) - 1.0),
                p_positive=float((col > 0).mean()))


def _selftest(seed=0, n_ath=900, n_course=45, races=6):
    """Recover known difficulties from synthetic data, and check COVERAGE.

    ★ A SAMPLER THAT RETURNS PLAUSIBLE NUMBERS IS NOT VALIDATED. What has to
      hold is that the intervals are honest: a 95% interval must contain the
      truth about 95% of the time. That is the property the whole point of this
      script -- "do these two differ?" -- rests on, and it is checkable only
      against a world whose answer is known.
    """
    rng = np.random.default_rng(seed)
    true_d = rng.normal(0, 0.05, n_course)
    true_d[0] = 0.0                     # the reference
    true_a = rng.normal(0, 0.08, n_ath)
    sigma = 0.03
    ai, ci, y, x = [], [], [], []
    for i in range(n_ath):
        for c in rng.choice(n_course, size=races, replace=False):
            doy = rng.uniform(-1, 1)
            ai.append(i); ci.append(int(c)); x.append(doy)
            y.append(6.5 + 0.01 * doy + true_a[i] + true_d[c]
                     + rng.normal(0, sigma))
    ai = np.asarray(ai); ci = np.asarray(ci)
    y = np.asarray(y); x = np.asarray(x)
    print(f"  synthetic: {y.size:,} rows, {n_ath} athlete-seasons, "
          f"{n_course} courses, true noise sd {sigma}")
    keep = _gibbs(y, ai, ci, x, n_ath, n_course, ref=0, seed=seed)
    post = keep["d"]
    est = post.mean(axis=0)
    err = est - true_d
    print(f"  recovered sigma  {keep['sigma'].mean():.5f}  (true {sigma})")
    print(f"  difficulty error: rms {err.std():.5f}, max |err| "
          f"{np.abs(err).max():.5f}  (in log units)")
    # ! THE REFERENCE IS EXCLUDED FROM COVERAGE. It is pinned to exactly 0.0
    #   and is not an estimate, so counting it as covered inflates the number
    #   by one free hit every run.
    inside = 0
    for c in range(1, n_course):
        lo, hi = np.percentile(post[:, c], [2.5, 97.5])
        inside += int(lo <= true_d[c] <= hi)
    cover = inside / (n_course - 1)
    print(f"  95% interval coverage: {inside}/{n_course - 1} = {cover:.0%}"
          f"   (one seed; see the note below)")
    # ★ MEASURED ACROSS 8 SEEDS: 346/352 = 98.3%, z = +2.8 against nominal 95%.
    #   So the intervals run CONSERVATIVE -- a little too wide -- and the reason
    #   is Slaney's course prior: sigma 1.0 in log units against a real course
    #   spread nearer 0.05, so the prior barely shrinks and the posterior
    #   inherits its width. That is the safe direction for the question this
    #   script exists to answer: it will not call two courses different when
    #   they are not. A single seed varies a lot (seed 0 gives 89%), which is
    #   why the gate below is a wide band and not a point.
    ok = (abs(keep["sigma"].mean() - sigma) < 0.004
          and err.std() < 0.01 and 0.85 <= cover <= 1.0)
    # and the question the script exists for
    i, j = 1, 2
    v = differs(keep, i, j)
    truth = 100.0 * (math.exp(true_d[i] - true_d[j]) - 1.0)
    print(f"  differs(1,2): {v['diff']:+.2f}% [{v['lo']:+.2f}, {v['hi']:+.2f}]"
          f"  truth {truth:+.2f}%  -> "
          + ("interval contains the truth" if v["lo"] <= truth <= v["hi"]
             else "MISSES THE TRUTH"))
    ok = ok and v["lo"] <= truth <= v["hi"]
    print("  SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="Slaney's model with posteriors, on a subsample")
    ap.add_argument("--selftest", action="store_true",
                    help="recover known difficulties from synthetic data and "
                         "check interval coverage; touches no database")
    ap.add_argument("--pack", default=os.path.join(_ROOT, "engine", "data",
                                                  "pack.npz"))
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--pool", default="hs_m",
                    help="bare pool name, as the pack's athlete_keys spell it")
    ap.add_argument("--max-rows", type=int, default=150_000,
                    help="cap the subsample; the sampler is O(rows) per draw")
    ap.add_argument("--min-races", type=int, default=3,
                    help="drop courses with fewer race days than this")
    ap.add_argument("--reference", default=None,
                    help="substring of the course key pinned to 0.0 (Slaney "
                         "uses Crystal Springs). Without it the mean is pinned "
                         "instead, which is OUR gauge, not his.")
    ap.add_argument("--must-include", action="append", default=[],
                    help="substring of a course key that must survive "
                         "subsampling; repeatable. Use it to keep the courses "
                         "under dispute in the sample.")
    ap.add_argument("--draws", type=int, default=N_DRAWS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()

    if args.selftest:
        print("\n=== selftest: can it recover a known world? ===")
        return _selftest(seed=args.seed)

    if not os.path.exists(args.pack):
        print(f"\n  no pack at {args.pack}\n"
              f"  This reads the solve's own pack, so it runs where the "
              f"pipeline runs.\n"
              f"  Build one:  python engine/speed_ratings.py --sport merged "
              f"--cache --pack-only\n"
              f"  Or try --selftest first, which needs nothing.")
        return 1

    import bracket as bk
    cols, _npz = bk.loadInputs(args.pack, None)
    keys = [str(k) for k in cols["course_keys"]]
    a_keys = cols["athlete_keys"]
    sport = np.asarray(cols["sport"])
    course = np.asarray(cols["course"]).astype(np.int64)
    ath = np.asarray(cols["athlete"]).astype(np.int64)
    year = np.asarray(cols["year"]).astype(np.int64)
    doy = np.asarray(cols["doy"]).astype(np.float64)
    norm = np.asarray(cols["norm"], dtype=np.float64)

    want_sport = 0 if args.sport == "XC" else 1
    pool_of_ath = np.array([str(k[1]).split("|", 1)[0] if k is not None
                            and len(k) > 1 else "" for k in a_keys])
    m = (sport == want_sport) & (course >= 0) & (norm > 0)
    m &= (pool_of_ath[ath] == args.pool)
    print(f"\n  {int(m.sum()):,} rows in {args.pool}|{args.sport}")
    if not m.any():
        print("  nothing to fit")
        return 1

    # ! SUBSAMPLE BY ATHLETE-SEASON, NOT BY ROW. Dropping random rows thins
    #   every athlete toward one race, which is the regime where a two-way
    #   model is least identified. Keeping whole athlete-seasons keeps the
    #   within-athlete comparisons that carry all the information.
    rng = np.random.default_rng(args.seed)
    seas = ath * 10_000 + year
    keep_rows = np.flatnonzero(m)
    uniq = np.unique(seas[keep_rows])
    if keep_rows.size > args.max_rows:
        frac = args.max_rows / keep_rows.size
        chosen = set(rng.choice(uniq, size=max(int(len(uniq) * frac), 1),
                                replace=False).tolist())
        forced = np.zeros(0, dtype=np.int64)
        if args.must_include:
            low = [k.lower() for k in keys]
            want_c = {i for i, k in enumerate(low)
                      if any(p.lower() in k for p in args.must_include)}
            forced = keep_rows[np.isin(course[keep_rows], list(want_c))]
            chosen |= set(seas[forced].tolist())
            print(f"  forced in {len(want_c)} course(s) named by "
                  f"--must-include, {forced.size:,} rows")
        keep_rows = keep_rows[np.isin(seas[keep_rows], list(chosen))]
        print(f"  subsampled to {keep_rows.size:,} rows "
              f"({len(chosen):,} athlete-seasons)")

    # drop thin courses, then recode
    c_sel = course[keep_rows]
    cnt = np.bincount(c_sel, minlength=len(keys))
    ok_c = cnt >= args.min_races
    keep_rows = keep_rows[ok_c[c_sel]]
    if keep_rows.size == 0:
        print(f"  every course had fewer than {args.min_races} rows")
        return 1
    uc, ci = np.unique(course[keep_rows], return_inverse=True)
    ua, ai = np.unique(seas[keep_rows], return_inverse=True)
    y = np.log(norm[keep_rows])
    x = doy[keep_rows] - doy[keep_rows].mean()
    names = [keys[i] for i in uc]

    ref = None
    if args.reference:
        hits = [i for i, k in enumerate(names)
                if args.reference.lower() in k.lower()]
        if not hits:
            print(f"  ⚠ --reference {args.reference!r} matched no course in the "
                  f"sample; falling back to the MEAN gauge, which is ours, not "
                  f"Slaney's")
        else:
            ref = hits[0]
            print(f"  reference (pinned to 0.0): {names[ref]}")
    print(f"  fitting {y.size:,} rows, {ua.size:,} athlete-seasons, "
          f"{uc.size:,} courses, {args.draws} draws")

    keep = _gibbs(y, ai, ci, x, ua.size, uc.size, draws=args.draws,
                  seed=args.seed, ref=ref, progress=True)
    if ref is None:
        keep["d"] -= keep["d"].mean(axis=1, keepdims=True)

    print(f"\n  posterior noise sd {keep['sigma'].mean():.5f} "
          f"(a race-day + measurement spread, in log time)")
    print(f"  posterior spread of athlete-season levels "
          f"{keep['a_sd'].mean():.4f}")
    rows = _summarise(keep, names, np.bincount(ci, minlength=uc.size))
    rows.sort(key=lambda r: -r["mean"])
    print(f"\n  === difficulty, with a 95% credible interval ===")
    print(f"  {'mean':>8} {'sd':>7} {'95% interval':>20} {'rows':>8}  course")
    print(f"  {'-' * 8} {'-' * 7} {'-' * 20} {'-' * 8}  {'-' * 44}")
    show = rows if args.show <= 0 else (rows[:args.show // 2]
                                       + rows[-(args.show // 2):])
    for r in show:
        print(f"  {r['mean']:>+7.2f}% {r['sd']:>6.2f}% "
              f"[{r['lo']:>+7.2f}, {r['hi']:>+7.2f}] {r['n']:>8,}  "
              f"{r['course'][:44]}")

    if len(args.must_include) >= 2:
        print(f"\n  === do the named courses differ? (posterior OF THE "
              f"DIFFERENCE) ===")
        idx = {}
        for pat in args.must_include:
            for i, k in enumerate(names):
                if pat.lower() in k.lower():
                    idx.setdefault(pat, i)
        pats = [p for p in args.must_include if p in idx]
        for u in range(len(pats)):
            for v in range(u + 1, len(pats)):
                i, j = idx[pats[u]], idx[pats[v]]
                g = differs(keep, i, j)
                verdict = ("DIFFERENT" if (g["lo"] > 0 or g["hi"] < 0)
                           else "not distinguishable")
                print(f"    {names[i][:28]:<28} vs {names[j][:28]:<28}")
                print(f"      {g['diff']:+.2f}% [{g['lo']:+.2f}, "
                      f"{g['hi']:+.2f}]  P(first harder) = "
                      f"{g['p_positive']:.1%}  -> {verdict}")
    print(f"\n  ! these intervals are the SUBSAMPLE's, and they are honest "
          f"about sampling\n    noise only. A term neither model has -- "
          f"altitude, championship taper --\n    biases both and shows up in "
          f"neither interval.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
