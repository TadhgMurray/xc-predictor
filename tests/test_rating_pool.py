"""The pool a rating was computed in rides with it (issue 218): the
staging carries a pool column, the merge rebuilds a second column in the
same pass, and the page prefers the row's own pool. No database.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_rating_pool.py
"""
import io
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import merge_column as mc                                       # noqa: E402
import speed_ratings_db as db                                   # noqa: E402


def test_select_list_merges_an_extra_column_the_same_way():
    cols = ["result_id", "speed_rating", "rating_pool", "time_seconds"]
    sel = mc._selectList(cols, "speed_rating", "val", True,
                         extra=(("rating_pool", "pool"),))
    assert "COALESCE(s.val, r.speed_rating) AS speed_rating" in sel
    assert "COALESCE(s.pool, r.rating_pool) AS rating_pool" in sel
    assert "r.time_seconds" in sel
    sel2 = mc._selectList(cols, "speed_rating", "val", False,
                          extra=(("rating_pool", "pool"),))
    assert "s.val AS speed_rating" in sel2 and "s.pool AS rating_pool" in sel2


def test_pairs_become_triples_with_or_without_a_pool():
    rid = np.array([1, 2]); val = np.array([120.5, 99.1])
    assert list(db._asPairs((rid, val))) == [(1, 120.5, None), (2, 99.1, None)]
    pool = np.array(["hs_m", "ms_f"], dtype=object)
    assert list(db._asPairs((rid, val, pool))) == [(1, 120.5, "hs_m"), (2, 99.1, "ms_f")]
    assert list(db._asPairs([(3, 100.0), (4, 101.0, "hs_f")])) == \
        [(3, 100.0, None), (4, 101.0, "hs_f")]


def test_writers_and_readers_carry_the_column():
    src = io.open(os.path.join(ROOT, "engine", "speed_ratings_db.py"), encoding="utf-8").read()
    assert "ADD COLUMN IF NOT EXISTS rating_pool text" in src
    assert 'extra=(("rating_pool", "pool"),)' in src
    assert "rating_pool  = COALESCE(t.pool" in src
    gl = io.open(os.path.join(ROOT, "engine", "joint_golive.py"), encoding="utf-8").read()
    assert "pool_row[m])" in gl
    fill = io.open(os.path.join(ROOT, "engine", "fill_ratings.py"), encoding="utf-8").read()
    assert "yield (row.result_id, rating, pool)" in fill
    pv = io.open(os.path.join(ROOT, "racecast", "pool_view.py"), encoding="utf-8").read()
    assert 'row.get("rating_pool") or pools.get' in pv
    app = io.open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert app.count("_ratingPoolCol(cur") >= 4
