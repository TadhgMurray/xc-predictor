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
  not a view of that pool. So the factor is now the SAME two-ratio formula
  evaluated once per sport at that sport's representative distance, and
  the two sports combined as a geometric mean -- one number per pool,
  which the joint solve's sport offset licenses: inside a pool, XC and TF
  ratings are already on one scale, so their HS multiplier must be too. Within a pool the HS view is now a monotone rescale of the own
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

# pool -> one line on WHERE that pool's factor came from: its own measurement,
# the college fallback, or the reason there is none. Diagnostic only; the page
# never reads it. It exists because the 2026-09-20 pro bug was invisible from
# the factor table alone -- pro_m printed a perfectly ordinary x1.1 and there
# was no way to see it was college_m's number wearing pro's name.
_FACTOR_WHY = {}

# ⚠ SANITY RAIL ON THE FACTOR ITSELF. Real level gaps are on the order of
#   10-30% (college vs hs, ms vs hs); a ratio outside this band means a
#   spline evaluated somewhere it has no business (a 100m dash through the
#   XC spline) or a broken artifact -- refuse it and show own-scale.
_FACTOR_LO, _FACTOR_HI = 0.5, 2.0

# ⚠⚠ AND THE PRO POOL GETS ITS OWN CEILING, BECAUSE THE RAIL ABOVE WAS SIZED
#    ON THE WRONG GAP (2026-09-21). "10-30%" is measured against college and
#    middle school; the pro-to-HS gap is the largest in the corpus and a
#    MEASURED pro factor can legitimately sit near 2. Capping it at 2.0 did
#    not just hide the toggle -- on 2026-09-20 it routed pro through the
#    college fallback below, which multiplied a pro rating by the
#    COLLEGE-to-HS number. That is not a conversion of anything: it left the
#    row on no scale at all, higher than it started, which is the owner's
#    "hs-equivalent for pros fucks it up rather than doing nothing ... it
#    makes pros have even higher speed rating".
#
#    So the rail is widened for pro instead of being worked around. 3.0 is
#    still a rail: a pro median reading 300 on the high-school scale is a
#    broken artifact, not a talent gap.
_PRO_FACTOR_HI = 3.0

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

# ⚠⚠⚠ AND A PRO POOL CANNOT BE SAMPLED FROM ranking_results AT ALL. That is
#     not a shortage of rows, it is a guarantee: build_ranking_results.
#     isRankablePool returns False for every pool starting "pro_", so the
#     table the query above reads contains ZERO pro rows, by construction,
#     and has since 2026-09-14.
#
#     So _poolConstant("pro_m") has always returned None, `ratios` has
#     always come back empty, and hsFactor has ALWAYS taken the college
#     fallback for a pro row. Every attempt to fix the pro factor by
#     adjusting the measured path -- the widened _PRO_FACTOR_HI rail I
#     added on 2026-09-21 among them -- was editing code that cannot run.
#     The owner has reported this three times ("when ppl are in pro pool
#     they are not able to be hs-equivalent", then "hs-equivalent for pros
#     fucks it up rather than doing nothing ... it makes pros have even
#     higher speed rating", then again on 2026-09-22) and each time I
#     looked at the rail instead of at where the sample comes from.
#
# ! SO THE PRO SAMPLE READS THE RESULT TABLES, keyed on rating_pool -- "the
#   pool the rating was computed in", written on the row by speed_ratings_db
#   (issue 171). It is the same recovery arithmetic on the same two columns;
#   only the source of the row list changes, because ranking_results is the
#   one table guaranteed not to hold them.
#
# ⚠ rating_pool HAS NO INDEX, so this is a scan that stops at the LIMIT.
#   Pro rows are about 1% of the corpus, so it reads ~150k rows to find
#   1,500 -- but "about" is not "always", and this runs on a page. It is
#   bounded below and falls back to exactly the old behaviour (the college
#   factor) if the budget runs out, so it is never worse than before.
#
# ! split_part, BECAUSE THE GO-LIVE ONCE WROTE A SPORT SUFFIX. conversions
#   records that rating_pool used to carry "pool|SPORT" and now carries the
#   bare pool; matching the bare prefix reads both.
_STAMPED_CONST_SQL = {
    "XC": """
        SELECT r.speed_rating, r.normalized_time
        FROM   results r
        WHERE  split_part(r.rating_pool, '|', 1) = %(pool)s
          AND  r.speed_rating > 0 AND r.normalized_time > 0
        LIMIT  %(n)s
    """,
    "TF": """
        SELECT r.speed_rating, r.normalized_time
        FROM   results_tf r
        WHERE  split_part(r.rating_pool, '|', 1) = %(pool)s
          AND  r.speed_rating > 0 AND r.normalized_time > 0
        LIMIT  %(n)s
    """,
}
_STAMPED_TIMEOUT_MS = 4000

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
_NONE_UNTIL = {}               # (pool, sport) -> retry a failed sample after
_NONE_RETRY_S = 600


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
                # ! NEVER A NULL FROM DISK (issue 159). A sample that failed
                #   while the pipeline was rebuilding ranking_results used
                #   to be saved as unavailable and honoured for the file's
                #   whole week: the HS-equivalent toggle went dead for every
                #   non-HS pool until the TTL ran out.
                if v is not None:
                    _CONST_CACHE[(pool, sport)] = v
    except Exception:                    # noqa: BLE001 -- cache, not truth
        pass


def _saveConstFile():
    import json
    try:
        with open(_constFile(), "w") as fh:
            json.dump({f"{p}|{s}": v
                       for (p, s), v in _CONST_CACHE.items()
                       if v is not None}, fh)
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
    # a failed sample is retried after a few minutes, not remembered
    import time as _time
    until = _NONE_UNTIL.get(key)
    if until is not None and _time.time() < until:
        return None
    # ! THE PRO POOLS TAKE THE OTHER SOURCE. See _STAMPED_CONST_SQL: the table
    #   the normal query reads is guaranteed to hold none of their rows.
    #   Since 2026-09-22 it is also the FALLBACK for every other pool;
    #   see the note above the retry below.
    is_pro = pool.startswith("pro_")
    sql = (_STAMPED_CONST_SQL if is_pro else _CONST_SQL).get(sport)
    vals = []
    if sql is not None:
        try:
            d = default_difficulty(sport)     # see the trade note above
            with getConn() as conn:
                with conn.cursor() as cur:
                    if is_pro:
                        # bounded, and a timeout leaves the college fallback
                        # in place -- exactly the pre-2026-09-22 behaviour
                        cur.execute("SET LOCAL statement_timeout = %s",
                                    (_STAMPED_TIMEOUT_MS,))
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
    # ⚠⚠⚠ THE LIMIT WAS INSIDE THE CTE, SO THE SAMPLE WAS TAKEN BEFORE THE
    #     ROWS WERE CHECKED (owner's pool_view run, 2026-09-22):
    #
    #         pool_view: constant for hs_m/TF unavailable (0 usable rows)
    #         pool_view: constant for hs_f/TF unavailable (0 usable rows)
    #
    #     _CONST_SQL reads 1,500 ranking_results rows for the pool and only
    #     THEN joins results_tf and drops the ones with no rating. An
    #     unordered LIMIT over the biggest pool in the corpus returns a
    #     correlated physical slice -- and high school track is mostly
    #     sprints and field events, which the engine never rates. Zero of
    #     1,500 survived, twice, while ms, college and pro all sampled
    #     cleanly: reproduced on Postgres 16, 0 rows against 1,500 for the
    #     same pool once the filter runs first.
    #
    # ★★ AND THE COST WAS NOT "NO TOGGLE ON HIGH SCHOOL TRACK". hsFactor
    #    takes C(hs)/C(pool) per sport and combines the RATIOS, so losing
    #    the hs TF constant costs every pool its TF ratio -- which is why
    #    that same run printed "measured from 1 sport ratio(s)" for all
    #    eight pools. One missing sample degraded the whole table to
    #    half its evidence.
    #
    # ! THE RETRY IS THE PRO QUERY, WHICH IS WHY IT IS NOT CALLED PRO ANY
    #   MORE. rating_pool is stamped by the go-live on every rated row,
    #   boards or not, so it needs no join and no ranking_results at all --
    #   and it is fast exactly where the primary fails. The primary reads
    #   a bounded 1,500 rows and wins on a RARE pool; this one seq-scans
    #   until the LIMIT fills and wins on a COMMON one, where matches are
    #   everywhere. Measured on a fixture: 14ms for the big pool.
    #
    # ! PRIMARY FIRST, STILL. ranking_results is the narrower statement --
    #   rows the athlete ran WHILE IN this pool, already adjudicated -- so
    #   a pool that samples cleanly from it keeps doing so and nothing
    #   about the working seven pools moves.
    if len(vals) < _CONST_MIN_ROWS and not is_pro:
        retry = _STAMPED_CONST_SQL.get(sport)
        if retry is not None:
            try:
                with getConn() as conn:
                    with conn.cursor() as cur:
                        # bounded: a rare pool can scan a long way before
                        # the LIMIT fills, and this is a page render
                        cur.execute("SET LOCAL statement_timeout = %s",
                                    (_STAMPED_TIMEOUT_MS,))
                        cur.execute(retry, {"pool": pool, "n": _CONST_SAMPLE})
                        rows = cur.fetchall()
                        # ! PUT THE BUDGET BACK BEFORE THE CONNECTION GOES
                        #   HOME. SET LOCAL is transaction-scoped, and this
                        #   connection is pooled -- racecast/school.py
                        #   leaked exactly this budget onto every later
                        #   query in a request on 2026-09-21.
                        cur.execute("SET LOCAL statement_timeout = DEFAULT")
                d = default_difficulty(sport)
                got = []
                for rating, norm in rows:
                    if not rating or not norm:
                        continue
                    got.append(float(rating) * float(norm) / (1.0 + d) / 100.0)
                if len(got) > len(vals):
                    print(f"pool_view: {pool}/{sport} sampled "
                          f"{len(got):,} rows from rating_pool after "
                          f"ranking_results gave {len(vals):,}", flush=True)
                    vals = got
            except Exception as exc:                 # noqa: BLE001
                if key not in _FAILED:
                    print(f"pool_view: rating_pool retry for {pool}/{sport} "
                          f"raised {type(exc).__name__}: {exc}", flush=True)

    value = median(vals) if len(vals) >= _CONST_MIN_ROWS else None
    if value is None and key not in _FAILED:
        _FAILED.add(key)
        print(f"pool_view: constant for {pool}/{sport} unavailable "
              f"({len(vals)} usable rows, need {_CONST_MIN_ROWS})", flush=True)
    if value is None:
        _NONE_UNTIL[key] = _time.time() + _NONE_RETRY_S
        return None
    _CONST_CACHE[key] = value
    _saveConstFile()
    return value


# the representative distance per sport: where aggregate numbers (season
# means, board rows) and the per-pool factor are priced
_REP_DIST = {"XC": 5000.0, "TF": 1600.0}


def hsFactor(pool, sport, distance_m):
    """Multiplier from `pool`'s scale onto the same-gender HS scale.

    ONE NUMBER PER POOL (module header): per sport, C(hs_g)/C(pool) times
    F(REP_DIST[sp], pool)/F(REP_DIST[sp], hs_g) -- the F ratio converts the
    pools' different reference distances -- then the geometric mean over
    the two sports. `sport` and `distance_m` are accepted for the callers
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
    # ⚠ AND THE UNIT CONVERSION STAYS (2026-09-03, diag_hs_factor on the
    #   live box). Each pool is normalised to its OWN reference distance --
    #   middle school near 3000 m, high school 5000, college men 8000,
    #   college women 6000 -- so the constants are in different units per
    #   pool (ms_m 860 against hs_m 1229) and their bare ratio read 1.41,
    #   which put the middle-school boards at 220. F(d, pool)/F(d, hs) at
    #   one fixed distance per sport converts the units; the module header
    #   explains why the two ratios together are the talent gap and neither
    #   alone is. Fixed at the sport's representative distance, the factor
    #   is still one number per pool.
    ratios = []
    for sp in ("XC", "TF"):
        c_own = _poolConstant(pool, sp)
        c_hs = _poolConstant("hs_" + suffix, sp)
        if not (c_own and c_hs):
            continue
        d = _REP_DIST[sp]
        try:
            f_own = _forward_factor(d, pool, None, None, None, sp, None)
            f_hs = _forward_factor(d, "hs_" + suffix, None, None, None, sp, None)
        except Exception as exc:         # noqa: BLE001 -- a view, not a page
            if key not in _FAILED:
                print(f"pool_view: engine factor for {pool}/{sp} raised "
                      f"{type(exc).__name__}: {exc}", flush=True)
            continue
        if not f_own or not f_hs:
            continue
        ratios.append((float(c_hs) / float(c_own)) * (float(f_own) / float(f_hs)))
    hi = _PRO_FACTOR_HI if pool.startswith("pro_") else _FACTOR_HI
    if not ratios:
        why = f"no sport with both constants and factors ({pool} and hs_{suffix})"
    else:
        factor = float(np.exp(np.mean(np.log(ratios))))
        if not (_FACTOR_LO <= factor <= hi):
            why = (f"factor {factor:.3f} outside the "
                   f"{_FACTOR_LO}-{hi} sanity rail "
                   f"(ratios {', '.join(f'{r:.3f}' for r in ratios)})")
            factor = None
    # ★ A PRO POOL RIDES ON THE COLLEGE FACTOR (2026-09-08, Graham
    #   Blanks' page: a 29:41 10k at the World XC trials read 96.4
    #   beside college rows at 146, because pro_m has too few rated
    #   rows for a constant of its own and the rating stayed on the
    #   pro scale). The college pool of the same gender is the nearest
    #   scale with a factor; wrong by the pro-college gap, which is
    #   small, rather than wrong by the whole conversion.
    #
    # ⚠⚠ AND IT IS GATED ON `not ratios` AGAIN, DELIBERATELY (2026-09-21). On
    #    2026-09-20 I widened this to fire whenever `factor is None`, so a pro
    #    pool that HAD been measured but missed the 0.5-2.0 rail got college's
    #    multiplier instead of its own. That was the wrong repair for a real
    #    problem: the rail was mis-sized for pro, and the fix for a mis-sized
    #    rail is _PRO_FACTOR_HI above, not a substituted number. Substituting
    #    college's factor takes a rating measured against the pro pool's mean
    #    and multiplies it by the college-to-HS gap -- arithmetic with no
    #    meaning, which is why the owner saw pro ratings go UP instead of
    #    converting.
    #
    #    The fallback survives only for the case it was written for: pro_m has
    #    no constants AT ALL (too few rated rows to sample), so there is
    #    nothing to be wrong about and college is the nearest scale that
    #    exists. When a pro pool does have constants, its own measurement wins
    #    -- and if that measurement is mad enough to miss even a 3.0 rail, the
    #    row stays on its own scale and says why on the console, because a
    #    visibly unconverted number beats an invisibly wrong one.
    if factor is None and not ratios and pool.startswith("pro_"):
        college = hsFactor("college_" + suffix, sport, distance_m)
        if college:
            _FACTOR_CACHE[key] = college
            _FACTOR_WHY[key] = (f"college_{suffix} fallback (x{college:.4f}) "
                                f"-- {pool} has no constants of its own")
            return college
    # ! FAILURES ARE LOUD. A factor that cannot be built hides the toggle
    #   with no other symptom, so say why ONCE on the server console.
    if why is not None and key not in _FAILED:
        _FAILED.add(key)
        print(f"pool_view: no HS factor for {pool} -- {why}", flush=True)
    _FACTOR_WHY[key] = (why if factor is None else
                        f"measured from {len(ratios)} sport ratio(s) "
                        f"({', '.join(f'{r:.4f}' for r in ratios)}), "
                        f"rail {_FACTOR_LO}-{hi}")
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
            # ★ THE ROW'S OWN POOL NEXT (issue 308, 2026-09-08): the go-live
            #   writes rating_pool on every rated row, boards or not. A pro
            #   row is on no board, so ranking_results had no pool for it,
            #   the season poll had no rows either, and a 29:41 10k stayed
            #   on the pro scale beside college rows on the HS one.
            pool = race.get("rating_pool") or None
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


def refreshConstants(pools, sports=("XC", "TF")):
    """Re-measure every pool constant from the CURRENT database and persist.

    ⚠⚠ WHY (2026-09-25). _poolConstant answers from the JSON sidecar for its
       whole week-long TTL, and the pipeline's 13b step warmed the constants
       by CALLING _poolConstant -- so after a solve it read last week's
       numbers back out of the file and wrote them in again. The ability
       gate went live and the pro pool's HS factor stayed x0.7491, a value
       measured on the contaminated pool the gate exists to clean up.
       A constant is a fact about ONE solve; the step that follows the solve
       has to measure it, not remember it.

    ! A FAILED SAMPLE KEEPS THE OLD VALUE, and says so. Dropping a pool would
      switch its HS-equivalent off on every page until someone noticed; a
      stale number beside a warning is the lesser failure.

    Returns [(pool, sport, old, new, kept_old)].
    """
    old = dict(_CONST_CACHE)
    _CONST_CACHE.clear()
    _FACTOR_CACHE.clear()
    _NONE_UNTIL.clear()
    out = []
    for pool in pools:
        for sport in sports:
            key = (pool, sport)
            v = _poolConstant(pool, sport)
            kept = False
            if v is None and old.get(key) is not None:
                _CONST_CACHE[key] = old[key]
                kept = True
            out.append((pool, sport, old.get(key), _CONST_CACHE.get(key), kept))
    _saveConstFile()
    return out


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
        # the pool the rating was computed in, when the row carries it
        # (issue 171); else the boards' pool by result id; else the mode
        pool = row.get("rating_pool") or pools.get(row.get("result_id")) or modal
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
    # ★★ THE GAP COLUMN IS A CONSISTENCY CHECK, AND IT IS FREE. Both
    #    constants for one pool are expressed at the SAME anchor -- the
    #    bare pool key decides it for both sports (normalize_distance.
    #    targetFor) -- so ln(C_XC / C_TF) is that pool's own measurement of
    #    what grass costs. It has nothing to do with the view factor and is
    #    not used by it; it is here because two numbers printed side by side
    #    for a year never got subtracted.
    #
    # ★ AND THERE IS A NUMBER TO CHECK IT AGAINST. bracket_engine's
    #   XC_TRACK_GAP is ln(1.06) = 5.83%, a DEFINITION -- the seasons do
    #   not overlap, so it could never be measured there. This is an
    #   independent measurement of the same quantity, and on 2026-09-22 it
    #   read 5.12% for hs_m and 4.98% for hs_f. Half a percent from a number
    #   nothing here has ever seen.
    #
    # ⚠ A NEGATIVE GAP IS PHYSICALLY BACKWARDS. It says that pool's track
    #   population is SLOWER than its cross-country population at the same
    #   anchor, and no pool's runners get slower on a track. The same run
    #   read college_m -2.02%, college_f -2.24% and pro_m -0.43%, which is
    #   what dropped college_m's view factor to x1.15, below this module's
    #   own printed sanity band. Whatever is wrong with those constants, the
    #   gap says so in one column and the eight-number table did not.
    import math as _math
    _GRASS = _math.log(1.06)        # bracket_engine.XC_TRACK_GAP
    for pool in pools:
        cells, got = [], {}
        for sport in ("XC", "TF"):
            c = _poolConstant(pool, sport)
            got[sport] = c
            cells.append((f"{c:.1f}" if c else "--").rjust(10))
        gap = ""
        if got["XC"] and got["TF"]:
            g = _math.log(float(got["XC"]) / float(got["TF"]))
            note = ""
            if g < 0:
                note = "  <- BACKWARDS: track slower than grass"
            elif abs(g - _GRASS) > 0.03:
                note = "  <- far from the 5.83% grass cost"
            gap = f"{100.0 * g:>8.2f}%{note}"
        print("  " + pool.ljust(12) + "".join(cells) + gap)
    print("\n    the last column is ln(C_XC/C_TF): this pool's own measure of"
          "\n    what grass costs. bracket_engine DEFINES it as ln(1.06) = "
          "5.83%\n    and cannot measure it (the seasons do not overlap), so "
          "this is the\n    only independent read of it in the tree. Negative "
          "is impossible.")

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

    # ★ WHERE EACH FACTOR CAME FROM. The table above shows the number; this
    #   shows whether it is the pool's OWN measurement or a borrowed one.
    #   A pro pool reading college's factor is the 2026-09-20 bug and is
    #   invisible without this line.
    print("\n  -- provenance")
    for pool in pools:
        print("  " + pool.ljust(12) + str(_FACTOR_WHY.get(pool, "(not built)")))

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
    # --fresh: measure from the database instead of the sidecar -- without
    # it this prints whatever the last warm-up saved, up to a week old.
    _args = [a for a in sys.argv[1:] if a != "--fresh"]
    if "--fresh" in sys.argv[1:]:
        from rankings import POOLS
        refreshConstants(sorted(POOLS))
    _diag(int(_args[0]) if _args else None)
