"""Arrow Live Results (sources/arrow.py), off real pages fetched 2026-11-10:
Indiana's recent-events index (trimmed) and the 2026 IHSAA Cross Country
Sectional 22 results, boys (86) and girls (66).

    python -m pytest -q tests/test_sources_arrow.py
"""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import arrow as A                                   # noqa: E402
from sources import land as L                                    # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "arrow")


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def test_the_index_lists_each_race_with_its_day_timer_and_state():
    idx = A.parseIndex(read("index_in_2026-11-10.html"))
    s22 = [e for e in idx if "Sectional 22" in e["name"]]
    assert {e["name"] for e in s22} == {"2026 IHSAA Cross Country Sectional 22 - Boys",
                                        "2026 IHSAA Cross Country Sectional 22 - Girls"}
    assert all(e["date"] == "2026-10-17" and e["state"] == "IN"
               and e["timer"] == "Above and Beyond Timing" for e in s22)
    assert s22[0]["url"].startswith("https://www.arrowliveresults.com/2026IHSAACrossCountrySectional22")


def test_road_races_and_relays_are_not_cross_country():
    assert A.isXC("2026 IHSAA Cross Country Sectional 22 - Boys")
    assert A.isXC("2026 Culver MS Boys XC")
    assert not A.isXC("Gobble Up Hunger - Half Mile")
    assert not A.isXC("2026 Decatur Hokum Karem")
    assert not A.isXC("2025 Plainfield Cross Country Relays - Girl Hokum Karem")


def test_a_meets_races_are_one_meet_even_with_a_mistyped_address():
    idx = [e for e in A.parseIndex(read("index_in_2026-11-10.html")) if A.isXC(e["name"])]
    meets = A.group(idx)
    mw = [m for m in meets if m["name"] == "Midwest Championship Cross Country Meet"]
    assert len(mw) == 1 and len(mw[0]["races"]) == 9      # one race says "Fishers, IN 10910 Elle"
    s22 = [m for m in meets if m["name"] == "2026 IHSAA Cross Country Sectional 22"]
    assert len(s22) == 1 and len(s22[0]["races"]) == 2


def test_by_address_too_when_the_names_differ():
    rows = [{"date": "2026-08-22", "timer": "RCC Timing", "address": "Culver, IN IN United States",
             "state": "IN", "url": f"https://www.arrowliveresults.com/{u}", "name": n}
            for u, n in (("a", "2026 Culver MS Boys XC"), ("b", "2026 HS Girls Culver XC Invitational"))]
    rows.append(dict(rows[0], url="https://www.arrowliveresults.com/c", address="Culver , IN IN United States",
                     name="2026 HS Boys Culver XC Invitaitonal"))
    (m,) = A.group(rows)
    assert len(m["races"]) == 3 and m["name"] == "2026 Culver XC Invitational"


def test_a_results_page_reads_place_name_team_gender_age_and_time():
    rows = A.parseResults(read("results_sectional22_boys.html"))
    assert len(rows) == 86
    row, gender, age = rows[0]
    assert (row.place, row.first, row.last, row.school, row.seconds) == (1, "Chase", "Norris",
                                                                          "Bloomington South", 964.44)
    assert (gender, age) == ("M", 17)
    assert [r[0].place for r in rows] == list(range(1, 87))
    girls = A.parseResults(read("results_sectional22_girls.html"))
    assert len(girls) == 66 and all(g == "F" for _r, g, _a in girls)


def test_a_race_with_both_genders_lands_as_two():
    both = A.parseResults(read("results_sectional22_boys.html")) + A.parseResults(
        read("results_sectional22_girls.html"))
    ev = A.events("Open Race", both, 3)
    assert [(e.gender, e.title, len(e.rows)) for e in ev] == [("F", "Girls Open Race", 66),
                                                              ("M", "Boys Open Race", 86)]
    (one,) = A.events("Girls", both[86:], 2)
    assert one.gender == "F" and one.sport == "XC"


def test_community_and_elementary_races_are_left_out():
    rows = A.parseResults(read("results_sectional22_boys.html"))
    assert A._ageKind(rows) == ""
    assert A._ageKind([(r, g, 40) for r, g, _a in rows]) == "community"
    assert A._ageKind([(r, g, 9) for r, g, _a in rows]) == "elementary"


class _Store:
    def meta(self, url):
        return {}


class _Pages:
    """The fixtures served as the site would: the race pages carry their
    EventId, the results calls return the saved tables."""

    def __init__(self):
        self.store, self.asked = _Store(), []

    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        self.asked.append(url)
        if "pvResults" in url:
            name = ("results_sectional22_boys.html" if "pEventId=25700" in url
                    else "results_sectional22_girls.html")
            return Fetched(url, 200, read(name).encode(), True, "")
        eid = 25700 if url.endswith("Boys") else 25698
        return Fetched(url, 200, f"<script>var EventId = {eid};</script>".encode(), True, "")


def test_a_meet_lands_with_both_races():
    idx = [e for e in A.parseIndex(read("index_in_2026-11-10.html")) if A.isXC(e["name"])]
    (meet,) = [m for m in A.group(idx) if "Sectional 22" in m["name"]]
    lm, why = asyncio.run(A.readMeet(_Pages(), meet, 0))
    assert lm.source == "timer:arrow-live-results" and lm.date == "2026-10-17" and lm.state == "IN"
    assert sorted((r.gender, len(r.rows)) for r in lm.races) == [("F", 66), ("M", 86)]
    assert all(r.sport == "XC" for r in lm.races) and lm.rowCount() == 152
    assert L.summary(lm)
