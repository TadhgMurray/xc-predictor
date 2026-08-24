"""
pool_view.py -- the HS-equivalent rating view for the athlete page.

★ WHY THIS VIEW EXISTS. Ratings are POOL-RELATIVE: 100 is the mean of the
  athlete's own pool (see pool_ceiling.py). That is the right default -- a
  rating measures distance above your own peers -- but it makes a career
  graph LIE at every pool boundary: a runner who graduates from hs_m into
  college_m drops ten points overnight without getting any slower, because
  the yardstick changed, not the athlete. This module re-expresses every
  rating on the SAME-GENDER HS pool's yardstick, so the graph and the
  tables show one continuous career.

★ THE CONVERSION IS EXACT, NOT A MODEL. The engine's own formula is

      speed_rating = 100 * pool_mean * (1 + difficulty) / normalized_time

  and everything except pool_mean is a property of the RACE, not the pool.
  So the same performance viewed from the HS pool is

      hs_rating = speed_rating * pool_mean(hs_g) / pool_mean(own pool)

  -- one multiplicative constant per pool, recovered by conversions.pool_mean
  from the engine's own written-down terms. pool_mean is sport-agnostic by
  design ('hs_m' is one pool spanning XC and TF), so the factor is too.

★ SAME GENDER, ALWAYS. 'college_f' maps onto 'hs_f', never 'hs_m': the view
  answers "what would this rating read among high schoolers like them", and
  the gender is part of the pool's identity, not part of the era/level
  difference the view exists to remove. A pool without a gender suffix
  (unknown_gender) gets no factor and keeps its own-scale number.

⚠ THE VIEW CHANGES NUMBERS ONLY, NEVER VERDICTS. PR/SR flags, board
  eligibility and record stars are all computed on the own-pool scale and
  stay put when the reader toggles -- a view must not re-adjudicate records.
"""

from collections import Counter

from conversions import pool_mean

# factor cache: bare pool -> multiplier onto the same-gender HS scale.
# Process-lifetime, same policy as conversions._POOL_MEAN_CACHE (it is a
# ratio of two entries of that cache). Only successes are cached, so a DB
# hiccup during recovery does not pin a None for the process lifetime.
_FACTOR_CACHE = {}

# ⚠ SANITY RAIL ON THE FACTOR ITSELF. The recovery medians over sampled rows;
#   if a pool's sample is somehow poisoned the ratio lands far from 1 and a
#   whole career would render at nonsense scale. Real level gaps are on the
#   order of 10-25% (college vs hs, ms vs hs), so a factor outside this band
#   is a broken recovery, not a discovery -- refuse it and show own-scale.
_FACTOR_LO, _FACTOR_HI = 0.5, 2.0


def hsFactor(pool):
    """Multiplier from `pool`'s scale onto the same-gender HS scale.

    Returns None when no factor can be computed (no pool, no gender suffix,
    recovery failed or landed outside the sanity rail) -- callers must treat
    None as "leave the rating on its own scale", never as 1.0-with-a-shrug:
    the difference between the two is whether the toggle claims comparability.
    """
    if not pool:
        return None
    if pool in _FACTOR_CACHE:
        return _FACTOR_CACHE[pool]

    if pool in ("hs_m", "hs_f"):
        _FACTOR_CACHE[pool] = 1.0
        return 1.0

    suffix = pool.rsplit("_", 1)[-1]
    if suffix not in ("m", "f"):
        return None                      # unknown_gender etc: no HS twin

    try:
        own = pool_mean(pool)
        hs = pool_mean("hs_" + suffix)
    except Exception:                    # noqa: BLE001 -- a view, not a page
        return None
    if not own or not hs:
        return None

    factor = float(hs) / float(own)
    if not (_FACTOR_LO <= factor <= _FACTOR_HI):
        return None
    _FACTOR_CACHE[pool] = factor
    return factor


def fetchPoolRows(cur, person_id):
    """The athlete's (sport, result_id, pool, year) rows from ranking_results.

    Fetched separately from get_races (rather than joined into its UNION)
    because the union is already the page's heaviest query and this is one
    indexed lookup returning a few hundred small rows.

    Returns [] on a database that has never run build_ranking_results, same
    posture as the athlete_season fallback in the route.
    """
    try:
        cur.execute("""
            SELECT sport, result_id, pool, year
            FROM   ranking_results
            WHERE  person_id = %s
        """, (person_id,))
        return cur.fetchall()
    except Exception:                    # noqa: BLE001 -- UndefinedTable et al.
        cur.connection.rollback()
        return []


def stampHsRatings(pool_rows, races):
    """Stamp race["pool"] and race["hs_rating"] onto every race dict.

    Arguments: pool_rows -- from fetchPoolRows; races -- the deduped list,
               AFTER season_label stamping (the fallback needs it).
    Output:    True when the page has anything to toggle -- at least one
               rating actually moves under the HS view. A pure-HS career
               returns False and the template hides the switch.

    ★ EXACT MATCH FIRST, SEASON FALLBACK SECOND. ranking_results drops
      unresolved/untrusted seasons that the engine still rated and the page
      still shows; those races take the athlete's modal pool for the same
      (sport, academic year), so a career does not render half-scaled just
      because one season's grade evidence was thin. A race with no pool from
      either source keeps its own-scale number (hs_rating None).

    ! ranking_results.year IS THE ACADEMIC YEAR; the stamped season_label is
      academic + 1 for TF (display rule). Undo the display rule here, same
      as _attach_season_verdicts does for grade_fix.
    """
    exact = {}
    polls = {}
    for row in pool_rows:
        pool = row["pool"]
        if not pool:
            continue
        exact[(row["sport"], row["result_id"])] = pool
        polls.setdefault((row["sport"], int(row["year"])), Counter())[pool] += 1
    modal = {key: c.most_common(1)[0][0] for key, c in polls.items()}

    has_alt = False
    for race in races:
        sport = race.get("sport")
        pool = exact.get((sport, race.get("result_id")))
        if pool is None:
            try:
                academic = (int(race.get("season_label"))
                            - (1 if sport == "TF" else 0))
                pool = modal.get((sport, academic))
            except (TypeError, ValueError):
                pool = None
        race["pool"] = pool

        factor = hsFactor(pool)
        rating = race.get("speed_rating")
        if rating is not None and factor is not None:
            race["hs_rating"] = float(rating) * factor
            if abs(factor - 1.0) > 1e-9:
                has_alt = True
        else:
            race["hs_rating"] = None
    return has_alt
