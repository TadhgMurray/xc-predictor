"""What it takes (racecast/cuts.py, owner 2026-10-05): the season rating it
took to get out of the section, to make the state meet, to finish top 25
and top 8, read off the runners who were actually there.

Pinned here: the mark is a percentile of who was there (never a typed-in
cut), the thin rule is derived from that percentile, a race's division comes
from its own title before its schools, a section's advancers are the
finishers who ran state that season, and the athlete line goes quiet rather
than quoting a thin mark. The SQL half runs against a throwaway Postgres
when XCP_TEST_DSN is set (buildFixture also feeds the screenshots)."""
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import pytest  # noqa: E402

import cuts as C  # noqa: E402

DSN = os.environ.get("XCP_TEST_DSN")


# ------------------------------------------------------------------ #
#  pure pieces
# ------------------------------------------------------------------ #

def test_quantile_is_linear_between_order_statistics():
    xs = [10, 20, 30, 40, 50]
    assert C.quantile(xs, 0.0) == 10
    assert C.quantile(xs, 0.5) == 30
    assert C.quantile(xs, 0.1) == pytest.approx(14.0)
    assert C.quantile([7], 0.1) == 7
    assert C.quantile([], 0.1) is None


def test_thin_threshold_is_derived_from_the_quantile():
    # below 1/q runners the q-quantile is the slowest one or two people
    assert C.THIN_N == round(1 / C.MARK_Q)
    xs = list(range(100, 100 + C.THIN_N - 1))
    assert C.quantile(xs, C.MARK_Q) < xs[1]


def test_mark_is_robust_to_one_odd_qualifier():
    good = [170.0 + i * 0.5 for i in range(40)]
    m1 = C.markOf(good)
    m2 = C.markOf(good + [120.0])          # one mis-linked result
    assert m2["slowest"] == 120.0
    assert abs(m2["mark"] - m1["mark"]) < 1.0
    assert m1["n"] == 40 and m2["n"] == 41


def test_trend_is_points_per_season():
    assert C.trendOf([(2023, 170.0), (2024, 171.0), (2025, 172.0)]) == 1.0
    assert C.trendOf([(2025, 170.0)]) is None
    assert C.trendOf([(2025, 170.0), (2024, None)]) is None


def test_majority_counts_unknowns_against_the_value():
    assert C.majority(["2", "2", None, "1"])[0] == "2"
    v, share, n = C.majority(["2", None, None, None])
    assert v == "2" and share == 0.25 and n == 1
    assert C.majority([None, None]) == (None, 0.0, 0)


def test_meet_level():
    assert C.meetLevel("CIF State XC Championships",
                       [("championship", None), ("state", "CA")]) == ("state", None)
    assert C.meetLevel("CIF-NCS Championships",
                       [("championship", None), ("section", "NCS")]) == ("section", "NCS")
    # an area meet carries the section's word but is not its final
    assert C.meetLevel("NCS Tri-Valley Area Championships",
                       [("section", "NCS"), ("area", "TRI-VALLEY")]) is None
    # a section's state qualifier is a section round, not the state meet
    assert C.meetLevel("MSHSL Section 1AA", [("state", "MN"), ("section", "1")]) == ("section", "1")
    assert C.meetLevel("Some State Qualifier", [("state", "NY")]) is None
    assert C.meetLevel("UIL Region II-5A", [("state", "TX"), ("region", "II")]) == ("region", "II")
    assert C.meetLevel("District 7-3A", [("district", "7")]) is None


def test_title_division_picks_the_rounds_own_token():
    facts = [("state", "CA"), ("state_div", "2")]
    assert C.titleDivision(facts, "state") == "2"
    assert C.titleDivision([("section", "NCS"), ("section_div", "3")], "section") == "3"
    assert C.titleDivision([("state", "CA")], "state") is None


# ------------------------------------------------------------------ #
#  computeMarks on hand-built rows
# ------------------------------------------------------------------ #

def _rows_for(meet, div, year, people, pool="hs_m", units=None, start=1):
    out = []
    for i, (pid, school, race_rating) in enumerate(people):
        u = (units or {}).get(school, {})
        out.append({"meet_id": meet, "source": "anet", "div_id": div,
                    "person_id": pid, "place": start + i,
                    "time_seconds": 900.0 + i, "pool": pool, "year": year,
                    "race_rating": race_rating, "school": school,
                    "state_div": u.get("state_div"), "class": None,
                    "section": u.get("section"),
                    "section_div": u.get("section_div")})
    return out


def _race(meet, div, level, title_div=None, name="State Meet"):
    return {"meet_id": meet, "source": "anet", "div_id": div,
            "meet_name": name, "title": "", "title_div": title_div,
            "level": level, "course_name": "Woodward Park", "distance": 5000,
            "meet_date": "2025-11-29"}


def _small_state():
    """One state final per season (D2 boys, 40 runners) and one section final
    (NCS D2, 60 runners, the fastest 30 of whom go on to state)."""
    races, rows, ratings = [], [], {}
    units = {f"S{i}": {"state_div": "2", "section": "NCS", "section_div": "2"}
             for i in range(10)}
    for year in (2023, 2024, 2025):
        sec_people = []
        for k in range(60):
            pid = year * 1000 + k
            school = f"S{k % 10}"
            r = 200.0 - k + (year - 2023)       # the field speeds up a point a year
            ratings[(pid, "hs_m", year)] = r
            sec_people.append((pid, school, r))
        sm, st = 2000 + year, 1000 + year
        races.append(_race(sm, sm * 10, ("section", "NCS"), "2", "NCS Champs"))
        rows += _rows_for(sm, sm * 10, year, sec_people, units=units)
        # state: the section's top 30 plus 10 from elsewhere (no section row)
        state_people = sec_people[:30]
        for k in range(10):
            pid = year * 1000 + 500 + k
            r = 185.0 - k + (year - 2023)
            ratings[(pid, "hs_m", year)] = r
            state_people.append((pid, "Elsewhere", r))
        state_people.sort(key=lambda p: -p[2])
        races.append(_race(st, st * 10, ("state", None), "2"))
        rows += _rows_for(st, st * 10, year, state_people, units=units)
    return races, rows, ratings


def test_state_marks_are_percentiles_of_who_ran():
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    boys = data["genders"]["boys"]
    assert [c["slug"] for c in boys["divisions"]] == ["2"]
    cell = boys["divisions"][0]
    s = cell["latest"]
    assert s["year"] == 2025 and s["n_finishers"] == 40 and s["n_rated"] == 40
    want = [r for (pid, pool, y), r in ratings.items()
            if y == 2025 and (pid % 1000 < 30 or pid % 1000 >= 500)]
    assert s["lines"]["qualify"]["mark"] == round(C.quantile(want, C.MARK_Q), 1)
    assert s["lines"]["qualify"]["slowest"] == round(min(want), 1)
    # top 25 is the first 25 across the line, their season ratings
    top = sorted(want, reverse=True)[:25]
    assert s["lines"]["top"]["n"] == 25
    assert s["lines"]["top"]["mark"] == round(C.quantile(top, C.MARK_Q), 1)
    assert s["lines"]["top"]["on_day"]["place"] == 25
    assert s["lines"]["podium"]["n"] == 8
    # three seasons, newest first, and the trend is the point a year we built
    assert [x["year"] for x in cell["seasons"]] == [2025, 2024, 2023]
    assert cell["trend"]["qualify"] == pytest.approx(1.0)
    assert not cell["thin"]


def test_section_advancers_are_finishers_who_ran_state():
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    secs = data["genders"]["boys"]["sections"]
    assert len(secs) == 1
    sc = secs[0]
    assert (sc["kind"], sc["unit"], sc["slug"]) == ("section", "NCS", "2")
    assert sc["label"] == "NCS D2"
    assert sc["feeds"] == "2"
    s = sc["latest"]
    assert s["n_finishers"] == 60 and s["n_advanced"] == 30
    assert s["deepest_place"] == 30
    adv = [200.0 - k + 2 for k in range(30)]
    assert s["lines"]["advance"]["mark"] == round(C.quantile(adv, C.MARK_Q), 1)
    assert C.sectionsFeeding(data, "boys", "2") == [sc]


def test_title_beats_the_schools_and_disagreement_is_flagged():
    races, rows, ratings = _small_state()
    # the 2025 state race's schools are all filed in D1 -- a wrong
    # assignment. The title says D2, so the race stays D2, flagged.
    for r in rows:
        if r["meet_id"] == 1000 + 2025:
            r["state_div"] = "1"
    data = C.computeMarks("CA", races, rows, ratings)
    cell = C.findDivision(data, "boys", "2")
    assert cell is not None and C.findDivision(data, "boys", "1") is None
    assert cell["latest"]["agree"] == 0.0
    assert any("another division" in f for f in cell["latest"]["flags"])
    assert cell["thin"]


def test_no_title_and_mixed_schools_is_left_unplaced():
    races, rows, ratings = _small_state()
    for r in races:
        if r["meet_id"] == 1000 + 2025:
            r["title_div"] = None
    for i, r in enumerate(x for x in rows if x["meet_id"] == 1000 + 2025):
        r["state_div"] = ("1", "2", "3")[i % 3]
    data = C.computeMarks("CA", races, rows, ratings)
    assert data["unplaced"] == 1
    assert C.findDivision(data, "boys", "2")["latest"]["year"] == 2024


def test_a_tiny_race_is_thin_and_has_no_place_lines():
    races = [_race(1, 10, ("state", None), "3")]
    people = [(i, "X", 150.0 - i) for i in range(6)]
    rows = _rows_for(1, 10, 2025, people)
    ratings = {(i, "hs_m", 2025): 150.0 - i for i in range(6)}
    data = C.computeMarks("CA", races, rows, ratings)
    cell = C.findDivision(data, "boys", "3")
    assert cell["thin"]
    assert "one season on record" in cell["thin_reasons"]
    assert any(f.startswith("only 6 rated") for f in cell["thin_reasons"])
    assert cell["latest"]["lines"]["top"] is None       # 6 runners: top 25 is everyone
    assert cell["latest"]["lines"]["podium"] is None


def test_a_duplicate_runner_counts_once():
    races = [_race(1, 10, ("state", None), "1")]
    people = [(i, "X", 160.0) for i in range(12)]
    rows = _rows_for(1, 10, 2025, people)
    rows.append(dict(rows[0], place=13))
    ratings = {(i, "hs_m", 2025): 160.0 for i in range(12)}
    data = C.computeMarks("CA", races, rows, ratings)
    assert C.findDivision(data, "boys", "1")["latest"]["n_finishers"] == 12


def test_stamp_times_uses_the_venue_only_when_its_difficulty_is_known():
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    calls = []

    def conv(r, pool, dist, diff=None, cid=None, course=None):
        calls.append((pool, dist, diff))
        return 1000.0 - r + (diff or 0) * 100
    venue = {"course_name": "Woodward Park", "distance": 5000, "editions": 3,
             "difficulty": 0.02, "canonical_id": 7}
    C.stampTimes(data, {"boys": venue}, convert=conv)
    ln = C.findDivision(data, "boys", "2")["latest"]["lines"]["qualify"]
    assert ln["t_typical"] == pytest.approx(1000.0 - ln["mark"])
    assert ln["t_state"] == pytest.approx(1000.0 - ln["mark"] + 2.0)
    venue["difficulty"] = None
    C.stampTimes(data, {"boys": venue}, convert=conv)
    assert ln["t_state"] is None and ln["t_typical"] is not None


# ------------------------------------------------------------------ #
#  the athlete line
# ------------------------------------------------------------------ #

def _season(rating, **units):
    d = {"pool": "hs_m", "state": "CA", "mean_rating": rating,
         "state_div": "2", "section": "NCS", "section_div": "2"}
    d.update(units)
    return d


def test_athlete_line_names_the_next_rung():
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    cell = C.findDivision(data, "boys", "2")
    sec = data["genders"]["boys"]["sections"][0]["latest"]["lines"]["advance"]["mark"]
    q = cell["latest"]["lines"]["qualify"]["mark"]
    # below the section's advancing mark: that is the rung named
    ln = C.lineFor(_season(sec - 2.1), data, 2026)
    assert ln["kind"] == "off" and ln["gap"] == 2.1
    assert "advance from NCS D2" in ln["text"] and "last year's" in ln["text"]
    assert ln["href"] == "/what-it-takes/ca/2"
    # no section on the row: the state qualifying mark is the first rung
    ln = C.lineFor(_season(q - 1.0, section=None), data, 2026)
    assert ln["text"] == "last year's state qualifying mark" and ln["gap"] == 1.0
    assert ln["label"] == "CA Division 2"
    # above everything
    ln = C.lineFor(_season(260.0), data, 2026)
    assert ln["kind"] == "above" and "top-8" in ln["text"]
    # an older season is named by year, not as "last year's"
    ln = C.lineFor(_season(q - 1.0, section=None), data, 2027)
    assert ln["text"].startswith("the 2025")


def test_athlete_line_is_silent_when_unsure():
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    assert C.lineFor(_season(170.0, state_div="9"), data, 2026) is None   # no such division
    assert C.lineFor(_season(170.0, pool="ms_m"), data, 2026) is None     # not HS
    assert C.lineFor(_season(170.0), {"genders": {}}, 2026) is None
    races = [_race(1, 10, ("state", None), "2")]
    people = [(i, "X", 150.0 - i) for i in range(6)]
    tiny = C.computeMarks("CA", races, _rows_for(1, 10, 2025, people),
                          {(i, "hs_m", 2025): 150.0 - i for i in range(6)})
    assert C.lineFor(_season(140.0, section=None), tiny, 2026) is None   # thin: no line


def test_athlete_line_gap_is_also_a_share():
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    q = C.findDivision(data, "boys", "2")["latest"]["lines"]["qualify"]["mark"]
    ln = C.lineFor(_season(q - 2.0, section=None), data, 2026)
    assert ln["gap_pct"] == round(100 * 2.0 / (q - 2.0), 1)
    assert ln["rung"]["where"] == "state" and ln["pool"] == "hs_m"


def _conv(r, pool, dist, diff=None, cid=None, course=None):
    return 1000.0 * (1 + (diff or 0)) * dist / 5000.0 - r


def test_goal_times_are_their_courses_and_the_final():
    line = {"pool": "hs_m", "mark": 130.0}
    raced = [{"course_name": "Crystal", "distance": 5000, "n": 3, "canonical_id": 3},
             {"course_name": "Toro", "distance": 5000, "n": 3, "canonical_id": None},
             {"course_name": "Once", "distance": 4000, "n": 1, "canonical_id": 5}]
    diffs = {3: 0.05, 5: 0.0}
    got = C.goalTimes(line, raced,
                      {"course_name": "Woodward", "distance": 5000,
                       "difficulty": 0.03, "canonical_id": 1},
                      convert=_conv, difficulty=lambda cid, d: diffs.get(cid))
    names = [t["course_name"] for t in got]
    # the most-raced rated course; Toro has no rated cell, so no clock; the
    # once-raced course is not a "most-raced" one; the state course is added
    assert names == ["Crystal", "Woodward"]
    assert got[0]["seconds"] == pytest.approx(1050.0 - 130.0)
    assert got[1]["champ"] and not got[0]["champ"]


def test_goal_times_fall_back_to_a_typical_course_and_merge_the_final():
    line = {"pool": "hs_m", "mark": 130.0}
    raced = [{"course_name": "Toro", "distance": 5000, "n": 2, "canonical_id": None}]
    got = C.goalTimes(line, raced, None, convert=_conv,
                      difficulty=lambda cid, d: None)
    assert got == [{"course_name": None, "distance": 5000.0,
                    "seconds": 870.0, "champ": False}]
    # their own course IS the final's: one clock, marked as the final
    raced = [{"course_name": "Woodward", "distance": 5000, "n": 2, "canonical_id": 1}]
    got = C.goalTimes(line, raced, {"course_name": "Woodward", "distance": 5000,
                                    "difficulty": 0.03, "canonical_id": 1},
                      convert=_conv, difficulty=lambda cid, d: 0.03)
    assert len(got) == 1 and got[0]["champ"]
    assert C.goalTimes({"pool": None, "mark": 1}, raced, None) == []


def test_athlete_line_never_raises():
    class Boom:
        connection = type("X", (), {"rollback": lambda self: None})()

        def execute(self, *a, **k):
            raise RuntimeError("database gone")
    assert C.athleteLine(Boom(), 1) is None


# ------------------------------------------------------------------ #
#  templates and routes
# ------------------------------------------------------------------ #

def test_templates_parse():
    import jinja2
    tdir = os.path.join(_ROOT, "racecast", "templates")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(tdir))
    for name in ("what_it_takes.html", "athlete.html", "projections.html"):
        with open(os.path.join(tdir, name), encoding="utf-8") as fh:
            env.parse(fh.read())


@pytest.fixture
def client(monkeypatch):
    flask = pytest.importorskip("flask")  # noqa: F841
    import contextlib
    import app as A
    import ttlcache
    races, rows, ratings = _small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    C.stampTimes(data, {"boys": {"course_name": "Woodward Park",
                                 "distance": 5000, "editions": 3,
                                 "difficulty": 0.0, "canonical_id": 1}},
                 convert=lambda r, *a, **k: 2000.0 - 6 * r)
    data["current_year"] = 2026

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self, **k):
            return Cur()

    @contextlib.contextmanager
    def fake():
        yield Conn()
    import database
    monkeypatch.setattr(database, "getConn", fake)
    ttlcache.clear()
    ttlcache.get(("wit", "CA"), lambda: data)
    ttlcache.get(("wit", "OR"), lambda: C._empty("OR", None))
    yield A.app.test_client()
    ttlcache.clear()


def test_division_page_renders(client):
    r = client.get("/what-it-takes/ca/2")
    assert r.status_code == 200, r.data[:500]
    html = r.get_data(as_text=True)
    assert "What it takes: CA Division 2" in html
    assert "Getting out of the section" in html and "NCS D2" in html
    assert 'class="rv" data-hs=' in html                 # the scale toggle's spans
    assert "Make state" in html and "Top 25" in html and "Top 8" in html
    assert "at the state course" in html
    assert "90% of the state meet&#39;s runners" in html or "90% of the state meet's runners" in html


def test_state_and_index_pages_render(client):
    r = client.get("/what-it-takes/ca")
    assert r.status_code == 200
    assert "/what-it-takes/ca/2" in r.get_data(as_text=True)
    r = client.get("/what-it-takes/or")
    assert r.status_code == 200
    assert "No Oregon state cross country final is on record" in r.get_data(as_text=True)
    assert client.get("/what-it-takes").status_code == 200
    assert client.get("/what-it-takes?state=CA").headers["Location"].endswith("/what-it-takes/ca")


def test_unknown_division_is_a_404_and_spellings_redirect(client):
    assert client.get("/what-it-takes/ca/99").status_code == 404
    assert client.get("/what-it-takes/zz").status_code == 404
    r = client.get("/what-it-takes/CA/2")
    assert r.status_code == 301 and r.headers["Location"].endswith("/what-it-takes/ca/2")
    # the girls have no race here: a real, empty answer, not a 404
    r = client.get("/what-it-takes/ca/2?g=girls")
    assert r.status_code == 200 and "No girls state meet race" in r.get_data(as_text=True)


# ------------------------------------------------------------------ #
#  the SQL, against a throwaway Postgres
# ------------------------------------------------------------------ #

_DDL = """
DROP TABLE IF EXISTS meet_unit, meets, results, ranking_results, athlete_season,
                     school_unit, homepage_meta, athletes, engine_scale,
                     course_canonical, course_difficulties CASCADE;
CREATE TABLE meet_unit (sport text, meet_id bigint, source text, kind text,
                        unit text, state text);
CREATE TABLE meets (div_id bigint PRIMARY KEY, meet_id bigint, meet_name text,
                    meet_date text, course_name text, distance real,
                    gps_lat real, gps_long real, state text, division text,
                    source text, altitude_meters real);
-- the conversions' own inputs, so a mark comes back as a clock: the engine's
-- pool means (hs boys / girls) and one rated state course
CREATE TABLE engine_scale (pool text, sport text, pool_mean real,
                           median_effect real, anchor_shift real);
INSERT INTO engine_scale VALUES ('hs_m', 'XC', 1227.5, 0, 0),
                                ('hs_f', 'XC', 1466.0, 0, 0);
CREATE TABLE course_canonical (course_name text, gps_lat real, gps_long real,
                               canonical_id bigint);
INSERT INTO course_canonical VALUES ('Woodward Park', 36.8, -119.7, 1),
                                    ('Crystal Springs', 37.5, -122.3, 3),
                                    ('NCS Course', 38.0, -122.2, 4);
CREATE TABLE course_difficulties (course_name text, canonical_id bigint,
                                  distance_m int, difficulty real,
                                  n_results int, n_athletes int,
                                  last_updated text);
INSERT INTO course_difficulties VALUES ('XC:1:5000', 1, 5000, 0.03, 5000, 900, NULL),
                                       ('XC:2:5000', 2, 5000, 0.0, 9000, 900, NULL),
                                       ('XC:3:5000', 3, 5000, 0.05, 3000, 900, NULL),
                                       ('XC:4:5000', 4, 5000, 0.01, 2000, 900, NULL);
CREATE TABLE results (result_id bigint PRIMARY KEY, athlete_id bigint,
                      person_id bigint, meet_id bigint, div_id bigint,
                      source text, time_seconds real, grade text, date text,
                      school text, speed_rating real, place int, team_id bigint,
                      normalized_time real, status text, athlete_name text);
CREATE TABLE ranking_results (sport text, result_id bigint, person_id bigint,
                              pool text, speed_rating real, race_date date,
                              year int, state text, school text, grade text,
                              meet_id bigint, div_id bigint, canon_meet_id bigint,
                              time_seconds real, distance real, event_id bigint,
                              state_div text, class text, section text,
                              section_div text);
CREATE TABLE athlete_season (person_id bigint, pool text, sport text, year int,
                             mean_rating real, best_rating real, n_races int,
                             last_race date, school text, state text, grade text,
                             state_div text, class text, section text,
                             section_div text);
CREATE TABLE school_unit (school text, state text, sport text, league text,
                          section text, section_div text, state_div text,
                          class text, is_college boolean DEFAULT false);
CREATE TABLE homepage_meta (key text, value text);
CREATE TABLE athletes (person_id bigint, first_name text, last_name text,
                       school text, gender text);
"""


# course -> (lat, lon, difficulty the fixture's clocks are run at). Toro Park
# and the CCS course have no rated cell, on purpose.
_COURSES = {"Woodward Park": (36.8, -119.7, 0.03),
            "Crystal Springs": (37.5, -122.3, 0.05),
            "NCS Course": (38.0, -122.2, 0.01),
            "CCS Course": (36.6, -121.9, 0.0),
            "Toro Park": (36.6, -121.7, 0.0)}


def buildFixture(conn, seed=7):
    """A California: two sections (NCS, CCS), state divisions 1 and 2, boys
    and girls, seasons 2023-2025 run, 2026 under way. Each section final's
    top 30 go to state. Plus the traps: a tfrrs meet sharing the 2025 state
    meet's id, an area meet carrying the NCS name, and one school filed in
    the wrong division. Returns {"athlete": a 2026 D2 boy's person_id}."""
    rnd = random.Random(seed)
    cur = conn.cursor()
    cur.execute(_DDL)
    schools = []
    for i in range(24):
        sec = "NCS" if i % 2 == 0 else "CCS"
        div = "1" if i < 12 else "2"
        schools.append((f"School {chr(65 + i % 26)}{i}", sec, div))
    for i, (name, sec, div) in enumerate(schools):
        # the last school runs D2 but is on record in D1 (a wrong assignment)
        filed = "1" if i == len(schools) - 1 else div
        cur.execute("INSERT INTO school_unit VALUES (%s,'CA','XC',NULL,%s,%s,%s,NULL,false)",
                    (name, sec, filed, filed))
    cur.execute("INSERT INTO homepage_meta VALUES ('season_year_XC', '2026')")
    strength = {s[0]: rnd.uniform(-8, 8) for s in schools}
    rid = [0]
    divid = [0]

    def race(meet_id, source, meet_name, title, course, people, year, pool):
        divid[0] += 1
        did = 900000 + divid[0]
        lat, lon, diff = _COURSES.get(course, (37.0, -121.0, 0.0))
        if source == "anet":
            cur.execute("INSERT INTO meets VALUES (%s,%s,%s,%s,%s,5000,%s,%s,'CA',%s,%s,90)",
                        (did, meet_id, meet_name, f"{year}-11-{20 if 'State' in meet_name else 10}",
                         course, lat, lon, title, source))
        pm = 1227.5 if pool == "hs_m" else 1466.0
        order = sorted(people, key=lambda p: -p[2])
        for k, (pid, school, rr) in enumerate(order):
            rid[0] += 1
            t = 100.0 * pm / rr * (1.0 + diff)
            cur.execute("INSERT INTO results (result_id, person_id, meet_id, div_id, source,"
                        " time_seconds, date, school, speed_rating, place) VALUES"
                        " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (rid[0], pid, meet_id, did, source, t, f"{year}-11-10",
                         school, rr, k + 1))
            u = next(s for s in schools if s[0] == school)
            cur.execute("INSERT INTO ranking_results (sport, result_id, person_id, pool,"
                        " speed_rating, race_date, year, state, school, meet_id, div_id,"
                        " time_seconds, distance, state_div, section, section_div)"
                        " VALUES ('XC',%s,%s,%s,%s,%s,%s,'CA',%s,%s,%s,%s,5000,%s,%s,%s)",
                        (rid[0], pid, pool, rr, f"{year}-11-10", year, school,
                         meet_id, did, t, u[2], u[1], u[2]))
        return did, order

    athlete = None
    for year in (2023, 2024, 2025, 2026):
        for g, pool, gword, base in (("M", "hs_m", "Boys", 128.0),
                                     ("F", "hs_f", "Girls", 127.0)):
            season = {}
            for si, (name, sec, div) in enumerate(schools):
                for k in range(9):
                    pid = 10_000_000 + si * 100 + (10 if g == "F" else 0) + k
                    r = base + strength[name] + (3 if div == "1" else 0) \
                        - 2.2 * k + rnd.gauss(0, 3) + 0.6 * (year - 2023)
                    season[pid] = (name, sec, div, r)
                    cur.execute("INSERT INTO athlete_season VALUES"
                                " (%s,%s,'XC',%s,%s,%s,5,%s,%s,'CA','11',%s,NULL,%s,%s)",
                                (pid, pool, year, r, r + 2, f"{year}-10-01", name,
                                 div, sec, div))
                    if year == 2023:
                        cur.execute("INSERT INTO athletes VALUES (%s,%s,%s,%s,%s)",
                                    (pid, "Runner", f"{name[-3:]}{k}{g}", name, g))
                    if (athlete is None and year == 2026 and g == "M"
                            and div == "2" and sec == "NCS" and k == 1):
                        athlete = pid
            if year == 2026:
                # the season under way: no finals yet, two invitationals at a
                # rated course and one at an unrated one
                everyone = [(pid, v[0], v[3]) for pid, v in season.items()]
                for mid, course in ((5001, "Crystal Springs"), (5002, "Crystal Springs"),
                                    (5003, "Toro Park")):
                    race(mid, "anet", f"{course} Invitational", f"Varsity {gword}",
                         course, everyone, year, pool)
                continue
            advancers = {"1": [], "2": []}
            for sec, mid0 in (("NCS", 2000), ("CCS", 3000)):
                for div in ("1", "2"):
                    people = [(pid, v[0], v[3] + rnd.gauss(0, 2))
                              for pid, v in season.items()
                              if v[1] == sec and v[2] == div]
                    _did, order = race(mid0 + year, "anet",
                                       f"CIF-{sec} Cross Country Championships",
                                       f"Division {div} {gword}", f"{sec} Course",
                                       people, year, pool)
                    advancers[div] += [(p, s, rr + rnd.gauss(0, 2))
                                       for p, s, rr in order[:30]]
            # an NCS area meet: section word, not the section final
            people = [(pid, v[0], v[3]) for pid, v in season.items() if v[1] == "NCS"][:30]
            race(4000 + year, "anet", "NCS Tri-Valley Area Championships",
                 f"Varsity {gword}", "Area Park", people, year, pool)
            for div in ("1", "2"):
                race(1000 + year, "anet", "CIF State Cross Country Championships",
                     f"Division {div} {gword}", "Woodward Park",
                     advancers[div], year, pool)
        for mid, kinds in ((1000 + year, [("state", "CA")]),
                           (2000 + year, [("section", "NCS")]),
                           (3000 + year, [("section", "CCS")]),
                           (4000 + year, [("section", "NCS"), ("area", "TRI-VALLEY")])):
            if year == 2026:
                continue
            cur.execute("INSERT INTO meet_unit VALUES ('XC',%s,'anet','championship',NULL,'CA')", (mid,))
            for k, u in kinds:
                cur.execute("INSERT INTO meet_unit VALUES ('XC',%s,'anet',%s,%s,'CA')", (mid, k, u))
    # ⚠ THE COLLISION: a tfrrs meet with the 2025 state meet's id. Its
    #   results sit on a div_id the state race also uses; joined on meet_id
    #   alone they would join the state field.
    cur.execute("SELECT div_id FROM meets WHERE meet_id = %s ORDER BY div_id LIMIT 1", (1000 + 2025,))
    sdiv = cur.fetchone()[0]
    for k in range(40):
        rid[0] += 1
        cur.execute("INSERT INTO results (result_id, person_id, meet_id, div_id, source,"
                    " time_seconds, date, school, place) VALUES (%s,%s,%s,%s,'tfrrs',%s,"
                    "'2025-11-01','Some College',%s)",
                    (rid[0], 77000000 + k, 1000 + 2025, sdiv, 700.0 + k, k + 1))
        cur.execute("INSERT INTO ranking_results (sport, result_id, person_id, pool,"
                    " speed_rating, year, state, school, time_seconds) VALUES"
                    " ('XC',%s,%s,'hs_m',250,2025,'CA','Some College',%s)",
                    (rid[0], 77000000 + k, 700.0 + k))
        cur.execute("INSERT INTO athlete_season (person_id, pool, sport, year, mean_rating)"
                    " VALUES (%s,'hs_m','XC',2025,250)", (77000000 + k,))
    conn.commit()
    return {"athlete": athlete}


@pytest.mark.skipif(not DSN, reason="set XCP_TEST_DSN to a throwaway Postgres")
def test_sql_against_postgres():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DSN)
    try:
        fx = buildFixture(conn)
        import predict
        predict._SEASON_CACHE.clear()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            races = C._races(cur, "CA", C._stateMeets(cur, "CA"))
            # the area meet is not a round; state + 2 sections x 2 divisions x 2 genders x 3 years
            assert {r["meet_name"] for r in races} == {
                "CIF State Cross Country Championships",
                "CIF-NCS Cross Country Championships",
                "CIF-CCS Cross Country Championships"}
            years = list(range(2020, 2027))
            rows = C._finishers(cur, "CA", races, years)
            # ★ not one tfrrs runner reached the state field
            assert not [r for r in rows if r["person_id"] >= 77000000]
            data = C.computeMarks("CA", races, rows,
                                  C._seasonRatings(cur, rows, years))
            boys = data["genders"]["boys"]
            assert [c["slug"] for c in boys["divisions"]] == ["1", "2"]
            d2 = C.findDivision(data, "boys", "2")
            assert d2["latest"]["n_finishers"] == 60
            assert [s["year"] for s in d2["seasons"]] == [2025, 2024, 2023]
            assert {c["label"] for c in C.sectionsFeeding(data, "boys", "2")} == {"NCS D2", "CCS D2"}
            assert C.sectionsFeeding(data, "boys", "2")[0]["latest"]["n_advanced"] == 30
            season = C._athleteSeason(cur, fx["athlete"], 2026)
            assert season["section"] == "NCS" and season["state_div"] == "2"
            assert C.lineFor(season, data, 2026) is not None
            raced = C._racedCourses(cur, fx["athlete"], 2026)
            assert [(c["course_name"], c["n"], c["canonical_id"]) for c in raced] == [
                ("Crystal Springs", 2, 3), ("Toro Park", 1, None)]
            sec = C.sectionsFeeding(data, "boys", "2")
            ncs = next(c for c in sec if c["unit"] == "NCS")["latest"]
            assert C._raceCourse(cur, ncs["meet_id"], ncs["source"],
                                 ncs["div_id"]) == {
                "course_name": "NCS Course", "distance": 5000.0, "canonical_id": 4}
    finally:
        conn.close()
