"""MeetPro accounts with no index page, and the run around them (2026-11-09:
"make sure the rest of the local timing sites work too").

  - results.tfmeetpro.com is an S3 website: http only, and its bucket lists
    itself (S3 ListObjects) -- that listing is an account's index
  - robots.txt is read per scheme AND host: an https robots.txt that never
    answers must not bar the http pages
  - MeetPro templates v1.3 / v1.6 have no event-number cell
  - `timers all` lands each timer as it finishes and stops at its budget

Synthetic names throughout.

    python -m pytest -q tests/test_sources_meetpro_bucket.py
"""
import asyncio
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sources import discover, meetpro, timers                       # noqa: E402
from sources import fetch as F                                       # noqa: E402
from sources.fetch import Fetched                                    # noqa: E402


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


# ------------------------------------------------------------------ fetcher
def test_robots_is_read_per_scheme_so_a_dead_https_does_not_bar_http():
    def getter(url):
        if url.startswith("https://"):
            raise ConnectionError("reset")          # S3 website: no TLS at all
        return Resp(404)                            # http: no robots.txt, allowed
    r = F.Robots(getter=getter)
    assert r.get("https://results.tfmeetpro.com/")[0] is False
    assert r.get("http://results.tfmeetpro.com/Acct/Meet/index.html") == (True, None)
    assert r.why("https://results.tfmeetpro.com/").startswith("robots.txt unreachable")


def test_a_real_disallow_is_still_called_a_disallow():
    r = F.Robots(getter=lambda u: Resp(200, "User-agent: *\nDisallow: /\n"))
    assert r.get("https://timer.example/x")[0] is False
    assert r.why("https://timer.example/x") == "robots.txt disallows"


def test_the_s3_hosts_have_their_own_pace_and_everyone_else_keeps_ten_seconds():
    lim = F.HostLimiter(interval=10)
    assert lim.intervalFor("results.tfmeetpro.com") == F.HOST_INTERVAL["results.tfmeetpro.com"] < 10
    assert lim.intervalFor("smalltimer.example") == 10
    lim.setInterval("results.tfmeetpro.com", 30)    # a published Crawl-delay still wins
    assert lim.intervalFor("results.tfmeetpro.com") == 30


# ------------------------------------------------------------------ bucket
LIST_1 = """<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><Name>results.tfmeetpro.com</Name>
<Prefix>Acct/</Prefix><IsTruncated>true</IsTruncated>
<Contents><Key>Acct/Old_Invite_2019/index.html</Key><LastModified>2019-09-07T16:40:43.000Z</LastModified></Contents>
<Contents><Key>Acct/Old_Invite_2019/results_1.html</Key><LastModified>2019-09-07T16:41:00.000Z</LastModified></Contents>
<Contents><Key>Acct/Reused_Folder/index.html</Key><LastModified>2024-05-01T10:00:00.000Z</LastModified></Contents>
</ListBucketResult>"""
LIST_2 = """<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><Name>results.tfmeetpro.com</Name>
<Prefix>Acct/</Prefix><IsTruncated>false</IsTruncated>
<Contents><Key>Acct/Reused_Folder/results_3.html</Key><LastModified>2026-10-02T18:00:00.000Z</LastModified></Contents>
<Contents><Key>Acct/River &amp; Pines XC/index.html</Key><LastModified>2026-09-12T15:00:00.000Z</LastModified></Contents>
<Contents><Key>Acct/images/logo.png</Key><LastModified>2026-09-12T15:00:00.000Z</LastModified></Contents>
<Contents><Key>Acct/README.txt</Key><LastModified>2026-09-12T15:00:00.000Z</LastModified></Contents>
</ListBucketResult>"""


def test_an_account_root_is_named_and_a_meet_url_is_not():
    assert discover.meetproAccount("http://results.tfmeetpro.com/Acct_Name/ (no index; S3 NoSuchKey)") == "Acct_Name"
    assert discover.meetproAccount("http://results.tfmeetpro.com/Acct_Name/Meet/index.html") is None
    assert discover.meetproAccount("https://www.timer.example/Acct_Name/") is None


def test_meetpro_links_are_plain_http():
    assert discover.meetproRoot("https://results.tfmeetpro.com/Acct/Meet/results_2.html") == \
        "http://results.tfmeetpro.com/Acct/Meet/index.html"


def test_a_bucket_page_gives_keys_dates_and_the_next_marker():
    entries, marker = discover.bucketPage(LIST_1)
    assert entries[0] == ("Acct/Old_Invite_2019/index.html", "2019-09-07T16:40:43.000Z")
    assert marker == "Acct/Reused_Folder/index.html"
    entries, marker = discover.bucketPage(LIST_2)
    assert entries[1][0] == "Acct/River & Pines XC/index.html" and marker is None


def test_meet_folders_newest_first_dated_by_their_newest_file():
    entries = discover.bucketPage(LIST_1)[0] + discover.bucketPage(LIST_2)[0]
    got = discover.accountMeets(entries, "Acct", 2026)
    # the reused folder is dated by its 2026 results page, not its 2024 index;
    # the 2019 meet is too old; images/ and a loose file are not meets
    assert got == [("http://results.tfmeetpro.com/Acct/Reused_Folder/index.html", "2026-10-02"),
                   ("http://results.tfmeetpro.com/Acct/River%20%26%20Pines%20XC/index.html", "2026-09-12")]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    async def fetch(self, url, max_age=None):
        self.asked.append(url)
        for key, body in self.pages.items():
            if key(url):
                return Fetched(url, 200, body.encode())
        return Fetched(url, 404, None)


def test_discover_reads_an_account_off_its_bucket_listing():
    fetcher = FakeFetcher({lambda u: "marker=" in u: LIST_2,
                           lambda u: u.startswith(discover.MEETPRO_BUCKET_LIST): LIST_1})
    src = types.SimpleNamespace(results_url="http://results.tfmeetpro.com/Acct/ (no index; S3 NoSuchKey)",
                                website="", slug="acct")
    known = {"http://results.tfmeetpro.com/Acct/Reused_Folder/index.html"}
    urls, note = asyncio.run(discover.discover(fetcher, src, 2026, 5, known))
    assert urls == [("http://results.tfmeetpro.com/Acct/River%20%26%20Pines%20XC/index.html", "meetpro")]
    assert "2 pages" in note and "1 new" in note
    assert not any("results.tfmeetpro.com/Acct/" == u.split("//", 1)[1] for u in fetcher.asked)


# ------------------------------------------------------------------ v1.6 index
INDEX_V16 = """<html><head><title>Hillcrest Invite | MeetPro by DirectAthletics</title></head>
<!--DO NOT ERASE! VERSION:&MeetProTemplate_v1.6--><body><div class="header">
<h3 class="text-muted"><a class="home-link-hdr" href="index.html">Hillcrest Invite</a><span>Hillcrest, OH<br></span><span>5/1/2026</span></h3></div>
<table class="session-events-table table"><thead><tr><th>Status</th><th class="event">Event</th><th class="round">Round</th>
<th class="start-list">Start List</th><th class="results">Results</th></tr></thead><tbody>
<tr><td>Complete</td><td class="event">Girls 4 x 800m Relay</td><td class="round">Final</td>
<td class="start-list"><a href='start_list_32.html'>Start List</a></td><td class="results"><a href='results_32.html#round_1'>Results</a></td></tr>
<tr><td>Complete</td><td class="event">Boys 100 Meters</td><td class="round">Prelims</td>
<td class="start-list"><a href='start_list_1.html'>Start List</a></td><td class="results"><a href='results_1.html#round_1'>Results</a></td></tr>
<tr><td>Complete</td><td class="event">Boys 100 Meters</td><td class="round">Final</td>
<td class="start-list"><a href='start_list_1.html'>Start List</a></td><td class="results"><a href='results_1.html#round_2'>Results</a></td></tr>
<tr><td>Scheduled</td><td class="event">Girls 3200 Meters</td><td class="round">Final</td>
<td class="start-list"><a href='start_list_20.html'>Start List</a></td><td class="results"></td></tr>
</tbody></table></body></html>"""


def test_an_index_with_no_number_cell_takes_the_number_from_its_pages():
    idx = meetpro.parseMeetIndex(INDEX_V16)
    assert (idx.name, idx.place, idx.date) == ("Hillcrest Invite", "Hillcrest, OH", "2026-05-01")
    got = [(e.number, e.title, e.results_href, e.relay) for e in idx.events]
    # one event per number: the 100's two rounds are one page (parseRounds splits them)
    assert got == [(32, "Girls 4 x 800m Relay", "results_32.html", True),
                   (1, "Boys 100 Meters", "results_1.html", False),
                   (20, "Girls 3200 Meters", "", False)]


# ------------------------------------------------------------------ the run
def _args(**kw):
    a = types.SimpleNamespace(dry=True, only=None, per_source=5, since_year=2026, out=None,
                              show=5, minutes=None)
    a.__dict__.update(kw)
    return a


def test_past_its_budget_the_run_starts_no_new_meet(monkeypatch, tmp_path):
    src = types.SimpleNamespace(slug="acct", enabled=True, kind="meetpro")
    monkeypatch.setattr(timers.registry, "load", lambda: [src])

    async def disc(fetcher, s, since, per, known):
        return [("http://results.tfmeetpro.com/Acct/A/index.html", "meetpro"),
                ("http://results.tfmeetpro.com/Acct/B/index.html", "meetpro")], "2 meets"
    monkeypatch.setattr(discover, "discover", disc)
    fetched = []

    async def fetchMeet(fetcher, slug, url, kind=None):
        fetched.append(url)
        return None, "nothing"
    monkeypatch.setattr(timers, "fetchMeet", fetchMeet)
    monkeypatch.setattr(timers, "_now", iter([0, 0, 10_000, 10_000, 10_000]).__next__)
    monkeypatch.setenv("XCP_RAW_DIR", str(tmp_path))
    monkeypatch.setattr(F, "RAW_DIR", str(tmp_path))
    monkeypatch.setattr(F.RawStore.__init__, "__defaults__", (str(tmp_path),))
    timers.cmdAll(_args(minutes=1, out=str(tmp_path / "x.jsonl")))
    assert fetched == ["http://results.tfmeetpro.com/Acct/A/index.html"]


def test_every_meetpro_account_the_research_file_names_is_found():
    assert discover.accountsNamed(
        "AthleticLIVE + MeetPro HTML on results.tfmeetpro.com/Acct_One and /Acct_Two/ (host)",
        "see results.tfmeetpro.com/Acct_One/") == ["Acct_One", "Acct_Two"]


def test_a_timer_whose_page_is_a_feeds_is_read_off_its_accounts():
    fetcher = FakeFetcher({lambda u: "marker=" in u: LIST_2,
                           lambda u: u.startswith(discover.MEETPRO_BUCKET_LIST): LIST_1})
    src = types.SimpleNamespace(results_url="https://xx.milesplit.com/timing/1/acct", website="",
                                platform="MileSplit + MeetPro HTML on results.tfmeetpro.com/Acct/",
                                notes="", sample_url="", slug="acct")
    urls, note = asyncio.run(discover.discover(fetcher, src, 2026, 5))
    assert [u for u, _k in urls] == ["http://results.tfmeetpro.com/Acct/Reused_Folder/index.html",
                                     "http://results.tfmeetpro.com/Acct/River%20%26%20Pines%20XC/index.html"]
    assert "feed" in note and not any("milesplit" in u for u in fetcher.asked)
