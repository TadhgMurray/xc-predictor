# Project: xc-predictor
# Author:  Tadhg Murray
# Subset:  Speed Rating Engine
# File:    speed_ratings.py
# Purpose: Iterative convergence engine. Jointly solves, for every athlete, a
#          course-independent ABILITY, and for every course, a DIFFICULTY.
#
# THE IDEA
# --------
# A slow time can mean a slow runner or a hard course. From one race you cannot
# tell. But across every race every athlete ever ran, the two separate:
#
#     ability_a   = weighted mean over a's races of  normalized_time / (1 + d_c)
#     difficulty_c= mean over c's results of        (normalized_time / ability_a) - 1
#
# Each needs the other, so we alternate until they stop moving. Damping stops it
# oscillating; this is coordinate descent with a learning rate.
#
# WHAT CHANGED FROM THE 6/3 VERSION (each was silently costing real data)
# -----------------------------------------------------------------------
#  1. IDENTIFIABILITY. Nothing pinned the difficulty scale. Add a constant to
#     every difficulty and divide every ability by it, and the fixed-point
#     equations are equally satisfied -- the solution is a RAY, not a point. The
#     old loop drifted along it. We now re-center difficulties to mean zero every
#     iteration, which pins the gauge and makes "difficulty" mean "harder than an
#     average course". This is the single most important correctness fix here.
#
#  2. DATE HANDLING. computeDecayWeight ran strptime() on the date. psycopg2
#     returns a DATE column as datetime.date, so strptime raised TypeError, the
#     handler returned 0.0, and `if weight == 0: continue` DROPPED EVERY ROW. The
#     engine would have processed nothing. Now it accepts date or str.
#
#  3. IDENTITY. Keyed on athlete_id, which is NULL for 100% of tfrrs XC. Now
#     person_id -- the cross-source identity -- so an athlete's anet and tfrrs
#     races belong to one person instead of two half-rated strangers.
#
#  4. POOLING. Called getPool(grade, gender), which cannot read tfrrs (grade is
#     NULL or 'FR-1'). Now poolFor(grade, gender, source, school), the SSOT, so
#     tfrrs rows land in the right pool instead of unknown_level -> dropped.
#
#  5. MIDDLE SCHOOL. Excluded outright ("MS results are fucked"). They were --
#     because MS rows were pooled college and judged against college floors. That
#     is fixed upstream, so ~2M real results come back.
#
#  6. MEMORY. 34M rows as Python dicts is tens of GB. Everything is now columnar
#     numpy: integer codes for person/course/pool, float32 for times and weights.
#     The whole corpus fits in well under 2GB and each iteration is vectorised.
#
# A row with no course (tfrrs, no meets row) still informs its athlete's ability;
# it simply casts no vote on any course. Dropping it would discard a real race.

import math
from collections import defaultdict
from datetime import date, datetime

import numpy as np

from normalize_distance import poolFor
from season_year import seasonYearFor, RolloverLedger
import speed_ratings_kernels as K
from speed_ratings_db import (streamResults, saveCourseDifficulties,
                              saveAthleteRatings, saveResultSpeedRatings)
from itertools import chain

import os

SPORTS = ("XC", "TF")


# ------------------------------------------------------------------ #
# CHUNK 0 — CONSTANTS
# ------------------------------------------------------------------ #

DECAY_K = 0.996              # per-day recency decay. 0.998^365 = 0.48
                             # ATHLETE decay. ~1-year half life: last season's
                             # form says far more about you than 2015's did.

# COURSE decay. A course is a physical place: the hill in 2005 is the hill now.
# Its difficulty should be estimated from a much longer window than an athlete's
# form. 0.99966^3652 = 0.29, i.e. a ~5.6-year half life.
#   Previously course difficulty used NO decay at all (a plain unweighted mean),
#   so a 1985 race and a 2025 race voted equally on a course that has since been
#   rerouted. Slow decay, not no decay.
DECAY_K_COURSE = 0.99966

# SEASON-PHASE CONTROL. Early-season fields race rusty (below their season
# ability); that inflates dev and would be blamed on the COURSE. When on, the
# per-(sport, week) mean deviation is subtracted from dev before difficulty is
# formed, so a course is credited only for slowness BEYOND the calendar. Toggle
# off to reproduce the prior behaviour byte-for-byte.
# SEASON_PHASE_CORRECTION / SEASON_SHRINK_K RETIRED. Replaced by

# rust_fitness.buildCorrection.

#

# The old term estimated a mean deviation per (sport, week), shrank it, and

# re-centred it to zero. Two things were fatally wrong with that:

#

#   1. It was estimated FROM `dev`, which already contains difficulty. At a

#      venue racing in only one week, the week term and that venue's difficulty

#      fit the SAME residual and cannot be told apart.

#

#   2. Week correlates with venue -- Glendoveer IS week 18. So it read "week 18

#      is slow", which is true because championships happen at hard courses, and

#      then subtracted it from the venues that caused it. Measured: it removed

#      2.7% from Glendoveer, which is most of the reason an NXN winner topped

#      out at a 140 rating.

#

# The replacement is fitted OFF-LINE from same-athlete, same-season, same-venue,

# same-distance pairs, where difficulty is literally the same number on both

# sides of a ratio and cancels exactly. And neither of its terms correlates with

# venue: a season opener has first-timers alongside athletes who opened

# elsewhere, and "weeks since THIS athlete's opener" makes a week-18 race late

# for some and mid-season for others.

# An ability is a 5K-EQUIVALENT TIME. Anything outside this band is a solver
# artefact, not an athlete, and it must never reach computeCourseDifficulties:
# one athlete with ability = 1e-10 gives dev = norm/ability - 1 = 1.3e13, which
# detonates every course they raced. Observed: 92.2% of courses driven to
# difficulty = -6.85 and 33.6M of 34.6M rows discarded.
ABILITY_MIN, ABILITY_MAX = 300.0, 5000.0
CONVERGENCE_THRESHOLD = 1e-5 # stop when mean |change in difficulty| is below this
WORST_TOLERANCE       = 10   # ...and the WORST cell must be within 10x of it.
                             # Mean alone hides a single diverging venue.
SWEEP_ITERATIONS = 150    # ★ iteration cap for --sweep ONLY. A sweep COMPARES
                          # settings, it does not produce final numbers, and the
                          # metrics settle long before 500: in the last full run
                          # the difficulty range was [-0.372,+0.759] at iter 200
                          # and [-0.372,+0.762] at iter 500 -- three thousandths
                          # of movement over 300 iterations. Capping at 150 makes
                          # a five-value sweep cost about one full solve.
                          # ⚠ Sweep results are therefore APPROXIMATE. Re-run the
                          # winner uncapped before trusting its exact numbers.
MAX_ITERATIONS = 500      # was 50; the shrinkage-bounded loop converges ~iter 57
PATIENCE = 5                 # non-improving iterations before we give up
DAMPING = 0.5                # ★ HALF-STEP. Undamped, the solve fell into a clean
                             # period-2 oscillation (max |d| change alternating
                             # 0.001717 / 0.001718 for 260 iterations). The fixed
                             # point of a 2-cycle is the AVERAGE of its two states,
                             # so a half-step collapses it exactly. 0.3 was worse
                             # in the other direction: it integrated a marginal
                             # mode into a 500-iteration drift.

# ★ REGION PRIOR. Ridge shrinks by SAMPLE SIZE, which is the wrong axis for this
# problem: Kincaid Park has 9,540 results, so no defensible lambda touches it --
# yet its LEVEL is unidentified, because adding x to every Alaskan difficulty and
# subtracting x from every Alaskan ability fits all 9,540 rows exactly as well.
# Sample size and identifiability are different quantities.
#
# What pins a block's level is evidence that CROSSES OUT of it. So each region's
# mean difficulty is shrunk toward zero by the number of athletes who race in
# more than one region -- a region whose runners never leave gets pulled flat,
# one with heavy interstate traffic keeps its level. Within a region, cells are
# left alone: relative terrain is identified by the internal links, and the
# undamped solve already gets it right (terrain anchors went 1/7 -> 7/7).
REGION_SHRINK_K = 3000.0     # cross-region athletes for a region to keep HALF its
                             # level. TUNED, not guessed: --sweep 0,3000 gave
                             #     K=0     anchors 3/7  diff sd 0.1001  region sd 0.1175
                             #     K=3000  anchors 7/7  diff sd 0.0566  region sd 0.0155
                             # All three moved together, which is what rules out
                             # over-shrinkage: a prior that was merely FLATTENING
                             # would drop region sd AND break the anchors. Target
                             # for region sd is reference_fit.py's 0.0142.
                             # Re-tune with:
                             #   --cache --sweep 1000,3000,10000
OUTLIER_STD_THRESHOLD = 2.0
MIN_RACES = 3                # races before an athlete gets an ability
MIN_COURSE_ATHLETES = 20     # unique athletes before a course gets a difficulty
MIN_COURSE_LINKS    = 3      # athletes who ALSO race elsewhere before a course
                             # earns any difficulty; fewer -> unanchored -> flat.
# ★ ROBUST REWEIGHTING (IRLS), OFF BY DEFAULT. A course's difficulty is a
#   weighted mean of its field's log deviations, and a mean has no defence
#   against one bad race. An athlete who jogs a tempo effort in a scoring race
#   reads as evidence that the COURSE was brutal, and the next ability step
#   then pays every other finisher back as a hero. The ridge bounds how far a
#   cell can walk; it does not stop a single row from pulling it.
#
#   Huber, in the log space the estimator already works in: a row whose
#   residual exceeds ROBUST_C robust scales is down-weighted by
#   scale/|residual|, so it still votes, just not proportionally to how odd
#   it is. Rows inside the band are untouched, which is most of them.
#
# ! ZERO MEANS OFF, AND OFF IS THE DEFAULT ON PURPOSE. This changes every
#   difficulty in the corpus, so it must be MEASURED against the current
#   estimator before it becomes the estimator -- engine/holdout_eval.py is
#   that measurement. Shipping it on would change every rating on the site
#   with nothing to say whether the change was an improvement.
#
# ⚠ AND IT COSTS A SECOND PASS. The fused numba kernel never materialises the
#   deviation; a residual needs it. So the robust path builds the 61.8M-element
#   log_dev the fast path avoids, and runs the accumulation twice. Expect
#   roughly 2-3x on this function while it is on.
ROBUST_C            = 0.0    # Huber threshold in robust scales. 0 disables.
                             # 1.345 is the classic 95%-efficiency-at-normal
                             # value; 2.0 is gentler and a reasonable first try
                             # on data whose tails are real races, not noise.
ROBUST_SAMPLE       = 1_000_000   # rows sampled to estimate the robust scale.
                             # A median over 61.8M sorts 61.8M floats every
                             # iteration; a strided sample of a million is the
                             # same number to three decimals and ~60x cheaper.

RIDGE_LAMBDA        = 25.0   # ★ ridge on difficulty. Stops the drift: without it
                             # the range grew [-0.08,+0.27] -> [-0.52,+1.22] over 500
                             # iterations and never converged. See computeCourseDifficulties.
LINK_SHRINK_K       = 10     # empirical-Bayes shrinkage: raw difficulty is scaled
                             # by links/(links + K), pulling weakly-connected
                             # courses toward neutral 0. Bounds the tail-spread
                             # soft mode that stopped the loop converging.

# ★ §6.1. The region prior runs on MAPPED cells; the gauge anchor below runs
# on ALL solved cells. The prior removes level from the mapped group and the
# anchor hands it back to everyone, unmapped cells included -- once per
# iteration, 276 iterations. Turning this on restricts the anchor to the same
# population the prior acts on.
# ⚠ DO NOT ENABLE UNTIL THE COVERAGE REPORT IS HEALTHY. If a sport is
#   ~0% mapped, this anchors the whole solve on the OTHER sport's cells and the
#   unanchored sport is free to drift. Read the §6.1 block first.
ANCHOR_ON_MAPPED_ONLY = False

# Optional CSV (state,cells) from reference_fit.py. When present the coverage
# report prints a mapped/reference RATIO instead of a bare count, which is the
# actual §6.1 test. Absent -> raw counts, no crash.
REGION_REF_COUNTS_CSV = None

# Column order from speed_ratings_db.COLUMNS.
_RID, _PID, _NORM, _GRADE, _SRC, _SCHOOL, _DATE, _SPORT, _VENUE, _GENDER = range(10)
_DIST = 10          # speed_ratings_db.COLUMNS: dist_m (issue 148)
_MEETCLASS = 11     # speed_ratings_db.COLUMNS: meet_class (issue #22)
_TIME = 12          # speed_ratings_db.COLUMNS: time_seconds (the raw time)
_TEAM = 13          # speed_ratings_db.COLUMNS: team_id (anet)
_SLUG = 14          # speed_ratings_db.COLUMNS: team_slug (tfrrs)
_ANET_LEVELS = None


_CLUB_PROS = None


def loadClubPros():
    """({team_id: n}, {normalised school: n}) of teams with professionals,
    once (speed_ratings_db.loadClubPros)."""
    global _CLUB_PROS
    if _CLUB_PROS is not None:
        return _CLUB_PROS
    _CLUB_PROS = ({}, {})
    try:
        from speed_ratings_db import loadClubPros as _load, loadClubTeams
        by_team, by_school = _load()
        n_pro_t, n_pro_s = len(by_team), len(by_school)
        # ★ AND THE TEAMS THE DATA CALLS CLUBS: no school grade on their
        #   rows, no college slug, not in the directory (loadClubTeams)
        ct, cs = loadClubTeams()
        for k in ct:
            by_team[k] = max(by_team.get(k, 0), 1)
        for k in cs:
            by_school[k] = max(by_school.get(k, 0), 1)
        # ★ AND EVERY NATIONAL TEAM (owner, 2026-09-26: "A person who runs a
        #   majority of their races for their national team is not a
        #   collegiate athlete"; John Rivera, racing for Puerto Rico, was
        #   pooled high school). A national team only entered this set when
        #   pro_flag already knew one of its athletes, or when its rows were
        #   gradeless -- Puerto Rico's carry grade 12. The majority gate is
        #   unchanged (clubSeason): one national-team race in a college year
        #   is still a college year, as the owner said on 2026-09-14.
        from pool_resolve import NATIONAL_TEAMS
        for k in NATIONAL_TEAMS:
            by_school[k] = max(by_school.get(k, 0), 1)
        _CLUB_PROS = (by_team, by_school)
        print(f"[engine] clubs: {n_pro_t:,} anet teams and {n_pro_s:,} school names carry a "
              f"professional; {len(ct):,} teams and {len(cs):,} names are gradeless non-schools; "
              f"together {len(by_team):,} teams and {len(by_school):,} names (their grade 1-8 "
              f"and gradeless rows pool pro in a season raced mostly for them)")
    except Exception as exc:                                     # noqa: BLE001
        print(f"[engine] clubs with professionals unavailable ({type(exc).__name__}: {exc})")
    return _CLUB_PROS


_CLUB_MAJORITY = None


def loadClubMajority():
    """{(person_id, year)} whose rows that year are mostly on a club or a
    team with professionals, once (speed_ratings_db.loadClubMajority)."""
    global _CLUB_MAJORITY
    if _CLUB_MAJORITY is not None:
        return _CLUB_MAJORITY
    _CLUB_MAJORITY = set()
    try:
        from speed_ratings_db import loadClubMajority as _load
        levels = loadAnetLevels()
        by_team, by_school = loadClubPros()
        club_ids = [t for t, lv in levels.items() if lv == "club"]
        _CLUB_MAJORITY = _load(club_ids, list(by_team), list(by_school))
        print(f"[engine] club seasons: {len(_CLUB_MAJORITY):,} athlete-years race mostly "
              f"for a club or a team with professionals (the club rules fire only there)")
    except Exception as exc:                                     # noqa: BLE001
        print(f"[engine] club seasons unavailable ({type(exc).__name__}: {exc}) -- "
              "the club rules stay off")
    return _CLUB_MAJORITY


def clubSeason(person_id, year):
    """Does this athlete race mostly for a club or a pro team this year?
    The gate on every club rule (owner: a college runner at the Euros is
    one row on a national team, not a professional)."""
    try:
        return (int(person_id), int(year)) in loadClubMajority()
    except (TypeError, ValueError):
        return False


def teamHasPros(team_id, school):
    by_team, by_school = loadClubPros()
    try:
        if team_id is not None and int(team_id) in by_team:
            return True
    except (TypeError, ValueError):
        pass
    return bool(school) and str(school).strip().lower() in by_school


_PRO_TEAMS = None


def loadProTeams():
    """The anet team_ids build_team_pool.py calls professional, once.

    ★ THE 15-ATHLETE RULE ARRIVES HERE AND NOWHERE ELSE (2026-09-19). Team-id
      pooling was already live through loadAnetLevels/teamLevelOf -- the level,
      the team_id == 0 rule and the club-with-pros rule all reach resolvePool.
      What did not was team_pool's own verdict, most of which is "fewer than
      fifteen distinct athletes all time is not a school".

    ! EMPTY WITHOUT THE TABLE, so a run before build_team_pool.py pools
      exactly as it did before and nothing has to be sequenced by hand.
    """
    global _PRO_TEAMS
    if _PRO_TEAMS is not None:
        return _PRO_TEAMS
    _PRO_TEAMS = set()
    # ⚠ OPT-IN, AND THAT IS A CONSEQUENCE OF HOW THIS WAS FOUND (2026-09-19).
    #   The loader queried a column that does not exist (`pool` for `kind`), so
    #   the 15-athlete rule had never repooled a row -- it failed safe and
    #   nobody knew. Fixing the column means the rule fires for the FIRST time
    #   on the next run, which is exactly the wrong moment to introduce it:
    #   there is a bad solve to diagnose and a second simultaneous change would
    #   make the two inseparable. So it now needs XCP_TEAM_POOL=1, and the line
    #   below says which state it is in either way.
    if os.environ.get("XCP_TEAM_POOL", "") in ("", "0", "false"):
        print("[engine] team_pool: OFF (XCP_TEAM_POOL=1 to apply the "
              "15-athletes-all-time rule). Pooling as before.")
        return _PRO_TEAMS
    try:
        from speed_ratings_db import loadProTeams as _load
        ids, why = _load()
        _PRO_TEAMS = ids
        if ids:
            print(f"[engine] team_pool: {len(ids):,} anet teams adjudicated "
                  f"PROFESSIONAL; their rows leave the school pools")
            for reason, n in sorted(why.items(), key=lambda kv: -kv[1])[:6]:
                print(f"            {n:>8,}  {reason}")
        else:
            print("[engine] team_pool: no table (or no pro teams) — pooling "
                  "as before; run engine/build_team_pool.py to enable the "
                  "15-athletes-all-time rule")
    except Exception as exc:                                     # noqa: BLE001
        print(f"[engine] team_pool unavailable ({exc}); pooling as before")
    return _PRO_TEAMS


_UNATTACHED_LEVEL = None


def loadUnattachedRaceLevel():
    """{(sport, result_id): level} -- the ceiling of the race each UNATTACHED
    row was run in (owner, 2026-09-20). Loaded once.

    ★ THE RULE: "if a runner is unattached they should resolve to the highest
      pool in the race they're running in". An unattached entry has no school,
      so every school-based rule is blind to it and pool_resolve fell back to
      professional. The race is the evidence.

    ! EMPTY WITHOUT race_top_level, so a run before engine/level_graph.py pools
      exactly as it did before and nothing has to be sequenced by hand -- the
      same contract loadProTeams has.
    """
    global _UNATTACHED_LEVEL
    if _UNATTACHED_LEVEL is not None:
        return _UNATTACHED_LEVEL
    _UNATTACHED_LEVEL = {}
    try:
        from speed_ratings_db import loadUnattachedRaceLevel as _load
        got = _load()
        _UNATTACHED_LEVEL = got
        if got:
            from collections import Counter
            by = Counter(got.values())
            print(f"[engine] unattached rows with a race ceiling: {len(got):,}")
            for lvl, n in by.most_common():
                print(f"            {n:>10,}  {lvl}")
        else:
            print("[engine] race_top_level: no table (or no rows) — unattached "
                  "rows stay professional; run engine/level_graph.py --write")
    except Exception as exc:                                     # noqa: BLE001
        print(f"[engine] race_top_level unavailable ({exc}); unattached rows "
              f"pool as before")
    return _UNATTACHED_LEVEL


def loadAnetLevels():
    """{anet team_id: level name}, once; prints the code table. Empty when
    the database has no anet_team (a pack then pools as before)."""
    global _ANET_LEVELS
    if _ANET_LEVELS is not None:
        return _ANET_LEVELS
    _ANET_LEVELS = {}
    try:
        from speed_ratings_db import loadTeamLevels, printTeamLevels
        by_team, meaning, rows = loadTeamLevels()
        if rows:
            printTeamLevels(meaning, rows)
        _ANET_LEVELS = by_team
        print(f"[engine] team levels: {len(by_team):,} anet teams carry a level "
              f"({sum(1 for v in by_team.values() if v == 'club'):,} clubs, "
              f"{sum(1 for v in by_team.values() if v == 'college'):,} colleges)")
    except Exception as exc:                                     # noqa: BLE001
        print(f"[engine] team levels unavailable ({type(exc).__name__}: {exc})")
    return _ANET_LEVELS


# ------------------------------------------------------------------ #
# CHUNK 0a -- THE ROW ON THE SCALE OF THE POOL IT IS RATED IN
# ------------------------------------------------------------------ #
#
# ★ THE 230 RATINGS (Leo and Lex Young, 2023 HS mile final: six seniors at
#   141-144 and the twins at 230-232 on the same times). A rating is
#   100 * pool_mean / normalized_time, and the pool mean is the pool's --
#   hs_m on a 5000 m scale, college_m on 8000 m. The backfill normalised
#   those rows as hs_m (its season verdict was not unanimous, so grade 12
#   -> hs_m) and the pack resolved the season college_m, so the row's
#   number was on one scale and its pool mean on another: x1.61 for free.
#
# ★ THE PACK IS WHERE THE RATED POOL IS DECIDED, SO THE PACK IS WHERE THE
#   SCALE HAS TO FOLLOW IT. engine/anchor_repair.py rewrites the column in
#   the database toward the pool the LAST go-live rated the row in, at step
#   5; a pack built before that repair, or a pool that resolves differently
#   this run, still reaches the solve mismatched, and every --from 8 run
#   reuses an old pack. Here the check is on the pool decided this run, in
#   memory, for every row, and the pack is consistent by construction.
#
#   Same arithmetic as anchor_repair: the stored value is t * factor(d,
#   pool_it_was_on) * (everything else the backfill applied). Only the pool
#   factor is swapped -- new = stored * factor(d, rated) / factor(d, was) --
#   so weather, geometry and era corrections survive untouched. A row whose
#   stored value no pool reproduces within IDENTIFY_TOL is left alone and
#   counted (census 'scale_not_identified'): a guess is not a repair.
_SCALE_TOL = 0.10          # anchor_check.TOLERANCE: off by this much is wrong
# ! THE NEAREST SCALE, IF IT IS NEAR AND ALONE (2026-09-14). A 3% match
#   missed rows whose stored value carries a weather or era correction of
#   more than that; pool scales sit 21-65% apart, so the nearest one within
#   8% is not ambiguous when the next is at least 10% further away.
_SCALE_IDENTIFY_TOL = 0.08    # nearest pool factor must reproduce the stored value this closely
_SCALE_IDENTIFY_GAP = 0.03    # ... and no factor of a different size may be nearly as close
_SCALE_SAME_SIZE = 0.05       # factors within 5% (hs_m/hs_f, XC/TF of one pool) count as one scale
_SCALE_POOLS = ("elem_m", "elem_f", "elem_unknown_gender", "ms_m", "ms_f",
                "ms_unknown_gender", "hs_m", "hs_f", "hs_unknown_gender",
                "college_m", "college_f", "college_unknown_gender", "pro_m", "pro_f")
_scaleFactorCache = {}


def _scaleFactor(dist, pool, sport):
    """normalizeTime's bare multiplier for (distance, pool, sport), cached
    on the rounded distance: no season, weather, geometry or course."""
    key = (int(round(dist)), pool, sport)
    f = _scaleFactorCache.get(key, False)
    if f is False:
        from normalize_distance import normalizeTime
        try:
            got = normalizeTime(1000.0, float(dist), pool, sport=sport)
        except Exception:                                    # noqa: BLE001
            got = None
        f = (got / 1000.0) if got else None
        _scaleFactorCache[key] = f
    return f


# ★ THE SCALE A POOL IS RATED ON (owner, 2026-09-25: Nuguse at ~208 against
#   147.5 for his last college season; "it shouldn't be 200 in hs-equivalent
#   land... and especially not in pro land"). A pro row is divided into the
#   COLLEGE pool's mean (pair_write_results._proScaleMap) -- a mean held on
#   the college anchor, 8000 m for men -- so the row has to be on that anchor
#   too. Rescaling it onto pro_m's own scale left it on the 5000 m default:
#   his dump read normalized_time ~770 on a 3:29 1500 (a 5K-equivalent)
#   beside ~1,340 on his college races, and every pro race came out ~1.7x.
def _scalePool(pool):
    """The pool whose anchor a row rated in `pool` must sit on: its college
    twin for a pro pool, itself for everything else."""
    if pool and str(pool).startswith("pro_"):
        return "college_" + str(pool)[4:]
    return pool


def rescaleToPool(norm, time_s, dist, pool, sport):
    """(normalized_time on `pool`'s scale, tag). tag: None when the stored
    value already is (or cannot be checked), 'rescaled' when it was moved
    from another pool's identified scale, 'scale_not_identified' when it
    is off and no pool reproduces it."""
    try:
        t = float(time_s) if time_s is not None else 0.0
        d = float(dist) if dist is not None else 0.0
        nt = float(norm)
    except (TypeError, ValueError):
        return norm, None
    if t <= 0 or d <= 0 or nt <= 0:
        return norm, None
    f_to = _scaleFactor(d, _scalePool(pool), sport)
    if not f_to:
        return norm, None
    ratio = nt / (t * f_to)
    if abs(ratio - 1.0) <= _SCALE_TOL:
        return norm, None
    # which scale is it on? the nearest pool factor, if it is near and no
    # other factor of a different size is nearly as near
    cands = []
    for sp in (sport, "XC" if sport == "TF" else "TF"):
        for p in _SCALE_POOLS:
            f = _scaleFactor(d, p, sp)
            if not f:
                continue
            cands.append((abs(nt / (t * f) - 1.0), f))
    if not cands:
        return norm, "scale_not_identified"
    cands.sort()
    best_off, best = cands[0]
    if best_off > _SCALE_IDENTIFY_TOL:
        return norm, "scale_not_identified"
    for off, f in cands[1:]:
        if abs(f / best - 1.0) > _SCALE_SAME_SIZE and off < best_off + _SCALE_IDENTIFY_GAP:
            return norm, "scale_ambiguous"
    return nt * f_to / best, "rescaled"


# ------------------------------------------------------------------ #
# CHUNK 0b — MEMOISED POOL LOOKUP
# ------------------------------------------------------------------ #

# _poolCache
# Purpose: poolFor does string work and a dict lookup. Called once per row over
#          ~150M rows (XC 34M + TF 121M) that is minutes of pure overhead. But
#          its inputs are LOW CARDINALITY: ~20 grades x 3 genders x 2 sources x
#          46k schools, and school is only consulted when the grade is unusable.
#          So memoise on the exact argument tuple.
_poolCache = {}

# (person_id, season) pairs that pro_flag.py identified as professional.
#
# ★ A SEASON, NOT A PERSON. Sadie Engelhardt forwent a high school outdoor
#   season to race professionally and then went to college; Cooper Lutkenhaus
#   was a high schooler through 2025 and a professional in 2026. An
#   athlete-level flag gets both wrong. See pro_flag.py for how the set is
#   built -- seeded on Diamond League / World Championship / Olympic fields,
#   propagated through races whose fields are >=70% confirmed professionals,
#   and vetoed by NCAA participation, since turning professional forfeits
#   eligibility.
#
# ★ REPOOLED, NOT DROPPED. A flagged season moves to pro_m / pro_f rather
#   than being discarded: the professionals stop dragging pool_mean in the
#   school pools -- which IS the 100 point -- and they get rated against each
#   other instead of being thrown away.
# ============================================================== #
#  THE SANITY BAND, AS A PACE
#
#  ★ A BAND ON normalized_time STOPPED BEING ONE NUMBER. Since per-pool
#    anchors, ms normalises to 3200 and hs to 5000, so the same seconds
#    describe a different performance in each pool. The loader's old
#    600-3600 was an hs band applied to everybody.
#
#    Measured: Luke Surface, corroborated grade 8, ran six 2025 XC races
#    normalising to 552-596 -- all six under the floor, all six dropped, and
#    an athlete who rated 141 the previous year vanished from the board.
#    Corpus-wide, 287 rows sat under that floor and every one was grade 6, 7
#    or 8; grades 9-12 had none. It was deleting the fastest middle schoolers
#    and nobody else.
#
#  ★ SO THE BAND IS EXPRESSED AS SECONDS PER METRE OF THE POOL'S OWN ANCHOR,
#    which is anchor-independent by construction. At the old 5000 anchor it
#    reproduces 600 and 3600 exactly, so hs behaviour is unchanged; ms gets
#    384-2304 and elem 290-1738, each the same PACE.
#
#      0.12 s/m = 2:00/km   -- faster than any human over any distance
#      0.72 s/m = 12:00/km  -- slower than walking
#
#  ! DEFINED IN normalize_distance NOW, because the band stopped being the
#    engine's private business: every row gets a rating (fill_ratings), so
#    the BOARDS gate on this same band, and two copies of the numbers would
#    drift. The aliases keep this file's forty readers unchanged.
from normalize_distance import PACE_FLOOR as _PACE_FLOOR      # noqa: E402
from normalize_distance import PACE_CEIL as _PACE_CEIL        # noqa: E402
_band_cache = {}


# poolBand
# Purpose:   (lo, hi) normalized_time bounds for one pool.
# Arguments: pool -- as poolOf returns it, e.g. 'ms_m|XC' or 'ms_m'.
# Output:    tuple of floats. Memoised: called once per packed row.
def poolBand(pool):
    band = _band_cache.get(pool)
    if band is None:
        from normalize_distance import targetFor
        # on the anchor the row was just rescaled onto (_scalePool)
        t = targetFor(_scalePool(pool))
        band = (_PACE_FLOOR * t, _PACE_CEIL * t)
        _band_cache[pool] = band
    return band


_PRO_SEASONS = None
_PRO_LEVELS = ("college", "hs", "ms", "elem")

# person_id -> first season with a COLLEGIATE race.
#
# ★ THIS DIRECTION IS GENUINELY ABSORBING, unlike 'pro'. An athlete who has
#   raced for a college cannot afterwards be a high schooler or a middle
#   schooler. It catches the post-collegiate athletes who never race a
#   Diamond League field and so never seed in pro_flag -- a club runner with
#   four races rating 137 in a school pool.
#
# ★ BUILT FROM poolFor, NOT FROM MEET NAMES. college_flag.py resolves every
#   distinct (grade, source, school) through poolFor -- the same school_levels
#   lookup the engine pools on -- so 'is this an actual university' has ONE
#   definition. Keying on meet names containing 'ncaa' missed every conference
#   meet, regional and dual, which is most collegiate racing.
_COLLEGE_FIRST = None
_SCHOOL_LEVELS = ("hs", "ms", "elem")

# person_id -> first season raced as a JUNIOR OR SENIOR.
#
# ★ GRADE 11-12, NOT 'RACED AN HS MEET'. A seventh grader can race a high
#   school open or frosh/soph section, so appearing in an hs pool proves
#   nothing. Being recorded as an eleventh or twelfth grader does: nobody is
#   an upperclassman and then a middle schooler.
_UPPER_FIRST = None

# (person_id, season) pairs whose GRADE field cannot be trusted.
#
# ★ poolFor step 1 is grade-first and returns BEFORE the school is ever
#   consulted. Ben Bidois, a New Zealand international, carries NZ Year
#   levels 1-6 in the grade column -- every value a legal US grade,
#   advancing one per year -- so he was pooled elem_m and rated 177-180,
#   the highest ratings in the corpus, as an ELEMENTARY schooler.
#
#   grade_sanity.py finds these by ABSOLUTE ability: a 15-minute 5k is not
#   a fifth grader, and ability in seconds does not depend on the pool the
#   way a rating does. For a listed SEASON the grade is skipped and
#   levelForSchool decides instead.
#
# ⚠ PER SEASON. An earlier version flagged the PERSON, which discarded
#   good grades along with bad ones: an athlete with NZ Year levels 4-6 in
#   2021-2023 and Fr/So/Jr at La Salle from 2024 lost the collegiate grades
#   too, fell through to the school, and 'La Salle' resolves to hs -- there
#   are nine La Salle HIGH SCHOOLS in this corpus -- putting a college
#   junior in hs_m at 134.2.
_GRADE_UNTRUSTED = None
_SUB_HS_LEVELS = ("ms", "elem")


# loadProSeasons
# Purpose:   fill _PRO_SEASONS from the pro_athlete_season table.
# Detail:    an absent table is not an error -- it means pro_flag.py has not
#            been run, and the engine should pool exactly as it did before.
_SEASON_LEVELS = None


# loadSeasonLevels
# Purpose:   {(person_id, academic_year): level} from athlete_season_level.
# Output:    dict; empty if the table was never built.
#
# ★ WHY THIS EXISTS. poolFor takes a `season_level` argument that enables its
#   step 0 -- the grade-vs-race arbitration -- and NOTHING IN THE ENGINE WAS
#   PASSING IT. backfill_normalize.py reads athlete_season_level and passes it;
#   the engine did not, so with season_level=None poolFor behaved exactly as it
#   did before the table existed.
#
#   The cost is not abstract. The engine keys athletes on (person_id, pool), so
#   a season whose races disagree about level SPLITS ONE ATHLETE INTO TWO
#   half-solved unknowns. Alexis Paterna, grade 11 at Exeter, came out as BOTH
#   hs_f AND college_f in 2023 and again in 2025, and the college_f fragment --
#   fitted on a couple of races -- is what surfaced on the college board.
#   athlete_season_level called all three of her seasons `hs`, unanimously. It
#   was simply never asked.
#
# ⚠ ACADEMIC YEAR, NOT CALENDAR YEAR. athlete_season_level.ay starts in JULY
#   (so a fall XC season and the following spring track season share one
#   label), but this engine uses the CALENDAR year as its season -- see the
#   note in poolOf about spring track and autumn college sharing a year.
#   For XC (Aug-Dec) the two agree; for TF (Feb-Jul) the academic year is one
#   LOWER. Looking up by calendar year would therefore miss on every single
#   track row while appearing to work on cross country.
def loadSeasonLevels():
    global _SEASON_LEVELS
    if _SEASON_LEVELS is not None:
        return _SEASON_LEVELS
    _SEASON_LEVELS = ({}, {})
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('athlete_season_level')")
            if cur.fetchone()[0] is None:
                print("[engine] athlete_season_level not found -- "
                      "no season-level pooling")
                return _SEASON_LEVELS
            # level IS NULL means "considered and undecided", which is written
            # deliberatelyrather than omitted. Skipping those here keeps the
            # dict to seasons that actually have a verdict.
            # ⚠ THE KEY IS (person_id, ay, sport) NOW. Selecting without
            #   `sport` returns up to THREE rows per season -- 'XC', 'TF' and
            #   'ALL' -- and this dict comprehension would have silently kept
            #   whichever arrived last. No error, no warning, an arbitrary
            #   verdict per athlete. That is the worst shape a bug can take
            #   here, because every rating downstream is pool-relative.
            #
            # ★ 'ALL' GOES IN A SEPARATE DICT so poolOf can ask for its sport
            #   first and fall back. A season with no votes for one sport is
            #   then exactly as well served as it was before the table gained
            #   a sport dimension.
            cur.execute("SELECT person_id, ay, sport, level "
                        "FROM athlete_season_level WHERE level IS NOT NULL")
            by_sport, combined = {}, {}
            for p, a, sp, lvl in cur.fetchall():
                key = (int(p), int(a))
                if sp == "ALL":
                    combined[key] = lvl
                else:
                    by_sport[(key, sp)] = lvl
            _SEASON_LEVELS = (by_sport, combined)
        print(f"[engine] {len(_SEASON_LEVELS[0]) + len(_SEASON_LEVELS[1]):,} "
              f"season levels ({len(_SEASON_LEVELS[0]):,} per-sport, "
              f"{len(_SEASON_LEVELS[1]):,} combined)")
    except Exception as exc:
        print(f"[engine] athlete_season_level unavailable ({exc}) -- "
              f"no season-level pooling")
    return _SEASON_LEVELS


# _academicYear
# Purpose:   the season label athlete_season_level is keyed on.
# Detail:    season_year's academic year (August seam). season_level's
#            _academicYearExpr writes `ay` from the same module, so the
#            lookup and the table cannot disagree. season_year is imported
#            lazily because this function is also called from contexts that
#            never touch the season tables.
def _academicYear(race_date):
    """The season a race belongs to. Delegates -- it does not decide.

    ⚠ THIS USED TO HARD-CODE `month >= 7`, AND season_year.py HARD-CODED ITS
      OWN SEPARATE RULE. Two copies of a boundary do not stay two copies of
      the same boundary: this one said July, that one said October for TF and
      February for XC, and grade_sanity's SQL said July again. Three clocks,
      and every join between tables on different ones silently matched
      nothing. season_year owns the seam now; everything here follows it.
    """
    if race_date is None:
        return None
    from season_year import seasonYearFor
    return seasonYearFor(None, race_date)


def loadProSeasons():
    global _PRO_SEASONS
    if _PRO_SEASONS is not None:
        return _PRO_SEASONS
    _PRO_SEASONS = set()
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('pro_athlete_season')")
            if cur.fetchone()[0] is None:
                print("[engine] pro_athlete_season not found -- "
                      "no pro repooling")
                return _PRO_SEASONS
            cur.execute("SELECT person_id, season FROM pro_athlete_season")
            _PRO_SEASONS = {(int(p), int(s)) for p, s in cur.fetchall()}
        print(f"[engine] pro repooling: {len(_PRO_SEASONS):,} "
              f"professional athlete-seasons")
    except Exception as exc:
        print(f"[engine] pro_athlete_season unavailable ({exc}) -- "
              f"no pro repooling")
    return _PRO_SEASONS


# ★ THE READER IS engine/pro_ability.py, NOT A COPY HERE. The site asks
#   the same question from three more call sites and must get the same
#   answer; a second implementation of a pool decision is the failure
#   pool_resolve's header exists to record. This module keeps only the
#   thin delegation so poolOf reads the same as its neighbours.
def loadProAbility():
    from pro_ability import loadProAbility as _load
    return _load()


def proAbilityFor(gkey):
    """True / False / None for one (person_id, academic_year) tuple."""
    from pro_ability import proAbilityFor as _for
    if gkey is None:
        return None
    return _for(gkey[0], gkey[1])


# loadNcaaFirst
# Purpose:   fill _COLLEGE_FIRST from college_first_season.
# Detail:    absent table is not an error -- pool exactly as before.
def loadCollegeFirst():
    global _COLLEGE_FIRST
    if _COLLEGE_FIRST is not None:
        return _COLLEGE_FIRST
    _COLLEGE_FIRST = {}
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('college_first_season')")
            if cur.fetchone()[0] is None:
                print("[engine] college_first_season not found -- "
                      "no college gate")
                return _COLLEGE_FIRST
            cur.execute("SELECT person_id, first_date "
                        "FROM college_first_season")
            _COLLEGE_FIRST = {int(p): dt for p, dt in cur.fetchall()}
        print(f"[engine] college gate: {len(_COLLEGE_FIRST):,} athletes "
              f"with a collegiate race")
    except Exception as exc:
        print(f"[engine] college_first_season unavailable ({exc})")
    return _COLLEGE_FIRST


# loadGradeUntrusted
# Purpose:   fill _GRADE_UNTRUSTED from grade_untrusted.
def loadGradeUntrusted():
    """{(person_id, academic_year): (fixed_grade, fixed_level, method)}.

    ⚠ THE KEY IS THE ACADEMIC YEAR. grade_sanity writes one row per academic
      year; poolOf looks it up with _academicYear(race_date). Feeding this a
      calendar year misses on every spring race, which is precisely the bug
      that split 2,979 athlete-seasons across ms and hs.

    ★ THE ANSWER, NOT JUST THE FLAG. grade_fix carries what grade_sanity
      RESOLVED: the majority of the athlete's own per-race grades, or the
      level of the field they raced, or pro when no race they ran carried a
      grade at all. Membership still means "the recorded grade is not the one
      to use", so every existing `key in loadGradeUntrusted()` test keeps
      working against a dict exactly as it did against a set.

    ⚠ FALLS BACK TO grade_untrusted. That table still exists and still holds
      the same keys, so an engine running against a database where
      grade_sanity has not been re-run behaves as before rather than losing
      the gate entirely.
    """
    global _GRADE_UNTRUSTED
    if _GRADE_UNTRUSTED is not None:
        return _GRADE_UNTRUSTED
    _GRADE_UNTRUSTED = {}
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('grade_fix')")
            if cur.fetchone()[0] is not None:
                # ! THE METHOD RIDES ALONG, AND EVERY UNPACK BELOW COUNTS.
                #   rule 6 writes an explicit 'no_evidence' verdict, which is
                #   identical to a field verdict in (grade, level) -- both are
                #   (None, None). Only the method distinguishes them, and
                #   resolvePool has to see it.
                cur.execute("SELECT person_id, season, grade, level, method "
                            "FROM grade_fix")
                _GRADE_UNTRUSTED = {(int(p), int(s)): (g, lv, m)
                                    for p, s, g, lv, m in cur.fetchall()}
                methods = len(_GRADE_UNTRUSTED)
                fixed = sum(1 for g, lv, m in _GRADE_UNTRUSTED.values()
                            if g is not None)
                print(f"[engine] grade gate: {methods:,} athlete-seasons "
                      f"resolved ({fixed:,} with a corrected grade)")
                return _GRADE_UNTRUSTED

            cur.execute("SELECT to_regclass('grade_untrusted')")
            if cur.fetchone()[0] is None:
                return _GRADE_UNTRUSTED
            cur.execute("SELECT person_id, season FROM grade_untrusted")
            _GRADE_UNTRUSTED = {(int(p), int(s)): (None, None, None)
                                for p, s in cur.fetchall()}
        print(f"[engine] grade gate: {len(_GRADE_UNTRUSTED):,} "
              f"athlete-seasons whose grade is ignored (no grade_fix)")
    except Exception as exc:
        print(f"[engine] grade gate unavailable ({exc})")
    return _GRADE_UNTRUSTED


# loadUpperclassFirst
# Purpose:   fill _UPPER_FIRST from upperclass_first_season.
def loadUpperclassFirst():
    global _UPPER_FIRST
    if _UPPER_FIRST is not None:
        return _UPPER_FIRST
    _UPPER_FIRST = {}
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('upperclass_first_season')")
            if cur.fetchone()[0] is None:
                return _UPPER_FIRST
            cur.execute("SELECT person_id, first_date "
                        "FROM upperclass_first_season")
            _UPPER_FIRST = {int(p): dt for p, dt in cur.fetchall()}
        print(f"[engine] upperclass gate: {len(_UPPER_FIRST):,} "
              f"athletes")
    except Exception as exc:
        print(f"[engine] upperclass_first_season unavailable ({exc})")
    return _UPPER_FIRST


# hsPool / collegePool / proPool moved to pool_resolve, beside the stage-2
# gate that is their only caller. Re-exported here so nothing else that
# imports them from this module breaks.
from pool_resolve import resolvePool, memoPoolFor, hsPool, collegePool, proPool

# The memoised poolFor handed to resolvePool. _poolCache is still this
# module's dict, so its lifetime and its memory are unchanged -- only the key
# gained season_level, which is what lets it hit on rows it used to skip.
_cachedPoolFor = memoPoolFor(_poolCache)


# poolOf
# Purpose:   the pool for one row, sport-namespaced.
# Arguments: grade, gender, source, school, sport.
# Output:    "hs_m|XC" etc., or None when the row cannot be pooled.
# Detail:    the sport is APPENDED, not passed into poolFor -- pools are a
#            (level, gender) concept and the fitters already key their curves
#            `pool|sport`. Keeping the same convention means an athlete's XC and
#            TF abilities are solved independently and never contaminate.
def poolOf(grade, gender, source, school, sport, merge=False,
           person_id=None, season=None, race_date=None, team_level=None,
           team_has_pros=False, no_team=False, team_pro=False,
           race_top_level=None):
    """The pool for one row, sport-namespaced. "hs_m|XC", or None.

    ★ THE DECISION ITSELF NOW LIVES IN pool_resolve.resolvePool, SHARED WITH
      THE SITE. This function's only job is to turn a person_id and a date
      into the five facts that decision needs, by looking them up in the dicts
      this module already holds. build_ranking_results and panels obtain the
      same five facts from LEFT JOINs and call the same function.

      Three implementations of pooling disagreed on roughly a million
      athlete-seasons. Because speed_rating is pool-relative AND poolFor picks
      the distance curve, a pooling disagreement does not shift a rating, it
      bends it differently at every distance -- one athlete came out 57 points
      apart between his 800m and his 8000m. Hence one implementation.

    The cache moved too. It is keyed on (grade, gender, source, school,
    season_level) now rather than the first four, so it no longer has to be
    bypassed whenever a season verdict applies -- which, with 29M season levels
    loaded, was most rows. See pool_resolve.memoPoolFor.
    """
    pid = None if person_id is None else int(person_id)

    # The season verdict, keyed on the ACADEMIC year (see loadSeasonLevels).
    slvl = None
    if pid is not None:
        ay = _academicYear(race_date)
        if ay is not None:
            # This sport's verdict, else the combined one.
            by_sport, combined = loadSeasonLevels()
            slvl = by_sport.get(((pid, ay), sport)) or combined.get((pid, ay))

    # Initialised alongside `untrusted`, or a row with no person_id reaches
    # the call with these unbound.
    untrusted = pro = False
    fixed_grade = fixed_level = None
    # ! None, NOT False. False is a VERDICT ("measured, and not fast
    #   enough") and would demote every row that cannot be looked up at
    #   all. A row with no person_id has no season to ask about, which is
    #   no verdict -- see pool_resolve's gate.
    pro_able = None
    if pid is not None and season is not None:
        # ! TWO TABLES, TWO CLOCKS, AND THEY ARE NOT THE SAME KEY.
        #
        #   pro_athlete_season is built on the CALENDAR year, so its lookup
        #   uses `season=` unchanged. season_year.py's "DO NOT feed this to
        #   poolOf's season=" warning is about exactly this parameter and
        #   still stands.
        #
        # ★ grade_fix IS BUILT ON THE ACADEMIC YEAR. It always reasoned that
        #   way; it now WRITES that way too, because a calendar year holds two
        #   grades and collapsing them split 2,979 athlete-seasons across ms
        #   and hs -- the spring half of a season inheriting the following
        #   autumn's grade.
        #
        #   panels.py and build_ranking_results.py were already joining this
        #   table academically and saying so in their comments. This line is
        #   what brings the engine into agreement with the site; until it
        #   moved, one table was being read on two different keys.
        # ★ ONE KEY. pro_flag now writes pro_athlete_season on the academic
        #   year too, so the two lookups finally agree and the pkey/gkey split
        #   is gone. `season=` is still accepted for callers that pass it and
        #   is no longer read here.
        gkey = (pid, ay) if ay is not None else None

        gate = loadGradeUntrusted()
        # ⚠ A ROW WITH NO PARSEABLE DATE HAS NO ACADEMIC YEAR, so it cannot be
        #   looked up and the raw grade decides. That is the old behaviour for
        #   such rows, not a new gap: _asDate already rejected them upstream.
        untrusted = gkey is not None and gkey in gate
        fixed_grade, fixed_level, grade_verdict = (
            gate.get(gkey, (None, None, None))
            if gkey is not None else (None, None, None))
        pro_set = loadProSeasons()
        pro = bool(pro_set) and gkey is not None and gkey in pro_set
        # ★ THE ABILITY GATE (owner, 2026-09-22). Tri-state; see
        #   proAbilityFor and pool_resolve's veto.
        pro_able = proAbilityFor(gkey)

    return resolvePool(
        grade, gender, source, school, sport,
        season_level=slvl,
        grade_untrusted=untrusted,
        fixed_grade=fixed_grade,
        fixed_level=fixed_level,
        grade_verdict=grade_verdict,
        # ! FOR _PRO_SEASONS, the hand-listed professionals whose school
        #   string is shared with a youth club and so cannot identify them.
        #   The SEASON goes with the person now: the list is per athlete-
        #   season, so a high school career before somebody turned
        #   professional stays a high school career.
        person_id=pid,
        season=ay,
        is_pro=pro,
        pro_ability=pro_able,
        college_first=None if pid is None else loadCollegeFirst().get(pid),
        upperclass_first=None if pid is None else loadUpperclassFirst().get(pid),
        race_date=race_date,
        merge=merge,
        poolfor=_cachedPoolFor,
        team_level=team_level,
        team_has_pros=team_has_pros,
        no_team=no_team,
        team_pro=team_pro,
        race_top_level=race_top_level)


from concurrent.futures import ThreadPoolExecutor

# One worker per physical core. os.cpu_count() reports logical cores
# (hyperthreads), and memory-bandwidth-bound work gets nothing from the second
# thread on a core -- it just adds contention.
_BINCOUNT_WORKERS = max(1, (os.cpu_count() or 4) // 2)
_BINCOUNT_POOL = ThreadPoolExecutor(max_workers=_BINCOUNT_WORKERS)


# _parallelBincount
# Purpose:   np.bincount over 58M elements, across threads.
# Detail:    np.bincount is single-threaded but RELEASES THE GIL while it runs,
#            so plain Python threads genuinely parallelise here -- this is one
#            of the few cases where they do. Each worker bincounts a contiguous
#            slice into its own length-minlength array; summing those gives the
#            same result, because a bincount is just an addition per element and
#            addition is associative.
#
#            Falls back to a single call below ~4M elements: the split, thread
#            dispatch and final sum cost more than they save on small arrays.
def _parallelBincount(codes, weights=None, minlength=0):
    if codes.size < 4_000_000 or _BINCOUNT_WORKERS == 1:
        return np.bincount(codes, weights=weights, minlength=minlength)

    bounds = np.linspace(0, codes.size, _BINCOUNT_WORKERS + 1).astype(np.int64)

    def chunk(i):
        lo, hi = bounds[i], bounds[i + 1]
        w = None if weights is None else weights[lo:hi]
        return np.bincount(codes[lo:hi], weights=w, minlength=minlength)

    parts = list(_BINCOUNT_POOL.map(chunk, range(_BINCOUNT_WORKERS)))

    total = parts[0]
    for part in parts[1:]:
        total += part
    return total


# ------------------------------------------------------------------ #
# CHUNK 1 — DECAY WEIGHT
# ------------------------------------------------------------------ #

# _asDate
# Purpose:   coerce whatever the driver handed us into a datetime.date.
# Arguments: value -- datetime.date, datetime, or "YYYY-MM-DD" string.
# Output:    date, or None if unparseable.
# Detail:    THE BUG THIS FIXES: psycopg2 returns a DATE column as datetime.date.
#            The old code called strptime() on it, which raises TypeError, was
#            caught, returned weight 0.0, and every single row was then skipped.
def _asDate(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# computeDecayWeight
# Purpose:   recency weight for one race.
# Arguments: race_date -- date or str; today -- date.
# Output:    float in (0, 1]; 0.0 for missing or future dates.
def computeDecayWeight(race_date, today: date) -> float:
    d = _asDate(race_date)
    if d is None:
        return 0.0
    days_ago = (today - d).days
    if days_ago < 0:
        return 0.0
    return math.pow(DECAY_K, days_ago)

# _daysAgo
# Purpose:   days between race_date and today, or None for an unusable date.
# Why separate from computeDecayWeight: packResults now stores the DAY COUNT and
#   derives both decay curves from it vectorised, instead of calling math.pow
#   34 million times in Python. The athlete curve and the course curve share one
#   integer per row.
def _daysAgo(race_date, today: date):
    d = _asDate(race_date)
    if d is None:
        return None
    n = (today - d).days
    return None if n < 0 else n


# _normalizeByGroup
# Purpose:   divide every weight by the LARGEST weight in its group.
# Arguments: w -- float64 weights; codes -- group id per row; n_groups.
# Output:    weights in (0, 1], with at least one 1.0 per group.
#
# WHY THIS IS NOT A HACK. A weighted mean is SCALE-INVARIANT: multiply every
# weight in a group by any c > 0 and the mean is unchanged. Decay weights only
# ever express RELATIVE recency WITHIN one athlete -- ability is a per-athlete
# quantity and poolMeans averages abilities unweighted, so the absolute size of
# an athlete's weights carries no information at all.
#
# But absolute size destroys the arithmetic. An athlete whose races are all from
# 1947 has every weight around 0.998^28770 = 1e-25, and their wsum underflows
# toward the noise floor. The old code then divided by max(wsum, 1e-12), which
# is not a guard against zero -- it is a silent rescaling of any small-but-real
# denominator, and it produced abilities of 1e-10 seconds.
#
# Normalizing per group makes wsum live in [1, n_races]. The result is
# mathematically IDENTICAL and underflow becomes structurally impossible rather
# than narrowly avoided.
#
# Syntax:    np.maximum.at(out, idx, vals) is an UNBUFFERED scatter-max: repeated
#   indices accumulate instead of overwriting, which plain `out[idx] = vals` does
#   not do. It is the max-analogue of np.bincount's sum.
def _normalizeByGroup(w, codes, n_groups):
    gmax = np.zeros(n_groups, dtype=np.float64)
    np.maximum.at(gmax, codes, w)
    denom = gmax[codes]
    return np.divide(w, denom, out=np.zeros_like(w), where=denom > 0)

import os

# Where packed arrays get cached. Same directory as the pickles.
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# saveCols / loadCols
# Purpose:   persist the packed arrays so a re-run can skip streaming.
# Detail:    packResults turns 61.6M database rows into a handful of numpy
#            arrays. The QUERY is deterministic, so for solver experiments the
#            whole stream-and-pack phase is repeated work.
#
#            np.savez, NOT savez_compressed. These are ~2GB of mostly
#            incompressible floats; compression would cost minutes of CPU to
#            save maybe 20% of disk.
#
# ⚠ THE CACHE DOES NOT KNOW WHEN THE DATABASE CHANGES. It is keyed on nothing
#   but the sport list. After a backfill, a scrape, or any edit to _xcQuery or
#   _tfQuery, DELETE IT. The age warning on load exists because a stale cache
#   fails silently -- you would be solving last week's data and it would look
#   completely normal.
def saveCols(cols, path):
    arrays = {k: v for k, v in cols.items() if isinstance(v, np.ndarray)}
    scalars = {k: v for k, v in cols.items() if not isinstance(v, np.ndarray)}

    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez(path, __scalars__=np.array([repr(scalars)]), **arrays)

    size = os.path.getsize(path) / 1e9
    print(f"[cache] wrote {path} ({size:.2f} GB, {len(arrays)} arrays)")


def loadCols(path, only=None):
    """The packed columns. `only`: the row arrays to read (the key lists and
    the scalars always come); a diagnostic that needs nine of the twelve
    arrays skips a quarter of the read."""
    import ast
    import time

    ageHours = (time.time() - os.path.getmtime(path)) / 3600.0
    print(f"[cache] loading {path} (written {ageHours:.1f}h ago)")
    if ageHours > 24:
        print(f"[cache] ⚠ THIS CACHE IS {ageHours:.0f} HOURS OLD. If the "
              f"database or the queries changed, delete it.")

    want = None if only is None else set(only) | {"athlete_keys", "course_keys"}
    with np.load(path, allow_pickle=False) as data:
        cols = {k: data[k] for k in data.files
                if k != "__scalars__" and (want is None or k in want)}
        if "__scalars__" in data.files:
            cols.update(ast.literal_eval(str(data["__scalars__"][0])))

    return cols


# _prefetched
# Purpose:   run a batch iterator in a background thread, `depth` batches
#            ahead, so the database and the row loop work AT THE SAME TIME.
#
# ★ WHY (2026-09-25). The pack's own timer: "5127s waiting on the database,
#   2426s resolving pools, 652s the rest of the row loop" -- 8,274 s spent
#   strictly one after the other. psycopg2 releases the GIL while it waits on
#   the server, so a fetcher thread can pull batch n+1 while this thread
#   resolves batch n; the pack then costs about the larger of the two rather
#   than their sum.
#
# ! SAME ROWS, SAME ORDER. One producer, one bounded FIFO, one consumer:
#   nothing is reordered, so the codes, the stable sort by athlete and the
#   packed arrays are identical. The streaming generator and its connection
#   live entirely in the producer thread.
# ! AN ERROR IN THE PRODUCER IS RAISED IN THE CONSUMER, at the point the
#   failed batch would have arrived -- a dead fetcher must never look like
#   the end of the data.
# ! XCP_PACK_PREFETCH=0 turns it off.
def _prefetched(it, depth=4):
    if os.environ.get("XCP_PACK_PREFETCH", "1") in ("0", "false"):
        return it
    import queue
    import threading
    q = queue.Queue(maxsize=depth)
    _END = object()

    def produce():
        try:
            for batch in it:
                q.put(batch)
        except BaseException as exc:            # noqa: BLE001 -- re-raised below
            q.put(("__prefetch_error__", exc))
            return
        q.put(_END)

    threading.Thread(target=produce, name="pack-prefetch", daemon=True).start()

    def consume():
        while True:
            item = q.get()
            if item is _END:
                return
            if (isinstance(item, tuple) and len(item) == 2
                    and item[0] == "__prefetch_error__"):
                raise item[1]
            yield item
    return consume()


# packOrLoad
# Purpose:   packed arrays, from cache when available.
# Arguments: sports -- tuple of sports to stream; today; merge; cache -- bool.
def packOrLoad(sports, today, merge, cache):
    path = os.path.join(_CACHE_DIR, f"packed_{'_'.join(sports)}.npz")

    import time
    if cache and os.path.exists(path):
        t0 = time.time()
        cols = loadCols(path)
        print(f"[time] cache load: {time.time() - t0:.1f}s")
        return cols

    # ★ MEASURED, not estimated. This phase was never instrumented, so its cost
    # was unknown -- and an unknown phase cannot be weighed against the solve
    # when deciding what is worth optimising.
    t0 = time.time()
    stream = _prefetched(chain(*(streamResults(s) for s in sports)))
    cols = packResults(stream, today, merge=merge)
    print(f"[time] stream + pack: {time.time() - t0:.1f}s")
    attachCourseCoords(cols)
    attachCourseGeometry(cols)

    if cache:
        saveCols(cols, path)

    return cols


def attachCourseCoords(cols):
    """course_lat / course_lon per course key on the pack (the place prior,
    speed_ratings_db.loadCourseCoords). A failure leaves the pack without
    them and says so: the engine then runs with no place prior."""
    if cols is None or "course_keys" not in cols:
        return
    try:
        from speed_ratings_db import loadCourseCoords
        lat, lon = loadCourseCoords(cols["course_keys"])
        cols["course_lat"] = np.asarray(lat, dtype=np.float64)
        cols["course_lon"] = np.asarray(lon, dtype=np.float64)
        n = len(cols["course_keys"])
        have = int(np.isfinite(cols["course_lat"]).sum())
        print(f"[engine] course coordinates: {have:,} of {n:,} course keys "
              f"({100.0 * have / max(n, 1):.0f}%) carry a (lat, lon) for the place prior")
    except Exception as exc:                                     # noqa: BLE001
        print(f"[engine] course coordinates unavailable ({type(exc).__name__}: {exc}) "
              "-- the pack carries none and the bracket engine runs without a place prior")


def attachCourseGeometry(cols):
    """track_length / track_type / track_indoor per course key -- the reference
    class the difficulty pin uses (engine/track_geometry.py, plan §1).

    ! THE SAME SHAPE AND THE SAME FAILURE MODE AS attachCourseCoords: per
      COURSE KEY rather than per row, and a failure leaves the pack without
      them and says so. The engine then falls back to the outdoor-mean gauge,
      which is what it did before this existed."""
    if cols is None or "course_keys" not in cols:
        return
    try:
        from speed_ratings_db import loadCourseGeometry
        import track_geometry as tg
        length, ttype, indoor = loadCourseGeometry(cols["course_keys"])
        cols["track_length"] = np.asarray(length, dtype=np.float64)
        # ⚠ A STRING ARRAY, NOT object. loadCols reads the pack with
        #   allow_pickle=False, so an object array would save and then fail to
        #   load -- silently costing the geometry on every later run. "" is the
        #   unknown, which isBanked() already treats as not-banked.
        cols["track_type"] = np.asarray([("" if t is None else str(t))
                                         for t in ttype], dtype="U32")
        cols["track_indoor"] = np.asarray(indoor, dtype=np.float64)
        n = len(cols["course_keys"])
        have = int(np.isfinite(cols["track_length"]).sum())
        ref = int(tg.flatOutdoor400Mask(cols["track_length"], cols["track_type"],
                                        cols["track_indoor"]).sum())
        print(f"[engine] track geometry: {have:,} of {n:,} course keys carry a "
              f"track_length; {ref:,} are flat outdoor 400s (the reference "
              f"class for the difficulty pin)")
    except Exception as exc:                                     # noqa: BLE001
        # ⚠⚠ THIS except IS WHY NOBODY KNEW (2026-09-20). loadCourseGeometry
        #    named meets_tf.meet_date, which does not exist on this schema, so
        #    every pack build raised UndefinedColumn here and printed ONE line
        #    among thousands. The pack then shipped with no track_length,
        #    gauge=flat400 silently became gauge=outdoor, and the reference pin
        #    the owner asked for on 2026-09-19 had never once run.
        #
        # ★ SO IT SHOUTS, AND IT SAYS WHAT IT COSTS. The catch stays -- a pack
        #   build must not die over a diagnostic column -- but a reader
        #   skimming the log now cannot miss it, and XCP_GAUGE=flat400 will
        #   refuse to run on the result rather than quietly re-gauge.
        import traceback
        print("[engine] ⚠⚠ TRACK GEOMETRY UNAVAILABLE -- THE PACK WILL CARRY "
              "NONE.", flush=True)
        print(f"[engine]    {type(exc).__name__}: {exc}", flush=True)
        print("[engine]    gauge=flat400 CANNOT RUN on this pack; the solve "
              "will refuse it.", flush=True)
        print("[engine]    Fix this before the solve, or the run is on the "
              "outdoor-mean gauge.", flush=True)
        traceback.print_exc()


# ------------------------------------------------------------------ #
# CHUNK 2 — PACK ROWS INTO COLUMNS
# ------------------------------------------------------------------ #

# packResults
# Purpose:   stream DB batches straight into columnar numpy arrays.
# Arguments: batches -- iterable of list[tuple]; today -- for decay weights.
# Output:    dict of arrays + decode tables + a drop census.
# Detail:    NEVER materialises the whole corpus as Python objects. TF alone is
#            ~121M rows; a list of tuples that size is >100GB. We append small
#            typed chunks and concatenate once at the end.
#
#            The athlete key is (person_id, pool) with the sport already inside
#            the pool string, so an athlete's XC and TF abilities are separate
#            unknowns. The venue key is namespaced by sport for the same reason.
def packResults(batches, today, merge=False):
    census = defaultdict(int)
    # Counts what the TF rollover moved. Folded into `census` at the end so it
    # rides the existing "[engine] ... census: {...}" line rather than adding a
    # new print -- a ZERO here means the rule never fired.
    _rollover = RolloverLedger()
    a_lookup, a_uniq = {}, []
    v_lookup, v_uniq = {}, []
    chunks = []

    # ★ WHERE THE PACK'S TWO HOURS GO (2026-09-22: "stream + pack: 7324.8s",
    #   about 120 us a row, and nothing said whether that was Postgres or this
    #   loop). Three clocks: waiting for the next batch from the database, the
    #   pool resolution (team level, club gates, poolOf), and everything else
    #   in the row loop. Two perf_counter pairs a row is ~0.3 us against ~120.
    import time as _time
    _clk = _time.perf_counter
    t_fetch = t_pool = t_loop = 0.0
    n_rows = 0
    _it = iter(batches)

    while True:
        _t0 = _clk()
        rows = next(_it, None)
        t_fetch += _clk() - _t0
        if rows is None:
            break
        _tb = _clk()
        _pool_b = t_pool
        rid, acode, vcode, norm, wt, scode, doys, yrs = [], [], [], [], [], [], [], []
        dists = []
        mcls = []                                # meet_class per row (issue #22)
        for r in rows:
            n_rows += 1
            # ⚠ DATE FIRST. The pool now depends on the SEASON, because a
            #   professional flag is per athlete-season. A row with an
            #   unusable date is therefore rejected before the pool is
            #   decided -- the only behavioural change is which census
            #   counter fires for a row that would fail both tests.
            d = _asDate(r[_DATE])
            if d is None or (today - d).days < 0:
                census["bad_or_future_date"] += 1
                continue
            _tp = _clk()
            team_level, has_pros, no_team = None, False, False
            team_pro = False
            if len(r) > _SLUG:
                from pool_resolve import teamLevelOf, UNATTACHED_TEAM_ID
                team_level = teamLevelOf(r[_TEAM], r[_SLUG], loadAnetLevels())
                # ★ NO TEAM AT ALL (owner, 2026-09-16: "If any school has
                #   id == 0 we should just put them in pro"), and ungated --
                #   see pool_resolve.UNATTACHED_TEAM_ID. Counted, because it
                #   is a rule whose cost has to stay visible.
                try:
                    no_team = int(r[_TEAM]) == UNATTACHED_TEAM_ID
                except (TypeError, ValueError):
                    no_team = False
                if no_team:
                    census["no_team_pro"] += 1
                if team_level:
                    census[f"team_level_{team_level}"] += 1
                # ★ THE TEAM'S OWN VERDICT, BY TEAM ID. Counted because a
                #   rule that repools rows out of the school boards must have
                #   its cost visible in the census, exactly like no_team_pro.
                try:
                    team_pro = int(r[_TEAM]) in loadProTeams()
                except (TypeError, ValueError):
                    team_pro = False
                if team_pro:
                    census["team_pool_pro"] += 1
                has_pros = teamHasPros(r[_TEAM], r[_SCHOOL])
                # ★ THE CLUB RULES FIRE ONLY IN A SEASON RACED MOSTLY FOR THE
                #   CLUB (clubSeason): one national-team race is one row
                # ! THE MAJORITY GATE DOES NOT APPLY TO no_team. A row with
                #   no team is not a club row: there is no club.
                if (team_level == "club" or has_pros) and not clubSeason(r[_PID], d.year):
                    has_pros = False
                    if team_level == "club":
                        team_level = None
                        census["club_row_in_a_school_season"] += 1
            # ★ THE RACE'S CEILING, FOR UNATTACHED ROWS ONLY (owner,
            #   2026-09-20). Looked up only when it can be used: the map holds
            #   unattached rows alone, so every other row skips the dict hit
            #   entirely and pools exactly as before.
            #
            # ! THE SCHOOL-STRING CASE IS IN THE MAP TOO, not just team_id 0.
            #   tfrrs carries no team id and says "Unattached" in the school,
            #   so testing no_team alone would have covered anet and missed
            #   every tfrrs row -- see loadUnattachedRaceLevel.
            race_top_level = None
            unatt = loadUnattachedRaceLevel()
            if unatt:
                try:
                    race_top_level = unatt.get((str(r[_SPORT]), int(r[_RID])))
                except (TypeError, ValueError):
                    race_top_level = None
                if race_top_level is not None:
                    census[f"unattached_race_{race_top_level}"] += 1
            pool = poolOf(r[_GRADE], r[_GENDER], r[_SRC], r[_SCHOOL],
                          r[_SPORT], merge,
                          person_id=r[_PID], season=d.year,
                          race_date=d, team_level=team_level, team_has_pros=has_pros,
                          no_team=no_team, team_pro=team_pro,
                          race_top_level=race_top_level)
            t_pool += _clk() - _tp
            if has_pros and pool and pool.startswith("pro_"):
                census["club_with_pros_repooled_pro"] += 1
            if pool is None:
                census["unknown_pool"] += 1
                continue
            # ! THE SANITY BAND, NOW THAT THE POOL IS KNOWN. The loader's band
            #   is garbage-only; this is the real one, and it could not run
            #   earlier because the SQL has no idea which pool a row lands in.
            # ★ ON THE RATED POOL'S SCALE FIRST (the 230 ratings, above):
            #   the band and the solve both read the number in this pool
            nt = r[_NORM]
            if len(r) > _TIME and r[_TIME]:
                dm_row = r[_DIST] if len(r) > _DIST else None
                nt, tag = rescaleToPool(nt, r[_TIME], dm_row, pool, r[_SPORT])
                if tag:
                    census[tag] += 1
            lo, hi = poolBand(pool)
            if not lo <= nt <= hi:
                census["outside_pool_band"] += 1
                continue
            days = (today - d).days
            doy = d.timetuple().tm_yday          # 1..366, for the season-phase term

            akey = (r[_PID], pool)
            ac = a_lookup.get(akey)
            if ac is None:
                ac = len(a_uniq); a_lookup[akey] = ac; a_uniq.append(akey)

            # venue: namespaced by sport, or -1 when the row has no venue at all.
            # A venueless row still races -- it informs its athlete's ability and
            # simply votes on nothing. Dropping it would discard a real result.
            v = r[_VENUE]
            if v is None:
                vc = -1
            else:
                vkey = f"{r[_SPORT]}:{v}"
                vc = v_lookup.get(vkey)
                if vc is None:
                    vc = len(v_uniq); v_lookup[vkey] = vc; v_uniq.append(vkey)

            rid.append(r[_RID]); acode.append(ac); vcode.append(vc)
            norm.append(nt); wt.append(days)
            scode.append(0 if r[_SPORT] == "XC" else 1)   # for the merged result split
            doys.append(doy)

            # ============================================================ #
            #  SEASON YEAR = THE ACADEMIC YEAR. ONE CLOCK.
            #
            #  ★ THIS IS THE GROUPING KEY, AND IT HAS TO MATCH THE CLOCK THE
            #    POOL WAS DECIDED ON. `pool` above comes from grade_fix, which
            #    grade_sanity keys on the ACADEMIC year. Grouping athletes on
            #    any other clock lets one athlete-season straddle two verdicts,
            #    and with per-pool anchors that is not a labelling problem --
            #    it is an arithmetic one.
            #
            #    Measured: person 23950279, corroborated grade 6 on every row,
            #    same club, same source, came out of the solve as TWO 2024
            #    athlete-seasons -- hs_m with ability 724 rating 174.1, and
            #    ms_m with ability 721 rating 124.8. Near-identical ability,
            #    49 rating points apart, because ms anchors at 3200 and hs at
            #    5000 and each half was measured against the other's pool_mean.
            #
            #  ★ AND IT SUBSUMES THE TF ROLLOVER seasonYearFor EXISTED FOR.
            #    That rule moved November and December indoor rows forward so
            #    they would not be split from the January races they belong
            #    with -- Alexis Paterna raced Dec 27 and Jan 3 and landed in
            #    two athlete-seasons. The academic year already puts both in
            #    the same bucket, because its boundary is July. One rule
            #    instead of two, and the two can no longer disagree.
            #
            #  ⚠ `season=` PASSED TO poolOf IS STILL THE CALENDAR YEAR, and
            #    must stay so until pro_flag is rebuilt: it is now used ONLY
            #    to look up pro_athlete_season, which is calendar-keyed. That
            #    is the last table on the old clock.
            # ============================================================ #
            yrs.append(seasonYearFor(r[_SPORT], d, _rollover))
            # the row's distance in metres, 0 when the loader had none
            dm = r[_DIST] if len(r) > _DIST else None
            dists.append(float(dm) if dm is not None and dm > 0 else 0.0)
            # the meet's championship class, 0 when the loader has none
            mc = r[_MEETCLASS] if len(r) > _MEETCLASS else None
            mcls.append(int(mc) if mc is not None else 0)
            census["kept"] += 1

        census["tf_rollover_moved"] = _rollover.moved
        census["tf_rows_seen"] = _rollover.tf_total
        t_loop += (_clk() - _tb) - (t_pool - _pool_b)

        if rid:
            chunks.append((
                np.asarray(rid, dtype=np.int64),
                np.asarray(acode, dtype=np.int32),
                np.asarray(vcode, dtype=np.int32),
                np.asarray(norm, dtype=np.float32),
                np.asarray(wt, dtype=np.int32),      # DAYS AGO, not a weight
                np.asarray(scode, dtype=np.int8),    # 0=XC 1=TF (merged split)
                np.asarray(doys, dtype=np.int16),    # day-of-year

                np.asarray(yrs, dtype=np.int16),     # season year (rust/fitness)
                np.asarray(dists, dtype=np.float32), # distance in metres, 0 = none
                np.asarray(mcls, dtype=np.int8),     # meet class 0/1/2 (issue #22)
            ))

    _us = 1e6 / max(n_rows, 1)
    print(f"[time] pack: {t_fetch:.0f}s waiting on the database, "
          f"{t_pool:.0f}s resolving pools ({t_pool * _us:.1f} us/row), "
          f"{t_loop:.0f}s the rest of the row loop ({t_loop * _us:.1f} us/row), "
          f"{n_rows:,} rows")

    if not chunks:
        return None

    athlete = np.concatenate([c[1] for c in chunks])
    course = np.concatenate([c[2] for c in chunks])
    days = np.concatenate([c[4] for c in chunks]).astype(np.float64)
    sport = np.concatenate([c[5] for c in chunks])
    doy = np.concatenate([c[6] for c in chunks])

    year = np.concatenate([c[7] for c in chunks])
    dist_m = np.concatenate([c[8] for c in chunks])
    meet_class = np.concatenate([c[9] for c in chunks])
    n_ath, n_crs = len(a_uniq), len(v_uniq)

    # Two decay curves from one day count, computed vectorised in float64.
    #   athlete weights -> normalized per ATHLETE (see _normalizeByGroup)
    #   course weights  -> normalized per COURSE, slower decay
    # Both normalizations are no-ops mathematically and make underflow impossible.
    w_ath = _normalizeByGroup(np.power(DECAY_K, days), athlete, n_ath)

    w_crs = np.power(DECAY_K_COURSE, days)
    has_course = course >= 0
    w_crs_norm = np.zeros_like(w_crs)
    if has_course.any():
        w_crs_norm[has_course] = _normalizeByGroup(
            w_crs[has_course], course[has_course], n_crs)

    # ★ SORT EVERY ROW ARRAY BY ATHLETE. Two reasons, both large:
    #
    #   1. The compiled kernels walk each athlete's rows as a CONTIGUOUS
    #      SEGMENT. That is the whole reason they can fuse -- no temporaries,
    #      no scatter.
    #   2. Even on the numpy path, `ability[athlete]` over unsorted rows is
    #      61.8M random reads into a 36MB array -- a cache miss almost every
    #      time. Sorted, it is sequential.
    #
    # kind="stable" keeps rows in their original order within an athlete, so
    # anything downstream that assumes chronological order inside an athlete
    # still holds. Paid once per corpus; --cache stores the sorted arrays.
    order = np.argsort(athlete, kind="stable")
    athlete = athlete[order]
    course = course[order]
    days = days[order]
    sport = sport[order]
    doy = doy[order]
    year = year[order]
    dist_m = dist_m[order]
    meet_class = meet_class[order]
    w_ath = w_ath[order]
    w_crs_norm = w_crs_norm[order]

    # ---- distinct RACE DAYS per cell ------------------------------------
    #
    # ★ WHY THIS ARRAY EXISTS. linkage_check.shrinkByLinkage nulls a cell when
    #   it has neither outside evidence NOR inside history:
    #
    #       ext < LINK_MIN  AND  days < DAYS_MIN   ->  unidentified
    #
    #   It already reads D["cell_days"] and falls back to DEGREE * 4 when the
    #   pack does not supply it. That fallback is why the null was blind to
    #   the worst cells in the corpus: degree counts ATHLETE-SEASONS, not
    #   days, so a cell raced ONCE by a 208-person field has degree 208 and
    #   reads as "50+ days of history". Mission Concepcion Park sat at
    #   delta = +0.7585 on a single afternoon and sailed through, while the
    #   largest delta the null actually discarded was +0.356.
    #
    #   A one-day cell is exactly the unidentifiable case -- its delta and
    #   that field's mean ability are the same number, and no amount of data
    #   from that one day separates them. Real days make it fail on days = 1.
    #
    # ⚠ PER CELL, NOT PER ROW. Length is n_crs, so it must stay out of the
    #   row-length assertions below and must NOT be masked by any row filter
    #   downstream (see the matching note in linkage_check.prepare).
    #
    # The bit-packing: a date is (year, doy), and year*400 + doy < 2**20 for
    # any plausible year, so shifting course left by 20 gives one int64 that
    # is unique per (cell, date). np.unique then collapses to one entry per
    # cell-day and a bincount on the recovered cell index counts them. That
    # is a single sort over the rows rather than a per-cell group-by.
    real = course >= 0
    cell_days = np.zeros(n_crs, dtype=np.int32)
    if real.any():
        datekey = (year[real].astype(np.int64) * 400
                   + doy[real].astype(np.int64))
        packed = (course[real].astype(np.int64) << 20) + datekey
        uniq = np.unique(packed)
        counts = np.bincount(uniq >> 20, minlength=n_crs)
        cell_days = counts.astype(np.int32)
    print(f"[pack] cell_days: median {int(np.median(cell_days[cell_days > 0]))}"
          f"  one-day cells {int((cell_days == 1).sum()):,}"
          f"  of {int((cell_days > 0).sum()):,} populated")
    # the championship class census (issue #22): read it against
    # scripts/meet_class_census.py, which names the meets behind each class
    for s_code, s_name in ((0, "XC"), (1, "TF")):
        m = sport == s_code
        if m.any():
            c = np.bincount(np.clip(meet_class[m].astype(np.int64), 0, 3),
                            minlength=4)
            print(f"[pack] meet_class {s_name}: ordinary {c[0]:,}  "
                  f"league {c[1]:,}  qualifier {c[2]:,}  final {c[3]:,} rows")

    out = {
        "result_id": np.concatenate([c[0] for c in chunks])[order],
        "athlete":   athlete,
        "course":    course,
        "norm":      np.concatenate([c[3] for c in chunks])[order],
        "weight":    w_ath,          # per-athlete normalized, in (0, 1]
        "cweight":   w_crs_norm,     # per-course normalized, slower decay
        "days":      days,
        "sport":     sport,          # 0=XC 1=TF per row (used by the merged split)
        "doy":       doy,            # day-of-year per row

        "year":      year,           # season year per row (rust/fitness term)
        "dist_m":    dist_m,         # the row's distance, 0 = none (issue 148)
        "meet_class": meet_class,    # 0 ordinary, 1 league-level, 2 state-level (#22)
        "athlete_keys": a_uniq,
        "course_keys":  v_uniq,
        "cell_days": cell_days,      # PER CELL: distinct race days, length n_crs
        "n_courses": n_crs,
        "census": dict(census),
    }

    # ⚠ THE SORT MUST BE CONSISTENT ACROSS EVERY ROW ARRAY OR THE RESULTS ARE
    # SILENTLY WRONG -- not slow, WRONG, with no error. Two cheap invariants:
    #   - athlete must now be non-decreasing (that is what the kernels assume)
    #   - every row array must still be the same length
    assert np.all(np.diff(out["athlete"]) >= 0), \
        "packResults: athlete column is not sorted -- kernels would be wrong"
    n_rows = len(out["athlete"])
    for k in ("result_id", "course", "norm", "weight", "cweight",
              "days", "sport", "doy", "year", "dist_m", "meet_class"):
        assert len(out[k]) == n_rows, f"packResults: {k} length mismatch"
    # cell_days is indexed by CELL, so it gets its own check rather than
    # joining the row loop above -- a length bug here would surface as a
    # silently mis-shrunk difficulty, not an error.
    assert len(out["cell_days"]) == n_crs, "packResults: cell_days length"

    out["ath_starts"] = K.athleteSegments(out["athlete"], n_ath)
    return out


# ------------------------------------------------------------------ #
# CHUNK 3 — THE TWO HALF-STEPS
# ------------------------------------------------------------------ #

# _weightedMeanBy
# Purpose:   group-wise weighted mean, vectorised.
# Arguments: codes -- int group id per row; values, weights -- float arrays;
#            n_groups -- number of groups.
# Output:    (means ndarray[n_groups], total_weight ndarray[n_groups]).
# Detail:    bincount with `weights` sums per group in one C pass. Groups with
#            zero weight come back as 0.0 and the caller must mask them.
def _weightedMeanBy(codes, values, weights, n_groups):
    wsum = _parallelBincount(codes, weights=weights, minlength=n_groups)
    vsum = _parallelBincount(codes, weights=values.astype(np.float64) * weights,
                             minlength=n_groups)
    # NO CLAMP. The old line was
    #     np.where(wsum > 0, vsum / np.maximum(wsum, 1e-12), 0.0)
    # np.where evaluates BOTH branches, so np.maximum() existed only to silence
    # a divide-by-zero warning -- but it fired for ANY wsum < 1e-12, silently
    # replacing a small-but-real denominator. Smallest observed was 1.055e-25
    # (an athlete whose races are all from ~1947), giving an ability of
    # 1.4e-10 seconds and dev = 1.3e13, which detonated every course they raced.
    #
    # np.divide(..., where=) SKIPS those elements instead of clamping them.
    means = np.divide(vsum, wsum, out=np.zeros_like(vsum), where=wsum > 0)
    return means, wsum


# Race counts per athlete. Depend only on cols["athlete"], which is fixed for
# the entire run -- so this was one wasted 61.6M-element bincount per iteration.
_NRACES_CACHE = {"n_athletes": None, "value": None}


# ------------------------------------------------------------------ #
# CHUNK 3b — THE PINNED-COURSE LEAK
# ------------------------------------------------------------------ #

# _knownCourseRows
# Purpose:   which rows sit at a course whose difficulty we actually SOLVED?
# Arguments: course -- per-row course code (-1 = venueless);
#            thin   -- ndarray[bool] per course, True = pinned flat at 0.
# Output:    ndarray[bool], one per row.
# Syntax:    np.maximum(course, 0) clamps the -1 sentinel into range so the
#            fancy index is legal; the `course >= 0` term then discards whatever
#            that bogus lookup returned. Same trick as the `d = np.where(...)`
#            line below.
def _knownCourseRows(course, thin):
    return (course >= 0) & ~thin[np.maximum(course, 0)]


# _abilityWeights
# Purpose:   the weights computeAthleteAbilities should actually average over.
# Arguments: cols; thin -- pinned mask from the previous iteration, or None on
#            the first pass; n_athletes.
# Output:    ndarray[float] -- cols["weight"] with pinned-course rows zeroed.
#
# ★ WHY THIS EXISTS. A pinned course carries difficulty 0 because it is
# UNANCHORED, not because it is average. The `d = np.where(...)` line reads that
# 0 as a real value, so a pinned course's unmodelled hardness flows straight
# into every athlete who raced it -- their ability comes out too SLOW. Then at
# every OTHER course they run, dev = norm/ability - 1 comes out too SMALL and
# that course is deflated. The leak runs FROM courses that never earn a
# difficulty INTO the ones that do, and since pinned courses are small local
# meets, it drains into the championship venues.
#
# ESCAPE HATCH: an athlete who only ever races pinned courses would lose every
# row and fall below MIN_RACES, so they keep all of theirs. A biased ability
# beats no ability -- they anchor nothing either way.
def _abilityWeights(cols, thin, n_athletes):
    weight = cols["weight"]
    if thin is None:                  # iteration 0: nothing solved yet
        return weight

    athlete = cols["athlete"]
    known = _knownCourseRows(cols["course"], thin)

    n_known = _parallelBincount(athlete[known], minlength=n_athletes)
    keep = known | (n_known[athlete] < MIN_RACES)

    return weight * keep              # bool * float zeroes the dropped rows


# computeAthleteAbilities
# Purpose:   every athlete's ability, given the current course difficulties.
# Arguments: cols -- packed arrays; difficulty -- ndarray per course;
#            remove_outliers -- drop races >2 sigma from the athlete's own mean.
# Output:    (ability ndarray, valid mask ndarray[bool]) per athlete code.
# Detail:    ability = weighted mean of normalized_time / (1 + d_course).
#            Rows with no course use d = 0 (an average course), which is exactly
#            what "we do not know this course" should mean.
#
# ⚠ UNLIKE the course side, this runs over the FULL row set, not a `usable`
#   subset -- a venueless row still informs its athlete. So the compaction cache
#   does not apply here; the win is threading and hoisting n_races.
def computeAthleteAbilities(cols, difficulty, remove_outliers, n_athletes,
                            thin=None):
    athlete = cols["athlete"]
    course = cols["course"]

    # padded: slot n_courses holds the 0.0 that venue-less rows read.
    log1p_d = np.append(np.log1p(difficulty), 0.0)
    idx = _courseIndex(course, len(difficulty)).astype(np.int32)

    # Pinned-course rows are excluded here -- see _abilityWeights.
    w0 = _abilityWeights(cols, thin, n_athletes)

    if K.HAVE_NUMBA and "ath_starts" in cols:
        # ★ FUSED PATH. `log_adj` is never materialised -- the old code built a
        # 61.8M-element (500MB) temporary for it every single iteration. Here it
        # is a scalar inside the loop, so the half-step allocates nothing.
        # Measured 3.2x on one core; the athlete loop is prange, so a multi-core
        # box gains more. Verified equal to the numpy path to 6.2e-15.
        log_mean = np.empty(n_athletes, dtype=np.float64)
        wsum = np.empty(n_athletes, dtype=np.float64)
        K.abilityKernel(cols["ath_starts"], _logNorm(cols["norm"]), log1p_d,
                        idx, w0, bool(remove_outliers), MIN_RACES,
                        log_mean, wsum)
    else:
        # numpy fallback -- identical maths, ~3x slower, no numba required.
        log_adj = _logNorm(cols["norm"]) - log1p_d[idx]
        log_mean, wsum = _weightedMeanBy(athlete, log_adj, w0, n_athletes)

        if remove_outliers:
            resid = log_adj - log_mean[athlete]
            var, _ = _weightedMeanBy(athlete, resid * resid, w0, n_athletes)
            sigma = np.sqrt(np.maximum(var, 0.0))
            sigma_row = sigma[athlete]
            keep = np.abs(resid) <= OUTLIER_STD_THRESHOLD * sigma_row
            keep |= sigma_row == 0.0
            kept_n = _parallelBincount(athlete[keep], minlength=n_athletes)
            keep |= kept_n[athlete] < MIN_RACES
            w2 = w0 * keep
            log_mean, wsum = _weightedMeanBy(athlete, log_adj, w2, n_athletes)

    # Back to seconds. Everything downstream -- ABILITY_MIN/MAX, the pool
    # medians, buildAthleteRatings -- keeps working on a 5K-equivalent TIME.
    mean = np.exp(log_mean)

    # HOISTED. Invariant across the whole solve.
    if _NRACES_CACHE["n_athletes"] != n_athletes:
        _NRACES_CACHE["n_athletes"] = n_athletes
        _NRACES_CACHE["value"] = _parallelBincount(athlete, minlength=n_athletes)
    n_races = _NRACES_CACHE["value"]

    # An ability is a 5K-EQUIVALENT TIME, so it must look like one. `mean > 0`
    # admitted 1e-10 and 5e4; both are solver artefacts, and one is enough to
    # destroy every course the athlete raced.
    sane = (mean >= ABILITY_MIN) & (mean <= ABILITY_MAX)
    valid = (wsum > 0) & (n_races >= MIN_RACES) & sane
    return mean, valid

# ln(norm) over the full row set. `norm` is invariant for the whole solve, so it
# is computed ONCE and reused by every iteration of both half-steps. ln() on
# 61.8M float64 costs ~0.6 s, and the log-space rewrite introduced three per
# iteration -- so 500 x 3 x 61.8M logarithms becomes 61.8M, once.
_LOGNORM_CACHE = {"key": None, "value": None}


# Padded course index. `course` uses -1 for "no venue", so every iteration used
# to pay a comparison, a maximum and a where over 61.8M rows just to route those
# rows to a zero. Instead, point them at ONE EXTRA SLOT past the end of the
# per-course arrays and keep that slot at 0.0 -- then the whole thing is a single
# gather with no branching. Invariant for the solve, so computed once.
_COURSEIDX_CACHE = {"key": None, "value": None}


# _courseIndex
# Purpose:   per-row index into a length-(n_courses+1) padded array.
# Output:    ndarray[int]; rows with no venue point at slot n_courses.
def _courseIndex(course, n_courses):
    key = (id(course), course.shape[0], n_courses)
    if _COURSEIDX_CACHE["key"] != key:
        _COURSEIDX_CACHE["key"] = key
        _COURSEIDX_CACHE["value"] = np.where(course >= 0, course, n_courses)
    return _COURSEIDX_CACHE["value"]


# _logNorm
# Purpose:   ln(norm), computed once per solve.
# Output:    ndarray[float64], same shape as `norm`.
# Syntax:    np.maximum guards ln(0). Keyed on the array's identity and length --
#            a new corpus always produces a new array object.
def _logNorm(norm):
    key = (id(norm), norm.shape[0])
    if _LOGNORM_CACHE["key"] != key:
        _LOGNORM_CACHE["key"] = key
        _LOGNORM_CACHE["value"] = np.log(np.maximum(norm, 1e-9))
    return _LOGNORM_CACHE["value"]


# Compacted per-iteration inputs. Keyed identically to _ANCHOR_CACHE: `usable`
# is a pure function of `valid`, so everything derived from it is too.
_COMPACT_CACHE = {"key": None}


# _compactedInputs
# Purpose:   the usable-row slices of every column, computed once per `valid`.
# Output:    dict with course, athlete, norm, cw, bucket, nb, uniq, linked.
#
# WHY: the old code did `cols["norm"][usable]`, `cols["cweight"][usable]`,
# `athlete[usable]` and two more slices EVERY iteration. Boolean fancy indexing
# allocates a whole new array each time -- roughly 1.5 GB of allocate-fill-free
# per iteration, and memory bandwidth is what this loop is actually limited by.
#
# `usable` = (course >= 0) & valid[athlete]. The ok_ability term in the original
# is redundant: `valid` already requires ability in [ABILITY_MIN, ABILITY_MAX]
# via `sane`, which is what the "belt and braces" comment was noting. So the
# mask, and everything sliced by it, changes only when `valid` does -- and the
# convergence log shows that happening ~5 times in 100 iterations.
#
# `norm` is cast to float64 ONCE here rather than per iteration.
def _compactedInputs(cols, valid, n_courses, n_athletes):
    key = (int(valid.sum()), hash(valid.tobytes()))

    if _COMPACT_CACHE.get("key") == key:
        return _COMPACT_CACHE

    usable = (cols["course"] >= 0) & valid[cols["athlete"]]

    course_u = cols["course"][usable]
    athlete_u = cols["athlete"][usable]
    norm_u = cols["norm"][usable].astype(np.float64)
    # ln(norm) for the same rows, sliced from the cached full-row version.
    # `valid` changes about five times in a hundred iterations, so this is free.
    lognorm_u = _logNorm(cols["norm"])[usable]
    cw_u = cols["cweight"][usable].astype(np.float64)

    uniq, linked = _courseAnchorStats(course_u, athlete_u, n_courses, n_athletes)

    _COMPACT_CACHE.clear()
    _COMPACT_CACHE.update(
        key=key, usable=usable, course=course_u, athlete=athlete_u,
        norm=norm_u, log_norm=lognorm_u, cw=cw_u, uniq=uniq, linked=linked,
    )
    return _COMPACT_CACHE


# ------------------------------------------------------------------ #
# CHUNK 4b — THE REGION PRIOR
# ------------------------------------------------------------------ #

# _regionCrossLinks
# Purpose:   per region, how many athletes race in MORE THAN ONE region?
# Arguments: region -- per-course region code (-1 = unknown);
#            course, athlete -- per-row codes; n_regions.
# Output:    ndarray[float], one count per region.
# Syntax:    the pair (athlete, region) is deduped with a lexsort, then an
#            athlete's region count is a bincount over the deduped pairs. Rows
#            whose course has no region are dropped -- an unknown region cannot
#            testify to connectivity either way.
#
# ★ THIS IS THE QUANTITY LINK_SHRINK_K SHOULD HAVE MEASURED. `linked` counted
#   athletes racing >=2 COURSES, which a closed set of mutually-linked venues
#   passes trivially -- every Alaskan cell scored as fully connected while the
#   whole state floated. Racing two courses in Alaska says nothing about where
#   Alaska sits relative to everyone else.
def _regionCrossLinks(region, course, athlete, n_regions):
    keep = (course >= 0) & (region[np.maximum(course, 0)] >= 0)
    a = athlete[keep]
    r = region[course[keep]]

    order = np.lexsort((r, a))                      # dedup (athlete, region)
    a, r = a[order], r[order]
    first = np.ones(len(a), dtype=bool)
    first[1:] = (a[1:] != a[:-1]) | (r[1:] != r[:-1])
    a, r = a[first], r[first]

    n_regions_per_ath = np.bincount(a, minlength=int(athlete.max()) + 1)
    multi = n_regions_per_ath[a] >= 2               # this athlete travels
    return np.bincount(r[multi], minlength=n_regions).astype(np.float64)


# _applyRegionPrior
# Purpose:   pull each region's MEAN difficulty toward zero, in proportion to how
#            little cross-region evidence pins it.
# Arguments: d -- per-course difficulty (modified copy returned);
#            region, weights -- per course; cross -- per region.
# Output:    ndarray, same shape as d.
# Syntax:    shrink = cross / (cross + K) is the same empirical-Bayes form as the
#            ridge, one level up: with no crossing athletes the factor is 0 and
#            the region is flattened to the global level; with many it tends to 1
#            and nothing moves. Only the region's MEAN is touched -- every cell
#            keeps its offset from that mean, so within-region terrain survives
#            untouched. That matters: the undamped solve gets terrain right.
# PERF: this was a Python loop over ~50 regions, each allocating a fresh boolean
# mask over 81k courses and calling np.average -- ~50 passes per iteration, every
# iteration. Two bincounts do the same work in one pass. Bincount is exactly a
# grouped sum, so mu = sum(d*w)/sum(w) per region falls straight out of it.
def _applyRegionPrior(d, region, weights, cross):
    known = region >= 0
    if not known.any():
        return d

    r = region[known]
    w = weights[known]
    n_reg = int(region.max()) + 1

    wsum = np.bincount(r, weights=w, minlength=n_reg)
    dsum = np.bincount(r, weights=w * d[known], minlength=n_reg)
    mu = np.divide(dsum, wsum, out=np.zeros_like(dsum), where=wsum > 0)

    # shrink toward zero by cross-region evidence; see the module comment
    # ⚠ 0/0 AT K=0. A region with no cross-links and no penalty produced NaN,
    # which propagated into every one of its difficulties and quietly corrupted
    # the K=0 control row of a whole sweep. `out=ones` is the right default:
    # no evidence AND no penalty means do not move it.
    denom = cross + REGION_SHRINK_K
    shrink = np.divide(cross, denom, out=np.ones_like(cross), where=denom > 0)

    # each cell moves by exactly its region's mean shift, so within-region
    # spread -- the terrain ordering -- is untouched by construction.
    shift = mu * (1.0 - shrink)
    out = d.copy()
    out[known] -= shift[r]
    return out


# computeCourseDifficulties
# Purpose:   every course's difficulty, given the current abilities.
# Arguments: cols; ability, valid -- per athlete; old -- previous difficulties.
# Output:    ndarray of damped, re-centered difficulties.
# Detail:    deviation = normalized_time / ability - 1. Averaged over a course's
#            results, that is "how much slower than expected people run here".
#
#            THE ANCHOR. difficulty and ability are confounded: scale every
#            ability by k and shift every difficulty to match, and the equations
#            hold identically. Left alone the loop wanders along that ray and
#            "convergence" is meaningless. We pin the gauge by forcing the
#            result-weighted mean difficulty to zero, so a difficulty reads as
#            "harder than the average course", which is what it should mean.
def computeCourseDifficulties(cols, ability, valid, old, n_courses, form,
                              region=None, cross=None):
    cache = _compactedInputs(cols, valid, n_courses, ability.size)

    c = cache["course"]
    if c.size == 0:
        return old.copy(), np.zeros(n_courses, dtype=bool)

    # ★ LOG SPACE. Was: dev = norm / ability - 1, then an ARITHMETIC mean.
    # That is a mean of ratios, and E[x/y] > E[x]/E[y] by roughly half the
    # relative variance of the field -- so a cell's difficulty picked up a term
    # proportional to how SPREAD OUT its field was, on top of its terrain.
    # ln(norm) - ln(ability) averaged and exponentiated is the geometric mean,
    # which carries no such term. It also cannot detonate: the old form divided
    # by `ability`, and one 1e-10 ability drove 92% of courses to -6.85.
    # PERF: log the ability array ONCE (n_athletes ~ 4.5M) and gather from it,
    # rather than gathering to 61.8M rows and taking 61.8M logarithms. Same
    # result, ~14x fewer transcendentals. cache["log_norm"] is the ln(norm)
    # slice, precomputed alongside the rest of the compaction.
    log_ability = np.log(np.maximum(ability, 1e-9))
    cw = cache["cw"]

    # SEASON FORM. Rust on a first race, fitness accrued since the athlete's
    # own opener. Both fitted off-line from pairs where difficulty cancels
    # exactly, and neither correlates with venue -- which is what the old
    # week-based term could not manage, and why it took 2.7% out of Glendoveer.
    # log1p because `form` is a RELATIVE offset: subtracting it raw would be
    # wrong by O(form^2), which at 2.7% is not negligible.
    log_form = cache.get("log_form")
    if log_form is None:
        log_form = np.log1p(form[cache["usable"]])
        cache["log_form"] = log_form          # `usable` changes ~5x in 100 iters

    if K.HAVE_NUMBA:
        # ★ FUSED PATH. The deviation is never materialised; the gather, the two
        # subtractions and both accumulations happen in one pass. 3.3x measured,
        # equal to numpy to 1.5e-14. Deliberately NOT prange: accumulating many
        # rows into one course is a scatter and would race. Courses are ~81k x 8
        # bytes = 650KB and fit in L2, so the serial scatter is cheap -- the
        # expensive axis was always the athlete side, and that one is parallel.
        sums = np.zeros(n_courses, dtype=np.float64)
        wsum = np.zeros(n_courses, dtype=np.float64)
        K.difficultyKernel(c, cache["athlete"], cache["log_norm"], log_ability,
                           log_form, cw, sums, wsum)
    else:
        log_dev = cache["log_norm"] - log_ability[cache["athlete"]] - log_form
        sums = _parallelBincount(c, weights=log_dev * cw, minlength=n_courses)
        wsum = _parallelBincount(c, weights=cw, minlength=n_courses)

    counts = _parallelBincount(c, minlength=n_courses)

    uniq, linked = cache["uniq"], cache["linked"]

    # ★ RIDGE. Was: divide by wsum alone -- an UNPENALISED group mean, free to
    # walk along any direction the data does not pin. It did: over 500 iterations
    # the difficulty range grew monotonically from [-0.08, +0.27] to
    # [-0.52, +1.22] and never converged (mean |d| change 3.9e-5 against a 1e-5
    # threshold). A venue at +1.22 is not a course, it is a parameter drifting.
    #
    # Adding lambda to the DENOMINATOR is exact ridge regression -- arithmetically
    # identical to `lambda` extra observations pinned at zero -- so the penalty
    # grows with displacement and the walk cannot continue. A cell with 10,000
    # rows is untouched; one with 30 is pulled most of the way to the prior.
    log_raw = np.divide(sums, wsum + RIDGE_LAMBDA,
                        out=np.zeros_like(sums),
                        where=(wsum + RIDGE_LAMBDA) > 0)

    # ================================================================
    # ROBUST REWEIGHTING -- one IRLS step. See ROBUST_C.
    # ================================================================
    # ! THE RESIDUAL IS PER ROW, AGAINST ITS OWN CELL'S CURRENT ESTIMATE. That
    #   is what makes this a robustness step and not a second prior: a row is
    #   discounted for disagreeing with the field it ran in, never for being
    #   fast or slow in absolute terms. A whole field having a bad day moves
    #   log_raw with it and nobody is down-weighted.
    #
    # ! MAD, NOT SD, FOR THE SCALE. The standard deviation is itself wrecked by
    #   the outliers this exists to handle -- one 22-minute jog inflates the
    #   scale enough to bring itself back inside the band. The median absolute
    #   deviation has a 50% breakdown point. 1.4826 makes it agree with sd on
    #   normal data, so ROBUST_C keeps its usual meaning.
    if ROBUST_C > 0:
        log_dev = cache["log_norm"] - log_ability[cache["athlete"]] - log_form
        resid = log_dev - log_raw[c]
        absr = np.abs(resid)
        # Strided sample, not a random one: reproducible, no RNG to seed, and
        # the row order here is compaction order rather than anything that
        # correlates with the residual.
        step = max(1, absr.size // ROBUST_SAMPLE)
        scale = 1.4826 * float(np.median(absr[::step]))
        if scale > 0:
            # Huber: unit weight inside the band, scale/|r| outside, so the
            # influence of a far row is bounded rather than proportional.
            rw = np.minimum(1.0, (ROBUST_C * scale) / np.maximum(absr, 1e-12))
            rcw = cw * rw
            sums = _parallelBincount(c, weights=log_dev * rcw,
                                     minlength=n_courses)
            wsum = _parallelBincount(c, weights=rcw, minlength=n_courses)
            log_raw = np.divide(sums, wsum + RIDGE_LAMBDA,
                                out=np.zeros_like(sums),
                                where=(wsum + RIDGE_LAMBDA) > 0)

    # Back to the (1+d) convention the whole rest of the system speaks --
    # backfill, ratings, the artefacts. expm1 is exact near zero where plain
    # exp(x)-1 loses precision, and difficulty is small by construction.
    raw = np.expm1(log_raw)

    # ================================================================
    # THE SOFT MODE, and why the three old guards are gone.
    # ================================================================
    # A course and its exclusive athletes slide along a shared
    # ability<->difficulty ray. Perturb every difficulty in a closed block by e:
    # the block's athletes come out faster by e, so their dev comes back larger
    # by e, and the perturbation REPRODUCES ITSELF at full strength. The mode is
    # marginally stable -- the block can sit anywhere along that ray and every
    # equation is still satisfied.
    #
    # The old code met that with three heuristics, none of which is an
    # estimator, and all three are removed:
    #
    #   raw *= linked / (linked + LINK_SHRINK_K)
    #       `linked` counts athletes racing >=2 COURSES. A closed set of
    #       mutually-linked venues passes that test perfectly -- every Alaskan
    #       cell scored as fully connected while the whole block floated. It
    #       measured the wrong kind of connectivity. RIDGE_LAMBDA in the
    #       denominator above does the shrinkage job correctly.
    #
    #   raw[thin] = 0.0  (n < 20 athletes)
    #       A cliff. The n_athletes histogram showed a 9x discontinuity at
    #       exactly 20, and nothing physical happens between 19 and 20 runners.
    #       sum/(count+lambda) degrades smoothly instead.
    #
    #   new = (1-DAMPING)*old + DAMPING*raw
    #       ★ THE ONE THAT MATTERED. Damping is an INTEGRATOR. On a marginal
    #       mode the per-iteration decay is 1 - D(1-beta); at beta=0.99 and
    #       D=0.3 that is 0.997, so a displacement needs ~1000 iterations to
    #       halve -- and the run does 280. It converted a one-step offset into
    #       a slow ramp and then gave it hundreds of steps to accumulate:
    #       the difficulty ceiling climbed +0.001/iteration for 280 straight
    #       iterations and never stopped. UNDAMPED, the same mode collapses in
    #       a handful of steps, which is why the reference fit -- 60 undamped
    #       iterations, no shrink, no gate -- lands Alaska at +0.043 where the
    #       engine walks to +0.392.
    #
    # What remains is a ridge-penalised weighted mean plus the gauge anchor:
    # an actual estimator, minimising a real objective, with one global optimum.
    # ★ REGION PRIOR, before the gauge anchor. See _applyRegionPrior.
    if region is not None and cross is not None:
        raw = _applyRegionPrior(raw, region, counts.astype(np.float64), cross)

    # Half-step. See DAMPING.
    new = (1.0 - DAMPING) * old + DAMPING * raw

    # --- the identifiability anchor ---------------------------------
    # STILL REQUIRED. alpha+delta is unchanged by adding k to every ability and
    # subtracting it from every difficulty, so the solve would slide along that
    # ray forever without a pin. Weighted by row count so a 50-row cell cannot
    # outvote a 50,000-row one.
    w = counts.astype(np.float64)
    solved = counts > 0
    # ★ §6.1 FIX #1 -- anchor over the SAME population the region prior
    #   acts on. Off by default; see ANCHOR_ON_MAPPED_ONLY.
    if ANCHOR_ON_MAPPED_ONLY and region is not None:
        solved = solved & (region >= 0)
    if solved.any() and w[solved].sum() > 0:
        new[solved] -= np.average(new[solved], weights=w[solved])

    # Cells with NO cross-links at all are genuinely unidentified -- not merely
    # thin. Nothing in the data distinguishes their difficulty from their
    # athletes' ability, so report them as unknown rather than inventing a
    # number, and keep them out of the ability step (see _abilityWeights).
    thin = (linked < 1) | (counts == 0)
    new[thin] = 0.0
    # `thin` goes back to the caller so the ABILITY step can exclude these
    # rows next iteration -- a pinned 0 is an unknown, not an average.
    return new, thin


# Cache for the anchor statistics. They depend ONLY on `valid` (and the fixed
# course/athlete codes), so they can be reused across iterations.
_ANCHOR_CACHE = {"key": None, "uniq": None, "linked": None}


# _courseAnchorStats
# Purpose:   per course, (distinct athletes, distinct CROSS-LINKED athletes).
# Arguments: course_codes, athlete_codes (aligned, usable rows only);
#            n_courses; n_athletes -- size of the ability array.
# Output:    (uniq, linked), both ndarray[n_courses].
#
# ONE LEXSORT, BOTH COUNTS. The old code sorted twice -- once by
# (course, athlete) for uniq and once by (athlete, course) for linked -- but
# both are just tallies over the SAME set of distinct (athlete, course) pairs:
#     uniq[c]   = how many distinct pairs mention course c
#     linked[c] = how many of those pairs belong to a multi-course athlete
# Deduplicating once and bincounting twice gives both.
#
# On 58M rows that removes a full O(n log n) sort per call -- roughly half the
# per-iteration cost before caching, and all of it after.
def _courseAnchorStats(course_codes, athlete_codes, n_courses, n_athletes):
    if course_codes.size == 0:
        zero = np.zeros(n_courses, dtype=np.int64)
        return zero, zero.copy()

    # Sort by athlete, then course, so identical (a, c) pairs sit adjacent.
    order = np.lexsort((course_codes, athlete_codes))
    a = athlete_codes[order]
    c = course_codes[order]

    pair_first = np.empty(a.size, dtype=bool)
    pair_first[0] = True
    np.not_equal(c[1:], c[:-1], out=pair_first[1:])   # new course...
    pair_first[1:] |= a[1:] != a[:-1]                 # ...or new athlete

    ad = a[pair_first]
    cd = c[pair_first]

    # Distinct athletes per course: one distinct pair per (athlete, course).
    uniq = np.bincount(cd, minlength=n_courses)

    # An athlete racing ONE course is perfectly confounded with that course's
    # difficulty and anchors nothing.
    courses_per_athlete = np.bincount(ad, minlength=n_athletes)
    multi = courses_per_athlete >= 2

    # multi is indexed by ATHLETE, so it must be subscripted with `ad`.
    linked = np.bincount(cd[multi[ad]], minlength=n_courses)

    return uniq, linked


# _cachedAnchorStats
# Purpose:   _courseAnchorStats, recomputed only when `valid` actually changes.
# Detail:    usable = (course >= 0) & valid[athlete] & ok_ability, and `valid`
#            already requires ability in [ABILITY_MIN, ABILITY_MAX] -- so
#            ok_ability is implied and `usable` is a pure function of `valid`.
#            Identical `valid` therefore means identical uniq/linked, exactly.
#
#            In practice `valid` is unchanged for 70+ consecutive iterations
#            (see "athletes rated" in the convergence log), so this turns ~100
#            lexsorts into ~5.
#
#            The key hashes the whole boolean array, not just its sum: two
#            different athletes flipping in opposite directions would leave the
#            count identical while changing the answer. 4.4M bytes hashes in a
#            couple of milliseconds against ~30 s of sorting.
def _cachedAnchorStats(course_codes, athlete_codes, n_courses, n_athletes, valid):
    key = (int(valid.sum()), hash(valid.tobytes()))

    if _ANCHOR_CACHE["key"] == key:
        return _ANCHOR_CACHE["uniq"], _ANCHOR_CACHE["linked"]

    uniq, linked = _courseAnchorStats(
        course_codes, athlete_codes, n_courses, n_athletes)

    _ANCHOR_CACHE.update(key=key, uniq=uniq, linked=linked)
    return uniq, linked


# ------------------------------------------------------------------ #
# CHUNK 4 — THE LOOP
# ------------------------------------------------------------------ #


# runConvergence
# Purpose:   alternate the two half-steps until the difficulties settle.
# Arguments: cols; n_athletes, n_courses.
# Output:    (difficulty ndarray, ability ndarray, valid mask).
# Detail:    iteration 0 does NOT remove outliers -- abilities are still raw, so
#            an "outlier" at that point is usually just a hard course.
# loadCourseRegions
# Purpose:   map each course code to an integer region (US state) code.
# Arguments: course_keys -- the engine's per-code venue keys.
# Output:    ndarray[int] of length n_courses; -1 where no region is known.
#
# XC keys look like 'XC:<course_name>'; TF keys like 'TF:loc:<location_id>:<in|out>'.
# Both are resolved from the meets tables by a single query each. Names are
# ambiguous for ~1,000 XC venues (twenty different "Riverside Park"s), which is
# fine here -- a slightly wrong STATE label barely perturbs a regional mean, and
# the alternative is no prior at all.
# Courses whose real-world difficulty is known independently of any model.
# The closest thing this system has to labelled test data.  +1 = genuinely hard.
# ★ KEYED ON CANONICAL ID, NOT NAME. The engine's in-memory course keys are
# 'XC:<canonical_id>:d<distance>' -- names only appear after
# saveCourseDifficulties translates on the way out. An earlier version of this
# table used names and silently matched NOTHING, reporting "anchors 0/0" for a
# whole sweep. Canonical ids are also the right key on their own merits: ~1,000
# XC course NAMES are ambiguous (twenty different "Riverside Park"s).
#
# Each entry is (canonical_id, flagship_distance, expected_sign). The flagship
# distance is the cell holding the most results, so the check lands on the cell
# that actually matters rather than some 60-row JV race at the same venue.
TERRAIN_ANCHORS = {
    13433: ("Mt. San Antonio College",  4700, +1),   # hardest famous US course
      983: ("Van Cortlandt Park",       4000, +1),   # brutal
     7126: ("Holmdel Park",             5000, +1),   # the Bowl
     2591: ("Sunken Meadow State Park", 5000, +1),   # cardiac hill
    13853: ("Bowdoin Park",             5000, +1),   # hard
    18719: ("Woodward Park",            5000, +1),   # mid-hard, CA state meet
    16188: ("Detweiller Park",          4800, -1),   # fastest course in America
}


# reportSolve
# Purpose:   the three numbers that decide whether a solve is good, printed
#            without writing anything to the database.
# Arguments: difficulty; course_keys; region; counts -- rows per course.
# Output:    dict of the metrics, also printed.
#
# ★ WHY THESE THREE. `anchors` is the only ground truth available -- seven
# courses whose difficulty is known from the real world. `state_sd` is the
# drift readout: reference_fit.py measures 0.0142 on the same corpus, so that
# is the target, NOT zero. `sd` catches over-shrinkage, which flattens
# everything and would otherwise look like an improvement in state_sd.
def reportSolve(difficulty, course_keys, region, counts):
    # Index the anchors by the engine's own key string, built from the table.
    want = {f"XC:{cid}:d{dist}": (name, sign)
            for cid, (name, dist, sign) in TERRAIN_ANCHORS.items()}
    ok = total = 0
    lines = []
    for i, key in enumerate(course_keys):
        hit = want.get(str(key))
        if hit is None or counts[i] == 0:
            continue
        name, sign = hit
        total += 1
        good = np.sign(difficulty[i]) == sign
        ok += good
        lines.append(f"      {name:<26} {difficulty[i]:+.4f}"
                     f"{'' if good else '   <-- WRONG SIGN'}")
    if total == 0:
        lines.append("      (no anchors matched -- check TERRAIN_ANCHORS keys "
                     "against cols['course_keys'])")

    solved = counts > 0
    sd = float(np.std(difficulty[solved])) if solved.any() else 0.0

    state_sd = 0.0
    if region is not None:
        known = solved & (region >= 0)
        if known.any():
            w = counts[known].astype(np.float64)
            r = region[known]
            n = int(region.max()) + 1
            wsum = np.bincount(r, weights=w, minlength=n)
            dsum = np.bincount(r, weights=w * difficulty[known], minlength=n)
            big = wsum > 0
            mu = dsum[big] / wsum[big]
            state_sd = float(np.std(mu))

    print("\n    --- solve report ---")
    print("\n".join(lines))
    print(f"      anchors correct : {ok}/{total}   (engine baseline 2/7)")
    print(f"      difficulty sd   : {sd:.4f}   (reference 0.0445)")
    print(f"      region-mean sd  : {state_sd:.4f}   (reference 0.0142  <-- TARGET)")
    return {"anchors": ok, "total": total, "sd": sd, "state_sd": state_sd}


# sweepRegionShrink
# Purpose:   ★ SOLVE ONCE PER CANDIDATE K, FROM ONE LOAD OF THE DATA.
# Arguments: cols, n_athletes, n_courses, region; values -- list of K.
# Output:    None; prints a comparison table.
#
# The whole point: streaming and packing 61.8M rows costs minutes and the merges
# cost ~19 more, but neither depends on K. Re-running the entire pipeline per
# candidate wastes all of it. This reuses one packed corpus for every value and
# writes nothing.
def sweepRegionShrink(cols, n_athletes, n_courses, region, values,
                      max_iters=SWEEP_ITERATIONS):
    global REGION_SHRINK_K
    rows = []
    for k in values:
        REGION_SHRINK_K = float(k)
        print(f"\n{'=' * 66}\n[sweep] REGION_SHRINK_K = {k}"
              f"  ({max_iters} iterations)\n{'=' * 66}")
        difficulty, _ability, _valid = runConvergence(
            cols, n_athletes, n_courses, region, max_iters=max_iters)
        counts = np.bincount(cols["course"][cols["course"] >= 0],
                             minlength=n_courses)
        m = reportSolve(difficulty, cols["course_keys"], region, counts)
        rows.append((k, m))

    print(f"\n{'=' * 66}\n[sweep] SUMMARY\n{'=' * 66}")
    print(f"{'K':>10}{'anchors':>10}{'diff sd':>10}{'region sd':>12}")
    for k, m in rows:
        print(f"{k:>10}{m['anchors']:>7}/{m['total']}{m['sd']:>10.4f}"
              f"{m['state_sd']:>12.4f}")
    print(f"\n  {max_iters}-iteration approximations. Re-run the winner uncapped.")
    print("  target: anchors 7/7, region sd near 0.0142 (reference_fit.py).")
    print("  region sd FAR BELOW target means over-shrunk -- check diff sd too.")


def loadCourseRegions(course_keys):
    from database import getConn, initPool, closePool

    # ★ §6.1 FIX #2. Optional: a missing helper degrades to today's
    #   behaviour (XC unmapped) rather than killing the solve.
    try:
        import region_lookup
    except Exception as exc:
        region_lookup = None
        print(f"[engine] region_lookup unavailable ({exc}) -- XC stays unmapped")

    xc_map, tf_map, xc_id_map = {}, {}, {}
    initPool()
    try:
        with getConn() as conn, conn.cursor() as cur:
            # modal state per course name
            cur.execute("""
                SELECT DISTINCT ON (course_name) course_name, state
                FROM (SELECT course_name, state, count(*) AS n
                      FROM meets
                      WHERE COALESCE(TRIM(state), '') <> ''
                        AND COALESCE(TRIM(course_name), '') <> ''
                      GROUP BY 1, 2) q
                ORDER BY course_name, n DESC
            """)
            xc_map = {k: v for k, v in cur.fetchall()}

            cur.execute("""
                SELECT DISTINCT ON (location_id) location_id, state
                FROM (SELECT location_id, state, count(*) AS n
                      FROM meets_tf
                      WHERE location_id IS NOT NULL
                        AND COALESCE(TRIM(state), '') <> ''
                      GROUP BY 1, 2) q
                ORDER BY location_id, n DESC
            """)
            tf_map = {str(k): v for k, v in cur.fetchall()}

            # §6.1 FIX #2 -- canonical_id -> state, built by repeating
            # _xcQuery's own course_canonical join so the two key spaces
            # cannot disagree. See region_lookup.loadIdStates.
            if region_lookup is not None:
                xc_id_map = region_lookup.loadIdStates(cur)
    finally:
        closePool()

    codes, seen = np.full(len(course_keys), -1, dtype=np.int32), {}
    for i, key in enumerate(course_keys):
        if not key:
            continue
        st = None
        if key.startswith("XC:"):
            st = xc_map.get(key[3:])
            # ★ The lookup above matches NOTHING: _xcQuery mints
            #   '<canonical_id>:d<dist>' or 'name:<name>:d<dist>', never a
            #   bare course_name. Kept only so a legacy key still resolves.
            if st is None and region_lookup is not None:
                st = region_lookup.xcState(key, xc_map, xc_id_map)
        elif key.startswith("TF:loc:"):
            st = tf_map.get(key.split(":")[2])
        if st is None:
            continue
        # UPPER+TRIM: the raw column contains both 'OS' and 'os'.
        st = st.strip().upper()
        codes[i] = seen.setdefault(st, len(seen))

    # Publish code -> state so the runaway reporter can print 'CA' not 'r17'.
    if region_lookup is not None:
        region_lookup.REGION_NAMES = {c: s for s, c in seen.items()}

    # §6.1 diagnostic. Wrapped: a missing helper module must not be able to
    # kill a 6.5-minute solve, so a failure here degrades to the one-line
    # summary runConvergence already prints.
    try:
        import region_coverage
        region_coverage.report(course_keys, codes, seen,
                               ref_path=REGION_REF_COUNTS_CSV)
    except Exception as exc:
        print(f"[engine] region coverage report skipped: {exc}")

    return codes


def runConvergence(cols, n_athletes, n_courses, region=None, max_iters=None):
    # Cross-region link counts are FIXED for the whole solve -- they depend only
    # on who raced where, never on the current difficulties -- so compute once.
    cross = None
    if region is not None:
        cross = _regionCrossLinks(region, cols["course"], cols["athlete"],
                                  int(region.max()) + 1)
        named = int((region >= 0).sum())
        print(f"[engine] region prior: {named:,}/{len(region):,} courses mapped, "
              f"{int((cross > 0).sum())} regions with cross-links")
    difficulty = np.zeros(n_courses)
    best = difficulty.copy()
    best_change = np.inf
    stale = 0
    ability = valid = None
    thin = None                  # pinned-course mask, lags one iteration

    # ---- season-form correction, built ONCE ----
    # Nothing here depends on the current ability or difficulty, so building it
    # inside the loop would burn a lexsort over 61.6M rows per iteration for an
    # identical answer.
    from rust_fitness import buildCorrection, reportCorrection
    from speed_ratings_db import loadPriorRatings

    form = buildCorrection(cols, cols["athlete_keys"], loadPriorRatings())
    reportCorrection(form, cols)

    K.warmup()
    import time
    _t_loop = time.time()

    # ★ TWO WAYS TO STOP EARLY AND STILL KEEP THE RESULT.
    #
    #   1. Ctrl-C  -- caught below, breaks the loop, falls through to the
    #      normal write path. A SECOND Ctrl-C during the merge will not be
    #      caught, which is deliberate: interrupting a heap rebuild mid-swap
    #      is how you end up with results_new orphaned and results_old live.
    #
    #   2. Create engine/data/STOP -- checked once per iteration, so an
    #      unattended or backgrounded run can be stopped without a terminal.
    #      The file is deleted on pickup so it cannot silently kill the next run.
    #
    # Either way `best` is restored, so you get the lowest-change state seen,
    # not whatever half-updated array the loop happened to be holding.
    # ★ §6.5 -- WHICH cell runs away. The loop already detects THAT one
    #   does; without the identity the fix is guesswork. Optional import:
    #   a missing helper degrades to today's behaviour.
    try:
        import runaway
        _rway = runaway.newTracker()
    except Exception as exc:
        runaway = None
        _rway = None
        print(f"[engine] runaway tracker unavailable: {exc}")

    # region_lookup is a LOCAL inside loadCourseRegions, so it is not in
    # scope here. getattr tolerates None, yielding {} when it is absent.
    try:
        import region_lookup as region_lookup_mod
    except Exception:
        region_lookup_mod = None

    # Rows per course, for the reporter only. One bincount, not per iteration.
    _real = cols["course"] >= 0
    _course_counts = np.bincount(cols["course"][_real], minlength=n_courses)

    _stop_file = os.path.join(_CACHE_DIR, "STOP")
    try:
        print("\n[engine] iterating...   (Ctrl-C once, or create "
              f"{_stop_file}, to stop early and keep the result)")
        for it in range(max_iters or MAX_ITERATIONS):
            if os.path.exists(_stop_file):
                os.remove(_stop_file)
                print(f"[engine] STOP file seen at iteration {it}; "
                      f"restoring best state")
                difficulty = best
                break
            prev = difficulty.copy()

            ability, valid = computeAthleteAbilities(
                cols, difficulty, remove_outliers=(it > 0), n_athletes=n_athletes,
                thin=thin)
            difficulty, thin = computeCourseDifficulties(
                cols, ability, valid, difficulty, n_courses, form, region, cross)

            diff = np.abs(difficulty - prev)
            change = float(diff.mean())
            worst = float(diff.max())
            rated = int(valid.sum())
            print(f"  iter {it + 1:>3}: mean |d| change = {change:.6f}   "
                  f"max = {worst:.6f}   "
                  f"athletes rated {rated:,}   "
                  f"difficulty range [{difficulty.min():+.3f}, {difficulty.max():+.3f}]")

            # Throttled: the 'worst cell still moving' warning below already
            # fires every iteration, so this must not double it.
            if runaway is not None:
                _obs = runaway.observe(_rway, diff, it)
                if runaway.shouldReport(_obs):
                    print(runaway.describe(
                        _obs, cols["course_keys"], region,
                        getattr(region_lookup_mod, "REGION_NAMES", {}),
                        _course_counts))

            if change < best_change:
                best_change, best, stale = change, difficulty.copy(), 0
            else:
                stale += 1
                print(f"    no improvement ({stale}/{PATIENCE})")

            # ★ BOTH the mean AND the worst cell must settle. The mean over ~81k
            # cells is dominated by the ~80k that already stopped, so a single
            # runaway is invisible in it: a run "converged" at mean 1.0e-5 while the
            # worst cell was still moving 5.4e-4 per iteration and the difficulty
            # ceiling had climbed to +1.18 and was still rising. Mean-only is a test
            # that a runaway CANNOT fail.
            if change < CONVERGENCE_THRESHOLD and worst < CONVERGENCE_THRESHOLD * WORST_TOLERANCE:
                print(f"[engine] converged after {it + 1} iterations")
                break
            if change < CONVERGENCE_THRESHOLD:
                print(f"    mean settled but worst cell still moving {worst:.6f}"
                      f" -- a cell is running away, not converging")
            if stale >= PATIENCE:
                print(f"[engine] plateaued; restoring best state")
                difficulty = best
                break
        else:
            print(f"[engine] hit MAX_ITERATIONS ({MAX_ITERATIONS}); restoring best")
            difficulty = best

        # Abilities must correspond to the difficulties we are about to save.
        ability, valid = computeAthleteAbilities(
            cols, difficulty, remove_outliers=True, n_athletes=n_athletes,
            thin=thin)
    except KeyboardInterrupt:
        # Ctrl-C: keep the best state and fall through to the writer.
        print(f"\n[engine] interrupted at iteration {it + 1}; "
              f"restoring best state and continuing to write")
        difficulty = best

    if runaway is not None:
        runaway.summary(_rway, cols["course_keys"], region,
                        getattr(region_lookup_mod, "REGION_NAMES", {}),
                        _course_counts)

    _el = time.time() - _t_loop
    print(f"[time] solve loop: {_el:.1f}s over {it + 1} iterations "
          f"({_el / max(it + 1, 1):.2f}s per iteration)")

    return difficulty, ability, valid


# ------------------------------------------------------------------ #
# CHUNK 5 — RATINGS
# ------------------------------------------------------------------ #

# poolMeans
# Purpose:   the mean ability of each pool -- the 100-point anchor.
# Arguments: ability, valid; athlete_keys -- decode table.
# Output:    {pool: mean_ability_seconds}
def poolMeans(ability, valid, athlete_keys):
    acc = defaultdict(lambda: [0.0, 0])
    for code in np.nonzero(valid)[0]:
        pool = athlete_keys[code][1]
        acc[pool][0] += float(ability[code])
        acc[pool][1] += 1
    return {p: s / n for p, (s, n) in acc.items() if n}


# buildAthleteRatings
# Purpose:   ability (seconds) -> points, anchored at 100 = pool mean.
# Arguments: ability, valid, athlete_keys, means, n_races.
# Output:    {(person_id, pool): {"speed_rating","n_races"}}
# Detail:    points = pool_mean / ability * 100. Faster (fewer seconds) -> >100.
def buildAthleteRatings(ability, valid, athlete_keys, means, n_races):
    out = {}
    for code in np.nonzero(valid)[0]:
        pid, pool = athlete_keys[code]
        m = means.get(pool)
        a = float(ability[code])
        if not m or a <= 0:
            continue
        out[(pid, pool)] = {"speed_rating": round(m / a * 100.0, 2),
                            "n_races": int(n_races[code])}
    return out


# buildDifficultiesToSave
# Purpose:   attach n_results / n_athletes so thin courses can be flagged later.
# Arguments: difficulty, cols, course_keys, n_courses.
# Output:    {course_name: {"difficulty","n_results","n_athletes"}}
def buildDifficultiesToSave(difficulty, cols, course_keys, n_courses):
    course = cols["course"]
    real = course >= 0
    counts = np.bincount(course[real], minlength=n_courses)
    # was: uniq = _uniqueAthletesPerCourse(...)
    #
    # _uniqueAthletesPerCourse was folded into _courseAnchorStats -- both counts
    # come from one dedup, so the second lexsort was pure waste. This runs once
    # at save time, not per iteration, so it goes straight to the uncached
    # version.
    uniq, _linked = _courseAnchorStats(
        course[real], cols["athlete"][real], n_courses,
        int(cols["athlete"].max()) + 1)
    out = {}
    for code, name in enumerate(course_keys):
        if name is None or counts[code] == 0:
            continue
        out[name] = {"difficulty": float(difficulty[code]),
                     "n_results": int(counts[code]),
                     "n_athletes": int(uniq[code])}
    return out


# buildResultRatings
# Purpose:   a speed rating for every individual result.
# Arguments: cols, difficulty, athlete_keys, means.
# Output:    list of (result_id, speed_rating) pairs.
# Detail:    fully vectorised; the per-row pool lookup is done once via a small
#            per-athlete array rather than a dict get per row.
def buildResultRatings(cols, difficulty, athlete_keys, means):
    pool_mean_by_athlete = np.array(
        [means.get(k[1], 0.0) for k in athlete_keys], dtype=np.float64)
    pm = pool_mean_by_athlete[cols["athlete"]]

    course = cols["course"]
    d = np.where(course >= 0, difficulty[np.maximum(course, 0)], 0.0)
    adjusted = cols["norm"] / (1.0 + d)

    ok = (pm > 0) & (adjusted > 0)
    ratings = np.zeros(adjusted.shape, dtype=np.float64)
    ratings[ok] = pm[ok] / adjusted[ok] * 100.0

    # Return the ARRAYS, not a list of tuples. `list(zip(rid.tolist(),
    # val.tolist()))` allocated 30M Python ints + 30M floats + 30M tuples in a
    # list -- about 3.7GB, discarded one row later. saveResultSpeedRatings walks
    # these lazily into COPY (see speed_ratings_db._asPairs), at 360MB.
    rid = cols["result_id"][ok]
    val = np.round(ratings[ok], 2)
    return rid, val


# ------------------------------------------------------------------ #
# CHUNK 6 — MAIN
# ------------------------------------------------------------------ #

# runSport
# Purpose:   solve one sport end to end: load, pack, converge, save.
# Arguments: sport -- 'XC' | 'TF'; today.
# Output:    (n_venues, n_athletes, n_results) actually written.
# Detail:    the sports are solved INDEPENDENTLY, by owner's decision. Pools are
#            namespaced `hs_m|XC`, venues `XC:Detweiller` / `TF:loc:44:out`, and
#            each sport writes its own results table. Nothing crosses over.
def runSport(sport: str, today: date):
    print("\n" + "=" * 70)
    print(f"SPORT: {sport}")
    print("=" * 70)

    cols = packOrLoad((sport,), today, merge=False, cache=args.cache)
    if cols is None:
        print(f"[engine] {sport}: no usable results.")
        return 0, 0, 0
    print(f"[engine] {sport} census: {cols['census']}")

    n_athletes = len(cols["athlete_keys"])
    n_courses = cols["n_courses"]
    n_novenue = int((cols["course"] < 0).sum())
    print(f"[engine] {len(cols['norm']):,} results   {n_athletes:,} athlete-pools   "
          f"{n_courses:,} venues   ({n_novenue:,} venueless -> ability only)")

    region = loadCourseRegions(cols["course_keys"])
    difficulty, ability, valid = runConvergence(cols, n_athletes, n_courses,
                                                region)

    print(f"\n[engine] {sport}: building outputs...")
    n_races = np.bincount(cols["athlete"], minlength=n_athletes)
    means = poolMeans(ability, valid, cols["athlete_keys"])
    for p in sorted(means):
        print(f"    pool mean  {p:<26} {means[p]:8.1f}s")

    difficulties = buildDifficultiesToSave(difficulty, cols,
                                           cols["course_keys"], n_courses)
    ratings = buildAthleteRatings(ability, valid, cols["athlete_keys"],
                                  means, n_races)
    result_ratings = buildResultRatings(cols, difficulty,
                                        cols["athlete_keys"], means)

    # NOTE: nothing is written here. course_difficulties and athlete_ratings are
    # TRUNCATE+INSERT and SHARED across sports, so writing them per sport would
    # make TF's truncate delete everything XC just wrote. runEngine collects both
    # sports first, then writes once.
    return difficulties, ratings, result_ratings


# _reportDelta
# Purpose:   measure whether an athlete's XC and TF abilities differ by a CONSTANT
#            (=> safe to merge into one pool) or scatter widely (=> keep separate).
# Arguments: all_ratings -- {(pid, "college_m|XC"): {"speed_rating":..}, ...}.
# Output:    none; prints the delta distribution. This is the SSOT gate for the
#            merged solve. Read-only.
# Detail:    speed_rating = pool_mean / ability, so within one base pool the
#            pool_mean is a shared constant and ln(sr_XC) - ln(sr_TF) tracks
#            ln(ability_XC) - ln(ability_TF) up to that constant. What matters is
#            the SPREAD: tight+unimodal -> the XC<->TF gap is one offset the
#            difficulties absorb; wide/bimodal -> merging would corrupt both.
def _reportDelta(all_ratings):
    import math
    paired = defaultdict(dict)                     # (pid, base) -> {"XC":sr,"TF":sr}
    for (pid, pool), rec in all_ratings.items():
        if "|" not in pool:
            continue                               # already merged -> nothing to pair
        base, sport = pool.rsplit("|", 1)
        paired[(pid, base)][sport] = rec["speed_rating"]

    deltas = [math.log(v["XC"]) - math.log(v["TF"])
              for v in paired.values()
              if v.get("XC", 0) > 0 and v.get("TF", 0) > 0]

    print("\n[engine] XC<->TF delta (ln ability_XC - ln ability_TF):")
    if len(deltas) < 100:
        print(f"    only {len(deltas)} dual-sport athletes -- too few to judge.")
        return
    d = np.array(deltas)
    p10, p50, p90 = np.percentile(d, [10, 50, 90])
    print(f"    n={len(d):,}  mean={d.mean():+.4f}  std={d.std():.4f}  "
          f"p10/p50/p90 = {p10:+.4f} / {p50:+.4f} / {p90:+.4f}")
    print("    READ: std <~0.05 and unimodal -> the gap is a constant -> MERGE is "
          "justified (run --sport merged). Wide or two-humped -> keep separate.")


# runMerged
# Purpose:   one solve over BOTH sports with a shared athlete ability.
# Detail:    athletes lose their sport (one ability); venues keep it (separate
#            difficulty). Result ratings are split back to results / results_tf by
#            the per-row sport code. Shared tables written once.
def runMerged(today, cache=False, noSave=False, sweep=None,
              sweepIters=SWEEP_ITERATIONS, packOnly=False):
    from itertools import chain
    print("\n" + "=" * 70)
    print("MERGED SOLVE  (XC + TF share athlete ability)")
    print("=" * 70)

    cols = packOrLoad(SPORTS, today, merge=True, cache=cache)
    if cols is None:
        print("[engine] merged: no usable results.")
        return
    print(f"[engine] merged census: {cols['census']}")

    # ★ PACK-ONLY: the pack is the deliverable, the solve is not.
    #   pair_all --golive loads this same packed_XC_TF.npz and then replaces
    #   every difficulty and rating the ALS solve below would write. Stopping
    #   here skips ~18 minutes of iteration whose output is discarded.
    #   ⚠ NOT a no-op: without --pack-only this script still writes
    #     course_difficulties / athlete_ratings / speed_rating, so anything
    #     that reads those between the two runs sees ALS numbers.
    if packOnly:
        print("[engine] --pack-only: packed and stopping. "
              "No solve, no writes. Run pair_all --golive next.")
        return

    n_athletes = len(cols["athlete_keys"])
    n_courses = cols["n_courses"]
    region = loadCourseRegions(cols["course_keys"])

    # ★ SWEEP MODE. Solve once per candidate K from THIS packed corpus, print
    # the comparison, and stop. Streaming and the merges are the expensive parts
    # and neither depends on K, so re-running the whole pipeline per candidate
    # throws away ~20 minutes each time.
    if sweep:
        sweepRegionShrink(cols, n_athletes, n_courses, region,
                          [float(v) for v in sweep.split(",")],
                          max_iters=sweepIters)
        return
    difficulty, ability, valid = runConvergence(cols, n_athletes, n_courses,
                                                region)

    # ★ anchors / diff sd / region sd on the NORMAL path, not only under
    #   --sweep. These are the three numbers that say whether a solve is good
    #   (see reportSolve), and a run that prints range but not anchors cannot
    #   be compared against the §3.5 sweep table.
    _real = cols["course"] >= 0
    reportSolve(difficulty, cols["course_keys"], region,
                np.bincount(cols["course"][_real], minlength=n_courses))

    means = poolMeans(ability, valid, cols["athlete_keys"])
    n_races = np.bincount(cols["athlete"], minlength=n_athletes)
    all_difficulties = buildDifficultiesToSave(difficulty, cols,
                                               cols["course_keys"], n_courses)
    all_ratings = buildAthleteRatings(ability, valid, cols["athlete_keys"],
                                      means, n_races)

    # ------------------------------------------------------------------
    # SOLVER-EXPERIMENT EXIT. Must come BEFORE saveResultSpeedRatings --
    # that is the ~20 min heap rebuild of results and results_tf, and it is
    # the whole reason --no-save exists. Everything needed to judge a
    # convergence run is already computed above.
    # ------------------------------------------------------------------
    if noSave:
        vals = np.array([entry["difficulty"]
                         for entry in all_difficulties.values()])
        solved = vals != 0.0

        print("\n[engine] --no-save: nothing written.")
        print(f"[engine] difficulty cells   {len(vals):,} "
              f"({int(solved.sum()):,} solved, "
              f"{int((~solved).sum()):,} gated to 0)")
        print(f"[engine] difficulty range   [{vals.min():+.4f}, "
              f"{vals.max():+.4f}]")
        print(f"[engine] solved mean/sd     {vals[solved].mean():+.5f} / "
              f"{vals[solved].std():.5f}")
        print(f"[engine] athlete ratings    {len(all_ratings):,}")
        return all_difficulties, all_ratings, None

    # Result ratings, split to their OWN tables by sport. (mirror of
    # buildResultRatings; duplicated so the separated path stays untouched --
    # buildResultRatings is the source of truth for this formula.)
    pm = np.array([means.get(k[1], 0.0) for k in cols["athlete_keys"]],
                  dtype=np.float64)[cols["athlete"]]
    course = cols["course"]
    # `courseAdj`, not `d` -- `d` was shadowed by the comprehension above and
    # a later reader would not be able to tell which one they were looking at.
    courseAdj = np.where(course >= 0, difficulty[np.maximum(course, 0)], 0.0)
    adjusted = cols["norm"] / (1.0 + courseAdj)
    ok = (pm > 0) & (adjusted > 0)
    ratings = np.zeros_like(adjusted, dtype=np.float64)
    ratings[ok] = pm[ok] / adjusted[ok] * 100.0

    for sc, sport in ((0, "XC"), (1, "TF")):
        m = ok & (cols["sport"] == sc)
        saveResultSpeedRatings(sport,
                               (cols["result_id"][m], np.round(ratings[m], 2)))
        print(f"[engine] merged/{sport}: {int(m.sum()):,} result ratings written")

    print("\n[engine] writing shared tables (merged)...")
    saveCourseDifficulties(all_difficulties, SPORTS)
    saveAthleteRatings(all_ratings, SPORTS)
    print("[engine] merged done.")


# runEngine
# Purpose:   orchestrate both sports.
# Arguments: none.
# Output:    none. Writes venue difficulties, athlete ratings, result ratings.
# Detail:    athlete_ratings and course_difficulties are TRUNCATE+INSERT, so both
#            sports must be collected BEFORE either is written -- otherwise the
#            second sport wipes the first. Result ratings go to separate tables
#            and are written per sport.
# runEngine
# Arguments: sports -- which sports to solve, e.g. ("TF",). Defaults to both.
# The shared tables are now SCOPE-DELETED per sport rather than TRUNCATEd, so a
# single-sport run leaves the other sport's rows intact. That is what makes
# `--sport TF` safe; the old TRUNCATE would have wiped every XC venue.
def runEngine(sports=SPORTS):
    today = date.today()
    all_difficulties = {}
    all_ratings = {}

    for sport in sports:
        difficulties, ratings, result_ratings = runSport(sport, today)
        if not difficulties and not ratings:
            continue
        all_difficulties.update(difficulties)
        all_ratings.update(ratings)
        # results_tf and results are different tables -> safe to write per sport.
        saveResultSpeedRatings(sport, result_ratings)
        # result_ratings is now (rid_array, val_array); len() of a tuple is 2.
        print(f"[engine] {sport}: {result_ratings[0].size:,} result ratings written")

    # These two tables are shared across sports and are full replacements, so we
    # write them ONCE, after both sports are solved. Writing per sport would make
    # TF's TRUNCATE delete every XC row.
    print("\n[engine] writing shared tables...")
    saveCourseDifficulties(all_difficulties, sports)
    saveAthleteRatings(all_ratings, sports)

    if len(sports) > 1:
        _reportDelta(all_ratings)

    print("\n[engine] done.")
    print(f"  venues rated  : {len(all_difficulties):,}")
    print(f"  athletes rated: {len(all_ratings):,}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Solve athlete ability + venue difficulty.")
    ap.add_argument("--sport", choices=["XC", "TF", "both", "merged"],
                    default="both",
                    help="which sport to solve. Single-sport runs are safe: the "
                         "shared tables are scope-deleted by namespace, not "
                         "truncated, so the other sport's rows survive. 'merged' "
                         "shares one athlete ability across XC+TF -- run only "
                         "after the delta gate (printed by --sport both) is tight.")
    ap.add_argument("--pack-only", action="store_true",
                    help="Stream and PACK, then stop -- skip the ALS solve and "
                         "all writes. The pipeline runs this before "
                         "pair_all --golive, which loads the same "
                         "packed_XC_TF.npz and then OVERWRITES every rating "
                         "this script would have produced. Measured: pair beats "
                         "ALS on every metric (per-race spread 3.25 vs 5.11, "
                         "6/7 anchors vs 2/7) and ALS plateaus rather than "
                         "converging, so its ~18 min of iteration is discarded "
                         "work. The PACK is not: pair_all needs it.")
    ap.add_argument("--cache", action="store_true",
                    help="cache/reuse packed arrays in engine/data. DELETE THE "
                         "FILE after any data or query change.")
    ap.add_argument("--sweep", default=None,
                    help="comma-separated REGION_SHRINK_K values to try, e.g. "
                         "'0,100,300,1000'. Solves once per value from ONE load "
                         "of the data and writes nothing. Use with --cache.")
    ap.add_argument("--sweep-iters", type=int, default=SWEEP_ITERATIONS,
                    help=f"iterations per sweep entry (default {SWEEP_ITERATIONS}). "
                         "Raise it if two candidates look too close to separate.")
    ap.add_argument("--no-save", action="store_true",
                    help="solve and report, write nothing. For solver "
                         "experiments -- skips ~20 min of heap rebuilds.")
    ap.add_argument("--anchor-mapped", action="store_true",
                    help="§6.1 FIX #1: restrict the identifiability anchor to region-mapped "
                         "cells, so the anchor and the region prior act on the same "
                         "population. Check the coverage report FIRST -- if a sport is "
                         "unmapped this leaves it unanchored.")
    args = ap.parse_args()
    ANCHOR_ON_MAPPED_ONLY = args.anchor_mapped

    if args.sport == "merged":
        runMerged(date.today(), cache=args.cache, noSave=args.no_save,
                  sweep=args.sweep, sweepIters=args.sweep_iters,
                  packOnly=args.pack_only)
    else:
        # runEngine does not take these yet -- add the same two parameters and
        # thread them through if you want cached/no-save single-sport runs.
        runEngine(SPORTS if args.sport == "both" else (args.sport,))