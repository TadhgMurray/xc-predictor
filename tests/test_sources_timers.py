"""The timing-company landing, dedup, promotion and linking (sources/land.py,
sources/dedup.py, sources/promote.py, sources/timers.py, sources/tfrrs_hs.py,
scripts/link_uploads.py --source timer). Synthetic names.

    python -m pytest -q tests/test_sources_timers.py
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import dedup as D                                     # noqa: E402
from sources import hytek, land as L                               # noqa: E402
from sources import promote as PR                                  # noqa: E402
from sources import registry, tfrrs_hs as HS, timers as T          # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "hytek")


def read(*p):
    with open(os.path.join(_ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------ landing

def test_a_hytek_report_lands_its_individual_races_with_the_timers_distance():
    meet = hytek.parseReport(read("tests", "fixtures", "hytek", "mm_xc_lakeside.txt"))
    lm = L.fromHytek(meet, "lincoln-timing", "https://t.example/lakeside.htm")
    assert lm.source == "timer:lincoln-timing" and lm.date == "2025-10-04"
    assert lm.state == "OR", "the venue line's state"
    assert lm.races and all(r.sport == "XC" for r in lm.races)
    girls = lm.races[0]
    assert (girls.gender, girls.meters) == ("F", 5000.0)
    assert girls.division == "Girls 5000 Meter Run CC Varsity"
    ann = girls.rows[0]
    assert (ann.name, ann.grade, ann.school, ann.place, ann.seconds) == ("Ann Doe", "10", "Sunset", 1, 1065.21)
    dnf = [r for r in girls.rows if r.mark == "DNF"]
    assert dnf and dnf[0].status == "DNF" and dnf[0].seconds is None
    assert lm.sport == "XC"


def test_relays_and_field_events_are_not_landed():
    ev = [hytek.Event(1, "M", "4x800 Meter Relay", 800.0, rows=[hytek.Row(1, "A", "B", "", "X", 500.0, "8:20")]),
          hytek.Event(2, "F", "High Jump", None, rows=[hytek.Row(1, "C", "D", "", "Y", None, "5-02")]),
          hytek.Event(3, "F", "1600 Meter Run", 1600.0, rows=[hytek.Row(1, "Ek", "Ann", "11", "Z", 300.1, "5:00.10")])]
    lm = L.fromHytek(hytek.Meet(name="County Meet", date="2026-04-11", events=ev), "t", "u")
    assert [r.n for r in lm.races] == [3] and lm.races[0].sport == "TF"


def test_sport_from_the_title_the_meet_or_a_long_distance():
    assert L.sportOf("5k Run CC Varsity") == "XC"
    assert L.sportOf("Boys 5000 Meters", "Wayne Cross Country Invitational") == "XC"
    assert L.sportOf("Girls 3200 Meter Run", "County Relays", 3200.0) == "TF"
    assert L.sportOf("Boys 6000 Meters", "Fall Classic", 6000.0) == "XC"
    assert L.sportOf("Boys 10000 Meter Run", "State Track Meet", 10000.0) == "TF"


def test_the_landed_meet_round_trips_through_its_file_and_hashes_the_same():
    meet = hytek.parseReport(read("tests", "fixtures", "hytek", "mm_xc_lakeside.txt"))
    lm = L.fromHytek(meet, "t", "u")
    back = L.LandedMeet.fromJson(lm.toJson())
    assert back == lm and back.contentSha() == lm.contentSha()


def test_ids_are_negative_and_never_an_uploads():
    assert L.meetIdFor(1) == -700_000_001 and L.divIdFor(1, 3) == -700_000_001_003
    upload_lo = -(900_000_000 + 1)                    # racecast/uploads.meetIdFor(1)
    assert L.meetIdFor(199_999_998) > upload_lo
    a = L.mintResultId("timer:t", 1, 2, 3, "Ann Doe", 1065.21, 1)
    assert a < 0 and a == L.mintResultId("timer:t", 1, 2, 3, "ann doe", 1065.21, 1)
    assert a != L.mintResultId("timer:t", 1, 2, 4, "Ann Doe", 1065.21, 1)


def test_the_landing_tables_keep_the_stated_distance_and_the_twin_beside_it():
    assert "meters_stated REAL" in L.DDL and "twin_meters   REAL" in L.DDL
    assert "source        TEXT NOT NULL,          -- 'timer:<slug>'" in L.DDL
    assert "result_id     BIGINT" in L.DDL


# ------------------------------------------------------------ dedup

def _l(race, n, name, ts, school):
    return D.LRow(race, n, name, ts, school)


def _f(name, ts, school, meet=5001, div=9001, source="anet"):
    return D.FRow(source, meet, div, name, ts, school)


BOYS = [("Aaron Alder", 960.43, "Lakeside"), ("Ben Birch", 965.12, "Lakeside"), ("Carl Cedar", 970.0, "Northfield"),
        ("Dan Dogwood", 975.5, "Northfield")]


def test_a_race_whose_runners_the_feed_holds_is_a_twin():
    landed = [_l(1, i, n, t, s) for i, (n, t, s) in enumerate(BOYS, 1)]
    feed = [_f(n, round(t, 1), s) for n, t, s in BOYS]
    v = D.judge(landed, feed, "XC")[1]
    assert v.twin and (v.source, v.meet_id, v.div_id, v.matched, v.strong) == ("anet", 5001, 9001, 4, 4)


def test_a_pack_by_school_alone_says_nothing():
    landed = [_l(1, 1, "Ann Ek", 1000.0, "Lakeside"), _l(1, 2, "Bea Fu", 1000.1, "Lakeside"),
              _l(1, 3, "Cy Go", 1000.2, "Lakeside")]
    feed = [_f("Unknown", 1000.0, "Lakeside"), _f("Unknown", 1000.1, "Lakeside")]
    v = D.judge(landed, feed, "XC")[1]
    assert not v.twin, "teammates a tenth apart pair with several partners: no evidence"


def test_another_day_or_another_time_is_not_the_race():
    landed = [_l(1, i, n, t, s) for i, (n, t, s) in enumerate(BOYS, 1)]
    feed = [_f(n, t + 4.0, s) for n, t, s in BOYS]
    assert not D.judge(landed, feed, "XC")[1].twin


def test_whole_seconds_against_tenths_still_pair():
    landed = [_l(1, i, n, float(int(t)), s) for i, (n, t, s) in enumerate(BOYS, 1)]
    feed = [_f(n, round(t, 1), s) for n, t, s in BOYS]
    assert D.judge(landed, feed, "XC")[1].twin


def test_track_pairs_to_the_hundredth():
    landed = [_l(8, 1, "Aaron Alder", 270.11, "Lakeside")]
    assert D.judge(landed, [_f("Aaron Alder", 270.11, "Lakeside")], "TF")[8].twin
    assert not D.judge(landed, [_f("Aaron Alder", 270.15, "Lakeside")], "TF")[8].twin


def test_a_meet_with_some_races_in_the_feed_is_partial():
    landed = [_l(1, i, n, t, s) for i, (n, t, s) in enumerate(BOYS, 1)] + \
             [_l(2, 1, "Max Maple", 1030.0, "Lakeside"), _l(2, 2, "Ned Nutmeg", 1040.0, "Northfield")]
    feed = [_f(n, round(t, 1), s) for n, t, s in BOYS]
    vs = D.judge(landed, feed, "XC")
    assert vs[1].twin and not vs[2].twin
    status, src, mid, share = D.meetVerdict(vs)
    assert (status, src, mid, share) == ("partial", "anet", 5001, round(4 / 6, 3))
    assert D.meetVerdict({})[0] == "new"


def test_the_feed_with_more_of_the_race_wins_and_anet_breaks_a_tie():
    landed = [_l(1, i, n, t, s) for i, (n, t, s) in enumerate(BOYS, 1)]
    feed = [_f(n, round(t, 1), s) for n, t, s in BOYS] + \
           [_f(n, round(t, 1), s, meet=777, div=1, source="tfrrs") for n, t, s in BOYS]
    v = D.judge(landed, feed, "XC")[1]
    assert (v.source, v.meet_id) == ("anet", 5001)


def test_dedup_reads_every_feed_on_three_days():
    assert D._days("2026-10-10") == ["2026-10-09", "2026-10-10", "2026-10-11"]
    assert D._days("") == [] and set(D.FEEDS) == {"anet", "tfrrs", "upload", "timer", "ext"}


# ------------------------------------------------------------ promotion

class Cur:
    def __init__(self, answers):
        self.answers = list(answers)
        self.sql = []
        self.rows = []
        self.rowcount = 0
        self.connection = None

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self.rows = []
        self.rowcount = 1
        for pat, rows in self.answers:
            if re.search(pat, sql, re.S):
                self.rows = list(rows)
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def mogrify(self, sql, args):
        return b"(" + b",".join(repr(a).encode() for a in args) + b")"


def test_promotion_writes_upload_shaped_rows_tagged_timer(monkeypatch):
    import psycopg2.extras as X
    wrote = []
    monkeypatch.setattr(X, "execute_values", lambda cur, sql, rows, **k: wrote.append((sql, list(rows))))
    cur = Cur([(r"FROM meets_ext WHERE", [(4, "timer:test-timing", "Lakeside XC", "2026-10-10", "Lakeside Park",
                                           "", "KS")]),
               (r"FROM races_ext", [(3, "XC", "5k Run CC JV", "M", "Boys 5k Run CC JV", 5000.0)]),
               (r"FROM results_ext", [(41, 3, 1, "Max Maple", "10", "Lakeside", 1, 1030.0, None),
                                      (42, 3, 2, "Ned Nutmeg", "9", "", None, None, "DNF")])])
    out = PR.promoteMeet(cur, 4)
    assert out["meet_id"] == -700_000_004 and out["races"] == 1
    meets = [r for s, r in wrote if "INSERT INTO meets " in s][0]
    assert meets == [(-700_000_004_003, -700_000_004, "Lakeside XC", "2026-10-10", "Lakeside Park", 5000.0,
                      "KS", "Boys 5k Run CC JV", "timer", "test-timing", 4)]
    res = [r for s, r in wrote if "INSERT INTO results " in s][0]
    assert [r[3] for r in res] == ["timer", "timer"] and all(r[2] is None for r in res), "no person: 04a links"
    assert res[1][12] == "Unknown" and res[1][13] is None and res[1][16] == "DNF"
    assert any("UPDATE results_ext" in s for s, _r in wrote)
    assert any("status = 'promoted'" in s for s, _p in cur.sql)
    races = [s for s, _p in cur.sql if "FROM races_ext" in s][0]
    assert "twin_source IS NULL" in races, "a feed's twin race is never promoted"


def test_only_cleared_meets_are_promoted():
    assert PR.PROMOTABLE == ("new", "partial")


# ------------------------------------------------------------ linking

def test_the_upload_linker_links_timer_rows_by_the_same_rules():
    import link_uploads as LU
    sql = LU.upRowsSql("AND x", "AND y", LU.TIMER)
    assert "r.source = 'timer' AND r.person_id IS NULL AND x" in sql
    assert "r.meet_id AS upload_id" in sql and "m.source = r.source" in sql
    assert LU.upRowsSql().count("r.source = 'upload'") == 2, "uploads unchanged"
    assert (LU.TIMER.rule, LU.TIMER.rule_mint, LU.TIMER.report) == ("timer", "timer_mint", "timer_link_report")
    assert LU.envKey(LU.TIMER) == "XCP_LINK_TIMERS" and LU.envKey(LU.UPLOAD) == "XCP_LINK_UPLOADS"


def test_timer_undo_takes_back_only_timer_rules():
    import link_uploads as LU
    cur = Cur([])
    LU.undo(cur, "all", LU.TIMER)
    assert cur.sql[-1][1] == (["timer", "timer_mint"],)


def test_pass_4_runs_the_timer_rows_too():
    src = read("scripts", "link_tfrrs_rows.py")
    assert "LU.run(conn, a.apply, feed=LU.TIMER)" in src and "XCP_LINK_TIMERS" in src
    assert '"timer", "timer_mint"' in src


def test_the_rest_of_the_pipeline_reads_timer_rows_like_uploads():
    assert "FLAGGED_SOURCES = (\"upload\", \"timer\")" in read("engine", "twin_flag.py")
    assert "r.source IN ('upload', 'timer') AND m.source = r.source" in read("engine", "person_gender.py")
    bn = read("backfill", "backfill_normalize.py")
    assert "WHERE source IN ('upload', 'timer')" in bn and 'row[_SRC] in ("upload", "timer")' in bn


# ------------------------------------------------------------ the job's guards

def test_no_host_is_off_limits_by_name_now():
    # owner, 2026-11-10: these told him scraping is fine (robots.txt is still read by sources.fetch)
    for u in ("https://www.pttiming.com/r/1", "https://results.itsyourrace.com/x", "https://eccathletics.org/r",
              "https://leonetiming.com/a", "https://timerhub.com/b", "https://results.tfmeetpro.com/M/x/"):
        assert not registry.offLimits(u), u


def test_a_bot_challenge_is_reported_not_parsed():
    assert T._isChallenge(b"<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'>")
    assert not T._isChallenge(b"<html><pre>Hy-Tek's MEET MANAGER</pre>")


def test_the_adapter_from_the_url_or_the_page():
    assert T.kindOf("https://results.tfmeetpro.com/B_B/Meet/index.html") == "meetpro"
    assert T.kindOf("https://timer.example/x/", b"<title>Meet | MeetPro by DirectAthletics</title>") == "meetpro"
    assert T.kindOf("https://results.yentiming.com/2026/cross/x/X.html") == "hytek"


def test_the_fetcher_says_who_it_is():
    import sources.fetch as F
    assert "chrome" in F.USER_AGENT.lower() and "racecast" not in F.USER_AGENT.lower()
    assert F.ROBOTS_AGENT == "racecast-results"


# ------------------------------------------------------------ TFRRS high school sites

INDEX = """<table><tbody>
        <tr>
          <td>10/25/26</td>
          <td><a data-turbo-frame="_top" data-turbo="false" href="/results/xc/28712/Lakeside_Ranch">Lakeside&#39;s Ranch</a></td>
          <td>Cross Country</td>
        </tr>
        <tr> <td>03/19-20/26</td> <td><a data-turbo="false" href="/results/95578/County_Indoor">County Indoor</a></td> <td>Track &amp; Field</td> </tr>
</tbody></table>
<a class="page-link" href="/results_search.html?page=2">2</a><a class="page-link" href="/results_search.html?page=231">231</a>"""


def test_the_state_site_index_reads_ids_sports_and_first_days():
    rows = HS.parseIndexPage(INDEX, "fl.tfrrs.org")
    assert rows == [
        {"host": "fl.tfrrs.org", "meet_id": 28712, "sport": "XC", "date": "2026-10-25", "name": "Lakeside's Ranch",
         "url": "https://fl.tfrrs.org/results/xc/28712/Lakeside_Ranch"},
        {"host": "fl.tfrrs.org", "meet_id": 95578, "sport": "TF", "date": "2026-03-19", "name": "County Indoor",
         "url": "https://fl.tfrrs.org/results/95578/County_Indoor"}]
    assert HS.lastPage(INDEX) == 231 and HS.lastPage("") == 1


def test_queueing_resets_a_meet_unless_a_scrape_holds_it():
    import inspect
    src = inspect.getsource(HS.queueMissing)
    assert "ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0" in src
    assert "WHERE meet_queue.scraped <> 3" in src
    assert "if not a.apply" in inspect.getsource(HS.cmdQueue)
    nightly = read("deploy", "nightly_update.sh")
    assert nightly.index("sources.tfrrs_hs refresh") < nightly.index("scrape scrape_tfrrs"), \
        "the newest state-site meets are queued before the TFRRS scrape runs"


# ------------------------------------------------------------ high school TFRRS rows -> careers

def _hs(rid, ident, name, school, grade, date="2026-10-17", meet=28712):
    import link_uploads as LU
    return LU.UpRow(rid, "XC", date, name, school, grade, "M", meet, 1, ident)


def _career(pid, name, school, grade, date="2026-09-20"):
    import link_uploads as LU
    return LU.Person(pid, name, "M", [LU.Row(date, "XC", grade, school, "hs")])


def test_a_high_schoolers_tfrrs_id_joins_the_one_career_at_the_school():
    import link_tfrrs_hs as LH
    rows = [_hs(-11, "tfrrs:501", "Kyle Kapok", "Lakeside", "10")]
    idents, _v = LH.judgeIdentities(rows, {"kyle kapok": [_career(1011, "Kyle Kapok", "Lakeside", "10")]})
    assert idents == {"tfrrs:501": (1011, "match")}


def test_its_own_minted_person_is_not_a_second_namesake():
    import link_tfrrs_hs as LH
    rows = [_hs(-12, "tfrrs:502", "Liam Linden", "Northfield", "9")]
    by_key = {"liam linden": [_career(1012, "Liam Linden", "Northfield", "9"),
                              _career(1_000_000_502, "Liam Linden", "Northfield", "9", "2026-10-17")]}
    assert LH.judgeIdentities(rows, by_key)[0]["tfrrs:502"] == (1012, "match")
    assert LH.mintedPid("directathletics:7") == 1_500_000_007


def test_one_refused_row_refuses_the_whole_id():
    import link_tfrrs_hs as LH
    rows = [_hs(-11, "tfrrs:501", "Kyle Kapok", "Lakeside", "10"),
            _hs(-21, "tfrrs:501", "Kyle Kapok", "Lakeside", "10", date="2026-09-20", meet=1)]
    idents, _v = LH.judgeIdentities(rows, {"kyle kapok": [_career(1011, "Kyle Kapok", "Lakeside", "10")]})
    target, why = idents["tfrrs:501"]
    assert target is None and "same cross country day" in why, "a race on the career's own day is two runners"


def test_no_career_is_left_for_pass_3_to_mint():
    import link_tfrrs_hs as LH
    idents, _v = LH.judgeIdentities([_hs(-13, "tfrrs:503", "Zack Zebra", "Lakeside", "11")], {})
    assert idents["tfrrs:503"][0] is None


def test_the_hs_rows_sql_reads_grades_as_high_school_and_moves_whole_ids():
    import inspect
    import link_tfrrs_hs as LH
    sql = LH.rowsSql("AND x", "AND y", recent=True)
    assert "JOIN   tfrrs_hs_meets h ON h.meet_id = r.meet_id AND h.sport = 'XC'" in sql
    assert "WHEN 'JR' THEN '11'" in sql and "r.person_id IS NULL OR r.person_id = (CASE" in sql
    assert "FROM th_recent" in sql and "AND x" in sql and "AND y" in sql
    w = inspect.getsource(LH.write)
    assert "m.native_id = r.native_id" in w and "COALESCE(r.person_id, 0)" in w
    src = read("scripts", "link_tfrrs_rows.py")
    assert src.index("import link_tfrrs_hs as LH") < src.index("n = mint(cur, a.apply, a.mint_seasons)"), \
        "pass 2b runs before pass 3 mints"
    assert "XCP_LINK_TFRRS_HS_DAYS" in src and '"tfrrs_hs"' in src


# ------------------------------------------------------------ the nightly run's memory (2026-11-10)

class _Store:
    def meta(self, url):
        return {}


class _OneDayOld:
    """A store holding the page as it stood on the meet's own day (fetched
    a day ago), and a site that has since posted the boys' race too."""

    def __init__(self, day_one, now):
        self.store, self.asked = _Store(), []
        self.day_one, self.now = day_one.encode(), now.encode()

    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        self.asked.append(max_age)
        if max_age is not None and max_age > 86400:      # a day-old copy is fresh enough
            return Fetched(url, 0, self.day_one, False, "fresh in store")
        return Fetched(url, 200, self.now, True, "")


def _lakeside(on):
    text = read("tests", "fixtures", "hytek", "mm_xc_lakeside.txt").replace("10/4/2025", on)
    girls = text[:text.index("Event 2")] if "Event 2" in text else text
    return girls, text


def test_a_meet_from_the_last_few_days_is_read_fresh_not_from_its_day_one_copy():
    import asyncio
    import datetime as dt
    y = dt.date.today() - dt.timedelta(days=1)
    day_one, now = _lakeside(f"{y.month}/{y.day}/{y.year}")
    assert day_one != now, "the fixture has a second race to post later"
    f = _OneDayOld(day_one, now)
    lm, _why = asyncio.run(T.readMeet(f, "lincoln-timing", "https://t.example/lakeside.htm"))
    assert f.asked[0] == T.MAX_AGE and T.RECENT_AGE in f.asked
    assert len(lm.races) == len(L.fromHytek(hytek.parseReport(now), "x", "u").races) > 1


def test_an_old_meet_still_comes_from_the_store():
    import asyncio
    day_one, now = _lakeside("10/4/2025")
    f = _OneDayOld(day_one, now)
    lm, _why = asyncio.run(T.readMeet(f, "lincoln-timing", "https://t.example/lakeside.htm"))
    assert set(f.asked) == {T.MAX_AGE} and len(lm.races) == 1


def test_a_page_asked_again_after_it_did_not_land_is_read_fresh():
    import asyncio
    day_one, now = _lakeside("10/4/2025")
    f = _OneDayOld(day_one, now)
    asyncio.run(T.readMeet(f, "lincoln-timing", "https://t.example/lakeside.htm", retried=True))
    assert set(f.asked) == {T.RETRY_AGE}


def test_every_page_offered_gets_an_outcome():
    import datetime as dt
    today = dt.date(2026, 11, 10)
    lm = L.fromHytek(hytek.parseReport(read("tests", "fixtures", "hytek", "mm_xc_lakeside.txt")), "t", "u")
    assert T._outcome(lm, "hytek", today) == "landed"
    assert T._outcome(None, "HTTP 404", today) == "not parsed: HTTP 404"
    lm.date = "2026-11-10"
    assert T._outcome(lm, "hytek", today) == "waits a day"
    lm.date = None
    assert T._outcome(lm, "hytek", today) == "no date"
    lm.races = []
    assert T._outcome(lm, "hytek", today) == "not parsed: no individual races"
    assert T._recent("2026-11-01", today) and not T._recent("2026-10-30", today)
    assert not T._recent(None, today)


def test_the_ext_seen_table_is_in_the_landing_ddl():
    assert "CREATE TABLE IF NOT EXISTS ext_seen" in L.DDL


def test_kilometers_are_read_before_the_miles_in_brackets():
    # race titles as printed on Heartland Timing's Clearwater Invitational and
    # Y Timing's 3A Region 1 XC 2026 reports (2026-11-10)
    assert hytek.metersOf("WOMEN (HS Open ) • 5 Kilometers (3.11 Miles)") == 5000
    assert hytek.metersOf("MEN • 3.2 Kilometers (1.99 Miles) 3A-1") == 3200
    assert hytek.metersOf("MEN (8th Grade) • 2 Miles (3.22 Kilometers)") == 3218.7
    assert hytek.metersOf("5k Run CC Varsity") == 5000


def test_a_class_year_becomes_the_grade_on_the_meets_day():
    # iResultsLIVE prints "Year 2028"; the site audit found grade "2010"
    assert L.gradeFromClassYear("2028", "2026-10-17") == "11"
    assert L.gradeFromClassYear("2027", "2027-03-14") == "12"        # spring: the same school year
    assert L.gradeFromClassYear("2031", "2026-09-12") == "8"         # a middle schooler
    assert L.gradeFromClassYear("2040", "2026-09-12") == ""          # no grade, never a guess
    assert L.gradeFromClassYear("2028", "2026-10-17", college=True) == "JR"     # = an 11th grader
    assert L.gradeFromClassYear("2030", "2026-10-17", college=True) == "FR"
    assert L.gradeFromClassYear("2027", "2026-10-17", college=True) == "SR"
    assert L.gradeFromClassYear("11", "2026-10-17") == "11"
    assert L.gradeFromClassYear("JR", "2026-10-17", college=True) == "JR"


def test_a_race_of_college_teams_reads_class_years_as_college_classes():
    rows = [hytek.Row(i, "L", "F", "2030", t, 1500.0 + i, "")
            for i, t in enumerate(["Utah State University", "Weber State University", "Boise State Univ.",
                                   "Snow College"], 1)]
    lm = L.fromHytek(hytek.Meet(name="Steve Reeder Invite", date="2026-09-25",
                                events=[hytek.Event(1, "F", "Women 6k", 6000.0, rows=rows)]), "t", "u")
    assert {r.grade for r in lm.races[0].rows} == {"FR"}


def test_a_plain_fixed_width_report_is_read_when_no_hytek_layout_fits():
    # T&H Timing's 2026 PCC boys varsity PDF, as pdftotext gives it: "Place
    # Bib Name Year Team Name ... Clock Time Pace", the times under "Clock"
    text = read("tests", "fixtures", "timers", "th_2026_pcc_boys_varsity.txt")
    m = T._fixedWidth(text)
    (e,) = m.events
    assert e.gender == "M" and len(e.rows) == 47
    r = e.rows[0]
    assert (r.place, r.first, r.last, r.grade, r.school, r.seconds) == (1, "Willy", "Wilks", "10",
                                                                      "Washington Township", 1027.56)
    assert m.date == ""        # this PDF prints no date: it is not landed on a guess
