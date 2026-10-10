"""
_score: a team whose entered runner the model could not place must not take
scoring places it can never score with, and tied teams break on the sixth.
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import predict                                                  # noqa: E402


def _field(rows):
    field, preds = [], []
    for i, (school, entered, secs) in enumerate(rows):
        field.append({"person_id": i, "school": school, "entered": entered})
        preds.append({"seconds": secs})
    return field, preds


def test_unplaced_runner_does_not_make_a_scoring_team():
    rows = []
    # A entered five, one unpredictable; A's four lead every pack
    for k in range(4):
        rows.append(("A", 5, 900 + 30 * k))
    rows.append(("A", 5, None))
    for k in range(5):
        rows.append(("B", 5, 910 + 30 * k))
        rows.append(("C", 5, 920 + 30 * k))
    teams, _ = predict._score(*_field(rows))
    got = {t["team"]: t["score"] for t in teams}
    assert got["A"] is None
    # B and C take 1..10 between them, alternating
    assert got["B"] == 1 + 3 + 5 + 7 + 9
    assert got["C"] == 2 + 4 + 6 + 8 + 10


def test_tie_breaks_on_the_sixth_runner():
    # X: 1,3,6,10,11 = 31, sixth 13; Y: 2,5,7,8,9 = 31, sixth 12 -> Y first
    order = "XYXZYXYYYXXYXZZZZ"
    rows = [(t, 6 if t != "Z" else 5, 900 + i) for i, t in enumerate(order)]
    teams, _ = predict._score(*_field(rows))
    assert teams[0]["score"] == teams[1]["score"] == 31
    assert teams[0]["team"] == "Y"
