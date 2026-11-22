"""The pack's COPY stream (speed_ratings_db._copyReady / _copyBatches,
2026-11-20) must hand the row loop exactly what the named cursor did: the
same rows, in the same order, the same Python values and types. And the
plan proof must refuse a plan whose order it cannot vouch for.

The value check needs a scratch Postgres (XCP_TEST_PG_DSN, skipped
without); the plan checks need none."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import speed_ratings_db as D                                     # noqa: E402

DSN = os.environ.get("XCP_TEST_PG_DSN")

# every awkward value the pack's columns can carry: NULLs, a real that is
# not its own double (0.1), extremes, every escape COPY writes, unicode,
# a literal backslash-N that is NOT a NULL, the empty string
_VALUES = """
  (1::bigint, 0.1::real, 'plain'::text, 7::int, 1e300::float8),
  (NULL, NULL, NULL, NULL, NULL),
  (-9223372036854775808, 3.4028235e38::real, E'tab\\there', -1, 'Infinity'),
  (9223372036854775807, 1.17549435e-38::real, E'nl\\nand\\rcr', 0, '-Infinity'),
  (42, -0.0::real, E'back\\\\slash \\\\N', 2147483647, 1.5e-300),
  (43, 1234.5677::real, E'\\\\N', NULL, 0.30000000000000004),
  (44, 786.40466::real, '', -2147483648, -0.0),
  (45, 1e-7::real, E'\\b\\f\\v ünï 中', 3, 2.5),
  (46, 'Infinity'::real, 'tfv:stadium 12, city:out', 4, 1)
"""


@pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")
def test_copy_gives_the_cursors_rows_values_and_types():
    import psycopg2
    sql = f"SELECT * FROM (VALUES {_VALUES}) v (a, b, c, d, e)"
    # repeat so batches are split mid-stream, like the real 200k batches
    sql = f"SELECT v.* FROM ({sql}) v, generate_series(1, 7) g ORDER BY g, a"
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor(name="old")
        cur.itersize = 4
        cur.execute(sql)
        old = []
        while True:
            rows = cur.fetchmany(4)
            if not rows:
                break
            old.append(rows)
        cur.close()
        c = conn.cursor()
        c.execute(f"SELECT * FROM ({sql}) q LIMIT 0")
        codes = [d.type_code for d in c.description]
        new = list(D._copyBatches([(conn, f"COPY ({sql}) TO STDOUT")], 4, codes))
    finally:
        conn.close()
    # repr: 1 vs 1.0, -0.0 vs 0.0, None, and every float bit for bit
    assert repr(new) == repr(old)
    assert [len(b) for b in new] == [len(b) for b in old]


def _scan(rel="results_tf", alias="r"):
    return {"Node Type": "Seq Scan", "Relation Name": rel, "Alias": alias,
            "Parent Relationship": "Outer"}


def _join(outer, inner, jt="Left", unique=True, typ="Nested Loop", **kw):
    outer = dict(outer, **{"Parent Relationship": "Outer"})
    inner = dict(inner, **{"Parent Relationship": "Inner"})
    return dict({"Node Type": typ, "Join Type": jt, "Inner Unique": unique,
                 "Plans": [outer, inner]}, **kw)


_IDX = {"Node Type": "Index Scan", "Relation Name": "meets_tf", "Alias": "m",
        "Index Cond": "((m.div_id = r.div_id) AND (m.event_id = r.event_id))"}


def _hash(rows, width=40, rel="tmp_pack_meet_class", alias="mc"):
    return {"Node Type": "Hash", "Plan Rows": rows, "Plan Width": width,
            "Plans": [{"Node Type": "Seq Scan", "Relation Name": rel,
                       "Alias": alias, "Schema": "pg_temp_3",
                       "Parent Relationship": "Outer"}]}


def test_the_plan_proof():
    hb = 1 << 30
    ok = _join(_join(_scan(), _IDX), {"Node Type": "Seq Scan",
                                     "Relation Name": "wheelchair_person",
                                     "Alias": "wc"}, jt="Anti", unique=False)
    assert D._planSpineOrdered(ok, "results_tf", hb)
    # r on the inner side, another table driving: no
    assert not D._planSpineOrdered(_join(_scan("meets_tf", "m"), _scan()),
                                   "results_tf", hb)
    # a left join that may fan out, with nothing to count it: no
    assert not D._planSpineOrdered(_join(_scan(), _IDX, unique=False),
                                   "results_tf", hb)
    # a hash that could spill into batches (which reorder the outer rows): no
    big = _join(_scan(), _hash(10_000_000, 200), typ="Hash Join",
                **{"Hash Cond": "(COALESCE(m.meet_name, ''::text) = mc.name)"})
    assert not D._planSpineOrdered(big, "results_tf", hb)
    # an unindexed hash inner is proved unique by counting its key
    small = _join(_scan(), _hash(50_000), typ="Hash Join", unique=False,
                  **{"Hash Cond": "(COALESCE(m.meet_name, ''::text) = mc.name)"})

    class Cur:
        def __init__(self, answer):
            self.answer, self.sql = answer, []

        def execute(self, q):
            self.sql.append(q)

        def fetchone(self):
            return (self.answer,)
    yes, no = Cur(True), Cur(False)
    assert D._planSpineOrdered(small, "results_tf", hb, yes)
    assert yes.sql == ['SELECT count("name") = count(DISTINCT "name") FROM pg_temp_3.tmp_pack_meet_class']
    assert not D._planSpineOrdered(small, "results_tf", hb, no)
    # a chunk must read r by a Tid Range Scan (blocks in order, never a
    # synchronized scan); the whole query by the cursor's Seq Scan
    tid = _join(dict(_scan(), **{"Node Type": "Tid Range Scan"}), _IDX)
    assert D._planSpineOrdered(tid, "results_tf", hb, leaf="Tid Range Scan")
    assert not D._planSpineOrdered(_join(_scan(), _IDX), "results_tf", hb,
                                   leaf="Tid Range Scan")
    # a sort or anything else on the spine: no
    assert not D._planSpineOrdered({"Node Type": "Sort", "Plans": [_scan()]},
                                   "results_tf", hb)


def test_a_repeated_row_in_the_ordered_stream_is_refused():
    import codecs
    dec = codecs.getdecoder("utf-8")
    codes = [20, D._COPY_CTID]
    last = [""]
    assert D._copyParse([b"1\t(0,1)", b"2\t(0,2)"], codes, dec, last) == [(1,), (2,)]
    with pytest.raises(RuntimeError, match="XCP_PACK_COPY=0"):
        D._copyParse([b"3\t(0,2)"], codes, dec, last)
