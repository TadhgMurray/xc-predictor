# Project: xc-predictor
# File:    scripts/cs_sweep.py
# Purpose: Decide, BEFORE the real fit runs, what the real fit has to show for
#          a single-input critical-speed fallback to be worth offering.
#
# ⚠ EVERY ATHLETE IN HERE IS INVENTED, AND THAT LIMITS WHAT IT MAY CLAIM. The
#   D' spread and the race-day noise are chosen by this file, so reading them
#   back out of it would be circular -- the same mistake as setting a tempo
#   offset to a coach's number and then testing that the offset equals the
#   coach's number. This produces no evidence about the corpus.
#
# ★ WHAT IT LEGITIMATELY PRODUCES IS A THRESHOLD. Sweep across worlds where
#   D' varies more or less between athletes, and find where the one-race
#   fallback stops tracking the two-race fit. That threshold is a rule the
#   REAL fit gets fed into:
#
#       fit_critical_speed.py prints a D' IQR per pool.
#       under ~60 m   the fallback is defensible; offer it
#       over  ~80 m   it is roughly twice the error; ask for a second race
#
#   So the decision is made by their data, and only the decision RULE comes
#   from here.
#
# USAGE
#   python scripts/cs_sweep.py
"""FABRICATED ATHLETES. Nothing here is evidence about the real corpus -- the
D' spread and the noise are chosen by me, so reading them back out would be
circular. What this CAN do is find the threshold: how wide can D' be before a
single-input fallback stops being worth offering? That is a rule the real fit
gets fed into, not a result."""
import random, statistics as st, sys, os
os.chdir("/home/user/xc-predictor")
sys.path[:0] = ["scripts", "engine"]
import fit_critical_speed as F

MILE = 1609.344
# A realistic TF season: what a distance runner actually contests.
MENUS = ([800, 1600, 3200], [1600, 3200, 5000], [800, 1600, 3200, 5000],
         [1500, 3000, 5000], [1600, 3200])


def athlete(cs, dprime, noise, rng):
    menu = rng.choice(MENUS)
    out = []
    for d in menu:
        t = (d - dprime) / cs
        if t <= 0:
            return None
        out.append((float(d), t * rng.gauss(1.0, noise)))
    return out


def world(n, dp_med, dp_iqr, noise, seed=1):
    """dp_iqr is the interquartile width of D' in metres."""
    rng = random.Random(seed)
    # a normal whose IQR is dp_iqr has sd = iqr / 1.349
    sd = dp_iqr / 1.349
    people = []
    for _ in range(n):
        cs = rng.uniform(3.8, 6.2)
        dp = max(20.0, rng.gauss(dp_med, sd))
        a = athlete(cs, dp, noise, rng)
        if a and F._spread(a) >= F.MIN_SPREAD:
            people.append(a)
    return people, dp_med


def evaluate(people, pop_dp):
    fit_err, pop_err = [], []
    for races in people:
        for i in range(len(races)):
            train = races[:i] + races[i + 1:]
            d_out, t_out = races[i]
            if len(train) < 2 or F._spread(train) < F.MIN_SPREAD:
                continue
            got = F.fitCS(train)
            if got and got[0] > 0:
                p = (d_out - got[1]) / got[0]
                if p > 0:
                    fit_err.append(abs(p - t_out) / t_out)
            d1, t1 = train[0]
            cs1 = (d1 - pop_dp) / t1
            if cs1 > 0:
                p2 = (d_out - pop_dp) / cs1
                if p2 > 0:
                    pop_err.append(abs(p2 - t_out) / t_out)
    med = lambda e: sorted(e)[len(e) // 2] if e else float("nan")
    return med(fit_err), med(pop_err)


print(__doc__)
print("\n  RACE-DAY NOISE HELD AT 1.5% (the figure build_team_season quotes"
      "\n  for a single race). Sweeping how varied D' is between athletes.\n")
DP = "D' IQR"
print(f"  {DP:>8}{'two-race fit':>15}{'one-race fallback':>20}"
      f"{'ratio':>9}   verdict")
for iqr in (20, 40, 60, 80, 120, 160, 220):
    ppl, dpm = world(1500, 220.0, iqr, 0.015)
    a, b = evaluate(ppl, dpm)
    verdict = ("fallback fine" if b / a < 1.3 else
               "fallback usable, say so" if b / a < 1.8 else
               "ASK for a second race")
    print(f"  {iqr:>6} m{a:>14.1%}{b:>20.1%}{b / a:>9.1f}x   {verdict}")

print("\n  AND HOW MUCH RACE-DAY NOISE THE FIT ITSELF SURVIVES"
      "\n  (D' IQR held at 80 m)\n")
print(f"  {'noise':>8}{'two-race fit':>15}{'one-race fallback':>20}{'ratio':>9}")
for noise in (0.005, 0.010, 0.015, 0.025, 0.040):
    ppl, dpm = world(1500, 220.0, 80, noise)
    a, b = evaluate(ppl, dpm)
    print(f"  {noise:>7.1%}{a:>14.1%}{b:>20.1%}{b / a:>9.1f}x")

print("\n  DOES A THIRD RACE EARN ITS KEEP? (D' IQR 80 m, noise 1.5%)\n")
rng = random.Random(4)
# ! STARTS AT THREE. Two races leave one to fit and one to hold out, and a
#   one-race fit has no slope -- MIN_SPREAD and the len<2 guard drop every
#   case, which is why k=2 produced an empty list rather than a number.
for k in (3, 4, 5):
    errs = []
    for _ in range(1500):
        cs = rng.uniform(3.8, 6.2); dp = max(20.0, rng.gauss(220, 80 / 1.349))
        ds = [800, 1600, 3200, 5000, 10000][:k]
        races = [(float(d), (d - dp) / cs * rng.gauss(1, .015)) for d in ds]
        held, train = races[-1], races[:-1]
        if len(train) < 2 or F._spread(train) < F.MIN_SPREAD:
            continue
        got = F.fitCS(train)
        if got and got[0] > 0:
            p = (held[0] - got[1]) / got[0]
            if p > 0:
                errs.append(abs(p - held[1]) / held[1])
    if not errs:
        print(f"  {k - 1} races to fit: no case survives the guards")
        continue
    print(f"  {k - 1} races to fit -> predicting race {k}: "
          f"median {sorted(errs)[len(errs)//2]:.1%}   n={len(errs):,}")
