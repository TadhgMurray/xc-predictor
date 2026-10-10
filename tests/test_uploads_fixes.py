"""Results uploads and suggested fixes (owner, 2026-10-10): staging, the
owner's approval paths, the permission checks, the decision mails.

    python -m pytest -q tests/test_uploads_fixes.py

Stub cursors only: no Postgres here. execute_values is replaced by a
recorder, so every write a path makes is visible to the test.
"""
import contextlib
import io
import json
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    @contextlib.contextmanager
    def _noConn():
        yield None
    _db.getConn = _noConn
    _db.initPool = lambda *a, **k: None
    sys.modules["database"] = _db

import pytest                                                    # noqa: E402
flask = pytest.importorskip("flask")
import psycopg2.extras                                           # noqa: E402
import accounts as AC                                            # noqa: E402
import decision_email as DE                                      # noqa: E402
import fixes as FX                                               # noqa: E402
import results_parse as RP                                       # noqa: E402
import uploads as UP                                             # noqa: E402

FIXT = os.path.join(ROOT, "tests", "fixtures", "hytek")


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def fixture(name):
    with open(os.path.join(FIXT, name), "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------- the stubs

class Cur:
    """Records every statement; answers from (pattern, rows) in order."""
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.sql = []
        self.values = []          # (sql, rows) from execute_values
        self.rows = []
        self.rowcount = 1
        self.connection = types.SimpleNamespace(rollback=lambda: None)

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self.rows = []
        self.rowcount = 1
        for pat, rows in self.answers:
            if re.search(pat, sql, re.S):
                self.rows = [dict(r) for r in rows]
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def ran(self, pat):
        return [p for s, p in self.sql if re.search(pat, s, re.S)]

    def wrote(self, pat):
        return [rows for s, rows in self.values if re.search(pat, s, re.S)]


class Conn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def cursor(self, *a, **k):
        raise AssertionError("a tool's own cursor was asked for")


@pytest.fixture(autouse=True)
def recorder(monkeypatch):
    def ev(cur, sql, rows, page_size=100, **k):
        rows = list(rows)
        cur.values.append((sql, rows))
        cur.rowcount = len(rows)
    monkeypatch.setattr(psycopg2.extras, "execute_values", ev)
    monkeypatch.setattr(psycopg2.extras, "Json", lambda x: x)
    monkeypatch.setitem(UP._READY, "checked", True)
    monkeypatch.setitem(UP._READY, "ready", True)
    monkeypatch.setitem(FX._READY, "checked", True)
    monkeypatch.setitem(FX._READY, "ready", True)
    monkeypatch.setattr(AC, "logEvent", lambda *a, **k: None)


def fakeDb(monkeypatch, cur):
    conn = Conn()

    @contextlib.contextmanager
    def _db():
        yield conn, cur
    monkeypatch.setattr(AC, "_db", _db)
    return conn


KID = {"sid_hash": "h", "csrf": "tok123", "account": {"id": 41, "email": "kid@example.com",
                                                     "name": "Owen Castellano", "role": "athlete"}}
OWNER = {"sid_hash": "o", "csrf": "own456", "account": {"id": 1, "email": "owner@racecast.co",
                                                       "name": "Owner", "role": "neither"}}
ORIGIN = {"Origin": "http://localhost"}


def client(monkeypatch, sess=KID):
    app = flask.Flask(__name__, template_folder=os.path.join(ROOT, "racecast", "templates"))
    for bp in (UP.bp, FX.bp):
        app.register_blueprint(bp)
    monkeypatch.setenv("XCP_ADMIN_EMAILS", "owner@racecast.co")
    monkeypatch.setattr(AC, "currentSession", lambda: sess)

    def render(name, **kw):
        return json.dumps({"tpl": name, **{k: v for k, v in kw.items()
                                           if isinstance(v, (str, bool, int)) or v is None}})
    monkeypatch.setattr(UP, "render_template", render)
    monkeypatch.setattr(FX, "render_template", render)
    return app.test_client()


def parsedXC():
    return RP.parse("mm_xc_lakeside.txt", fixture("mm_xc_lakeside.txt"))


def stagedRows(parsed, uid=7):
    """The staging rows stage() would write, as loadRows returns them."""
    evs = {e["n"]: e for e in parsed["events"]}
    out = []
    for n, r in enumerate(parsed["rows"], start=1):
        e = evs[r["event"]]
        out.append({"upload_id": uid, "n": n, "event_n": r["event"], "event": e["title"],
                    "gender": e["gender"], "division": e["division"], "distance_m": e["distance_m"],
                    "round": r["round"], "place": r["place"], "name": r["name"], "name_raw": r["name_raw"],
                    "grade": r["grade"], "school": r["school"], "time_text": r["time_text"],
                    "time_seconds": r["time_seconds"], "status": r["status"], "line": r["line"],
                    "warning": r.get("warning")})
    return out


def uploadRow(parsed, uid=7, status="submitted", **kw):
    up = {"id": uid, "account_id": 41, "status": status, "sport": parsed["sport"],
          "meet_name": parsed["meet"]["name"], "meet_date": parsed["meet"]["date"],
          "meet_date_end": parsed["meet"]["date_end"], "location": parsed["meet"]["location"],
          "state": "OR", "distance_m": None, "events": parsed["events"], "n_rows": len(parsed["rows"]),
          "file_name": "results.txt", "account_email": "kid@example.com", "account_name": "Owen Castellano"}
    up.update(kw)
    return up


# ================================================================ uploads: pure

def test_minted_ids_are_negative_stable_and_distinct():
    r = {"event_n": 1, "round": "F", "name": "Ann Doe", "time_seconds": 1065.21, "status": None,
         "place": 1, "n": 1}
    a, b = UP.mintResultId(7, r), UP.mintResultId(7, dict(r))
    assert a == b and a < 0
    assert UP.mintResultId(8, r) != a and UP.mintResultId(7, dict(r, n=2)) != a
    assert UP.meetIdFor(7) == -900_000_007
    divs = {UP.divIdFor(7, n) for n in range(1, 50)} | {UP.divIdFor(8, n) for n in range(1, 50)}
    assert len(divs) == 98 and max(divs) < 0
    assert UP.divIdFor(7, 1) != UP.meetIdFor(7)


def test_meet_names_that_read_alike_match():
    assert UP.similarity("2025 Lakeside XC Invitational", "Lakeside Invite") >= UP.NAME_SIM
    assert UP.similarity("35th Annual Nike Portland XC", "Nike Portland XC") >= UP.NAME_SIM
    assert UP.similarity("Lakeside Invitational", "Harbor League Finals") < UP.NAME_SIM
    got = UP.matchMeets("Lakeside Cross Country Invitational", "Lakeside Park, Fairview, OR", [
        {"name": "Lakeside Invitational", "venue": "Lakeside Park"},
        {"name": "Harbor League Finals", "venue": "Harbor Point"},
        {"name": "Saturday Morning Meet", "venue": "Lakeside Park Fairview OR"}])
    assert [g["name"] for g in got][:2] == ["Lakeside Invitational", "Saturday Morning Meet"] or \
        {g["name"] for g in got} == {"Lakeside Invitational", "Saturday Morning Meet"}
    assert all(g["name"] != "Harbor League Finals" for g in got)


def test_the_meet_details_are_checked():
    meta, err = UP.checkMeta({}, {"name": "Lakeside", "date": "2025-10-04"}, "XC")
    assert not err and meta["meet_name"] == "Lakeside" and meta["sport"] == "XC"
    meta, err = UP.checkMeta({"meet_name": "X", "meet_date": "2025-10-04", "sport": "TF",
                              "distance_m": "3 mi", "state": "or"}, {}, None)
    assert not err and meta["distance_m"] == 4828.0 and meta["state"] == "OR"
    assert UP.checkMeta({}, {}, None)[1]                                   # no name, date, sport
    assert UP.checkMeta({"meet_date": "2099-01-01"}, {"name": "X"}, "XC")[1]   # the future
    assert UP.checkMeta({"distance_m": "50"}, {"name": "X", "date": "2025-01-01"}, "XC")[1]
    assert UP.checkMeta({"state": "Oregon"}, {"name": "X", "date": "2025-01-01"}, "XC")[1]


def test_an_upload_with_a_race_and_no_distance_cannot_be_written():
    events = [{"n": 1, "title": "Varsity Girls", "kind": "running", "distance_m": None, "n_rows": 3}]
    up = {"meet_name": "X", "meet_date": "2025-10-04", "sport": "XC", "distance_m": None}
    assert UP.readyToApply(up, events) == ["no distance for Varsity Girls"]
    assert UP.readyToApply(dict(up, distance_m=5000.0), events) == []
    assert "no sport" in UP.readyToApply(dict(up, sport=None, distance_m=5000.0), events)


# ================================================================ uploads: staging

def test_staging_writes_the_upload_and_every_row_and_nothing_live():
    parsed = parsedXC()
    cur = Cur([(r"INSERT INTO result_upload \(", [{"id": 7}])])
    uid = UP.stage(cur, 41, "results.txt", fixture("mm_xc_lakeside.txt"), parsed,
                   {"note": "never posted"})
    assert uid == 7
    params = cur.ran(r"INSERT INTO result_upload \(")[0]
    assert params[0] == 41 and params[2] == "hytek_text" and params[7] == "XC"
    assert params[8] == "Lakeside Cross Country Invitational" and params[9] == "2025-10-04"
    assert params[-1] == "never posted"
    rows = cur.wrote(r"INSERT INTO result_upload_row")[0]
    assert len(rows) == len(parsed["rows"]) == 22
    assert rows[0][9] == "Ann Doe" and rows[0][14] == 1065.21
    # staging only: no raw table is touched
    for s, _p in cur.sql + cur.values:
        assert not re.search(r"INSERT INTO (results|results_tf|meets|meets_tf|meets_tf_meta)\b", s)


def test_the_form_overrides_what_the_file_says():
    parsed = parsedXC()
    cur = Cur([(r"INSERT INTO result_upload \(", [{"id": 9}])])
    UP.stage(cur, 41, "r.txt", b"x", parsed, {"meet_name": "Lakeside XC 2025", "sport": "XC"})
    assert cur.ran(r"INSERT INTO result_upload \(")[0][8] == "Lakeside XC 2025"


# ================================================================ uploads: approval

def test_approve_writes_cross_country_into_the_scrapers_tables_tagged_upload():
    parsed = parsedXC()
    up = uploadRow(parsed)
    cur = Cur()
    out = UP.applyUpload(cur, up, stagedRows(parsed), parsed["events"])
    meets = cur.wrote(r"INSERT INTO meets \(")[0]
    assert len(meets) == 3 and out["races"] == 3
    div_ids = {m[0] for m in meets}
    assert all(m[1] == UP.meetIdFor(7) and m[8] == "upload" and m[9] == "upload" for m in meets)
    assert meets[0][5] == 5000.0 and meets[2][5] == 3218.7 and meets[0][7] == "Girls Varsity"
    res = cur.wrote(r"INSERT INTO results \(")[0]
    assert len(res) == 22 and out["results"] == 22
    for r in res:
        assert r[0] < 0 and r[1] is None and r[2] is None            # no athlete, no person
        assert r[3] == "upload" and r[4] == "upload" and r[8] in div_ids
    dnf = next(r for r in res if r[6] == "Kim Brown")
    assert dnf[9] is None and dnf[16] == "DNF"
    assert not cur.wrote(r"results_tf") and not cur.ran(r"UPDATE (results|athletes)")


def test_approve_writes_track_into_meets_tf_meta_meets_tf_results_tf():
    parsed = RP.parse("mm_track_county.txt", fixture("mm_track_county.txt"))
    up = uploadRow(parsed, uid=12)
    cur = Cur()
    out = UP.applyUpload(cur, up, stagedRows(parsed, 12), parsed["events"])
    meta = cur.ran(r"INSERT INTO meets_tf_meta")[0]
    assert meta[0] == UP.meetIdFor(12) and meta[5] == "upload" and meta[2] == "2025-05-09"
    races = cur.wrote(r"INSERT INTO meets_tf \(")[0]
    assert [r[4] for r in races] == ["Girls 1600 Meter Run", "Boys 100 Meter Dash", "Boys 3200 Meter Run"]
    res = cur.wrote(r"INSERT INTO results_tf")[0]
    assert len(res) == out["results"] == 15
    titles = {r[10] for r in res}
    assert "Boys 100 Meter Dash Prelims" in titles and "Boys 100 Meter Dash" in titles
    assert all(r[3] == "upload" and r[1] is None and r[2] is None for r in res)
    assert not cur.wrote(r"INSERT INTO results \(")


def _decideCur(parsed, up, dupes=()):
    return Cur([
        (r"FROM result_upload WHERE id = %s FOR UPDATE", [up]),
        (r"to_regclass", [{"t": "x"}]),
        (r"FROM meets WHERE", list(dupes)),
        (r"FROM meets_tf_meta", []),
        (r"FROM meets_tfrrs", []),
        (r"FROM result_upload WHERE status = 'approved'", []),
        (r"FROM result_upload_row", stagedRows(parsed, up["id"])),
    ])


def test_a_meet_we_already_have_is_flagged_not_written():
    parsed = parsedXC()
    up = uploadRow(parsed)
    cur = _decideCur(parsed, up, [{"meet_id": 412345, "name": "Lakeside XC Invitational",
                                   "venue": "Lakeside Park", "date": "2025-10-04", "source": "anet"}])
    status, err = UP.decide(cur, 7, "approve", "owner@racecast.co")
    assert (status, err) == ("flagged", None)
    flagged = cur.ran(r"UPDATE result_upload SET status = 'flagged'")[0]
    assert flagged[0][0]["meet_id"] == 412345 and flagged[0][0]["where"] == "anet XC"
    assert not cur.values                         # nothing live
    # the date window is the upload's own day
    assert cur.ran(r"FROM meets WHERE")[0] == (["2025-10-04"],)


def test_a_new_meet_is_written_on_approve_and_recorded():
    parsed = parsedXC()
    up = uploadRow(parsed)
    cur = _decideCur(parsed, up)
    status, err = UP.decide(cur, 7, "approve", "owner@racecast.co", "thanks")
    assert (status, err) == ("approved", None)
    assert len(cur.wrote(r"INSERT INTO results \(")[0]) == 22
    upd = cur.ran(r"UPDATE result_upload SET status = 'approved'")[0]
    assert upd[0] == "owner@racecast.co" and upd[1] == "thanks" and upd[3] == UP.meetIdFor(7)
    assert upd[2]["forced"] is False and upd[2]["results"] == 22


def test_insert_anyway_is_only_for_a_flagged_upload_and_skips_the_check():
    parsed = parsedXC()
    cur = _decideCur(parsed, uploadRow(parsed))
    assert UP.decide(cur, 7, "force", "o@x")[1]              # submitted: refused
    cur = _decideCur(parsed, uploadRow(parsed, status="flagged"),
                     [{"meet_id": 1, "name": "Lakeside XC Invitational", "venue": "", "date": "2025-10-04"}])
    status, err = UP.decide(cur, 7, "force", "o@x")
    assert (status, err) == ("approved", None)
    assert not cur.ran(r"FROM meets WHERE") and cur.wrote(r"INSERT INTO results \(")
    assert cur.ran(r"status = 'approved'")[0][2]["forced"] is True


def test_reject_and_the_states_that_cannot_be_decided():
    parsed = parsedXC()
    cur = _decideCur(parsed, uploadRow(parsed))
    assert UP.decide(cur, 7, "reject", "o@x", "duplicate of athletic.net") == ("rejected", None)
    assert cur.ran(r"status = 'rejected'")[0] == ("o@x", "duplicate of athletic.net", 7)
    assert not cur.values
    for st in ("draft", "rejected", "discarded"):
        cur = _decideCur(parsed, uploadRow(parsed, status=st))
        status, err = UP.decide(cur, 7, "approve", "o@x")
        assert err and not cur.values
    cur = _decideCur(parsed, uploadRow(parsed, status="approved"))
    assert UP.decide(cur, 7, "approve", "o@x") == ("approved", "Already approved.")
    # a race with no distance anywhere cannot be written
    ev = [dict(e, distance_m=None) for e in parsed["events"]]
    cur = _decideCur(parsed, uploadRow(parsed, events=ev))
    status, err = UP.decide(cur, 7, "approve", "o@x")
    assert err.startswith("Cannot write it") and not cur.values


# ================================================================ uploads: routes

def test_signed_out_goes_to_login_and_strangers_get_a_404(monkeypatch):
    c = client(monkeypatch, sess=None)
    for path in ("/account/uploads", "/account/uploads/new", "/account/admin/uploads", "/account/admin/fixes"):
        r = c.get(path)
        assert r.status_code == 302 and "/login?next=" in r.headers["Location"], path
    c = client(monkeypatch, sess=KID)
    fakeDb(monkeypatch, Cur())
    assert c.get("/account/admin/uploads").status_code == 404
    assert c.get("/account/admin/uploads/7").status_code == 404
    assert c.post("/account/admin/uploads/7/decide", data={"csrf": "tok123", "action": "approve"},
                  headers=ORIGIN).status_code == 404
    assert c.get("/account/admin/fixes").status_code == 404
    assert c.post("/account/admin/fixes/3/decide", data={"csrf": "tok123", "action": "approve"},
                  headers=ORIGIN).status_code == 404


def test_upload_post_needs_csrf_and_this_origin(monkeypatch):
    c = client(monkeypatch)
    cur = Cur([(r"count\(\*\)", [{"h": 0, "d": 0}]), (r"INSERT INTO result_upload \(", [{"id": 7}])])
    fakeDb(monkeypatch, cur)
    data = {"csrf": "tok123", "file": (io.BytesIO(fixture("mm_xc_lakeside.txt")), "results.txt")}
    assert c.post("/account/uploads/new", data=data, content_type="multipart/form-data").status_code == 400
    data = {"csrf": "wrong", "file": (io.BytesIO(fixture("mm_xc_lakeside.txt")), "results.txt")}
    assert c.post("/account/uploads/new", data=data, headers=ORIGIN,
                  content_type="multipart/form-data").status_code == 400
    assert not cur.sql


def test_an_upload_is_parsed_staged_and_shown(monkeypatch):
    c = client(monkeypatch)
    cur = Cur([(r"count\(\*\)", [{"h": 0, "d": 1}]), (r"INSERT INTO result_upload \(", [{"id": 7}])])
    conn = fakeDb(monkeypatch, cur)
    data = {"csrf": "tok123", "file": (io.BytesIO(fixture("mm_xc_lakeside.txt")), "results.txt")}
    r = c.post("/account/uploads/new", data=data, headers=ORIGIN, content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/account/uploads/7")
    assert conn.commits == 1 and len(cur.wrote("result_upload_row")[0]) == 22


def test_bad_files_too_big_and_too_many_are_refused(monkeypatch):
    c = client(monkeypatch)
    cur = Cur([(r"count\(\*\)", [{"h": 0, "d": 0}])])
    fakeDb(monkeypatch, cur)
    for name, body in (("evil.exe", b"MZ..."), ("r.txt", b"MZ\x90\x00"), ("r.txt", b"   ")):
        data = {"csrf": "tok123", "file": (io.BytesIO(body), name)}
        r = c.post("/account/uploads/new", data=data, headers=ORIGIN, content_type="multipart/form-data")
        assert r.status_code == 400, name
    monkeypatch.setattr(UP, "MAX_UPLOAD_BYTES", 100)
    data = {"csrf": "tok123", "file": (io.BytesIO(fixture("mm_xc_lakeside.txt")), "r.txt")}
    r = c.post("/account/uploads/new", data=data, headers=ORIGIN, content_type="multipart/form-data")
    assert r.status_code == 400 and "9+MB" in r.headers["Location"]
    monkeypatch.setattr(UP, "MAX_UPLOAD_BYTES", 9 * 1024 * 1024)
    cur.answers = [(r"count\(\*\)", [{"h": UP.UPLOADS_PER_HOUR, "d": UP.UPLOADS_PER_HOUR}])]
    data = {"csrf": "tok123", "file": (io.BytesIO(fixture("mm_xc_lakeside.txt")), "r.txt")}
    r = c.post("/account/uploads/new", data=data, headers=ORIGIN, content_type="multipart/form-data")
    assert r.status_code == 429 and not cur.ran("INSERT INTO result_upload")
    assert UP.MAX_UPLOAD_BYTES == 9 * 1024 * 1024 == 9437184


def test_an_upload_is_its_uploaders_and_the_owners_only(monkeypatch):
    parsed = parsedXC()
    theirs = uploadRow(parsed, account_id=99)
    c = client(monkeypatch, sess=KID)
    fakeDb(monkeypatch, Cur([(r"FROM result_upload u LEFT JOIN account", [theirs]),
                             (r"FROM result_upload_row", [])]))
    assert c.get("/account/uploads/7").status_code == 404
    c = client(monkeypatch, sess=OWNER)
    assert c.get("/account/uploads/7").status_code == 200
    # sending someone else's upload: the UPDATE is keyed on the account
    c = client(monkeypatch, sess=KID)
    cur = Cur([(r"FOR UPDATE", [])])
    fakeDb(monkeypatch, cur)
    r = c.post("/account/uploads/7/submit", data={"csrf": "tok123", "action": "send"}, headers=ORIGIN)
    assert r.status_code == 404 and cur.ran(r"account_id = %s FOR UPDATE")[0] == (7, 41)


def test_sending_a_draft_puts_it_in_the_queue(monkeypatch):
    parsed = parsedXC()
    c = client(monkeypatch)
    cur = Cur([(r"FOR UPDATE", [uploadRow(parsed, status="draft")])])
    conn = fakeDb(monkeypatch, cur)
    r = c.post("/account/uploads/7/submit", headers=ORIGIN,
               data={"csrf": "tok123", "action": "send", "meet_name": "Lakeside XC", "meet_date": "2025-10-04",
                     "sport": "XC"})
    assert r.status_code == 302 and "Sent" in r.headers["Location"]
    p = cur.ran(r"UPDATE result_upload SET meet_name")[0]
    assert p[0] == "Lakeside XC" and p[-3] is True and conn.commits == 1


def test_the_owner_decides_and_the_uploader_is_emailed(monkeypatch):
    parsed = parsedXC()
    c = client(monkeypatch, sess=OWNER)
    cur = _decideCur(parsed, uploadRow(parsed))
    cur.answers.insert(0, (r"FROM result_upload u LEFT JOIN account", [uploadRow(parsed, status="approved")]))
    fakeDb(monkeypatch, cur)
    sent = []
    monkeypatch.setattr(AC, "sendMail", lambda to, subject, text, **k: sent.append((to, subject, text, k)) or True)
    assert c.post("/account/admin/uploads/7/decide", data={"csrf": "own456", "action": "approve"}).status_code == 403
    r = c.post("/account/admin/uploads/7/decide", headers=ORIGIN, data={"csrf": "own456", "action": "approve"})
    assert r.status_code == 302 and "emailed" in r.headers["Location"]
    assert sent and sent[0][0] == "kid@example.com" and sent[0][1].startswith("Added to Racecast")
    assert "html" in sent[0][3]
    r = c.post("/account/admin/uploads/7/decide", headers=ORIGIN, data={"csrf": "own456", "action": "force"})
    assert r.status_code == 400 and "Tick+the+box" in r.headers["Location"]


def test_the_csv_template_downloads(monkeypatch):
    c = client(monkeypatch)
    r = c.get("/account/uploads/template.csv")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/csv")
    assert r.get_data(as_text=True).startswith("meet_name,meet_date,location")


# ================================================================ fixes: pure

def test_fix_forms_for_every_kind():
    d, e = FX.parseFixForm({"kind": "grade", "grade_season": "2025", "grade": "11", "grade_sport": "XC"}, 7)
    assert e is None and d == {"kind": "grade", "season": 2025, "sport": "XC", "grade": "11",
                               "class_year": None, "note": ""}
    d, e = FX.parseFixForm({"kind": "grade", "grade_season": "2025", "class_year": "2027"}, 7)
    assert e is None and d["class_year"] == 2027
    assert FX.parseFixForm({"kind": "grade", "grade_season": "2025"}, 7)[1]
    assert FX.parseFixForm({"kind": "grade", "grade_season": "1850", "grade": "11"}, 7)[1]
    d, e = FX.parseFixForm({"kind": "school", "school_season": "2024", "school": " Jesuit ", "state": "or"}, 7)
    assert e is None and d["school"] == "Jesuit" and d["state"] == "OR"
    d, e = FX.parseFixForm({"kind": "not_mine", "result": "TF:-4611686018427387904"}, 7)
    assert e is None and d["sport"] == "TF" and d["result_id"] == -4611686018427387904
    assert FX.parseFixForm({"kind": "not_mine", "result": "XC;DROP"}, 7)[1]
    d, e = FX.parseFixForm({"kind": "missing", "url": "https://www.athletic.net/CrossCountry/meet/243130/results"}, 7)
    assert e is None and d["queue"] == [243130, "XC", "anet"]
    assert FX.parseFixForm({"kind": "missing", "url": "javascript:alert(1)"}, 7)[1]
    d, e = FX.parseFixForm({"kind": "same_person", "other_person_id": "https://racecast.co/athlete/31559611"}, 7)
    assert e is None and d["other_person_id"] == 31559611
    assert FX.parseFixForm({"kind": "same_person", "other_person_id": "/athlete/7"}, 7)[1]   # itself
    assert FX.parseFixForm({"kind": "rename_me"}, 7)[1]


def test_only_the_two_feeds_meets_can_be_queued():
    assert FX.queueTarget("https://www.athletic.net/TrackAndField/meet/543210/results/all") == (543210, "TF", "anet")
    assert FX.queueTarget("https://xc.tfrrs.org/results/xc/25113/Some_Meet") == (25113, "XC", "tfrrs")
    assert FX.queueTarget("https://www.tfrrs.org/results/84012/Meet") == (84012, "TF", "tfrrs")
    for u in ("https://www.milesplit.com/meets/1/results", "https://evil.com/athletic.net/CrossCountry/meet/1",
              "http://athletic.net.evil.com/CrossCountry/meet/1", "ftp://athletic.net/CrossCountry/meet/1"):
        assert FX.queueTarget(u) is None, u


# ================================================================ fixes: routes and approval

def test_only_the_claimant_can_suggest_a_fix(monkeypatch):
    c = client(monkeypatch)
    cur = Cur([(r"FROM account_claim", [])])
    fakeDb(monkeypatch, cur)
    assert c.get("/account/fixes/new?person=7").status_code == 403
    r = c.post("/account/fixes/new?person=7", headers=ORIGIN,
               data={"csrf": "tok123", "kind": "grade", "grade_season": "2025", "grade": "11"})
    assert r.status_code == 403 and not cur.ran("INSERT INTO fix_request")
    # the claim query names the account and the person, athlete claims only
    assert cur.ran(r"FROM account_claim")[0] == (41, 7)
    assert "kind IN ('athlete', 'coach_self')" in cur.sql[0][0]
    r = c.post("/account/fixes/new?person=7", data={"csrf": "tok123", "kind": "grade"})
    assert r.status_code == 400                         # no Origin


def _claimedCur(extra=()):
    return Cur(list(extra) + [
        (r"FROM account_claim", [{"x": 1}]),
        (r"count\(\*\) AS n FROM fix_request", [{"n": 0}]),
        (r"FROM athletes WHERE person_id", [{"name": "Owen Castellano", "school": "Jesuit", "gender": "M"}]),
        (r"to_regclass", [{"t": "x"}]),
        (r"FROM athlete_season", [{"sport": "XC", "year": 2025, "grade": "10", "school": "Jesuit",
                                   "state": "OR", "n_races": 6, "pool": "hs_m"}]),
        (r"SELECT \(SELECT count", [{"xc": 6, "tf": 4}]),
        (r"INSERT INTO fix_request", [{"id": 3}]),
    ])


def test_a_claimant_sends_a_fix_with_its_evidence(monkeypatch):
    c = client(monkeypatch)
    cur = _claimedCur()
    conn = fakeDb(monkeypatch, cur)
    r = c.post("/account/fixes/new?person=7", headers=ORIGIN,
               data={"csrf": "tok123", "kind": "grade", "grade_season": "2025", "grade": "11", "note": "I was a junior"})
    assert r.status_code == 302 and r.headers["Location"].startswith("/account/fixes?notice=Sent")
    p = cur.ran(r"INSERT INTO fix_request")[0]
    assert p[:3] == (41, 7, "grade") and p[3]["grade"] == "11" and p[5] == "I was a junior"
    assert p[4]["athlete"]["seasons"][0]["grade"] == "10"           # what the page said
    assert conn.commits == 1


def test_not_mine_must_name_a_row_on_this_page(monkeypatch):
    c = client(monkeypatch)
    row = {"sport": "XC", "result_id": 555, "date": "2025-10-04", "time_seconds": 912.4, "school": "Jesuit",
           "athlete_name": "Owen Castellano", "source": "anet", "athlete_id": 12, "meet_id": 1, "meet_name": "M"}
    cur = _claimedCur([(r"WHERE r.result_id = %s", [row]), (r"FROM person_link_log", []),
                       (r"SELECT person_id FROM results WHERE", [{"person_id": 999}])])
    fakeDb(monkeypatch, cur)
    r = c.post("/account/fixes/new?person=7", headers=ORIGIN,
               data={"csrf": "tok123", "kind": "not_mine", "result": "XC:555"})
    assert r.status_code == 400 and "not+on+this+page" in r.headers["Location"]
    assert not cur.ran("INSERT INTO fix_request")


def test_the_daily_limit_on_fixes(monkeypatch):
    c = client(monkeypatch)
    cur = _claimedCur([(r"count\(\*\) AS n FROM fix_request", [{"n": FX.FIXES_PER_DAY}])])
    fakeDb(monkeypatch, cur)
    r = c.post("/account/fixes/new?person=7", headers=ORIGIN,
               data={"csrf": "tok123", "kind": "grade", "grade_season": "2025", "grade": "11"})
    assert r.status_code == 429 and not cur.ran("INSERT INTO fix_request")


def fixRow(kind, detail, **kw):
    f = {"id": 3, "account_id": 41, "person_id": 7, "kind": kind, "status": "open", "detail": dict(detail, kind=kind),
         "evidence": {"athlete": {"name": "Owen Castellano"}, "other": {"name": "Owen Castellano"}}}
    f.update(kw)
    return f


@pytest.mark.parametrize("kind,detail", [("grade", {"season": 2025, "grade": "11"}),
                                         ("school", {"season": 2025, "school": "Jesuit"}),
                                         ("not_mine", {"sport": "XC", "result_id": 555})])
def test_approving_a_kind_with_no_mechanism_records_it_and_touches_nothing(kind, detail):
    cur = Cur([(r"FROM fix_request WHERE id = %s FOR UPDATE", [fixRow(kind, detail)])])
    status, err, outcome = FX.decide(Conn(), cur, 3, "approve", "owner@racecast.co", "ok")
    assert (status, err) == ("approved", None) and "Recorded" in outcome
    upd = cur.ran(r"UPDATE fix_request SET status = 'approved'")[0]
    assert upd[3]["missing_support"] == FX.MISSING_SUPPORT[kind] and upd[3]["applied"] is None
    for s, _p in cur.sql:
        assert not re.search(r"\b(UPDATE|INSERT INTO|DELETE FROM)\s+(results|results_tf|athletes|meet_queue|"
                             r"person_link_log|profile_school_merge)\b", s)


def test_approving_a_missing_feed_meet_queues_it_like_find_meet():
    f = fixRow("missing", {"url": "https://www.athletic.net/CrossCountry/meet/243130", "queue": [243130, "XC", "anet"]})
    cur = Cur([(r"FOR UPDATE", [f])])
    status, err, outcome = FX.decide(Conn(), cur, 3, "approve", "o@x")
    assert status == "approved" and "queued" in outcome
    q = cur.ran(r"INSERT INTO meet_queue")[0]
    assert q == (243130, "XC", "anet") and "SET scraped = 0" in cur.sql[1][0]
    f = fixRow("missing", {"url": "https://www.milesplit.com/meets/1", "queue": None})
    cur = Cur([(r"FOR UPDATE", [f])])
    status, err, outcome = FX.decide(Conn(), cur, 3, "approve", "o@x")
    assert status == "approved" and not cur.ran("meet_queue")
    assert cur.ran("UPDATE fix_request")[0][3]["missing_support"] == FX.MISSING_SUPPORT["missing_other"]


def test_approving_same_person_goes_through_link_profile_school(monkeypatch):
    import link_profile_school as LPS
    calls = []
    monkeypatch.setattr(LPS, "write", lambda conn, decisions: calls.append(decisions) or {"XC rows moved": 3})
    f = fixRow("same_person", {"other_person_id": 31559611})
    cur = Cur([(r"FOR UPDATE", [f])])
    status, err, outcome = FX.decide(Conn(), cur, 3, "approve", "o@x", None, keep=None)
    assert status == "approved" and err is None
    assert calls == [[(31559611, 7, "Owen Castellano", "fix request #3")]]   # the claimed page survives
    assert cur.ran(r"UPDATE fix_request SET applied")[0][0]["applied"]["undo"] == \
        "python scripts/link_profile_school.py --undo 31559611"
    # the owner may keep the other page instead; never a third
    calls.clear()
    cur = Cur([(r"FOR UPDATE", [f])])
    FX.decide(Conn(), cur, 3, "approve", "o@x", None, keep=31559611)
    assert calls == [[(7, 31559611, "Owen Castellano", "fix request #3")]]
    cur = Cur([(r"FOR UPDATE", [f])])
    assert FX.decide(Conn(), cur, 3, "approve", "o@x", None, keep=12345)[1] == "Keep one of the two pages."
    assert not cur.ran("UPDATE fix_request")


def test_reject_and_decided_requests():
    cur = Cur([(r"FOR UPDATE", [fixRow("grade", {"season": 2025, "grade": "11"})])])
    assert FX.decide(Conn(), cur, 3, "reject", "o@x", "the meet says 10") == ("rejected", None, None)
    assert cur.ran("status = 'rejected'")[0] == ("o@x", "the meet says 10", 3)
    cur = Cur([(r"FOR UPDATE", [fixRow("grade", {}, status="approved")])])
    assert FX.decide(Conn(), cur, 3, "approve", "o@x")[1] == "This request has been decided."


def test_the_requester_is_emailed_on_the_decision(monkeypatch):
    c = client(monkeypatch, sess=OWNER)
    f = fixRow("grade", {"season": 2025, "grade": "11"})
    cur = Cur([(r"FOR UPDATE", [f]), (r"SELECT \* FROM fix_request WHERE id", [dict(f, status="approved")]),
               (r"SELECT email, name FROM account", [{"email": "kid@example.com", "name": "Owen Castellano"}])])
    fakeDb(monkeypatch, cur)
    sent = []
    monkeypatch.setattr(AC, "sendMail", lambda to, subject, text, **k: sent.append((to, subject, text)) or True)
    r = c.post("/account/admin/fixes/3/decide", headers=ORIGIN, data={"csrf": "own456", "action": "approve"})
    assert r.status_code == 302 and "emailed" in r.headers["Location"]
    assert sent[0][0] == "kid@example.com" and sent[0][1] == "Fix approved: Owen Castellano"
    assert "Wrong grade or class year" in sent[0][2]


# ================================================================ the mails

def test_decision_mails_are_plain_and_name_no_file_content():
    up = {"id": 7, "meet_name": "Lakeside Invitational", "meet_date": "2025-10-04", "n_rows": 22,
          "file_name": "results.txt", "account_name": "Owen Castellano"}
    subject, text, html = DE.uploadDecision(up, "approved", None, "https://racecast.co")
    assert subject == "Added to Racecast: Lakeside Invitational"
    assert text.startswith("Hi Owen,") and "Results: 22" in text and "https://racecast.co/account/uploads/7" in text
    assert "<script" not in html and "RACECAST" in html and "—" not in text
    subject, text, html = DE.uploadDecision(dict(up, meet_name="<b>x</b>"), "rejected", "dup", "https://racecast.co")
    assert subject.startswith("Not added") and "&lt;b&gt;" in html and "Note: dup" in text
    subject, text, html = DE.fixDecision({"kind": "same_person", "person_id": 7}, "approved", "Joined.", None,
                                         "https://racecast.co", "Owen Castellano", "Owen Castellano")
    assert subject == "Fix approved: Owen Castellano" and "What happens: Joined." in text


# ================================================================ the wiring

def test_the_wiring():
    app = read("racecast", "app.py")
    assert "import uploads as _uploads" in app and "import fixes as _fixes" in app
    assert "_uploads.bp, _fixes.bp" in app
    assert '"/account"' in app                      # every route here is private (no-store)
    ath = read("racecast", "templates", "athlete.html")
    assert ath.count('{% include "_suggest_fix.html" %}') == 1
    assert ath.index('{% include "_suggest_fix.html" %}') < ath.index("</div></header>")
    inc = read("racecast", "templates", "_suggest_fix.html")
    assert " hidden" in inc and "xcp:me" in inc and "/account/fixes/new?person=" in inc
    assert "/account/uploads/new?sport=" in read("racecast", "templates", "meets.html")
    assert "/account/uploads/new?sport=" in read("racecast", "templates", "school.html")
    for t in ("uploads.html", "upload_view.html", "admin_uploads.html", "fixes.html", "admin_fixes.html"):
        src = read("racecast", "templates", t)
        assert "contrib.css" in src and 'name="robots" content="noindex"' in src
        assert "|safe" not in src, t                 # nothing from a file or a form unescaped
        for form in re.findall(r'<form method="post".*?</form>', src, re.S):
            assert 'name="csrf"' in form, t
    for mod in ("uploads.py", "fixes.py"):
        src = read("racecast", mod)
        assert src.count("requireAdmin()") >= 2 and "csrfOk(sess)" in src
    assert "DROP " not in UP.DDL and "DROP " not in FX.DDL
    assert "CREATE TABLE IF NOT EXISTS result_upload (" in UP.DDL
    assert "CREATE TABLE IF NOT EXISTS result_upload_row (" in UP.DDL
    assert "CREATE TABLE IF NOT EXISTS fix_request (" in FX.DDL


def test_the_templates_render(monkeypatch):
    """Every new page through the real app's Jinja environment."""
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    try:
        import app as A
    except Exception as exc:                                    # noqa: BLE001
        pytest.skip(f"app does not import here: {exc}")
    import datetime
    from flask import render_template
    parsed = parsedXC()
    up = uploadRow(parsed, status="draft", warnings=parsed["warnings"], file_kind="hytek_text",
                   software="Meet Manager", decided_at=None, submitted_at=None, note=None, raw_text="x",
                   file_bytes=10, applied=None, dupes=None)
    rows = stagedRows(parsed)
    fx = dict(fixRow("same_person", {"other_person_id": 31559611}), summary="same person as /athlete/31559611",
              created_at=datetime.datetime(2026, 10, 10, 9, 0), account_email="kid@example.com", note=None,
              now={"person_id": 7, "name": "Owen", "seasons": [], "n_xc": 1, "n_tf": 0},
              other_now={"person_id": 31559611, "name": "Owen", "seasons": [], "n_xc": 1, "n_tf": 0})
    snap = {"person_id": 7, "name": "Owen Castellano", "seasons": [{"year": 2025, "sport": "XC", "grade": "10",
                                                                     "school": "Jesuit"}], "n_xc": 6, "n_tf": 2}
    with A.app.test_request_context("/account/uploads/7"):
        pages = [
            render_template("uploads.html", mode="new", uploads=[], csrf="t", sport="XC", school="Jesuit",
                            max_mb=9, per_day=12, notice="", error="", status_words=UP.STATUS_WORDS),
            render_template("uploads.html", mode="list", uploads=[dict(up, created_at=datetime.datetime(2026, 10, 9))],
                            csrf="t", notice="", error="", status_words=UP.STATUS_WORDS),
            render_template("upload_view.html", up=up, rows=rows, events=parsed["events"], csrf="t", gaps=[],
                            preview_rows=400, sports=UP.SPORTS, mine=True, notice="", error="",
                            status_words=UP.STATUS_WORDS),
            render_template("admin_uploads.html", mode="one", up=dict(up, status="submitted"), rows=rows,
                            events=parsed["events"], dupes=[{"where": "anet XC", "name": "Lakeside", "venue": "",
                                                             "date": "2025-10-04", "meet_id": 1, "name_sim": 0.8,
                                                             "venue_sim": 0.0}],
                            csrf="t", gaps=UP.PIPELINE_GAPS, apply_gaps=[], raw="x", notice="", error="",
                            status_words=UP.STATUS_WORDS),
            render_template("fixes.html", mode="new", person_id=7, snap=snap, results=[], kind="grade",
                            kinds=FX.KINDS, kind_words=FX.KIND_WORDS, grades=FX.GRADES, csrf="t", note_max=1000,
                            error=""),
            render_template("admin_fixes.html", open_=[fx], decided=[], csrf="t", kind_words=FX.KIND_WORDS,
                            missing=FX.MISSING_SUPPORT, notice="", error=""),
        ]
    assert "Lakeside Cross Country Invitational" in pages[2] and "Ann Doe" in pages[2]
    assert 'name="csrf" value="t"' in pages[3] and "Approve" in pages[3]
    assert "link_profile_school" in pages[5] and "keep #31559611" in pages[5]
