"""normalize_distance.parseEventShort reads "10-km" and "10,000" (2026-10-04:
WashU's "10-km" and "Men's 10,000 Meters" both read as 10 metres)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
from normalize_distance import parseEventShort as P            # noqa: E402


def test_kilometres_and_thousands():
    for ev, m in {"10-km": 10000, "10 km": 10000, "10K": 10000, "8 kilometers": 8000,
                  "Men's 10,000 Meters": 10000, "10,000m": 10000, "5,000 Meters": 5000,
                  "10000m": 10000, "3200m Final": 3200, "1600m": 1600}.items():
        assert abs(P(ev)["meters"] - m) < 0.01, (ev, P(ev))


def test_not_a_distance_stays_so():
    assert P("4x400")["meters"] is None
    assert abs(P("2 Mile")["meters"] - 3218.688) < 0.01
