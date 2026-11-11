"""MileSplit (sources/milesplit.py), off real pages fetched 2026-11-10: New
York's results list (trimmed), Section 5 Championships 2026's raw file (a
<pre> of tab-separated races) and NYSAIS XC Championships 2026's raw file (a
RunMeet table).

    python -m pytest -q tests/test_sources_milesplit.py
"""
import asyncio
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import milesplit as M                               # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "milesplit")
S5 = "https://ny.milesplit.com/meets/770565-section-5-championships-2026/results"


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def test_the_states_list_gives_each_meet_its_day_venue_and_state():
    idx = M.parseIndex(read("index_ny_2026-11-10.html"), datetime.date(2026, 11, 10))
    s5 = [m for m in idx if m["meet_id"] == 770565]
    assert s5 and s5[0]["name"] == "Section 5 Championships" and s5[0]["date"] == "2026-11-07"
    assert s5[0]["venue"] == "Castile, NY" and s5[0]["state"] == "NY" and s5[0]["url"] == S5


def test_a_meet_names_its_raw_files():
    assert M.rawUrls(read("meet_section5_2026_files.html"), S5) == [S5 + "/1340187/raw"]


def test_a_tab_separated_file_reads_every_race():
    ev = M.eventsOf(M.rawText(read("raw_section5_2026_pre.html")))
    assert [(e.title, e.gender, e.meters, len(e.rows)) for e in ev][:2] == [
        ("Boys Class C - 5K Run", "M", 5000.0, 86), ("Boys Class D - 5K Run", "M", 5000.0, 184)]
    assert sum(len(e.rows) for e in ev) == 815
    r = ev[0].rows[0]
    assert (r.place, r.first, r.last, r.grade, r.school, r.seconds) == (1, "Desmond", "Hill", "11",
                                                                      "Marcus Whitman", 919.3)


def test_a_runmeet_table_reads_like_the_tab_file():
    ev = M.eventsOf(M.rawText(read("raw_nysais_2026_table.html")))
    assert [(e.title, e.gender, len(e.rows)) for e in ev] == [("Varsity Boys 5000 Meter", "M", 237),
                                                              ("Varsity Girls 5000 Meter", "F", 213)]
    assert ev[0].rows[0].school == "Rye Country Day School"


class _Site:
    store = None

    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        body = read("raw_section5_2026_pre.html") if url.endswith("/raw") else read(
            "meet_section5_2026_files.html")
        return Fetched(url, 200, body.encode(), True, "")


def test_a_fall_meet_with_no_track_events_is_cross_country(monkeypatch):
    monkeypatch.setattr(M, "GAP", 0)
    meet = {"meet_id": 770565, "url": S5, "name": "Section 5 Championships", "venue": "Castile, NY",
            "state": "NY", "date": "2026-11-07"}
    lm, _ = asyncio.run(M.readMeet(_Site(), meet, 0))
    assert lm.rowCount() == 815 and {r.sport for r in lm.races} == {"XC"}
    assert M.isFallMeet("2026-11-07") and not M.isFallMeet("2026-04-11")


def test_an_athlete_heading_with_a_bib_column_reads_every_row():
    # HXN Championships 2026: "#  Athlete  Yr Team  Mark  H#", no "Name"
    ev = M.eventsOf(read("hxn_2026_athlete_heading.txt"))
    assert len(ev) == 1 and ev[0].gender == "M" and len(ev[0].rows) == 10
    r = ev[0].rows[0]
    assert (r.first, r.last, r.grade, r.school, r.mark) == ("Abraham", "Raines", "12", "Sovereign Grace", "15:06.07")
    assert r.points is None                                  # H# is the heat
    assert ev[0].rows[9].school == "Grand Rapids Track Club"


class _Track(_Site):
    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        if url.endswith("/raw"):
            return Fetched(url, 200, ("<pre>" + read("aia_2026_womens_track.txt") + "</pre>").encode(), True, "")
        return await super().fetch(url, max_age)


def test_a_womens_event_reads_and_a_november_meet_on_a_track_stays_track(monkeypatch):
    # Athletes in Action Evening of Miles 2026: "Event 1  Womens 3200 Meter Run"
    monkeypatch.setattr(M, "GAP", 0)
    meet = {"meet_id": 789829, "url": S5, "name": "Athletes in Action Evening of Miles", "venue": "",
            "state": "OH", "date": "2026-11-09"}
    lm, _ = asyncio.run(M.readMeet(_Track(), meet, 0))
    assert lm.rowCount() >= 6 and {r.sport for r in lm.races} == {"TF"}
