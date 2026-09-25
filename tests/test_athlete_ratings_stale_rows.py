"""A merged solve must replace athlete_ratings whole, per-sport leftovers too.

⚠ 2026-09-25: a four-year high schooler's page headed him 93.4 on the HS
  view. His races were all rated hs_m, but athlete_ratings still held
  'pro_m|TF' (124.65, 47 races) and 'pro_m|XC' from an older per-sport
  engine. The merged solve cleared only bare-pool rows, so those were never
  refreshed or removed, and the page's fallback picked the row with the
  most races.

    python -m pytest -q tests/test_athlete_ratings_stale_rows.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import speed_ratings_db as S                                    # noqa: E402


class _Cur:
    def __init__(self, log):
        self.log, self.rowcount = log, 0

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))
        self.rowcount = 2

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn(_Cur):
    def cursor(self):
        return _Cur(self.log)

    def commit(self):
        self.log.append("COMMIT")


def _run(monkeypatch, ratings):
    log = []
    monkeypatch.setattr(S, "getConn", lambda: _Conn(log))
    monkeypatch.setattr(S, "_copyInto", lambda cur, t, cols, rows:
                        log.append(f"COPY {t} {len(list(rows))}") or 0)
    S.saveAthleteRatings(ratings)
    return log


def test_a_merged_solve_clears_the_per_sport_leftovers(monkeypatch):
    log = _run(monkeypatch, {(29603086, "hs_m"): {"speed_rating": 130.9,
                                                  "n_races": 18}})
    deletes = [s for s in log if s.startswith("DELETE FROM athlete_ratings")]
    assert any("LIKE '%|%'" in s and "NOT LIKE" not in s for s in deletes), log
    assert any("NOT LIKE '%|%'" in s for s in deletes), log
    assert log.index(deletes[-1]) < next(i for i, s in enumerate(log)
                                         if s.startswith("COPY"))


def test_a_per_sport_run_still_clears_only_its_own_sport(monkeypatch):
    log = _run(monkeypatch, {(1, "hs_m|XC"): {"speed_rating": 100.0,
                                              "n_races": 3}})
    deletes = [s for s in log if s.startswith("DELETE FROM athlete_ratings")]
    assert deletes and all("NOT LIKE" not in s and "WHERE pool LIKE %s" in s
                           for s in deletes), log
