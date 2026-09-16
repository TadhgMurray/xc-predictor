"""The prediction's own history lookup must reach an index.

    python -m pytest -q tests/test_history_lookup_indexable.py

★ THE PREDICTIONS PAGE 504'd ON A CHAMPIONSHIP FIELD (owner, 2026-09-16).
  _historyRows asks both result tables for one field's athletes, and the
  filter was

      COALESCE(r.person_id, r.athlete_id) = ANY(%s)

  which is an EXPRESSION -- no index on either column can serve it. So every
  prediction seq-scanned `results` (54M rows) and then did it again for
  track. Measured on a 2M-row stand-in: 2,938 ms, with 1,999,789 rows
  discarded by the join filter, against 7.2 ms for the two-branch form.

⚠ IT IS THE SAME FAULT THAT MADE EXTRACTION APPEAR TO HANG, which is the
  reason this test exists rather than a comment: an expression the planner
  cannot reach an index through reads as "slow", never as "wrong", and the
  next person to write one will not know it happened twice.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_filter_is_two_indexable_branches():
    fx = read("model", "feature_extraction.py")
    i = fx.index("def personResultsSql")
    body = fx[i:fx.index("\ndef ", i + 10)]
    body = re.sub(r"#.*", "", body)          # the comment explains the old
                                             # form; the code must not use it
    assert "COALESCE(r.person_id, r.athlete_id) = ANY" not in body, \
        "the unindexable expression is back"
    assert "r.person_id = ANY(%s)" in body, body
    assert "r.person_id IS NULL AND r.athlete_id = ANY(%s)" in body, body


def test_the_caller_binds_the_ids_twice():
    """⚠ TWO PLACEHOLDERS, TWO PARAMETERS. A rewrite that adds a %s without
    a matching argument does not fail subtly -- psycopg raises -- but it
    would take the page down, so it is pinned next to the SQL."""
    fx = read("model", "feature_extraction.py")
    i = fx.index("def personResultsSql")
    body = fx[i:fx.index("\ndef ", i + 10)]
    # the comment quotes the old predicate to explain it; count the code
    body = re.sub(r"#.*", "", body)
    assert body.count("ANY(%s)") == 2, body.count("ANY(%s)")

    pred = read("racecast", "predict.py")
    j = pred.index("def _historyRows")
    call = pred[j:pred.index("\ndef ", j + 10)]
    assert "fx.MIN_NORMALIZED_TIME, ids, ids" in call, call


def test_both_id_columns_of_both_tables_are_indexed():
    """! FOUR INDEXES. The filter has two branches and there are two sports;
    the planner BitmapOrs the branches, so each column needs its own."""
    idx = read("scripts", "add_page_indexes.py")
    for table, col in (("results", "person_id"), ("results", "athlete_id"),
                       ("results_tf", "person_id"),
                       ("results_tf", "athlete_id")):
        assert re.search(rf'\("{table}",\s*"{col}"', idx), f"{table}.{col}"
