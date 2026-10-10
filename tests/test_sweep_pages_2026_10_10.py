"""Page bugs from the read-only review of sweep 2026-10-10.

No Postgres: the SQL is read off stub cursors and the templates are rendered
with the site's own Jinja environment.

    python -m pytest -q tests/test_sweep_pages_2026_10_10.py
"""
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

TEMPLATES = os.path.join(_ROOT, "racecast", "templates")


def _tpl(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        return fh.read()


class StubCursor:
    """Records every statement; answers from a list of canned results, each
    either a list of rows (fetchall/fetchone take from it) or a callable
    (sql, params) -> rows."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.sql = []
        self._rows = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        a = self.answers.pop(0) if self.answers else []
        self._rows = list(a(sql, params) if callable(a) else a)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, n):
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows


@pytest.fixture(scope="module")
def A():
    import app
    return app


# ---- 1. links pin the feed --------------------------------------------- #

def test_school_meets_pin_the_feed_and_name_it():
    import school
    cur = StubCursor([[{"meet_id": 7, "pin_rid": -301, "date": "2025-10-01",
                        "runners": 7, "divisions": 1, "avg_rating": None,
                        "best_rating": None, "distances": [8000],
                        "meet_name": "tfrrs meet"}]])
    rows = school.schoolMeets(cur, "Dana Hills", "XC")
    sql = cur.sql[-1][0]
    assert "min(rr.result_id)" in sql and "pin_rid" in sql
    assert "GROUP  BY rr.meet_id, (rr.result_id < 0)" in sql
    assert "WHEN g.pin_rid < 0" in sql            # the pin picks the name table
    assert rows[0]["pin_rid"] == -301


@pytest.mark.parametrize("tpl", ["school.html", "embed_school.html"])
def test_school_meet_links_carry_the_pin(tpl):
    assert "r={{ m.pin_rid }}" in _tpl(tpl)


def test_course_meet_links_carry_the_pin(A):
    src = _tpl("course.html")
    assert src.count("?r={{ m.pin_rid }}") == 2
    cur = StubCursor([[]])
    A.get_course_meets(cur, "Woodward Park")
    assert "min(r.result_id) AS pin_rid" in cur.sql[-1][0]


def test_meet_tf_event_links_carry_alt_and_school(A):
    src = _tpl("meet_tf.html")
    macro = re.search(r"{% macro race_q\(\) %}.*?{% endmacro %}", src).group(0)
    t = A.app.jinja_env.from_string(macro + "{{ race_q() }}")
    assert t.render(other_sources=[{"alt": 0, "n": 3}], alt_idx=1,
                    school="A B") == "?alt=1&amp;school=A%20B"
    assert t.render(other_sources=[], alt_idx=0, school=None) == ""
    assert "{{ e.div_id }}{{ race_q() }}" in src


def test_race_tf_links_back_with_alt():
    src = _tpl("race_tf.html")
    assert ('/meet/tf/{{ header.meet_id }}{% if other_sources %}'
            '?alt={{ alt_idx }}{% endif %}') in src


def _srcRows(*names):
    return [{"source": n, "n": 10 - i} for i, n in enumerate(names)]


def test_race_tf_honours_alt_when_that_feed_ran_it(A):
    # meet feeds: anet (alt 0), tfrrs (alt 1); both ran the triple
    cur = StubCursor([_srcRows("anet"), _srcRows("tfrrs"),
                      [{"source": "anet"}, {"source": "tfrrs"}]])
    src, alt, others = A._tfRaceSource(cur, 9, 1, 2, {"alt": "1"})
    assert (src, alt) == ("tfrrs", 1) and others == [{"alt": 0, "n": 10}]


def test_race_tf_alt_falls_back_when_that_feed_did_not_run_it(A):
    cur = StubCursor([_srcRows("anet"), _srcRows("tfrrs"),
                      [{"source": "anet"}]])
    src, alt, _ = A._tfRaceSource(cur, 9, 1, 2, {"alt": "1"})
    assert (src, alt) == ("anet", 0)


def test_race_tf_pin_wins(A):
    cur = StubCursor([[{"source": "tfrrs"}], _srcRows("anet"), _srcRows("tfrrs")])
    src, alt, _ = A._tfRaceSource(cur, 9, 1, 2, {"r": "-5", "alt": "0"})
    assert (src, alt) == ("tfrrs", 1)


# ---- 3. distance overrides --------------------------------------------- #

def test_course_rows_take_the_override(A):
    cte = A._courseRowsCte()
    assert cte.count("LEFT JOIN dist_override dov") == 2
    assert cte.count("COALESCE(dov.distance::real") == 2
    assert "dist_drop" not in cte
    assert cte.count("dist_drop") == 0
    assert A._courseRowsCte(drop=True).count("FROM dist_drop dd") == 2


@pytest.mark.parametrize("has_drop", [True, False])
def test_course_time_records_leave_out_dropped_divisions(A, has_drop):
    for fn in (A.get_course_records, A.get_course_team_records):
        cur = StubCursor([[{"d": "dist_drop" if has_drop else None}], []])
        fn(cur, "Woodward Park", 5000)
        assert ("FROM dist_drop dd" in cur.sql[-1][0]) is has_drop


def test_compiled_races_take_the_override():
    import meet_compile as M
    cur = StubCursor([[{"1": 1}], []])
    M.compiledResults(cur, 9)
    sql = cur.sql[-1][0]
    assert "LEFT JOIN dist_override dov" in sql
    assert sql.count("COALESCE(\n                   dov.distance::real") + \
        sql.count("COALESCE(\n                 dov.distance::real") == 2
    cur = StubCursor([[]])
    M.compiledIndex(cur, 9)
    sql = cur.sql[-1][0]
    assert "LEFT JOIN dist_override dov" in sql
    assert sql.count("dov.distance::real, m.distance") == 2
