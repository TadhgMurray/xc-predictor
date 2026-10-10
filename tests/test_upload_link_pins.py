"""Uploaded results get a person, and the owner's approved fixes stay fixed
(owner, 2026-10-10): scripts/link_uploads.py (pipeline 04a, pass 4),
scripts/person_pins.py (grade_pin, school_pin, result_detach; step 04a3),
engine/twin_flag.py's twin_upload, and the readers that honour them.

    python -m pytest -q tests/test_upload_link_pins.py

Stub cursors only: no Postgres here. The decisions are pure and tested on
small lists; the writers are run on a recording cursor, so every statement
they would send is visible.
"""
import os
import re
import sys
import types

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast", "backfill"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest                                                    # noqa: E402
import psycopg2.extras                                           # noqa: E402

if "corrections" not in sys.modules:            # 165 MB, not in git (test_backfill_new_only)
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c

# the real database module, not another test's stand-in
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import link_uploads as LU                                        # noqa: E402
import person_pins as PP                                         # noqa: E402
import link_tfrrs_rows as LT                                     # noqa: E402
_REAL_DB = sys.modules["database"]
if _stub is not None:
    sys.modules["database"] = _stub

U, R, P = LU.UpRow, LU.Row, LU.Person


@pytest.fixture
def realDb(monkeypatch):
    """The real database module for a heavy import (build_ranking_results,
    backfill_normalize), whatever stand-in another test file installed."""
    monkeypatch.setitem(sys.modules, "database", _REAL_DB)


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- the stubs

class Cur:
    """Records every statement; answers from (pattern, rows) in order. Rows
    are TUPLES, as the pipeline's plain cursors return them."""
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.sql = []
        self.values = []
        self.rows = []
        self.rowcount = 0

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

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ran(self, pat):
        return [p for s, p in self.sql if re.search(pat, s, re.S)]

    def wrote(self, pat):
        return [rows for s, rows in self.values if re.search(pat, s, re.S)]


class Conn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *a, **k):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def recorder(monkeypatch):
    def ev(cur, sql, rows, page_size=100, **k):
        rows = list(rows)
        cur.values.append((sql, rows))
        cur.rowcount = len(rows)
    monkeypatch.setattr(psycopg2.extras, "execute_values", ev)


# ======================================================== 1. the decisions

OWEN = U(-11, "XC", "2025-10-04", "Owen Castellano", "Jesuit High School", "11", "M", -900000007,
         -900000007001, 7)
# his anet career at "Jesuit" (the feed's spelling), grade 10 last season
CAREER = P(4412, "Owen Castellano", "M", [
    R("2024-09-21", "XC", "10", "Jesuit", "hs"), R("2025-04-12", "TF", "10", "Jesuit", "hs"),
    R("2025-09-13", "XC", "11", "Jesuit", "hs")])


def test_name_and_school_and_generation_link_to_the_one_holder():
    v = LU.decide(OWEN, [CAREER])
    assert (v.target, v.reason, v.touched) == (4412, LU.MATCH, (4412,))
    # "Castellano, Owen" is the same key (link_freshmen.normName)
    assert LU.decide(OWEN._replace(name="CASTELLANO, Owen"), [CAREER]).target == 4412


def test_two_namesakes_at_the_school_is_report_only():
    other = CAREER._replace(pid=5501)
    v = LU.decide(OWEN, [CAREER, other])
    assert v.target is None and v.reason == "ambiguous: namesakes at the school"
    assert v.touched == (4412, 5501)


def test_a_namesake_elsewhere_that_autumn_is_doubtful_not_a_new_person():
    away = CAREER._replace(rows=[R("2025-09-20", "XC", "11", "Central Catholic", "hs")])
    v = LU.decide(OWEN, [away])
    assert v.target is None and v.reason == "namesake at another school in the window"
    assert v.touched == (4412,)


def test_nobody_by_that_name_is_a_new_person():
    assert LU.decide(OWEN, []).reason == LU.MINT
    # a namesake years away at another school is no claim on him
    old = CAREER._replace(rows=[R("2012-10-01", "XC", "12", "Grant", "hs")])
    assert LU.decide(OWEN, [old]).reason == LU.MINT
    # nor is a girl of the same name at his school (link_teamless rule 1)
    her = CAREER._replace(gender="F")
    assert LU.decide(OWEN, [her]).reason == LU.MINT


def test_the_fit_tests_refuse():
    old = CAREER._replace(rows=[R("2019-10-01", "XC", "12", "Jesuit", "hs")])
    assert LU.decide(OWEN, [old]).reason == "generation mismatch"
    same_day = CAREER._replace(rows=CAREER.rows + [R("2025-10-04", "XC", "11", "Jesuit", "hs")])
    assert LU.decide(OWEN, [same_day]).reason == "same cross country day as the namesake's own race"
    college = CAREER._replace(rows=[R("2025-09-27", "XC", "FR-1", "Jesuit", "college")])
    assert LU.decide(OWEN, [college]).reason == "level mismatch (college namesake)"
    assert LU.decide(OWEN._replace(grade="17"), [CAREER]).reason == "grade reads as an age"


def test_a_row_with_nothing_to_identify_it_is_report_only():
    assert LU.decide(OWEN._replace(school="Unattached"), [CAREER]).reason == "school identifies nothing"
    assert LU.decide(OWEN._replace(name="Owen"), [CAREER]).reason == "no usable name"


def test_two_rows_of_one_race_on_one_person_are_both_refused():
    twin = OWEN._replace(result_id=-12)
    vs, groups = LU.judge([OWEN, twin], {"owen castellano": [CAREER]})
    assert {v.reason for v in vs.values()} == {"two rows of one race claim one person"}
    assert not groups


def test_new_persons_one_per_name_and_school_when_the_rows_fit_one_athlete():
    fall = OWEN._replace(result_id=-21, date="2025-10-04")
    spring = OWEN._replace(result_id=-22, sport="TF", date="2026-04-11", meet_id=-900000009,
                           div_id=-900000009001, upload_id=9)
    vs, groups = LU.judge([fall, spring], {})
    assert list(groups.values()) == [[fall, spring]]
    # two of one name and school in ONE race are two runners: refused, reported
    vs, groups = LU.judge([fall, fall._replace(result_id=-23)], {})
    assert not groups and all(v.reason.startswith("new person: two runners") for v in vs.values())


# ======================================================== 2. the writers

def test_apply_links_mints_logs_and_reports(monkeypatch):
    doubtful = OWEN._replace(result_id=-31, name="Ann Doe", school="Lakeside", gender="F")
    new = OWEN._replace(result_id=-32, name="Zed Quill", school="Lakeside")
    by_key = {"owen castellano": [CAREER],
              "ann doe": [P(1, "Ann Doe", "F", [R("2025-09-20", "XC", "11", "Lakeside", "hs")]),
                          P(2, "Ann Doe", "F", [R("2025-09-20", "XC", "11", "Lakeside", "hs")])]}
    monkeypatch.setattr(LU, "gather", lambda cur: ([OWEN, doubtful, new], by_key))
    cur = Cur([(r"to_regclass", [("x",)]), (r"GREATEST", [(2000000100,)])])
    conn = Conn(cur)
    out = LU.run(conn, apply=True)
    moves = cur.wrote(r"INSERT INTO lu_map")[0]
    assert ("XC", -11, 4412, "upload") in moves
    assert ("XC", -32, 2000000101, "upload_mint") in moves
    assert all(m[1] != -31 for m in moves), "a doubtful row is never linked"
    rep = cur.wrote(r"INSERT INTO upload_link_report")[0]
    assert rep == [("XC", -31, 7, "Ann Doe", "Lakeside", "ambiguous: namesakes at the school", [1, 2])]
    log = [s for s, _p in cur.sql if "INSERT INTO person_link_log" in s]
    assert log and all("0, m.to_person, m.rule" in s for s in log)
    upd = [s for s, _p in cur.sql if re.search(r"UPDATE results(_tf)? r SET person_id = m.to_person", s)]
    assert len(upd) == 2 and all("r.source = 'upload' AND r.person_id IS NULL" in s for s in upd)
    assert cur.ran(r"pg_advisory_xact_lock") and conn.commits == 1 and out["reported"] == 1


def test_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(LU, "gather", lambda cur: ([OWEN], {"owen castellano": [CAREER]}))
    cur = Cur()
    conn = Conn(cur)
    LU.run(conn, apply=False)
    assert not cur.values and conn.commits == 0 and conn.rollbacks == 1
    assert not any(re.search(r"\b(INSERT INTO|UPDATE|DELETE FROM)\b", s) for s, _p in cur.sql)


def test_undo_puts_the_rows_back_to_nobody():
    cur = Cur()
    LU.undo(cur, "all")
    ups = [(s, p) for s, p in cur.sql if "SET person_id = NULLIF(l.from_person, 0)" in s]
    assert len(ups) == 2 and ups[0][1][1] == ["upload", "upload_mint"]
    assert cur.ran(r"DELETE FROM person_link_log WHERE rule = ANY")[0] == (["upload", "upload_mint"],)
    cur = Cur()
    LU.undo(cur, "upload_mint")
    assert cur.ran(r"DELETE FROM person_link_log")[0] == (["upload_mint"],)


def test_gather_reads_the_upload_rows_with_their_own_gender():
    sql = LU.upRowsSql("AND x", "AND y")
    assert "r.source = 'upload' AND r.person_id IS NULL AND x" in sql
    assert "m.source = r.source" in sql and "m.division" in sql and "r.event_short" in sql
    assert "COALESCE(r.is_relay, 0) = 0 AND y" in sql
    key = LU.nameKeyTextSql("n")
    assert "position(',' in n)" in key and "split_part(n, ',', 1)" in key


def test_the_tfrrs_linker_runs_uploads_as_pass_4_and_skips_detached_rows():
    src = read("scripts", "link_tfrrs_rows.py")
    assert "import link_uploads as LU" in src and "LU.run(conn, a.apply)" in src
    assert '"upload", "upload_mint"' in src and "XCP_LINK_UPLOADS" in src
    cur = Cur([(r"to_regclass", [("result_detach",)]), (r"SELECT count", [(0,)])])
    LT.fanout(cur, apply=False)
    nat = [s for s, _p in cur.sql if "CREATE TEMP TABLE tl_nat" in s][0]
    assert nat.count("NOT EXISTS (SELECT 1 FROM result_detach dt") == 2, \
        "a detached row's fresh person must not stop its tfrrs id fanning out"
    cur = Cur([(r"to_regclass", [("result_detach",)])])
    LT.fanout(cur, apply=True)
    stamp = [s for s, _p in cur.sql if "UPDATE results r SET person_id = n.person_id" in s][0]
    assert "result_detach dt WHERE dt.sport = 'XC'" in stamp
    # and no table, no clause: a database that never detached links as before
    assert PP.skipSql("XC", "r", on=False) == ""


def test_the_freshman_pass_never_joins_a_detached_person():
    src = read("scripts", "link_freshmen.py")
    assert "DELETE FROM lf_t WHERE person_id IN ({detachedPersonsSql()})" in src
    assert PP.detachedPersonsSql() == "SELECT to_person FROM result_detach"


# ======================================================== 3. the pins

def test_grade_from_the_request():
    assert PP.gradeFromRequest({"season": 2025, "grade": "11"}) == ("11", None)
    assert PP.gradeFromRequest({"season": 2025, "grade": "fr"}) == ("FR", "college")
    assert PP.gradeFromRequest({"season": 2025, "grade": None, "class_year": 2027}) == ("11", None)
    assert PP.gradeFromRequest({"season": 2025, "grade": "", "class_year": 2040}) is None


def test_grade_pins_are_honoured_on_every_rebuild():
    cur = Cur([(r"to_regclass", [("grade_pin",)]),
               (r"FROM grade_pin", [(7, 2025, "11", None)])])
    pins = PP.loadGradePins(cur)
    # grade_sanity's verdicts, as a rebuild makes them: the pin wins
    acad = {(7, 2025): {"grade": "10", "level": None, "method": "corroborated"},
            (7, 2024): {"grade": "9", "level": None, "method": "corroborated"}}
    assert PP.applyGradePins(acad, pins) == 1
    assert acad[(7, 2025)] == {"grade": "11", "level": None, "method": "pinned", "trust": "high"}
    assert acad[(7, 2024)]["grade"] == "9"
    # ...before the rules (evidence for the neighbours) and after them all
    src = read("engine", "grade_sanity.py")
    body = src[src.index("def resolve("):src.index("def write(")]
    first = body.index("_PP.applyGradePins(acad, grade_pins)")
    assert body.index("_CORROBORATED_SQL") < first < body.index("_BARE_CLASS_SQL")
    last = body.rindex("_PP.applyGradePins(acad, grade_pins)")
    assert body.index("trustByProgression(") < last < body.index("out = dict(acad)")
    # no table: nothing pinned, nothing breaks
    assert PP.loadGradePins(Cur([(r"to_regclass", [(None,)])])) == {}


def test_school_pins_are_honoured_on_every_rebuild(realDb):
    import build_ranking_results as B
    cur = Cur([(r"to_regclass", [("school_pin",)]),
               (r"FROM school_pin", [(7, 2025, "", "Jesuit"), (8, 2025, "TF", "Grant")])])
    try:
        assert B.loadSchoolPins(cur) == 2
        assert B.pinnedSchool(7, "XC", "2025-10-04") == "Jesuit"
        assert B.pinnedSchool(7, "TF", "2026-04-11") == "Jesuit"       # one academic year
        assert B.pinnedSchool(7, "XC", "2024-10-05") is None           # another season
        assert B.pinnedSchool(8, "XC", "2025-10-04") is None           # a track-only pin
        assert B.pinnedSchool(8, "TF", "2026-04-11") == "Grant"
        assert B.pinnedSchool(9, "XC", "2025-10-04") is None
    finally:
        B._SCHOOL_PIN.clear()
        B._SCHOOL_PIN_PEOPLE.clear()
    src = read("racecast", "build_ranking_results.py")
    prep = src[src.index("def prepareRow("):]
    assert "canonicalSchool(pinnedSchool(row.person_id, sport, row.date) or row.school)" in \
        prep[:prep.index("_isNonSchoolCached(school)")]
    assert "_np = loadSchoolPins(_cur)" in src


def test_a_pin_is_logged_and_undone():
    cur = Cur()
    PP.pinSchool(cur, 7, 2025, "Jesuit", "XC", "OR", 3, "o@x")
    PP.unpin(cur, "school", 7, 2025, "XC", "o@x")
    assert cur.ran(r"DELETE FROM school_pin")[0] == (7, 2025, "XC")
    acts = [p[1] for p in cur.ran(r"INSERT INTO person_pin_log")]
    assert acts == ["pin", "unpin"]


# ======================================================== 4. detach + undo

def test_detach_moves_the_row_to_a_fresh_person_and_undo_puts_it_back():
    cur = Cur([(r"SELECT person_id FROM results_tf WHERE result_id", [(7,)]),
               (r"to_regclass", [("x",)]), (r"GREATEST", [(0,)])])
    to = PP.detach(cur, "TF", 555, 7, fix_id=3, actor="o@x")
    from unlink import SPLIT_BASE
    assert to == SPLIT_BASE, "the first fresh id is SPLIT_BASE, above every mint"
    assert cur.ran(r"INSERT INTO result_detach")[0] == ("TF", 555, 7, SPLIT_BASE, 3, "o@x")
    upd = [(s, p) for s, p in cur.sql if "UPDATE results_tf r SET person_id = d.to_person" in s]
    assert upd and upd[0][1]["rid"] == 555
    assert not any("UPDATE results r SET" in s for s, _p in cur.sql), "only the row's own sport"
    # the row has moved since the request: nothing is written
    cur = Cur([(r"SELECT person_id FROM results WHERE result_id", [(99,)])])
    assert PP.detach(cur, "XC", 555, 7) is None and not cur.ran("INSERT INTO result_detach")
    # undo: back to from_person, the log row and the decision gone
    cur = Cur()
    PP.undoDetach(cur, "555", "o@x")
    back = [s for s, _p in cur.sql if "SET person_id = NULLIF(l.from_person, 0)" in s]
    assert len(back) == 2 and all("r.person_id = l.to_person" in s for s in back)
    assert cur.ran(r"DELETE FROM person_link_log")[0]["one"] == 555
    assert cur.ran(r"DELETE FROM result_detach")[0]["one"] == 555


def test_the_pipeline_reapplies_detaches_after_the_linkers():
    cur = Cur()
    PP.applyDetaches(cur)
    upd = [s for s, _p in cur.sql if "SET person_id = d.to_person" in s]
    assert len(upd) == 2 and all("r.person_id IS NULL OR r.person_id = d.from_person" in s for s in upd)
    sh = read("deploy", "run_pipeline.sh")
    order = ["step 04a_link_tfrrs", "step 04a2_link_teamless", "step 04a3_pins", "step 04_grade_sanity",
             "step 04c_twins", "step 04d_gender", "step 05c_pro_ability", "step 10_rankings_prepare"]
    at = [sh.index(s) for s in order]
    assert at == sorted(at), order
    assert "04a3_pins" in re.search(r'_ALWAYS="([^"]*)"', sh).group(1).split()
    ni = read("deploy", "nightly_update.sh")
    assert ni.index("step 04a2_link_teamless") < ni.index("step 04a3_pins") < ni.index("step 04c_twins")
    assert "04a2_link_teamless 04a3_pins 04b_wheelchair" in ni


# ======================================================== 5. twins

def _row(rid, date, ts, name, school, person=None, sport="XC"):
    return {"result_id": rid, "date": date, "ts": ts, "name": name, "school": school,
            "person_id": person, "sport": sport}


def test_an_uploaded_race_the_feed_scrapes_later_is_a_twin():
    import twin_flag as TF
    up = [_row(-1, "2025-10-04", 912.0, "Owen Castellano", "Jesuit High School"),
          _row(-2, "2025-10-04", 915.3, "Ann Doe", "Lakeside", person=55)]
    feed = [_row(10, "2025-10-04", 912.4, "Owen Castellano", "Jesuit"),       # whole s vs tenths
            _row(11, "2025-10-04", 915.3, "Annie Doe", "Lakeside", person=55)]  # same person
    assert TF.uploadTwins(up, feed) == [-2, -1]
    # another day, another time: not the race
    assert TF.uploadTwins(up, [_row(10, "2025-10-05", 912.0, "Owen Castellano", "Jesuit")]) == []
    assert TF.uploadTwins(up, [_row(10, "2025-10-04", 931.0, "Owen Castellano", "Jesuit")]) == []
    # school alone: only with one partner each way (a pack says nothing)
    blank = [_row(-3, "2025-10-04", 1001.2, "O. Castellano", "Jesuit")]
    one = [_row(20, "2025-10-04", 1001.2, "Owen Castellano", "Jesuit HS")]
    assert TF.uploadTwins(blank, one) == [-3]
    pack = one + [_row(21, "2025-10-04", 1001.2, "Ike Castellano", "Jesuit")]
    assert TF.uploadTwins(blank, pack) == []
    # track: to the hundredth
    t_up = [_row(-4, "2026-04-11", 61.23, "Owen Castellano", "Jesuit", sport="TF")]
    assert TF.uploadTwins(t_up, [_row(30, "2026-04-11", 61.23, "Owen Castellano", "Jesuit", sport="TF")]) == [-4]
    assert TF.uploadTwins(t_up, [_row(30, "2026-04-11", 61.27, "Owen Castellano", "Jesuit", sport="TF")]) == []


def test_the_twin_rule_flags_the_upload_side_and_reuses_the_feed_rules():
    import twin_flag as TF
    assert [r for r, _ in TF.RULES][:4] == ["twin_race", "twin_person", "twin_same_day", "twin_upload"]
    xc = TF.twinUploadSql("results", "XC")
    assert "WHERE  source = 'upload'" in xc and "r.source IN ('anet', 'tfrrs')" in xc
    assert "SELECT DISTINCT u_rid AS result_id" in xc, "the upload's copy is flagged, the feed's stays"
    assert "floor(s.ts::numeric + 0.000001)" in xc or "floor(u.ts::numeric + 0.000001)" in xc
    assert "WHERE strong OR (nu = 1 AND ns = 1)" in xc
    tf = TF.twinUploadSql("results_tf", "TF")
    assert "round(u.ts::numeric, 2) = round(s.ts::numeric, 2)" in tf and "is_relay" in tf


# ======================================================== 6. gender

def test_an_uploaded_xc_row_takes_its_divisions_gender(realDb):
    import backfill_normalize as bn
    bn._UPLOAD_DIV_GENDER.clear()
    cur = Cur([(r"FROM meets WHERE source = 'upload'",
                [(-900000007001, "Girls Varsity"), (-900000007002, "Boys JV"), (-900000007003, "Open")])])
    rows = cur.answers[0][1]

    class It(Cur):
        def __iter__(self):
            return iter(rows)
    it = It(cur.answers)
    assert bn._loadUploadDivGenders(it) == 2
    row = [None] * 32
    row[bn._SRC], row[bn._DIV] = "upload", -900000007001
    assert bn._blobGender(tuple(row), {}) == "F"
    row[bn._SRC] = "anet"
    assert bn._blobGender(tuple(row), {}) is None, "an anet row's gender is the athlete's"
    bn._UPLOAD_DIV_GENDER.clear()
    import person_gender as PG
    assert "(r.source = 'upload' AND m.source = 'upload')" in PG._BUILD


def test_the_site_serves_uploaded_races_and_names_the_feed():
    app = read("racecast", "app.py")
    for rule in ('@app.route("/race/xc/<int(signed=True):meet_id>/<int(signed=True):div_id>")',
                 '@app.route("/meet/xc/<int(signed=True):meet_id>")',
                 '@app.route("/race/tf/<int(signed=True):meet_id>/<int:event_id>/<int(signed=True):div_id>")',
                 '@app.route("/meet/tf/<int(signed=True):meet_id>")'):
        assert rule in app, rule
    assert '_FEEDS = ("anet", "tfrrs", "upload")' in app
    import usage as US
    assert US.normalizeRule("/race/xc/<int(signed=True):meet_id>/<int(signed=True):div_id>") == \
        "/race/xc/:meet_id/:div_id"
