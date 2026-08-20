"""
pool_ceiling.py -- the highest rating a pool can plausibly produce.

★ A RATING FAR OUTSIDE ITS POOL IS A POOLING ERROR, NOT A RECORD. Oregon
  Track Club topped the all-time high school board with a top-five average
  of 180, in seasons where the best real high school squad reached about
  140. Nobody ran a 180 high school season; a professional club landed in
  hs_m because pro_flag had not seen enough of its athletes at a seeded
  professional meet, and the board dutifully published it as the best team
  in history.

⚠ THE CEILINGS RISE AS THE POOL GETS WEAKER, WHICH LOOKS BACKWARDS AND IS
  NOT. Ratings are POOL-RELATIVE: 100 is the mean of that pool, in every
  era. So the number does not measure speed, it measures distance above
  your own peers -- and the thinner the pool, the further the best of it
  stands above the middle. The best 13-year-old in the country is a bigger
  outlier among middle schoolers than the best collegian is among
  collegians, who have already been filtered twice by making a team.

  Which is also why one global rail cannot do this job. build_ranking_results
  has one -- reject anything outside 20..200 -- and it is a FOSSIL rail, there
  to catch a speed_rating of 7528 on a 20.6-second time. 180 sails through it
  in every pool, because 180 is a real number for somebody; it is just not a
  real number for a high schooler.

⚠ THESE ARE STARTING VALUES, NOT MEASUREMENTS, and the difference matters:
  set one too low and the rail deletes the genuinely great seasons this site
  exists to show. Run `python racecast/audit_pool_ceilings.py` against the
  corpus BEFORE trusting them -- it prints each pool's real distribution and
  how many athlete-seasons each candidate ceiling would drop, so the number
  can be chosen from the data instead of from a guess.

! AND THE RAIL DOES NOT ERASE THE ATHLETE. It decides who is eligible for a
  BOARD. The rating stays on their own page, exactly as grade_trust='low'
  seasons are rated but not ranked -- see build_ranking_results.poolOf.
"""

# The ceiling per pool, in rating points. Gender does not change the shape of
# a pool's distribution -- ratings are already centred per pool, and hs_f is
# no more spread than hs_m -- so the two share a number.
POOL_CEILING = {
    "ms_m": 180.0, "ms_f": 180.0,
    "hs_m": 150.0, "hs_f": 150.0,
    "college_m": 130.0, "college_f": 130.0,
}

# ! AN UNLISTED POOL IS NOT SILENTLY ALLOWED THROUGH AT INFINITY. A pool this
#   module has never heard of is a pool nobody chose a ceiling for, so it
#   falls back to the widest school ceiling rather than to no ceiling at all.
#   The alternative -- default to inf -- means adding a pool quietly disables
#   the rail for it, and nothing anywhere would say so.
DEFAULT_CEILING = max(POOL_CEILING.values())


def ceilingFor(pool):
    """The ceiling for a pool name, with or without a sport suffix.

    ! BOTH SPELLINGS, because the corpus carries both. ranking_results stores
      'hs_m' with sport in its own column, while the engine's in-memory pools
      read 'hs_m_XC'. A lookup that only knew one of them would return the
      default for half its callers and the rail would half-work, which is the
      worst of the three outcomes.
    """
    if not pool:
        return DEFAULT_CEILING
    if pool in POOL_CEILING:
        return POOL_CEILING[pool]
    for name, ceiling in POOL_CEILING.items():
        if pool.startswith(name + "_"):
            return ceiling
    return DEFAULT_CEILING


def withinPool(pool, rating):
    """Is this rating plausible for this pool?

    A missing rating is NOT within: a row with nothing to check cannot be
    said to have passed a check.
    """
    if rating is None:
        return False
    try:
        return float(rating) <= ceilingFor(pool)
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ #
#  SELF-CHECK -- `python racecast/pool_ceiling.py`
# ------------------------------------------------------------------ #

def _selfCheck():
    bad = 0

    def check(label, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}"
              + ("" if ok else f"  (want {want!r})"))

    print("the case the module exists for")
    check("a 180 club season is not a high school season",
          withinPool("hs_m", 180.0), False)
    check("the best real high school season still ranks",
          withinPool("hs_m", 140.0), True)
    check("and the same 180 IS plausible in middle school",
          withinPool("ms_m", 180.0), True)

    print("\nthe sport suffix does not change the answer")
    for pool in ("hs_m", "hs_m_XC", "hs_m_TF"):
        check(f"{pool} ceiling", ceilingFor(pool), 150.0)

    print("\nboth genders share a pool's shape")
    check("hs_f matches hs_m", ceilingFor("hs_f"), ceilingFor("hs_m"))
    check("ms_f matches ms_m", ceilingFor("ms_f"), ceilingFor("ms_m"))

    print("\nnothing slips through on a technicality")
    check("an unknown pool gets the widest ceiling, not none",
          ceilingFor("juco_m"), 180.0)
    check("a missing rating does not pass", withinPool("hs_m", None), False)
    check("nor does a non-number", withinPool("hs_m", "fast"), False)
    check("the boundary itself is allowed", withinPool("hs_m", 150.0), True)

    print("\nweaker pools have room for bigger outliers, which is the point")
    check("ms above hs above college",
          ceilingFor("ms_m") > ceilingFor("hs_m") > ceilingFor("college_m"),
          True)

    print("\nall cases pass" if not bad else f"\n{bad} FAILURES")
    return bad


if __name__ == "__main__":
    raise SystemExit(_selfCheck())
