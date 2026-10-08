"""The course page's "Year by year" (owner, 2026-10-04: "every year of a
venue showed one difficulty"): race_day_effect rows by season, the eras of
the course number, each day matched to its meet. No database.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_course_history.py
"""
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))

import course_history as ch                                    # noqa: E402

ROWS = [
    {"race_date": "2025-10-04", "day_effect": 0.021, "n_rows": 300, "course_effect": 0.076},
    {"race_date": "2025-10-04", "day_effect": 0.001, "n_rows": 100, "course_effect": 0.076},
    {"race_date": "2025-09-06", "day_effect": -0.012, "n_rows": 80, "course_effect": 0.076},
    {"race_date": "2024-10-05", "day_effect": 0.15, "n_rows": 50, "course_effect": 0.076},
    {"race_date": "2021-10-02", "day_effect": 0.0, "n_rows": 40, "course_effect": 0.052},
    {"race_date": "2022-01-08", "day_effect": None, "n_rows": 1, "course_effect": 0.052},
]
MEETS = [{"meet_id": 7, "meet_name": "Clovis Invite", "last_date": "2025-10-04",
          "distances": [5000.0], "n_results": 900},
         {"meet_id": 8, "meet_name": "Frosh Day", "last_date": "2025-10-04",
          "distances": [3200.0], "n_results": 2000}]


def test_seasons_eras_and_meets():
    out = ch.summarize(ROWS, MEETS, 5000, None)
    days = out["days"]
    # two divisions on one day merge, runner-weighted
    first = days[0]
    assert first["date"] == "2025-10-04" and first["n"] == 400
    assert abs(first["day"] - (0.021 * 300 + 0.001 * 100) / 400) < 1e-12
    # the meet that ran this distance wins the shared date
    assert first["meet"] == "Clovis Invite" and first["meet_id"] == 7
    # a NULL day is dropped, a capped day flagged
    assert all(d["date"] != "2022-01-08" for d in days)
    assert next(d for d in days if d["date"] == "2024-10-05")["capped"]
    seasons = {s["season"]: s for s in out["seasons"]}
    assert set(seasons) == {2025, 2024, 2021}
    assert seasons[2025]["days"] == 2 and seasons[2025]["runners"] == 480
    assert abs(seasons[2024]["day"] - ch.DAY_CAP) < 1e-12          # clipped
    assert out["eras"] == [{"first": 2021, "last": 2021, "course": 0.052},
                           {"first": 2024, "last": 2025, "course": 0.076}]
    assert abs(seasons[2021]["together"] - 0.052) < 1e-12
    assert out["has_course"] and 0.05 <= out["scale"] <= 0.25


def test_nothing_to_show():
    assert ch.summarize([], MEETS, 5000) is None
    assert ch.summarize([{"race_date": "2025-10-04", "day_effect": None}]) is None
    assert ch.together(None, None) is None
    assert abs(ch.together(0.05, 0.5) - (1.05 * math.exp(0.10) - 1)) < 1e-12


def test_a_january_race_is_last_seasons():
    assert ch.seasonOf("2022-01-08") == 2021
    assert ch.seasonOf("2025-10-04") == 2025


def test_without_course_effect_the_page_says_so():
    rows = [dict(r, course_effect=None) for r in ROWS]
    out = ch.summarize(rows, None, 5000)
    assert not out["has_course"] and out["eras"] == []
    assert all(s["course"] is None for s in out["seasons"])


class Cur:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.sql, self.params = rows or [], fail, None, None
        self.connection = types.SimpleNamespace(rollback=lambda: None)

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        if self.fail:
            raise RuntimeError("no race_day_effect")

    def fetchall(self):
        return self.rows


def test_fetch_keys_every_cell_of_the_name_and_survives_a_missing_table():
    """Owner, 2026-10-08: "not every meet is shown" -- the day list read
    only the biggest of the name's cells; now every cell of the name at the
    distance (summarize merges a date two cells share)."""
    cur = Cur(ROWS)
    assert ch.fetchHistory(cur, "Woodward Park", 4997, "rde.course_effect") == ROWS
    assert cur.params == {"course": "Woodward Park", "dm": 5000}
    cell = cur.sql.split("WITH cell AS", 1)[1].split(")", 1)[0]
    assert "LIMIT" not in cell and "DISTINCT" in cell
    assert "rde.course_effect AS course_effect" in cur.sql
    assert ch.fetchHistory(Cur(fail=True), "X", 5000) == []
    assert ch.fetchHistory(Cur(ROWS), "X", None) == []


def test_the_partial_renders():
    import pytest
    app = pytest.importorskip("app")
    out = ch.summarize(ROWS, MEETS, 5000, "all")
    tpl = app.app.jinja_env.get_template("_course_history.html")
    with app.app.test_request_context("/course/Woodward%20Park"):
        html = tpl.render(course_history=out)
        empty = tpl.render(course_history=None)
    assert "Year by year" in html and "Difficulty by era" in html
    assert "2021" in html and "+7.6%" in html and "+5.2%" in html
    assert "/meet/xc/7" in html and "cross country ratings carry it" in html
    assert "Year by year" not in empty


def test_the_course_route_passes_it():
    src = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert 'render_template("course.html", course_history=history, **ctx)' in src
    tpl = open(os.path.join(ROOT, "racecast", "templates", "course.html"),
               encoding="utf-8").read()
    assert '{% include "_course_history.html" %}' in tpl
