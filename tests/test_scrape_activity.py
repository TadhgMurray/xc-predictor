# ★ What's new and Activity read the savers' own record (owner, 2026-10-23:
#   "is there no way to make activity and what's new just fast to compute?").
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "racecast")):
    if p not in sys.path:
        sys.path.insert(0, p)
import _env  # noqa: F401,E402
import psycopg2.extras                                             # noqa: E402
import database as D                                              # noqa: E402
import activity as A                                              # noqa: E402


class Cur:
    def __init__(self, answers=()):
        self.answers, self.sql, self.rows = list(answers), [], []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self.rows = []
        for pat, rows in self.answers:
            if pat in sql:
                self.rows = list(rows)
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur


def test_the_saver_records_one_row_per_day_and_meet(monkeypatch):
    got = []
    monkeypatch.setattr(psycopg2.extras, "execute_values", lambda cur, sql, rows, **k: got.extend(rows))
    monkeypatch.setitem(D._SCRAPE_ACTIVITY, "ready", True)
    at = dt.datetime(2026, 10, 23, 9, tzinfo=dt.timezone.utc)
    cur = Cur()
    D.noteScraped(Conn(cur), "XC", [("anet", 10, "2026-10-18", at)] * 3 + [("anet", 10, "2026-10-19", at),
                                                                         ("tfrrs", 99, None, at)])
    assert sorted(got, key=str) == sorted([
        (dt.date(2026, 10, 23), "XC", "anet", 10, 4, "2026-10-18", "2026-10-19", at),
        (dt.date(2026, 10, 23), "XC", "tfrrs", 99, 1, None, None, at)], key=str)
    assert [s for s, _ in cur.sql] == ["SAVEPOINT scrape_activity", "RELEASE SAVEPOINT scrape_activity"]


def test_a_failure_never_breaks_the_save(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no table")
    monkeypatch.setattr(psycopg2.extras, "execute_values", boom)
    monkeypatch.setitem(D._SCRAPE_ACTIVITY, "ready", True)
    cur = Cur()
    D.noteScraped(Conn(cur), "TF", [("anet", 1, "2026-10-18", dt.datetime.now(dt.timezone.utc))])
    assert cur.sql[-1][0] == "ROLLBACK TO SAVEPOINT scrape_activity"


def test_the_feed_reads_the_record_once_it_covers_the_window():
    since = "2026-10-20"
    rows = [("XC", "anet", 10, "2026-10-18", 40, "2026-10-23 09:00:00+00", "2026-10-23", "2030-01-01"),
            ("TF", "tfrrs", 7, "2026-10-17", 9, "2026-10-22 08:00:00+00", "2026-10-22", "2026-10-17")]
    cur = Cur([("to_regclass", [(True,)]), ("covered_from", [("2026-10-19",)]), ("FROM   scrape_activity", rows)])
    got, latest = A._fromActivity(cur, since, 400)
    assert got[0] == ("XC", "anet", 10, "2026-10-18", 40, "2026-10-23 09:00:00+00")
    # a race dated after its scrape day is not a real one: the meet's first date is used
    assert latest == {"2026-10-23": "2026-10-18", "2026-10-22": "2026-10-17"}
    # a record that starts after the window: the old query answers instead
    cur = Cur([("to_regclass", [(True,)]), ("covered_from", [("2026-10-23",)])])
    assert A._fromActivity(cur, since, 400) is None
