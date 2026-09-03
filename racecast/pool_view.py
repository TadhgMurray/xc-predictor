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

★ THE FACTOR IS TWO MEASURED RATIOS, AND IT TOOK THREE ATTEMPTS TO LEARN
  WHY. The record, so nobody relearns it:

  1. pool_mean(hs)/pool_mean(pool) from conversions.pool_mean -> every
     factor came back x1.0001. NOT because the constant is per-gender:
     conversions' sampler scopes rows by athlete_ratings MEMBERSHIP
     (`ath.athlete_id = r.person_id`), so a college athlete's sample is
     mostly their own HS-era rows and every pool's median lands on the
     corpus-dominant HS mode (~1227.5 men / ~1466 women). A polluted
     sample, not a discovery. (Harmless inside the conversions tool
     itself -- there source and target share one pool, so its pool_mean
     always cancels.)
  2. The spline-factor ratio F(d, pool)/F(d, hs) alone -> college_m
     x1.63, pro x1.00: OVERcorrects, because the per-pool spline LEVEL is
     arbitrary (the fitter pins ratios between distances, not the level;
     gated pools inherit the sport global, which is why pro and
     college_f|XC looked "reasonable"). The level is not the talent gap.
  3. Both ratios together -- and the arbitrary level cancels:

      nt          = raw * F(d, pool)                (spline level inside)
      rating      = 100 * C(pool) * (1+difficulty) / nt
      C(pool)     = pool-mean ability IN THE POOL'S OWN nt UNITS
                  ~ mean_raw(pool) * F(d, pool)
   => hs_rating   = rating * [C(hs)/C(pool)] * [F(d, pool)/F(d, hs)]
                  = rating * mean_raw(hs)/mean_raw(pool)     -- levels gone

  C comes from the same recovery formula conversions uses, but scoped by
  ranking_results.pool -- the pool of the ROW'S OWN SEASON -- which is
  what kills the membership pollution. F comes from the engine's cached
  normalization factor. Era (gender-keyed) and geometry (pool-blind)
  cancel in the F ratio; raw time, weather and difficulty cancel between
  the race's two views. The factor stays per (pool, sport, distance):
  spline levels differ by distance, and pretending otherwise would be the
  flat-1.06 mistake all over again.

★ SAME GENDER, ALWAYS. 'college_f' maps onto 'hs_f', never 'hs_m': the view
  answers "what would this rating read among high schoolers like them", and
  the gender is part of the pool's identity, not part of the level
  difference the view exists to remove. A pool without a gender suffix
  (unknown_gender) gets no factor and keeps its own-scale number.

★ ONE FACTOR PER POOL (owner, 2026-09-02: "hs-equivalent changes between
  sports too ... it totally fucks the rankings"). The per-(pool, sport,
  distance) factor above was right about the LEVEL and wrong about the
  BOARDS: a middle-school board of both sports sorted on HS-equivalents
  put a 142.2 XC season under a 138.2 track season, because the two rows
  wore different multipliers. A view that reorders rows inside one pool is
  not a view of that pool. So the factor is now C(hs)/C(pool) alone, taken
  inside each sport (the two sports' constants sit on different scales,
  1600 m against 5000 m) and combined as the geometric mean of the two
  ratios -- one number per pool, which is exactly the old factor at the
  normalising distance (where F is 1 on both sides), and which the joint
  solve's sport offset licenses: inside a pool, XC and TF ratings are
  already on one scale, so their HS multiplier must be too. Within a pool the HS view is now a monotone rescale of the own
  view -- same order on every board, for every mix of sport and distance.
  The `sport` and `distance_m` arguments stay for the callers and are
  ignored.

⚠ THE VIEW CHANGES NUMBERS ONLY, NEVER VERDICTS. PR/SR flags, board
  eligibility and record stars are all computed on the own-pool scale and
  stay put when the reader toggles -- a view must not re-adjudicate records.
"""

import sys

import numpy as np
from collections import Counter
from statistics import median

# The engine's forward machinery via the conversions wrapper -- reused,
# never reimplemented, so a spline refit changes this view automatically.
from conversions import _forward_factor, default_difficulty
from athlete_bests import _tfEvent
# conversions has already put scripts/ on sys.path by the time this runs.
from database import getConn

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

_FAILED = set()          # keys already complained about


# ------------------------------------------------------------------ #
#  C(pool, sport) -- the pool constant, recovered from SEASON-ACCURATE rows
# ------------------------------------------------------------------ #
#
# Same recovery formula as conversions.pool_mean (the engine wrote every
# other term of its own equation down), but the sample is scoped by
# ranking_results.pool -- the pool of the row's own season -- instead of
# athlete_ratings membership. That scoping is the entire fix: membership
# mixes a college athlete's HS-era rows into the college sample and every
# pool medians to the HS mode.
#
# ★ NO PER-ROW DIFFICULTY JOINS, AND THAT IS A MEASURED TRADE. The first
#   version copied conversions._MEAN_SQL's venue joins for the exact
#   (1+difficulty) per row -- but the XC shape matches course_canonical on
#   round(gps, 5) equality, which no index serves, so each constant cost a
#   1,500-row nested-loop scan and the HOME PAGE (the one place needing all
#   ~16 pool x sport constants at once) took seconds per fresh process.
#   Using the sport's DEFAULT difficulty instead: the same constant divides
#   both C's, so it cancels in the C_hs/C_pool ratio the view actually
#   uses; what remains is each pool's venue-mix deviation from the default,
#   ~1% -- inside the tolerance the representative factor already accepts.
#   The pool/sport sample fetch is index-served (rr_board_rating_idx) and
#   the results join is by primary key, so a constant now costs
#   milliseconds instead of seconds.

_CONST_SQL = {
    "XC": """
        WITH sample AS (
            SELECT rr.result_id
            FROM   ranking_results rr
            WHERE  rr.pool = %(pool)s AND rr.sport = 'XC'
            LIMIT  %(n)s
        )
        SELECT r.speed_rating, r.normalized_time
        FROM   sample s
        JOIN   results r ON r.result_id = s.result_id
        WHERE  r.speed_rating > 0 AND r.normalized_time > 0
    """,
    "TF": """
        WITH sample AS (
            SELECT rr.result_id
            FROM   ranking_results rr
            WHERE  rr.pool = %(pool)s AND rr.sport = 'TF'
            LIMIT  %(n)s
        )
        SELECT r.speed_rating, r.normalized_time
        FROM   sample s
        JOIN   results_tf r ON r.result_id = s.result_id
        WHERE  r.speed_rating > 0 AND r.normalized_time > 0
    """,
}

_CONST_CACHE = {}              # (pool, sport) -> constant or None
_CONST_SAMPLE = 1500           # rows to median over; a constant needs few
_CONST_MIN_ROWS = 50           # below this the median is an anecdote

# ★ THE CONSTANTS SURVIVE RESTARTS. Twenty cold samples made the first
#   home page of every fresh process cost ~6s; the constants only drift
#   when a pipeline rewrites ratings, and a day-stale constant moves a
#   factor by ~1% -- under what the toggle can even display. So they
#   persist to a JSON sidecar for a day and the first page pays nothing.
_CONST_FILE = None
# 7 days: the pipeline's warm step (13b) rewrites the file on
# every rebuild, so the TTL is only a backstop against a very
# stale sidecar on a machine that stopped running pipelines.
_CONST_FILE_TTL = 7 * 24 * 3600


def _constFile():
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pool_constants_cache.json")


def _loadConstFile():
    import json, os, time
    try:
        path = _constFile()
        if time.time() - os.path.getmtime(path) > _CONST_FILE_TTL:
            return
        with open(path) as fh:
            for k, v in json.load(fh).items():
                pool, _, sport = k.partition("|")
                _CONST_CACHE[(pool, sport)] = v
    except Exception:                    # noqa: BLE001 -- cache, not truth
        pass


def _saveConstFile():
    import json
    try:
        with open(_constFile(), "w") as fh:
            json.dump({f"{p}|{s}": v
                       for (p, s), v in _CONST_CACHE.items()}, fh)
    except Exception:                    # noqa: BLE001
        pass


_loadConstFile()


def _poolConstant(pool, sport):
    """C(pool, sport): median of rating*nt/(1+difficulty)/100 over rows the
    athlete ran WHILE IN this pool. None when the pool cannot be sampled.
    ⚠ PER SPORT, ALWAYS. The track pools normalise to 1600 m and the
      cross-country pools to 5000 m, so the two sports' constants sit on
      different scales (about 300 against 1300); a median over both is a
      number on neither scale. hsFactor takes the ratio inside each sport
      and combines the ratios, which are scale-free."""
    key = (pool, sport)
    if key in _CONST_CACHE:
        return _CONST_CACHE[key]
    sql = _CONST_SQL.get(sport)
    vals = []
    if sql is not None:
        try:
            d = default_difficulty(sport)     # see the trade note above
            with getConn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, {"pool": pool, "n": _CONST_SAMPLE})
                    for rating, norm in cur.fetchall():
                        if not rating or not norm:
                            continue
                        vals.append(
                            float(rating) * float(norm) / (1.0 + d) / 100.0)
        except Exception as exc:         # noqa: BLE001 -- a view, not a page
            if key not in _FAILED:
                _FAILED.add(key)
                print(f"pool_view: constant sample for {pool}/{sport} "
                      f"raised {type(exc).__name__}: {exc}", flush=True)
    value = median(vals) if len(vals) >= _CONST_MIN_ROWS else None
    if value is None and key not in _FAILED:
        _FAILED.add(key)
        print(f"pool_view: constant for {pool}/{sport} unavailable "
              f"({len(vals)} usable rows, need {_CONST_MIN_ROWS})", flush=True)
    _CONST_CACHE[key] = value
    _saveConstFile()
    return value


def hsFactor(pool, sport, distance_m):
    """Multiplier from `pool`'s scale onto the same-gender HS scale.

    ONE NUMBER PER POOL (module header): C(hs_g) / C(pool), with C sampled
    over both sports. `sport` and `distance_m` are accepted for the callers
    and ignored -- a factor that varied by them reordered rows inside a
    pool on every mixed board.

    Returns None when no factor can be computed (no pool, no gender suffix,
    constant unavailable, sanity rail) -- callers must treat None as "leave
    the rating on its own scale", never as 1.0-with-a-shrug.
    """
    if not pool:
        return None
    pool = pool.split("|", 1)[0]
    if pool in ("hs_m", "hs_f"):
        return 1.0
    suffix = pool.rsplit("_", 1)[-1]
    if suffix not in ("m", "f"):
        return None                      # unknown_gender etc: no HS twin
    key = pool
    if key in _FACTOR_CACHE:
        return _FACTOR_CACHE[key]
    why = None
    factor = None
    # ★ ONE RATIO PER SPORT, THEN THE GEOMETRIC MEAN. Each sport's
    #   constants share a scale, so C(hs)/C(pool) inside a sport is a
    #   pure multiplier; under the joint solve the two sports' ratios
    #   should agree, and averaging them is what makes the factor one
    #   number per pool. (A median over both sports' samples, tried on
    #   2026-09-02, mixed a 1600 m scale with a 5000 m one and put the
    #   middle-school boards at 220.)
    ratios = []
    for sp in ("XC", "TF"):
        c_own = _poolConstant(pool, sp)
        c_hs = _poolConstant("hs_" + suffix, sp)
        if c_own and c_hs:
            ratios.append(float(c_hs) / float(c_own))
    if not ratios:
        why = f"no sport with both constants ({pool} and hs_{suffix})"
    else:
        factor = float(np.exp(np.mean(np.log(ratios))))
        if not (_FACTOR_LO <= factor <= _FACTOR_HI):
            why = (f"factor {factor:.3f} outside the "
                   f"{_FACTOR_LO}-{_FACTOR_HI} sanity rail "
                   f"(ratios {', '.join(f'{r:.3f}' for r in ratios)})")
            factor = None
    # ! FAILURES ARE LOUD. A factor that cannot be built hides the toggle
    #   with no other symptom, so say why ONCE on the server console.
    if why is not None and key not in _FAILED:
        _FAILED.add(key)
        print(f"pool_view: no HS factor for {pool} -- {why}", flush=True)
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


# ------------------------------------------------------------------ #
#  SITE-WIDE STAMPS -- every other page funnels through these two
# ------------------------------------------------------------------ #

# A season/career mean has no single race context; these are the contexts a
# typical rated race in each sport actually has, and the F ratio moves only
# ~1-2% across the realistic distance range, so one representative factor
# per (pool, sport) is honest for aggregate numbers.
_REP_DIST = {"XC": 5000.0, "TF": 1600.0}


def repFactor(pool, sport):
    """The representative factor for aggregate numbers (season means, career
    bests, board rows) that carry a pool but no single race distance."""
    return hsFactor(pool, sport, _REP_DIST.get(sport))


def stampRowsHs(cur, sport, rows, distance=None, distance_key=None,
                event_key=None, rating_key="speed_rating"):
    """Stamp row["hs_rating"] onto server-rendered RESULT rows (race, course,
    venue, compiled pages).

    Pools come from ranking_results by result_id in ONE ANY() query; rows
    that never ranked (unresolved seasons) take the table's MODAL pool --
    on a race page the field is one population, so the mode is the honest
    fill. Distance: one value for the whole table (`distance`), a per-row
    column (`distance_key`), or a TF event string (`event_key`).
    Returns True when anything moves enough to show the toggle."""
    ids = [r["result_id"] for r in rows
           if r.get("result_id") is not None and r.get(rating_key) is not None]
    pools = {}
    if ids:
        try:
            cur.execute("""
                SELECT result_id, pool FROM ranking_results
                WHERE  sport = %s AND result_id = ANY(%s)
            """, (sport, ids))
            # ! EVERY CALLER PASSES A RealDictCursor. Unpacking a dict row
            #   as `rid, p` silently binds its KEYS ("result_id", "pool"),
            #   which mapped every row to the literal pool "pool", failed
            #   every factor, and hid the toggle on all result pages.
            for rec in cur.fetchall():
                rid, p = ((rec["result_id"], rec["pool"])
                          if isinstance(rec, dict) else (rec[0], rec[1]))
                if p:
                    pools[rid] = p
        except Exception:                # noqa: BLE001 -- UndefinedTable et al.
            cur.connection.rollback()
            pools = {}

    modal = (Counter(pools.values()).most_common(1)[0][0] if pools else None)

    has_alt = False
    for row in rows:
        rating = row.get(rating_key)
        if rating is None:
            row["hs_rating"] = None
            continue
        pool = pools.get(row.get("result_id")) or modal
        d = distance
        if d is None and distance_key is not None:
            d = row.get(distance_key)
        if d is None and event_key is not None and row.get(event_key):
            d, _label = _tfEvent(row[event_key])
        factor = hsFactor(pool, sport, d)
        if factor is not None:
            row["hs_rating"] = float(rating) * factor
            if abs(factor - 1.0) > _MOVES:
                has_alt = True
        else:
            row["hs_rating"] = None
    return has_alt


def stampBoardRows(rows, rating_keys=("rating",), pool=None, sport=None):
    """Stamp row["hs_<key>"] onto BOARD rows (rankings/teams APIs, homepage
    panels, school tables) that already carry their pool -- per row, or one
    for the whole board via `pool`/`sport`. Uses the representative factor:
    these are season means and career bests, not single races.
    Returns True when anything moves enough to show the toggle."""
    has_alt = False
    for row in rows:
        p = row.get("pool", pool)
        s = row.get("sport", sport)
        # A row that knows its race distance gets the exact factor; a
        # season/career aggregate takes the representative one.
        d = row.get("distance")
        factor = hsFactor(p, s, d) if d else repFactor(p, s)
        for key in rating_keys:
            rating = row.get(key)
            if rating is not None and factor is not None:
                row["hs_" + key] = round(float(rating) * factor, 1)
                if abs(factor - 1.0) > _MOVES:
                    has_alt = True
            else:
                row["hs_" + key] = None
    return has_alt


def sortByShown(rows, key):
    """Order board rows by the number the page SHOWS: hs_<key> where it was
    stamped, else <key>. Descending; rows with neither go last.

    ★ A ROSTER THAT MIXES POOLS READS UNSORTED OTHERWISE. schoolRoster orders
      by the stored own-pool rating; the page then renders the HS-equivalent
      (the default view), and two pools with different factors -- college_m
      onto hs_m against college_f onto hs_f -- interleave out of order. The
      rank column is loop.index, so the order of this list IS the rank.
      Within one pool the factor is a constant and this changes nothing.

    ! SORTED FOR THE DEFAULT VIEW. The scale toggle is client-side and swaps
      numbers without re-ranking, so the own-pool view of a mixed table can
      read unsorted the way the HS view used to. That is the smaller wrong
      way round: own-pool numbers across pools were never comparable.
    """
    def shown(r):
        v = r.get("hs_" + key)
        if v is None:
            v = r.get(key)
        return (v is None, -(float(v) if v is not None else 0.0))
    rows.sort(key=shown)
    return rows


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

    pools = ("ms_m", "ms_f", "hs_m", "hs_f",
             "college_m", "college_f", "pro_m", "pro_f")

    print("\n  -- pool constants C (season-accurate recovery; own nt units)")
    print("  " + "pool".ljust(12) + "XC".rjust(10) + "TF".rjust(10)
          + "    (a pool equal to its HS twin here has its whole gap in "
            "the spline level)")
    for pool in pools:
        cells = []
        for sport in ("XC", "TF"):
            c = _poolConstant(pool, sport)
            cells.append((f"{c:.1f}" if c else "--").rjust(10))
        print("  " + pool.ljust(12) + "".join(cells))

    print("\n  -- spline-level ratios F(pool)/F(hs) (arbitrary alone; "
          "cancelled by C)")
    header = "  " + "pool".ljust(12) + "".join(
        f"{s} {int(d)}m".rjust(12) for s, d in _REF)
    print(header)
    for pool in pools:
        cells = []
        for sport, dist in _REF:
            suffix = pool.rsplit("_", 1)[-1]
            try:
                f_own = _forward_factor(dist, pool, None, None, None,
                                        sport, None)
                f_hs = _forward_factor(dist, "hs_" + suffix, None, None,
                                       None, sport, None)
                ratio = (float(f_own) / float(f_hs)
                         if f_own and f_hs else None)
            except Exception:             # noqa: BLE001
                ratio = None
            cells.append((f"x{ratio:.4f}" if ratio is not None
                          else "--").rjust(12))
        print("  " + pool.ljust(12) + "".join(cells))

    print("\n  -- VIEW FACTORS (C ratio x F ratio -- what the page applies)")
    print(header)
    for pool in pools:
        cells = []
        for sport, dist in _REF:
            factor = hsFactor(pool, sport, dist)
            cells.append((f"x{factor:.4f}" if factor is not None
                          else "--").rjust(12))
        print("  " + pool.ljust(12) + "".join(cells))
    print("  (sanity marks: college_m ~x1.2-1.35, ms_m ~x0.75-0.9, "
          "pro above college;\n  a -- cell printed its reason above)")

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
