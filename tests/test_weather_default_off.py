"""The weather adjustment is off by default, and the reason is a number.

    python -m pytest -q tests/test_weather_default_off.py

⚠ MEASURED, NOT SUSPECTED (2026-09-17, scripts/diag_model_quality.py on a
  152-athlete championship, same model, same field, same race):

      weather       overall bias    median error at a normal gap
      normal              -5.0%                 5.0%
      none                +0.4%                 1.9%

  And section E showed why it is not weather modelling: the normal moved
  every prediction -5.44% with p05 -6.25% and p95 -4.28%. A near-constant
  offset for everybody. Real conditions help some athletes more than others;
  one number for the whole field is the signature of a FEATURE DISTRIBUTION
  the model never trained on, not of a day being harder.

★ THE MECHANISM IS IN THE EXTRACTION. _orZero writes a NULL as 0.0, so
  pressure 0 hPa -- physically impossible -- is how "we do not know" is
  spelled, in the same slot where 1013 is a real reading. Training saw a
  great deal of the first; inference feeds the second.

! AND THE ORDERING BARELY MOVED: spearman 0.899 -> 0.900, inversions 13.4%
  -> 13.4%. A uniform shift cannot reorder anybody. So this fixes the TIMES
  and not the RANKING, and it must not be sold as more than that.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_route_defaults_to_no_weather_adjustment():
    app = read("racecast", "app.py")
    hits = re.findall(r'args\.get\("weather"\) or "(\w+)"', app)
    assert hits, "the weather default moved or was renamed"
    assert set(hits) == {"none"}, hits


def test_the_engine_defaults_the_same_way():
    """! BOTH ENDS. predictTeam can be called from cards.py and from a script
    without going through _target at all, so a default that lives only in the
    route is not a default."""
    pred = read("racecast", "predict.py")
    hits = re.findall(r'target\.get\("weather"\) or "(\w+)"', pred)
    assert hits == ["none"], hits


def test_asking_for_weather_still_works():
    """★ WITHDRAWN, NOT REMOVED. ?weather=normal still asks for it, so the
    fix can be checked and reverted without a deploy."""
    pred = read("racecast", "predict.py")
    i = pred.index("def _weatherVariants(")
    fn = pred[i:pred.index("\ndef ", i + 10)]
    for name in ("none", "normal", "forecast", "both", "all"):
        assert f'"{name}"' in fn, name


def test_the_reason_is_written_down_where_it_is_decided():
    """A default nobody can justify gets flipped back by the next person who
    thinks weather ought to help."""
    app = read("racecast", "app.py")
    i = app.index('args.get("weather") or "none"')
    why = app[max(0, i - 1400):i]
    assert "5.44%" in why, why
    assert "+0.4%" in why or "0.4%" in why, why
    # and it says what is NOT withdrawn
    assert "/api/predict/weather" in why, why


def test_the_diagnostic_can_still_ask_for_each_variant():
    """The measurement above has to stay reproducible."""
    diag = read("scripts", "diag_model_quality.py")
    assert '--weather' in diag, diag
    assert 'choices=("none", "normal", "forecast", "both", "all")' in diag


if __name__ == "__main__":
    for fn in [test_the_route_defaults_to_no_weather_adjustment,
               test_the_engine_defaults_the_same_way,
               test_asking_for_weather_still_works,
               test_the_reason_is_written_down_where_it_is_decided,
               test_the_diagnostic_can_still_ask_for_each_variant]:
        fn()
    print("  ok")
