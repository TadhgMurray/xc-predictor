"""scripts/field_audit.py: the finishers we missed, split by why. No
database.

    python -m pytest -q tests/test_field_audit.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import field_audit as F                                      # noqa: E402


def test_each_missed_finisher_gets_one_cause():
    fc = {"runners": [{"person_id": 1, "school": "A"}, {"person_id": 2, "school": "A"},
                      {"person_id": 3, "school": "B"}, {"person_id": 9, "school": "B"}]}
    fin = [{"person_id": 1, "school": "A"}, {"person_id": 2, "school": "a "},
           {"person_id": 4, "school": "A"},                 # A ran three, we had two
           {"person_id": 5, "school": "B"},                 # B ran two, we had two: 5 not 9
           {"person_id": 3, "school": "B"},
           {"person_id": 6, "school": "C"},                 # C was not in our field
           {"person_id": 7, "school": None}]
    got, prec = F.auditRace(fc, fin)
    assert got == {"listed": 3, "school not in our field": 1, "team ran more than we had": 1,
                   "the wrong runners": 1, "no school": 1}
    assert prec == {"listed_n": 4, "listed_ran": 3}
