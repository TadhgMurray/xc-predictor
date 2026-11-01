"""AthleticLIVE's landed meets close their athletic.net queue rows, so the
anet launcher never asks for them (owner, 2026-11-01)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import athleticlive  # noqa: E402


class _Cur:
    def __init__(self):
        self.sql = self.args = None
        self.rowcount = 7

    def execute(self, sql, args=None):
        self.sql, self.args = sql, args


def test_cover_closes_landed_meets_only_and_leaves_in_flight():
    cur = _Cur()
    assert athleticlive.coverAnetQueue(cur) == 7
    sql = " ".join(cur.sql.split())
    assert "am.landed_n > 0" in sql
    assert "'anet', 1" in sql
    assert "WHERE meet_queue.scraped IN (0, 2, 4)" in sql      # 3 = in flight: untouched
    assert cur.args == (["TF", "XC"],)


def test_scrape_local_runs_cover_after_land():
    sh = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "deploy", "scrape_local.sh"), encoding="utf-8").read()
    assert sh.index("athleticlive land") < sh.index("athleticlive cover")


def test_landing_waits_for_the_meets_last_day():
    import inspect
    from sources import athleticlive_xc
    assert "COALESCE(am.date_end, am.meet_date) < %s" in inspect.getsource(athleticlive._candidates)
    assert "COALESCE(am.date_end, am.meet_date) < %s" in inspect.getsource(athleticlive_xc.candidates)
    assert "still being posted" in inspect.getsource(athleticlive.landForScrape)
