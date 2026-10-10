# Project: xc-predictor / tests
# File:    test_sweep_20261010_model.py
# Purpose: the 2026-10-10 sweep's model-side fixes, one test per finding:
#          (1) grade_fix / pro_athlete_season joined on the academic year,
#          (2) the stored pool after anchor_repair, (3) one val split,
#          (4) no same-day race in a history, (5) the target row's season
#          facts, (7) dropout keeps the last race, (8) no future years,
#          (14) the backtest never samples a twin copy.
#
#     python -m pytest -q tests/test_sweep_20261010_model.py
import os
import random
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "model"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "corrections" not in sys.modules:          # see test_horizon_long_twin
    import types
    _c = types.ModuleType("corrections")
    _c.distanceOverrideSQL = lambda *a, **k: ("", "")
    sys.modules["corrections"] = _c

import feature_extraction as F                                 # noqa: E402
from season_year import seasonYearSqlInt                       # noqa: E402


# ------------------------------------------------------------------ #
# 1. THE SEASON KEY                                                  #
# ------------------------------------------------------------------ #

def _joinLine(sql, alias):
    return [ln.strip() for ln in sql.splitlines()
            if ln.strip().startswith(f"AND {alias}.season =")]


def test_grade_fix_and_pro_season_join_on_the_academic_year():
    want = seasonYearSqlInt(None, "r.date")
    for sql in (F._XC_SQL, F._TF_SQL, F.personResultsSql("XC"),
                F.personResultsSql("TF")):
        for alias in ("gu", "pas"):
            lines = _joinLine(sql, alias)
            assert lines == [f"AND {alias}.season = {want}"], (alias, lines)
    # none of the old clocks survives anywhere in the corpus SQL
    for sql in (F._XC_SQL, F._TF_SQL):
        assert "<= '02'" not in sql and ">= '10'" not in sql
        assert "pas.season = substring" not in sql


# ------------------------------------------------------------------ #
# 8. NO FUTURE DATES                                                 #
# ------------------------------------------------------------------ #

def test_both_corpus_queries_refuse_a_future_date():
    for sql in (F._XC_SQL, F._TF_SQL, F.personResultsSql("XC"),
                F.personResultsSql("TF")):
        assert F._DATE_NOT_FUTURE in sql
    # evaluated by Postgres, not frozen at import (the site is long-running)
    assert "CURRENT_DATE" in F._DATE_NOT_FUTURE


def test_compute_stats_ignores_a_year_after_the_extraction():
    import torch
    import train as T
    n = 3
    ctx = torch.zeros(n, F.CONTEXT_FEATURES)
    ctx[:, T.CONTEXT_YEAR_INDEX] = torch.tensor([2025.0, 2026.0, 2222.0])
    seqs = torch.ones(n, T.SEQUENCE_FEATURES) * 900.0
    chunk = {"targets": torch.tensor([900.0, 905.0, 910.0]),
             "sequences": seqs, "offsets": torch.tensor([0, 1, 2, 3]),
             "context": ctx}

    class _DS:
        num_chunks = 1
        chunk_size = n
        base_index = 0
        extracted_on = "2026-10-10"

        def _loadChunk(self, c):
            return chunk

    stats = T.computeStats(_DS(), torch.ones(n, dtype=torch.bool))
    assert stats["max_year"] == 2026.0, stats["max_year"]
    # a metadata.pkl from before the key: today's year bounds it
    assert T._yearCeiling(None) >= 2026.0
    assert T._yearCeiling("2026-12-31") == 2027.0      # the date-line day


# ------------------------------------------------------------------ #
# 2. THE POOL THE STORED VALUE IS ON                                 #
# ------------------------------------------------------------------ #

def test_stored_pool_prefers_the_rated_pool(monkeypatch):
    monkeypatch.setattr(F, "rowPool", lambda row: "hs_m")
    # same pool: nothing to decide
    assert F.storedPool({"rating_pool": "hs_m|XC"}) == "hs_m"
    # unrated: the backfill's pool, as before
    assert F.storedPool({}) == "hs_m"
    # rated elsewhere and uncheckable (no time): 05b's steady state
    assert F.storedPool({"rating_pool": "college_m|XC"}) == "college_m"
    # a pro pool sits on its college twin's anchor
    assert F.storedPool({"rating_pool": "pro_m|XC"}) == "college_m"


def test_stored_pool_lets_the_row_decide_when_the_pools_disagree(monkeypatch):
    import anchor_check
    monkeypatch.setattr(F, "rowPool", lambda row: "hs_m")
    # the stored value reproduces hs_m within 1% and college_m 40% off:
    # a row the nightly rated but 05b has not yet repaired
    offs = {"hs_m": 1.01, "college_m": 0.60}
    monkeypatch.setattr(anchor_check, "mismatch",
                        lambda t, d, nt, p, sp=None: (False, 1.0, offs[p]))
    row = {"rating_pool": "college_m|XC", "time_seconds": 900.0,
           "distance_meters": 5000.0, "normalized_time": 900.0, "is_xc": True}
    assert F.storedPool(row) == "hs_m"
    # ...and once 05b has moved it, the rated pool
    offs.update({"hs_m": 0.62, "college_m": 1.0})
    assert F.storedPool(row) == "college_m"


# ------------------------------------------------------------------ #
# 3. ONE VALIDATION SPLIT                                            #
# ------------------------------------------------------------------ #

def test_backtest_val_set_is_extractions_val_set():
    import backtest_predictions as B
    pids = range(1, 5001)
    ext = {p for p in pids if F._isValAthlete(F._identity({"person_id": p}))}
    bt = {p for p in pids if B._isValAthlete(p)}
    assert ext == bt
    assert 0 < len(ext) < len(pids)


# ------------------------------------------------------------------ #
# 4. NO SAME-DAY RACE IN A HISTORY                                   #
# ------------------------------------------------------------------ #

def test_a_same_day_race_never_sits_in_the_history(monkeypatch):
    monkeypatch.setattr(F, "_baseVectors",
                        lambda rows, enc: [[0.0, 0.0, 0.0] for _ in rows])
    rows = [{"date": "2026-04-01", "normalized_time": 1000.0},
            {"date": "2026-05-02", "normalized_time": 990.0},   # prelim
            {"date": "2026-05-02", "normalized_time": 985.0},   # final
            {"date": "2026-05-09", "normalized_time": 980.0}]
    ex = F.buildAthleteExamples(rows, {}, rng=random.Random(0))
    plain = [e for e in F.buildAthleteExamples(rows, {}, rng=None)]
    for e in plain:
        td = e["target_result"]["date"]
        assert all(p["date"] < td for p in e["prior_results"]), e
        assert len(e["sequence"]) == len(e["prior_results"])
        assert all(v[2] > 0 for v in e["sequence"])      # no days_ago 0
    # the final's history is the April race only, not its own prelim
    final = [e for e in plain if e["target_result"] is rows[2]][0]
    assert final["prior_results"] == rows[:1]
    # every twin takes the same strict-before prefix
    for e in ex:
        td = e["target_result"]["date"]
        assert all(p["date"] < td for p in e["prior_results"])
    # a first day with two races yields no example for either
    two = [{"date": "2026-04-01", "normalized_time": 1000.0},
           {"date": "2026-04-01", "normalized_time": 999.0}]
    assert F.buildAthleteExamples(two, {}, rng=None) == []


# ------------------------------------------------------------------ #
# 7. DROPOUT KEEPS THE LAST RACE                                     #
# ------------------------------------------------------------------ #

def test_dropout_never_deletes_the_last_prior_race():
    hist = [{"date": f"2025-{m:02d}-01", "normalized_time": 1000.0}
            for m in range(1, 13)]
    seq = [[0.0, 0.0, float(i)] for i in range(len(hist))]
    target = {"date": "2025-12-20", "normalized_time": 950.0}
    rng = random.Random(3)
    twins = [t for t in (F._dropoutTwin(hist, target, seq, rng)
                         for _ in range(500)) if t]
    assert twins
    for t in twins:
        assert t["prior_results"][-1] is hist[-1]
        assert t["sequence"][-1][2] == float(len(hist) - 1)
        assert "hidden_days" not in t or t["hidden_days"] == 0.0
    assert F._dropoutTwin([], target, [], rng) is None


# ------------------------------------------------------------------ #
# 14. THE BACKTEST NEVER SAMPLES A TWIN COPY                         #
# ------------------------------------------------------------------ #

def test_backtest_anti_joins_result_twin_in_both_queries():
    src = open(os.path.join(_ROOT, "scripts", "backtest_predictions.py"),
               encoding="utf-8").read()
    assert len(re.findall(r"NOT EXISTS \(SELECT 1 FROM result_twin x", src)) == 2
    # no copy of the val hash left to drift
    assert "zlib.crc32" not in src
