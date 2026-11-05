"""One meet, two feeds, one row on a school's Races tab (owner, 2026-11-05:
NYU's 2026 UAA Championships listed twice)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import school as S                                            # noqa: E402


def test_the_two_feeds_copies_fold_into_the_bigger():
    rows = [{"meet_id": 28709, "date": "2026-10-31", "meet_name": "2026 UAA Cross Country Championships",
             "runners": 18, "divisions": 2, "best_rating": 147.7, "avg_rating": 137.8,
             "hs_best_rating": 150.0, "hs_avg_rating": 140.0, "distances": [6000, 8000]},
            {"meet_id": 289628, "date": "2026-10-31", "meet_name": "UAA Cross Country Championships",
             "runners": 1, "divisions": 1, "best_rating": 130.3, "avg_rating": 130.3,
             "hs_best_rating": 132.0, "hs_avg_rating": 132.0, "distances": [6000]},
            {"meet_id": 5, "date": "2026-10-31", "meet_name": "Al Carius and Paul Olsen Open",
             "runners": 3, "divisions": 1, "distances": [8000]},
            {"meet_id": 6, "date": "2026-10-16", "meet_name": "SUNY Geneseo Mike Woods Invitational",
             "runners": 10, "divisions": 1, "distances": [8000]}]
    out = S.foldFeedCopies(rows)
    assert [r["meet_id"] for r in out] == [28709, 5, 6]
    uaa = out[0]
    assert uaa["runners"] == 19 and uaa["best_rating"] == 147.7 and uaa["distances"] == [6000, 8000]
    assert abs(uaa["avg_rating"] - round((137.8 * 18 + 130.3) / 19, 1)) < 1e-9
