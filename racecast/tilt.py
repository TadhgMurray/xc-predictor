"""
tilt.py -- display-layer ability tilt for course difficulty and ratings.

THE MEASUREMENT
    A course does not slow every runner by the same factor. Mean residual by
    (cell difficulty x athlete rating) over 51M rows tilts systematically, and
    the tilt tracks difficulty at corr = -0.993 across five difficulty
    quintiles:

        h(rating) = 1 + K * (rating - 100) / 10      K = -0.031

        rating  70  ->  h = 1.093   feels 9% harder than delta says
        rating 100  ->  h = 1.000   delta as measured
        rating 140  ->  h = 0.876   feels 12% easier

    Checked against a venue held out by hand: Steens Mountain, delta 0.45, the
    140+ rating band -- predicted over-credit 7.8 rating points, measured 7.87.

★ WHY THIS IS A DISPLAY LAYER AND NOT AN ENGINE CHANGE.
    delta and the ratings are both stored, so the tilt is arithmetic that can be
    applied at read time. That makes K tunable without a three-hour pipeline
    run, reversible, and -- the real point -- it lets a page show difficulty for
    an average runner AND for an elite side by side, rather than baking one
    answer into 62M rows.

    It also answers the "which top x% should difficulty be fitted on" question
    without picking an x. Fitting on the top 25% evaluates h at the fast end of
    the field; this evaluates it wherever you ask.

★ AND WHY IT BARELY MOVES MOST RATINGS. h is constant within an athlete-season,
    so alpha absorbs it: a runner is rated against their OWN races, and their
    mean course difficulty is near zero after anchoring. Only athletes racing
    systematically hard or easy courses move at all. On synthetic data with the
    tilt genuinely planted, every rating band moved by less than 0.002.

    The exception is exactly the case worth fixing: Steens Mountain (delta 0.45)
    costs an elite ~7.8 points, The Hydrangea Ranch (delta 0.12) ~1.8.

⚠ WHAT THE TILT DOES NOT EXPLAIN. The measured elite over-credit at Hydrangea
  is 6.78 points and the tilt accounts for 1.8 of it. The rest is likely real:
  Ultimook is a destination invitational, athletes travel to it fit and race
  stacked fields. That is a taper effect, collinear with the venue, and no
  design tried this session could separate it.
"""

# Fitted over 51M rows in cells with degree >= 25. See ability_slope.py.
TILT_K = -0.031

# ⚠ RAILS, NOT A CLAMP AT 140. The tilt was fitted over ratings ~70-140 and
#   the line runs on past that: h = 0.69 at a rating of 200, 0.60 at 229,
#   1.50 at -61, so within any rating that exists these bounds never bind
#   (the joint solver extrapolates the same way, TILT_RATING_LO/HI 40/200,
#   and run_joint.reportTiltByBand measures the line per band each run).
#   They only stop a garbage rating from inverting the sign of a course.
H_MIN, H_MAX = 0.60, 1.50


def h(rating, k=TILT_K):
    """
    How much of a course's difficulty this athlete actually feels.

    Arguments: rating -- the athlete's speed rating, 100-centred.
    Output:    a multiplier on delta, clamped to [H_MIN, H_MAX].
    """
    if rating is None:
        return 1.0
    try:
        v = 1.0 + k * (float(rating) - 100.0) / 10.0
    except (TypeError, ValueError):
        return 1.0
    return max(H_MIN, min(H_MAX, v))


def difficultyFor(difficulty, rating, k=TILT_K):
    """
    Course difficulty as THIS athlete experiences it.

    Arguments: difficulty -- the stored value from course_difficulties;
               rating     -- the athlete's speed rating.

    Use for "how hard is this course for a 130 runner". At rating 100 it returns
    the stored value unchanged, which is the definition of the anchor.
    """
    if difficulty is None:
        return None
    return float(difficulty) * h(rating, k)


def ratingFor(rating, difficulty, k=TILT_K):
    """
    ★ THE ONE A RACE PAGE WANTS. The rating this performance is worth once the
      course is charged at the athlete's own level.

      A rating is  100 * pool_mean * (1 + d) / normalized_time,  so replacing d
      with d * h means multiplying by (1 + d*h) / (1 + d). Nothing else is
      needed -- pool_mean and the time are unchanged.

      An elite at a hard course rates LOWER, because the course was not as hard
      for them as delta says. A slow runner at the same course rates higher.

    ⚠ THE INPUT RATING IS USED TO PICK h, AND THE OUTPUT IS A DIFFERENT RATING.
      One pass is the intended use: the shift is under a point for almost
      everyone, so iterating to a fixed point would be false precision. Do not
      feed the output back in.
    """
    if rating is None or difficulty is None:
        return rating
    d = float(difficulty)
    denom = 1.0 + d
    if abs(denom) < 1e-9:
        return rating
    return float(rating) * (1.0 + d * h(rating, k)) / denom


def shift(rating, difficulty, k=TILT_K):
    """How many rating points the tilt moves this performance. Signed."""
    out = ratingFor(rating, difficulty, k)
    return None if out is None or rating is None else out - float(rating)


if __name__ == "__main__":
    print("  the tilt, on the venues that motivated it\n")
    print("  venue                delta   rating   tilted   shift")
    for name, d in (("Steens Mountain", 0.4522),
                    ("The Hydrangea Ranch", 0.1204),
                    ("Mt. San Antonio", 0.0566),
                    ("Woodbridge HS", -0.0295)):
        for r in (90, 100, 120, 140):
            print(f"  {name:<20} {d:+.4f} {r:>7} "
                  f"{ratingFor(r, d):>8.1f} {shift(r, d):>+7.2f}")
        print()