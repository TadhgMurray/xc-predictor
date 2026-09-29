# Project: xc-predictor / tests
# File:    test_course_bests_hs_order.py
# Purpose: a course page's "Best speed ratings" and "Best team performances
#          by rating" are RANKED on the HS-equivalent they show.
#
# ★ THE REPORT (owner, 2026-09-29): "should sort by hs equivalent no matter
#   what". Hidden Valley Park's boys list opened
#       1 Ean Hernandez   108.8  12:18.8 3218m  Mount Diablo Heat 2025
#       2 Craig Thompson  108.8  12:19.7 3218m  Mount Diablo Heat 2025
#       3 Mike Stone      138.5  15:35   4828m  Las Lomas 1986
#   because the query ranked on the stored OWN-POOL rating (139.3 for a
#   youth-club two-mile) and the page drew the HS-equivalent (108.8) beside
#   it. The team list below it did the same with Mount Diablo Heat at 136.6.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_course_bests_hs_order.py
import io
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as A                                               # noqa: E402
import pool_view as PV                                        # noqa: E402

# the factors the live page implied on 2026-09-29: 108.8/139.3, 119.8/137.2
_FACTORS = {"hs_m": 1.0, "hs_f": 1.0, "elem_m": 0.781, "ms_m": 0.873,
            "ms_f": 0.873, "college_m": 1.2}


def _row(pid, own, pool, gender="M", **kw):
    r = {"person_id": pid, "result_id": pid * 10 + len(kw), "speed_rating": own,
         "rating_pool": pool, "gender": gender, "distance": 3218,
         "time_seconds": 700.0, "name": f"p{pid}"}
    r.update(kw)
    return r


class _Cur:
    """Serves the three statements get_course_rating_bests runs: the
    rating_pool column probe, the bests query, the ranking_results lookup."""

    def __init__(self, rows):
        self.rows, self.sql, self._last = rows, [], None

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = sql

    def fetchone(self):
        return {"?column?": 1}                     # rating_pool is present

    def fetchall(self):
        if "ranking_results" in self._last:
            return []
        return [dict(r) for r in self.rows]


def _patchFactors(monkeypatch):
    monkeypatch.setattr(PV, "hsFactor",
                        lambda pool, sport, d: _FACTORS.get(pool))
    A._RATING_POOL.clear()


def test_the_hidden_valley_list_ranks_on_the_hs_number(monkeypatch):
    _patchFactors(monkeypatch)
    rows = [  # the SQL's order: own-pool rating
        _row(1, 139.3, "elem_m"),   # Ean Hernandez   -> 108.8
        _row(2, 139.2, "elem_m"),   # Craig Thompson  -> 108.7
        _row(3, 138.5, "hs_m"),     # Mike Stone
        _row(4, 137.8, "hs_m"),     # John Smith
        _row(7, 137.2, "ms_m"),     # Eli Hernandez   -> 119.8
        _row(8, 137.2, "hs_m"),     # Wyatt Landrum
    ]
    out = A.get_course_rating_bests(_Cur(rows), "Hidden Valley Park")
    shown = [round(r["hs_rating"], 1) for r in out]
    assert shown == sorted(shown, reverse=True), shown
    assert [r["person_id"] for r in out] == [3, 4, 8, 7, 1, 2]
    assert out[0]["hs_rating"] == 138.5


def test_an_athlete_is_listed_on_their_best_hs_run(monkeypatch):
    """8th grade 139 own (121 HS) and a senior 125: the senior run is the
    better one and the one listed -- the old per-athlete pick took the 139."""
    _patchFactors(monkeypatch)
    rows = [_row(5, 139.0, "ms_m", date="2022"), _row(5, 125.0, "hs_m", date="2026"),
            _row(6, 124.0, "hs_m")]
    out = A.get_course_rating_bests(_Cur(rows), "c")
    assert [(r["person_id"], r["speed_rating"]) for r in out] == [(5, 125.0), (6, 124.0)]


def test_the_cut_is_per_gender_and_after_the_hs_sort(monkeypatch):
    _patchFactors(monkeypatch)
    rows = [_row(1, 140.0, "elem_m"), _row(2, 120.0, "hs_m"), _row(3, 119.0, "hs_m"),
            _row(9, 150.0, "hs_f", gender="F"), _row(10, 149.0, "ms_f", gender="F")]
    out = A.get_course_rating_bests(_Cur(rows), "c", limit=2)
    assert [r["person_id"] for r in out if r["gender"] == "M"] == [2, 3]
    assert [r["person_id"] for r in out if r["gender"] == "F"] == [9, 10]


def test_a_row_without_a_factor_ranks_on_what_it_shows():
    """No factor -> hs_rating None -> the page shows the own number, so that
    is the number it ranks on."""
    rows = [{"person_id": 1, "gender": "M", "speed_rating": 130.0, "hs_rating": None},
            {"person_id": 2, "gender": "M", "speed_rating": 140.0, "hs_rating": 110.0}]
    assert [r["person_id"] for r in PV.bestByShown(rows, 5)] == [1, 2]


def test_the_sql_keeps_a_superset_per_pool():
    src = io.open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    i = src.index("def get_course_rating_bests(")
    body = src[i:src.index("\ndef ", i + 1)]
    assert "_courseRowsCte(pool_col)" in body
    assert re.search(r"PARTITION BY r\.person_id,\s*NULLIF\(split_part\(r\.rating_pool", body)
    assert "PARTITION BY gender, rating_pool" in body
    assert "bestByShown(rows, limit)" in body
    i = src.index("def get_course_team_rating_bests(")
    body = src[i:src.index("\ndef ", i + 1)]
    assert "PARTITION BY gender, pool" in body
    assert 'hs_key="hs_avg5"' in body


def test_the_team_list_ranks_and_renders_on_the_hs_number(monkeypatch):
    _patchFactors(monkeypatch)
    teams = [{"school": "Mount Diablo Heat", "gender": "M", "avg5": 136.6, "pool": "elem_m",
              "distance": 3218},
             {"school": "Dublin", "gender": "M", "avg5": 133.4, "pool": "hs_m",
              "distance": 3218}]
    out = A.get_course_team_rating_bests(_Cur(teams), "Hidden Valley Park")
    assert [t["school"] for t in out] == ["Dublin", "Mount Diablo Heat"]
    assert out[1]["hs_avg5"] == round(136.6 * 0.781, 1)
    tpl = io.open(os.path.join(_ROOT, "racecast", "templates", "course.html"),
                  encoding="utf-8").read()
    assert "rv(t.avg5, t.hs_avg5)" in tpl
    # "no matter what": the toggle swaps numbers, it does not re-rank
    assert "data-scale-sort" not in tpl


def test_the_share_card_shows_the_hs_number():
    src = io.open(os.path.join(_ROOT, "racecast", "cards.py"), encoding="utf-8").read()
    i = src.index("def courseCardData(")
    body = src[i:src.index("\ndef ", i + 1)]
    assert "r['hs_rating'] if r.get('hs_rating') is not None" in body
    assert 'key=lambda r: -(r.get("speed_rating") or 0)' not in body
