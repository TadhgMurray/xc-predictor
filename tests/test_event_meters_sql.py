"""speed_ratings_db._eventMetersSql agrees with event_parse on yard events
(owner, 2026-10-13: a 1:57.3 880-yard dual meet rated 150.6 on the CA
all-time board -- normalized at 804.67 m, packed at 880 m). Needs a scratch
Postgres (XCP_TWIN_TEST_DSN), like the twin-rule tests."""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "tests"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import _env  # noqa: E402,F401

import speed_ratings_db as S  # noqa: E402

CASES = {"880 Yards": 804.672, "880y": 804.672, "880yd": 804.672, "880": 804.672,
         "660 Yard Run": 603.504, "1320": 1207.008, "1760 Yards": 1609.344,
         "1000 Yards": 914.4, "600 Yards": 548.64,
         "800 Meters": 800.0, "800m": 800.0, "600m": 600.0, "1000m": 1000.0,
         "400 Yards": 400.0,                     # no 400 on the yard schedule
         "880 Meters": 880.0, "Mile": 1609.34, "2 Mile": 3218.68,
         "5000m": 5000.0, "8k": 8000.0, "3000m Steeplechase": None}


def test_yard_events_in_sql():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    with psycopg2.connect(dsn) as c, c.cursor() as cur:
        for ev, want in CASES.items():
            # no parameters: the expression carries LIKE '%mile%'
            lit = "'" + ev.replace("'", "''") + "'"
            cur.execute(f"SELECT {S._eventMetersSql('r')} FROM (SELECT {lit}::text AS event_short) r")
            got = cur.fetchone()[0]
            if want is None:
                assert got is None, ev
            else:
                assert got == pytest.approx(want, abs=0.05), (ev, got)
