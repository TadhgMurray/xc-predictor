"""Athlete page: on one day the final sits above the prelim (owner, 2026-09-28)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as A                                               # noqa: E402


def test_round_order_reads_the_column_then_the_name():
    ro = A.round_order
    assert ro({"round": "Finals"}) == 4 and ro({"round": "f"}) == 4
    assert ro({"round": "Prelims"}) == 0 and ro({"round": "p"}) == 0
    assert ro({"round": "2"}) == 0, "a numbered heat"
    assert ro({"round": "Semifinal"}) == 2 and ro({"round": "q"}) == 1
    assert ro({"event": "Men's 1600 Meters Prelims"}) == 0
    assert ro({"event": "400m Semi-Final"}) == 2
    assert ro({"event": "1600m Finals"}) == 4
    assert ro({"event": "1600m"}) == 3


def test_same_day_final_on_top():
    races = [
        {"date": "2026-05-16", "sport": "TF", "event": "1600m", "round": "Prelims", "id": "p"},
        {"date": "2026-05-16", "sport": "TF", "event": "1600m", "round": "Finals", "id": "f"},
        {"date": "2026-05-17", "sport": "TF", "event": "800m", "round": None, "id": "next"},
        {"date": "2026-05-09", "sport": "TF", "event": "400m Semis", "round": None, "id": "s"},
        {"date": "2026-05-09", "sport": "TF", "event": "400m Final", "round": None, "id": "f2"},
        {"date": "2026-05-09", "sport": "TF", "event": "400m Heats", "round": None, "id": "h"},
    ]
    assert [r["id"] for r in A.dedupe_races(races)] == ["next", "f", "p", "f2", "s", "h"]


def test_date_shapes_do_not_beat_the_round():
    # the prelim stored with a time on its date, the final without
    races = [
        {"date": "2025-06-11", "sport": "TF", "event": "1500m", "round": "Finals", "id": "f"},
        {"date": "2025-06-11T00:00:00", "sport": "TF", "event": "1500m", "round": "Prelims", "id": "p"},
    ]
    assert [r["id"] for r in A.dedupe_races(races)] == ["f", "p"]
