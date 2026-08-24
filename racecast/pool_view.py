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

import sys
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

    # ! FAILURES ARE LOUD AND CACHED. A factor that cannot be built hides the
    #   toggle with no other symptom, so say why ONCE on the server console
    #   instead of failing silently -- and cache the None, because
    #   conversions.pool_mean has already cached whatever broke: retrying per
    #   page load would re-pay nothing and re-learn nothing until a restart.
    why = None
    try:
        own = pool_mean(pool)
        hs = pool_mean("hs_" + suffix)
    except Exception as exc:             # noqa: BLE001 -- a view, not a page
        own = hs = None
        why = f"pool_mean raised {type(exc).__name__}: {exc}"

    factor = None
    if why is None:
        if not own or not hs:
            why = (f"pool_mean returned {pool}={own!r}, hs_{suffix}={hs!r} "
                   f"(recovery found no rated rows and the legacy table has "
                   f"no entry?)")
        else:
            factor = float(hs) / float(own)
            if not (_FACTOR_LO <= factor <= _FACTOR_HI):
                why = (f"factor {factor:.3f} outside the "
                       f"{_FACTOR_LO}-{_FACTOR_HI} sanity rail "
                       f"({pool}={own:.1f}, hs_{suffix}={hs:.1f})")
                factor = None

    if why is not None:
        print(f"pool_view: no HS factor for {pool} -- {why}", flush=True)
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


# ------------------------------------------------------------------ #
#  DIAGNOSTIC CLI -- "why is there no toggle on this athlete?"
# ------------------------------------------------------------------ #
#
#     python racecast\pool_view.py 12345678      # a person_id
#     python racecast\pool_view.py               # just the factor table
#
# Run from the PROJECT ROOT. Prints every step the page takes silently:
# the pool means as recovered, the factor (or the reason there is none),
# the athlete's pools per season, and the final has-toggle verdict. The
# fast-explain rule applied here: a feature that can hide itself must be
# able to say why.
def _diag(person_id=None):
    sys.path.insert(0, "scripts")
    from database import getConn
    import psycopg2.extras

    print("\n  -- factors ------------------------------------------------")
    for pool in ("ms_m", "ms_f", "hs_m", "hs_f",
                 "college_m", "college_f", "pro_m", "pro_f"):
        try:
            mean = pool_mean(pool)
        except Exception as exc:          # noqa: BLE001
            print(f"  {pool:<12} pool_mean RAISED {type(exc).__name__}: {exc}")
            continue
        factor = hsFactor(pool)
        mean_s = f"{mean:.1f}" if mean else str(mean)
        factor_s = f"x{factor:.4f}" if factor else "NO FACTOR (see line above)"
        print(f"  {pool:<12} mean {mean_s:>10}   {factor_s}")

    if person_id is None:
        print()
        return

    with getConn() as conn:
        with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = fetchPoolRows(cur, person_id)

    print(f"\n  -- ranking_results pools for person {person_id} -----------")
    if not rows:
        print("  NONE. Either the person_id is wrong, ranking_results was "
              "never built,\n  or every season was dropped (unresolved "
              "grade). Without pool rows the\n  page cannot scale anything "
              "and the toggle stays hidden.")
        print()
        return

    seen = {}
    for r in rows:
        key = (r["sport"], int(r["year"]), r["pool"])
        seen[key] = seen.get(key, 0) + 1
    for (sport, year, pool), n in sorted(seen.items()):
        factor = hsFactor(pool)
        note = (f"x{factor:.4f}" if factor is not None
                else "NO FACTOR -> own-scale passthrough")
        print(f"  {sport} {year}  {pool or '(none)':<12} {n:>4} rows   {note}")

    pools = {p for (_s, _y, p) in seen if p}
    movable = [p for p in pools
               if (f := hsFactor(p)) is not None and abs(f - 1.0) > 1e-9]
    print(f"\n  verdict: toggle {'SHOWS' if movable else 'HIDDEN'} -- "
          f"{len(pools)} pool(s), "
          f"{len(movable)} with a factor that moves anything")
    if not movable and len(pools) > 1:
        print("  (multiple pools but no usable factor: the factor lines "
              "above say which\n  recovery failed -- likely a mid-pipeline "
              "database or an empty sample)")
    print()


if __name__ == "__main__":
    _diag(int(sys.argv[1]) if len(sys.argv) > 1 else None)
