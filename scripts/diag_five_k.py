#!/usr/bin/env python3
"""
diag_five_k.py -- why a rating's "5K on a track" disagrees with the rating
breakdown's own start. READ-ONLY.

★ WHY (owner, 2026-10-28: Robert Jazwinski III, 14:35.82 on a +9.6% course,
  rated 144.3 and quoted "14:20 on a track"). The breakdown
  (explain_rating) says 14:37.0 over 5000 m is "a 135.0 on an average
  outdoor track", which puts a 144.3 near 13:40 -- yet ratingClock says
  14:20. Both are 100 * pool_mean * exp(tilt * shift) / (time * factor) on
  paper, so one of the inputs differs. This prints each, both ways.

    /srv/venv/bin/python scripts/diag_five_k.py [rating] [time_s] [pool]
    /srv/venv/bin/python scripts/diag_five_k.py 144.3 877 hs_m
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def main(rating=144.3, tsec=877.0, pool="hs_m", sport="XC", dist=5000.0):
    import conversions as cv
    import normalize_distance as nd
    sc = cv.engineScale(pool, sport)
    pm_conv = cv.pool_mean(pool, sport)
    print(f"engineScale {pool}/{sport}: pm={sc[0] if sc else None} median={sc[1] if sc else None} "
          f"shift={sc[2] if sc else None}")
    print(f"pool_mean (the conversions' pm): {pm_conv}")
    print(f"abilityMode: {nd.abilityMode()}")
    # the breakdown's start: time -> rating on a track
    f0 = nd.factorForTime(tsec, dist, pool, None, None, None, sport, None)
    tilt_t = cv._tilt(rating, pool) if sc else 1.0
    shift = sc[2] if sc else 0.0
    start = 100.0 * pm_conv / (tsec * f0) * math.exp(tilt_t * shift)
    print(f"\nbreakdown leg: {tsec:.1f}s x factorForTime {f0:.5f} -> a {start:.2f} on a track")
    # the column: rating -> time on a track
    norm = cv._norm_from_rating(rating, pool, 0.0, sport)
    f_inv = cv._forward_factor(dist, pool, None, None, None, sport, None,
                               norm=norm if nd.abilityMode() else None)
    eff = cv.venueEffect(pool, sport, 100.0 * sc[0] / norm, 0.0, dist) if sc else 0.0
    t_col = norm * math.exp(eff) / f_inv if (norm and f_inv) else None
    print(f"column leg:    {rating} -> norm {norm:.2f}, factor {f_inv:.5f}, effect {eff:+.5f} "
          f"(tilt {cv._tilt(100.0 * sc[0] / norm, pool) if sc else 1:.3f} x shift {shift:+.5f}, "
          f"dist offset {cv.distance_offset(pool, sport, dist, rating):+.5f}, "
          f"sport gain {cv.sport_gain(pool, sport, rating):+.5f})")
    print(f"               -> {t_col:.1f}s" if t_col else "               -> none")
    print(f"ratingSeconds (the column itself): {cv.ratingSeconds(rating, pool, sport, dist, track=True)}")
    # the breakdown's own scale, carried to this rating
    if start:
        print(f"\nby the breakdown's scale a {rating} is {tsec * start / rating:.1f}s "
              f"(rating ~ 1/time); the column says {t_col:.1f}s" if t_col else "")
        # and the same rating through factorForTime, solved for the time
        lo, hi = 600.0, 1500.0
        for _ in range(60):
            mid = (lo + hi) / 2
            r_mid = 100.0 * pm_conv / (mid * nd.factorForTime(mid, dist, pool, None, None, None,
                                                              sport, None)) * math.exp(tilt_t * shift)
            lo, hi = (mid, hi) if r_mid > rating else (lo, mid)
        print(f"the breakdown's leg solved for {rating}: {lo:.1f}s")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(float(a[0]) if a else 144.3, float(a[1]) if len(a) > 1 else 877.0,
         a[2] if len(a) > 2 else "hs_m")
