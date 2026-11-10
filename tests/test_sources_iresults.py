"""iResultsLIVE (sources/iresults.py), off real pages fetched 2026-11-10: the
latest-events index (trimmed), MVC XC Championship 2026 boys varsity as the
site's own CSV download, and Pelham XC Invitational 2026 girls varsity as its
printable report.

    python -m pytest -q tests/test_sources_iresults.py
"""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import iresults as I                                # noqa: E402
from sources import raceroster as R                              # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "iresults")


def raw(name):
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def _index():
    return I.parseIndex(raw("index_latest_2026-11-10.html").decode())


def test_the_index_gives_each_event_its_day_place_and_races():
    ev = {e["eid"]: e for e in _index()}
    mvc = ev[6907]
    assert (mvc["name"], mvc["date"], mvc["place"], mvc["state"]) == (
        "MVC XC Championship Meet 2026", "2026-10-31", "Lowell, MA", "MA")
    assert mvc["races"][:2] == [("BoysVarsity", 88), ("GirlsVarsity", 72)]


def test_school_cross_country_not_road_races():
    ev = {e["eid"]: e for e in _index()}
    assert I.isSchoolXC(ev[6907]) and I.isSchoolXC(ev[6891]) and I.isSchoolXC(ev[6871])
    assert I.isSchoolXC(ev[6896])          # "League Championship": its races are BoysJVLower ...
    assert not I.isSchoolXC(ev[6909])      # Run to the Riverfront 5K
    assert not I.isSchoolXC(ev[6899])      # Fall Fest Run Walk for the Esplanade


def test_a_combined_report_that_repeats_jv_and_varsity_is_left_out():
    races = [r for r, _n in {e["eid"]: e for e in _index()}[6871]["races"]]
    assert "GirlsCombined" in races and "GirlsCombined" not in I.withoutCombined(races)
    assert I.withoutCombined(["GirlsJVCombined", "GirlsVarsityLower"]) == ["GirlsJVCombined",
                                                                           "GirlsVarsityLower"]


def test_the_csv_download_reads_place_name_school_and_time():
    rows = I.csvRows(raw("csv_mvc_2026_boys_varsity.csv"))
    assert len(rows) == 88
    a, sex = rows[0]
    assert (a.place, a.first, a.last, a.school, a.seconds, sex) == (1, "Niles", "Mason", "Billerica",
                                                                    927.0, "M")


def test_a_printable_report_takes_the_grade_not_the_class_year():
    # "Sex Year School Grade": Year is the class of 2028, Grade is 11
    rows = R.textRows(raw("report_pelham_2026_girls_varsity.txt").decode())
    assert len(rows) == 105
    a = rows[0][0]
    assert (a.place, a.first, a.last, a.grade, a.school, a.seconds) == (1, "Jaylyn", "Paul", "11",
                                                                      "Bishop Guertin", 1136.98)


class _Site:
    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        body = raw("csv_mvc_2026_boys_varsity.csv") if "BoysVarsity&" in url else b"Place,First,Last\n"
        return Fetched(url, 200, body, True, "")


def test_a_meet_lands_from_its_races_csvs():
    mvc = {e["eid"]: e for e in _index()}[6907]
    lm, _why = asyncio.run(I.readMeet(_Site(), mvc, 0))
    assert lm.source == "timer:iresultslive" and lm.date == "2026-10-31" and lm.state == "MA"
    assert [(r.gender, len(r.rows)) for r in lm.races] == [("M", 88)]
