"""A batch detach (person_pins --detach) moves another athlete's races off a
page onto ONE fresh person, not one person per row (owner, 2026-10-18: a
North Carolina Eli Hernandez's 2022-23 track on the California one's page).

    python -m pytest -q tests/test_person_pins_batch.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import person_pins as PP                                         # noqa: E402


class Cur:
    def __init__(self, rows):
        self.rows, self.out, self.inserted, self.fresh = rows, [], [], 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT result_id, date, school, meet_id"):
            self.out = list(self.rows)
        elif "FOR UPDATE" in s:
            self.out = [(32781879,)]
        elif s.startswith("INSERT INTO result_detach"):
            self.inserted.append(params)
            self.out, self.rowcount = [], 1
        else:
            self.out, self.rowcount = [], 0

    def fetchall(self):
        return self.out

    def fetchone(self):
        return self.out[0] if self.out else None


def test_one_fresh_person_for_the_whole_batch(monkeypatch):
    ids = iter([4_100_000_001, 4_100_000_002, 4_100_000_003])
    monkeypatch.setattr(PP, "nextFreshId", lambda cur: next(ids))
    monkeypatch.setattr(PP, "ensure", lambda cur: None)
    monkeypatch.setattr(PP, "_log", lambda *a, **k: None)
    monkeypatch.setattr(PP, "applyDetaches", lambda cur, only=None: {})
    rows = [(11, "2022-05-07", "Charlotte Flights", 1), (12, "2022-05-28", "Durham Striders", 2),
            (13, "2023-07-30", "USATF", 3)]
    cur = Cur(rows)
    got, to = PP.detachRows(cur, 32781879, "TF", until="2023-12-31", write=True)
    assert len(got) == 3 and to == 4_100_000_001
    assert {p[3] for p in cur.inserted} == {4_100_000_001}     # every row, one person


def test_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(PP, "nextFreshId", lambda cur: 1 / 0)
    cur = Cur([(11, "2022-05-07", "Charlotte Flights", 1)])
    got, to = PP.detachRows(cur, 32781879, "TF", until="2023-12-31")
    assert len(got) == 1 and to is None and cur.inserted == []
