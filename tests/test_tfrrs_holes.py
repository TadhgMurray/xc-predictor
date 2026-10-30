"""TFRRS cross country ids that showed nothing are kept and re-asked below
the watermark (owner, 2026-10-30: the Mike Woods Classic, XC 28447, had
been deleted from the queue and was never asked again).

    python -m pytest -q tests/test_tfrrs_holes.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


class Cur:
    def __init__(self):
        self.sql, self.rowcount = [], 3

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return (7,)


def test_the_hole_pass_queues_the_missing_and_wakes_rested_state_4():
    import queue_meets as Q
    cur = Cur()
    assert Q.tfrrsHoles(cur, 29018, write=False, span=1500, rest_hours=20) == (7, 7)
    cur = Cur()
    assert Q.tfrrsHoles(cur, 29018, write=True, span=1500, rest_hours=20) == (3, 3)
    ins, upd = cur.sql
    assert ins[0].startswith("INSERT INTO meet_queue") and "ON CONFLICT (meet_id, sport, source) DO NOTHING" in ins[0]
    assert ins[1] == (27518, 29018)
    assert "scraped = 4" in upd[0] and "requeued_at < now()" in upd[0] and upd[1] == (27518, 29018, 20)


def test_wired_into_the_seed_and_the_drain():
    q = read("scripts", "queue_meets.py")
    assert 'if source == "tfrrs" and sport == "XC":' in q and "tfrrsHoles(cur, top, write=write)" in q
    r = read("tfrrs", "driver", "run_tfrrs.py")
    assert "if sport == SPORT_XC:\n            await asyncio.to_thread(_markNotAMeet" in r
    assert "SET scraped = 4, requeued_at = now()" in r
