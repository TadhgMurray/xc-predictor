"""Compiled XC results split by level, and the DQ rule (owner, 2026-10-10).

★ A compiled race merged every division at one distance and gender, so JV
  runners took varsity places and displaced varsity scorers. Each level
  (division_group.divisionGroup) now compiles on its own; varsity is primary.
★ A DQ (status DQ / FS) has a real time and no place: it keeps its row, gets
  no compiled place, and neither scores nor displaces. DNF/DNS likewise.

No Postgres: a stub cursor answers the probes and the one results query.

    python -m pytest -q tests/test_compiled_levels.py
"""
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

import meet_compile as MC                                      # noqa: E402


@pytest.fixture(autouse=True)
def _freshProbes(monkeypatch):
    """The column probes cache a "yes"; keep this file's yeses to itself, so
    another file's stub cursor still sees its probes asked."""
    monkeypatch.setattr(MC, "_RESULTS_COLS", {})


# ---- fixture: one meet, boys 5000 in five divisions, girls 5000 in one --- #

DIVS = {1: "Varsity", 2: "Open", 3: "JV", 4: "Frosh/Soph", 5: "Masters",
        6: "Alumni", 7: "Varsity"}
SCHOOLS = ("Jesuit", "Central", "Lincoln")


def fixtureRows():
    """Rows as compiledResults' query returns them (time order)."""
    rows, rid = [], [1000]

    def add(div, gender, t, school, status=None, name=None):
        rid[0] += 1
        rows.append({"result_id": rid[0], "person_id": rid[0], "team_id": None,
                     "place": None, "time_seconds": t, "grade": "11",
                     "school": school, "speed_rating": 100.0, "div_id": div,
                     "rating_pool": None, "status": status,
                     "div_label": DIVS[div], "distance": 5000,
                     "gender": gender,
                     "name": name or f"{DIVS[div]} {school} {rid[0]}"})
    # varsity-level: divisions 1 and 2, five or more per school between them
    for i, sc in enumerate(SCHOOLS):
        for k in range(4):
            add(1, "M", 960.0 + 10 * k + i, sc)
        for k in range(2):
            add(2, "M", 1010.0 + 10 * k + i, sc)
    # ! the fastest JV runner beats most of the varsity field
    for i, sc in enumerate(SCHOOLS):
        for k in range(5):
            add(3, "M", 950.5 + 20 * k + i, sc, name=f"JV {sc} {k}")
    for i, sc in enumerate(SCHOOLS):
        for k in range(5):
            add(4, "M", 1100.0 + 15 * k + i, sc, name=f"FS {sc} {k}")
    add(5, "M", 1200.0, "Club A")
    add(6, "M", 1210.0, "Club B")
    # a DQ in the varsity race, faster than everyone, and a team short
    add(1, "M", 955.0, "Jesuit", status="DQ", name="Dq Runner")
    add(1, "M", 1300.0, "Central", status="DNF", name="Dnf Runner")
    # girls: one varsity race only
    for i, sc in enumerate(SCHOOLS):
        for k in range(5):
            add(7, "F", 1100.0 + 10 * k + i, sc)
    rows.sort(key=lambda r: (r["distance"], r["gender"], r["time_seconds"]))
    return rows


class StubCursor:
    """Answers the column probes yes, the table probes no, and the compiled
    query from `rows`."""

    def __init__(self, rows=None, index_rows=None):
        self.rows = rows or []
        self.index_rows = index_rows or []
        self.sql = []
        self._rows = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "information_schema.columns" in sql:
            self._rows = [{"1": 1}]
        elif "to_regclass" in sql:
            self._rows = [{"present": False, "ok": False}]
        elif "count(*) AS n" in sql:
            self._rows = list(self.index_rows)
        elif "FROM   results r" in sql:
            self._rows = [dict(r) for r in self.rows]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


def _boys():
    groups = MC.compiledResults(StubCursor(fixtureRows()), 77)
    return {(g["distance"], g["gender"]): g for g in groups}[(5000, "M")]


# ---- 1. partition by level ------------------------------------------------ #

def test_levels_partition_the_group():
    g = _boys()
    assert [lv["level"] for lv in g["levels"]] == [
        "varsity", "jv", "fs", "other:alumni", "other:masters"]
    by = {lv["level"]: lv for lv in g["levels"]}
    # Varsity and Open are one varsity-level field
    assert by["varsity"]["divisions"] == [1, 2]
    assert by["varsity"]["div_labels"] == ["Varsity", "Open"]
    assert by["jv"]["divisions"] == [3] and by["jv"]["label"] == "JV"
    assert by["fs"]["label"] == "Frosh-Soph"
    # every row lands in exactly one level
    assert {r["div_id"] for r in by["jv"]["results"]} == {3}
    assert all(r["div_id"] in (1, 2) for r in by["varsity"]["results"])
    assert sum(len(lv["results"]) for lv in g["levels"]) == g["n_results"]


def test_each_level_places_and_scores_on_its_own():
    by = {lv["level"]: lv for lv in _boys()["levels"]}
    jv = by["jv"]["results"]
    assert [r["place"] for r in jv] == list(range(1, len(jv) + 1))
    # three full JV teams scored, apart from varsity
    assert len(by["jv"]["scores"]["teams"]) == 3
    assert len(by["fs"]["scores"]["teams"]) == 3


# ---- 2. varsity is primary ------------------------------------------------ #

def test_varsity_is_primary_and_the_group_keys_are_its():
    g = _boys()
    v = g["levels"][0]
    assert g["level"] == "varsity" and v["level"] == "varsity"
    assert g["results"] is v["results"] and g["scores"] is v["scores"]
    assert g["divisions"] == [1, 2]
    # ★ THE JV RUNNER WHO OUTRAN THE VARSITY FIELD IS NOT IN IT: varsity's
    #   first place is a varsity runner, and no JV row displaces a scorer
    assert not any(r["name"].startswith("JV ") for r in g["results"])
    winner = next(r for r in g["results"] if r["place"] == 1)
    assert winner["div_id"] == 1 and winner["time_seconds"] == 960.0
    scorers = [r["score_place"] for r in g["results"] if r["score_place"]]
    assert scorers == list(range(1, len(scorers) + 1))


def test_groups_with_no_varsity_level_lead_with_their_first():
    rows = [r for r in fixtureRows() if r["div_id"] in (3, 4)]
    g = MC.compiledResults(StubCursor(rows), 77)[0]
    assert g["level"] == "jv" and g["levels"][0]["level"] == "jv"


def test_one_division_is_labelled_single():
    groups = MC.compiledResults(StubCursor(fixtureRows()), 77)
    girls = {(g["distance"], g["gender"]): g for g in groups}[(5000, "F")]
    assert len(girls["levels"]) == 1 and girls["levels"][0]["single"]
    assert not _boys()["levels"][0]["single"]


# ---- 3. unknown labels are never merged ----------------------------------- #

def test_unknown_labels_are_their_own_levels():
    by = {lv["level"]: lv for lv in _boys()["levels"]}
    assert by["other:masters"]["divisions"] == [5]
    assert by["other:alumni"]["divisions"] == [6]
    # called by the division's own name -- there is no generic one
    assert by["other:masters"]["label"] == "Masters"
    assert MC.levelLabel("other:gold race", []) == "Gold Race"


# ---- 4. DQ: no place, no score, status in the place column ---------------- #

def test_dq_keeps_its_row_and_loses_its_place():
    v = _boys()["levels"][0]["results"]
    dq = next(r for r in v if r["name"] == "Dq Runner")
    # in time order (its time is real) but unplaced
    assert v[0] is dq
    assert dq["place"] is None and dq["place_status"] == "DQ"
    assert dq["score_place"] is None and dq["team_place"] is None
    assert dq["division_place"] is None and dq["division_status"] == "DQ"
    # the runner behind it is first, not second -- in the compile and in
    # the race they ran
    assert v[1]["place"] == 1 and v[1]["division_place"] == 1


def test_dnf_with_a_time_is_unplaced_and_last():
    v = _boys()["levels"][0]["results"]
    assert v[-1]["name"] == "Dnf Runner"
    assert v[-1]["place"] is None and v[-1]["place_status"] == "DNF"
    assert v[-1]["score_place"] is None


def test_annotate_scoring_skips_a_dq():
    """Four finishers and a DQ are not a team; a DQ ahead of a scorer
    displaces nobody."""
    rows = [{"school": "A", "time_seconds": 900.0, "status": "DQ"}]
    rows += [{"school": "B", "time_seconds": 901.0 + i, "status": None}
             for i in range(5)]
    rows += [{"school": "A", "time_seconds": 910.0 + i, "status": None}
             for i in range(4)]
    rows += [{"school": "C", "time_seconds": 920.0, "status": "FS"}]
    rows += [{"school": "C", "time_seconds": 999999, "status": "DNF"}]
    MC.annotateScoring(rows)
    assert rows[0]["score_place"] is None and rows[0]["team_place"] is None
    # B's five are 1..5: the DQ did not take place one
    assert [r["score_place"] for r in rows[1:6]] == [1, 2, 3, 4, 5]
    # A has four finishers -- not a scoring team, so it displaces nobody
    assert all(r["score_place"] is None for r in rows[6:10])
    assert [r["team_place"] for r in rows[6:10]] == [1, 2, 3, 4]
    scores = MC.scoreRows([dict(r) for r in rows])
    assert [t["school"] for t in scores["teams"]] == ["B"]
    assert {i["school"]: i["n"] for i in scores["incomplete"]} == {"A": 4}


def test_a_row_with_no_time_column_still_finishes():
    """The team boards' rating-ordered fields carry no time at all."""
    assert MC._finished({"school": "A"})


def test_the_race_page_and_the_compile_share_one_rule():
    import app as A
    rows = [{"time_seconds": 900.0, "status": "DQ"},
            {"time_seconds": 901.0, "status": None}]
    A._stampXcPlaces(rows)
    assert [r["pl"] for r in rows] == [None, 1]
    assert A._xcPlaced(rows[1]) and not A._xcPlaced(rows[0])
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    body = src[src.index("def _stampXcPlaces("):][:600]
    assert "from meet_compile import stampXcPlaces" in body


def test_compiled_query_reads_status_and_the_division_label():
    cur = StubCursor([])
    MC.compiledResults(cur, 9)
    sql = next(s for s, _ in cur.sql if "FROM   results r" in s)
    assert "r.status" in sql and "AS div_label" in sql


# ---- 5. the meet page's index lists the levels ---------------------------- #

def _indexRows():
    from collections import Counter
    c = Counter((r["distance"], r["gender"], r["div_id"], r["div_label"],
                 r["school"]) for r in fixtureRows())
    return [{"distance": d, "gender": g, "div_id": dv, "div_label": lb,
             "school": sc, "n": n} for (d, g, dv, lb, sc), n in c.items()]


def test_compiled_index_lists_levels_varsity_first():
    got = MC.compiledIndex(StubCursor(index_rows=_indexRows()), 77)
    boys = [e for e in got if e["gender"] == "M"]
    assert [e["level"] for e in boys] == [
        "varsity", "jv", "fs", "other:alumni", "other:masters"]
    assert [e["primary"] for e in boys] == [True, False, False, False, False]
    assert boys[0]["n_divisions"] == 2 and boys[0]["n_teams"] == 3
    assert boys[0]["n_results"] == 20
    assert boys[1]["n_teams"] == 3 and boys[1]["single"]
    assert boys[3]["n_teams"] == 0 and boys[3]["label"] == "Alumni"
    girls = [e for e in got if e["gender"] == "F"]
    assert len(girls) == 1 and girls[0]["primary"] and girls[0]["single"]
    # biggest (distance, gender) first, as before
    assert got[0]["gender"] == "M"


def test_meet_template_links_levels():
    src = open(os.path.join(_ROOT, "racecast", "templates", "meet.html"),
               encoding="utf-8").read()
    assert "?level={{ g.level|urlencode }}" in src
    assert "{% if g.primary %}{{ _rq }}" in src


# ---- 6. URL back-compat --------------------------------------------------- #

def test_compiled_level_picks_by_query():
    import app as A
    g = _boys()
    assert A._compiledLevel(g, None)["level"] == "varsity"
    assert A._compiledLevel(g, "")["level"] == "varsity"
    assert A._compiledLevel(g, "VARSITY")["level"] == "varsity"
    assert A._compiledLevel(g, "jv")["level"] == "jv"
    assert A._compiledLevel(g, "other:masters")["level"] == "other:masters"
    assert A._compiledLevel(g, "ms") is None
    assert A._compiledLevel(None, None) is None


@pytest.fixture
def client(monkeypatch):
    import app as A
    rows = fixtureRows()

    class _Conn:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def cursor(s, **k):
            return _CM(StubCursor(rows))
        def rollback(s): pass

    class _CM:
        def __init__(s, c): s.c = c
        def __enter__(s): return s.c
        def __exit__(s, *a): return False

    monkeypatch.setattr(A, "getConn", lambda *a, **k: _Conn())
    monkeypatch.setattr(A, "_inMaintenance", lambda: False)
    monkeypatch.setattr(A, "meet_sources", lambda cur, t, m: [])
    monkeypatch.setattr(A, "pick_source", lambda s, alt: (None, None, []))
    monkeypatch.setattr(A, "get_meet_header", lambda cur, m, source=None: {
        "meet_id": m, "meet_name": "Fixture Invitational", "state": "OR",
        "course_name": None})
    def _hs(cur, sport, rows, distance=None):
        for r in rows:
            r["hs_rating"] = None
        return False
    monkeypatch.setattr(A, "stampRowsHs", _hs)
    A.app.config["TESTING"] = True
    return A.app.test_client()


def test_old_compiled_url_lands_on_varsity(client):
    r = client.get("/race/xc/77/compiled/5000/M")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "JV Jesuit 0" not in html            # no JV row in the varsity race
    assert "Dq Runner" in html
    assert 'class="rc-levels"' in html or "rc-levels" in html
    # the level bar links the others by ?level=, the primary by the bare URL
    assert 'href="/race/xc/77/compiled/5000/M?level=jv"' in html
    assert 'href="/race/xc/77/compiled/5000/M"' in html
    # lower-case gender, as older links were written, still resolves
    assert client.get("/race/xc/77/compiled/5000/m").status_code == 200


def test_level_query_selects_and_keeps_other_args(client):
    html = client.get("/race/xc/77/compiled/5000/M?level=jv&alt=1").get_data(as_text=True)
    assert "JV Jesuit 0" in html and "Dq Runner" not in html
    assert 'href="/race/xc/77/compiled/5000/M?alt=1"' in html
    assert 'href="/race/xc/77/compiled/5000/M?level=fs&amp;alt=1"' in html


def test_unknown_level_is_404(client):
    assert client.get("/race/xc/77/compiled/5000/M?level=ms").status_code == 404
    # ?level=varsity always lands somewhere
    assert client.get("/race/xc/77/compiled/5000/F?level=varsity").status_code == 200


def test_dq_shows_its_status_in_the_place_column(client):
    html = client.get("/race/xc/77/compiled/5000/M").get_data(as_text=True)
    i = html.index("Dq Runner")
    row = html[html.rindex("<tr", 0, i):i]
    assert '<td class="pl"><span>DQ</span></td>' in row
