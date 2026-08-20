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

★ THESE ARE NOW MEASURED. audit_pool_ceilings was run against the corpus and
  moved three of the six -- see the comment on POOL_CEILING for which and
  why. Rerun it after any change that moves the rating scale (the anchor
  gate, a spline refit, a repooling), because a ceiling is a number about a
  distribution and the distribution will have moved.

! AND THE RAIL DOES NOT ERASE THE ATHLETE. It decides who is eligible for a
  BOARD. The rating stays on their own page, exactly as grade_trust='low'
  seasons are rated but not ranked -- see build_ranking_results.poolOf.
"""

# The ceiling per pool, in rating points.
#
# ⚠ AND THE GENDERS DO NOT SHARE A NUMBER, though the first version of this
#   asserted they did on the reasoning that ratings are already centred per
#   pool. The corpus disagrees: the women's pools are measurably wider at the
#   top. Real programmes reach 150.3 in hs_f against 146.2 in hs_m, and 137.0
#   in college_f against 127.8 in college_m. Whatever the cause -- smaller
#   fields, thinner depth -- the distributions differ, so the lines do.
# ★ MEASURED AGAINST THE CORPUS, NOT GUESSED. audit_pool_ceilings named the
#   athlete-seasons each candidate would drop, and the names changed three of
#   the six numbers. What follows is what the report said, per pool.
#
# ⚠ college_f 130 WAS CUTTING RECORDS. Its 154 drops were BYU 137.0, Alabama
#   134.6, Notre Dame 131.0, Stanford 130.7, New Mexico 132.6, NC State
#   132.0, Oregon 131.3, Colorado 130.7 -- real programmes with real
#   athletes, exactly the seasons this site exists to show. 140 drops 2.
#   (college_m needs no such move: its real programmes top out near 128, and
#   130 catches only the youth clubs at 143-166.)
#
# ⚠ hs_f 150 SITS ON TOP OF A REAL SEASON. Lewisville Flower Mound reaches
#   150.3 and Cherry Creek 149.7 -- so the line ran straight through the best
#   girls' programme in the country. 160 clears them and still drops 81.
#
# ⚠ ms 180 DROPPED NOTHING AT ALL -- the whole pool tops out at 179.1, so the
#   rail was decorative. The seasons that should go are Iowa Flyers 179.1,
#   Track Houston Youth 174.6 and Runnin on Faith 171.1: track clubs, not
#   middle schools. The real middle schoolers below them (A.I. Root 165.0,
#   Herbert Hoover 162.8, Harrisburg 158.5) are schools. 170 splits them.
#
# ! AND THE PATTERN IN EVERY DROP LIST IS A CLUB, NOT A FAST CHILD. Break Away
#   Track, Kansas Flyers, Capitol Track Club, Georgia Stars Elite. A ceiling
#   is a blunt instrument for that -- it catches them only because they are
#   also mis-pooled, and it will keep needing to be retuned. The durable fix
#   is upstream, in what counts as a school.
POOL_CEILING = {
    "ms_m": 170.0, "ms_f": 170.0,
    "hs_m": 150.0, "hs_f": 160.0,
    "college_m": 130.0, "college_f": 140.0,
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
    check("Oregon Track Club's 189.7 hs_m season goes",
          withinPool("hs_m", 189.7), False)
    check("Mead's 146.2 -- the best real hs_m programme -- stays",
          withinPool("hs_m", 146.2), True)
    check("Lewisville Flower Mound's 150.3 stays, on the hs_f line",
          withinPool("hs_f", 150.3), True)
    check("BYU's 137.0 college_f season stays", withinPool("college_f", 137.0),
          True)
    check("Iowa Flyers' 179.1 ms_m season goes",
          withinPool("ms_m", 179.1), False)
    check("A.I. Root's 165.0 -- a real middle school -- stays",
          withinPool("ms_f", 165.0), True)

    print("\nthe sport suffix does not change the answer")
    for pool in ("hs_m", "hs_m_XC", "hs_m_TF"):
        check(f"{pool} ceiling", ceilingFor(pool), 150.0)

    print("\nthe women's pools are wider at the top, and measured so")
    check("hs_f sits above hs_m", ceilingFor("hs_f") > ceilingFor("hs_m"), True)
    check("college_f above college_m",
          ceilingFor("college_f") > ceilingFor("college_m"), True)
    check("ms shares one line", ceilingFor("ms_f"), ceilingFor("ms_m"))

    print("\nnothing slips through on a technicality")
    check("an unknown pool gets the widest ceiling, not none",
          ceilingFor("juco_m"), max(POOL_CEILING.values()))
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
