"""One race seen by both feeds, its time posted to the hundredth by one and
rounded up to the tenth by the other, is one row on the athlete page (owner,
2026-11-03, /athlete/29320769: the Big Ten championship twice)."""
import os
import sys

os.environ.setdefault("XCP_DB_QUIET", "1")
for k, v in (("NAME", "x"), ("USER", "x"), ("PASSWORD", "x"),
             ("HOST", "127.0.0.1"), ("PORT", "5432")):
    os.environ.setdefault(f"XCP_DB_{k}", v)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import app   # noqa: E402


def _row(src, t, rating=None, day="2026-10-30"):
    return {"sport": "XC", "source": src, "time_raw": t, "date": day,
            "speed_rating": rating, "result_id": hash((src, t))}


def test_hundredths_and_the_rounded_tenth_fold():
    out = app._merge_cross_source([_row("tfrrs", 1384.19), _row("anet", 1384.2, 148.7)])
    assert len(out) == 1


def test_same_feed_or_a_tenth_apart_stay_two():
    assert len(app._merge_cross_source([_row("anet", 1384.2), _row("anet", 1384.2)])) == 2
    assert len(app._merge_cross_source([_row("tfrrs", 1384.21), _row("anet", 1384.2)])) == 2
    assert len(app._merge_cross_source([_row("tfrrs", 1384.19),
                                        _row("anet", 1384.2, day="2026-10-20")])) == 2
