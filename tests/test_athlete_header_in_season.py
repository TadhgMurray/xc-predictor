"""The athlete header takes the season of the sport in season (owner,
2026-11-01: a fall track race put XC runners under '2027 Track')."""
import datetime
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()


def _inSeason():
    m = re.search(r"\ndef _inSeason\(today=None\):.*?\n\n\n", SRC, re.S)
    ns = {}
    exec(m.group(0), ns)
    return ns["_inSeason"]


def test_in_season_sport_and_year():
    f = _inSeason()
    assert f(datetime.date(2026, 11, 1)) == ("XC", 2026)
    assert f(datetime.date(2026, 8, 20)) == ("XC", 2026)
    assert f(datetime.date(2026, 12, 5)) == ("TF", 2026)      # shown "2027 Track"
    assert f(datetime.date(2027, 4, 10)) == ("TF", 2026)


def test_header_orders_in_season_first():
    i = SRC.index("(sport = %s AND year = %s) DESC")
    assert i < SRC.index("(n_races >= 3 OR year >= %s) DESC", i)
    assert "*_inSeason()" in SRC
