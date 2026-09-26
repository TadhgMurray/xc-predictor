"""app.fillUnknownNames: a row named Unknown takes its person's name from
their other rows (owner, 2026-09-26: Unknown on the race, John Rivera one
click later)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


class Cur:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append(params)

    def fetchall(self):
        return [{"person_id": 7, "name": "John Rivera"}]


def test_unknown_rows_are_named_and_others_untouched():
    import app as A
    rows = [{"person_id": 7, "name": "Unknown"},
            {"person_id": 8, "name": "Mariano García"},
            {"person_id": None, "name": "Unknown"}]
    cur = Cur()
    A.fillUnknownNames(cur, rows, "name")
    assert [r["name"] for r in rows] == ["John Rivera", "Mariano García", "Unknown"]
    assert cur.calls == [{"p": [7]}]


def test_no_query_when_nobody_is_unknown():
    import app as A
    cur = Cur()
    A.fillUnknownNames(cur, [{"person_id": 1, "name": "A"}], "name")
    assert cur.calls == []
