"""
joint_solve.py -- the joint estimator: ability, difficulty, the per-athlete
sport offset, the race-day effect, the whole-year form curve, the opener rust
and the ability tilt fitted in ONE objective instead of six sequential stages.

    ln(t) = a[athlete_season]
          + beta[athlete_season] * sc            per-athlete sport offset
          + (mu[sport(cell)] + d[cell]) * h(a)   sport level + cell difficulty
          + u[race]                              race-day effect
          + amp(a) * f_pool(day of academic year)   the form curve
          + r_pool * is_first                    opener rust
          + eps

  a      nuisance ability per athlete-season, unpenalised
  beta   sport offset per athlete-season, ridge K (issue #70: K=0.5 is the
         held-out optimum; every unit of freedom given to beta comes out of
         the sport level, which is why mu exists as its own parameter)
  mu     ONE LEVEL PER SHRINKAGE GROUP, unpenalised. The cell prior is
         d_c ~ N(0, tau2[group]) AROUND mu, not around zero. A zero-centred
         prior shrinks XC cells and TF cells toward the same point and
         re-asserts "equal mean difficulty" in proportion to the shrinkage --
         the assumption the old engine's recentring exists to avoid.
  d      cell difficulty about its group level, hierarchically shrunk
  u      race-day effect, u_j ~ N(0, sigma_u2): the term the sequential engine
         does not have, and the reason day-level noise became permanent
         venue difficulty there
  f      the form curve: one piecewise-linear curve per pool over the
         academic year (knots every CURVE_KNOT_DAYS from 1 August), second-
         difference penalised so it is smooth ACROSS the November/December
         boundary. amp(a) is the ability tilt of the season-form amplitude
         (rust_fitness._TILT_PER_POINT, 1.0 at rating 100). The curve is a
         nuisance in the fit: it never enters a rating.
  r      opener rust per pool, unpenalised: the first race of each sport
         season is slow by ~1% and the curve, a function of the calendar,
         cannot see "first race".
  eps    ASYMMETRIC robust noise: a bad day is slow and unbounded, a good day
         is bounded by physiology, so the two tails get different thresholds

★ WHAT THE CURVE CHANGES ABOUT THE SPORT LEVEL. The old engine's bbar is the
  mean within-athlete fall-to-spring discrepancy: surface PLUS six months of
  fitness, in a proportion nobody chose, resolved by assuming the average
  athlete has no sport preference. Here the seasonal part lives in f and the
  level mu[TF] - mu[XC] is surface only. What identifies the split is the
  one place surface and calendar come apart: late XC in December against the
  indoor openers weeks later, under the smoothness penalty on f. That is a
  stated, checkable assumption (scripts/check_phase_year.py) where the old
  one was neither. Without December overlap in a pool the split is carried
  by the penalty alone -- the log prints the Nov->Mar move of each pool's
  curve beside the level so a reader can see which.

WHAT THIS REPLACES, and why each is a deletion rather than an addition:

  pair_engine.solveDelta + linkage_check.shrink   -> the penalised solve here
  linkage_check.recenterSport + data/sport_gap_bbar.json + the ridge guard
        mu is a parameter. There is nothing to recentre and no file to keep.
  rust_fitness.buildCorrection (a fixed input subtracted from the response)
        The curve and the rust are parameters fitted with the cells, so
        they cannot be charged to a venue and the venue cannot be charged
        to them.
  shrinkByLinkage / bridgeFraction / shortLabelDead
        Those gates detect weakly-identified cells and overwrite them, because
        cellVariance uses sigma2/A_ii -- the diagonal of the INFORMATION, not
        the diagonal of its INVERSE. cellPosteriorVar() below estimates
        diag(A^-1) by Hutchinson probing, so precision shrinkage subsumes
        identification shrinkage and the gates have nothing left to do.
  apply_tilt (post-pass)
        h is evaluated at the model's own ability, so there is no implicit
        equation to approximate.
  rowguard (diag/triage/apply)
        A robust WEIGHT cannot spiral: the row stays in with weight 0.05
        instead of vanishing.

Everything is implicit-operator + conjugate gradient; nothing materialises a
matrix. The parameter vector is packed (a | d | u | mu | beta | c | r) with
the optional blocks empty when not asked for, so the legacy three-block
callers keep working.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np


# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# ★ ASYMMETRIC BY DESIGN. Residual is in log-time, so POSITIVE = ran slow.
HUBER_SLOW = 2.5          # positive residuals, in robust scales
HUBER_FAST = 1.5          # negative residuals

TILT_K = -0.031           # per 10 rating points; matches racecast/tilt.py
# ★ THE LINE RUNS ON (owner, 2026-09-11: "I'd prefer to extrapolate rather
#   than remain constant"). The slope was measured over ratings 70-140 and
#   used to be held flat outside that band, so a 150 was charged for a
#   course as a 140. It now extrapolates; these are safety rails only (h
#   stays inside 0.69 .. 1.19). run_joint.reportTiltByBand measures the
#   tilt the corpus actually shows per band, including above 140, every
#   run, so the extrapolation is checked rather than assumed.
TILT_RATING_LO = 40.0
TILT_RATING_HI = 200.0

# The sport-offset ridge, in row units exactly as pair_engine.demeanWithinSport
# uses it (beta = sum(sc*v) / (sum(sc^2) + K)). Issue #70's optimum.
SPORT_RIDGE = 0.5

# The form curve: knots every 30 days from 1 August (academic day 0), 13 of
# them, so the last spans June into July. Piecewise linear: two nonzeros per
# row, no scipy, and a second difference is the natural roughness.
# ★★ THE ONE NUMBER THE DATA CANNOT MEASURE (owner, 2026-09-09: "how do we
#    measure the free scalar?"). It cannot. Sport is season and the two
#    components share no data, so the relative level of XC against TF is a
#    DEFINITION. This is that definition, written down once.
#
# ★ TRACK IS THE REFERENCE, AND THE SCALE MEANS "WHAT YOU WOULD RUN ON A
#   TRACK". A track is the fastest, most standardised surface in the corpus,
#   so anchoring there is the one choice that is not arbitrary -- and it is
#   the only one that makes a CHECKABLE prediction about the difficulties.
#
# ★ 0.0583 = ln(1.06), FROM COACHING PRACTICE, NOT FROM THIS CORPUS. The
#   long-standing conversion for the same distance on grass rather than a
#   track:
#
#       firm and flat        x1.03      hilly                x1.08
#       average course       x1.06      muddy championship   x1.10
#
#   so an average cross country course costs about 6%. Independent physiology
#   agrees on the size: running on grass costs roughly 5% more energy than a
#   hard surface at the same speed.
#
# ⚠ A CONSTANT IS THE RIGHT SHAPE, AND THAT WAS CHECKED RATHER THAN ASSUMED.
#   The surface cost is a per-step energy loss and stays a roughly constant
#   FRACTION of running economy across speeds, so it does not vary with
#   ability. What it varies by is the COURSE -- the 3% to 10% ladder above --
#   and that variation already has a home in course_difficulties. Putting it
#   in this scalar as well would count it twice.
#
# ! HOW TO FALSIFY IT. Anchored here, the XC difficulty distribution must
#   come out physically sane: the famously fast courses near +2%, an average
#   one near +6%, the brutal ones +10-15%, and nothing meaningfully NEGATIVE
#   (nothing is faster than a track). scripts/difficulty_spread.py prints
#   exactly that distribution. If it does not look like the ladder, this
#   number is wrong and the failure is visible in one command.
XC_TRACK_GAP = 0.0583

CURVE_KNOT_DAYS = 30.0
CURVE_N_KNOTS = 13
ACADEMIC_YEAR_START_DOY = 213          # 1 August

# Smoothness, as a multiple of a pool's rows-per-knot: at 1.0 a unit of
# curvature (second difference) costs as much as one knot's worth of rows
# carrying a unit residual. Not tunable by held-out error -- the winter step
# barely moves predictions -- so it is a stated prior, printed in the log.
CURVE_SMOOTH = 1.0
# The knot pinned at zero in every pool's curve (index 2 = academic day 60,
# 1 October). The reported curve is re-anchored to its row-weighted mean.
CURVE_REF_KNOT = 2

# ★ THE CURVE CARRIES NO NET LEVEL BETWEEN THE SPORTS' WINDOWS (issue 143).
#   The between-sport level mu and the curve's fall-to-spring step are
#   collinear on every row except the December bridge; the smoothness
#   prior alone decided the split, and on the real data it decided that
#   runners gain 13% over the winter and the track is 8% HARDER than XC
#   (mu[TF] +0.081 where the sequential engine's gap read -0.047), which
#   put every XC rating 14 points under the same athlete's track rating.
#   This penalty pins, per pool, the row-weighted mean of amp*f over the
#   non-reference rows (track) to the mean over the reference rows (XC), so
#   the curve is within-window form only and mu carries the whole
#   between-sport difference -- the sequential engine's identification (the
#   average dual-sport athlete has no sport preference), with the
#   within-season shape kept. It is the stiff direction now, so CG resolves
#   the level in the first outer instead of drifting toward it for six.
#   Weight in units of a pool's rows; 0 restores the penalty-only split.
CURVE_GAP_WEIGHT = 100.0
# The stated winter gain (issue 113 / 143): the penalty pins the curve's
# track-window mean BELOW its XC-window mean by this much (log-time, so
# 0.03 = the average athlete 3% fitter in spring than in fall). 0 books
# the whole fall-to-spring change into the level, as the sequential engine
# did. A number here is a stated assumption, printed in the log, never a
# measurement -- the data cannot make it.
WINTER_GAIN = 0.0

# ★ THE TRACK DISTANCE OFFSET (issue 148, 2026-09-03). normalized_time puts
#   every event on the pool's reference distance through the distance
#   potential, and that potential is wrong by event: the same hs_m runner's
#   3200 read 3.6 rating points under their 800 at every ability band, the
#   1600 halfway. A cross-country cell is keyed by distance and absorbs its
#   own share; a track cell is keyed by location and mixes the events, so
#   the error lands in the ratings. One free coefficient per (pool, track
#   distance) class, pinned at the pool's reference event (1600 where it
#   exists), identified by athlete-seasons that race two events, fitted
#   with the course, the day and the form. Goes into the per-race effect
#   like difficulty. The prior sd is loose: the big classes are decided by
#   their rows, the tiny ones fall to zero.
DIST_PRIOR_SD = 0.03

# ★ A DAY TERM BEYOND THIS IS NOT A DAY, IT IS A BROKEN SHEET (issue 187,
#   2026-09-05). A JV meet whose 3200 lost a lap ran "20-28% fast", the
#   race-day term absorbed it (as it should: nothing else was polluted),
#   and the go-live handed the same term back as a credit -- an 11:13
#   rated 151.8. Real days after the tilt fix sit within a few percent
#   (Simplot's 99 days all inside 0.03). The SOLVE keeps the full term;
#   the RATING applies it clipped here, and the days beyond it go to the
#   rowguard as race_day_suspect.
RACE_DAY_CAP = 0.10
# ★ BY RATING BAND (issue 167, 2026-09-04, the owner's option 1). One
#   number per pool and event was the AVERAGE runner's exchange rate; a
#   4:13 1600 rated under a 15:40 at Mt. SAC because the top needs more
#   credit for the 1600 than the average does (109's finding on season
#   bests). Each (pool, event) class is now three: by the athlete-season's
#   own rating, refreshed every outer iteration like the tilt, with the
#   1600 pinned in every band. Middle band until the first ratings exist.
DIST_BANDS = (105.0, 120.0, 135.0)
DIST_N_BAND = len(DIST_BANDS) + 1
# ★ A FOURTH BAND AT THE TOP (2026-09-11). The published equivalence tables
#   (World Athletics 2025, Purdy, Mercier) all put the 800->1600 exponent
#   HIGHER at lower ability and the population of "athletes who race both"
#   is speed-selected at the 800; one band for everyone over 120 applied
#   the relation of a 4:10 miler to a 1:48 half-miler rated 150. The top
#   band now starts at 135, and the offsets interpolate between the
#   representative rating of each band (flat beyond the outer ones).
DIST_BAND_ANCHORS = (90.0, 112.0, 127.0, 145.0)
# the winter-gain machinery (sportGainShift / SPORT_GAIN_ANCHORS) keeps its
# own three bands: an operator passes three gains, and always has
SPORT_GAIN_BANDS = (105.0, 120.0)
# ★ THE EVENT RELATION FROM SEASON-BEST PAIRS (issues 109, 189, 190,
#   2026-09-05). Fitted from every row, the banded 3200 offset came out
#   SMALLER at the top than in the middle: a 9:01 rated 132.1, a 4:13
#   128.3, when the athletes who run 9:0x for a 3200 run 4:12-4:14 for a
#   1600 in the same season (scripts/diag_event_pairs.py). The rows
#   cannot see it: the top band is populated by the event people are
#   good at, and the row-level fit weights every dual-meet 1600 a
#   distance runner jogs. The relation people actually reason with is
#   season best against season best. So each (pool, event, band) class
#   with DIST_CAL_MIN_PAIRS athlete-seasons that ran both the event and
#   the reference event gets a PRIOR MEAN: the median over those
#   athlete-seasons of log(best at the event) - log(best at the
#   reference), in normalized time, banded by the solve's own rating of
#   the athlete-season (the average of everything they ran, so a miler
#   and a distance runner of the same ability land in the same band);
#   the class's penalty is then not a fixed sd (a fixed sd is nothing
#   against fifty thousand rows) but DIST_CAL_SHARE of the class's own
#   row information: the solved e is that share of the pairs' median and
#   the rest the rows' answer. Classes with fewer pairs keep the loose
#   zero prior. Refreshed every outer iteration with the bands. The
#   bests are COUNT-MATCHED: the min of 6 draws sits lower than the min
#   of 3, and the 1600 is raced twice as often as the 3200, so a plain
#   best-vs-best would hand the 3200 about 1.3% MORE credit -- the wrong
#   way. Each athlete-season's best at each side of a pair is taken over
#   the same number of races, a random subset of the side with more.
DIST_CAL_SHARE = 0.9

# ★ THE WINTER GAIN, STATED PER ABILITY (issue 194, 2026-09-06). The curve
#   pin (WINTER_GAIN) sets the fall-to-spring gain of the AVERAGE dual-
#   sport athlete, and the amplitude tilt hands the top two thirds of it;
#   with the count of races and the era on top, the same athlete's spring
#   best rated 0.7 UNDER their fall best at every level (diag_cross_sport
#   _pairs, 2024+), a 9:01 level with the 15:40 those athletes ran at Mt.
#   SAC in October. How much fitter a runner is in May than at their
#   November best is not in the data (it is the same person months apart)
#   -- it is the owner's number, and it can differ by ability. So the
#   go-live applies, to every track row, the shift that makes the dual-
#   sport page gap in each rating band (DIST_BANDS: low / middle / top)
#   equal the stated gain for that band, measured on the rows it is about
#   to publish: per (pool, band), the mean over athlete-seasons raced in
#   both sports of (mean log adjusted time over track rows - over XC
#   rows); the shift is that mean plus the target, interpolated linearly
#   between the band anchors so nothing jumps at 105 or 120. Written to
#   sport_gain for the conversions page. Off (no shift) unless
#   --winter-gain-bands is given.
SPORT_GAIN_ANCHORS = (90.0, 112.0, 130.0)
SPORT_GAIN_MIN_ATHLETES = 50


def distOffsetRow(D, e, rating_row):
    """The event offset each row's RATING applies: the class's three band
    values interpolated at the athlete-season's rating between the band
    anchors (SPORT_GAIN_ANCHORS), flat beyond -- no step at 105 or 120
    (2026-09-06, the owner: "band edges"). The SOLVE keeps the hard bands
    (a fit); an unbanded design gets its one value."""
    if not getattr(D, "n_e", 0) or e is None:
        return np.zeros(D.n)
    if not getattr(D, "dist_banded", False):
        return D.e_w * e[D.e_idx]
    nb = D.n_e_base
    table = np.asarray(e).reshape(nb, DIST_N_BAND)          # class x band
    r = np.nan_to_num(np.asarray(rating_row, dtype=np.float64), nan=100.0)
    anchors = np.asarray(DIST_BAND_ANCHORS)
    x = np.clip(r, anchors[0], anchors[-1])
    # piecewise-linear over the three anchors, vectorised per row
    j = np.clip(np.searchsorted(anchors, x, side="right") - 1, 0, DIST_N_BAND - 2)
    t = (x - anchors[j]) / (anchors[j + 1] - anchors[j])
    lo = table[D.e_base, j]
    hi = table[D.e_base, j + 1]
    return D.e_w * (lo + t * (hi - lo))


def altitudeCredit(D):
    """Per row, the km the RATING credits (ALT_ACCLIM): the venue's km
    above the floor less the acclimatisation share of the race's field's
    mean home km -- one number per race, so everyone in it gets the
    same. The solve's own exposure where no home is known."""
    if not getattr(D, "n_k", 0):
        return None
    home = getattr(D, "alt_home", None)
    dist_f = getattr(D, "alt_dist", None)
    if dist_f is None:
        dist_f = np.ones(D.n)
    if home is None or not np.any(home):
        return D.alt
    field = (np.bincount(D.race, weights=home, minlength=D.n_race)
             / np.maximum(np.bincount(D.race, minlength=D.n_race), 1))
    # the event's share of the cost rides on the credit as it does on the
    # exposure (ALT_DIST_KNOTS): an 800 at altitude earns a fifth of a 5000's
    return np.maximum(D.alt_venue - ALT_ACCLIM * field[D.race], 0.0) * dist_f


def homeAltitude(alt_row, known_row, athlete, n_ath):
    """Per athlete-season, the median venue km over its rows with a known
    elevation (0 where none is known): where they live, as far as the
    races say."""
    alt_row = np.asarray(alt_row, dtype=np.float64)
    known = np.asarray(known_row, dtype=bool)
    athlete = np.asarray(athlete, dtype=np.int64)
    home = np.zeros(n_ath)
    idx = np.flatnonzero(known)
    if idx.size == 0:
        return home
    order = np.lexsort((alt_row[idx], athlete[idx]))
    a_s, v_s = athlete[idx][order], alt_row[idx][order]
    first = np.flatnonzero(np.r_[True, a_s[1:] != a_s[:-1]])
    cnt = np.diff(np.r_[first, a_s.size])
    mid = first + (cnt - 1) // 2
    home[a_s[mid]] = v_s[mid]
    return home


def sportGainShift(log_adj, sport, athlete, rating_ath, pool_ath, n_pool,
                   gains, anchors=SPORT_GAIN_ANCHORS,
                   min_athletes=SPORT_GAIN_MIN_ATHLETES):
    """Per (pool, band): the realised dual-sport gap in log adjusted time
    (track minus XC, negative = track rates higher), the athletes behind
    it, and the shift that turns it into -gains[band]. Returns
    (shift[n_pool, n_band], gap[n_pool, n_band], n[n_pool, n_band])."""
    gains = np.asarray(gains, dtype=np.float64)
    nb = len(anchors)
    assert gains.size == nb, "one gain per band"
    log_adj = np.asarray(log_adj, dtype=np.float64)
    sport = np.asarray(sport)
    athlete = np.asarray(athlete, dtype=np.int64)
    n_ath = int(rating_ath.size)
    tf = sport == 1
    ok = np.isfinite(log_adj)
    sum_tf = np.bincount(athlete[tf & ok], weights=log_adj[tf & ok], minlength=n_ath)
    cnt_tf = np.bincount(athlete[tf & ok], minlength=n_ath)
    sum_xc = np.bincount(athlete[~tf & ok], weights=log_adj[~tf & ok], minlength=n_ath)
    cnt_xc = np.bincount(athlete[~tf & ok], minlength=n_ath)
    both = (cnt_tf > 0) & (cnt_xc > 0) & np.isfinite(rating_ath) & (pool_ath >= 0)
    gap_ath = np.where(both, sum_tf / np.maximum(cnt_tf, 1)
                       - sum_xc / np.maximum(cnt_xc, 1), 0.0)
    band = np.digitize(np.nan_to_num(rating_ath, nan=100.0), SPORT_GAIN_BANDS)
    key = np.clip(pool_ath, 0, None) * nb + band
    n = np.bincount(key[both], minlength=n_pool * nb).reshape(n_pool, nb)
    tot = np.bincount(key[both], weights=gap_ath[both],
                      minlength=n_pool * nb).reshape(n_pool, nb)
    gap = np.where(n > 0, tot / np.maximum(n, 1), np.nan)
    shift = np.where(n >= min_athletes, gap + gains[None, :], 0.0)
    # a band too thin to measure borrows its pool's nearest measured band
    for p in range(n_pool):
        have = n[p] >= min_athletes
        if have.any() and not have.all():
            shift[p, ~have] = np.interp(np.asarray(anchors)[~have],
                                        np.asarray(anchors)[have], shift[p, have])
    return shift, gap, n


def sportGainRow(rating_row, pool_row, shift, anchors=SPORT_GAIN_ANCHORS):
    """The per-row shift: the pool's band shifts interpolated at the
    row's athlete-season rating, flat beyond the outer anchors."""
    r = np.nan_to_num(np.asarray(rating_row, dtype=np.float64), nan=100.0)
    out = np.zeros(r.size)
    for p in np.unique(pool_row):
        if p < 0:
            continue
        m = pool_row == p
        out[m] = np.interp(r[m], np.asarray(anchors), shift[p])
    return out
DIST_CAL_MIN_PAIRS = 150

# ★ THE PER-ATHLETE ENDURANCE SLOPE (issue 154, 2026-09-03). One number per
#   athlete-season, how steeply THEIR log time rises with log distance
#   beyond the pool's law, times the row's log distance centred on the
#   distances that athlete-season raced. A miler's 3200 is slower than the
#   pool law says from their 1600 and a strength runner's is faster; today
#   that leaks into the ability, the sport offset and the cells. Centred
#   on the athlete's own distances so the ability keeps its meaning (the
#   speed at the distances they race) and moves little. Like the sport
#   offset it is NOT in a race rating: same time, same course, same day is
#   the same number for everyone. Ridge in row units, like SPORT_RIDGE:
#   a season racing one distance has every lz = 0 and the slope is exactly
#   zero, which is the pool's law.
SLOPE_RIDGE = 2.0

# ★ THE SEASON LINK (issue 154). Consecutive athlete-seasons of one person
#   (same person, same pool) are tied by a zero-mean difference penalty of
#   LINK_WEIGHT / years between them, in row units. Less than one race's
#   worth of evidence, so it steadies a one- or two-race season against
#   the neighbouring years and does nothing to a full one. It is a
#   smoothing prior: it says nothing about the winter gain, and it pulls
#   year-to-year change toward zero, which is why it must stay weak.
LINK_WEIGHT = 0.8

# ★ THE ALTITUDE TERM (issue 172, 2026-09-04; OFF unless run_joint
#   --altitude). One coefficient per sport group times the row's venue
#   elevation above ALT_FLOOR_M, in km. In the ROW model, not the cell's
#   prior: a resident of altitude has every ability-row at altitude, so
#   without this term their ability IS their altitude-slowed speed, they
#   run "as expected" at Simplot and contribute nothing to its cell, and
#   the sea-level visitors contribute the whole penalty -- the cell lands
#   between, residents' ratings there come out inflated and their careers
#   sit below their sea-level worth. With the term in every row, an
#   ability is the sea-level speed for everyone, the cell is terrain, and
#   k is measured by the athletes who race at both elevations. Goes into
#   the per-race effect like difficulty. The ridge is only numerical.
ALT_FLOOR_M = 600.0
ALT_RIDGE = 1e-6
# ★ THE PHYSIOLOGY IS THE PRIOR, AND BY DEFAULT IT DECIDES (2026-09-04).
#   The synthetic world showed the fitted k is a prior-side split with the
#   cells (about half the truth), because a closed altitude community
#   cannot separate "slow venue" from "slow athletes". The owner's case is
#   exactly that community: altitude-to-altitude runners are a minority at
#   Simplot, their abilities carry their home altitude, and the sea-level
#   field's credit lands on them there. With k held at the distance-
#   running literature's ~3.5% per km above 600 m, every altitude row gets
#   the same credit whoever ran it: home rows and Simplot rows alike, so
#   the resident's ability is their sea-level speed and Simplot is
#   terrain. run_joint --altitude-fit loosens the prior to let the bridge
#   athletes move k; the log prints where it landed either way.
ALT_PRIOR_MEAN = 0.035          # log-time per km above ALT_FLOOR_M
ALT_PRIOR_PEN_FIXED = 1e9       # row units: physiology decides
ALT_PRIOR_PEN_FIT = 20.0        # row units: about sd 0.01 around it
# ★ ACCLIMATISATION (issue 192, 2026-09-06). A Flagstaff resident's 15:31
#   at Buffalo Park (2,100 m) out-rated a 14:10 course record at Mt. SAC
#   because every row at altitude got the full 5% credit whoever ran it.
#   An acclimatised resident recovers about half the acute loss (the
#   literature's 3-4% at 2,000 m against 6-7% acute), so in the SOLVE the
#   row's exposure is venue km minus ALT_ACCLIM x the athlete-season's
#   HOME km (the median elevation of the venues they raced that season):
#   a resident's ability is their sea-level speed with the right credit,
#   a visitor's is unchanged. In the RATING a race must give everyone in
#   it the same credit (finish order is sacred), so the credit at a race
#   is venue km minus ALT_ACCLIM x the FIELD's mean home km: a resident
#   field at Flagstaff earns half, a sea-level field at a national meet
#   at altitude earns all of it. altitudeCredit() is that number.
ALT_ACCLIM = 0.5
# ★ ALTITUDE COSTS THE 800 A FIFTH OF WHAT IT COSTS THE 5000 (2026-09-11).
#   The NCAA conversion tables at Albuquerque (1,511 m) are purely
#   multiplicative and run 800 -0.56%, mile -2.18%, 3000 -2.46%, 5000 -2.64%
#   (Hamlin et al. 2015: 4-6% for 5k-10k at altitude); Peronnet 1991 puts
#   the aerobic share past half at about 100 s of racing, and it is the
#   aerobic share that thin air taxes. One coefficient per sport was the
#   average of those, so the 800 was over-credited and the 10k under. The
#   row's exposure is scaled by the event's share of the 5000's cost,
#   piecewise-linear in log distance; an XC row (5 km or so) reads 1.0 and
#   a row with no distance reads 1.0, exactly as before.
ALT_DIST_KNOTS = ((800.0, 0.21), (1600.0, 0.83), (3000.0, 0.93),
                  (5000.0, 1.00), (10000.0, 1.05))


def altDistanceFactor(dist_m):
    """Per row, the event's altitude cost as a share of the 5000's;
    1.0 where the distance is unknown (0 or negative)."""
    d = np.asarray(dist_m, dtype=np.float64)
    known = d > 0
    xs = np.log([k for k, _v in ALT_DIST_KNOTS])
    ys = np.array([v for _k, v in ALT_DIST_KNOTS])
    out = np.ones(d.shape)
    if known.any():
        out[known] = np.interp(np.log(d[known]), xs, ys)
    return out

# ★ THE SEASON-END TAPER TERM (issue #22). A tapered, qualified field runs
#   a couple of percent faster than the same athletes mid-season (a 2-week
#   taper is worth about 2-3%: Bosquet et al. 2007; Mujika & Padilla
#   2003), and a venue that hosts only such fields would otherwise book the
#   taper as an easy course. The term is a shared coefficient per (pool,
#   sport), log-time per unit of the row's race covariate, untilted, and
#   it is never applied in a rating: a tapered race is a real performance.
#   ★★ NOT FROM MEET NAMES (owner, 2026-09-11: "no one is tapering for
#      their league championship, but they are for their state meet";
#      "it's so easy for it to go bad. Some other system would be best").
#      The covariate is the race's SEASON-END SHARE: the fraction of its
#      voting field for whom this race falls within two weeks of the last
#      race of their own season (run_joint.seasonEndShare; a voter has
#      three or more races and a season that has closed). A state final is
#      a race where nearly everyone's season ends; a mid-season
#      invitational is one where nearly nobody's does; a league meet sits
#      wherever its own field puts it. Read off the athletes' calendars in
#      the pack, so it needs no label and cannot misread a name. The name
#      classes (meet_class.py) are kept only as a diagnostic the log
#      cross-tabulates the share against.
#   ⚠ THE PRIOR MEAN IS ZERO (the owner: "I could see that going very
#     wrong"). The literature's -2% is the size to EXPECT in the log, not a
#     number to apply: a corpus that shows no taper carries none.
IMP_PRIOR_MEAN = 0.0
IMP_EXPECTED = -0.02                 # per unit share: what a healthy fit looks like
# ⚠ SIZED AS A PRIOR SD, LIKE DIST_PRIOR_SD, NOT AS PSEUDO-ROWS. Within a
#   race the term and the race-day term are collinear; what separates them
#   is this prior against the race-day prior over ALL the races that carry
#   a share -- sigma_u2 per race, tens of thousands of them -- and the
#   venues whose races carry different shares. A penalty stated in rows (a
#   first cut used 50) competed with that sum on a small world and halved
#   the estimate. pen = sigma2 / SD^2 is a few row-units.
IMP_PRIOR_SD = 0.02
# ★★ THE FIELD-STRENGTH TERM (owner, 2026-09-11: "if there is a race that is
#    very top-heavy, where people will run fast because there's more
#    competition, those races should get some refund to their difficulty").
#    The covariate is the race's FRONT: the mean rating of its top FIELD_TOP_K
#    rows, from the model's own ratings each pass (like the tilt), centred at
#    the median race of its (pool, sport) and in units of FIELD_UNIT rating
#    points -- a dual meet sits a unit or two below zero, a national final
#    three to five above. Constant within a race, so finishing order is
#    untouched; identified across races by the same athletes in stronger
#    and weaker fields, and by venues that host both. Untilted, zero prior,
#    NOT in a rating: a fast time in a stacked field is a real performance,
#    and the course keeps its difficulty (the "refund"). How much is the
#    data's to say: FIELD_EXPECTED is what a healthy fit looks like, and
#    run_joint.reportFieldByBand prints the residual by strength band so
#    the shape can be read, not assumed. The default covariate since
#    2026-09-11 (run_joint --importance field); the season-end share stays
#    as --importance season-end.
FIELD_TOP_K = 5
FIELD_UNIT = 10.0
FIELD_CLIP = (-4.0, 6.0)
FIELD_EXPECTED = -0.005              # per unit: a few tenths of a percent
# ★ THE INDOOR PRIOR: the NCAA facility factors (2012) and World Athletics'
#   2025 short-track tables both put a 200 m oval 0.8-1.8% slower than
#   outdoors for 800-5000, larger for the faster and the shorter. One
#   number per pool, the data move it.
IND_PRIOR_MEAN = 0.012
IND_PRIOR_SD = 0.01
# ★★ INDOOR IS SEASON (owner, 2026-09-11: "I think indoor might be off";
#    the page read every indoor oval 2.4-3.8% EASIER than outdoors, which
#    is backwards). No location hosts both an indoor and an outdoor track
#    and nobody races indoors in May, so the form curve's Dec-Mar level and
#    the indoor cells' mean are ONE free direction: the smooth curve
#    interpolates the fall-to-spring gain through the winter and the indoor
#    cells absorb the difference. Exactly the XC/TF problem one level down,
#    with the same answer: the level is ASSERTED (the NCAA facility factors
#    and the WA short-track tables), taken off y like mu_fixed, and the
#    indoor cells are recentred to it every pass so they keep only their
#    own deviation (a banked BU below it, a flat 200 m oval above). The
#    fitted coefficient (--indoor-level fit) stays for the ladder.
IND_LEVEL_DEFAULT = 0.012


# Amplitude tilt: the season-form swing shrinks with ability
# (rust_fitness._TILT_PER_POINT and its clamps).
AMP_TILT_PER_POINT = 0.01135
AMP_FLOOR, AMP_CEIL = 0.15, 1.80

# Athlete-seasons with fewer races than this do not vote in the pool mean the
# ratings (and so the tilt and amplitude) are anchored on. pair_ratings uses 3.
POOL_MEAN_MIN_RACES = 3

CG_TOL = 1e-8
CG_MAX_ITER = 600
# ★ WHERE THE PRECISION IS SPENT (2026-09-02). The final outer iteration
#   solves to CG_TOL from a warm start. The outers before it only feed the
#   weight and variance updates, and a relative residual of 1e-6 moves
#   those by nothing a reader could see. A posterior probe is a Hutchinson
#   sample whose own error is 1/sqrt(n_probe) -- 25% at 16 -- so solving it
#   to 1e-8 was precision nobody used.
# ⚠ CG_TOL_OUTER WENT BACK TO CG_TOL (2026-09-02, the first live run).
#   At 1e-6 the five early outers left the LEVEL direction -- the
#   XC-to-track offset, which trades off against the sport offset and is
#   the slowest direction to converge -- unfinished, and the tight final
#   solve moved it from 0.047 to 0.079 in one step. The weights and
#   variances had been estimated under the old level. Warm starts make the
#   later outers cheap at full tolerance anyway; the saving was not worth a
#   level that swings on the last iteration. The probes keep 1e-4: they
#   size uncertainties, they do not move the point estimate.
CG_TOL_OUTER = CG_TOL
CG_TOL_PROBE = 1e-4
# ⚠ AND A PROBE IS CAPPED (first live run, 2026-09-03). A random probe
#   excites the level direction the solve finds hardest, and a probe that
#   cannot reach 1e-4 ran to CG_MAX_ITER: sixteen of those took most of a
#   night. At 150 iterations a probe is a usable Hutchinson sample -- the
#   estimate's own error is 1/sqrt(16) -- and the pass is under an hour.
CG_MAX_ITER_PROBE = 150
# bincount and fancy indexing release the GIL, so two different things run
# on this pool -- and they scale differently, which is the whole reason to
# read this before tuning XCP_THREADS:
#
#   _Operator._reduce   the block reductions (the adjoint's bincounts). One
#                       job per model term, so 8-9 of them. Threads past
#                       that sit idle, and the scatter is memory-bound
#                       anyway -- this is where the old cap of 4 came from.
#
#   rowPrediction       splits the ROWS into _N_THREADS chunks (~60M of
#                       them). This one scales with cores, not with the
#                       number of model terms, and it is the bigger half of
#                       every CG iteration: measured at 0.9 s of 1.2 s per
#                       8M rows on four cores (2026-09-06).
#
# ⚠ SO THE JOB COUNT IS NOT THE CEILING. Sizing this to ~9 because that is
#   how many reduction jobs exist leaves the dominant term -- the gather --
#   on a fraction of the box. On the 32-core server the default of 8 runs
#   rowPrediction on a quarter of it. Raise XCP_THREADS toward the core
#   count and measure the per-outer line; the gather keeps paying until
#   memory bandwidth saturates, and only the reductions stop caring.
# ★ THE DEFAULT FOLLOWS THE BOX (owner, 2026-09-09: "yes do that free
#   thing"). It was a flat 8, which on the 32-core server ran rowPrediction
#   -- the bigger half of every CG iteration -- on a quarter of the machine
#   while the reductions, which cannot use more than their ~9 jobs, were
#   never the reason for the cap. An env var you have to remember every run
#   is not a default.
#
# ! CAPPED AT 24, not left at cpu_count. The gather is memory-bandwidth
#   bound at the top end and the reductions stop caring past their job
#   count, so the last threads buy little and contend for the same
#   bandwidth. On a box with 24 cores or fewer this changes nothing.
#   XCP_THREADS still overrides in both directions.
_N_THREADS = max(1, min(int(os.environ.get("XCP_THREADS", "0")) or
                        min(os.cpu_count() or 1, 24),
                        os.cpu_count() or 1))


# ------------------------------------------------------------------ #
# THE DESIGN
# ------------------------------------------------------------------ #

class Design:
    """Index arrays for one set of rows, and the packing of theta.

    Required: athlete, cell, race (per row, dense codes).
    Optional: group_of_cell (per CELL; default one group), sc (per row,
    centred sport indicator -> beta block), pool_row + day (per row -> curve
    block; pool_row is the curve family, day the day-of-year), first (per
    row bool -> rust block, one coefficient per pool_row family).
    Sizes may be passed to keep a held-out design aligned with the training
    one; otherwise they come from the arrays.
    """

    def __init__(self, athlete, cell, race, group_of_cell=None, sc=None,
                 pool_row=None, day=None, first=None,
                 n_ath=None, n_cell=None, n_race=None, n_pool=None,
                 n_knot=CURVE_N_KNOTS, knot_days=CURVE_KNOT_DAYS,
                 dist=None, n_e=None, lz=None, link=None, alt=None,
                 dist_ref=None, alt_home=None,
                 dist_banded=False,
                 era_pairs=None, era_w=None, eras_per_base=None,
                 mu_fixed=None, imp=None, n_imp=None, imp_prior=None,
                 ind=None, e_table=None, alt_dist=None, imp_w=None,
                 imp_kind=None, ind_fixed=None):
        self.athlete = np.asarray(athlete, dtype=np.int64)
        self.cell = np.asarray(cell, dtype=np.int64)
        self.race = np.asarray(race, dtype=np.int64)
        self.n = self.athlete.size
        self.n_ath = int(n_ath if n_ath is not None else self.athlete.max() + 1)
        self.n_cell = int(n_cell if n_cell is not None else self.cell.max() + 1)
        self.n_race = int(n_race if n_race is not None else self.race.max() + 1)

        g = (np.zeros(self.n_cell, dtype=np.int64) if group_of_cell is None
             else np.asarray(group_of_cell, dtype=np.int64))
        self.group_of_cell = g
        self.n_group = int(g.max()) + 1
        self.group_row = g[self.cell]

        # ★ GROUP 0 IS THE REFERENCE: mu[0] = 0 by construction and the
        #   abilities carry that level. An unpenalised mu for every group
        #   would leave (a + c, mu - c) as an exact null direction, and CG
        #   on a singular operator wanders along it -- the posterior-variance
        #   probes came back six orders of magnitude too large that way.
        self.n_mu = self.n_group - 1
        self.mu_idx = np.maximum(self.group_row - 1, 0)
        self.mu_w = (self.group_row > 0).astype(np.float64)
        # ★ THE SPORT LEVEL, ASSERTED (2026-09-11). Sport is season, so the
        #   between-sport level is a definition, not an estimate (see
        #   XC_TRACK_GAP). With mu_fixed the level is NOT a parameter at
        #   all: the block is empty and solveJoint subtracts h * mu_fixed
        #   from y before every pass. --merge-sports used to leave mu in
        #   theta unpenalised and merely refused to bank the cell means
        #   into it, so CG parked whatever it liked there along the
        #   near-null direction against the curve.
        self.mu_fixed = (None if mu_fixed is None
                         else np.asarray(mu_fixed, dtype=np.float64))
        if self.mu_fixed is not None:
            assert self.mu_fixed.size == self.n_group, \
                "mu_fixed wants one level per group"
            self.n_mu = 0
            self.mu_w = np.zeros(self.n)

        # ★ THE RANDOM WALK OVER ERAS. era_pairs is (2, M): each column is
        #   an adjacent pair of era-cells belonging to the SAME course, and
        #   era_w is 1/(era gap) so a course that skipped four years is tied
        #   more loosely than one measured back to back -- a random walk's
        #   variance grows with elapsed time.
        self.era_pairs = (None if era_pairs is None
                          else np.asarray(era_pairs, dtype=np.int64))
        self.era_w = (None if era_w is None
                      else np.asarray(era_w, dtype=np.float64))
        self.n_era_pair = 0 if self.era_pairs is None else self.era_pairs.shape[1]
        # ⚠ HOW MANY ERAS EACH COURSE WAS SPLIT INTO, so the tau prior can be
        #   divided by it. tau is a prior on a COURSE's level; applied whole
        #   to each of eight eras it would shrink an eight-era course eight
        #   times as hard as a one-era course toward the sport's mean, which
        #   is a shrinkage that depends on how long a venue has existed.
        self.eras_per_base = (np.ones(self.n_cell) if eras_per_base is None
                              else np.asarray(eras_per_base,
                                              dtype=np.float64))

        self.sc = None if sc is None else np.asarray(sc, dtype=np.float64)

        self.pool_row = (None if pool_row is None
                         else np.asarray(pool_row, dtype=np.int64))
        self.n_pool = int(n_pool if n_pool is not None else
                          (0 if self.pool_row is None
                           else self.pool_row.max() + 1))
        self.n_knot = int(n_knot)
        self.knot_days = float(knot_days)
        self.has_curve = self.pool_row is not None and day is not None
        self.n_c = 0
        if self.has_curve:
            t = academicDay(np.asarray(day, dtype=np.float64))
            k0 = np.minimum((t // self.knot_days).astype(np.int64),
                            self.n_knot - 2)
            w1 = np.clip((t - k0 * self.knot_days) / self.knot_days, 0.0, 1.0)
            self.k0 = self.pool_row * self.n_knot + k0       # full-grid index
            self.k1 = self.k0 + 1
            self.w0 = 1.0 - w1
            self.w1 = w1
            # ★ ONE REFERENCE KNOT PER POOL IS PINNED AT ZERO (CURVE_REF_KNOT,
            #   1 October), for the same reason as mu[0]: a pool's curve
            #   plus a constant is its abilities minus that constant. The
            #   free block holds the other knots; the row basis drops the
            #   pinned one by zeroing its weight.
            grid = np.arange(self.n_pool * self.n_knot)
            is_ref = (grid % self.n_knot) == CURVE_REF_KNOT
            self.col_of = np.full(grid.size, -1, dtype=np.int64)
            self.col_of[~is_ref] = np.arange(int((~is_ref).sum()))
            self.n_c = int((~is_ref).sum())
            self.c0 = np.maximum(self.col_of[self.k0], 0)
            self.c1 = np.maximum(self.col_of[self.k1], 0)
            self.w0 = np.where(self.col_of[self.k0] < 0, 0.0, self.w0)
            self.w1 = np.where(self.col_of[self.k1] < 0, 0.0, self.w1)
            self.free_grid = np.flatnonzero(~is_ref)
        self.has_rust = first is not None and self.pool_row is not None
        if self.has_rust:
            self.first = np.asarray(first, dtype=np.float64)

        # the track distance offset (DIST_PRIOR_SD): per row a FREE class
        # index, or -1 for no class -- an XC row, the pinned reference
        # event, or no distance. e_w zeroes the -1 rows in every product.
        self.n_e = 0
        self.dist_banded = False
        if dist is not None:
            dist = np.asarray(dist, dtype=np.int64)
            self.e_base = np.maximum(dist, 0)
            self.e_w = (dist >= 0).astype(np.float64)
            n_base = int(n_e if n_e is not None
                         else (int(dist.max()) + 1 if dist.size else 0))
            self.n_e_base = max(n_base, 0)
            self.dist_banded = bool(dist_banded) and self.n_e_base > 0
            if self.dist_banded:
                self.n_e = self.n_e_base * DIST_N_BAND
                self.e_idx = self.e_base * DIST_N_BAND + 1     # middle band
            else:
                self.n_e = self.n_e_base
                self.e_idx = self.e_base
            if self.n_e <= 0:
                self.n_e = 0
        # the pairs calibration (DIST_CAL_SHARE): per row, is this the
        # pool's pinned reference event; per class, the prior mean and the
        # number of pairs behind it (0 = not calibrated, loose zero prior)
        self.e_ref = (np.asarray(dist_ref, dtype=bool)
                      if dist_ref is not None and self.n_e else None)
        self.e_mean = np.zeros(self.n_e)
        self.e_cal_n = np.zeros(self.n_e, dtype=np.int64)
        self.e_cal_via = np.full(self.n_e, -1, dtype=np.int64)   # the chain
        # ★ THE PUBLISHED TABLES AS THE PRIOR MEAN (2026-09-11, issues 9/13).
        #   Per class, the log-time offset the World Athletics / Purdy
        #   equivalence implies for the event against the pool's reference
        #   event, LESS what the distance potential already applied -- so a
        #   class with too few season-best pairs to calibrate itself is
        #   pulled toward a stated relation instead of toward "the curve's
        #   tangent extension is right". NaN = no table for that class.
        self.e_table = None
        if e_table is not None and self.n_e:
            t = np.asarray(e_table, dtype=np.float64)
            assert t.size == self.n_e, "e_table wants one value per class"
            self.e_table = t
            self.e_mean[:] = np.where(np.isfinite(t), t, 0.0)

        # the endurance slope (SLOPE_RIDGE): per row the centred log distance
        self.has_slope = lz is not None
        self.n_g = 0
        if self.has_slope:
            self.lz = np.asarray(lz, dtype=np.float64)
            self.n_g = self.n_ath
        # the season link (LINK_WEIGHT): pairs of athlete-season indices and
        # a weight per pair (1 / years apart)
        self.has_link = link is not None and len(link[0]) > 0
        if self.has_link:
            self.link_k0 = np.asarray(link[0], dtype=np.int64)
            self.link_k1 = np.asarray(link[1], dtype=np.int64)
            self.link_w = np.asarray(link[2], dtype=np.float64)
        # the altitude term (ALT_FLOOR_M): per row, km of venue elevation
        # above the floor, 0 where unknown; one coefficient per group
        self.n_k = 0
        if alt is not None:
            self.alt_venue = np.asarray(alt, dtype=np.float64)
            self.alt_home = (np.zeros(self.n) if alt_home is None
                             else np.asarray(alt_home, dtype=np.float64))
            # the event's share of the 5000's altitude cost (ALT_DIST_KNOTS):
            # 1.0 where the caller passes none, so the old designs are exact
            self.alt_dist = (np.ones(self.n) if alt_dist is None
                             else np.asarray(alt_dist, dtype=np.float64))
            # the solve's exposure (ALT_ACCLIM): the venue's km less the
            # share of home km an acclimatised athlete has recovered, scaled
            # by the event's share of the cost
            self.alt = (self.alt_venue - ALT_ACCLIM * self.alt_home) * self.alt_dist
            self.n_k = self.n_group

        # ★ THE MEET-IMPORTANCE TERM (issue #22, 2026-09-11). Per row a
        #   coefficient index (-1 = the reference class, an ordinary
        #   invitational or dual) for the championship class of the meet
        #   the row was run at, keyed by the caller (run_joint: per pool,
        #   sport and class). A tapered, qualified, motivated field runs
        #   2-3% faster than the same athletes mid-season (Bosquet 2007,
        #   Mujika & Padilla 2003); without a shared term for that, a venue
        #   that hosts ONLY championships books the taper as an easy course
        #   (Foot Locker, NXN, every state meet), because u is a deviation
        #   around the cell's own mean and cannot see a constant. The term
        #   is identified globally: nearly every athlete races both kinds
        #   of meet, and on venues that host a mix the course and the class
        #   come apart. It is a level covariate, not a weight (an Elo
        #   K-factor by tournament class fixes the update, not the expected
        #   time). Untilted. NOT in a rating: the tapered race is a real
        #   performance, exactly as the race-day term is left in.
        self.n_imp = 0
        self.imp_kind = None
        self.field_strength = None
        self.field_centre = None
        if imp is not None:
            imp = np.asarray(imp, dtype=np.int64)
            self.imp_idx = np.maximum(imp, 0)
            self.imp_mask = imp >= 0
            # the weight is 0/1 from the index, a caller's continuous
            # covariate (the race's season-end share), or -- "field" -- the
            # race's front strength, recomputed from the model's own
            # ratings each pass (fieldStrength): zero on the first pass and
            # live from the second, like the tilt
            self.imp_kind = imp_kind or ("share" if imp_w is not None else "flag")
            if self.imp_kind == "field":
                self.imp_w = np.zeros(self.n)
            elif imp_w is not None:
                w_ = np.asarray(imp_w, dtype=np.float64)
                assert w_.size == self.n, "imp_w wants one weight per row"
                self.imp_w = np.where(imp >= 0, w_, 0.0)
            else:
                self.imp_w = (imp >= 0).astype(np.float64)
            n_i = int(n_imp if n_imp is not None
                      else (int(imp.max()) + 1 if imp.size and imp.max() >= 0
                            else 0))
            self.n_imp = max(n_i, 0)
            self.imp_prior = (np.zeros(self.n_imp) if imp_prior is None
                              else np.asarray(imp_prior, dtype=np.float64))
            if self.n_imp:
                assert self.imp_prior.size == self.n_imp
        # ★ INDOOR AS A SHARED TERM (2026-09-11). 1,574 indoor cells and
        #   25,629 outdoor ones, and no location hosts both, so each
        #   indoor cell used to rediscover the surface from its own thin
        #   evidence under a prior (tau[TF]) narrower than the effect
        #   itself. One coefficient per pool (speed and sex decide the
        #   curve cost: NCAA flat->banked factors run 1.4% at 800 to 1.1%
        #   at 5000 for men, 0.8-1.2% for women), multiplied by the
        #   cell's indoor flag, tilted like the course because it IS part
        #   of the course. The cells keep only their deviation from it.
        #   solveJoint folds the cell's mean indoor term into delta, so
        #   the go-live and the display see one course number.
        self.n_ind = 0
        self.ind_fixed = None
        if ind is not None:
            ind = np.asarray(ind, dtype=bool)
            assert ind.size == self.n_cell, "ind wants one flag per cell"
            self.ind_cell = ind
            self.ind_w = ind[self.cell].astype(np.float64)
            if self.pool_row is not None:
                self.ind_idx = self.pool_row
                self.n_ind = max(self.n_pool, 1) if ind.any() else 0
            else:
                self.ind_idx = np.zeros(self.n, dtype=np.int64)
                self.n_ind = 1 if ind.any() else 0
            # ★ AN ASSERTED INDOOR LEVEL (IND_LEVEL_DEFAULT) is not a
            #   parameter: solveJoint takes h * level off y (fixedOffset)
            #   and recentreLevels holds the indoor cells' mean at zero, so
            #   the level is the whole of it and the cells keep only their
            #   own deviation
            if ind_fixed is not None and ind.any():
                lvl = np.asarray(ind_fixed, dtype=np.float64).ravel()
                n_lvl = max(self.n_ind, 1)
                if lvl.size == 1:
                    lvl = np.full(n_lvl, float(lvl[0]))
                assert lvl.size == n_lvl, "ind_fixed wants one level per pool"
                self.ind_fixed = lvl
                self.n_ind = 0

        # packing
        self.o_a = 0
        self.o_d = self.n_ath
        self.o_u = self.o_d + self.n_cell
        self.o_mu = self.o_u + self.n_race
        self.o_beta = self.o_mu + self.n_mu
        self.n_beta = self.n_ath if self.sc is not None else 0
        self.o_c = self.o_beta + self.n_beta
        self.o_r = self.o_c + self.n_c
        self.n_r = self.n_pool if self.has_rust else 0
        self.o_e = self.o_r + self.n_r
        self.o_g = self.o_e + self.n_e
        self.o_k = self.o_g + self.n_g
        self.o_imp = self.o_k + self.n_k
        self.o_ind = self.o_imp + self.n_imp
        self.n_total = self.o_ind + self.n_ind

    def fixedOffset(self, h):
        """The asserted parts of a row's prediction, tilted: the sport
        level (mu_fixed) and the indoor level (ind_fixed). Off y before
        the solve, back on after; zeros when neither is asserted."""
        off = np.zeros(self.n)
        mu_fixed = getattr(self, "mu_fixed", None)
        if mu_fixed is not None:
            off += mu_fixed[self.group_row]
        ind_fixed = getattr(self, "ind_fixed", None)
        if ind_fixed is not None:
            off += self.ind_w * ind_fixed[self.ind_idx]
        return h * off

    def rebandDist(self, rating_row):
        """Re-point every row's offset class at its athlete-season's
        rating band (DIST_BANDS); a no-op for an unbanded design."""
        if not self.dist_banded:
            return
        band = np.digitize(np.asarray(rating_row, dtype=np.float64), DIST_BANDS)
        self.e_idx = self.e_base * DIST_N_BAND + band

    def calibrateDist(self, y, rating_row, min_pairs=DIST_CAL_MIN_PAIRS,
                      seed=0):
        """Set each (class, band)'s prior mean from season-best pairs
        (DIST_CAL_SHARE): the median over athlete-seasons in the band of
        log(best at the event) - log(best at the pool's reference event),
        where the class has at least `min_pairs` such athlete-seasons.
        Count-matched: an athlete-season with 6 reference races and 3 at
        the event has its best taken over a random 3 of each, so the
        event raced more often does not read faster only by being the
        min of more draws. Returns the number of calibrated classes. A
        no-op without the reference rows or the bands."""
        # the tables are the floor: a class the pairs cannot calibrate
        # keeps the published relation, not zero (e_table)
        if self.e_table is not None:
            self.e_mean[:] = np.where(np.isfinite(self.e_table), self.e_table, 0.0)
        else:
            self.e_mean[:] = 0.0
        self.e_cal_n[:] = 0
        self.e_cal_via[:] = -1
        if not self.dist_banded or self.e_ref is None or not self.e_ref.any():
            return 0
        y = np.asarray(y, dtype=np.float64)
        band = np.digitize(np.asarray(rating_row, dtype=np.float64), DIST_BANDS)
        ath_band = np.zeros(self.n_ath, dtype=np.int64)
        ath_band[self.athlete] = band
        nb = self.n_e_base
        rnd = np.random.default_rng(seed).random(self.n)

        def _ranked(rows, key):
            """Rows ordered by (key, random), each row's position within
            its key group, and the group sizes by key."""
            order = np.lexsort((rnd[rows], key))
            rows, key = rows[order], key[order]
            first = np.r_[True, key[1:] != key[:-1]]
            start = np.maximum.accumulate(np.where(first, np.arange(key.size), 0))
            return rows, key, np.arange(key.size) - start

        free = np.flatnonzero(self.e_w > 0)
        f_rows, f_key, f_pos = _ranked(free, self.athlete[free] * nb
                                       + self.e_base[free])
        n_y = np.bincount(f_key, minlength=self.n_ath * nb)
        ref = np.flatnonzero(self.e_ref)
        r_rows, r_ath, r_pos = _ranked(ref, self.athlete[ref])
        n_ref = np.bincount(r_ath, minlength=self.n_ath)
        # the pool of each class, from its rows (a class never spans pools)
        class_pool = np.zeros(nb, dtype=np.int64)
        if self.pool_row is not None and free.size:
            class_pool[self.e_base[free]] = self.pool_row[free]

        def _side(c):
            """(rows, athlete, position-in-group, count per athlete) of one
            side of a pair: the reference event (c < 0) or free class c."""
            if c < 0:
                return r_rows, r_ath, r_pos, n_ref
            sel = f_key % nb == c
            return f_rows[sel], f_key[sel] // nb, f_pos[sel], n_y[c::nb]

        def _pairs(c, z):
            """Per athlete-season with both sides: log(best at c) - log(best
            at z), count-matched; and the athletes."""
            rc, ac, pc, ncnt = _side(c)
            rz, az, pz, nz = _side(z)
            k = np.minimum(ncnt, nz)
            if not (k > 0).any():
                return None, None
            best_c = np.full(self.n_ath, np.inf)
            m = pc < k[ac]
            np.minimum.at(best_c, ac[m], y[rc[m]])
            best_z = np.full(self.n_ath, np.inf)
            m = pz < k[az]
            np.minimum.at(best_z, az[m], y[rz[m]])
            ath = np.flatnonzero(np.isfinite(best_c) & np.isfinite(best_z))
            return best_c[ath] - best_z[ath], ath

        n_cal = 0
        for c in range(nb):
            d, ath = _pairs(c, -1)
            if d is None or ath.size < min_pairs:
                continue
            b = ath_band[ath]
            for j in range(DIST_N_BAND):
                mm = b == j
                if int(mm.sum()) < min_pairs:
                    continue
                idx = c * DIST_N_BAND + j
                self.e_mean[idx] = float(np.median(d[mm]))
                self.e_cal_n[idx] = int(mm.sum())
                n_cal += 1

        # ★ THE CHAIN (issue 195): an event with too few pairs against the
        #   reference -- the college 10k against the mile -- is calibrated
        #   against the calibrated event of its pool and band it shares the
        #   most athlete-seasons with (the 5k), its offset plus that one's.
        #   Up to three links; a class that stays uncalibrated keeps the
        #   loose zero prior. e_cal_n counts the link's pairs, e_cal_via
        #   names the class it hangs on (-1 = the reference).
        for _round in range(3):
            linked = 0
            for c in range(nb):
                want = [j for j in range(DIST_N_BAND)
                        if self.e_cal_n[c * DIST_N_BAND + j] == 0]
                if not want:
                    continue
                cands = [z for z in range(nb) if z != c
                         and class_pool[z] == class_pool[c]
                         and any(self.e_cal_n[z * DIST_N_BAND + j] > 0
                                 and self.e_cal_via[z * DIST_N_BAND + j] != c
                                 for j in want)]
                best = {}
                for z in cands:
                    d, ath = _pairs(c, z)
                    if d is None or ath.size < min_pairs:
                        continue
                    b = ath_band[ath]
                    for j in want:
                        zidx = z * DIST_N_BAND + j
                        if self.e_cal_n[zidx] == 0 or self.e_cal_via[zidx] == c:
                            continue
                        mm = b == j
                        n_j = int(mm.sum())
                        if n_j >= min_pairs and n_j > best.get(j, (0,))[0]:
                            best[j] = (n_j, z, float(np.median(d[mm])))
                for j, (n_j, z, med) in best.items():
                    idx = c * DIST_N_BAND + j
                    self.e_mean[idx] = med + self.e_mean[z * DIST_N_BAND + j]
                    self.e_cal_n[idx] = n_j
                    self.e_cal_via[idx] = z
                    n_cal += 1
                    linked += 1
            if not linked:
                break
        return n_cal

    def unpack(self, theta):
        """Blocks as FULL arrays: mu over every group (0 for the reference),
        c over the full pool x knot grid (0 at the pinned knot)."""
        mu = np.zeros(self.n_group)
        if self.n_mu:                        # empty under mu_fixed
            mu[1:] = theta[self.o_mu:self.o_beta]
        b = {"a": theta[self.o_a:self.o_d],
             "d": theta[self.o_d:self.o_u],
             "u": theta[self.o_u:self.o_mu],
             "mu": mu}
        b["beta"] = theta[self.o_beta:self.o_c] if self.n_beta else None
        if self.n_c:
            c = np.zeros(self.n_pool * self.n_knot)
            c[self.free_grid] = theta[self.o_c:self.o_r]
            b["c"] = c
        else:
            b["c"] = None
        b["r"] = theta[self.o_r:self.o_e] if self.n_r else None
        b["e"] = theta[self.o_e:self.o_g] if self.n_e else None
        b["g"] = theta[self.o_g:self.o_k] if self.n_g else None
        b["k"] = theta[self.o_k:self.o_imp] if self.n_k else None
        b["imp"] = theta[self.o_imp:self.o_ind] if self.n_imp else None
        b["ind"] = theta[self.o_ind:self.n_total] if self.n_ind else None
        return b



def academicDay(doy):
    """0 at 1 August, so the winter is contiguous instead of wrapping."""
    return (np.asarray(doy, dtype=np.float64) - ACADEMIC_YEAR_START_DOY) % 365.0


# ------------------------------------------------------------------ #
# THE OPERATOR
# ------------------------------------------------------------------ #

def _predictSlice(b, D, h, amp, sl, out):
    """rowPrediction for the rows in slice `sl`, written into out[sl]."""
    ath = D.athlete[sl]
    grp = D.group_row[sl]
    hs = h[sl] if h.shape else h
    row = b["a"][ath] + hs * (b["mu"][grp] + b["d"][D.cell[sl]])
    u = b["u"]
    if u.size == D.n_race:
        row += hs * u[D.race[sl]]
    if b.get("beta") is not None and D.sc is not None:
        row += b["beta"][ath] * D.sc[sl]
    if b.get("c") is not None and D.has_curve:
        c = b["c"]
        a_s = amp[sl] if amp.shape else amp
        row += a_s * (D.w0[sl] * c[D.k0[sl]] + D.w1[sl] * c[D.k1[sl]])
    if b.get("r") is not None and D.has_rust:
        row += D.first[sl] * b["r"][D.pool_row[sl]]
    if b.get("e") is not None and getattr(D, "n_e", 0):
        row += D.e_w[sl] * b["e"][D.e_idx[sl]]
    if b.get("g") is not None and getattr(D, "n_g", 0):
        row += b["g"][ath] * D.lz[sl]
    if b.get("k") is not None and getattr(D, "n_k", 0):
        row += b["k"][grp] * D.alt[sl]
    if b.get("imp") is not None and getattr(D, "n_imp", 0):
        row += D.imp_w[sl] * b["imp"][D.imp_idx[sl]]
    if b.get("ind") is not None and getattr(D, "n_ind", 0):
        row += hs * D.ind_w[sl] * b["ind"][D.ind_idx[sl]]
    out[sl] = row


_PRED_POOL = None


def rowPrediction(b, D, h, amp, u_missing_zero=True):
    """The model's prediction for every row of design D from blocks b.

    ★ THE RACE-DAY EFFECT IS TILTED LIKE THE COURSE (issue 156,
      2026-09-04). Untilted, delta and u were separated within a race
      only by h: the pair (delta = +c, u = -c) cost almost nothing under
      the two priors and bought an ability-shaped spread, (h - 1) * c
      per row, so the solve used it as a free spread parameter. Great
      Park read delta +0.19 with u about -0.2 on EVERY one of its seven
      days, net zero, and the elite rows there lost 7-9%. With h on both,
      delta and u are exactly collinear inside a race and the split is
      the priors' alone: delta is the shrunk mean of the cell's days and
      u is each day's deviation, which is what the column claims.

    ★ IN ROW CHUNKS, ON THREADS (2026-09-06, the owner: "make the engine
      faster"). The gathers were the serial half of every CG iteration
      (0.9 s of 1.2 s per 8M rows on four cores); numpy releases the GIL
      in a gather, so _N_THREADS slices run side by side and each writes
      its own part of one preallocated array. Same numbers to the bit:
      every row's terms are computed in the same order."""
    global _PRED_POOL
    h = np.asarray(h, dtype=np.float64)
    amp = np.asarray(amp, dtype=np.float64)
    out = np.empty(D.n)
    n_chunks = _N_THREADS if D.n >= 2_000_000 else 1
    if n_chunks == 1:
        _predictSlice(b, D, h, amp, slice(0, D.n), out)
        return out
    if _PRED_POOL is None:
        _PRED_POOL = ThreadPoolExecutor(_N_THREADS)
    edges = np.linspace(0, D.n, n_chunks + 1).astype(np.int64)
    futs = [_PRED_POOL.submit(_predictSlice, b, D, h, amp,
                              slice(int(edges[i]), int(edges[i + 1])), out)
            for i in range(n_chunks)]
    for f in futs:
        f.result()
    return out


def _curvePenaltyApply(c_free, D, lam):
    """lam * D2'D2 on the FREE curve block: expand to the full pool x knot
    grid (0 at the pinned knot), penalise, gather back. lam is per pool."""
    v = np.zeros(D.n_pool * D.n_knot)
    v[D.free_grid] = c_free
    v = v.reshape(D.n_pool, D.n_knot)
    z = (v[:, :-2] - 2.0 * v[:, 1:-1] + v[:, 2:]) * lam[:, None]
    out = np.zeros_like(v)
    out[:, :-2] += z
    out[:, 1:-1] -= 2.0 * z
    out[:, 2:] += z
    return out.reshape(-1)[D.free_grid]


def _curvePenaltyDiag(D, lam):
    k = D.n_knot
    col = np.full(k, 6.0)
    col[0] = col[-1] = 1.0
    if k > 1:
        col[1] = col[-2] = 5.0
    if k < 4:                       # degenerate tiny grids
        col[:] = 1.0
    return (lam[:, None] * col[None, :]).reshape(-1)[D.free_grid]



def curveGapVectors(D, w, amp):
    """Per pool, the vector g over the FREE curve block with
    g . c = mean over the pool's non-reference-group rows of amp*f
          - mean over its reference-group rows of amp*f,
    both row-weighted by w. None for a pool that has rows of only one
    group (nothing to balance).

    ★ OVER THE ROWS THAT IDENTIFY THE LEVEL (2026-09-03). A per-result
      rating leaves the curve out, so for an athlete-season raced in both
      sports the track-minus-XC gap of its race ratings is exactly
      -amp * (mean f over its track rows - mean f over its XC rows) minus
      beta. The recentre puts mean beta at zero over those athlete-seasons;
      this pin puts the curve part at zero (or -winter_gain) over the SAME
      rows -- the ones with sc != 0 -- so the stated gain is the gain the
      dual-sport athlete's page shows, not the gain of a pool whose track
      rows are mostly single-sport people on a different calendar (the
      indoor Northeast in January). The live run of 2026-09-03 pinned over
      every row of the pool and the owner read track as underrated against
      XC on dual-sport pages. A pool with no dual-sport rows on one side
      falls back to all its rows."""
    if not D.n_c:
        return []
    ref = D.group_row == 0
    both = None if D.sc is None else np.abs(D.sc) > 1e-9
    wa = w * amp
    vecs = []
    for p in range(D.n_pool):
        m = D.pool_row == p
        if both is not None and (m & both & ref).any() \
                and (m & both & ~ref).any():
            m = m & both
        g = np.zeros(D.n_c)
        ok = True
        for sign, mm in ((-1.0, m & ref), (1.0, m & ~ref)):
            tot = float(w[mm].sum())
            if tot <= 0:
                ok = False
                break
            g += sign * (np.bincount(D.c0[mm], weights=wa[mm] * D.w0[mm],
                                     minlength=D.n_c)
                         + np.bincount(D.c1[mm], weights=wa[mm] * D.w1[mm],
                                       minlength=D.n_c)) / tot
        vecs.append(g if ok else None)
    return vecs


def curveWindowGaps(c_free, vecs):
    """The realised track-minus-XC mean of amp*f per pool (nan where
    unbalanced), for the log."""
    return np.array([np.nan if g is None else float(g @ c_free)
                     for g in vecs])


class _Operator:
    """(Z'WZ + P) as a matvec, with its diagonal, for one outer iteration."""

    def __init__(self, D, w, h, amp, pen_cell, pen_race, ridge, lam,
                 lam_gap=None, gap_target=0.0, pen_dist=0.0,
                 ridge_slope=0.0, link_weight=0.0,
                 alt_prior_mean=ALT_PRIOR_MEAN, alt_prior_pen=ALT_PRIOR_PEN_FIXED,
                 pen_era=0.0, imp_prior_pen=None, ind_prior_pen=None):
        self.D, self.w, self.h, self.amp = D, w, h, amp
        # the importance and indoor terms' priors, in row units: a few
        # dozen pseudo-rows toward the stated mean, nothing against the
        # millions of rows that carry each coefficient (IMP_PRIOR_PEN)
        # (solveJoint passes sigma2 / IMP_PRIOR_SD^2; None = numerical only)
        self.imp_prior_pen = (1e-6 if imp_prior_pen is None
                              else float(imp_prior_pen))
        self.ind_prior_pen = (1e-6 if ind_prior_pen is None
                              else float(ind_prior_pen))
        self.pen_cell, self.pen_race, self.ridge, self.lam = (
            pen_cell, pen_race, ridge, lam)
        # ★ tau IS A PRIOR ON A COURSE, NOT ON AN ERA. Split into eras and
        #   applied whole to each, it would shrink a long-lived venue harder
        #   than a new one purely for having existed longer. Divided by the
        #   course's era count, the total pull toward the sport's level is
        #   what it was before the split.
        if getattr(D, "n_era_pair", 0):
            self.pen_cell = np.asarray(pen_cell, dtype=np.float64) \
                / np.maximum(D.eras_per_base, 1.0)
        self.pen_era = float(pen_era)
        # incident weight per era-cell, for the diagonal
        self._era_deg = None
        if getattr(D, "n_era_pair", 0) and self.pen_era > 0.0:
            a, b_ = D.era_pairs
            self._era_deg = (
                np.bincount(a, weights=D.era_w, minlength=D.n_cell)
                + np.bincount(b_, weights=D.era_w, minlength=D.n_cell))
        # the event offsets' prior: pen_dist per class; where the season-
        # best pairs calibrated a class (Design.calibrateDist) the penalty
        # is DIST_CAL_SHARE of its own row information instead, toward a
        # prior mean that enters the right-hand side
        self.pen_dist = float(pen_dist)
        if D.n_e:
            self.pen_dist = np.full(D.n_e, float(pen_dist))
            cal = getattr(D, "e_cal_n", None)
            if cal is not None and (cal > 0).any():
                rows_w = np.bincount(D.e_idx, weights=w * D.e_w,
                                     minlength=D.n_e)
                share = DIST_CAL_SHARE / (1.0 - DIST_CAL_SHARE)
                self.pen_dist = np.where(cal > 0, rows_w * share, self.pen_dist)
        self.ridge_slope = float(ridge_slope)
        self.link_weight = float(link_weight)
        self.alt_prior_mean = float(alt_prior_mean)
        self.alt_prior_pen = float(alt_prior_pen)
        # the window-balance penalty (CURVE_GAP_WEIGHT): rank one per pool,
        # lg * (g.c - target)^2 with target = -winter_gain
        self.gap = []
        self.gap_target = float(gap_target)
        if lam_gap is not None and D.n_c:
            for lg, g in zip(lam_gap, curveGapVectors(D, w, amp)):
                if g is not None and lg > 0:
                    self.gap.append((float(lg), g))
        self.pool = (ThreadPoolExecutor(_N_THREADS) if _N_THREADS > 1
                     else None)

    def _reduce(self, jobs):
        """Run independent block reductions, in parallel where there is
        a pool. Each job is a zero-argument callable returning one block;
        order is preserved."""
        if self.pool is None:
            return [j() for j in jobs]
        return list(self.pool.map(lambda j: j(), jobs))

    def adjoint(self, wr):
        """Z' applied to a per-row vector, packed as theta."""
        D, h, amp = self.D, self.h, self.amp
        jobs = [lambda: np.bincount(D.athlete, weights=wr, minlength=D.n_ath),
                lambda: np.bincount(D.cell, weights=wr * h, minlength=D.n_cell),
                lambda: np.bincount(D.race, weights=wr * h, minlength=D.n_race),
                lambda: np.bincount(D.mu_idx, weights=wr * h * D.mu_w,
                                    minlength=max(D.n_mu, 1))[:D.n_mu]]
        if D.n_beta:
            jobs.append(lambda: np.bincount(D.athlete, weights=wr * D.sc,
                                            minlength=D.n_ath))
        if D.n_c:
            jobs.append(lambda: np.bincount(D.c0, weights=wr * amp * D.w0,
                                            minlength=D.n_c)
                        + np.bincount(D.c1, weights=wr * amp * D.w1,
                                      minlength=D.n_c))
        if D.n_r:
            jobs.append(lambda: np.bincount(D.pool_row, weights=wr * D.first,
                                            minlength=D.n_pool))
        if D.n_e:
            jobs.append(lambda: np.bincount(D.e_idx, weights=wr * D.e_w,
                                            minlength=D.n_e))
        if D.n_g:
            jobs.append(lambda: np.bincount(D.athlete, weights=wr * D.lz,
                                            minlength=D.n_ath))
        if D.n_k:
            jobs.append(lambda: np.bincount(D.group_row, weights=wr * D.alt,
                                            minlength=D.n_group))
        if D.n_imp:
            jobs.append(lambda: np.bincount(D.imp_idx, weights=wr * D.imp_w,
                                            minlength=D.n_imp))
        if D.n_ind:
            jobs.append(lambda: np.bincount(D.ind_idx, weights=wr * h * D.ind_w,
                                            minlength=D.n_ind))
        return np.concatenate(self._reduce(jobs))

    def matvec(self, theta):
        D = self.D
        b = D.unpack(theta)
        out = self.adjoint(self.w * rowPrediction(b, D, self.h, self.amp))
        out[D.o_d:D.o_u] += self.pen_cell * b["d"]
        # ★★ THE RANDOM WALK BETWEEN ERAS. The penalty is
        #    0.5 * pen_era * sum_pairs w * (d[a] - d[b])^2, so its gradient
        #    pushes adjacent eras of ONE course together and says nothing
        #    about their common level -- that is still tau's job. This is
        #    what lets a well-measured venue move between eras while a thin
        #    one stays one number.
        if self._era_deg is not None:
            a, b_ = D.era_pairs
            c = self.pen_era * D.era_w * (b["d"][a] - b["d"][b_])
            out[D.o_d:D.o_u] += (np.bincount(a, weights=c, minlength=D.n_cell)
                                 - np.bincount(b_, weights=c,
                                               minlength=D.n_cell))
        out[D.o_u:D.o_mu] += self.pen_race * b["u"]
        if D.n_beta:
            out[D.o_beta:D.o_c] += self.ridge * b["beta"]
        if D.n_c:
            c_free = theta[D.o_c:D.o_r]
            out[D.o_c:D.o_r] += _curvePenaltyApply(c_free, D, self.lam)
            for lg, g in self.gap:
                out[D.o_c:D.o_r] += lg * g * float(g @ c_free)
        if D.n_e:
            out[D.o_e:D.o_g] += self.pen_dist * b["e"]
        if D.n_g:
            out[D.o_g:D.o_k] += self.ridge_slope * b["g"]
        if D.n_k:
            out[D.o_k:D.o_imp] += (ALT_RIDGE + self.alt_prior_pen) * b["k"]
        if D.n_imp:
            out[D.o_imp:D.o_ind] += self.imp_prior_pen * b["imp"]
        if D.n_ind:
            out[D.o_ind:D.n_total] += self.ind_prior_pen * b["ind"]
        if getattr(D, "has_link", False) and self.link_weight > 0:
            a = b["a"]
            d = self.link_weight * D.link_w * (a[D.link_k0] - a[D.link_k1])
            out[:D.n_ath] += (np.bincount(D.link_k0, weights=d,
                                          minlength=D.n_ath)
                              - np.bincount(D.link_k1, weights=d,
                                            minlength=D.n_ath))
        return out


    def rhs(self, y):
        out = self.adjoint(self.w * y)
        D = self.D
        if self.gap and self.gap_target:
            for lg, g in self.gap:
                out[D.o_c:D.o_r] += lg * self.gap_target * g
        if D.n_k:
            out[D.o_k:D.o_imp] += self.alt_prior_pen * self.alt_prior_mean
        if D.n_imp:
            out[D.o_imp:D.o_ind] += self.imp_prior_pen * D.imp_prior
        if D.n_ind:
            out[D.o_ind:D.n_total] += self.ind_prior_pen * IND_PRIOR_MEAN
        if D.n_e and getattr(D, "e_mean", None) is not None:
            out[D.o_e:D.o_g] += self.pen_dist * D.e_mean
        return out

    def diag(self):
        D, w, h, amp = self.D, self.w, self.h, self.amp
        jobs = [lambda: np.bincount(D.athlete, weights=w, minlength=D.n_ath),
                lambda: np.bincount(D.cell, weights=w * h * h,
                                    minlength=D.n_cell) + self.pen_cell
                + (0.0 if self._era_deg is None
                   else self.pen_era * self._era_deg),
                lambda: np.bincount(D.race, weights=w * h * h,
                                    minlength=D.n_race)
                + self.pen_race,
                lambda: np.bincount(D.mu_idx, weights=w * h * h * D.mu_w,
                                    minlength=max(D.n_mu, 1))[:D.n_mu]]
        if D.n_beta:
            jobs.append(lambda: np.bincount(D.athlete, weights=w * D.sc * D.sc,
                                            minlength=D.n_ath) + self.ridge)
        if D.n_c:
            jobs.append(lambda: np.bincount(D.c0,
                                            weights=w * amp * amp * D.w0 * D.w0,
                                            minlength=D.n_c)
                        + np.bincount(D.c1, weights=w * amp * amp * D.w1 * D.w1,
                                      minlength=D.n_c)
                        + _curvePenaltyDiag(D, self.lam)
                        + sum((lg * g * g for lg, g in self.gap),
                              np.zeros(D.n_c)))
        if D.n_r:
            jobs.append(lambda: np.bincount(D.pool_row, weights=w * D.first,
                                            minlength=D.n_pool))
        if D.n_e:
            jobs.append(lambda: np.bincount(D.e_idx, weights=w * D.e_w,
                                            minlength=D.n_e) + self.pen_dist)
        if D.n_g:
            jobs.append(lambda: np.bincount(D.athlete, weights=w * D.lz * D.lz,
                                            minlength=D.n_ath)
                        + self.ridge_slope)
        if D.n_k:
            jobs.append(lambda: np.bincount(D.group_row, weights=w * D.alt * D.alt,
                                            minlength=D.n_group)
                        + ALT_RIDGE + self.alt_prior_pen)
        if D.n_imp:
            jobs.append(lambda: np.bincount(D.imp_idx, weights=w * D.imp_w,
                                            minlength=D.n_imp)
                        + self.imp_prior_pen)
        if D.n_ind:
            jobs.append(lambda: np.bincount(D.ind_idx,
                                            weights=w * h * h * D.ind_w,
                                            minlength=D.n_ind)
                        + self.ind_prior_pen)
        out = np.concatenate(self._reduce(jobs))
        if getattr(D, "has_link", False) and self.link_weight > 0:
            out[:D.n_ath] += self.link_weight * (
                np.bincount(D.link_k0, weights=D.link_w, minlength=D.n_ath)
                + np.bincount(D.link_k1, weights=D.link_w, minlength=D.n_ath))
        return out


# ---- the legacy three-block operator, kept for its callers ---------------- #

def applyOperator(theta, athlete, cell, race, w, h,
                  n_ath, n_cell, n_race, pen_cell, pen_race):
    a = theta[:n_ath]
    d = theta[n_ath:n_ath + n_cell]
    u = theta[n_ath + n_cell:]
    row = a[athlete] + d[cell] * h + u[race]
    wr = w * row
    out_a = np.bincount(athlete, weights=wr, minlength=n_ath)
    out_d = np.bincount(cell, weights=wr * h, minlength=n_cell) + pen_cell * d
    out_u = np.bincount(race, weights=wr, minlength=n_race) + pen_race * u
    return np.concatenate([out_a, out_d, out_u])


def _rhs(y, athlete, cell, race, w, h, n_ath, n_cell, n_race):
    wy = w * y
    return np.concatenate([
        np.bincount(athlete, weights=wy, minlength=n_ath),
        np.bincount(cell, weights=wy * h, minlength=n_cell),
        np.bincount(race, weights=wy, minlength=n_race)])


def _operatorDiag(athlete, cell, race, w, h, n_ath, n_cell, n_race,
                  pen_cell, pen_race):
    return np.concatenate([
        np.bincount(athlete, weights=w, minlength=n_ath),
        np.bincount(cell, weights=w * h * h, minlength=n_cell) + pen_cell,
        np.bincount(race, weights=w, minlength=n_race) + pen_race])


# Purpose:   preconditioned conjugate gradient on the normal equations.
# Detail:    Jacobi preconditioner -- the operator's own diagonal, which is
#            available in closed form and costs one bincount per block.
def conjugateGradient(rhs, matvec, diag, tol=CG_TOL, max_iter=CG_MAX_ITER,
                      x0=None):
    x = np.zeros_like(rhs) if x0 is None else x0.copy()
    r = rhs - matvec(x)
    inv_diag = 1.0 / np.maximum(diag, 1e-12)
    z = inv_diag * r
    p = z.copy()
    rz = float(r @ z)
    rhs_norm = max(float(np.linalg.norm(rhs)), 1e-30)

    # ★ XCP_CG_TRACE=<n>: print the relative residual every n iterations.
    #   Free -- the norm below is computed every iteration for the tolerance
    #   test anyway -- and it answers the only question that decides whether
    #   this loop can be made shorter (2026-09-08).
    #
    #   A CG count of 300-400 has two very different causes and they want
    #   opposite fixes. ONE weakly-determined direction (the XC-to-track
    #   level against the sport offset, which CG_TOL_OUTER above blames) is
    #   cheap for CG: an isolated small eigenvalue costs about one extra
    #   iteration, and deflating it works. A CLUSTER of weak directions --
    #   say the beta block, one per athlete under a ridge of 0.5 -- is what
    #   actually produces hundreds, and deflating one direction out of such
    #   a cluster buys NOTHING (measured on a synthetic operator of this
    #   shape: 20 small eigenvalues, plain 448 iterations, deflate-one 449,
    #   deflate-twenty 59).
    #
    #   The trace tells them apart by SHAPE. A smooth geometric decay is a
    #   cluster; a plateau that breaks into a sudden drop is a handful of
    #   isolated directions, and the count of drops is roughly how many to
    #   deflate. Read one outer's trace before writing any deflation code.
    trace = int(os.environ.get("XCP_CG_TRACE", "0"))
    for it in range(max_iter):
        Ap = matvec(p)
        pAp = float(p @ Ap)
        if pAp <= 0:
            break                       # not positive definite; stop clean
        alpha = rz / pAp
        x += alpha * p
        r -= alpha * Ap
        rel = float(np.linalg.norm(r)) / rhs_norm
        if trace and (it % trace == 0):
            print(f"    [cg] {it + 1:4d}  rel {rel:.3e}", flush=True)
        if rel < tol:
            return x, it + 1
        z = inv_diag * r
        rz_new = float(r @ z)
        p = z + (rz_new / rz) * p
        rz = rz_new
    return x, max_iter


# ------------------------------------------------------------------ #
# ROBUST WEIGHTS -- replaces rowguard
# ------------------------------------------------------------------ #

# ! NOTHING IS EVER DROPPED. A row that would have been condemned gets a small
#   weight and stays in the design, so it can still anchor its athlete and can
#   recover on the next iteration if the fit moves.
# ★★ THE BACK OF THE FIELD IS NOISE, AND WE HAVE BEEN COUNTING IT AS
#    EVIDENCE (owner, 2026-09-10, liking Slaney's top-25%-of-finishers
#    filter). The observation is right: a runner jogging the JV race is not
#    producing a measurement of the course, and residual variance is
#    strongly heteroscedastic in ability.
#
#  ⚠⚠ AND THE OBVIOUS OBJECTION TO SLANEY'S CUT DID NOT SURVIVE TESTING.
#     The argument was: cutting on finishing position is selection on the
#     OUTCOME, an athlete makes the top quartile more often on days they ran
#     WELL, so kept rows carry negative residuals and the course reads easy.
#     Planted worlds say otherwise -- see tests/test_ability_weighting.py.
#     Recovered difficulty was IDENTICAL to four decimals with and without a
#     top-25% cut, even on a fixture built specifically to break it (half
#     the courses hosting elite-only fields, half mixed, noise scaling with
#     ability).
#
#     The reason is worth knowing: ABILITY IS A FREE PARAMETER. If an
#     athlete is kept only when they run well, their ability is estimated
#     faster to match, the residual at the kept rows goes to zero, and the
#     selection lands in the ability rather than in the cell. Relative
#     difficulty is untouched.
#
#     Ability recovery did not move either (rmse 0.0308 full, 0.0307 cut,
#     0.0309 weighted). So on synthetic data NONE of the three matters, and
#     the honest position is that this cannot be settled by simulation --
#     both are rungs on the ladder and the held-out score decides.
#
#     Inverse-variance weighting is kept because it is the textbook response
#     to heteroscedasticity and costs nothing, not because it was shown to
#     beat the cut.
#
#  ! MEASURED, NOT ASSUMED. The residual sd is estimated per rating band
#    from the current residuals each outer, so a corpus where the back of
#    the field is NOT noisier produces flat weights and this does nothing.
#
#  ! AND IT COMPOSES WITH robustWeights, which handles single outliers. This
#    is the systematic half: a whole class of rows being less informative.
ABILITY_BANDS = (85.0, 95.0, 105.0, 115.0, 130.0)
# a band's weight is capped so a thin band cannot dominate the solve
ABILITY_W_FLOOR, ABILITY_W_CEIL = 0.25, 2.0


def abilityWeights(resid, rating, w_prev=None):
    """Inverse-variance weights by rating band, normalised to mean 1.

    resid   current residuals, per row
    rating  the athlete-season's rating, per row (refreshed each outer)
    """
    r = np.asarray(rating, dtype=np.float64)
    band = np.searchsorted(np.asarray(ABILITY_BANDS), r, side="right")
    n_band = len(ABILITY_BANDS) + 1
    var = np.full(n_band, np.nan)
    for b in range(n_band):
        m = band == b
        if m.sum() >= 50:
            var[b] = max(float(np.mean(resid[m] ** 2)), 1e-12)
    seen = np.isfinite(var)
    if seen.sum() < 2:                      # nothing to compare
        return np.ones_like(r), np.ones(n_band)
    # ⚠ THE MEDIAN OVER POPULATED BANDS ONLY. Filling empty bands with 1.0
    #   first put a variance of ONE beside real ones near 0.0004, so the
    #   median was 1.0, every real band's ratio was tiny, 1/var blew past
    #   the ceiling and EVERY band clipped to the same weight -- the whole
    #   thing silently did nothing.
    mid = float(np.median(var[seen]))
    var = np.where(seen, var, mid)
    # ! RELATIVE TO THE TYPICAL BAND, not to the best one: the target is a
    #   reweighting, not a global rescale of sigma2.
    var /= max(mid, 1e-12)
    w = np.clip(1.0 / var, ABILITY_W_FLOOR, ABILITY_W_CEIL)[band]
    return w / max(float(w.mean()), 1e-12), var


# ★ SLANEY'S FILTER, VERBATIM, SO IT CAN BE TESTED RATHER THAN ARGUED
#   ABOUT. Keep the fastest `frac` of each race and drop the rest. It is
#   selection on the outcome, which sounds fatal and measurably is not --
#   see abilityWeights above for the planted-world result and why. A rung on
#   the ladder, decided by the held-out score.
#
# ! IMPLEMENTED AS A ZERO WEIGHT, not by rebuilding the design: the athlete
#   and cell indices stay put, so the two rungs differ in exactly one thing.
def topFractionWeights(y, race, frac):
    if not frac or frac >= 1.0:
        return np.ones_like(y)
    order = np.lexsort((y, race))
    r = np.asarray(race)[order]
    start = np.searchsorted(r, r, side="left")
    rank = np.arange(r.size) - start                      # 0-based, in race
    size = np.bincount(r, minlength=int(r.max()) + 1)[r]
    keep_sorted = rank < np.maximum(1, np.ceil(size * frac))
    w = np.zeros_like(y)
    w[order] = keep_sorted.astype(np.float64)
    return w


def robustWeights(resid, scale=None):
    if scale is None:
        mad = float(np.median(np.abs(resid - np.median(resid))))
        scale = max(1.4826 * mad, 1e-9)
    z = resid / scale
    cut = np.where(z >= 0, HUBER_SLOW, HUBER_FAST)
    w = np.ones_like(z)
    big = np.abs(z) > cut
    w[big] = cut[big] / np.abs(z[big])
    return w, scale


# ------------------------------------------------------------------ #
# RATINGS FROM THE MODEL'S OWN ABILITY -- the tilt and the amplitude
# ------------------------------------------------------------------ #

# ★ NO CIRCULARITY. apply_tilt must evaluate h at the PRE-tilt rating because
#   the tilted rating is not known until after the tilt. Here ability is a
#   parameter, so h is exact at every iteration.
def tiltFromAbility(a_row, pool_mean_row):
    with np.errstate(over="ignore", invalid="ignore"):
        rating = 100.0 * pool_mean_row / np.exp(a_row)
    rating = np.clip(np.nan_to_num(rating, nan=100.0), TILT_RATING_LO,
                     TILT_RATING_HI)
    return 1.0 + TILT_K * (rating - 100.0) / 10.0


def amplitudeFromRating(rating):
    """The season-form swing relative to a rating-100 athlete of the pool."""
    return np.clip(1.0 - AMP_TILT_PER_POINT * (rating - 100.0),
                   AMP_FLOOR, AMP_CEIL)


def ratingsFromAbility(a, athlete_pool, n_races, n_pool,
                       min_races=POOL_MEAN_MIN_RACES):
    """Per athlete-season: 100 * pool_mean / exp(a), the pool mean over
    athlete-seasons with enough races. Gauge-free: a constant added to every
    a of a pool cancels, which is why the curve's and mu's null directions
    never reach a rating."""
    ability = np.exp(a - a.mean())           # centred for exp() safety only
    vote = n_races >= min_races
    tot = np.bincount(athlete_pool[vote], weights=ability[vote],
                      minlength=n_pool)
    cnt = np.bincount(athlete_pool[vote], minlength=n_pool)
    mean = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
    pm = mean[athlete_pool]
    fallback = float(np.nanmean(mean)) if np.isfinite(mean).any() else 1.0
    pm = np.where(np.isfinite(pm), pm, fallback)
    return 100.0 * pm / ability


def raceFront(r_row, race, n_race, k=FIELD_TOP_K):
    """Per race, the mean rating of its top k rows (NaN for a race with no
    rows): the FRONT of the race. One sort, no per-race Python."""
    r = np.nan_to_num(np.asarray(r_row, dtype=np.float64), nan=100.0)
    race = np.asarray(race, dtype=np.int64)
    n = r.size
    if n == 0 or n_race == 0:
        return np.full(n_race, np.nan)
    order = np.lexsort((-r, race))            # by race, then rating descending
    rs = race[order]
    starts = np.flatnonzero(np.r_[True, rs[1:] != rs[:-1]])
    lengths = np.diff(np.r_[starts, n])
    pos = np.arange(n) - np.repeat(starts, lengths)
    top = pos < k
    top_sum = np.bincount(rs[top], weights=r[order][top], minlength=n_race)
    top_cnt = np.bincount(rs[top], minlength=n_race)
    return np.where(top_cnt > 0, top_sum / np.maximum(top_cnt, 1), np.nan)


def fieldStrength(r_row, race, n_race, imp_idx, mask, n_imp, k=FIELD_TOP_K,
                  unit=FIELD_UNIT, clip=FIELD_CLIP, centre=None):
    """Per race, the mean rating of its top k rows (its FRONT), centred at
    the median race of its (pool, sport) group and in units of `unit`
    rating points, clipped to `clip`. `centre` fixes the per-group centres
    (a held-out design uses the training ones). Returns (per-row weight,
    per-race strength, per-group centre)."""
    r = np.nan_to_num(np.asarray(r_row, dtype=np.float64), nan=100.0)
    race = np.asarray(race, dtype=np.int64)
    mask = np.asarray(mask, dtype=bool)
    n = r.size
    cen = (np.full(max(n_imp, 1), np.nan) if centre is None
           else np.asarray(centre, dtype=np.float64).copy())
    if n == 0 or n_race == 0:
        return np.zeros(n), np.zeros(n_race), cen
    front = raceFront(r, race, n_race, k)
    race_imp = np.full(n_race, -1, dtype=np.int64)
    race_imp[race[mask]] = np.asarray(imp_idx)[mask]
    strength = np.zeros(n_race)
    for g in range(n_imp):
        m = (race_imp == g) & np.isfinite(front)
        if not m.any():
            continue
        if not np.isfinite(cen[g]):
            cen[g] = float(np.median(front[m]))
        strength[m] = (front[m] - cen[g]) / unit
    strength = np.clip(strength, clip[0], clip[1])
    w = np.where(mask, strength[race], 0.0)
    return w, strength, cen


# ------------------------------------------------------------------ #
# THE SPORT-OFFSET RECENTRE -- the one assumption the curve does not remove
# ------------------------------------------------------------------ #

# ★ A UNIFORM beta AND THE LEVEL ARE THE SAME PARAMETER, EXACTLY. Add b to
#   every athlete's sport offset, subtract it from mu[TF], shift each
#   athlete-season's ability by b * (sbar_g - s_ref), and no row's
#   prediction moves. The indoor bridge cannot see this direction either:
#   it separates the CURVE's winter step from the level, not the mean
#   offset from the level. The ridge only decides how the level is split
#   between the two, in proportion to K against sum(sc^2), and CG resolves
#   that near-null direction last and slowly (measured: the level came out
#   at 40% of the truth with the rest sitting in mean beta).
#
#   So the joint model carries the same stated assumption pair_recenter
#   states: the sum(sc^2)-weighted mean specialisation is zero. What the
#   curve changes is WHAT that assumption is applied to -- a level with the
#   season already removed, i.e. the surface -- not whether it is needed.
#   Applied after every CG pass as a reparameterisation, so the next pass
#   warm-starts on the centred point.
# ★ THE DELTA IS THE ONLY WAY THE MEASURED SPORT GAP GETS IN (2026-09-09).
#   scripts/measure_sport_gap.py interpolates an athlete's TF level across an
#   XC season and back, over 2.4M sandwiches, and reports D -- the error in
#   the XC/TF GAP, in log-rating. Its own closing line: "recenterSport has to
#   TAKE a bbar rather than compute one; the reparameterisation arithmetic is
#   unchanged, you are only replacing a confounded estimate with a measured
#   constant."
#
# ⚠ A DELTA, NOT AN ABSOLUTE, AND THE DIFFERENCE IS NOT COSMETIC. The
#   measurement's product is the ERROR in the gap, and it printed
#   "bbar -0.03924 -> -0.06719" against the value in linkage_check's header
#   -- the OLD pair engine's number, read out of a comment. This solve
#   computes its own bbar from beta on every outer pass and does not read
#   that file, so the two are not the same quantity and pinning the absolute
#   would import an unrelated engine's estimate. Adding D to whatever this
#   pass computed moves the gap by exactly D, which is what was measured,
#   whatever the base turns out to be.
#
# ⚠ AND IT IS ONLY VALID AT THE RIDGE IT WAS MEASURED AT, the same caveat
#   linkage_check.recenterSport carries: bbar is a weighted mean of beta and
#   the ridge decides how much of the level sits in beta rather than mu. A
#   run with a different --ridge needs the measurement repeated.
#
# ! DEFAULTS TO 0.0, so a run that does not pass one behaves exactly as
#   before. The verbose line prints the base and the delta separately, so a
#   log says which run carried it.
def recentreSportOffset(b, D, w=None, delta=0.0):
    """Move the weighted mean of beta into mu (and the abilities). Returns
    (b, bbar); b is modified in place. `delta` is added to the computed
    bbar -- the measured gap error, not a replacement estimate."""
    if b.get("beta") is None or D.sc is None:
        return b, 0.0
    ww = np.ones(D.n) if w is None else w
    wsum = np.bincount(D.athlete, weights=ww * D.sc * D.sc, minlength=D.n_ath)
    ok = wsum > 0
    if not ok.any():
        return b, 0.0
    bbar = float(np.average(b["beta"][ok], weights=wsum[ok])) + float(delta)
    # s per row is sc + sbar_g; the group's mean s and each athlete's sbar
    s_row = D.sc + (np.bincount(D.athlete, weights=D.sc, minlength=D.n_ath)
                    / np.maximum(np.bincount(D.athlete, minlength=D.n_ath), 1)
                    )[D.athlete]
    # (sc is centred per athlete-season, so its per-athlete mean is ~0 and
    #  s_row recovers the raw +-0.5 indicator only when sbar is added back)
    cnt_a = np.maximum(np.bincount(D.athlete, minlength=D.n_ath), 1)
    return _recentreWith(b, D, bbar, s_row, cnt_a)


def _recentreWith(b, D, bbar, s_row, cnt_a):
    s_grp = (np.bincount(D.group_row, weights=s_row, minlength=D.n_group)
             / np.maximum(np.bincount(D.group_row, minlength=D.n_group), 1))
    s_ref = s_grp[0]
    b["beta"] = b["beta"] - bbar
    b["mu"] = b["mu"] + bbar * (s_grp - s_ref)
    sbar_a = np.bincount(D.athlete, weights=s_row, minlength=D.n_ath) / cnt_a
    b["a"] = b["a"] - bbar * (sbar_a - s_ref)
    return b, bbar


# ★ THE LEVEL MUST BE MOVED INTO mu BY HAND AFTER EVERY PASS. A constant
#   added to every d of a sport group and subtracted from that group's mu
#   changes no prediction; only the penalty on d prefers the mean in mu, and
#   that direction's eigenvalue is the penalty weight -- tiny against the
#   data blocks -- so CG resolves it last and, at any finite tolerance,
#   incompletely. Measured on the synthetic year: two thirds of a 3% track
#   level stayed in the cells' group mean, and the hierarchical tau2 then
#   read that offset as spread and never shrank it out. The race-day block
#   has the same null direction against mu (a uniform u over one sport's
#   races) and against the abilities (a uniform u over all races). The
#   penalised optimum has every such mean at exactly zero, so moving them
#   is a step toward the optimum, not away from it.
# ★ THE ONE NUMBER THE DATA CANNOT SEE (owner, 2026-09-09: "how does that
#   not mess with the difference being fitness?").
#
#   Sport is season: cross country is autumn, track is spring, and nobody
#   races both close enough together for fitness to be held constant. So
#   "track courses are easier" and "athletes are fitter in spring" are the
#   same sentence in this data, and no estimator can split them. Two months
#   of trying to measure a sport gap were spent on something unidentified.
#
# ★ BUT IT IS EXACTLY ONE SCALAR, AND THIS IS WHERE IT LIVES. Everything
#   else IS identified: relative difficulties inside autumn (athletes race
#   several grass courses each fall), relative difficulties inside spring,
#   and the curve's shape inside each window. The only free thing is the
#   mean offset between the two sets -- which this function computes as
#   d_mean and stores in mu.
#
# ★ merge=True IS OPTION (b): ASSERT IT IS ZERO. The per-sport mean course
#   difficulty is dropped instead of being kept in mu, so the two sports'
#   average course is equal BY CONSTRUCTION on the shared ruler, and every
#   bit of autumn-to-spring movement has nowhere to go but the form curve --
#   which is to say, into FITNESS. That is the property the owner asked for:
#   the number moving means fitness moved.
#
# ⚠ IT IS AN ASSUMPTION, NOT A MEASUREMENT, AND IT IS ALREADY BEING MADE.
#   Today the same scalar is fixed by XCP_WINTER_GAIN=0.02 pinning the
#   curve at CURVE_GAP_WEIGHT=100, entangled with beta, the ridge and mu --
#   four places, interacting, none of them labelled as the assumption. This
#   is the same choice made once, in the open, where it can be argued with.
#
# ! targetFor ALREADY IGNORES THE SPORT (its own docstring: "the bare key is
#   authoritative"), so hs_m normalises to 5000m in BOTH sports. The shared
#   ruler this rests on is already there; nothing needs re-normalising.
# ★★ FITNESS HAS MEAN ZERO IN A SEASON, AND ITS LEVEL BELONGS IN THE RATING
#    (owner, 2026-09-09: "fitness should have a mean of 0 in season but
#    fitness needs to apply to course difficulty").
#
#    The curve sits beside the ability in the prediction:
#
#        time ~ ability[athlete-season] + amp * f(pool, day) + difficulty
#
#    Inside one season, adding c to the curve and taking c off every ability
#    in it gives IDENTICAL predictions -- a null direction the model cannot
#    resolve. The solver parks the constant wherever its priors push it, and
#    then go-live reads the rating from the ability ALONE and throws the
#    curve away. Whatever the curve happened to hold is deleted.
#
#    Measured tonight: unpinned, the curve held -0.09 to -0.19 by pool. That
#    much, deleted, sport-selectively.
#
# ★ SO GIVE THE LEVEL BACK. Per athlete-season, the mean of its own curve
#   contribution is added to its ability. The curve then explains only what
#   varies WITHIN the season -- shape, which is what it is for and what
#   de-biases a course that only ever hosts November races -- while the level
#   reaches the board.
#
# ! A PUBLISH-TIME RELABEL, NOT A SOLVE-TIME PROJECTION, AND THAT IS
#   DELIBERATE. The fit is finished and untouched: this only changes which
#   number is called the ability. A reparameterisation inside the loop would
#   have to move the shared curve's knots to match, respecting the pinned
#   reference knot and the per-athlete amplitude, and getting that subtly
#   wrong is how the merge attempt corrupted a whole run.
def curveLevelPerAthlete(b, D, amp):
    """Each athlete-season's mean curve contribution, per athlete-season.

    Zeros when there is no curve. Pure: no database, no globals."""
    n = int(D.n_ath)
    if b.get("c") is None or not getattr(D, "has_curve", False):
        return np.zeros(n)
    f_row = D.w0 * b["c"][D.k0] + D.w1 * b["c"][D.k1]
    a_row = amp if np.ndim(amp) else np.full(D.n, float(amp))
    tot = np.bincount(D.athlete, weights=a_row * f_row, minlength=n)
    cnt = np.maximum(np.bincount(D.athlete, minlength=n), 1)
    return tot / cnt


def recentreLevels(b, D, merge=False):
    """Zero the per-group mean of d and of u, moving them into mu (and,
    for the reference group, into every ability). In place.

    merge=True drops those means instead of banking them in mu: the sports
    are held to one level and the difference becomes fitness."""
    g = D.group_of_cell
    ind_cell = (getattr(D, "ind_cell", None)
                if getattr(D, "ind_fixed", None) is not None else None)
    if ind_cell is not None and ind_cell.any():
        # ★ THE INDOOR CELLS DO NOT VOTE ON THE OUTDOOR ZERO, AND THEIR OWN
        #   MEAN IS DISCARDED: the asserted level (ind_fixed) is the whole
        #   of it, the outdoor mean is the sport's zero, and each indoor
        #   cell keeps only its deviation from the level
        outd = ~ind_cell
        d_mean = (np.bincount(g[outd], weights=b["d"][outd], minlength=D.n_group)
                  / np.maximum(np.bincount(g[outd], minlength=D.n_group), 1))
        b["d"] = b["d"] - d_mean[g]
        i_mean = (np.bincount(g[ind_cell], weights=b["d"][ind_cell],
                              minlength=D.n_group)
                  / np.maximum(np.bincount(g[ind_cell], minlength=D.n_group), 1))
        b["d"] = np.where(ind_cell, b["d"] - i_mean[g], b["d"])
    else:
        d_mean = (np.bincount(g, weights=b["d"], minlength=D.n_group)
                  / np.maximum(np.bincount(g, minlength=D.n_group), 1))
        b["d"] = b["d"] - d_mean[g]
    if not merge:
        b["mu"] = b["mu"] + d_mean

    race_group = np.zeros(D.n_race, dtype=np.int64)
    race_group[D.race] = D.group_row
    seen = np.bincount(D.race, minlength=D.n_race) > 0
    u_mean = np.zeros(D.n_group)
    for gg in range(D.n_group):
        m = seen & (race_group == gg)
        if m.any():
            u_mean[gg] = float(b["u"][m].mean())
    b["u"] = np.where(seen, b["u"] - u_mean[race_group], b["u"])
    # ! THE RACE-DAY MEANS TOO, OR THE LEVEL COMES BACK THROUGH THE BACK
    #   DOOR. u is the per-race effect; its per-sport mean is a sport level
    #   by another name, and banking it in mu would rebuild exactly the
    #   quantity merge=True exists to refuse. The first cut of this guarded
    #   only the difficulty half and left mu at [0, 1.5] on a fixture built
    #   to come out [0, 0].
    if not merge:
        b["mu"] = b["mu"] + u_mean

    # mu[0] is pinned at zero: whatever landed there is a global constant
    shift = float(b["mu"][0])
    b["mu"] = b["mu"] - shift
    b["a"] = b["a"] + shift
    return b


def _pack(b, D):

    parts = [b["a"], b["d"], b["u"],
             b["mu"][1:] if D.n_mu else np.zeros(0)]     # empty under mu_fixed
    if D.n_beta:
        parts.append(b["beta"])
    if D.n_c:
        parts.append(b["c"][D.free_grid])
    if D.n_r:
        parts.append(b["r"])
    if D.n_e:
        parts.append(b["e"])
    if D.n_g:
        parts.append(b["g"])
    if D.n_k:
        parts.append(b["k"])
    if getattr(D, "n_imp", 0):
        parts.append(b["imp"])
    if getattr(D, "n_ind", 0):
        parts.append(b["ind"])
    return np.concatenate(parts)


# ------------------------------------------------------------------ #
# POSTERIOR VARIANCE -- the number the gate stack exists to approximate
# ------------------------------------------------------------------ #


# Purpose:   diag(A^-1) for the cell block, by Hutchinson probing.
# ⚠ EACH PROBE IS ONE CG SOLVE, and the error falls as 1/sqrt(n_probe).
#   64 is a usable default for shrinkage weights; use several hundred before
#   PUBLISHING a per-cell standard error.
def cellPosteriorVar(matvec, diag, n_total, n_ath, n_cell, sigma2,
                     n_probe=64, seed=0, tol=CG_TOL_PROBE, verbose=False,
                     nested=None):
    # ★ NO PROBES: THE NESTED BLOCK, ELSE THE INFORMATION-DIAGONAL BOUND.
    #   sigma2 / A_ii is a lower bound on the posterior variance (it ignores
    #   every off-diagonal coupling). `nested`, when the caller supplies it,
    #   is nestedPosteriorVar's cell block: exact for the coupling that
    #   dominates a thin cell (its own races) and still a lower bound for
    #   the rest. n_probe=0 is the fast path for a go-live run that does not
    #   need per-cell standard errors that night; the probes are telemetry.
    if not n_probe or n_probe <= 0:
        if nested is not None:
            if verbose:
                print("  [joint] probes off: cell variance from the nested "
                      "(cell + its races) block, abilities held",
                      flush=True)
            return np.maximum(np.asarray(nested, dtype=np.float64), 1e-12)
        d = diag[n_ath:n_ath + n_cell]
        if verbose:
            print("  [joint] probes off: cell variance from the information "
                  "diagonal (a lower bound)", flush=True)
        return sigma2 / np.maximum(d, 1e-12)
    rng = np.random.default_rng(seed)
    acc = np.zeros(n_cell)
    t0 = time.time()
    for k in range(n_probe):
        z = rng.integers(0, 2, size=n_total).astype(np.float64) * 2.0 - 1.0
        x, iters = conjugateGradient(z, matvec, diag, tol=tol,
                                     max_iter=CG_MAX_ITER_PROBE)
        if verbose:
            # ! SAY SO. Sixteen probes on 59M rows is hours of silence
            #   otherwise, and a silent step reads as a hung one.
            print(f"  [joint] probe {k + 1}/{n_probe}: cg {iters} iters "
                  f"[{time.time() - t0:.0f}s]", flush=True)
        acc += z[n_ath:n_ath + n_cell] * x[n_ath:n_ath + n_cell]
    return sigma2 * np.maximum(acc / n_probe, 1e-12)


# ------------------------------------------------------------------ #
# THE SOLVE
# ------------------------------------------------------------------ #

# Purpose:   fit the joint model by block coordinate descent: one CG solve
#            per outer iteration with the weights, the tilt, the amplitude
#            and the variance components held; then update them.
# Input:     y, athlete, cell, race -- as Design; group -- per CELL
#            shrinkage group; design -- a prebuilt Design (then the index
#            arguments are ignored); athlete_pool -- pool code per
#            athlete-season, which enables ratings from the model's own
#            ability (tilt and amplitude) without a file; pool_mean_row --
#            the legacy per-row pool mean for the tilt.
# Output:    dict of fitted blocks, variance components and diagnostics.
# ★★ ESTIMATE A VARIANCE COMPONENT ONLY WHERE IT IS IDENTIFIED.
#
#   THE BUG (measured 2026-09-10; owner: courses on 0-2 races sitting at
#   +54%, +52%, +49%). In a cell with ONE race, the course effect d and the
#   race-day effect u are THE SAME NUMBER -- nothing in the data separates
#   "this course is slow" from "that day was slow". The model is fine with
#   that: the prior is supposed to split the common effect between them in
#   proportion to tau2 and sigma_u2.
#
#   But both are re-estimated each outer from their own posteriors, and
#   that is a winner-take-all race. Whichever starts larger takes more of
#   the common effect, which makes its next estimate larger still, which
#   takes more. Measured on a synthetic world with one race per cell:
#
#       races/cell   kept    tau      sigma_u
#       1            1.000   0.1386   0.0012      <-- collapsed
#       2            0.981   0.1338   0.0201
#       4            0.957   0.1296   0.0257
#
#   sigma_u goes to zero and the cell keeps ONE HUNDRED PERCENT of a single
#   race's noise. That is "shrinkage is not working", and it is not the
#   prior being weak -- it is the prior being estimated from the cells that
#   cannot inform it.
#
#   THE FIX. A cell's d is identified apart from u only if the cell has at
#   least TWO races; a race's u is identified apart from d only if its cell
#   does. So tau2 and sigma_u2 are estimated on that subset alone and then
#   APPLIED EVERYWHERE. Single-race cells are consumers of the prior, never
#   contributors to it, and the split they get is the one the rest of the
#   corpus supports.
#
# ⚠ WHAT I TRIED FIRST AND WHY IT WAS WRONG, so it is not tried again.
#   Scaling pen_cell by rows-per-race, to turn n/(n+k) into R/(R+k). The
#   arithmetic is right and the effect is not: penalising d while leaving u
#   free does not shrink a course, it LAUNDERS the course's difficulty
#   through the race term. On the same world sigma_u ran from 0.016 to
#   0.133 -- almost exactly the planted delta_sd of 0.15 -- while tau
#   collapsed to 0.005. The difficulty was still there, just wearing a
#   different name.
# ★ HOW SLOW A DAY CAN BE, AS A FLOOR (2026-09-10).
#
#   The identified-priors fix above stops sigma_u COLLAPSING, but it does
#   not decide how much a single race should be trusted -- the data does,
#   and on this corpus it says "quite a lot". A cell seen once keeps
#
#       tau2 / (tau2 + sigma_u2)
#
#   of whatever that one race showed. With the measured tau near 4.5% and
#   sigma_u near 1.5%, that is ~0.90: a course seen once is published at
#   ninety percent of one day's noise.
#
#   sigma_u is also biased DOWN by the estimator (the posterior variance
#   comes off the information diagonal, which is a lower bound). Measured
#   on a synthetic world where the answer is known:
#
#       planted u_sd   recovered
#       0.010          0.0050
#       0.030          0.0237
#       0.060          0.0557
#       0.100          0.0939
#
#   -- fine at the top, half at the bottom, and the bottom is where this
#   corpus sits. On a world with FEW RACES PER ATHLETE it is far worse: a
#   planted 3% race day came back as 0.0041, a SEVENFOLD underestimate,
#   and flooring it at the truth improved athlete ability rmse (0.02130 ->
#   0.02021) as well as pulling thin courses in.
#
# ⚠ THE FLOOR IS NOT FREE, AND THIS IS THE COST. On a world with NO
#   race-day effect at all, forcing one costs ability accuracy:
#   rmse 0.00710 at floor 0, 0.01341 at 0.03. So the floor is a claim that
#   race days DO vary -- true of cross country (mud, heat, wind, a slow
#   field) and the reason it is on by default. Set --sigma-u-floor 0 to
#   drop the claim.
#
# ! SO THE FLOOR IS A STATED BELIEF, NOT A FITTED ONE, and it is the only
#   honest place to put one: "a race day is worth at least this much".
#   Mud, heat, wind, a slow field and a tactical race are all real and all
#   land here. At 0.04 a course seen once keeps ~0.56 instead of ~0.90.
#   Zero restores the pure fitted behaviour.
# ★ WHY 0.03 AND NOT ANOTHER NUMBER. Swept on a mixed world (50 cells with
#   12 races, 50 with one; planted tau 0.05, planted race day 0.03):
#
#       floor   thick kept   thin kept   thin RMSE
#       0.00    1.003        0.873       0.0312
#       0.02    0.989        0.739       0.0291
#       0.03    0.985        0.716       0.0289   <-- best
#       0.04    0.966        0.602       0.0294
#       0.06    0.903        0.390       0.0344
#
#   The floor that minimises error on thin cells is the TRUE race-day sd,
#   which is what theory says it should be, and it costs the thick cells
#   almost nothing (1.003 -> 0.985). Past it thin cells over-shrink and the
#   error climbs again: a floor with an optimum, not a dial that always
#   helps.
#
# ⚠ 0.03 IS A BELIEF ABOUT RACE DAYS, NOT A MEASUREMENT OF THIS CORPUS. The
#   solve prints the FITTED sigma_u beside it every run and says whether the
#   floor is binding, so this is visible rather than assumed. If the fitted
#   value is already above it, this does nothing at all.
# ★★ RAISED TO 0.045 (owner, 2026-09-10: "we should shrink more, make it
#    so"). At 0.03 a course seen once kept tau2/(tau2+sigma_u2) = 0.69 of
#    what that one race showed -- a +54 percent course came out at +37,
#    which is not shrinkage anybody would notice. With tau near 0.045 on
#    this corpus, a floor at 0.045 makes a one-race course keep HALF, which
#    is the honest reading of "one race is one opinion".
#
#  ! THIS IS STILL A STATED BELIEF, and the sweep above says the optimum is
#    the TRUE race-day sd. scripts/difficulty_reliability.py measures that
#    per race-count band; when it has been run, set this from it rather
#    than from the argument above.
#
#  ⚠ AND IT WILL NOT FIX A WELL-EVIDENCED VENUE. Glendoveer, Balboa and
#    Morley have thousands of results across dozens of races, so the prior
#    is nowhere near them at any floor. If those courses are wrong the
#    cause is elsewhere, and scripts/venue_check.py is how to tell.
# ★★ PER SPORT, BECAUSE THE TWO SPORTS ARE NOT THE SAME PROBLEM
#    (2026-09-10). Within a race, delta and u are EXACTLY COLLINEAR -- see
#    rowPrediction -- so which of them takes the common effect is decided
#    by tau2 against sigma_u2 and by nothing else. A shared sigma_u
#    therefore hands both sports the same answer to a question they answer
#    differently:
#
#      XC   difficulty reliability 0.928 -- courses ARE different, and
#           93 per cent of the difference reproduces on independent races
#      TF   difficulty reliability 0.574 -- a flat oval is a flat oval, and
#           most of what separated two of them was the days they hosted
#
#    ⚠ AND A SHARED FLOOR AT 0.045 WAS ACTIVELY WRONG FOR XC. With the
#      measured cap tau[XC] = 0.035, a race-day prior of 0.045 is LARGER
#      than the course prior, so the day won the split on every cross
#      country race and genuine course difficulty leaked into u -- which
#      is exactly what a thin elite venue like Foot Locker cannot afford,
#      and it came back still reading too low.
#
#    XC is left to the data (0.0) because its difficulty is demonstrably
#    real and the tau cap already shrinks its thin cells.
#
# ★★★ AND THEN TF's OWN FLOOR DID THE SAME THING TO TF (2026-09-10, run22).
#     The paragraph above diagnoses a 0.045 floor destroying XC difficulty
#     and, in the same breath, keeps a 0.045 floor on TF. It destroyed TF
#     difficulty. From the shipped run's log:
#
#       TF: race-day sd 0.04500 (fitted 0.02279, floor BINDING),
#           course prior 0.00147 -- a one-race course keeps 0.00
#
#     The floor was TWICE what the data fitted, so the day won every TF
#     split and the course prior collapsed to 0.147% -- against a spread
#     of 1.22% that difficulty_reliability.py MEASURED as real by
#     splitting each venue's races in half on different days, which a
#     race-day effect cannot fake. Every track was published as the
#     average track: share = tau^2/(tau^2 + sigma_u^2) = 0.001.
#
#     Both floors are now 0. The floor was a second, cruder fix for the
#     problem identified_priors already solves -- tau and sigma_u are
#     unidentified only within a ONE-RACE cell, and the priors are now
#     estimated on 2+ race cells alone, where the data separate them. A
#     floor on top of that is not a guard; it is an override of a
#     measurement by a guess, and it was wrong by 8x.
#
#  ! IF A COLLAPSE COMES BACK, the answer is not a floor. checkPriors below
#    shouts when a fitted tau lands far from the measured spread; find out
#    why the cells stopped identifying it.
SIGMA_U_FLOOR = {0: 0.0, 1: 0.0}            # 0 = XC, 1 = TF

# ★★ THE COURSE-DIFFICULTY PRIOR, MEASURED (2026-09-10). Until now tau was
#    whatever the EB update landed on, and --tau-max was an unset env var.
#    scripts/difficulty_reliability.py split each venue's races in two and
#    correlated the halves, which gives the fraction of the observed spread
#    that reproduces -- and therefore the spread that is REAL:
#
#      XC   reliability 0.928   observed sd 3.64%   true sd 3.51%
#      TF   reliability 0.574   observed sd 1.61%   true sd 1.22%
#
#    Cross country's difficulty is real: 93 per cent of it reproduces on
#    independent races, and shrinking it would throw away signal. TRACK'S
#    IS MOSTLY NOT. Only 57 per cent reproduces, and at the thin end far
#    less -- 0.40 on 4-5 races, against 0.90 on 61+. A track is a flat
#    400m oval; most of what separated one oval from another was the days
#    they happened to host.
#
#    So the prior SD is capped at the MEASURED TRUE SPREAD of each sport.
#    A well-evidenced venue still keeps its own value -- the cap is a
#    prior, not a clamp -- while a thin one is pulled to its sport's
#    level, and track's thin ones are pulled much harder because that is
#    what the data says they are worth.
#
#  ! --tau-max still overrides this, and 0 disables the cap.
#  ⚠⚠ AND THE FIRST VERSION OF THIS OVER-SHRANK TRACK, by exactly the
#     mistake this repo has now made twice (owner, 2026-09-10: "I think
#     that tf variance might be too small now"). Setting tau to the
#     MEASURED TRUE SPREAD is not the same as publishing that spread: with
#     a correct prior the posterior means come out at
#
#         published sd  =  tau * sqrt(reliability)
#
#     so tau = 1.22% and r = 0.574 published about 0.92% -- against an
#     observed 1.61% before, a board barely more than half as wide. It is
#     the same sqrt(r) under-dispersion as the season-rating work
#     (season_reliability section 3), walked into again one file later.
#
#     Sized the other way round, so the PUBLISHED spread lands on the true
#     spread rather than below it:
#
#         tau  =  true_sd / sqrt(reliability)
#              =  observed_sd                     (they cancel)
#
#     which is to say: with a correct prior, the cap that reproduces the
#     true spread on the board is the OBSERVED spread. XC 0.0364, TF
#     0.0161.
#
#   ! THIS IS A DISPLAY-FIDELITY CHOICE, NOT A BAYESIAN ONE. tau = true_sd
#     minimises squared error per course and is what you want for a
#     prediction; tau = observed_sd reproduces the spread and is what you
#     want for a BOARD, where systematically flat numbers read as a broken
#     scale. The shrinkage that matters is still there -- it falls on the
#     thin cells, which is where the noise is -- and the ladder's
#     `free-tau` rung measures what either costs.
TAU_MAX_DEFAULT = {0: 0.0364, 1: 0.0161}         # 0 = XC, 1 = TF

# ★★★ COURSES CHANGE, AND UNTIL NOW ONE NUMBER HAD TO COVER EVERY YEAR THEY
#     EXISTED (owner, asked three times: "difficulty per every couple years",
#     "courses do change. For example Mt. SAC started crazily cleaning their
#     course b4 the meet each year, making it faster", "I think yearly (well
#     not yearly yearly but a couple years) course difficulties could be
#     better").
#
#     A cell is now (course, era) with eras ERA_YEARS wide, and consecutive
#     eras of the same course are tied by a RANDOM WALK: the penalty is on
#     the DIFFERENCE between adjacent eras, not on each era's value. That is
#     the same device as Coulom's Whole-History Rating, where a player's
#     strength is a random walk and each rating is informed by its
#     neighbours in time, and as a state-space/Kalman smoother generally.
#
#     What it buys, and why a per-era cell ALONE would not: a venue with
#     thousands of races gets genuinely separate era values, while a thin
#     one collapses back toward one number because the walk prior holds its
#     eras together. Splitting cells without the walk would just shatter
#     every thin course into noise -- which is the failure this avoids.
#
#  ! THE DRIFT IS STATED, NOT FITTED. ERA_DRIFT_SD is how far a course may
#    move between adjacent eras, as log-time. 0.01 says a course drifts
#    about 1% per era on its own; a course with evidence of more will still
#    move more, because the prior is a spring and not a clamp. Fitting it by
#    EM is possible and is deliberately not done yet: one new estimated
#    variance interacting with tau and sigma_u is how the last three
#    regressions happened.
ERA_YEARS_DEFAULT = 0            # 0 = off, one difficulty for all time
ERA_DRIFT_SD = 0.010             # log-time drift allowed per era step

# The true (reproducing) course spread each sport was MEASURED to have, by
# splitting every venue's races in half on different days -- which a
# race-day effect cannot fake. checkPriors reads the solve against these.
MEASURED_TRUE_SD = {0: 0.0351, 1: 0.0122}        # 0 = XC, 1 = TF


def checkPriors(tau2, sigma_u2, group_names=("XC", "TF")):
    """★★ THE COLLAPSE ALARM. Returns a list of complaint strings.

    Within a race, delta and u are EXACTLY COLLINEAR (see rowPrediction),
    so which of them takes the common effect is decided by tau2 against
    sigma_u2 and by nothing else. When sigma_u is forced above what the
    data fit, the day wins every split, the course prior collapses toward
    zero, and every course in that sport is published as the average
    course -- silently, because the solve converges happily and the boards
    are merely flat.

    That is not hypothetical. A 0.045 race-day floor against a fitted
    0.0228 drove tau[TF] to 0.00147, 8x below a measured 0.0122, and it
    shipped. The floor is gone; this is the alarm that would have caught
    it, and it reads the fit against the MEASUREMENT rather than against
    a guess.
    """
    tau2 = np.atleast_1d(np.asarray(tau2, dtype=np.float64))
    sigma_u2 = np.atleast_1d(np.asarray(sigma_u2, dtype=np.float64))
    out = []
    for g in range(min(tau2.size, sigma_u2.size)):
        name = group_names[g] if g < len(group_names) else str(g)
        tau = float(np.sqrt(tau2[g]))
        su = float(np.sqrt(sigma_u2[g]))
        share = tau2[g] / (tau2[g] + sigma_u2[g])
        truth = MEASURED_TRUE_SD.get(g)
        if truth and tau < truth / 3.0:
            out.append(
                f"{name}: course prior COLLAPSED -- tau {tau:.5f} against a "
                f"MEASURED true spread of {truth:.4f} ({truth / max(tau, 1e-9):.1f}x "
                f"larger). Race-day sd is {su:.5f}. A one-race course keeps "
                f"{share:.3f} of what its race showed, so this sport's "
                f"courses are being published as one average course. The "
                f"day is taking the difficulty: check SIGMA_U_FLOOR and "
                f"whether enough cells have 2+ races to identify the split.")
        elif share < 0.05:
            out.append(
                f"{name}: a one-race course keeps only {share:.3f} of what "
                f"its race showed (tau {tau:.5f} vs race-day {su:.5f}) -- "
                f"thin courses in this sport are all the sport's average.")
    return out


def nestedPosteriorVar(D, w, h, pen_cell, pen_race, sigma2, era_deg=None,
                       pen_era=0.0):
    """Conditional posterior variance of every cell's d and every race's u,
    with the (cell, its races) block inverted EXACTLY and everything else
    held at its estimate. Returns (var_d[n_cell], var_u[n_race]).

    ★★ WHY THE INFORMATION DIAGONAL WAS THE WRONG E-STEP (2026-09-11).
       The EM update for tau2 is mean(d^2 + Var(d | y)); for sigma_u2 it is
       mean(u^2 + Var(u | y)). Both variances were taken as sigma2 / A_ii --
       the diagonal of the INFORMATION -- which ignores the one coupling
       that matters here: within a cell, d and every u of its races load on
       the SAME rows with the SAME h, so the block is an arrowhead

           A_dd = P_c + sum_j n_j      A_dj = n_j      A_jj = n_j + P_u

       with n_j = sum over race j's rows of w h^2, P_c = sigma2/tau2 and
       P_u = sigma2/sigma_u2. Its inverse is closed form:

           s        = P_c + sum_j n_j P_u / (n_j + P_u)
           Var(d)   = sigma2 / s
           Var(u_j) = sigma2 [ 1/(n_j + P_u) + (n_j/(n_j + P_u))^2 / s ]

       In a one-race cell with many rows, sigma2/A_dd -> 0 while the truth
       is sigma2/(P_c + P_u) = tau2 sigma_u2 / (tau2 + sigma_u2): the split
       between the course and the day is NEVER resolved by more rows of
       the same race, and the diagonal said it was. That is the mechanism
       behind the sevenfold under-recovery of sigma_u noted above (a
       planted 3% day came back 0.0041 on a few-races world), and behind
       every "thin course keeps too much of one race" complaint: an
       under-estimated sigma_u hands the course the day's noise.

       This is the standard nested-design result (Searle, Casella &
       McCulloch, Variance Components, ch. 3; Gelman & Hill ch. 12-13): the
       conditional variance of a group effect given its subgroups is the
       Schur complement of the arrowhead, not its diagonal entry. It costs
       two bincounts per outer.

    ! CONDITIONED ON THE ABILITIES. The athletes' block still couples the
      cells to each other, and the probes (cellPosteriorVar) remain the
      unbiased estimate of the full diag(A^-1). This is the cheap step that
      runs every outer; the probes are the expensive one at the end."""
    w = np.asarray(w, dtype=np.float64)
    hh = np.asarray(h, dtype=np.float64)
    wh2 = w * hh * hh if hh.shape else w * float(hh) ** 2
    info_race = np.bincount(D.race, weights=wh2, minlength=D.n_race)
    pr = np.broadcast_to(np.asarray(pen_race, dtype=np.float64),
                         (D.n_race,))
    tot = np.maximum(info_race + pr, 1e-12)
    q = info_race / tot                          # n_j / (n_j + P_u)
    cell_of = cellOfRace(D)
    pc = np.array(np.broadcast_to(np.asarray(pen_cell, dtype=np.float64),
                                  (D.n_cell,)), dtype=np.float64)
    if era_deg is not None and pen_era > 0.0:
        pc = pc + float(pen_era) * np.asarray(era_deg, dtype=np.float64)
    s = pc + np.bincount(cell_of, weights=q * pr, minlength=D.n_cell)
    s = np.maximum(s, 1e-12)
    var_d = sigma2 / s
    var_u = sigma2 * (1.0 / tot + q * q / s[cell_of])
    return var_d, var_u


def racesPerCell(D):
    """How many distinct races back each cell."""
    pair = D.cell * np.int64(D.n_race) + D.race
    first = np.unique(pair, return_index=True)[1]
    return np.bincount(D.cell[first], minlength=D.n_cell)


def groupOfRace(D):
    """The sport group each race belongs to. Races nest inside cells and
    cells carry a group, so a race inherits its cell's."""
    out = np.zeros(D.n_race, dtype=np.int64)
    out[D.race] = D.group_of_cell[D.cell]
    return out


def cellOfRace(D):
    """The cell each race belongs to (races nest inside cells)."""
    out = np.zeros(D.n_race, dtype=np.int64)
    out[D.race] = D.cell
    return out


def solveJoint(y, athlete=None, cell=None, race=None, group=None,
               pool_mean_row=None, n_outer=6, robust=True, tilt=True,
               n_probe=64, seed=0, verbose=False,
               design=None, athlete_pool=None, ridge=SPORT_RIDGE,
               curve_smooth=CURVE_SMOOTH, cg_max_iter=CG_MAX_ITER,
               curve_gap=CURVE_GAP_WEIGHT, winter_gain=WINTER_GAIN,
               ridge_slope=SLOPE_RIDGE, link_weight=LINK_WEIGHT,
               tau_max="default", alt_prior_pen=ALT_PRIOR_PEN_FIXED,
               era_drift_sd=ERA_DRIFT_SD,
               dist_cal=True, sport_gap_delta=0.0,
               merge_sports=False, centre_curve=False,
               identified_priors=True, sigma_u_floor="default",
               ability_weight=False, top_frac=0.0, nested_var=True):
    y = np.asarray(y, dtype=np.float64)
    D = design if design is not None else Design(athlete, cell, race,
                                                 group_of_cell=group)
    n = y.size
    assert D.n == n, "design and response disagree on the row count"
    # ★ AN ASSERTED SPORT LEVEL IS THE MERGE SEMANTICS WITH A NUMBER. The
    #   level is not in theta; it is taken off y (tilted, so refreshed with
    #   h every pass) and the per-sport means of d and u are DROPPED rather
    #   than banked, exactly as --merge-sports does at zero.
    mu_fixed = getattr(D, "mu_fixed", None)
    if mu_fixed is not None:
        merge_sports = True
        if verbose:
            print("[joint] sport level ASSERTED, not estimated: mu = "
                  f"{np.round(mu_fixed, 5)} (log-time; a negative TF level "
                  "means the track is faster)", flush=True)

    if isinstance(sigma_u_floor, str) and sigma_u_floor == "default":
        sigma_u_floor = dict(SIGMA_U_FLOOR)
    # see TAU_MAX_DEFAULT: the sentinel keeps None meaning "no cap"
    if isinstance(tau_max, str) and tau_max == "default":
        tau_max = dict(TAU_MAX_DEFAULT)
    if verbose and tau_max:
        print("[joint] course-difficulty prior capped at "
              + ", ".join(f"{('XC', 'TF')[g]} {v:.4f}"
                          for g, v in sorted(tau_max.items()))
              + " (measured true spread; difficulty_reliability.py)",
              flush=True)

    n_races = np.bincount(D.athlete, minlength=D.n_ath)
    # ★ WHERE tau2 AND sigma_u2 ARE ALLOWED TO COME FROM. See the note on
    #   racesPerCell: a one-race cell cannot tell d from u, so it must not
    #   vote on how they are split.
    _rpc = racesPerCell(D)
    cell_ok = _rpc >= 2
    race_ok = cell_ok[cellOfRace(D)]
    if not identified_priors or not cell_ok.any() or not race_ok.any():
        if identified_priors and verbose:
            print("[joint] no multi-race cells -- variance components fall "
                  "back to every cell", flush=True)
        cell_ok = np.ones(D.n_cell, dtype=bool)
        race_ok = np.ones(D.n_race, dtype=bool)
    elif verbose:
        print(f"[joint] priors from identified cells only: "
              f"{int(cell_ok.sum()):,} of {D.n_cell:,} cells have 2+ races "
              f"({int(race_ok.sum()):,} of {D.n_race:,} races)", flush=True)
    if athlete_pool is not None:
        athlete_pool = np.asarray(athlete_pool, dtype=np.int64)
        n_pool_r = int(athlete_pool.max()) + 1

    # ★ START WEAK, NOT AT ZERO. tau2 = inf would be a flat prior and a
    #   singular first solve; these are loosened by the updates below.
    tau2 = np.full(D.n_group, 0.05)
    group_of_race = groupOfRace(D)
    sigma_u2 = np.full(D.n_group, 0.01)
    sigma2 = 1.0
    scale = None
    n_cal = 0
    w = np.ones(n)
    # ! BEFORE THE LOOP, because it never changes: it is a property of the
    #   finish order, not of the current fit.
    if top_frac:
        w = topFractionWeights(y, D.race, float(top_frac))
        if verbose:
            print(f"[joint] top-fraction filter: keeping the fastest "
                  f"{100 * float(top_frac):.0f}% of each race "
                  f"({int((w > 0).sum()):,} of {n:,} rows)", flush=True)
    h = np.ones(n)
    amp = np.ones(n)
    lam = np.zeros(max(D.n_pool, 1))
    if D.has_curve:
        rows_per_pool = np.bincount(D.pool_row, minlength=D.n_pool)
        lam = curve_smooth * rows_per_pool / float(D.n_knot)
        lam = np.maximum(lam, 1.0)
    lam_gap = None
    gap_target = -float(winter_gain or 0.0)
    if D.has_curve and curve_gap and curve_gap > 0:
        lam_gap = float(curve_gap) * rows_per_pool.astype(np.float64)
    theta = None
    rating = None

    for outer in range(n_outer):
        pen_cell = sigma2 / np.maximum(tau2[D.group_of_cell], 1e-12)
        # the walk's stiffness: sigma2 over the drift variance per era step
        pen_era = (sigma2 / max(era_drift_sd, 1e-9) ** 2
                   if getattr(D, "n_era_pair", 0) else 0.0)
        pen_race = sigma2 / np.maximum(sigma_u2[group_of_race], 1e-12)
        pen_dist = sigma2 / DIST_PRIOR_SD ** 2 if D.n_e else 0.0
        op = _Operator(D, w, h, amp, pen_cell, pen_race, ridge, lam,
                       lam_gap, gap_target, pen_dist=pen_dist,
                       ridge_slope=ridge_slope, link_weight=link_weight,
                       alt_prior_pen=alt_prior_pen, pen_era=pen_era,
                       imp_prior_pen=sigma2 / IMP_PRIOR_SD ** 2,
                       ind_prior_pen=sigma2 / IND_PRIOR_SD ** 2)
        diag = op.diag()
        # the asserted level comes off y, tilted like the estimated one
        # the asserted level(s) come off y, tilted like the estimated ones
        y_fit = y - D.fixedOffset(h)
        # the last outer carries the published numbers; see CG_TOL_OUTER
        theta, iters = conjugateGradient(
            op.rhs(y_fit), op.matvec, diag, max_iter=cg_max_iter, x0=theta,
            tol=CG_TOL if outer == n_outer - 1 else CG_TOL_OUTER)
        b = D.unpack(theta)
        bbar = 0.0
        if D.n_beta:
            b, bbar = recentreSportOffset(b, D, w, delta=sport_gap_delta)
        b = recentreLevels(b, D, merge=merge_sports)
        theta = _pack(b, D)

        resid = y_fit - rowPrediction(b, D, h, amp)

        # --- variance components ------------------------------------ #

        # ! NOT PLAIN mean(d^2): a shrunk estimate has less spread than the
        #   truth, and updating tau2 from it alone shrinks harder every
        #   iteration. Add the sampling variance the estimate has lost,
        #   sigma2/A_ii -- a lower bound on the posterior variance, so tau2
        #   is still conservative, but no longer collapsing.
        sigma2 = float(np.average(resid ** 2, weights=w))
        if nested_var:
            # ★ THE ARROWHEAD, NOT THE DIAGONAL. See nestedPosteriorVar:
            #   within a cell d and its u's are collinear, and the
            #   diagonal pretends more rows of one race resolve the split.
            d_var, u_var = nestedPosteriorVar(
                D, w, h, op.pen_cell, pen_race, sigma2,
                era_deg=op._era_deg, pen_era=op.pen_era)
        else:
            d_var = sigma2 / np.maximum(diag[D.o_d:D.o_u], 1e-12)
            u_var = sigma2 / np.maximum(diag[D.o_u:D.o_mu], 1e-12)
        # ! race_ok / cell_ok, NOT every race and cell. See racesPerCell.
        #   And PER GROUP, because the split between a course and a day is
        #   the priors' alone -- see SIGMA_U_FLOOR.
        for g in range(D.n_group):
            m = (group_of_race == g) & race_ok
            if not m.any():
                m = group_of_race == g
            if not m.any():
                continue
            fitted = max(float(np.mean(b["u"][m] ** 2 + u_var[m])), 1e-9)
            floor = (sigma_u_floor.get(g, 0.0)
                     if isinstance(sigma_u_floor, dict)
                     else float(sigma_u_floor or 0.0))
            sigma_u2[g] = max(fitted, floor ** 2) if floor > 0 else fitted
            if verbose and outer == n_outer - 1:
                name = ("XC", "TF")[g] if g < 2 else str(g)
                bound = floor > 0 and sigma_u2[g] > fitted + 1e-12
                share = tau2[g] / (tau2[g] + sigma_u2[g])
                print(f"[joint] {name}: race-day sd "
                      f"{np.sqrt(sigma_u2[g]):.5f} "
                      f"(fitted {np.sqrt(fitted):.5f}"
                      f"{', floor BINDING' if bound else ''}), course prior "
                      f"{np.sqrt(tau2[g]):.5f} -- a one-race course keeps "
                      f"{share:.2f} of what that race showed", flush=True)
        for g in range(D.n_group):
            m = (D.group_of_cell == g) & cell_ok
            if not m.any():                 # a group of one-race cells only
                m = D.group_of_cell == g
            if m.any():
                tau2[g] = max(float(np.mean(b["d"][m] ** 2 + d_var[m])), 1e-9)
                # an owner's cap on a group's cell spread (--tau-tf-max):
                # more shrinkage toward the sport's level than the data ask
                if tau_max and g in tau_max and tau_max[g]:
                    tau2[g] = min(tau2[g], float(tau_max[g]) ** 2)

        # ⚠ THE COLLAPSE ALARM, on the last pass. A sport whose course
        #   prior has fallen to nothing still converges and still writes a
        #   board -- a flat one. See checkPriors.
        if verbose and outer == n_outer - 1:
            for _c in checkPriors(tau2, sigma_u2):
                print(f"[joint] ⚠⚠ {_c}", flush=True)

        # --- robust reweighting (replaces rowguard) ------------------ #
        if robust:
            w, scale = robustWeights(resid)
        # ! ABILITY WEIGHTING ON TOP, and it needs the ratings, so it can
        #   only run once they exist (athlete_pool given, second outer on).
        #   See abilityWeights.
        if ability_weight and rating is not None:
            aw, band_var = abilityWeights(resid, rating[D.athlete])
            w = w * aw
            if verbose and outer == n_outer - 1:
                print("[joint] ability weights by band "
                      f"(var/median): {np.round(band_var, 3)}", flush=True)

        # --- the tilt and the amplitude, at the model's own ability -- #
        if athlete_pool is not None:
            rating = ratingsFromAbility(b["a"], athlete_pool, n_races,
                                        n_pool_r)
            r_row = rating[D.athlete]
            if tilt:
                r_clip = np.clip(r_row, TILT_RATING_LO, TILT_RATING_HI)
                h = 1.0 + TILT_K * (r_clip - 100.0) / 10.0
            if D.has_curve:
                amp = amplitudeFromRating(r_row)
            D.rebandDist(r_row)                    # the event offsets' bands
            if dist_cal:
                n_cal = D.calibrateDist(y, r_row)  # ... and their prior means
            # ★ THE FRONT OF EACH RACE from this pass's ratings, for the
            #   next pass's field-strength weights -- not after the last,
            #   so the published coefficient sits on the weights it was
            #   fitted with (fieldStrength)
            if (getattr(D, "imp_kind", None) == "field" and D.n_imp
                    and outer < n_outer - 1):
                D.imp_w, D.field_strength, D.field_centre = fieldStrength(
                    r_row, D.race, D.n_race, D.imp_idx, D.imp_mask, D.n_imp)
        elif tilt and pool_mean_row is not None:
            h = tiltFromAbility(b["a"][D.athlete], np.asarray(pool_mean_row))

        if verbose:
            extra = ""
            if D.n_group > 1:
                extra += f", level mu {np.round(b['mu'] - b['mu'][0], 4)}"
            if D.n_beta:
                extra += (f", |beta| mean {np.abs(b['beta']).mean():.4f}, "
                          f"recentred by {bbar:+.5f}")
                if sport_gap_delta:
                    extra += (f" (measured gap {sport_gap_delta:+.5f} of it, "
                              f"base {bbar - sport_gap_delta:+.5f})")
            if D.n_c:
                gaps = curveWindowGaps(theta[D.o_c:D.o_r],
                                       curveGapVectors(D, w, amp))
                extra += f", curve TF-XC window gap {np.round(gaps, 4)}"
            if D.n_e:
                extra += (f", track distance offsets |e| max "
                          f"{np.abs(b['e']).max():.4f}, {n_cal} classes "
                          f"calibrated from season-best pairs")
            if D.n_k:
                extra += f", altitude k {np.round(b['k'], 4)} /km"
            if getattr(D, "n_imp", 0):
                extra += (f", meet importance {np.round(b['imp'], 4)} "
                          f"(log-time, negative = the field ran faster)")
            if getattr(D, "n_ind", 0):
                extra += f", indoor {np.round(b['ind'], 4)} per pool"

            print(f"  [joint] outer {outer + 1}/{n_outer}: cg {iters} iters, "
                  f"sigma {np.sqrt(sigma2):.5f}, sigma_u "
                  f"{np.round(np.sqrt(sigma_u2), 5)}, "
                  f"tau {np.round(np.sqrt(tau2), 5)}, "
                  f"mean w {w.mean():.3f}{extra}")

    # --- posterior variance, and the shrinkage it licenses ----------- #
    pen_cell = sigma2 / np.maximum(tau2[D.group_of_cell], 1e-12)
    pen_era = (sigma2 / max(era_drift_sd, 1e-9) ** 2
               if getattr(D, "n_era_pair", 0) else 0.0)
    pen_race = sigma2 / np.maximum(sigma_u2[group_of_race], 1e-12)
    pen_dist = sigma2 / DIST_PRIOR_SD ** 2 if D.n_e else 0.0
    op = _Operator(D, w, h, amp, pen_cell, pen_race, ridge, lam, lam_gap,
                   gap_target, pen_dist=pen_dist,
                   ridge_slope=ridge_slope, link_weight=link_weight,
                   alt_prior_pen=alt_prior_pen, pen_era=pen_era,
                   imp_prior_pen=sigma2 / IMP_PRIOR_SD ** 2,
                   ind_prior_pen=sigma2 / IND_PRIOR_SD ** 2)
    diag_final = op.diag()
    nested_d, nested_u = (nestedPosteriorVar(
        D, w, h, op.pen_cell, pen_race, sigma2,
        era_deg=op._era_deg, pen_era=op.pen_era) if nested_var
        else (None, None))
    cell_var = cellPosteriorVar(op.matvec, diag_final, D.n_total, D.n_ath,
                                D.n_cell, sigma2, n_probe=n_probe, seed=seed,
                                verbose=verbose, nested=nested_d)

    b = D.unpack(theta)
    mu_full = b["mu"] if mu_fixed is None else mu_fixed.copy()
    delta = mu_full[D.group_of_cell] + b["d"]
    # ★ THE INDOOR TERM IS PART OF THE COURSE: fold each cell's mean indoor
    #   term (per row it is the pool's coefficient) into delta, so the
    #   go-live tilts and applies one number and the board displays it.
    ind_cell = None
    ind_coef = None
    if getattr(D, "n_ind", 0) and b.get("ind") is not None:
        ind_coef = b["ind"]
    elif getattr(D, "ind_fixed", None) is not None:
        ind_coef = D.ind_fixed.copy()             # the asserted level
    if ind_coef is not None:
        rows_c = np.maximum(np.bincount(D.cell, minlength=D.n_cell), 1)
        ind_cell = (np.bincount(D.cell, weights=D.ind_w * ind_coef[D.ind_idx],
                                minlength=D.n_cell) / rows_c)
        delta = delta + ind_cell
    out = {
        "ability": b["a"],
        "delta": delta,                                # the full difficulty
        "d": b["d"], "mu": mu_full,
        "mu_fixed": None if mu_fixed is None else mu_fixed.copy(),
        "importance": b.get("imp"),
        "indoor": ind_coef, "indoor_cell": ind_cell,
        "indoor_fixed": getattr(D, "ind_fixed", None) is not None,
        "importance_kind": getattr(D, "imp_kind", None),
        "field_strength": getattr(D, "field_strength", None),
        "field_centre": getattr(D, "field_centre", None),
        "race_effect": b["u"],
        "beta": b["beta"],
        "rust": b["r"],
        "dist_offset": b["e"],
        "dist_cal_mean": D.e_mean.copy() if D.n_e else None,
        "dist_cal_n": D.e_cal_n.copy() if D.n_e else None,
        "dist_cal_via": D.e_cal_via.copy() if D.n_e else None,
        "slope": b["g"],
        "altitude_coef": b["k"],
        "cell_var": cell_var, "cell_se": np.sqrt(cell_var),
        "cell_var_nested": nested_d, "race_var": nested_u,
        "sigma2": sigma2, "sigma_u2": sigma_u2, "tau2": tau2,
        "weights": w, "robust_scale": scale, "h": h, "amp": amp,
        "rating": rating, "n_races": n_races,
        "n_downweighted": int((w < 0.999).sum()),
        "theta": theta,
    }
    if D.has_curve:
        c = b["c"].reshape(D.n_pool, D.n_knot)
        # ★ THE CURVE'S GAUGE IS THE SEASON RATING'S MEANING. The fit pins
        #   one knot (1 October) at zero, so the raw abilities are "ability
        #   at October form". Per-result ratings leave the curve out and so
        #   scatter around ability at the athlete's AVERAGE form over the
        #   year; the two only agree if the curve is anchored to its
        #   row-weighted mean per pool and the abilities are shifted by the
        #   same amount, scaled by each athlete-season's own amplitude --
        #   an exact reparameterisation, since amp is constant within an
        #   athlete-season. Measured on the synthetic year before this: the
        #   per-race median sat 1.1 points above the season rating.
        f_row = D.w0 * b["c"][D.k0] + D.w1 * b["c"][D.k1]
        mean_p = (np.bincount(D.pool_row, weights=w * f_row, minlength=D.n_pool)
                  / np.maximum(np.bincount(D.pool_row, weights=w,
                                           minlength=D.n_pool), 1e-12))
        amp_g = np.ones(D.n_ath)
        amp_g[D.athlete] = amp
        pool_g = np.zeros(D.n_ath, dtype=np.int64)
        pool_g[D.athlete] = D.pool_row
        out["ability_raw"] = b["a"]
        out["ability"] = b["a"] + amp_g * mean_p[pool_g]

        # ★★ PER ATHLETE-SEASON, NOT PER POOL (owner, 2026-09-09: "fitness
        #    should have a mean of 0 in season"). The line above folds back
        #    the curve's mean over the WHOLE POOL -- every athlete, all year,
        #    both sports. An athlete who races only in autumn does not
        #    experience that average, so what is left in the curve for them
        #    is their own season's deviation from it: precisely the
        #    season-correlated part, and precisely what gets deleted when
        #    go-live reads the rating from the ability alone.
        #
        #    Unpinned, that leftover was -0.09 to -0.19 by pool -- 9 to 19%
        #    of a rating, applied by season and therefore by sport.
        #
        # ! IT IS THE SAME REPARAMETERISATION AT A FINER GRAIN, and it is
        #   still exact: amp is constant within an athlete-season, so its own
        #   mean curve contribution is a constant for that season and moving
        #   it changes no prediction. On the comment above's own test -- the
        #   per-race median agreeing with the season rating -- this is
        #   strictly better, because the ability now IS the athlete's average
        #   form over the races they actually ran.
        if centre_curve:
            lvl = curveLevelPerAthlete(b, D, amp)
            out["ability"] = b["a"] + lvl
            out["curve_level"] = lvl
        out["curve"] = c
        out["curve_anchored"] = c - mean_p[:, None]
        out["curve_knot_days"] = np.arange(D.n_knot) * D.knot_days
        out["curve_lambda"] = lam
        out["curve_gap_weight"] = float(curve_gap or 0.0)
        out["winter_gain"] = float(winter_gain or 0.0)
        out["curve_window_gap"] = curveWindowGaps(
            theta[D.o_c:D.o_r], curveGapVectors(D, w, amp))
    return out



# ------------------------------------------------------------------ #
# HELD-OUT SCORING
# ------------------------------------------------------------------ #

def predictHeldOut(out, D_train, D_test, athlete_pool=None, tilt=True):
    """Predictions for rows of D_test from a fit on D_train.

    A test row is covered when its athlete-season and its cell were both
    fitted; a race unseen in training contributes 0 (its prior mean).
    Returns (prediction, covered mask)."""
    b = D_train.unpack(out["theta"])
    a_cnt = np.bincount(D_train.athlete, minlength=D_train.n_ath)
    c_cnt = np.bincount(D_train.cell, minlength=D_train.n_cell)
    r_cnt = np.bincount(D_train.race, minlength=D_train.n_race)
    covered = (a_cnt[D_test.athlete] > 0) & (c_cnt[D_test.cell] > 0)

    h = np.ones(D_test.n)
    amp = np.ones(D_test.n)
    if out.get("rating") is not None:
        r_row = out["rating"][D_test.athlete]
        if tilt:
            r_clip = np.clip(r_row, TILT_RATING_LO, TILT_RATING_HI)
            h = 1.0 + TILT_K * (r_clip - 100.0) / 10.0
        if D_test.has_curve:
            amp = amplitudeFromRating(r_row)
    u = np.where(r_cnt > 0, b["u"], 0.0)
    bb = dict(b)
    bb["u"] = u
    if getattr(D_train, "mu_fixed", None) is not None:
        bb["mu"] = D_train.mu_fixed          # the asserted level, off theta
    # the field-strength weights of the held-out races, from the training
    # ratings of their fields, centred where the training races were
    if (getattr(D_test, "imp_kind", None) == "field"
            and getattr(D_test, "n_imp", 0) and out.get("rating") is not None):
        D_test.imp_w, D_test.field_strength, _ = fieldStrength(
            out["rating"][D_test.athlete], D_test.race, D_test.n_race,
            D_test.imp_idx, D_test.imp_mask, D_test.n_imp,
            centre=out.get("field_centre"))
    pred = rowPrediction(bb, D_test, h, amp)
    ind_fixed = getattr(D_train, "ind_fixed", None)
    if ind_fixed is not None and getattr(D_test, "ind_w", None) is not None:
        pred = pred + h * D_test.ind_w * ind_fixed[D_test.ind_idx]
    return pred, covered
