# Project: xc-predictor / tests
# File:    test_course_bracket.py
# Purpose: scripts/course_bracket.py measures a venue the owner's way -- each
#          runner against their own races a few weeks either side -- and
#          lines it up with the model's terms for the same day. On the
#          planted era world the bracket tracks the planted course-plus-day
#          and its trend is the planted drift.
#
#   python -m pytest -q tests/test_course_bracket.py
import io
import contextlib
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402
import course_bracket as cb                                    # noqa: E402
from test_era_publish import _era_pack                         # noqa: E402


def test_the_bracket_tracks_the_planted_course_and_its_drift(capsys):
    cols, keep, keys, sport_of_course = _era_pack()
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            cols, keep, sport_offset=False, curve=False, rust=False, dist=False,
            slope=False, link=False, altitude=False, era_years=2,
            importance="none", indoor=True, dist_table=False)
        y = np.log(cols["norm"][keep])
        out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=3,
                            tilt=False, n_probe=0)
    npz = {"delta": out["delta"], "race_effect": out["race_effect"],
           "rating": out["rating"], "course_keys": np.array(D.course_keys)}
    res = cb.bracket(cols, npz, ["XC:100:"], window=60, top=0.3, era_years=2)
    assert list(res) == ["XC:100:d5000"]
    r = res["XC:100:d5000"]
    races = r["races"]
    # 48 race days planted; a couple fall under min_rows in the random draw
    assert 44 <= len(races) <= 48 and all(x["n_bracketed"] > 0 for x in races)
    # the bracket and the model's board + day agree race by race
    b = np.array([x["bracket"] for x in races])
    m = np.array([x["board"] + x["day"] for x in races])
    # both sides are noisy at 15-30 rows a race; the planted spread is ~1.5%
    assert np.corrcoef(b, m)[0, 1] > 0.5
    assert abs(float((b - m).mean())) < 0.02
    # bracket + ref is the bracket on the board's scale: it sits on board +
    # day, and closer than the raw bracket does
    imp = np.array([x["bracket"] + x["ref"] for x in races])
    assert np.isfinite(imp).all()
    assert abs(float((imp - m).mean())) < 0.01
    assert float(np.abs(imp - m).mean()) <= float(np.abs(b - m).mean()) + 1e-9
    assert all(np.isfinite(x["bracket_top"]) for x in races)
    assert all(np.isfinite(x["front"]) and x["front"] >= x["depth"] for x in races)
    # the venue was planted to get 5% harder over seven years
    assert 0.003 < r["slope_per_year"] < 0.012, r["slope_per_year"]
    assert [x["year"] for x in r["by_year"]] == list(range(2018, 2026))
    # a wrong era width is refused rather than silently misaligned
    import pytest
    with pytest.raises(SystemExit):
        cb.bracket(cols, npz, ["XC:100:"], era_years=0)
    cb.report(res, top=0.3)
    text = capsys.readouterr().out
    assert "bracket trend" in text and "XC:100:d5000" in text


def test_the_bracket_scales_like_a_sort_not_a_square():
    """★ THE BUG OF 2026-09-12: the first cut copied the whole row array once
    per athlete-season and never finished on the corpus. Two million rows
    and 300,000 athlete-seasons here; a sort finishes in seconds, a square
    never does."""
    import time
    rng = np.random.default_rng(9)
    n_ath, n_year, per = 60_000, 5, 7                 # 300k athlete-seasons
    n_cell, n_days = 400, 12
    ath = np.repeat(np.arange(n_ath), n_year * per)
    year = np.tile(np.repeat(np.arange(2019, 2019 + n_year), per), n_ath)
    n = ath.size
    course = rng.integers(0, n_cell, n)
    days = (2025 - year) * 365.0 + rng.integers(0, n_days, n) * 14.0
    norm = np.exp(rng.normal(0, 0.05, n) + 0.0005 * course)
    cols = {"athlete": ath, "year": year, "course": course, "days": days,
            "sport": np.zeros(n, dtype=np.int64), "norm": norm,
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": [f"XC:{100 + c}:d5000" for c in range(n_cell)]}
    npz = {"delta": np.zeros(n_cell), "course_keys": np.array(cols["course_keys"])}
    t0 = time.time()
    res = cb.bracket(cols, npz, ["XC:100:", "XC:101:"], window=45)
    took = time.time() - t0
    assert set(res) == {"XC:100:d5000", "XC:101:d5000"}
    assert all(len(r["races"]) > 0 for r in res.values())
    assert took < 60, took


def test_the_venue_subset_changes_nothing_but_the_time():
    """The bracket is within an athlete-season, so keeping only the seasons
    that raced the venue gives the same numbers as the whole pack."""
    cols, keep, keys, sport_of_course = _era_pack()
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            cols, keep, sport_offset=False, curve=False, rust=False, dist=False,
            slope=False, link=False, altitude=False, era_years=2,
            importance="none", indoor=True, dist_table=False)
        y = np.log(cols["norm"][keep])
        out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=2,
                            tilt=False, n_probe=0)
    npz = {"delta": out["delta"], "race_effect": out["race_effect"],
           "rating": out["rating"], "course_keys": np.array(D.course_keys)}
    full = cb.bracket(cols, npz, ["XC:101:"], window=60, era_years=2, subset=False)
    small = cb.bracket(cols, npz, ["XC:101:"], window=60, era_years=2, subset=True)
    a = full["XC:101:d5000"]["races"]; b = small["XC:101:d5000"]["races"]
    assert len(a) == len(b) > 0
    # race ids are renumbered inside the subset; match days, not ids
    key = lambda r: (r["year"], r["days_ago"])
    for x, y_ in zip(sorted(a, key=key), sorted(b, key=key)):
        assert key(x) == key(y_) and x["n"] == y_["n"]
        assert abs(x["bracket"] - y_["bracket"]) < 1e-12
        assert abs(x["ref"] - y_["ref"]) < 1e-12 and x["cell_key"] == y_["cell_key"]
        assert np.isfinite(x["board"]) and x["board"] == y_["board"]


def test_the_engine_block_rebuilds_the_published_number_and_the_readings_match(capsys):
    """★ OWNER, 2026-09-13: the venue diagnostic and the engine must agree
    -- "we made diagnostics that capture course difficulty and then we
    aren't using it". Under --difficulty bracket the solve file carries
    the engine's per-cell arithmetic; the venue report shows each race's
    engine reading (bracket / h + the references' board) and the chain
    raw -> history -> era -> pin -> recentre -> published, and the chain
    lands on the published number."""
    cols, keep, keys, sport_of_course = _era_pack()
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            cols, keep, sport_offset=False, curve=False, rust=False, dist=False,
            slope=False, link=False, altitude=False, era_years=2,
            importance="none", indoor=True, dist_table=False)
        y = np.log(cols["norm"][keep])
        out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=3,
                            tilt=False, n_probe=0)
        rj.bracketDifficulties(out, D, cols, keep, y, athlete_pool, pool_names,
                               window=60, top=0.5)
    import bracket_engine as be
    npz = {"delta": out["delta"], "race_effect": out["race_effect"],
           "rating": out["rating"], "course_keys": np.array(D.course_keys),
           "mu": out["mu"], "bracket_prior_races": np.array([be.PRIOR_RACES]),
           "bracket_race_sat": np.array([be.RACE_SAT]),
           "bracket_prior_group_names": np.array(list(be.PRIOR_GROUP_NAMES))}
    for k in ("bracket_cell_raw", "bracket_votes", "bracket_races_per_cell", "bracket_base",
              "bracket_base_votes", "bracket_pin", "bracket_shift", "bracket_cell_fit",
              "bracket_prior_group"):
        npz[k] = np.asarray(out[k])
    res = cb.bracket(cols, npz, ["XC:100:"], window=60, top=0.5, era_years=2)
    r = res["XC:100:d5000"]
    eng = r["engine"]
    assert len(eng) == 4                      # four two-year eras
    for e in eng:
        assert e["races"] >= 10 and e["votes"] > 4
        # the chain lands on the published number (the engine's iterate is
        # within its tolerance of the exact fixed-point step)
        assert abs(e["fit"] - e["pin"] * 0 - (e["era"] - e["pin"])) < 1e-12
        assert abs(e["fit"] - e["shift"] + e["level"] - e["published"]) < 2e-4
    # the races' engine readings, vote-weighted per era, are the era's raw
    by_cell = {}
    for x in r["races"]:
        if np.isfinite(x["reading"]) and x["race_weight"] > 0:
            s_, w_ = by_cell.get(x["cell_key"], (0.0, 0.0))
            by_cell[x["cell_key"]] = (s_ + x["race_weight"] * x["reading"], w_ + x["race_weight"])
    for e in eng:
        s_, w_ = by_cell[e["cell_key"]]
        # the diagnostic's readings are the engine's to a few tenths of a
        # percent: same references, same tilt, same voters up to ties
        assert abs(s_ / w_ - e["raw"]) < 0.004, (e["cell_key"], s_ / w_, e["raw"])
    cb.report(res, None, top=0.5)
    text = capsys.readouterr().out
    assert "the engine's arithmetic" in text and "reading" in text
