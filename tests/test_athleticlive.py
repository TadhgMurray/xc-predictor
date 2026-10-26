"""AthleticLIVE's track results in athletic.net's shape (sources/athleticlive.py):
a meet's anet id, each runner's anet id, the meet's anet slots when we have
them, and nothing landed that could make the same race twice.

    XCP_TWIN_TEST_DSN="host=... dbname=..." runs the SQL half too
"""
import asyncio
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

from sources import athleticlive as A                         # noqa: E402
from sources.athleticlive import Slot                           # noqa: E402

# the shapes AthleticLIVE serves (probed 2026-10-26, meet 65672 / anet 642474)
MEET = {"ani": 642474, "anis": [{"ani": 642474, "n": ""}], "md": ["2026-04-02"],
        "sdy": "2026-04-02", "n": "Hueneme / Santa Paula at Channel Islands",
        "lo": "Channel Islands High School, Raiders Way, Oxnard, CA, USA",
        "ls": "Oxnard, CA", "lsa": "California", "o": "outdoor",
        "mvs": ["highSchool"], "li": "speedtiming", "ua": "2026-05-21T14:56:52Z"}


def _ev(i, ab, gl, na, peg="Distance", ec="Individual", runm="Finals"):
    return {"ab": ab, "gl": gl, "na": na, "i": i, "ec": ec, "peg": peg,
            "egs": ["Field"] if peg == "Field" else [peg], "runm": runm, "nu": i % 100,
            "xc": False, "n": f"{gl} {ab} {na}"}


SUMMARY = {
    "Individual-2402879": _ev(2402879, "1600m", "Boys", "Varsity"),
    "Individual-2402871": _ev(2402871, "1600m", "Boys", "Frosh Soph"),
    "Individual-2402928": _ev(2402928, "LJ", "Girls", "Varsity", peg="Field"),
    "Relay-410219": _ev(410219, "4x100mR", "Boys", "Varsity", peg="Relay", ec="Relay"),
}


def _ath(ani, fn, ln, y, g="Male", team_ani=1611):
    return {"i": ani + 1, "n": f"{fn} {ln}", "fn": fn, "l": ln, "y": y, "g": g,
            "ani": ani, "t": {"i": 1535917, "n": "Channel Islands", "ani": team_ani}}


MILE = {"_source": {"dv": {"i": 96634, "n": "Varsity"}, "r": [
    {"i": 37183136, "p": "1", "m": "4:25.96", "im": 265960, "w": "", "hn": 2,
     "a": _ath(30578388, "Uriel", "Texis", "12")},
    {"i": 37183137, "p": "2", "m": "4:36.10", "im": 276100, "w": "", "hn": 2,
     "a": _ath(30475359, "Ann", "Other", "12")},
    {"i": 37183138, "p": "--", "m": "DNS", "im": 0, "w": "", "hn": 1,
     "a": _ath(21940232, "Not", "Started", "12")},
    {"i": 37183139, "p": "3", "m": "4:40.00", "im": 280000, "w": "", "hn": 1,
     "a": {"i": 9, "fn": "No", "l": "Id", "y": "11", "g": "Male", "t": {}}},
]}}
LJ = {"_source": {"dv": {"i": 96634}, "r": [
    {"i": 37221659, "p": "1", "m": "13-08.75", "im": 4180300, "w": "+1.2", "hn": 1,
     "a": _ath(27377960, "Jo", "Jump", "10", "Female")},
    {"i": 37221660, "p": "--", "m": "FOUL", "im": 0, "w": "", "hn": 1,
     "a": _ath(27377961, "Fo", "Ul", "10", "Female")},
]}}
RELAY = {"_source": {"dv": {"i": 96634}, "rts": [
    {"i": 2355135, "p": "1", "m": "45.01", "im": 45010, "w": "", "hn": 1,
     "t": {"n": "Santa Paula", "ani": 1612},
     "rm": [{"to": 2, "a": {"n": "B Two"}}, {"to": 1, "a": {"n": "A One"}},
            {"to": 4, "a": {"n": "D Four"}}, {"to": 3, "a": {"n": "C Three"}}]},
]}}


def test_a_meet_names_its_athletic_net_meet_and_its_state():
    m = A.parseMeet("65672", MEET)
    assert m["alive_id"] == 65672 and m["anet_ids"] == [642474]
    assert m["sport"] == "TF" and m["indoor"] is False and m["state"] == "CA"
    assert m["meet_date"] == "2026-04-02" and m["timer"] == "speedtiming"
    assert A.parseMeet(1, dict(MEET, o="xc", ls="Somewhere", lsa="New York"))["state"] == "NY"
    assert A.parseMeet(1, dict(MEET, o="xc"))["sport"] == "XC"
    assert A.parseMeet(1, None) is None
    assert A.parseMeet(1, dict(MEET, ani=None, anis=[]))["anet_ids"] == []


def test_events_and_their_spellings():
    evs = {e["id"]: e for e in A.parseSummary(SUMMARY)}
    assert evs[410219]["kind"] == "rel" and evs[2402879]["kind"] == "ind"
    assert evs[2402928]["field"] and not evs[2402879]["field"]
    assert evs[2402879]["gender"] == "m" and evs[2402928]["gender"] == "f"
    assert evs[2402879]["round"] == "finals"
    for a, b in (("4x100mR", "4x100m"), ("4x400", "4x400m"), ("SP", "shot"),
                 ("DT", "discus"), ("110mH", "110mh"), ("1600m", "1600m"), ("HJ", "hj")):
        assert A.canonShort(a) == A.canonShort(b), (a, b)
    assert A.canonShort("400m") != A.canonShort("4x400m")


def test_an_event_takes_anets_slot_by_event_gender_and_division():
    evs = A.parseSummary(SUMMARY)
    slots = [Slot(52, 111, "1600m", "m", "Varsity"), Slot(52, 112, "1600m", "m", "Frosh Soph"),
             Slot(35, 111, "lj", "f", "Varsity"), Slot(7, 111, "4x100m", "m", "Varsity")]
    got, why = A.matchSlots(evs, slots)
    assert got[2402879].div_id == 111 and got[2402871].div_id == 112
    assert got[2402928].event_id == 35 and got[410219].event_id == 7
    assert not why


def test_two_divisions_never_share_one_slot():
    """anet has one 1600 slot named neither; two AthleticLIVE divisions want
    it -- that is two races in one, so neither lands."""
    evs = A.parseSummary(SUMMARY)
    got, why = A.matchSlots(evs, [Slot(52, 111, "1600m", "m", "Open")])
    assert 2402879 not in got and 2402871 not in got
    assert why[2402879] == why[2402871] == "shares a slot with another division"
    # one division, an unnamed slot: it lands
    got, _ = A.matchSlots([e for e in evs if e["id"] == 2402879],
                          [Slot(52, 111, "1600m", "m", "Open")])
    assert got[2402879].div_id == 111
    # a slot by name wins over the loose one
    got, why = A.matchSlots(evs, [Slot(52, 111, "1600m", "m", "Varsity")])
    assert got[2402879].div_id == 111 and 2402871 not in got


def test_a_slot_without_a_gender_never_takes_both():
    evs = [dict(e, ab="5000m") for e in A.parseSummary(SUMMARY) if e["id"] in (2402879,)]
    evs.append(dict(evs[0], id=1, gender="f"))
    got, why = A.matchSlots(evs, [Slot(999, 111, "5000m", None, "Varsity")])
    assert not got and set(why.values()) == {"one slot, both genders"}


def test_rows_in_athletic_nets_own_shape():
    evs = {e["id"]: e for e in A.parseSummary(SUMMARY)}
    rows, ath, skipped = A.resultRows(MILE, evs[2402879])
    by = {r["IDResult"]: r for r in rows}
    first = by[A.RESULT_BASE + 37183136]
    assert first["AthleteID"] == 30578388 and first["SortInt"] == 265960
    assert first["Place"] == 1 and first["Grade"] == "12" and first["Gender"] == "M"
    assert first["TeamID"] == 1611 and first["TeamName"] == "Channel Islands"
    assert first["Round"] == "finals" and first["Heat"] == 2 and first["Result"] is None
    dns = by[A.RESULT_BASE + 37183138]
    assert dns["SortInt"] is None and dns["Result"] == "DNS" and dns["Place"] is None
    # a runner with no athletic.net id is kept under a stand-in (minus
    # AthleticLIVE's own id) for resolveIds / holdUnknown
    assert skipped["no anet athlete id"] == 1 and len(rows) == 4
    assert by[A.RESULT_BASE + 37183139]["AthleteID"] == -9
    assert {a["AthleteID"] for a in ath} == {30578388, 30475359, 21940232, -9}
    # field: the mark as printed; a foul is no mark, so it is left out
    rows, _a, skipped = A.resultRows(LJ, evs[2402928])
    assert [r["Result"] for r in rows] == ["13-08.75"] and rows[0]["SortInt"] is None
    assert rows[0]["Wind"] == "+1.2" and skipped["no mark"] == 1
    # relay: no athlete, legs in order on the name
    rows, ath, _s = A.resultRows(RELAY, evs[410219])
    assert ath == [] and "AthleteID" not in rows[0]
    assert rows[0]["FirstName"] == "A One<BR>B Two<BR>C Three<BR>D Four"
    assert rows[0]["SortInt"] == 45010 and rows[0]["TeamID"] == 1612


def test_the_rows_read_back_as_anet_would_store_them():
    """What saveResultsTFBulk makes of a row: seconds from SortInt, a status
    in its own column, the feed's name from the legs."""
    from database import _feedName, _statusOf, _timeFromSortInt
    evs = {e["id"]: e for e in A.parseSummary(SUMMARY)}
    rows, _a, _s = A.resultRows(MILE, evs[2402879])
    assert _timeFromSortInt(rows[0]["SortInt"]) == 265.96
    assert _statusOf(rows[0]) is None and _statusOf(rows[2]) == "DNS"
    rel, _a, _s = A.resultRows(RELAY, evs[410219])
    assert _feedName(rel[0], is_relay=True) == "A One, B Two, C Three, D Four"


def test_plan_on_anet_slots_and_on_its_own():
    evs = A.parseSummary(SUMMARY)
    blobs = {2402879: MILE, 2402928: LJ, 410219: RELAY}
    alive = A.parseMeet(65672, MEET)
    slots = [Slot(52, 111, "1600m", "m", "Varsity"), Slot(35, 111, "lj", "f", "Varsity"),
             Slot(7, 111, "4x100m", "m", "Varsity")]
    p = A.plan(642474, alive, evs, blobs, slots, None)
    assert not p["own"]
    keys = {(t[2], t[3], t[4]) for t in p["results_to_save"]}
    assert keys == {(111, 52, "1600m"), (111, 35, "lj"), (111, 7, "4x100m")}
    assert {t[5] for t in p["results_to_save"] if t[3] == 7} == {1}        # is_relay
    assert {t[7] for t in p["results_to_save"] if t[3] == 35} == {1}       # is_field
    assert p["unmatched"][2402871] == "shares a slot with another division"
    assert p["meet_info"]["ID"] == 642474 and p["meet_info"]["MeetDate"] == "2026-04-02"
    assert p["meet_info"]["Location"]["Name"] == "Channel Islands High School"
    assert p["meet_info"]["LevelMask"] == 4
    # no anet slots: AthleticLIVE's divisions, anet's catalogue events
    p = A.plan(642474, alive, evs, blobs, [], None)
    assert p["own"]
    mile = [t for t in p["results_to_save"] if t[4] == "1600m"]
    assert {t[2] for t in mile} == {A.DIV_BASE + 96634} and {t[3] for t in mile} == {52}
    assert {t[3] for t in p["results_to_save"] if t[5]} == {7}
    m2s = {(m[1], m[2]): m for m in p["meets_to_save"]}
    assert m2s[(A.DIV_BASE + 96634, 52)][4] == 1600 and m2s[(A.DIV_BASE + 96634, 35)][4] is None


def test_an_event_anets_catalogue_lacks_keys_the_same_every_time():
    ev = dict(A.parseSummary(SUMMARY)[0], ab="5000m", gender="m")
    a = A.ownSlot(ev, 5, {})
    assert a == A.ownSlot(ev, 5, {}) and a.event_id >= A.EVENT_BASE
    assert a.event_id != A.ownSlot(dict(ev, gender="f"), 5, {}).event_id


class _Fetched:
    def __init__(self, body):
        self.body = json.dumps(body).encode() if body is not None else None


class _Fetcher:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    async def fetch(self, url, max_age=None):
        self.asked.append(url)
        for key, body in self.pages.items():
            if key in url:
                return _Fetched(body)
        return _Fetched(None)

    async def fetchAll(self, urls, max_age=None):
        return [await self.fetch(u, max_age) for u in urls]


def test_fetch_meet_reads_the_summary_then_each_event():
    f = _Fetcher({"meet_65672/event_summary": SUMMARY, "ind_res_list/_doc/2402879": MILE,
                  "rel_res_list/_doc/410219": RELAY})
    events, blobs, note = asyncio.run(A.fetchMeet(f, 65672, "2026-04-02"))
    assert len(events) == 4 and set(blobs) == {2402879, 410219}
    assert any("rel_res_list/_doc/410219" in u for u in f.asked)
    assert all(u.startswith((A.FIREBASE, A.BLOB)) for u in f.asked)


def test_discovery_url_is_one_robots_allows():
    from sources.fetch import RobotsRules
    fb = RobotsRules("User-agent: *\nAllow: /*.json$\nAllow: /*.json?*$\nDisallow: /\n")
    assert fb.can_fetch("racecast-results", A.listUrl(65000))
    assert fb.can_fetch("racecast-results", A.summaryUrl(65672))
    assert not fb.can_fetch("racecast-results", A.FIREBASE + "/")


def test_robots_wildcards_longest_rule_wins():
    from sources.fetch import RobotsRules
    r = RobotsRules("User-agent: *\nDisallow: /a/\nAllow: /a/*.html$\nCrawl-delay: 3\n"
                    "\nUser-agent: other\nDisallow: /\n")
    assert r.can_fetch("racecast-results", "https://h/a/x.html")
    assert not r.can_fetch("racecast-results", "https://h/a/x.htmlz")
    assert not r.can_fetch("racecast-results", "https://h/a/x.pdf")
    assert r.can_fetch("racecast-results", "https://h/b") and r.crawl_delay("racecast-results") == 3
    mine = RobotsRules("User-agent: *\nDisallow: /\n\nUser-agent: racecast\nAllow: /\n")
    assert mine.can_fetch("racecast-results", "https://h/x")      # our group replaces "*"
    assert RobotsRules("User-agent: *\nDisallow:\n").can_fetch("racecast-results", "https://h/x")


def test_alive_meets_sql():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,"
                    " event_short text, division text)")
        cur.execute("CREATE TEMP TABLE results_tf (result_id bigint, meet_id bigint, source text)")
        cur.execute("DROP TABLE IF EXISTS alive_meets")
        A.initTables(cur)
        f = _Fetcher({'startAt="0"': {"65672": MEET, "65673": dict(MEET, ani=642475, anis=[], o="xc"),
                                      "id": {"x": 1}}})
        n, pages = asyncio.run(A.discover(cur, f, start=0))
        assert (n, pages) == (2, 1)
        assert A.aliveFor(cur, 642474)["alive_id"] == 65672
        assert A.aliveFor(cur, 642475) is None and A.aliveFor(cur, 1) is None
        assert A.aliveFor(cur, 642475, sport="XC")["alive_id"] == 65673
        cur.execute("INSERT INTO meets_tf VALUES (642474, 111, 52, '1600m', 'Varsity'),"
                    " (642474, 111, 999999, 'odd', NULL)")
        slots = {s.event_id: s for s in A.anetSlots(cur, 642474)}
        assert slots[52].gender == "m" and slots[999999].gender is None
        assert not A.anetHasResults(cur, 642474)
        cur.execute("INSERT INTO results_tf VALUES (%s, 642474, 'anet')", (A.RESULT_BASE + 5,))
        assert not A.anetHasResults(cur, 642474)          # our own row is not anet's
        cur.execute("INSERT INTO results_tf VALUES (123, 642474, 'anet')")
        assert A.anetHasResults(cur, 642474)
        A.noteLanded(cur, 65672, 12, "test")
        assert A.aliveFor(cur, 642474)["landed_n"] == 12
    finally:
        conn.rollback()
        conn.close()


def test_a_page_firebase_sends_as_a_list_is_read_by_position():
    """The first pages come back as a JSON array (ids 1, 2, 3 ...): position
    is the id. Read as 'no page', the first server run found 0 meets."""
    assert A.keyedPage([None, MEET, None, dict(MEET, ani=7)]) == {"1": MEET, "3": dict(MEET, ani=7)}
    assert A.keyedPage({"65672": MEET}) == {"65672": MEET}
    assert A.keyedPage(None) == {}

    class _Cur:
        def __init__(self):
            self.rows = []

        def execute(self, sql, row=None):
            self.rows.append(row)

    full = [None] + [dict(MEET, ani=i) for i in range(1, A.PAGE + 1)]
    f = _Fetcher({'startAt="0"': full, 'startAt="%d"' % (A.PAGE + 1): {"9999": MEET}})
    cur = _Cur()
    n, pages = asyncio.run(A.discover(cur, f, start=0))
    assert (n, pages) == (A.PAGE + 1, 2)
    assert cur.rows[0]["alive_id"] == 1 and cur.rows[-1]["alive_id"] == 9999



def test_one_fetcher_serves_meet_after_meet():
    """check / land fetch meet after meet with one fetcher. Each meet on its
    own asyncio.run raised 'bound to a different event loop' on the server's
    second meet (2026-10-26)."""
    from sources.fetch import Fetcher, HostLimiter, RawStore
    import tempfile

    class _Resp:
        status_code, headers = 200, {}

        def __init__(self, body):
            self.content = json.dumps(body).encode()

    class _Allow:
        def get(self, url):
            return True, None

    pages = {"event_summary": SUMMARY, "2402879": MILE, "2402928": LJ, "410219": RELAY}

    def getter(url, headers):
        return _Resp(next((b for k, b in pages.items() if k in url), {}))

    with tempfile.TemporaryDirectory() as d:
        f = Fetcher(RawStore(d), limiter=HostLimiter(interval=0.0), robots=_Allow(),
                    getter=getter)
        for alive_id in (65672, 65673, 65674):
            events, blobs, _ = A._run(A.fetchMeet(f, alive_id, None))
            assert len(events) == 4 and len(blobs) == 4


def test_check_says_why_rows_differ():
    evs = A.parseSummary(SUMMARY)
    slots = [Slot(52, 111, "1600m", "m", "Varsity"), Slot(52, 112, "1600m", "m", "Frosh Soph"),
             Slot(35, 111, "lj", "f", "Varsity"), Slot(42, 111, "60m", "m", "Varsity")]
    p = A.plan(642474, A.parseMeet(65672, MEET), evs, {2402879: MILE, 2402928: LJ}, slots, None)
    # (result_id, athlete_id, div_id, event_id, time_seconds, mark, place,
    #  is_relay, event_short, status, name, person_id)
    have = [
        (1, 30578388, 111, 52, 265.96, None, 1, 0, "1600m", None, None, 30578388),
        (2, 30475359, 111, 52, 276.2, None, 2, 0, "1600m", None, None, 30475359),   # mark differs
        (3, 27377960, 112, 52, 300.0, None, 1, 0, "1600m", None, None, 27377960),   # FS: not landed
        (4, 11111111, 111, 52, 290.0, None, 4, 0, "1600m", None, None, 11111111),   # absent
        (5, 22222222, 111, 7, 44.0, None, 1, 1, "4x100m", None, None, None),        # relay: ignored
        (6, 99999999, 111, 35, None, "13' 8.75", 1, 0, "lj", None, None, 77),       # LJ winner, other id
        (7, 33333333, 111, 42, 7.5, None, 1, 0, "60m", None, None, 33333333),        # race not landed
    ]
    known = {27377960: (77, "Jo Jump"), 30578388: (30578388, "Uriel Texis")}
    c, ex = A.compareMeet(have, p["results_to_save"], known)
    assert c["anet rows"] == 6 and c["same runner+race"] == 2 and c["same mark"] == 1
    assert c["same place"] == 2
    assert c["same race+mark, other id, same person"] == 1           # 13-08.75 is 4.18 m
    assert c["anet: race not landed"] == 2 and c["anet: runner not in alive"] == 1
    assert c["anet: runner in another race"] == 0
    assert c["alive only, no mark (DNS/SCR...)"] == 1                 # the DNS
    assert "276.2" in ex["same runner and race, different mark"][0]
    assert "11111111" in ex["anet runner AthleticLIVE does not have"][0]
    assert "Uriel Texis" not in str(ex)                                # matched rows are not examples


def test_field_marks_compare_across_units():
    assert abs(A.markMetres("78' 0") - 23.7744) < 1e-6
    assert abs(A.markMetres("13-08.75") - A.markMetres("4.18m")) < 0.015
    assert A.markMetres("23.77m") == 23.77 and A.markMetres("FOUL") is None
    assert A.markMetres("17-15") is None
    assert A._sameMark({"Result": "23.77m"}, (0, 1, 1, 1, None, "78' 0", 1, 0, "dt", None))
    assert not A._sameMark({"Result": "23.17m"}, (0, 1, 1, 1, None, "78' 0", 1, 0, "dt", None))


def test_learned_event_genders():
    assert A.eventGender(95, 2) == "m" and A.eventGender(1, 40) == "f"
    assert A.eventGender(30, 30) is None and A.eventGender(3, 0) is None


def test_unlanded_lines_survive_a_slot_with_no_event_name():
    """The server's second check died sorting a slot whose event_short is
    NULL next to named ones."""
    evs = A.parseSummary(SUMMARY)
    slots = [Slot(84, 111, None, "m", None), Slot(52, 111, "1600m", "m", None),
             Slot(52, 112, "1600m", "m", "Open")]
    p = A.plan(1, A.parseMeet(65672, MEET), evs, {2402879: MILE}, slots, None)
    lines = A._unlandedLines(1, p, slots)
    assert lines and any("ambiguous division" in l or "no anet slot" in l for l in lines)


def test_an_id_we_do_not_have_becomes_ours_when_one_person_fits():
    """AthleticLIVE kept 'Caleb Schulz' 19402627; athletic.net merged him
    into 30054942. One person at his school with his name and gender: ours."""
    ours = [
        (30054942, 30054942, "Caleb", "Schulz", "M", "oprf"),
        (28000001, 30054942, "Caleb", "Schulz", "M", "oprf"),       # an older id, same person
        (30000002, 30000002, "John", "Smith", "M", "oprf"),
        (30000003, 30000003, "John", "Smith", "M", "oprf"),          # two people: no answer
        (30000004, 30000004, "John", "Smith Jr", "M", "oprf"),
        (30000005, 30000005, "Ann", "Lee", "F", "oprf"),
    ]
    alive = [
        {"AthleteID": 19402627, "FirstName": "Caleb", "LastName": "Schulz", "Gender": "M",
         "SchoolName": "OPRF", "SchoolFull": "Oak Park River Forest"},
        {"AthleteID": 1, "FirstName": "John", "LastName": "Smith", "Gender": "M", "SchoolName": "OPRF"},
        {"AthleteID": 2, "FirstName": "Ann", "LastName": "Lee", "Gender": "M", "SchoolName": "OPRF"},
        {"AthleteID": 3, "FirstName": "Ann", "LastName": "Lee", "Gender": "F", "SchoolName": "Elsewhere"},
        {"AthleteID": 4, "FirstName": "John", "LastName": "Smith Jr.", "Gender": "M", "SchoolName": "oprf "},
    ]
    assert A._resolveIds(ours, alive) == {19402627: 30054942, 4: 30000004}


def test_apply_ids_moves_rows_and_merges_athletes():
    p = {"results_to_save": [({"AthleteID": 1},), ({"AthleteID": 2},), ({"AthleteID": 1},)],
         "athletes": [{"AthleteID": 1, "SchoolName": "X"}, {"AthleteID": 9, "SchoolName": "X"}]}
    assert A.applyIds(p, {1: 9}) == 2
    assert [t[0]["AthleteID"] for t in p["results_to_save"]] == [9, 2, 9]
    assert p["athletes"] == [{"AthleteID": 9, "SchoolName": "X"}]


def test_divisions_a_year_apart_still_meet():
    assert A.divWords("13-15 Cadet") == A.divWords("13-14 Cadet") == {"cadet"}
    evs = [dict(e, division="13-15 Cadet") for e in A.parseSummary(SUMMARY) if e["id"] == 2402879]
    slots = [Slot(52, 1, "1600m", "m", "11-12 Minor"), Slot(52, 2, "1600m", "m", "13-14 Cadet"),
             Slot(52, 3, "1600m", "m", "9-10 Novice")]
    got, why = A.matchSlots(evs, slots)
    assert got[2402879].div_id == 2 and not why
    # two alive divisions with one word set onto one slot: neither
    two = evs + [dict(evs[0], id=5, division="15-16 Cadet")]
    got, why = A.matchSlots(two, slots)
    assert not got


def test_high_jump_marks_lose_their_letter():
    hj = dict(A.parseSummary(SUMMARY)[2], ab="HJ", field=True)
    doc = {"_source": {"r": [{"i": 1, "p": "1", "m": "J4-10.00", "im": 1, "hn": 1,
                              "a": _ath(5, "R", "S", "10", "Female")}]}}
    rows, _a, _s = A.resultRows(doc, hj)
    assert rows[0]["Result"] == "4-10.00"


def test_a_name_the_other_way_round_still_finds_its_person():
    ours = [(27242759, 27242759, "Avila", "Ethan", "M", "cedar"),
            (30000002, 30000002, "Thomas", "James", "M", "cedar"),
            (30000003, 30000003, "James", "Thomas", "M", "cedar")]
    alive = [{"AthleteID": 1, "FirstName": "Ethan", "LastName": "Avila", "Gender": "M",
              "SchoolName": "Cedar"},
             # the name as given wins: James Thomas is 30000003, never the swap
             {"AthleteID": 2, "FirstName": "James", "LastName": "Thomas", "Gender": "M",
              "SchoolName": "Cedar"}]
    assert A._resolveIds(ours, alive) == {1: 27242759, 2: 30000003}


def _hold_plan(own):
    evs = A.parseSummary(SUMMARY)
    slots = [] if own else [Slot(52, 111, "1600m", "m", "Varsity"), Slot(35, 111, "lj", "f", "Varsity"),
                            Slot(7, 111, "4x100m", "m", "Varsity")]
    return A.plan(642474, A.parseMeet(65672, MEET), evs,
                  {2402879: MILE, 2402928: LJ, 410219: RELAY}, slots, None)


def test_a_race_with_a_runner_nobody_can_name_waits():
    """The mile has one runner with no athletic.net id. On anet's slots the
    mile waits (the long jump and relay land); on our own, the meet waits."""
    p = _hold_plan(own=False)
    assert A.holdUnknown(p) == 1
    assert {(t[2], t[3]) for t in p["results_to_save"]} == {(111, 35), (111, 7)}
    assert p["held"] == {"races": 1, "runners": 1, "whole meet": False}
    assert all(a["AthleteID"] > 0 for a in p["athletes"])
    p = _hold_plan(own=True)
    assert A.holdUnknown(p) == 1
    assert p["results_to_save"] == [] and p["meets_to_save"] == [] and p["held"]["whole meet"]
    # once our athlete is found for them, nothing waits
    p = _hold_plan(own=True)
    assert A.applyIds(p, {-9: 4242}) == 1 and A.holdUnknown(p) == 0
    assert any(t[0].get("AthleteID") == 4242 for t in p["results_to_save"])

