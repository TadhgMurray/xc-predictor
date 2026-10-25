"""scripts/backtest_baseline.py's pure parts on synthetic races where the
answer is known: a level the rating misses, a slope it ignores, a race-day
spread and a place spread. No database.

    python -m pytest -q tests/test_backtest_baseline.py
"""
import math
import os
import random
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402
import backtest_baseline as B                                # noqa: E402


def _races(n_races=60, level_shift=-0.02, beta=0.5, day_sd=0.03, place_sd=0.025,
           seed=0):
    """Races of 40: the rating time is the truth moved by the level and by
    beta * slope * age, plus a shared day and a runner's own spread."""
    rng = random.Random(seed)
    out = []
    for k in range(n_races):
        day = rng.gauss(0.0, day_sd)
        runners = []
        for j in range(40):
            rating_s = 1000.0 + 5 * j
            slope = rng.choice([0.0, 0.0, 0.01, 0.02])
            age = rng.uniform(10, 30)
            ln_act = (math.log(rating_s) + level_shift - beta * slope * age / 30.0
                      + day + rng.gauss(0.0, place_sd))
            runners.append({"pid": j, "school": f"S{j % 6}", "actual": math.exp(ln_act),
                            "rating_s": rating_s, "sig": place_sd, "level": "hs_m",
                            "age": age, "slope": slope, "model_s": None})
        out.append({"key": str(k), "day": "2026-10-01", "has_cell": True,
                    "runners": runners})
    return out


def test_the_table_recovers_the_level_the_slope_and_both_spreads():
    t = B.fitTable(_races(), train=("2026-09-01", "2026-09-30"))
    assert t["overall"] == pytest.approx(-0.02, abs=0.012)     # the day noise is in it
    assert t["slope_beta"] == pytest.approx(0.5, abs=0.15)
    assert t["day_sd"] == pytest.approx(0.03, abs=0.01)
    assert t["place_k"] == pytest.approx(1.0, abs=0.15)
    assert t["train"] == ["2026-09-01", "2026-09-30"]


def test_a_thin_cell_leans_on_its_level():
    assert B._shrink(0.10, 1, 0.0) == pytest.approx(0.10 / 9)
    assert B._shrink(0.10, 80, 0.0) == pytest.approx(0.10 * 80 / 88)


def test_trend_beats_rating_on_held_out_races_and_its_band_is_honest():
    t = B.fitTable(_races(seed=1))
    test = _races(seed=2)
    r = B.scoreBasis(test, B.picker("rating", t))
    tr = B.scoreBasis(test, B.picker("trend", t))
    assert abs(tr["bias"]) < abs(r["bias"])
    assert tr["mae"] < r["mae"]
    # the rating's band is the place spread alone: it under-covers times
    assert r["cov68"] < 0.6
    assert tr["cov68"] == pytest.approx(0.68, abs=0.07)
    assert tr["place68"] == pytest.approx(0.68, abs=0.07)
    # a uniform move cannot change an order
    assert tr["rho"] == pytest.approx(r["rho"], abs=0.02)


def test_team_points_are_the_rulebooks():
    # six of A then six of B: A scores 1+2+3+4+5, B 7+8+9+10+11 (A's sixth displaces)
    pts = B.teamPoints(["A"] * 6 + ["B"] * 6)
    assert pts == {"A": 15, "B": 45}
    # an incomplete team is lifted out and nobody is displaced by it
    assert B.teamPoints(["C"] * 4 + ["A"] * 5) == {"A": 15}


def test_a_perfect_order_scores_every_team_exactly():
    races = _races(n_races=3, day_sd=0.0, place_sd=0.0, level_shift=0.0, beta=0.0)
    for race in races:
        for r in race["runners"]:
            r["slope"] = 0.0
    s = B.scoreBasis(races, B.picker("rating"))
    assert s["team_err"] == 0.0 and s["winner"] == 1.0 and s["rho"] == pytest.approx(1.0)


def test_the_model_flag_scores_every_basis_on_the_same_runners():
    races = _races(n_races=4)
    for race in races:
        for r in race["runners"][::2]:
            r["model_s"] = r["rating_s"]
        race["blend"] = 1.0
    full = B.scoreBasis(races, B.picker("rating"))
    same = B.scoreBasis(races, B.picker("rating", need_model=True))
    model = B.scoreBasis(races, B.picker("model", need_model=True))
    assert same["runners"] == model["runners"] == full["runners"] // 2


def test_paired_says_which_basis_is_better_by_race():
    a = {str(k): 2.0 for k in range(20)}
    b = {str(k): 2.5 for k in range(20)}
    n, mean, lo, hi, share = B.paired(a, b)
    assert n == 20 and mean == pytest.approx(-0.5) and lo == hi == pytest.approx(-0.5)
    assert share == 1.0
    assert B.paired({"1": 1.0}, {"1": 2.0}) is None            # too few races


def test_team_rho_finds_a_shared_team_day():
    rng = random.Random(3)
    races = []
    for k in range(40):
        runners = []
        team_day = {f"S{t}": rng.gauss(0, 0.02) for t in range(6)}
        for j in range(42):
            school = f"S{j % 6}"
            x = team_day[school] + rng.gauss(0, 0.02)       # rho = 0.5 by construction
            runners.append({"school": school, "actual": 1000.0 * math.exp(x),
                            "rating_s": 1000.0, "level": "hs_m", "age": 20, "slope": 0.0})
        races.append({"has_cell": True, "runners": runners})
    assert B.teamRho(races, {}) == pytest.approx(0.5, abs=0.1)
    for race in races:                                     # independent teammates
        for r in race["runners"]:
            r["actual"] = 1000.0 * math.exp(rng.gauss(0, 0.02))
    assert B.teamRho(races, {}) == pytest.approx(0.0, abs=0.05)
