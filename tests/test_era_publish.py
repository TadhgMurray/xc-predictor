# Project: xc-predictor / tests
# File:    test_era_publish.py
# Purpose: With --era-years the solve keys cells '<key>@e<k>'. The page looks
#          a venue up by its BARE key, so the go-live publishes each venue's
#          latest solved era under it and the venue-key splitter drops the
#          suffix (2026-09-11, the owner: "are we currently doing the every
#          couple years split? I think that could help").
#
#   python -m pytest -q tests/test_era_publish.py
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_golive as jg                                      # noqa: E402

# the key splitter lives in the pack module, which imports the database
# driver at import time; no database here
try:
    import speed_ratings_db as sdb                             # noqa: E402
except ImportError:                                            # no psycopg2
    import types
    from unittest import mock
    _pg = mock.MagicMock()
    _pg.__path__ = []                                          # a package
    sys.modules.setdefault("psycopg2", _pg)
    for _sub in ("errors", "extras", "extensions", "sql", "pool"):
        sys.modules.setdefault(f"psycopg2.{_sub}", mock.MagicMock())
    sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
    import speed_ratings_db as sdb                             # noqa: E402


def test_the_latest_solved_era_publishes_under_the_bare_key():
    keys = ["XC:Ultimook:d5000@e0", "XC:Ultimook:d5000@e1", "XC:Ultimook:d5000@e2",
            "TF:loc:7:in@e0", "TF:loc:7:in@e1", "XC:Balboa:d5000", "TF:loc:9:out"]
    solved = np.array([True, True, False, True, True, True, False])
    bare, pub = jg.latestEraKeys(keys, solved)
    assert bare == ["XC:Ultimook:d5000"] * 3 + ["TF:loc:7:in"] * 2 + ["XC:Balboa:d5000", "TF:loc:9:out"]
    # Ultimook's latest SOLVED era is e1 (e2 is unsolved); the oval's is e1;
    # the un-split cells publish iff solved
    assert pub.tolist() == [False, True, False, False, True, True, False]
    # no eras at all: the mask is just `solved`
    bare2, pub2 = jg.latestEraKeys(["XC:A:d5000", "TF:loc:1:out"], np.array([True, False]))
    assert bare2 == ["XC:A:d5000", "TF:loc:1:out"] and pub2.tolist() == [True, False]


def test_the_venue_key_splitter_drops_the_era_suffix():
    assert sdb._splitVenueKey("TF:loc:7:in@e3", {}) == ("TF:loc:7:in", None, None)
    assert sdb._splitVenueKey("XC:123:d5000@e2", {"123": "Ultimook"}) == ("XC:Ultimook", 123, 5000)
    assert sdb._splitVenueKey("XC:123:d5000", {"123": "Ultimook"}) == ("XC:Ultimook", 123, 5000)


def test_the_era_index_grows_with_the_year():
    import run_joint as rj
    course = np.array([0, 0, 0, 1, -1])
    year = np.array([2016, 2019, 2024, 2024, 2024])
    new, n_new, group, keys, pairs, w, per_base = rj.eraCells(
        course, year, 2, 2, np.array([0, 0]), ["XC:A:d5000", "XC:B:d5000"])
    assert keys[new[0]] == "XC:A:d5000@e0"
    assert keys[new[2]] == "XC:A:d5000@e4"          # (2024 - 2016) // 2
    assert int(keys[new[2]].rpartition("@e")[2]) > int(keys[new[1]].rpartition("@e")[2])


def _era_pack(seed=2, n_ath=600, years=(2018, 2026), n_xc=20, n_tfo=8, n_tfi=2,
              races_per_year=6):
    """A pack the design builder and the go-live both accept: eight years,
    thirty base courses, six races per course per year, six races per
    athlete-season. Course 0 gets 5% harder over the eight years (an
    Ultimook); every other course holds still.

    ⚠ TWELVE RACE DAYS PER ERA ON PURPOSE. An era's shift and the mean of
      its days' race-day terms are the same data; only the priors split
      them. With two days a year the planted change and the realised
      weather of four days were the same size (the first cut of this test
      read the era as "a third of the change" for that reason); with
      twelve the weather mean is a quarter of it and the era follows."""
    rng = np.random.default_rng(seed)
    n_course = n_xc + n_tfo + n_tfi
    sport_of_course = np.r_[np.zeros(n_xc, int), np.ones(n_tfo + n_tfi, int)]
    keys = ([f"XC:{100 + i}:d5000" for i in range(n_xc)]
            + [f"TF:loc:{i}:out" for i in range(n_tfo)]
            + [f"TF:loc:{n_tfo + i}:in" for i in range(n_tfi)])
    base_d = np.r_[rng.normal(0, 0.05, n_xc), rng.normal(0, 0.02, n_tfo + n_tfi)]
    y0, y1 = years
    race_course, race_year, race_days = [], [], []
    for c in range(n_course):
        for yr in range(y0, y1):
            for k in range(races_per_year):
                race_course.append(c); race_year.append(yr)
                # days ago, counted from a pack date after the last season
                race_days.append((y1 - yr) * 365 - 100 - 14 * k)
    race_course = np.array(race_course); race_year = np.array(race_year)
    race_days = np.array(race_days, dtype=np.float64)
    race_u = rng.normal(0, 0.015, race_course.size)
    ath, rac = [], []
    for i in range(n_ath):
        for yr in range(y0, y1):
            if rng.random() < 0.5:
                continue
            picks = rng.choice(np.flatnonzero(race_year == yr), 6, replace=False)
            ath.extend([i] * 6); rac.extend(picks.tolist())
    ath = np.array(ath); rac = np.array(rac)
    course = race_course[rac]; year = race_year[rac]; days = race_days[rac]
    a_true = rng.normal(0, 0.15, n_ath)
    drift = np.where(course == 0, 0.05 * (year - y0) / (y1 - 1 - y0), 0.0)
    level = np.where(sport_of_course[course] == 1, -0.05, 0.0)
    y = (a_true[ath] + level + base_d[course] + drift + race_u[rac]
         + rng.normal(0, 0.02, ath.size))
    n = y.size
    cols = {"athlete": ath, "year": year, "course": course, "days": days,
            "sport": sport_of_course[course], "norm": np.exp(y),
            "result_id": np.arange(n) + 900_000,
            "athlete_keys": [(5000 + i, "hs_m") for i in range(n_ath)],
            "course_keys": keys}
    return cols, np.ones(n, dtype=bool), keys, sport_of_course


def test_the_go_live_runs_on_an_era_split_design_and_publishes_bare_keys():
    """★ THE CRASH OF RUN 20 (2026-09-12): the go-live took its cell keys
    from the pack (74,366 base courses) while the era-split design had
    219,715 (course, era) cells, and died three hours in. The design's
    keys are the ones every per-cell array is indexed by."""
    import run_joint as rj
    import joint_solve as js
    cols, keep, keys, sport_of_course = _era_pack()
    D, athlete_pool, pool_names = rj.buildDesign(
        cols, keep, sport_offset=False, curve=False, rust=False, dist=False,
        slope=False, link=False, altitude=False, era_years=2,
        importance="none", indoor=True, dist_table=False)
    n_base = len(keys)
    assert D.n_cell > n_base and len(D.course_keys) == D.n_cell
    assert sum("@e" in k for k in D.course_keys) == D.n_cell
    y = np.log(cols["norm"][keep])
    out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=3,
                        tilt=False, n_probe=0)
    live = jg.buildLive(out, D, cols, keep)
    # one published difficulty per base course, under the bare key
    assert set(live["diffs"]) <= set(keys) and len(live["diffs"]) == n_base
    assert not any("@e" in k for k in live["diffs"])
    # the file the diagnostics read is keyed per (course, era) cell
    assert live["npz"]["course_keys"].size == D.n_cell
    assert live["npz"]["difficulty"].size == D.n_cell
    # every row is rated, and the published number for the drifting course
    # is its LATEST era's, which is the hardest one
    s = live["summary"]
    assert s["n_rated"] == s["n_rows"], s
    eras0 = sorted((int(k.rpartition("@e")[2]), i) for i, k in enumerate(D.course_keys)
                   if k.startswith(keys[0] + "@e"))
    first, last = eras0[0][1], eras0[-1][1]
    moved = float(out["delta"][last] - out["delta"][first])
    print(f"  drifting course moved {moved:+.4f} from its first era to its last "
          f"(planted about +0.043)")
    assert 0.025 < moved < 0.06, moved
    assert abs(live["diffs"][keys[0]]["difficulty"]
               - float(live["npz"]["difficulty"][last])) < 1e-12
    # a course that held still publishes about the same number from any era
    eras1 = [i for i, k in enumerate(D.course_keys) if k.startswith(keys[1] + "@e")]
    assert np.ptp(out["delta"][eras1]) < 0.02, np.ptp(out["delta"][eras1])
