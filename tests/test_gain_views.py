# Project: xc-predictor / tests
# File:    test_gain_views.py
# Purpose: four ways to say "improved", offered side by side, and the two
#          expensive ones are pay-per-use (owner, 2026-09-16: "is gaining 30
#          speed rating at 80 vs 110 the same amount of improvement
#          percentage wise?" -- no, and neither number is improvement).
#          No database.
#
#   python -m pytest -q tests/test_gain_views.py
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import recruiting as R                                         # noqa: E402

_SRC = open(os.path.join(_ROOT, "racecast", "recruiting.py")).read()


def _render(sort):
    """The query as searchRecruits will build it for this sort."""
    q = _SRC[_SRC.index('    cur.execute(f"""\n        WITH cur AS ('):]
    q = q[:q.index('""", params)')]
    if str(sort).startswith("gain_"):
        c = R._VIEW_CTE.format(band=R.GAIN_BAND, min_band_n=R.MIN_BAND_N)
        col, j = R._VIEW_COLS, R._VIEW_JOINS.format(band=R.GAIN_BAND)
    else:
        c = col = j = ""
    return (q.replace("{view_cte}", c).replace("{view_cols}", col)
             .replace("{view_joins}", j).replace("{gain_where}", "")
             .replace("{grad_where}", "").replace("{nameLateral('j')}", "")
             .replace("{SORTS[f['sort']]}", R.SORTS[sort]))


def test_the_arithmetic_the_views_exist_for():
    """rating = 100 * pool_mean / time, so a gain of d from r is a time
    improvement of d/(r+d): 27.3% at 80, 21.4% at 110. Equal rating steps
    are NOT equal percentage steps, and the low end moves MORE."""
    def pct(r, d):
        return d / (r + d)
    assert round(pct(80, 30), 4) == 0.2727
    assert round(pct(110, 30), 4) == 0.2143
    assert pct(80, 30) > pct(110, 30)
    # and the view computes it off the NEW rating, which is r + d
    assert "/ g.mean_rating)::real END AS gain_pct" in R._VIEW_COLS


def test_every_sort_renders_a_query_with_nothing_left_unresolved():
    for sort in R.SORTS:
        q = _render(sort)
        assert not re.findall(r"\{(\w+)\}", q), (sort, "unresolved")
        assert not [m for m in re.finditer(r"%(?!\(|s)", q)], (sort, "stray %")
        assert q.count("SELECT") >= 3


def test_the_two_expensive_views_are_pay_per_use():
    """fld is two aggregates and curve is a self-join over a pool-year.
    Running them for a search that sorts by rating spends that on every page
    for nothing, under a 12s statement timeout."""
    plain = _render("rating")
    assert "fld AS (" not in plain and "curve AS (" not in plain
    assert "gain_resid" not in plain and "gain_z" not in plain
    rich = _render("gain_resid")
    assert "fld AS (" in rich and "curve AS (" in rich
    assert rich.count("LEFT JOIN fld") == 2          # this year and last
    assert "wants_views = str(f.get(\"sort\") or \"\").startswith(\"gain_\")" in _SRC


def test_the_expectation_is_the_whole_pool_not_the_filtered_search():
    """An expectation built from the rows a user filtered to would move with
    the filter, and "improved more than expected" would mean something
    different on every page."""
    cte = R._VIEW_CTE
    assert "FROM   athlete_season p2" in cte and "JOIN   athlete_season c2" in cte
    assert "HAVING count(*) >=" in cte               # a band needs a population
    assert "round(p2.mean_rating /" in cte           # banded on the START
    # the band is the athlete's PRIOR rating on both sides, or the join is a lie
    assert "round(p.mean_rating /" in R._VIEW_JOINS


def test_the_raw_gain_view_is_still_there():
    """It is what a coach means by improved, and it is the one the owner was
    reading. The point is the others beside it, not replacing it."""
    assert "gain" in R.SORTS and "j.gain DESC" in R.SORTS["gain"]
    assert set(R.SORTS) >= {"rating", "gain", "gain_pct", "gain_z",
                            "gain_resid", "best", "grad"}
