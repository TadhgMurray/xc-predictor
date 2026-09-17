# Project: xc-predictor / tests
# File:    test_predict_sim_wiring.py
# Purpose: the simulation reaches the page, and it reaches it as JSON. No
#          model, no database: predictTeam's pieces are stubbed and only the
#          merge and the serialization are under test.
#
#   python -m pytest -q tests/test_predict_sim_wiring.py
import json
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import predict as P                                          # noqa: E402
import race_sim as R                                         # noqa: E402


def _field(n_per_team=7, teams=("Alpha", "Beta", "Gamma")):
    field, preds, pid = [], [], 0
    for ti, t in enumerate(teams):
        for j in range(n_per_team):
            pid += 1
            field.append({"person_id": pid, "school": t, "entered": n_per_team})
            preds.append({"seconds": 1000.0 + 8.0 * j + 5.0 * ti,
                          "sigma_pct": 3.0})
    return field, preds


def test_the_sigma_is_the_models_own_number_not_a_re_derivation():
    """predict.py puts sigma_pct on the row straight off predictInterval and
    THEN converts lo/hi onto the race clock. That conversion is monotone but
    not exactly multiplicative, so half of log(hi/lo) is the same number with
    the conversion's error added."""
    assert abs(R.sigmaFromPred({"seconds": 1000, "sigma_pct": 4.0}) - 0.04) < 1e-9
    # sigma_pct wins over a band that disagrees
    got = R.sigmaFromPred({"seconds": 1000, "lo": 900, "hi": 1100,
                           "sigma_pct": 2.0})
    assert abs(got - 0.02) < 1e-9
    # and the band still serves when the model published no sigma
    band = R.sigmaFromPred({"seconds": 1000, "lo": 970, "hi": 1030})
    assert band and 0.025 < band < 0.035
    # a degenerate band is floored, not rejected -- no runner is a certainty
    assert R.sigmaFromPred({"seconds": 1000, "sigma_pct": 0.0}) == R.SIGMA_FLOOR
    # nonsense falls through to the band, which here is absent
    assert R.sigmaFromPred({"seconds": 1000, "sigma_pct": -5}) is None


def test_the_simulation_lands_on_the_team_rows_and_summarizes_on_top():
    field, preds = _field()
    teams = [{"team": t} for t in ("Alpha", "Beta", "Gamma")]
    finishers = [dict(f) for f in field]
    summary = P._stampSim(field, preds, teams, finishers, draws=200)
    assert summary["available"] is True
    assert summary["draws"] == 200
    for t in teams:
        s = t["sim"]
        for key in ("p_win", "p_top3", "score_mean", "score_sd",
                    "score_p10", "score_p50", "score_p90", "p_incomplete"):
            assert key in s, key
    # the win probabilities are a distribution over the teams that scored
    total = sum(t["sim"]["p_win"] for t in teams)
    assert abs(total - 1.0) < 1e-9, total
    # Alpha is the fastest team, so it should win most of the time
    assert teams[0]["sim"]["p_win"] > teams[2]["sim"]["p_win"]
    # every runner carries a place band
    assert all("sim" in r for r in finishers)
    assert all(r["sim"]["place_p10"] <= r["sim"]["place_p90"] for r in finishers)


def test_the_head_to_head_matrix_is_nested_and_json_serializable():
    """★ A TUPLE KEY IS NOT JSON. race_sim keys h2h (a, b); a flat "a,b"
    string would also be ambiguous for a school with a comma in its name."""
    field, preds = _field()
    teams = [{"team": t} for t in ("Alpha", "Beta", "Gamma")]
    summary = P._stampSim(field, preds, teams, [dict(f) for f in field],
                          draws=200)
    h2h = summary["h2h"]
    assert set(h2h) == {"Alpha", "Beta", "Gamma"}
    assert "Alpha" not in h2h["Alpha"]
    # P(a beats b) and P(b beats a) are complements where both always score
    assert abs(h2h["Alpha"]["Beta"] + h2h["Beta"]["Alpha"] - 1.0) < 1e-9
    assert h2h["Alpha"]["Gamma"] > 0.5
    # and the whole payload survives jsonify's encoder
    json.dumps({"teams": teams, "sim": summary})


def test_a_failed_simulation_never_costs_the_prediction():
    """The table is the product; the spread is an extra. A page that cannot
    simulate still shows what it already had."""
    out = P._stampSim([], [], [], [], draws=200)
    assert out["available"] is False and out["reason"]
    # a field of unpredictable rows is a reason, not an exception
    bad = P._stampSim([{"person_id": 1, "school": "Alpha"}], [{}], [], [])
    assert bad["available"] is False


def test_rho_zero_is_a_floor_on_the_spread_not_a_measurement():
    """A UNIFORM race-day effect cannot change a place -- scoring is by place
    and a monotone transform of every time leaves the order alone. What moves
    a team score is WITHIN-team correlation, which nobody has measured here,
    so rho=0 gives the narrowest honest spread."""
    field, preds = _field()
    teams0 = [{"team": t} for t in ("Alpha", "Beta", "Gamma")]
    teams9 = [{"team": t} for t in ("Alpha", "Beta", "Gamma")]
    P._stampSim(field, preds, teams0, [dict(f) for f in field],
                draws=400, team_rho=0.0)
    P._stampSim(field, preds, teams9, [dict(f) for f in field],
                draws=400, team_rho=0.9)
    wide = sum(t["sim"]["score_sd"] for t in teams9)
    tight = sum(t["sim"]["score_sd"] for t in teams0)
    assert wide > tight, (wide, tight)


def test_the_api_exposes_sim_and_lineup_without_making_anyone_pay_for_them():
    app_src = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    assert 'sim = (args.get("sim") or "").lower()' in app_src
    assert "sim=sim" in app_src and "draws=draws" in app_src
    assert '@app.route("/api/predict/lineup"' in app_src
    # the draw count is a public query parameter, so it is capped
    assert "min(draws, 20_000)" in app_src
    assert "min(rho, 0.95)" in app_src
    # and "seven" is not respelled in the route
    assert "_squadCaps()" in app_src
    assert "MAX_PER_TEAM_API" not in app_src


def test_the_lineup_route_calls_a_function_that_exists():
    """I have guessed at an interface in this repo four times now."""
    assert hasattr(P, "predictTeamLineup")
    assert hasattr(R, "bestLineup") and hasattr(R, "simulate")
    app_src = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    assert "from predict import predictTeamLineup" in app_src
