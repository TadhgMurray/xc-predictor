"""Two things the prediction was getting wrong about time itself.

    python -m pytest -q tests/test_race_time_and_cutoff.py

1. IT PRINTED A 5K OVER AN 8K. Owner, 2026-09-16: "we need to fix the 8k
   issue as well now how can we do that?"

   XCPredictor.predictInterval returns baselineSeconds * exp(mu), and
   baselineSeconds is the athlete's last race's normalized_time -- which
   normalize_distance defines as raw * (5000/d)**k with course difficulty
   divided out. So the model's output is a flat-5K equivalent end to end,
   and predictions.js printed it through fmtTime with no label, whatever the
   target race was.

   ⚠ AND INVERTING THROUGH THE CURVE WOULD HAVE MADE IT WORSE. The engine
     defines normalized = raw * (5000/d)**k, so the spline turns a 24:16 into
     39:57 at 8000 m. But 24:16 is already a believable 8K, and its 5K
     equivalent (14:45) rates 139.7 -- which is the rating that athlete
     actually carries. The number on the page was ALREADY behaving like a raw
     8K time, which is only possible if the rows behind it are raw 8K times
     recorded at 5000 m: the owner's own guess, "is it bcs most 8ks they run
     are mislableed 5ks?"

   ★ SO THE CONVERSION IS MEASURED, NOT MODELLED. Every corpus row carries
     both time_seconds and normalized_time, so their ratio is what the engine
     ACTUALLY did to that race, whatever its label claims. The median of that
     ratio over the athlete's own races near the target distance is right
     under either world -- see the two-world test below. The fitted curve
     (conversions.normalized_to_time) stays as the fallback for a distance
     they have never raced.

2. IT COULD SEE THE FUTURE. Owner: "when you do a race as it ran, it
   includes all races the athlete has ever run in the prediction. Even past
   the date of the meet we're predicting... This filter should apply to the
   other way too bcs any race after the date we're predicting shouldn't
   count."

   Re-running the 2025 championship fed the model that athlete's 2026
   season -- the championship included -- and the page then printed the
   error against the real result as though it meant something.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import predict                                                  # noqa: E402

PRED = io.open(os.path.join(ROOT, "racecast", "predict.py"),
               encoding="utf-8").read()
JS = io.open(os.path.join(ROOT, "racecast", "static", "predictions.js"),
             encoding="utf-8").read()


def body(name, src=PRED):
    i = src.index(f"def {name}(")
    return src[i:src.index("\ndef ", i + 10)]


# ---------------------------------------------------------------- dates --

def test_a_date_is_a_date_whatever_shape_it_arrives_in():
    """! TOLERANT ON PURPOSE. Corpus rows store the date as TEXT and a
    target's may arrive as a real date off a meet row; raising on the second
    shape would take the whole prediction down over a type."""
    import datetime as dt
    assert predict._asDate("2026-10-03") == dt.date(2026, 10, 3)
    assert predict._asDate(dt.date(2025, 1, 2)) == dt.date(2025, 1, 2)
    assert predict._asDate(dt.datetime(2025, 1, 2, 10)) == dt.date(2025, 1, 2)
    assert predict._asDate("2025-11-21 00:00:00") == dt.date(2025, 11, 21)
    # unreadable is None, and the caller treats None as "not after the cut":
    # a row with no usable date is not evidence that it happened later
    assert predict._asDate(None) is None
    assert predict._asDate("") is None
    assert predict._asDate("garbage") is None


def test_the_history_is_cut_at_the_target_date():
    """⚠ THE DIFFERENCE BETWEEN A PREDICTION AND A MEMORY. Strictly before,
    so the race being predicted is never its own input."""
    fn = body("_predictTimes")
    src = "\n".join(l.split("#")[0] for l in fn.splitlines())
    assert "_asDate(spec.get(\"date\"))" in src, src
    assert "_before(" in src, src
    # strictly less-than, never <=
    assert "< cut]" in src, src
    # ...and it is NOT gated on the retrospective mode: a target date is a
    # date, and every race after it is information nobody could have had
    cut = src[src.index("def _before"):src.index("entries = [None]")]
    assert "rerun" not in cut, cut


def test_the_actual_comes_off_the_uncut_history():
    """! The result on the day is exactly what the cut removed, and it is
    what the comparison is looking for."""
    fn = body("_predictTimes")
    i = fn.index('if target.get("mode") in ("rerun", "rerun_exact")')
    block = fn[i:i + 900]
    assert "for r in full" in block, block
    # and it is the RAW time they ran, not its normalized form: the
    # prediction beside it is a race time now
    assert "time_seconds" in block, block


# ------------------------------------------------------------ 5K vs race --

class Ctx(dict):
    pass


def test_the_conversion_is_measured_not_modelled():
    """⚠ THE TEST THAT DECIDES THE WHOLE DESIGN. The same prediction has to
    come out at 24:16 whether the corpus labelled that 8K correctly or
    called it a 5K -- because the ratio is read off the athlete's own rows,
    where both numbers came from the same race.
    """
    k, f8 = 1.06, (5000 / 8000) ** 1.06

    def rows(d, raw, norm, n=3):
        return [{"distance_meters": d, "time_seconds": raw,
                 "normalized_time": norm} for _ in range(n)]

    # WORLD 1 -- labels correct: an 8K stored at 8000, normalized down to 5K.
    # The model then predicts a 5K equivalent, and the ratio has to undo the
    # curve exactly.
    ratio = predict._distanceRatio(rows(8000, 1456.0, 1456.0 * f8), 8000)
    assert abs(ratio - 1 / f8) < 0.005, ratio
    assert abs(885.0 * ratio - 1456.5) < 2.0, 885.0 * ratio

    # WORLD 2 -- labels wrong: an 8K stored at 5000, so nothing was ever
    # normalized. The model predicts a raw 8K, and the ratio must no-op.
    ratio = predict._distanceRatio(rows(5000, 1456.0, 1456.0), 5000)
    assert abs(ratio - 1.0) < 0.005, ratio
    assert abs(1456.0 * ratio - 1456.0) < 1.0, 1456.0 * ratio


def test_the_measured_ratio_refuses_thin_or_junk_evidence():
    def rows(d, raw, norm, n=3):
        return [{"distance_meters": d, "time_seconds": raw,
                 "normalized_time": norm} for _ in range(n)]

    # ! ONE ROW IS AN ANECDOTE. Two agreeing rows are a measurement.
    assert predict._distanceRatio(rows(8000, 1456, 885, 1), 8000) is None
    # nothing near the target distance: nothing to measure
    assert predict._distanceRatio(rows(5000, 900, 900), 8000) is None
    # zero/missing times are not evidence
    assert predict._distanceRatio(
        [{"distance_meters": 8000, "time_seconds": 0,
          "normalized_time": 885}] * 4, 8000) is None
    assert predict._distanceRatio([], 8000) is None
    assert predict._distanceRatio(rows(8000, 1456, 885), None) is None

    # five miles IS eight thousand metres -- 8047 is 0.6% away
    assert abs(predict._distanceRatio(rows(8047, 1456.0, 885.0), 8000)
               - 1456.0 / 885.0) < 0.01

    # ! MEDIAN, NOT MEAN. A scraped 1-second time -- the same class of row
    #   that took the first full-corpus training run to NaN -- would drag a
    #   mean anywhere.
    got = predict._distanceRatio(
        rows(8000, 1456.0, 885.0, 2)
        + [{"distance_meters": 8000, "time_seconds": 1.0,
            "normalized_time": 885.0}], 8000)
    assert abs(got - 1456.0 / 885.0) < 0.01, got


def test_the_curve_is_the_fallback_not_the_first_answer():
    """! AND IT IS STILL THE SITE'S OWN INVERSE. Two pages that each invert
    the normalization their own way disagree about what a 24:16 is."""
    fn = body("_raceSeconds")
    assert "conversions" in fn, fn
    assert "normalized_to_time" in fn, fn
    # the order: measured first, curve second, nothing third
    fn = body("_targetClock")
    assert fn.index("_distanceRatio") < fn.index("_denormContext"), fn
    assert '"basis": "own"' in fn, fn
    assert '"basis": "curve"' in fn, fn


def test_a_failed_conversion_keeps_the_5k_and_says_so():
    """! None IS AN ANSWER. No pool, no fitted factor, an unknown distance --
    the row keeps the normalized time rather than inventing a conversion."""
    assert predict._raceSeconds(None, {"distance": 8000}) is None
    assert predict._raceSeconds(900.0, None) is None
    assert predict._onClock(900.0, None) is None
    assert predict._onClock(None, {"ratio": 1.6}) is None
    assert predict._onClock(900.0, {"ratio": 1.6}) == 1440.0
    assert predict._targetClock([], {}) is None
    # a context with no distance is not a context
    assert predict._denormContext({"sport": "XC"}, {"grade": "12",
                                                    "gender": "M"}) is None
    # ...nor is one whose athlete has no resolvable level
    assert predict._denormContext({"distance_meters": 8000},
                                  {"grade": None, "gender": None}) is None


def test_the_context_is_the_target_race_and_the_athletes_pool():
    """! THE POOL IS THE ATHLETE'S, not the race's: the distance curve is per
    pool, because a 13-year-old's 8K is relatively worse than their 5K and
    that is exactly what the curve encodes."""
    ctx = predict._denormContext(
        {"distance_meters": 8000, "sport": "XC", "date": "2026-10-03",
         "course_difficulty": 0.031, "canonical_id": 77,
         "course_name": "Apalachee Regional Park"},
        {"grade": "12", "gender": "M"})
    assert ctx["distance"] == 8000.0, ctx
    assert ctx["pool"] == "hs_m", ctx
    assert ctx["season"] == 2026, ctx
    # conversions.resolve_difficulty reads `difficulty`; the spec calls the
    # same number course_difficulty
    assert ctx["difficulty"] == 0.031, ctx
    assert ctx["sport"] == "XC", ctx

    # ⚠ THE WEATHER IS DELIBERATELY ABSENT. The strict inverse would multiply
    #   the weather factor back in, but the model has ALREADY been given the
    #   target's weather in its context vector -- and the two weather
    #   variants differ by exactly that. Applying it twice makes the forecast
    #   delta wrong.
    assert "weather" not in ctx, ctx


def test_every_number_on_the_row_goes_through_one_conversion():
    """The band has to keep bracketing the prediction, and the forecast has
    to stay on the same clock as the headline."""
    fn = body("_predictTimes")
    i = fn.index("race = _onClock(norm, clock)")
    block = fn[i:]
    # the band
    assert "_onClock(blo, clock)" in block, block
    assert "_onClock(bhi, clock)" in block, block
    # the forecast, and only when the headline itself converted -- otherwise
    # a race time would be diffed against a 5K equivalent
    assert "if race is not None else None" in block, block
    # and the normalized value survives, because it is what makes different
    # courses comparable and what a rating is derived from
    assert '"normalized": round(norm, 1)' in block, block
    assert '"is_race_time": race is not None' in block, block


def test_the_page_says_which_clock_it_is_showing():
    i = JS.index("function timeBasisNote(")
    fn = JS[i:JS.index("\n}", i)]
    assert "is_race_time === false" in fn, fn
    assert "flat-5K equivalent" in fn, fn
    # and nothing is said when every row converted, which is the normal case
    assert "if (!equiv) return \"\";" in fn, fn


if __name__ == "__main__":
    for fn in [test_a_date_is_a_date_whatever_shape_it_arrives_in,
               test_the_history_is_cut_at_the_target_date,
               test_the_actual_comes_off_the_uncut_history,
               test_the_conversion_is_measured_not_modelled,
               test_the_measured_ratio_refuses_thin_or_junk_evidence,
               test_the_curve_is_the_fallback_not_the_first_answer,
               test_a_failed_conversion_keeps_the_5k_and_says_so,
               test_the_context_is_the_target_race_and_the_athletes_pool,
               test_every_number_on_the_row_goes_through_one_conversion,
               test_the_page_says_which_clock_it_is_showing]:
        fn()
    print("  ok")
