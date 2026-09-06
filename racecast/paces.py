# Project: xc-predictor / racecast
# File:    racecast/paces.py
# Purpose: Training paces and an estimated VO2max, derived from an athlete's
#          own races rather than from a rule of thumb.
#
# ★ THE CHAIN, AND WHERE EVERY LINK COMES FROM.
#
#     CS, D'      from TWO of the athlete's races, exactly. d = CS*t + D' is
#                 two equations in two unknowns; there is no constant to pick.
#     interval    CS + 3-4% in speed                     (literature)
#     threshold   CS pace x 1.08                         (one measurement)
#     tempo       predicted 10K pace + 15-20 s/mi        (literature)
#     steady/easy fractions of threshold                 (convention, unbacked)
#
#   Each imported number is one figure from one citable source, applied to a
#   quantity that already scales per athlete. That is the difference between
#   this file and the version it replaces, which set tempo to "mile pace +
#   60-80 s/mi" because a coach said so and then tested that the output was
#   mile pace + 60-80 s/mi. That test could not fail. It was the constant
#   checking itself.
#
# ⚠ CRITICAL SPEED IS NOT THRESHOLD, AND CALLING IT THAT WAS THE ERROR THIS
#   FILE WAS REWRITTEN FOR. CS sits between 5K and 10K pace, is sustainable
#   for roughly 25-40 minutes, and is explicitly FASTER than lactate
#   threshold. Reported as CS with that stated, not relabelled as a training
#   pace it is faster than.
#
#     Running Writings, "The science of critical speed, critical velocity
#       (CV), and critical power training for runners" (2024) -- CS between 5K
#       and 10K pace, 25-40 min, faster than LT; CS+/- 3-4% for intervals;
#       tempo about 10K pace + 15-20 s/mi.
#     Eur J Appl Physiol (2021), doi 10.1007/s00421-021-04780-8 -- CS 16.4
#       km/h against MLSS 15.2 km/h in well-trained runners: CS is ~8% faster
#       in speed, which is the 1.08 on pace below.
#     Sports Medicine scoping review, doi 10.1007/s40279-026-02410-x -- D' of
#       150-450 m for trained runners; 1500-5000m types 150-200 m and up.
#
# ★ TWO INDEPENDENT CHECKS THIS PASSES ON REAL-SHAPED INPUTS, neither of them
#   arranged. Fitting CS from 1600/3200 pairs and then asking the model for a
#   10K puts CS 4-10 s/mile SLOWER than 10K pace -- exactly where the
#   literature says CS sits, with nothing steering it there. And CS x 1.08
#   lands on the coaching rule this file used to hardcode:
#
#       1600  3200     CS   pred 10K   CS x 1.08   coach: mile+60-80
#       4:10  8:58   4:50       4:44        5:13         5:11-5:31
#       4:30  9:30   5:02       4:57        5:26         5:32-5:52
#       5:00 10:45   5:47       5:40        6:15         6:02-6:22
#
#   Two sources that know nothing about each other, agreeing across the
#   ability range. The coach's rule is kept below as a CROSS-CHECK for the
#   one-race case, not as the implementation.
#
# ⚠ THE 1.08 IS MEASURED ON WELL-TRAINED ADULTS and this corpus is high
#   schoolers. It is the best available figure and a measurement of the right
#   quantity, which is more than the alternative had; it is not fitted to
#   this population. scripts/fit_critical_speed.py is where that would come
#   from if it ever does.

import math

MILE_M = 1609.344

# ! MIN_SPREAD, FOR THE SAME REASON fit_distance_exponent NEEDS IT. CS is a
#   slope, (d2-d1)/(t2-t1). Two races at nearly the same distance make both
#   terms small and the ratio meaningless -- and a season of nothing but 5Ks
#   says nothing about an asymptote.
MIN_SPREAD = 1.6

# ⚠ AND D' IS THE MODEL'S OWN HONESTY CHECK. Outside the published band the
#   two performances do not belong to one athlete at one fitness -- a mislinked
#   identity, a mistimed race, a freshman paired with a senior. Refused rather
#   than turned into a confident pace.
DPRIME_MIN, DPRIME_MAX = 80.0, 500.0

_INTERVAL_FASTER = 0.035        # CS + 3-4% in speed          (Running Writings)
_THRESHOLD_OF_CS = 1.08         # CS pace x 1.08 = MLSS pace   (EJAP 2021)
_TEMPO_OVER_10K = (15.0, 20.0)  # 10K pace + 15-20 s/mi        (Running Writings)

# ★ STEADY AND EASY AS RATIOS OF THRESHOLD PACE, FROM DANIELS' OWN ZONES.
#   These were round offsets in seconds per mile with nothing behind them.
#   Daniels prescribes his zones as percentages of VDOT velocity -- E 65-78%,
#   M ~84%, T 86-92%, I 95-100% -- which are SPEED fractions, so expressed
#   against threshold they are pace RATIOS:
#
#       E/T   65/92 to 78/86 in speed  =  1.10-1.41 in pace
#       M/T   84/88 in speed           =  1.05 in pace
#
# ⚠ AND AN OFFSET IN SECONDS CANNOT DO THIS JOB, which is the second time that
#   lesson has cost a rewrite here. Checked against two published Daniels
#   rows, the ratio reproduces them and the offset does not:
#
#       threshold   Daniels easy   ratio 1.15-1.38   offset +85-125s
#         6:51       7:52-9:26       7:53-9:27        8:16-8:56
#         7:33       8:55-9:15       8:41-10:25       8:58-9:38
#
#   The ratio lands within a second on the VDOT 50 row. The offset is too
#   narrow there and runs 38 s/mile slow at the fast end of the corpus, where
#   a fixed number of seconds is a much larger share of the pace.
#
# ! THE BAND IS WIDE BECAUSE DANIELS' IS. E spans 65-78% of VDOT velocity; a
#   slow easy day and a brisk one are both easy days, and narrowing that to
#   look precise would be inventing precision.
_STEADY_OF_THRESHOLD = 1.05              # Daniels M against T
_EASY_OF_THRESHOLD = (1.15, 1.38)        # Daniels E against T

# The coach's rule, retained for the ONE-RACE case only: it needs nothing but
# a mile time, which is exactly the situation where CS cannot be computed.
_COACH_TEMPO_OVER_MILE = (60.0, 80.0)


def _fmt(sec_per_mile):
    s = int(round(sec_per_mile))
    return f"{s // 60}:{s % 60:02d}"


def _pair(lo, hi):
    """A range as m:ss-m:ss, per mile and per km."""
    return (f"{_fmt(lo)}–{_fmt(hi)}",
            f"{_fmt(lo * 1000.0 / MILE_M)}–{_fmt(hi * 1000.0 / MILE_M)}")


def criticalSpeed(races):
    """(CS m/s, D' m) from [(distance_m, time_s), ...], or (None, reason).

    Least squares on d = CS*t + D'. With exactly two races that is the line
    through them; with more it is the best line, which is what an athlete
    with a full season should get.
    """
    races = [(float(d), float(t)) for d, t in races
             if d and t and d > 0 and t > 0]
    if len(races) < 2:
        return None, "needs two races at different distances"
    ds = [d for d, _ in races]
    if max(ds) / min(ds) < MIN_SPREAD:
        return None, (f"the races are within {max(ds) / min(ds):.2f}x in "
                      f"distance; {MIN_SPREAD}x is needed to pin an asymptote")
    n = len(races)
    mt = sum(t for _, t in races) / n
    md = sum(ds) / n
    sxx = sum((t - mt) ** 2 for _, t in races)
    if sxx <= 0:
        return None, "the races share one time"
    cs = sum((t - mt) * (d - md) for d, t in races) / sxx
    dprime = md - cs * mt
    if cs <= 0:
        return None, "no positive critical speed fits these races"
    if not (DPRIME_MIN <= dprime <= DPRIME_MAX):
        return None, (f"implies a {dprime:.0f}m anaerobic reserve, outside the "
                      f"{DPRIME_MIN:.0f}-{DPRIME_MAX:.0f}m runners show - "
                      f"these two results may not be the same athlete at one "
                      f"fitness")
    return (cs, dprime), None


def _timeFor(cs, dprime, distance_m):
    """d = CS*t + D'  ->  t = (d - D') / CS."""
    t = (distance_m - dprime) / cs
    return t if t > 0 else None


def trainingPaces(races):
    """[{key, label, per_mile, per_km, basis, source}] from an athlete's races.

    On failure returns [] and the reason is available from criticalSpeed().
    """
    got, why = criticalSpeed(races)
    if got is None:
        return []
    cs, dprime = got
    cs_pace = MILE_M / cs
    t10 = _timeFor(cs, dprime, 10000.0)
    p10 = t10 / (10000.0 / MILE_M) if t10 else None

    out = [{
        "key": "interval", "label": "Interval",
        **dict(zip(("per_mile", "per_km"),
                   _pair(cs_pace / (1 + _INTERVAL_FASTER * 1.15),
                         cs_pace / (1 + _INTERVAL_FASTER * 0.85)))),
        "basis": "3–4% faster than critical speed", "source": "literature",
    }, {
        "key": "critical_speed", "label": "Critical speed",
        "per_mile": _fmt(cs_pace),
        "per_km": _fmt(cs_pace * 1000.0 / MILE_M),
        "basis": "near 10K pace - the 25–40 min effort",
        "source": "derived", "dprime_m": round(dprime),
    }]
    # ⚠ ONE ROW, BECAUSE THE TWO SOURCES DESCRIBE ONE ZONE AND DISAGREE.
    #   CS x 1.08 (MLSS) and 10K pace + 15-20 s/mi are both estimates of
    #   threshold, and for a 4:10/8:58 athlete they land at 5:13 and 4:59-5:04
    #   -- about 14 s/mile apart. The first version of this listed them as
    #   separate "Threshold" and "Tempo" rows, which put tempo FASTER than
    #   threshold and implied an ordering neither source supports. The
    #   disagreement is real and belongs on the page as a band, not hidden by
    #   picking a favourite or by stacking them in an invented order.
    thr = cs_pace * _THRESHOLD_OF_CS
    lo, hi = thr, thr
    basis = "critical speed pace × 1.08"
    if p10:
        lo = min(thr, p10 + _TEMPO_OVER_10K[0])
        hi = max(thr, p10 + _TEMPO_OVER_10K[1])
        basis = ("two estimates of one zone: critical speed pace "
                 "× 1.08, and 10K pace + 15–20 s/mi")
    pm, pk = _pair(lo, hi)
    out.append({"key": "threshold", "label": "Threshold / Tempo",
                "per_mile": pm, "per_km": pk, "basis": basis,
                "source": "literature", "spread_s_per_mile": round(hi - lo)})
    thr = hi                      # the slow end anchors steady and easy
    # ! ANCHORED ON CS x 1.08 SPECIFICALLY, not on the slow end of the band
    #   above. That figure is the MLSS estimate, and MLSS is what Daniels' T
    #   zone is; anchoring on the 10K-derived end would mix two definitions.
    anchor = cs_pace * _THRESHOLD_OF_CS
    st_pace = anchor * _STEADY_OF_THRESHOLD
    out.append({"key": "steady", "label": "Steady / Marathon",
                "per_mile": _fmt(st_pace),
                "per_km": _fmt(st_pace * 1000.0 / MILE_M),
                "basis": "threshold pace × 1.05 (Daniels, M against T)",
                "source": "literature"})
    pm, pk = _pair(anchor * _EASY_OF_THRESHOLD[0],
                   anchor * _EASY_OF_THRESHOLD[1])
    out.append({"key": "easy", "label": "Easy", "per_mile": pm, "per_km": pk,
                "basis": "threshold pace × 1.15–1.38 (Daniels, E against T)",
                "source": "literature"})
    return out


def coachRuleTempo(mile_seconds):
    """The one-race fallback, and it is labelled as a rule of thumb.

    ⚠ NOT A SUBSTITUTE FOR CS, AND THE PAGE SHOULD SAY SO. A single race
      cannot separate a miler from a 5K runner: three athletes with the same
      4:10 1600 and 3200s of 8:45, 8:58 and 9:20 have critical speeds 35
      s/mile apart, and this rule hands all three the same number. It is here
      because it needs only a mile time, which is exactly the case where CS is
      unavailable.
    """
    if not mile_seconds or mile_seconds <= 0:
        return None
    lo = mile_seconds + _COACH_TEMPO_OVER_MILE[0]
    hi = mile_seconds + _COACH_TEMPO_OVER_MILE[1]
    pm, pk = _pair(lo, hi)
    return {"key": "tempo", "label": "Tempo (rule of thumb)", "per_mile": pm,
            "per_km": pk, "basis": "mile race pace + 60–80 s/mi",
            "source": "one coach's rule - enter a second race for your "
                      "actual critical speed"}


# ===================================================================== #
#  VO2 MAX
# ===================================================================== #
#
# ⚠ A REGRESSION FROM PERFORMANCE, NOT A MEASUREMENT, and the page must not
#   call it anything else. VO2max is measured on a treadmill with a gas
#   analyser; every calculator reporting it from a race time is reporting how
#   fast you ran, rescaled. Daniels and Gilbert's is the rescaling other
#   calculators use, so the number is at least comparable to what a runner
#   will see elsewhere.
#
# ! COEFFICIENTS TRANSCRIBED AND NOT VERIFIED AGAINST DANIELS BY THIS AUTHOR.
#   Widely republished, and they produce sane values on the self-check -- but a
#   transposed digit here yields plausible numbers rather than an error.
_VO2_A, _VO2_B, _VO2_C = -4.60, 0.182258, 0.000104
_PCT_A, _PCT_B, _PCT_C = 0.8, 0.1894393, 0.2989558
_PCT_D, _PCT_E = -0.012778, -0.1932605

# ⚠ FITTED ON TRAINED ADULTS. Children carry high VO2max per kg with poor
#   running economy and this formula cannot separate the two, so a 12-year
#   old's 3200m yields a number that does not mean what an adult's does.
#   Withheld rather than footnoted.
_VDOT_POOLS = ("hs_m", "hs_f", "college_m", "college_f")


def vdot(time_seconds, distance_meters, pool=None):
    """{value, note} -- estimated VO2max, or a refusal with a reason."""
    if not time_seconds or not distance_meters or time_seconds <= 0:
        return {"value": None, "note": "needs a time and a distance"}
    bare = (pool or "hs_m").replace("_unknown_gender", "_f")
    if bare not in _VDOT_POOLS:
        return {"value": None,
                "note": ("VO2max estimates are fitted on trained adults and "
                         "do not transfer to this age group")}
    minutes = time_seconds / 60.0
    # ! ONLY SENSIBLE OVER RACE DURATIONS. Outside this band the exponentials
    #   are being asked about efforts they were never fitted to.
    if not (3.0 <= minutes <= 240.0):
        return {"value": None,
                "note": "only meaningful for efforts of about 3 to 240 minutes"}
    v = distance_meters / minutes
    vo2 = _VO2_A + _VO2_B * v + _VO2_C * v * v
    pct = (_PCT_A + _PCT_B * math.exp(_PCT_D * minutes)
           + _PCT_C * math.exp(_PCT_E * minutes))
    if pct <= 0:
        return {"value": None, "note": "unresolvable"}
    return {"value": round(vo2 / pct, 1),
            "note": "from race performance (Daniels–Gilbert), "
                    "not a lab measurement"}


# ===================================================================== #
#  THE ONE-RACE LADDER
# ===================================================================== #
#
# ★ WHAT A SINGLE PERFORMANCE CAN AND CANNOT BUY.
#
#   It cannot buy critical speed. CS is the SLOPE of an athlete's
#   distance-time line and one point has no slope: three athletes with the
#   same 4:10 1600 and 3200s of 8:45, 8:58 and 9:20 have critical speeds 35
#   s/mile apart. That row stays absent, and the page says why.
#
#   It CAN buy a 10K pace, and that is not a guess. This project's distance
#   curve -- normalized_time = t * (anchor / d) ** K -- is fitted on 60M
#   results; projecting one race to 10,000m is the same operation the
#   conversion table on this page already performs, and the user is looking
#   at its output in the next table over. Every published relationship below
#   hangs off that 10K pace, so each row names a real source:
#
#       threshold   10K pace + 15-20 s/mi        (Running Writings)
#       interval    3-4% faster in speed         (Running Writings)
#       steady      threshold x 1.05             (Daniels, M against T)
#       easy        threshold x 1.15-1.38        (Daniels, E against T)
#
#   THIS IS NOT THE COACH'S RULE UNDER A NEW NAME. mile + 60-80 s/mi is a
#   CONSTANT: it hands the same answer to a miler and a 5K runner because
#   nothing in it knows the difference. A projected 10K pace is a
#   MEASUREMENT of this performance against 60M others at that distance, so
#   the three athletes above get three different ladders -- as they should.
#
# ⚠ IT IS STILL WEAKER THAN THE FITTED LADDER AND MUST SAY SO. The projection
#   assumes this athlete's distance curve is the CORPUS curve. A true miler
#   and a true 10K runner with the same 1600 do not share one, which is
#   exactly the difference two races would reveal. Every row here is marked
#   `modeled`, against `derived` for a fitted critical speed.

def projectedPaces(mile_seconds, pace_10k_seconds):
    """The ladder from one race, anchored on a projected 10K pace.

    mile_seconds       -- this performance expressed at 1609.34m
    pace_10k_seconds   -- this performance expressed at 10,000m, per mile

    Returns the same row shape trainingPaces does, minus the critical speed
    row, or [] if the projection is unusable.
    """
    if not pace_10k_seconds or pace_10k_seconds <= 0:
        return []
    p10 = float(pace_10k_seconds)

    lo = p10 + _TEMPO_OVER_10K[0]
    hi = p10 + _TEMPO_OVER_10K[1]
    thr = (lo + hi) / 2.0                 # anchors steady and easy
    pm, pk = _pair(lo, hi)

    out = []
    # Interval is set off the 10K pace, not off threshold: the published
    # figure is against CS, and 10K pace is the closer stand-in for CS of the
    # two -- CS sits between 5K and 10K pace.
    ipm, ipk = _pair(p10 / (1 + _INTERVAL_FASTER * 1.15),
                     p10 / (1 + _INTERVAL_FASTER * 0.85))
    out.append({"key": "interval", "label": "Interval",
                "per_mile": ipm, "per_km": ipk,
                "basis": "3–4% faster than projected 10K pace",
                "source": "modeled"})
    out.append({"key": "threshold", "label": "Threshold / Tempo",
                "per_mile": pm, "per_km": pk,
                "basis": "projected 10K pace + 15–20 s/mi",
                "source": "modeled"})
    st = thr * _STEADY_OF_THRESHOLD
    out.append({"key": "steady", "label": "Steady / Marathon",
                "per_mile": _fmt(st), "per_km": _fmt(st * 1000.0 / MILE_M),
                "basis": "threshold pace × 1.05 (Daniels, M against T)",
                "source": "modeled"})
    epm, epk = _pair(thr * _EASY_OF_THRESHOLD[0], thr * _EASY_OF_THRESHOLD[1])
    out.append({"key": "easy", "label": "Easy",
                "per_mile": epm, "per_km": epk,
                "basis": "threshold pace × 1.15–1.38 (Daniels, E against T)",
                "source": "modeled"})
    return out
