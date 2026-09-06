"""Two step-10 / step-13c guards from the 2026-09-03 run (issues 151, 152):
the rankings builder reads only the school_unit columns the table has and
rolls back after a failed read; the search index builds meet_agg_xc and
meet_agg_tf itself. Text checks: both modules need a database to import.
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_units_read_only_existing_columns_and_roll_back():
    s = _src("racecast", "build_ranking_results.py")
    i = s.index("def _loadUnits(")
    body = s[i:s.index("\ndef ", i + 1)]
    assert "information_schema.columns" in body
    assert 'NULL::text AS' in body
    assert "conn.rollback()" in body
    assert body.index("except Exception") < body.index("conn.rollback()")


def test_search_index_builds_both_meet_aggregates():
    s = _src("racecast", "search_index.py")
    assert '"meet_agg_xc":' in s and '"meet_agg_tf":' in s
    assert "def _ensure_meet_agg(" in s
    lm = s[s.index("def _load_meets("):]
    assert lm.index("_ensure_meet_agg(conn)") < lm.index('_meet_rows("meet_agg_xc"')
    agg = s[s.index("_MEET_AGG = {"):s.index("def _ensure_meet_agg(")]
    # single-table aggregates joined small: no results-to-meets join
    assert "FROM   results_tf\n" in agg and "FROM   meets_tf\n" in agg
    assert "JOIN   meets_tf m" not in agg
    assert "RENAME TO {table}" in s


def test_golive_takes_the_advisory_lock_first():
    s = _src("engine", "speed_ratings_db.py")
    body = s[s.index("def saveResultSpeedRatings("):]
    nxt = body.find("\ndef ", 1)
    body = body if nxt < 0 else body[:nxt]      # it is the file's last def
    assert body.index("_takeGoLiveLock(conn)") < body.index("_fillStaging(conn")
    assert "pg_try_advisory_lock" in s


def test_pipeline_holds_a_lock_and_runs_the_diags_in_parallel():
    s = _src("deploy", "run_pipeline.sh")
    assert "flock -n 9" in s and ".pipeline.lock" in s
    assert "steps2 15_rowguard_diag_xc" in s
    assert 'step 15_rowguard_diag_xc' not in s


def test_fill_streams_only_priceable_rows():
    s = _src("engine", "fill_ratings.py")
    mark = s[s.index("_MARK = {"):s.index("def _sqlFor(")]
    assert mark.count("AND r.normalized_time > 0") == 2


def test_board_build_streams_the_sports_in_parallel():
    s = _src("deploy", "run_pipeline.sh")
    assert "--stage prepare" in s and "--stage finish" in s
    assert 'stepsN 10_rankings_xc_a' in s and "--stage stream --sport TF --since" in s
    assert "--until" in s, "each sport streams in two halves on a date seam (2026-09-06)"
    b = _src("racecast", "build_ranking_results.py")
    assert 'choices=["prepare", "stream", "finish"]' in b
    assert 'if stage == "prepare":' in b and 'if stage == "stream":' in b
