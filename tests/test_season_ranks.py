"""The precomputed rank line (311): the build's statement has the board's
partitions, and the page reads the table before it counts.

    python -m pytest -q tests/test_season_ranks.py
"""
import contextlib
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    @contextlib.contextmanager
    def _noConn():
        yield None
    _db.getConn = _noConn
    _db.initPool = lambda *a, **k: None
    sys.modules["database"] = _db

import build_season_ranks as B                                   # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_statement_partitions_like_the_boards():
    sql = " ".join(B.rankSql(True).split())
    assert "CREATE TABLE season_rank_new AS" in sql
    # the floor and the US scope, as the board applies them
    assert "(n_races >= 3 OR year >= 2026) AS ranked" in sql
    assert "(state = ANY(%(us)s)) AS us" in sql
    # nation: the board's own order inside the board's own set
    assert ("CASE WHEN ranked AND us THEN row_number() OVER (PARTITION BY pool, sport, year, ranked, us "
            "ORDER BY mean_rating DESC, person_id) END::int AS nation") in sql
    # the high-school units carry the state; the nested ones their section
    assert "PARTITION BY pool, sport, year, ranked, us, state, sec, secd ORDER BY" in sql
    assert "PARTITION BY pool, sport, year, ranked, us, state, sec, ar ORDER BY" in sql
    assert "PARTITION BY pool, sport, year, ranked, us, state, lg ORDER BY" in sql
    # the college units carry nothing
    assert "PARTITION BY pool, sport, year, ranked, us, dv ORDER BY" in sql
    # state_div is the board's two columns folded into one key
    assert 'coalesce("state_div", "class") AS sd' in sql
    # team: strictly faster + 1, any pool, any race count
    assert "rank() OVER (PARTITION BY school, sport, year ORDER BY mean_rating DESC) END::int AS team" in sql
    # an older database without the unit columns still builds nation, state and team
    old = " ".join(B.rankSql(False).split())
    assert '"section"' not in old and "NULL::text AS sec" in old


def test_the_page_reads_the_table_first():
    app = read("racecast", "app.py")
    assert "def precomputedRanks(cur, person_id, season):" in app
    assert "to_regclass('public.season_rank')" in app
    assert "pre = precomputedRanks(cur, person_id, season)" in app
    # every scope short-circuits on the row
    assert 'return pre.get("state_rank" if with_state else "nation")' in app
    assert 'return pre.get("nation_total")' in app
    assert "return pre.get(kind)" in app
    assert 'row = {"place": pre["team"]} if pre.get("team") else None' in app
    assert 'step 10g_season_ranks "$PY" -u racecast/build_season_ranks.py' in read("deploy", "run_pipeline.sh")
    assert B._COLS == ("nation", "nation_total", "state_rank", "state_div", "section", "section_div",
                       "area", "league", "division", "region", "conference", "team")
