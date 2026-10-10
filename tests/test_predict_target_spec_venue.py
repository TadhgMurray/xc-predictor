"""
predict._targetSpec (sweep 2026-10-10):
  A2  the target's distance goes through the hand-verified overrides
      (corrections.distanceOverrideSQL), in the SELECT and the difficulty
      join, for anet XC, tfrrs XC and TF alike;
  A4  XCP_PREDICT_VENUE_DIFFICULTY=tf: the track venue cell and geometry;
  A5  XCP_PREDICT_VENUE_DIFFICULTY=xc: a course with no cell converts at the
      median race (difficulty None), not an explicit 0.0.
No database: a stub cursor records the SQL it is handed.
"""
import os
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import predict                                                  # noqa: E402

import pytest                                                   # noqa: E402


def _stubOverride(alias, sport, stored_expr=None):
    # a recognisable join keyed off the alias, and a clamped prefix -- the
    # shape the generated helper returns (prefix ends in a comma)
    return (f"LEFT JOIN ov_{sport.lower()} dov ON dov.meet_id = {alias}.meet_id"
            f" AND dov.div_id = {alias}.div_id\n",
            f"LEAST(dov.distance, {stored_expr}),")


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    fx = types.ModuleType("feature_extraction")
    fx.escapeLiteralPercent = lambda s: s.replace("%", "%%")
    corr = types.ModuleType("corrections")
    corr.distanceOverrideSQL = _stubOverride
    monkeypatch.setitem(sys.modules, "feature_extraction", fx)
    monkeypatch.setitem(sys.modules, "corrections", corr)
    monkeypatch.setattr(predict, "_SPEC_OVERRIDE_CACHE", {})
    monkeypatch.delenv("XCP_PREDICT_VENUE_DIFFICULTY", raising=False)


class _Cur:
    def __init__(self, row):
        self.row, self.sql, self.params = row, [], []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.params.append(params)
        if params is not None:
            sql % params            # every placeholder must be fillable

    def fetchone(self):
        return self.row


def _spec(cur, **target):
    target.setdefault("mode", "rerun_exact")
    target.setdefault("meet_id", 123)
    target.setdefault("date", "2026-09-01")
    return predict._targetSpec(cur, target)


# ---------------------------------------------------------------- A2
def test_anet_xc_reads_override_in_select_and_difficulty_join():
    cur = _Cur({"distance_meters": 2700.0, "date": "2026-09-01"})
    _spec(cur, sport="XC", source="anet", div_id="9")
    sql = cur.sql[0]
    assert "ov_xc dov ON dov.meet_id = m.meet_id AND dov.div_id = m.div_id" in sql
    assert sql.count("COALESCE(LEAST(dov.distance, m.distance), m.distance)") == 2
    # the join is before the difficulty join that reads it
    assert sql.index("ov_xc dov") < sql.index("LEFT JOIN course_difficulties")


def test_tfrrs_xc_names_its_division_for_the_override():
    cur = _Cur({"distance_meters": 5000.0, "date": "2026-09-01"})
    _spec(cur, sport="XC", source="tfrrs", div_id="4")
    sql = cur.sql[0]
    assert "dov.meet_id = ovk.meet_id AND dov.div_id = ovk.div_id" in sql
    assert sql.count("COALESCE(LEAST(dov.distance, d.dist), d.dist)") == 2
    assert ") ovk" in sql and "AS div_key" in sql
    assert cur.params[0]["divtext"] == "4"


def test_tf_reads_override():
    cur = _Cur({"distance_meters": 1600.0, "date": "2026-04-01"})
    _spec(cur, sport="TF", source="anet")
    sql = cur.sql[0]
    assert "ov_tf dov ON dov.meet_id = m.meet_id" in sql
    # ★ 2026-10-10: the row with a real distance first (ORDER BY, not a
    #   filter -- a row with none still answers, its name carries it)
    assert "ORDER BY (COALESCE(LEAST(dov.distance, m.distance_meters), " \
           "m.distance_meters) > 0) DESC" in sql


def test_no_corrections_module_is_the_old_query(monkeypatch):
    monkeypatch.setitem(sys.modules, "corrections", None)   # ImportError
    cur = _Cur({"distance_meters": 5000.0})
    _spec(cur, sport="XC", source="anet")
    assert "COALESCE( m.distance) AS distance_meters" in cur.sql[0]


def test_literal_percent_in_generated_sql_is_escaped(monkeypatch):
    sys.modules["corrections"].distanceOverrideSQL = (
        lambda a, s, stored_expr=None: ("-- 5% of rows\n", ""))
    cur = _Cur({"distance_meters": 5000.0})
    _spec(cur, sport="XC", source="anet")          # _Cur formats: no error
    assert "5%% of rows" in cur.sql[0]


# ---------------------------------------------------------------- A4
def test_tf_flag_off_is_zero_and_no_geometry():
    cur = _Cur({"distance_meters": 1600.0, "date": "2026-04-01",
                "course_difficulty": 0.0})
    spec = _spec(cur, sport="TF", source="anet")
    assert "0.0 AS course_difficulty" in cur.sql[0]
    assert "tfrrs_meet_geometry" not in cur.sql[0]
    ctx = predict._venueContext(spec, {})
    assert ctx["difficulty"] == 0.0 and "track_length" not in ctx


def test_tf_flag_on_reads_cell_and_geometry(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_VENUE_DIFFICULTY", "tf")
    cur = _Cur({"distance_meters": 1600.0, "date": "2026-02-01",
                "course_difficulty": -0.03, "venue_cell": -0.03,
                "location_id": 77, "is_indoor": 1,
                "track_length": 200, "track_type": "banked"})
    spec = _spec(cur, sport="TF", source="anet")
    sql = cur.sql[0]
    assert "'TF:loc:' || m.location_id" in sql
    assert "m.location_id <> 0" in sql
    assert "COALESCE(m.track_length, tg.track_length)" in sql
    ctx = predict._venueContext(spec, {})
    assert ctx["difficulty"] == -0.03
    assert ctx["track_length"] == 200 and ctx["track_type"] == "banked"


def test_tf_flag_on_no_cell_is_average_outdoor(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_VENUE_DIFFICULTY", "1")
    spec = {"sport": "TF", "venue_cell": None, "course_difficulty": 0.0,
            "location_id": 5}
    assert predict._contextDifficulty(spec) == (0.0, None)


def test_xc_flag_does_not_turn_on_tf(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_VENUE_DIFFICULTY", "xc")
    assert predict._venueDifficultyOn("XC")
    assert not predict._venueDifficultyOn("TF")


# ---------------------------------------------------------------- A5
def test_xc_no_cell_flag_off_is_explicit_zero():
    spec = {"sport": "XC", "venue_cell": None, "course_difficulty": 0.0,
            "canonical_id": 12}
    assert predict._contextDifficulty(spec) == (0.0, 12)


def test_xc_no_cell_flag_on_is_median(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_VENUE_DIFFICULTY", "xc")
    spec = {"sport": "XC", "venue_cell": None, "course_difficulty": 0.0,
            "canonical_id": 12}
    # None difficulty and no canonical id: conversions answers "no venue"
    # (the median race) without a lookup
    assert predict._contextDifficulty(spec) == (None, None)


def test_xc_cell_flag_on_is_the_cell(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_VENUE_DIFFICULTY", "all")
    spec = {"sport": "XC", "venue_cell": 0.021, "course_difficulty": 0.021,
            "canonical_id": 12}
    assert predict._contextDifficulty(spec) == (0.021, 12)


def test_xc_model_feature_stays_coalesced(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_VENUE_DIFFICULTY", "xc")
    cur = _Cur({"distance_meters": 5000.0, "course_difficulty": 0.0,
                "venue_cell": None, "date": "2026-09-01"})
    spec = _spec(cur, sport="XC", source="anet")
    assert "COALESCE(cd.difficulty, 0.0) AS course_difficulty" in cur.sql[0]
    assert spec["course_difficulty"] == 0.0


def test_swapped_course_without_cell_clears_the_meets_cell():
    spec = {"sport": "XC", "venue_cell": 0.05, "distance_meters": 5000.0}
    cur = _Cur({"course_name": "Other Park", "course_difficulty": 0.0,
                "venue_cell": None})
    predict._applyCourse(cur, spec, "Other Park", "XC")
    assert spec["venue_cell"] is None
