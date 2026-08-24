"""
pool_view.py -- the HS-equivalent rating view for the athlete page.

★ WHY THIS VIEW EXISTS. Ratings are POOL-RELATIVE: 100 sits at the mean of
  the athlete's own pool (see pool_ceiling.py). That is the right default --
  a rating measures distance above your own peers -- but it makes a career
  graph LIE at every pool boundary: a runner who graduates from hs_m into
  college_m drops overnight without getting any slower, because the
  yardstick changed, not the athlete. This module re-expresses every rating
  on the SAME-GENDER HS pool's yardstick, so the graph and the tables show
  one continuous career.

★ WHERE THE POOL SCALE ACTUALLY LIVES -- MEASURED, NOT ASSUMED. The first
  version of this file scaled by pool_mean(hs)/pool_mean(pool), reasoning
  from `rating = 100 * pool_mean * (1+difficulty) / normalized_time`. The
  diagnostic below then showed every pool recovering the SAME mean per
  gender (~1227.5 men / ~1466 women on the 2026-08 corpus): the rating
  constant is per-GENDER, and the pool-relative scaling is inside
  normalized_time -- concretely in the per-pool DISTANCE SPLINE level that
  _normalizationFactorCached evaluates (era keys off gender only, geometry
  never sees the pool). So the correct, exact conversion is the ratio of
  the engine's own normalization factors for the same race context:

      nt        = raw * F(distance, pool, ...)
      rating    = C_gender * (1 + difficulty) / nt
   => hs_rating = rating * F(distance, own pool) / F(distance, hs pool)

  raw time, weather and difficulty cancel -- same race on both sides. The
  factor is per (pool, sport, distance), not one constant per pool: the
  spline levels can differ by distance, and pretending otherwise would be
  the flat-1.06 mistake all over again.

★ SAME GENDER, ALWAYS. 'college_f' maps onto 'hs_f', never 'hs_m': the view
  answers "what would this rating read among high schoolers like them", and
  the gender is part of the pool's identity, not part of the level
  difference the view exists to remove. A pool without a gender suffix
  (unknown_gender) gets no factor and keeps its own-scale number.

⚠ THE VIEW CHANGES NUMBERS ONLY, NEVER VERDICTS. PR/SR flags, board
  eligibility and record stars are all computed on the own-pool scale and
  stay put when the reader toggles -- a view must not re-adjudicate records.
"""

import sys
from collections import Counter
from statistics import median

# The engine's forward machinery via the conversions wrapper -- reused,
# never reimplemented, so a spline refit changes this view automatically.
from conversions import _forward_factor, pool_mean
from athlete_bests import _tfEvent

# factor cache: (pool, sport, distance) -> multiplier onto the same-gender
# HS scale, or None for a context that cannot be scaled. Process-lifetime;
# failures ARE cached -- the spline artifacts do not change under a running
# process, so retrying per page load would re-learn nothing.
_FACTOR_CACHE = {}

# ⚠ SANITY RAIL ON THE FACTOR ITSELF. Real level gaps are on the order of
#   10-30% (college vs hs, ms vs hs); a ratio outside this band means a
#   spline evaluated somewhere it has no business (a 100m dash through the
#   XC spline) or a broken artifact -- refuse it and show own-scale.
_FACTOR_LO, _FACTOR_HI = 0.5, 2.0

# A factor within half a percent of 1.0 moves nothing a reader can see;
# a page whose factors are all inside this band hides the toggle.
_MOVES = 0.005

_FAILED = set()          # (pool, sport, dq) already complained about


def hsFactor(pool, sport, distance_m):
    """Multiplier from `pool`'s scale onto the same-gender HS scale, for one
    race context (sport + distance in metres).

    Returns None when no factor can be computed (no pool, no gender suffix,
    no distance, engine refused, sanity rail) -- callers must treat None as
    "leave the rating on its own scale", never as 1.0-with-a-shrug.
    """
    if not pool or not distance_m:
        return None
    if pool in ("hs_m", "hs_f"):
        return 1.0
    suffix = pool.rsplit("_", 1)[-1]
    if suffix not in ("m", "f"):
        return None                      # unknown_gender etc: no HS twin

    try:
        dq = int(round(float(distance_m)))
    except (TypeError, ValueError):
        return None
    key = (pool, sport, dq)
    if key in _FACTOR_CACHE:
        return _FACTOR_CACHE[key]

    # ! FAILURES ARE LOUD. A factor that cannot be built hides the toggle
    #   with no other symptom, so say why ONCE on the server console.
    why = None
    factor = None
    try:
        # season/track/event_short deliberately None: era keys off the
        # pool's GENDER (identical on both sides) and geometry never sees
        # the pool, so both cancel in the ratio -- the ratio isolates
        # exactly the per-pool spline level, which is the thing being
        # converted.
        own = _forward_factor(float(dq), pool, None, None, None, sport, None)
        hs = _forward_factor(float(dq), "hs_" + suffix,
                             None, None, None, sport, None)
    except Exception as exc:             # noqa: BLE001 -- a view, not a page
        own = hs = None
        why = f"engine factor raised {type(exc).__name__}: {exc}"

    if why is None:
        if not own or not hs:
            why = f"engine factor returned {pool}={own!r}, hs_{suffix}={hs!r}"
        else:
            factor = float(own) / float(hs)
            if not (_FACTOR_LO <= factor <= _FACTOR_HI):
                why = (f"factor {factor:.3f} outside the "
                       f"{_FACTOR_LO}-{_FACTOR_HI} sanity rail "
                       f"({pool}={own:.4f}, hs_{suffix}={hs:.4f})")
                factor = None

    if why is not None and key not in _FAILED:
        _FAILED.add(key)
        print(f"pool_view: no HS factor for {pool}/{sport}/{dq}m -- {why}",
              flush=True)
    _FACTOR_CACHE[key] = factor
    return factor


def _raceDistance(race):
    """Metres for one race dict, or None. XC's `event` IS the distance (see
    get_races); TF's is event_short, parsed by the same helper the bests
    panel sorts with. Field events have no distance to price."""
    if race.get("is_field"):
        return None
    ev = race.get("event")
    if ev is None:
        return None
    if race.get("sport") == "XC":
        try:
            return float(ev)
        except (TypeError, ValueError):
            return None
    metres, _label = _tfEvent(ev)
    return float(metres) if metres else None


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
               rating moves by more than _MOVES under the HS view. A
               pure-HS career returns False and the template hides the
               switch.

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

        factor = hsFactor(pool, sport, _raceDistance(race))
        rating = race.get("speed_rating")
        if rating is not None and factor is not None:
            race["hs_rating"] = float(rating) * factor
            if abs(factor - 1.0) > _MOVES:
                has_alt = True
        else:
            race["hs_rating"] = None
    return has_alt


def seasonFactor(races, label=None, sport=None):
    """The median hs/own ratio over stamped races, optionally filtered to
    one (season label, sport). For scaling season-level MEANS (the header
    rating from athlete_season), which have no single race context of their
    own. None when nothing matches -- the caller shows own-scale."""
    ratios = []
    for r in races:
        if label is not None and str(r.get("season_label")) != str(label):
            continue
        if sport is not None and r.get("sport") != sport:
            continue
        own = r.get("speed_rating")
        hs = r.get("hs_rating")
        if own and hs:
            ratios.append(float(hs) / float(own))
    return median(ratios) if ratios else None


# ------------------------------------------------------------------ #
#  DIAGNOSTIC CLI -- "why is there no toggle on this athlete?"
# ------------------------------------------------------------------ #
#
#     python racecast\pool_view.py 12345678      # a person_id
#     python racecast\pool_view.py               # just the factor table
#
# Run from the PROJECT ROOT. Prints every step the page takes silently:
# the engine's per-pool normalization factors at reference distances, the
# view factor per pool (or the reason there is none), the recovered rating
# constants (per gender -- the measurement that redirected this module),
# the athlete's pools per season, and the final has-toggle verdict. The
# fast-explain rule applied here: a feature that can hide itself must be
# able to say why.
_REF = (("XC", 5000.0), ("XC", 8000.0), ("TF", 1600.0), ("TF", 5000.0))


def _diag(person_id=None):
    sys.path.insert(0, "scripts")
    from database import getConn
    import psycopg2.extras

    print("\n  -- view factors (own scale -> HS scale) --------------------")
    pools = ("ms_m", "ms_f", "hs_m", "hs_f",
             "college_m", "college_f", "pro_m", "pro_f")
    header = "  " + "pool".ljust(12) + "".join(
        f"{s} {int(d)}m".rjust(12) for s, d in _REF)
    print(header)
    for pool in pools:
        cells = []
        for sport, dist in _REF:
            factor = hsFactor(pool, sport, dist)
            cells.append((f"x{factor:.4f}" if factor is not None
                          else "--").rjust(12))
        print("  " + pool.ljust(12) + "".join(cells))
    print("  (x1.0000 across every non-HS row means the current spline "
          "artifacts carry\n  no per-pool level -- see the constants below "
          "-- and the view has nothing\n  to show; that is an engine-side "
          "fact, not a website bug)")

    print("\n  -- recovered rating constants (per gender if the view can "
          "work) ----")
    for pool in pools:
        try:
            mean = pool_mean(pool)
        except Exception as exc:          # noqa: BLE001
            print(f"  {pool:<12} pool_mean RAISED {type(exc).__name__}: {exc}")
            continue
        mean_s = f"{mean:.1f}" if mean else str(mean)
        print(f"  {pool:<12} {mean_s:>10}")

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
    movable = 0
    for (sport, year, pool), n in sorted(seen.items()):
        dist = 5000.0 if sport == "XC" else 1600.0
        factor = hsFactor(pool, sport, dist)
        if factor is not None and abs(factor - 1.0) > _MOVES:
            movable += 1
            note = f"x{factor:.4f}"
        elif factor is not None:
            note = f"x{factor:.4f} (moves nothing)"
        else:
            note = "NO FACTOR -> own-scale passthrough"
        print(f"  {sport} {year}  {pool or '(none)':<12} {n:>4} rows   {note}")

    print(f"\n  verdict: toggle {'SHOWS' if movable else 'HIDDEN'} -- "
          f"{movable} season(s) whose factor moves anything")
    print()


if __name__ == "__main__":
    _diag(int(sys.argv[1]) if len(sys.argv) > 1 else None)
