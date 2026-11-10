"""sources/discover.py: meet pages read off a timer's own results index."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import discover  # noqa: E402
from sources.fetch import Fetched  # noqa: E402


def test_meetpro_links_become_meet_index_urls():
    page = ('<a href="http://results.tfmeetpro.com/SnakeRiverTiming/Boise_City_XC_2026/">x</a>'
            '<a href="http://results.tfmeetpro.com/SnakeRiverTiming/">timer root</a>'
            '<a href="https://snakerivertiming.com/jimmyd26/index.html">own copy</a>'
            '<a href="https://hytek.active.com/index.html">someone else</a>')
    got = discover.candidates(page, "https://snakerivertiming.com/archives.html", 2026)
    assert got == [("http://results.tfmeetpro.com/SnakeRiverTiming/Boise_City_XC_2026/index.html", "meetpro"),
                   ("https://snakerivertiming.com/jimmyd26/index.html", "meetpro")]


def test_old_years_feeds_and_nav_pages_are_dropped():
    page = ('<a href="/2025-results.html">2025</a><a href="/2026-results.html">2026</a>'
            '<a href="https://www.athletic.net/x">anet</a><a href="/contact.html">Contact</a>'
            '<a href="/files/wayne_invite.pdf">Wayne Invitational</a>')
    got = [u for u, _ in discover.candidates(page, "https://t.com/", 2026)]
    assert got == ["https://t.com/2026-results.html", "https://t.com/files/wayne_invite.pdf"]


def test_listing_subfolders_from_since_year():
    page = ('<title>Index of /</title><a href="../">up</a><a href="2025/">2025/</a>'
            '<a href="2026/">2026/</a>')
    assert discover.candidates(page, "https://r.t.com/", 2026) == [("https://r.t.com/2026/", "listing")]


def test_alive_tenant_skipped():
    assert discover.isAliveTenant('<link href="https://livestatic.athletic.net/a.css">')


def test_xc_folders_first_in_the_fall():
    urls = ["https://r/2026/outdoor/", "https://r/2026/indoor/", "https://r/2026/cross/"]
    assert discover.inSeason(urls, month=10)[0] == "https://r/2026/cross/"
    assert discover.inSeason(urls, month=4)[-1] == "https://r/2026/cross/"


class _FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    async def fetch(self, url, max_age=None):
        body = self.pages.get(url)
        return Fetched(url, 200 if body else 404, body.encode() if body else None)


def test_season_folder_splits_into_meets_and_meet_folder_stays_whole():
    season = ('<title>Index of /2026/outdoor</title><a href="../">up</a>'
              '<a href="04-11-26-Mercy.htm">a</a><a href="04-18-26-Webster.htm">b</a>')
    meet = ('<title>Index of /2026/cross/10-31-26-WFL</title><a href="../">up</a>'
            '<a href="boys-varsity.htm">a</a><a href="girls-varsity.htm">b</a>')
    f = _FakeFetcher({"https://r/2026/outdoor/": season, "https://r/2026/cross/10-31-26-WFL/": meet})
    got = asyncio.run(discover.walkListing(f, "https://r/2026/outdoor/", 2026, None, 5))
    assert got == ["https://r/2026/outdoor/04-18-26-Webster.htm", "https://r/2026/outdoor/04-11-26-Mercy.htm"]
    got = asyncio.run(discover.walkListing(f, "https://r/2026/cross/10-31-26-WFL/", 2026, None, 5))
    assert got == ["https://r/2026/cross/10-31-26-WFL/"]


def test_numbered_reports_in_one_folder_are_one_meet():
    found = [("https://r/2026/Outdoor/Sub/Results2.htm", "hytek"),
             ("https://r/2026/Outdoor/Sub/Results1.htm", "hytek"),
             ("https://r/other.pdf", "hytek")]
    assert discover.grouped(found) == [
        ("https://r/2026/Outdoor/Sub/", ("group", ["https://r/2026/Outdoor/Sub/Results1.htm",
                                                   "https://r/2026/Outdoor/Sub/Results2.htm"])),
        ("https://r/other.pdf", "hytek")]


def test_files_before_pages_and_xc_first_in_fall():
    cands = [("https://t/indoor-results", "page"), ("https://t/cross-country-results", "page"),
             ("https://s/2026/indoor/a.pdf", "hytek"), ("https://s/2026/XC/b.pdf", "hytek")]
    assert [u for u, _ in discover.ordered(cands, month=10)] == [
        "https://s/2026/XC/b.pdf", "https://s/2026/indoor/a.pdf",
        "https://t/cross-country-results", "https://t/indoor-results"]


def test_words_inside_words_are_not_nav_or_road():
    assert not discover._NAV_WORDS.search("maple-leaf-classic-xc")
    assert discover.ROAD.search("2026/roadrace/26Yankton.pdf")
    assert not discover.ROAD.search("tri-county xc invitational")


def test_crystal_reports_html_is_a_report():
    assert discover.isReport(b'<HTML><HEAD><META NAME="Author" CONTENT="Crystal Reports 11.0">')


def test_pages_that_link_to_each_other_are_read_once():
    # 2026-11-10, baytaf.org and three more: two index.htm pages on the
    # timer's own site, each linking the other, went round until Python's
    # RecursionError lost the timer's whole night
    a = '<a href="https://t.example/a/index.htm">A</a><a href="https://t.example/b/index.htm">B</a>'
    pages = {"https://t.example/results.htm": a, "https://t.example/a/index.htm": a,
             "https://t.example/b/index.htm": a}

    class Counting(_FakeFetcher):
        asked = []

        async def fetch(self, url, max_age=None):
            self.asked.append(url)
            return await _FakeFetcher.fetch(self, url, max_age)
    f = Counting(pages)
    found, _note, _n = asyncio.run(discover._ownIndex(f, "https://t.example/results.htm", 2026, 5,
                                                      set(), None))
    assert found == []
    assert f.asked.count("https://t.example/a/index.htm") == 1
    assert f.asked.count("https://t.example/b/index.htm") == 1
