"""A team prediction of a meet with no results yet (2026-10-05: strptime()
argument 1 must be str, not None): the target spec always carries a date."""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", "model"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import pytest  # noqa: E402

predict = pytest.importorskip("predict")


class _Cur:
    def __init__(self, row):
        self.row, self.sql = row, []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        return self.row


def _row(date):
    return {"course_name": "Park A", "distance_meters": 5000.0, "gps_lat": 1.0,
            "gps_long": 2.0, "altitude_meters": None, "canonical_id": 7,
            "course_difficulty": 0.03, "date": date}


def test_the_query_falls_back_to_the_meets_own_date():
    cur = _Cur(_row("2026-10-24"))
    spec = predict._targetSpec(cur, {"mode": "meet", "meet_id": 1, "sport": "XC"})
    assert "m.meet_date" in cur.sql[0]
    assert spec["date"] == "2026-10-24"


def test_no_date_anywhere_takes_the_pages_then_today():
    spec = predict._targetSpec(_Cur(_row(None)),
                               {"mode": "meet", "meet_id": 1, "sport": "XC",
                                "date": "2026-11-07"})
    assert spec["date"] == "2026-11-07"
    spec = predict._targetSpec(_Cur(_row(None)), {"mode": "meet", "meet_id": 1, "sport": "XC"})
    assert spec["date"] == datetime.date.today().isoformat()
    spec = predict._targetSpec(_Cur(None), {"mode": "meet", "meet_id": 1, "sport": "TF"})
    assert spec["date"] == datetime.date.today().isoformat()
