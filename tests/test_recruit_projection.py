# Project: xc-predictor / tests
# File:    test_recruit_projection.py
# Purpose: the model-projected "room left" column -- the arithmetic that turns
#          a predicted time into a rating, the residual that stops it from
#          just re-sorting the bottom of the range, and the wiring. No model,
#          no database.
#
#   python -m pytest -q tests/test_recruit_projection.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import datetime as dt                                        # noqa: E402

import build_recruit_projection as B                         # noqa: E402
import recruiting as R                                       # noqa: E402


def test_a_rating_is_a_reciprocal_so_the_ratio_is_the_answer():
    """★ speed_rating = 100 * pool_mean / adjusted_time. So a predicted time
    and the model's own baseline for the same athlete give the rating
    multiplier directly, and the pool mean cancels -- nothing has to be
    looked up."""
    rows = [{"person_id": 1, "mean_rating": 100.0, "n_races": 6}]
    # predicted 2% faster than the baseline -> 2% more rating
    preds = [{"baseline": 1000.0, "normalized": 980.0, "sigma_pct": 3.0}]
    got = B.projectRows(rows, preds)
    assert len(got) == 1
    _row, proj, gain, pct, sig = got[0]
    assert abs(proj - 100.0 * (1000.0 / 980.0)) < 1e-9
    assert abs(gain - (proj - 100.0)) < 1e-9
    assert abs(pct - (1000.0 / 980.0 - 1.0)) < 1e-9
    assert sig == 3.0
    # and slower than the baseline is a NEGATIVE gain, not a floor at zero
    slower = B.projectRows(rows, [{"baseline": 1000.0, "normalized": 1020.0}])
    assert slower[0][2] < 0


def test_an_unusable_prediction_is_dropped_not_invented():
    rows = [{"person_id": i, "mean_rating": 100.0, "n_races": 5}
            for i in range(5)]
    preds = [
        {},                                             # no prediction at all
        {"baseline": 0.0, "normalized": 950.0},          # no anchor
        {"baseline": 1000.0, "normalized": 0.0},         # no projection
        {"baseline": 1000.0, "normalized": 950.0, "sigma_pct": 90.0},
        {"baseline": 1000.0, "normalized": 950.0, "sigma_pct": 3.0},
    ]
    got = B.projectRows(rows, preds)
    assert len(got) == 1, [g[0]["person_id"] for g in got]
    # a 90% band is not a projection
    assert B.MAX_SIGMA_PCT < 90.0
    # and a missing rating cannot be multiplied
    assert B.projectRows([{"person_id": 9, "mean_rating": None}],
                         [{"baseline": 1000.0, "normalized": 950.0}]) == []


def test_the_residual_is_against_the_starting_rating_not_the_whole_field():
    """! WHAT IS RANKED IS THE RESIDUAL. Everybody at 80 is projected to gain
    more than everybody at 115, so ranking the raw projection re-sorts the
    bottom of the range -- the trap gain_resid exists to avoid."""
    projected = []
    # two bands: slow runners gaining ~10, fast runners gaining ~2, with one
    # over-achiever in the FAST band who should win the residual sort
    for i in range(40):
        projected.append(({"person_id": i, "mean_rating": 80.0},
                          90.0, 10.0 + (i % 5) - 2, 0.1, 3.0))
    for i in range(40):
        gain = 2.0 + (i % 5) - 2
        projected.append(({"person_id": 100 + i, "mean_rating": 115.0},
                          117.0, gain, 0.02, 3.0))
    projected.append(({"person_id": 999, "mean_rating": 115.0},
                      124.0, 9.0, 0.08, 3.0))
    out = B.addResiduals(projected)
    best = max((o for o in out if o[5] is not None), key=lambda o: o[5])
    assert best[0]["person_id"] == 999, best[0]
    # the raw gain would instead have picked somebody from the 80 band
    raw = max(out, key=lambda o: o[2])
    assert raw[0]["mean_rating"] == 80.0


def test_a_band_without_a_population_gets_no_residual_rather_than_a_fake_one():
    lonely = [({"person_id": 1, "mean_rating": 140.0}, 145.0, 5.0, 0.03, 2.0)]
    out = B.addResiduals(lonely)
    assert out[0][5] is None and out[0][7] is None
    assert B.MIN_BAND_N >= 20


def test_the_horizon_is_a_year_because_that_is_where_the_corpus_stops():
    """⚠ HORIZON_GAP_MAX_WEEKS says 208, but the twin generator truncates to
    the last race at least 44 weeks before the target, and an athlete who
    races continuously always has one at about a year. The calibration run
    over the WHOLE validation split topped out at 52.1 weeks with the 104w+
    band empty, so a year is as far as this has a measured right to speak."""
    assert B.PROJECT_WEEKS == 52
    t = B.targetFor("XC", today=dt.date(2026, 9, 17))
    assert t["mode"] == "manual"            # no meet: the athlete is the question
    assert t["date"] == "2027-09-16"
    assert t["distance"] == 5000.0
    assert "course" not in t and "weather" not in t


def test_the_projection_bands_match_the_historical_gain_bands():
    """The two columns sit next to each other, so they have to be slicing the
    population the same way or "vs their own level" means two things."""
    assert B.PROJ_BAND == R.GAIN_BAND
    assert B.MIN_BAND_N == R.MIN_BAND_N


def test_the_sorts_exist_and_read_from_the_joined_alias():
    """The outer ORDER BY cannot see the join's alias -- only the columns the
    CTE aliased into j."""
    for key in ("proj", "proj_gain", "proj_rating"):
        assert key in R.SORTS, key
        assert R.SORTS[key].startswith("j."), R.SORTS[key]
        assert "x." not in R.SORTS[key]
    assert R.wantsProjection("proj") and R.wantsProjection("proj_gain")
    assert not R.wantsProjection("gain_resid") and not R.wantsProjection("rating")
    # the default proj sort is the residual, not the raw projection
    assert "proj_resid" in R.SORTS["proj"]


def test_the_join_is_pay_per_use_and_survives_a_missing_table():
    src = open(os.path.join(_ROOT, "racecast", "recruiting.py")).read()
    assert "if wantsProjection(f.get(\"sort\")):" in src
    assert '_tableExists(cur, "recruit_projection")' in src
    # a missing table falls back rather than 500-ing
    assert 'f = dict(f, sort="rating")' in src
    # and nothing joins it for a rating sort
    assert "_PROJ_JOIN" in src and "LEFT JOIN recruit_projection" in src


def test_the_page_shows_the_band_and_the_caveat_not_a_bare_number():
    js = open(os.path.join(_ROOT, "racecast", "static",
                           "recruiting-search.js")).read()
    assert "function projCell(r)" in js
    assert "if they keep racing" in js
    assert "proj_sigma_pct" in js and "proj_resid" in js
    # the column only appears when the sort asked for it
    assert "const anyProj = rows.some((r) => r.proj_rating != null);" in js
    assert "anyProj ? projCell(r)" in js
    html = open(os.path.join(_ROOT, "racecast", "templates",
                             "recruiting_search.html")).read()
    assert 'value="proj"' in html and 'value="proj_gain"' in html
    assert "Most room left (model)" in html


def test_the_build_is_a_pipeline_step_that_does_not_need_a_model_to_pass():
    sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    # ! bgstep since 2026-09-22: a leaf (only the recruiting page reads the
    #   table), so it runs beside the later steps and is collected by bgwait
    assert 'bgstep 10f2_projection "$PY" -u racecast/build_recruit_projection.py' in sh
    # it runs after the recruits table it sits beside
    assert sh.index("step 10f_recruits") < sh.index("bgstep 10f2_projection")
    # and is collected before the run's last checks and its summary
    assert sh.index("bgstep 10f2_projection") < sh.index("\nbgwait\n") \
        < sh.index("step 17_checklist")
    src = open(os.path.join(_ROOT, "racecast",
                            "build_recruit_projection.py")).read()
    assert "no model, skipped" in src and "return 0" in src
    # an empty build never swaps: publishing nothing takes the column off
    assert "NOTHING TO WRITE" in src


def test_the_floor_binds_the_number_it_reads():
    """floorSql takes "did the reader set this", not the number, and BOTH its
    branches read %(min_races)s -- leaving it unbound is a failed query."""
    from season_floor import floorSql
    assert "%(min_races)s" in floorSql(True)
    assert "%(min_races)s" in floorSql(False)
    src = open(os.path.join(_ROOT, "racecast",
                            "build_recruit_projection.py")).read()
    assert '"min_races": int(min_races) if explicit else DEFAULT_FLOOR' in src
    assert "floorSql(explicit)" in src


# ---------------------------------------------------------------------------
# EVERY SEASON SINCE 2020 (owner, 2026-09-22: "can you do beyond just 2026.
# Like do since 2020")
# ---------------------------------------------------------------------------

def test_a_past_season_is_projected_as_of_its_end_and_sees_nothing_after():
    """★ The class of 2021 is asked what the model would have said THEN. Its
    history is cut on 1 August 2022 (the academic year the season is stored
    under is over), and the target is a year after that -- the same question
    the current season is asked. Without the cut the model is fed the year
    it is predicting."""
    today = dt.date(2026, 9, 22)
    as_of = B.seasonAsOf(2021, 2026, today)
    assert as_of == dt.date(2022, 8, 1)
    t = B.targetFor("XC", today=as_of, history_before=as_of)
    assert t["history_before"] == "2022-08-01"
    assert t["date"] == (as_of + dt.timedelta(weeks=52)).isoformat()


def test_the_current_season_is_asked_exactly_as_before():
    today = dt.date(2026, 9, 22)
    assert B.seasonAsOf(2026, 2026, today) == today
    # the last complete season before today is capped at today, never later
    assert B.seasonAsOf(2025, 2026, dt.date(2026, 7, 1)) == dt.date(2026, 7, 1)
    t = B.targetFor("XC", today=today)
    assert "history_before" not in t          # no cut beyond the target


def test_the_seasons_run_from_since_to_current():
    assert B.SINCE_YEAR == 2020
    assert B.seasonsFor(2026) == [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    assert B.seasonsFor(2025, since=2023) == [2023, 2024, 2025]
    assert B.seasonsFor(2026, only=2021) == [2021]
    assert B.seasonsFor(None) == []


def test_one_row_per_athlete_per_season():
    """The page joins on (person, sport, pool, year); a key without the year
    would keep one season per athlete and silently drop the rest."""
    assert "PRIMARY KEY (person_id, sport, year)" in B._DDL
    assert "model_id" in B._DDL and "as_of" in B._DDL
    assert "x.year = %(year)s" in R._PROJ_JOIN


def test_the_prediction_cuts_history_at_the_earlier_of_the_two_dates():
    """predict._predictTimes cuts at the target date; history_before may only
    move that cut EARLIER. Read from the source: the function needs torch."""
    src = open(os.path.join(_ROOT, "racecast", "predict.py")).read()
    body = src[src.index("def _predictTimes"):]
    body = body[:body.index("\ndef ")]
    assert 'target.get("history_before")' in body
    assert "_hb < cut" in body
