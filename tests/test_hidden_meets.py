"""queue_meets.hiddenMeets: 'no meet' answers among this season's meets are
asked again, a few a night (owner, 2026-11-01: anet XC 274532)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import queue_meets as Q  # noqa: E402


class Cur:
    def __init__(self):
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(101,)]


def test_hidden_query_is_capped_rested_and_near_recent_meets():
    c = Cur()
    assert Q.hiddenMeets(c, "XC", 290000, "anet") == [101]
    sql, args = c.calls[-1]
    assert "q.scraped = 4" in sql and "LIMIT  %(cap)s" in sql
    assert args["cap"] == Q.HIDDEN_CAP and args["near"] == Q.HIDDEN_NEAR
    assert args["rest"] == Q.HIDDEN_REST_DAYS and "requeued_at" in sql
    assert "x.meet_id <> q.meet_id" in sql


def test_seed_sport_requeues_hidden_meets():
    src = open(os.path.join(ROOT, "scripts", "queue_meets.py"), encoding="utf-8").read()
    assert "todo = sorted(set(dated) | set(sched_retry) | set(hidden))" in src
