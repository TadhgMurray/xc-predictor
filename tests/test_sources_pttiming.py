"""PrimeTime Timing (sources/pttiming.py), off real pages fetched 2026-11-10:
the results list (trimmed) and the WIAA State XC Championships 2026 event
page's report links.

    python -m pytest -q tests/test_sources_pttiming.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import pttiming as P                                # noqa: E402
from sources import registry                                     # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "pttiming")


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def test_the_list_gives_each_event_its_first_day_and_place():
    ev = {e["name"]: e for e in P.parseIndex(read("results_2026-11-10.html"))}
    ms = ev["MSHSAA Cross Country Championships"]
    assert (ms["date"], ms["place"], ms["state"]) == ("2026-11-06", "Columbia, MO", "MO")
    assert ms["url"] == "https://www.pttiming.com/event/0d9eb265-313e-4d14-bc0e-1080b8a3b9e2?pt=results"


def test_cross_country_by_name_not_road_races():
    ev = {e["name"]: e for e in P.parseIndex(read("results_2026-11-10.html"))}
    assert P.isSchoolXC(ev["WIAA Cross Country Championships"])
    assert P.isSchoolXC(ev["IHSA Class 3A Sectional - Lake Park"])
    assert not P.isSchoolXC(ev["Green Bay Beer Run 5K & .05K"])
    assert not P.isSchoolXC(ev["UW Oshkosh Dia de Los Muertos Carrera"])


def test_an_event_page_names_its_html_reports():
    urls = P.reportUrls(read("event_wiaa_2026_links.html"))
    assert len(urls) == 6 and all(u.startswith("https://dash.pttiming.com/f/") for u in urls)


def test_dates_with_a_range():
    assert P.parseDate("November 6th - 7th, 2026") == "2026-11-06"
    assert P.parseDate("October 31st, 2026") == "2026-10-31"


def test_nothing_is_off_limits_by_name_or_host_now():
    # owner, 2026-11-10: PTTiming, Leone, TimerHub, ItsYourRace and eccathletics told him it is fine
    assert not registry.offLimits("https://www.pttiming.com/results")
    assert not registry.offLimits("https://results.leonetiming.com/x")
    assert not registry.BLOCKED_NAMES.search("PrimeTime Timing (PTTiming)")
