"""A track relay row is a team, not "Unknown" (2026-10-02): the race page
names the squad and lists its runners from the meet's relayLegs blob, in leg
order, names from the blob or from `athletes`. No database: a fake cursor."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "racecast", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                  # noqa: E402

app = pytest.importorskip("app")


class Cur:
    def __init__(self, blob, athletes):
        self.blob, self.athletes, self.rows = blob, athletes, []

    def execute(self, sql, params=None):
        if "meet_extras" in sql:
            self.rows = [{"relay_legs_json": self.blob}]
        else:
            ids = set(params["ids"])
            self.rows = [a for a in self.athletes if a["athlete_id"] in ids]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def test_legs_in_order_with_names_from_blob_or_athletes():
    blob = [{"IDResult": 20, "AthleteID": 5, "Leg": 2},
            {"IDResult": 20, "AthleteID": 1, "Leg": 1},
            {"IDResult": 20, "AthleteID": 9, "FirstName": "Cy", "LastName": "Ono", "Leg": 3},
            {"IDResult": 21, "AthleteID": 7}]
    athletes = [{"athlete_id": 1, "person_id": 11, "name": "Ann Lee"},
                {"athlete_id": 5, "person_id": 5, "name": "Bo Ray"}]
    legs = app._tf_relay_legs(Cur(blob, athletes), 200)
    assert legs[20] == [{"person_id": 11, "name": "Ann Lee"},
                        {"person_id": 5, "name": "Bo Ray"},
                        {"person_id": 9, "name": "Cy Ono"}]
    # a runner nobody can name stays nameless (the page leaves it out)
    assert legs[21] == [{"person_id": 7, "name": None}]


def test_no_blob_no_legs():
    assert app._tf_relay_legs(Cur(None, []), 200) == {}


def test_the_race_query_reads_is_relay():
    src = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    body = src[src.index("def get_tf_race_results"):src.index("def _field_gender")]
    assert "is_relay" in body
