"""Follows, alerts, My page, team calendars, the shortlist (owner, 2026-10-10).

    python -m pytest -q tests/test_follows.py

Stub cursors only: no Postgres here. The pure rules are tested directly; the
routes through a Flask test client with the session and the database faked.
"""
import contextlib
import datetime
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
import accounts as AC                                            # noqa: E402
import follows as F                                              # noqa: E402
import follow_alerts as FA                                       # noqa: E402
import my_page as M                                              # noqa: E402
import shortlist as SL                                           # noqa: E402
import team_meets as TM                                          # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


# ---------------------------------------------------------------- the stubs

class Cur:
    """Records every statement; answers from a list of (pattern, rows)."""
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.sql = []
        self.rows = []
        self.rowcount = 1
        self.connection = types.SimpleNamespace(rollback=lambda: None)

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self.rows = []
        for pat, rows in self.answers:
            if re.search(pat, sql, re.S):
                self.rows = [dict(r) for r in rows]
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class Conn:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def fakeDb(monkeypatch, cur):
    conn = Conn()

    @contextlib.contextmanager
    def _db():
        yield conn, cur
    monkeypatch.setattr(AC, "_db", _db)
    monkeypatch.setitem(F._READY, "checked", True)
    monkeypatch.setitem(F._READY, "ready", True)
    return conn


SESSION = {"sid_hash": "h", "csrf": "tok123", "account": {"id": 41, "email": "kid@example.com",
                                                         "name": "Kid", "role": "athlete"}}


def client(monkeypatch, signed_in=True):
    app = flask.Flask(__name__, template_folder=os.path.join(ROOT, "racecast", "templates"))
    for bp in (F.bp, SL.bp, TM.bp, M.bp):
        app.register_blueprint(bp)
    monkeypatch.setattr(AC, "currentSession", lambda: SESSION if signed_in else None)
    monkeypatch.setattr(F, "render_template", lambda name, **kw: json.dumps(
        {"tpl": name, **{k: v for k, v in kw.items() if isinstance(v, (str, bool, int))}}))
    return app.test_client()


ORIGIN = {"Origin": "http://localhost"}


# ---------------------------------------------------------------- follows: pure

def test_follow_requests_are_parsed_and_checked():
    row, err = F.parseFollow({"kind": "athlete", "person_id": "7"})
    assert err is None and row == {"kind": "athlete", "person_id": 7, "school": None, "state": None, "level": None}
    assert F.parseFollow({"kind": "athlete", "person_id": "x"})[1]
    assert F.parseFollow({"kind": "athlete", "person_id": "0"})[1]
    assert F.parseFollow({"kind": "who"})[1]
    row, err = F.parseFollow({"kind": "team", "school": "Jesuit", "state": "or", "level": "hs"})
    assert err is None and row["state"] == "OR" and row["level"] == "hs"
    assert F.parseFollow({"kind": "team", "school": "Jesuit", "state": "Oregon"})[1]
    assert F.parseFollow({"kind": "team", "school": "Jesuit", "level": "pro"})[1]
    assert F.parseFollow({"kind": "team", "school": ""})[1]
    assert F.subjectKey({"kind": "athlete", "person_id": 7}) == "athlete:7"
    assert F.subjectKey({"kind": "team", "school": "Jesuit", "state": "OR", "level": None}) == "team:Jesuit|OR|"


def test_unsubscribe_links_are_signed_and_name_no_address():
    t = F.unsubToken(41, "s3cret")
    assert len(t) == 64 and F.unsubOk(41, t, "s3cret")
    assert not F.unsubOk(42, t, "s3cret")             # another account
    assert not F.unsubOk(41, t, "other")              # another secret
    assert not F.unsubOk(41, "", "s3cret")
    assert F.unsubToken(41, "") == "" and not F.unsubOk(41, "", "")   # no secret, no link works
    url = F.unsubUrl(41, "https://racecast.co", "s3cret")
    assert url.startswith("https://racecast.co/account/unsubscribe?a=41&t=") and "@" not in url


def test_ddl_is_idempotent_cascades_and_never_runs_per_request():
    for t in F.TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {t} (" in F.DDL
    assert F.DDL.count("ON DELETE CASCADE") >= 4       # an account's follows, prefs, items, shortlist go with it
    assert "PRIMARY KEY (account_id, item_key)" in F.DDL   # the dedupe is the key
    src = read("racecast", "follows.py")
    # ! DDL ONLY FROM --init: the one call is in main()
    calls = [m.start() for m in re.finditer(r"(?<!def )initTables\(conn\)", src)]
    assert len(calls) == 1 and calls[0] > src.index("def main(")
    for mod in ("my_page.py", "shortlist.py", "team_meets.py", "follow_alerts.py"):
        assert "CREATE TABLE" not in read("racecast", mod)


# ---------------------------------------------------------------- follows: routes

def test_follow_state_signed_out_and_in(monkeypatch):
    c = client(monkeypatch, signed_in=False)
    assert c.get("/api/follow/state?kind=athlete&person_id=7").get_json() == {"signed_in": False}
    cur = Cur([(r"FROM account_follow", [{"x": 1}]), (r"FROM alert_pref", [{"cadence": "weekly"}])])
    fakeDb(monkeypatch, cur)
    c = client(monkeypatch)
    j = c.get("/api/follow/state?kind=athlete&person_id=7").get_json()
    assert j == {"signed_in": True, "ready": True, "following": True, "cadence": "weekly", "csrf": "tok123"}


def test_follow_needs_csrf_and_this_origin(monkeypatch):
    cur = Cur([(r"FROM athlete_season", [{"x": 1}]), (r"SELECT 1 AS x FROM account_follow", [{"x": 1}])])
    conn = fakeDb(monkeypatch, cur)
    c = client(monkeypatch)
    body = {"kind": "athlete", "person_id": "7", "action": "follow"}
    assert c.post("/api/follow", data=body, headers={"X-CSRF": "tok123"}).status_code == 400      # no Origin
    assert c.post("/api/follow", data=body, headers={"X-CSRF": "tok123",
                                                    "Origin": "http://evil.com"}).status_code == 400
    assert c.post("/api/follow", data=body, headers={"X-CSRF": "wrong", **ORIGIN}).status_code == 400
    assert not any("INSERT INTO account_follow" in s for s, _ in cur.sql)
    r = c.post("/api/follow", data=body, headers={"X-CSRF": "tok123", **ORIGIN})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "following": True}
    ins = [p for s, p in cur.sql if "INSERT INTO account_follow" in s]
    assert ins == [(41, "athlete", 7, None, None, None)] and conn.commits == 1
    # signed out: refused before anything else
    c2 = client(monkeypatch, signed_in=False)
    assert c2.post("/api/follow", data=body, headers={"X-CSRF": "tok123", **ORIGIN}).status_code == 401


def test_unfollow_drops_the_subjects_waiting_items(monkeypatch):
    cur = Cur()
    fakeDb(monkeypatch, cur)
    c = client(monkeypatch)
    r = c.post("/api/follow", data={"kind": "team", "school": "Jesuit", "state": "OR", "action": "unfollow"},
               headers={"X-CSRF": "tok123", **ORIGIN})
    assert r.status_code == 200
    drop = [p for s, p in cur.sql if "DELETE FROM alert_item" in s]
    assert drop == [(41, "team:Jesuit|OR|")]


def test_alert_setting_is_a_csrf_post(monkeypatch):
    cur = Cur()
    fakeDb(monkeypatch, cur)
    c = client(monkeypatch)
    assert c.post("/account/alerts", data={"cadence": "weekly", "csrf": "nope"}, headers=ORIGIN).status_code == 400
    assert c.post("/account/alerts", data={"cadence": "hourly", "csrf": "tok123"}, headers=ORIGIN).status_code == 400
    r = c.post("/account/alerts", data={"cadence": "weekly", "csrf": "tok123"}, headers=ORIGIN)
    assert r.status_code == 302 and "/account/me" in r.headers["Location"]
    assert [p for s, p in cur.sql if "INSERT INTO alert_pref" in s] == [(41, "weekly")]


def test_unsubscribe_get_changes_nothing_post_needs_the_signature(monkeypatch):
    monkeypatch.setenv("XCP_ALERT_SECRET", "s3cret")
    cur = Cur()
    fakeDb(monkeypatch, cur)
    c = client(monkeypatch, signed_in=False)
    t = F.unsubToken(41)
    r = c.get(f"/account/unsubscribe?a=41&t={t}")
    assert r.status_code == 200 and json.loads(r.data)["valid"] is True and not cur.sql   # a scanner's GET
    assert c.post("/account/unsubscribe", data={"a": "41", "t": "forged"}).status_code == 400
    assert c.post("/account/unsubscribe", data={"a": "42", "t": t}).status_code == 400
    assert not any("alert_pref" in s for s, _ in cur.sql)
    # ★ the mail client's one-click POST (RFC 8058): token in the query, no session, no Origin
    r = c.post(f"/account/unsubscribe?a=41&t={t}", data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and json.loads(r.data)["done"] is True
    assert [p for s, p in cur.sql if "INSERT INTO alert_pref" in s] == [(41, "off")]


def test_return_route_signs_in_then_sets_the_hint(monkeypatch):
    c = client(monkeypatch, signed_in=False)
    r = c.get("/account/return?next=/athlete/7")
    assert r.status_code == 302 and r.headers["Location"].startswith("/login?next=")
    c = client(monkeypatch)
    r = c.get("/account/return?next=/athlete/7")
    assert r.headers["Location"].endswith("/athlete/7")
    assert "rc_si=1" in r.headers.get("Set-Cookie", "") and "HttpOnly" not in r.headers["Set-Cookie"]
    # never off-site
    assert c.get("/account/return?next=//evil.com").headers["Location"].endswith("/account/me")


def test_every_new_post_route_checks_csrf():
    for mod in ("follows.py", "shortlist.py"):
        src = read("racecast", mod)
        for m in re.finditer(r'@bp\.route\("([^"]+)", methods=\[([^\]]+)\]\)\s*\ndef (\w+)\(\):(.*?)(?=\n@bp|\n# ----|\Z)',
                             src, re.S):
            path, methods, body = m.group(1), m.group(2), m.group(4)
            if "POST" not in methods:
                continue
            if path == "/account/unsubscribe":
                assert "unsubOk(" in body            # the signature is its CSRF
            else:
                assert "csrfOk(" in body, path


# ---------------------------------------------------------------- alerts: pure

def _rr(rid, pid, day, rating, sport="XC", pool="hs_m", **kw):
    return {"result_id": rid, "person_id": pid, "sport": sport, "pool": pool, "race_date": day,
            "speed_rating": rating, "time_seconds": kw.get("t"), "distance": kw.get("d", 5000),
            "meet_id": kw.get("meet", rid), "div_id": 1, "meet_name": kw.get("mn", f"Meet {rid}"),
            "name": kw.get("name", "Owen"), "race_href": f"/race/xc/{rid}/1"}


def test_season_bests_are_times_at_the_same_distance_before_this_day():
    rows = [_rr(1, 7, "2026-09-01", 110, t=960.0), _rr(2, 7, "2026-09-08", 112, t=950.0),
            _rr(3, 7, "2026-09-08", 115, t=940.0),      # same day as 2: beats 960, not 950's day
            _rr(4, 7, "2026-09-15", 114, t=945.0), _rr(5, 7, "2026-09-22", 116, t=930.0),
            _rr(6, 7, "2026-09-29", 118, t=600.0, d=3000),   # another distance: nothing to beat
            _rr(7, 8, "2026-09-01", 99, t=1100.0)]           # one race: nothing to beat
    assert FA.seasonBests(rows) == {2: 960.0, 3: 960.0, 5: 940.0}
    assert FA.priorRatings(rows)[5] == 114 and 1 not in FA.priorRatings(rows)


def test_race_items_carry_pr_season_best_breakout_and_the_previous_rating():
    r = _rr(9, 7, "2026-09-27", 123.46, t=912.4, mn="Nike Portland XC")
    key, p = FA.raceItem(r, {("XC", 9): {"prev_best": 925.0}}, {("XC", 9): {"jump": 4.13, "base": 119.3}},
                         {9: 930.1}, {9: 122.2})
    assert key == "race:XC:9" and p["result_id"] == 9
    assert p["flags"] == {"pr": {"prev": 925.0}, "sr": {"prev": 930.1}, "jump": {"by": 4.1, "base": 119.3}}
    assert p["rating"] == 123.5 and p["prev_rating"] == 122.2


def test_places_share_on_ties_and_the_team_finish_is_the_race_pages_scoring():
    rows = [{"result_id": i, "time_seconds": t, "school": s, "person_id": i, "athlete_name": f"R{i}"}
            for i, (t, s) in enumerate([(900, "A"), (905, "B"), (905, "A"), (910, "A"), (911, "B"),
                                        (912, "A"), (913, "B"), (914, "A"), (915, "B"), (916, "B"),
                                        (917, "C")], start=1)]
    assert FA.placesOf(rows)[:4] == [1, 2, 2, 4]
    a = FA.teamResult(rows, "A")
    assert a["place"] == 1 and a["n_teams"] == 2 and a["points"] == 1 + 3 + 4 + 6 + 8
    assert [x["name"] for x in a["runners"]] == ["R1", "R3", "R4", "R6", "R8"]
    assert a["runners"][1]["place"] == 2          # the race place (a tie), not the scoring place and FA.teamResult(rows, "C") is None


def test_race_context_adds_place_field_course_and_team(monkeypatch):
    field = [{"result_id": i, "person_id": i, "school": "A" if i % 2 else "B", "time_seconds": 900 + i,
              "athlete_name": f"R{i}"} for i in range(1, 13)]
    cur = Cur([(r"WHERE result_id = %s", [{"result_id": 5, "meet_id": 77, "div_id": 3, "source": "anet",
                                           "school": "A"}]),
               (r"ORDER  BY time_seconds", field),
               (r"FROM meets", [{"course_name": "Blue Lake Park", "division": "Varsity Boys"}])])
    p = FA.raceContext(FA.Races(cur), {"type": "race", "sport": "XC", "result_id": 5})
    assert p["place"] == 5 and p["field"] == 12 and p["course"] == "Blue Lake Park"
    assert p["team"]["school"] == "A" and p["team"]["n_teams"] == 2


def test_team_items_are_one_per_meet_with_pr_names():
    rows = [_rr(1, 7, "2026-09-27", 120, meet=55, name="A"), _rr(2, 8, "2026-09-27", 124, meet=55, name="B"),
            _rr(3, 9, "2026-09-27", 101, meet=55, name="C"), _rr(4, 7, "2026-10-04", 121, meet=56, name="A")]
    items = dict(FA.teamItems("team:Jesuit|OR|", rows, {("XC", 3): {}}, {("XC", 1): {}, ("XC", 2): {}}))
    assert set(items) == {"team:Jesuit|OR|:XC:55", "team:Jesuit|OR|:XC:56"}
    p = items["team:Jesuit|OR|:XC:55"]
    assert p["n_rated"] == 3 and p["pr_names"] == ["C"] and p["jump_names"] == ["A", "B"]
    assert p["result_ids"] == [1, 2, 3]


def test_due_daily_weekly_off():
    now = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)
    assert FA.isDue("daily", None, now)
    assert not FA.isDue("daily", now - datetime.timedelta(hours=3), now)      # once a day
    assert FA.isDue("daily", now - datetime.timedelta(days=1), now)
    assert not FA.isDue("weekly", now - datetime.timedelta(days=6), now)
    assert FA.isDue("weekly", now - datetime.timedelta(days=7), now)
    assert not FA.isDue("off", None, now)


_RACE = {"type": "race", "sport": "XC", "person_id": 7, "result_id": 9, "name": "Ezra Goldfarb",
         "pool": "hs_m", "date": "2026-10-03", "meet_name": "Nike Portland XC", "distance": 5000,
         "time": 912.4, "rating": 118.9, "prev_rating": 117.6, "href": "/race/xc/41237/2",
         "place": 12, "field": 184, "course": "Blue Lake Park", "division": "Varsity Boys",
         "team": {"school": "Lincoln", "place": 3, "n_teams": 22, "points": 98},
         "flags": {"pr": {"prev": 925.0}, "sr": {"prev": 930.1}, "jump": {"by": 4.1, "base": 114.8}}}
_TEAM = {"type": "team", "sport": "XC", "meet_id": 41237, "date": "2026-10-03", "meet_name": "Nike Portland XC",
         "n_rated": 7, "href": "/race/xc/41237/2", "pr_names": ["Ben Sato", "Miles Okafor"], "n_pr": 2,
         "jump_names": [], "n_jump": 0,
         "races": [{"division": "Boys Championship", "place": 2, "n_teams": 22, "points": 64,
                    "runners": [{"name": "Owen Castellano", "place": 3, "time": 884.1},
                                {"name": "Miles Okafor", "place": 9, "time": 902.3}]}]}


def test_digest_reads_like_a_results_email():
    unsub = "https://racecast.co/account/unsubscribe?a=41&t=x"
    nx = {"name": "Metro League Championships", "date": "2026-10-24", "venue": "Mt. Hood CC", "state": "OR"}
    subject, text, html = FA.digest([("Ezra Goldfarb", "/athlete/7", [_RACE], "athlete", nx)],
                                    "https://racecast.co", unsub, "Tadhg Murray")
    assert subject == "Ezra Goldfarb: PR at Nike Portland XC (15:12.4, 12th of 184)"
    assert text.startswith("Hi Tadhg,")
    for line in ("Nike Portland XC, Sat, Oct 3. Blue Lake Park, Varsity Boys, 5000m.",
                 "Place: 12th of 184", "Time: 15:12.4", "PR: previous 15:25.0, 12.6 seconds faster",
                 "Season best: previous 15:30.1, 17.7 seconds faster",
                 "Rating: 118.9, up 1.3 from the previous race",
                 "Breakout: 4.1 above the median of earlier races this season",
                 "Team: Lincoln 3rd of 22 teams, 98 points",
                 "Next likely meet: Metro League Championships, Sat, Oct 24 (Mt. Hood CC, OR)"):
        assert line in text, line
    # ! no AI-isms (owner, 2026-10-10): no em dash, none of the stock phrases
    for bad in ("\u2014", "Here's the latest", "well above", "best race of the season"):
        assert bad not in text and bad not in html
    visible = re.sub(r"<[^>]+>", " ", html)
    assert "https://" not in visible and "See the race" in html and "Ezra&#x27;s page" in html
    assert "You&#x27;re getting this because you follow Ezra Goldfarb on Racecast." in html
    assert "@" not in text and "@" not in html
    assert FA.digest([("Ezra", "/a", [_RACE], "athlete", None)], "o", "u")[1].startswith("Hi,\n")


def test_team_digest_lists_the_squad_and_the_prs():
    subject, text, _h = FA.digest([("Jesuit", "/school/Jesuit", [_TEAM], "team", None)], "o", "u", "Tadhg")
    assert subject == "Jesuit: 2nd of 22 teams at Nike Portland XC"
    assert "Boys Championship: 2nd of 22 teams, 64 points" in text
    assert "    3rd  Owen Castellano  14:44.1" in text and "PRs (2): Ben Sato, Miles Okafor" in text
    subject, _t, _h = FA.digest([("Ezra Goldfarb", "/athlete/7", [_RACE, _RACE], "athlete", None),
                                 ("Jesuit", "/school/Jesuit", [_TEAM], "team", None)], "o", "u")
    assert subject == "3 results at Nike Portland XC: Ezra Goldfarb PR, Jesuit 2nd"
    other = dict(_TEAM, meet_name="Clackamas")
    subject, _t, _h = FA.digest([("Ezra Goldfarb", "/athlete/7", [_RACE], "athlete", None),
                                 ("Jesuit", "/school/Jesuit", [other], "team", None)], "o", "u")
    assert subject == "2 results: Ezra Goldfarb PR at Nike Portland XC, Jesuit 2nd at Clackamas"


def test_long_subjects_stop_at_a_label():
    import alert_email as E
    p = {"type": "race", "flags": {"pr": {}}, "meet_name": "M", "date": "2026-09-27"}
    groups = [(f"Athlete Number {i}", "/a", [p], "athlete") for i in range(8)]
    s = E.subjectLine(groups)
    assert s.endswith(" and more") and len(s) <= E.SUBJECT_MAX + len(" and more")


def test_send_mail_sends_html_and_text_together(monkeypatch):
    monkeypatch.setenv("XCP_MAIL_KEY", "k")
    sent = []
    monkeypatch.setattr(AC, "_http", lambda url, body, hdrs: sent.append(body) or {})
    for prov in ("resend", "postmark"):
        monkeypatch.setenv("XCP_MAIL_PROVIDER", prov)
        assert AC.sendMail("a@b.co", "s", "plain", html="<p>rich</p>", headers={"List-Unsubscribe": "<u>"})
    assert sent[0]["text"] == "plain" and sent[0]["html"] == "<p>rich</p>" and sent[0]["headers"]
    assert sent[1]["TextBody"] == "plain" and sent[1]["HtmlBody"] == "<p>rich</p>"
    assert sent[1]["Headers"] == [{"Name": "List-Unsubscribe", "Value": "<u>"}]


def test_items_record_once_quiet_for_new_follows_and_muted_accounts():
    cur = Cur()
    follows = [{"id": 1, "account_id": 41, "primed_at": None, "cadence": "daily"},
               {"id": 2, "account_id": 42, "primed_at": "x", "cadence": "off"},
               {"id": 3, "account_id": 43, "primed_at": "x", "cadence": "weekly"}]
    found = {1: [("race:XC:9", "athlete:7", {"a": 1})], 2: [("race:XC:9", "athlete:7", {"a": 1})],
             3: [("race:XC:9", "athlete:7", {"a": 1})]}
    assert FA.recordItems(cur, follows, found, 2026) == 1
    ins = [p for s, p in cur.sql if "INSERT INTO alert_item" in s]
    assert [(p[0], p[5]) for p in ins] == [(41, "baseline"), (42, "muted"), (43, "pending")]
    assert all("ON CONFLICT (account_id, item_key) DO NOTHING" in s for s, _ in cur.sql if "INSERT INTO alert_item" in s)
    assert [p for s, p in cur.sql if "SET primed_at" in s] == [(1,)]


def test_send_marks_sent_before_mailing_and_rolls_back_on_failure(monkeypatch):
    monkeypatch.setattr(FA, "_titles", lambda cur, follows: {"athlete:7": ("Owen", "/athlete/7")})
    monkeypatch.setattr(FA.time, "sleep", lambda s: None)
    payload = FA.raceItem(_rr(9, 7, "2026-09-27", 123.5), {}, {}, set())[1]
    pending = [{"account_id": 41, "item_key": "race:XC:9", "subject": "athlete:7", "payload": payload,
                "email": "kid@example.com", "cadence": "daily", "last_sent_at": None}]
    order = []
    for ok in (True, False):
        cur = Cur([(r"FROM\s+alert_item i", pending)])
        sent_box = {}

        def fake_send(to, subject, text, headers=None, log_as=None, html=None, ok=ok, cur=cur):
            # ★ at the moment of sending, the items are already marked sent
            order.append(any("SET status = 'sent'" in s for s, _ in cur.sql))
            sent_box.update(to=to, headers=headers, log_as=log_as)
            return ok
        monkeypatch.setattr(AC, "sendMail", fake_send)
        logs = []
        res = FA.sendDigests(cur, Conn(), [{"account_id": 41, "kind": "athlete", "person_id": 7}],
                             datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc),
                             "https://racecast.co", log=logs.append)
        assert res == ((1, 0) if ok else (0, 1))
        assert sent_box["log_as"] == "account 41" and "kid@" not in " ".join(logs)
        assert sent_box["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        back = [s for s, _ in cur.sql if "SET status = 'pending'" in s]
        assert bool(back) == (not ok)
    assert order == [True, True]


def test_mail_failure_log_can_name_the_account_not_the_address(monkeypatch, capsys):
    monkeypatch.setenv("XCP_MAIL_PROVIDER", "resend")
    monkeypatch.setenv("XCP_MAIL_KEY", "k")

    def boom(*a, **k):
        raise RuntimeError("HTTP 500")
    monkeypatch.setattr(AC, "_http", boom)
    assert AC.sendMail("kid@example.com", "s", "t", log_as="account 41") is False
    out = capsys.readouterr().out
    assert "account 41" in out and "kid@example.com" not in out


def test_pipeline_step_is_optional_and_after_breakouts():
    for f in ("deploy/nightly_update.sh", "deploy/run_pipeline.sh"):
        src = read(*f.split("/"))
        assert 'if [ "${XCP_ALERTS:-0}" = "1" ]; then' in src
        assert src.index("13f_breakouts") < src.index("13g_follow_alerts")
        assert "racecast/follow_alerts.py --send --prewarm" in src
    # ! the alert switch and secret must not re-trigger the ladder
    assert "LACCTIC|ALERT)" in read("deploy", "run_pipeline.sh")


# ---------------------------------------------------------------- team calendars

def test_season_end_is_the_day_before_the_next_season():
    assert TM.seasonEnd(datetime.date(2026, 10, 10)) == datetime.date(2027, 7, 31)
    assert TM.seasonEnd(datetime.date(2027, 3, 1)) == datetime.date(2027, 7, 31)


def test_a_likely_meet_is_one_whose_picked_edition_the_team_ran():
    posted = [{"meet_id": 1, "name": "2026 Nike Portland XC", "date": "2026-10-17", "source": "anet", "sport": "XC"},
              {"meet_id": 2, "name": "Twilight Invitational", "date": "2026-10-03", "source": "anet", "sport": "XC"},
              {"meet_id": 3, "name": "Somewhere Else Classic", "date": "2026-10-24", "source": "anet", "sport": "XC"}]
    cands = TM.candidates(posted, ["2025 Nike Portland XC", "38th Twilight Invitational"])
    assert [m["meet_id"] for m in cands] == [1, 2]
    eds = {1: {"meet_id": 901, "name": "2025 Nike Portland XC", "date": "2025-10-18"},
           2: {"meet_id": 902, "name": "Twilight Invitational", "date": "2025-10-04"}}
    ran = {901}                                   # the Twilight they ran was ANOTHER state's
    out = TM.likelyFrom(cands, lambda m: eds.get(m["meet_id"]), lambda m, ed: ed["meet_id"] in ran)
    assert [m["meet_id"] for m in out] == [1] and out[0]["edition"]["date"] == "2025-10-18"
    assert TM.likelyFrom(cands, lambda m: None, lambda m, ed: True) == []


def test_level_fits():
    assert TM.levelFits("HS", "hs") and TM.levelFits("HS & college", "college")
    assert not TM.levelFits("College", "hs") and not TM.levelFits("HS", "college")
    assert TM.levelFits("", "hs") and TM.levelFits("College", None)


def test_ics_is_rfc5545_and_says_likely():
    meets = [{"meet_id": 4120, "sport": "XC", "source": "anet", "date": "2026-10-17",
              "name": "Nike Portland XC; the big one, 2026", "venue": "Blue Lake Park", "state": "OR",
              "edition": {"meet_id": 901, "name": "2025 Nike Portland XC", "date": "2025-10-18"},
              "predict_href": "/predictions?meet_id=4120&sport=XC"}]
    body = TM.icsCalendar("Jesuit (OR)", meets, "https://racecast.co",
                          now=datetime.datetime(2026, 10, 10, 12, 0, 0))
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "\n" not in body.replace("\r\n", "")                       # CRLF only
    for ln in body.split("\r\n"):
        assert len(ln.encode("utf-8")) <= 75
    unfolded = body.replace("\r\n ", "")
    assert "SUMMARY:Likely: Nike Portland XC\\; the big one\\, 2026" in unfolded
    assert "DTSTART;VALUE=DATE:20261017" in unfolded and "DTEND;VALUE=DATE:20261018" in unfolded
    assert "STATUS:TENTATIVE" in unfolded and "UID:anet-xc-4120@racecast.co" in unfolded
    assert "Likely\\, not confirmed: entries are not posted" in unfolded
    assert "URL:https://racecast.co/predictions?meet_id=4120&sport=XC" in unfolded
    assert "LOCATION:Blue Lake Park\\, OR" in unfolded


def test_ics_folding_never_splits_a_character():
    s = TM._fold("DESCRIPTION:" + "é" * 80)
    for part in s.split("\r\n"):
        part.encode("utf-8").decode("utf-8")
        assert len(part.encode("utf-8")) <= 75
    assert s.replace("\r\n ", "") == "DESCRIPTION:" + "é" * 80


def test_ics_route_is_public_and_beats_the_school_page(monkeypatch):
    app = flask.Flask(__name__)
    app.register_blueprint(TM.bp)
    monkeypatch.setattr(TM, "likelyMeetsCached", lambda *a, **k: [])
    import school_identity
    monkeypatch.setattr(school_identity, "primaryState", lambda s: "OR")
    monkeypatch.setattr(school_identity, "schoolLabelIn", lambda s, st, trusted=False: f"{s} ({st})")
    r = app.test_client().get("/school/Chisago Lakes/Rush City/meets.ics?state=MN")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert "X-WR-CALNAME:Chisago Lakes/Rush City (MN) - likely meets" in r.get_data(as_text=True)
    app_src = read("racecast", "app.py")
    priv = re.search(r"_PRIVATE_PREFIXES = \(([^)]*)\)", app_src, re.S).group(1)
    assert '"/school' not in priv                    # cached at the edge like the school page
    assert TM.calHref("Chisago Lakes/Rush City", "MN", "hs") == \
        "/school/Chisago%20Lakes/Rush%20City/meets.ics?sport=XC&state=MN&level=hs"


def test_school_page_links_the_calendar_and_loads_the_button():
    src = read("racecast", "templates", "school.html")
    assert "/meets.ics?sport=" in src and "follow.js" in src and 'data-follow="team"' in src
    ath = read("racecast", "templates", "athlete.html")
    assert 'data-follow="athlete"' in ath
    assert 'data-shortlist="1"' in read("racecast", "templates", "recruiting_school.html")


# ---------------------------------------------------------------- My page

def test_subjects_claims_first_each_once():
    claims = [{"kind": "athlete", "person_id": 7}, {"kind": "coach_team", "school": "Jesuit", "state": "OR",
                                                   "level": "hs"}]
    follows = [{"id": 5, "kind": "athlete", "person_id": 7}, {"id": 6, "kind": "athlete", "person_id": 8},
               {"id": 9, "kind": "team", "school": "Jesuit", "state": "OR", "level": "hs"}]
    assert M.subjectsFor(claims, follows) == [("athlete", 7, True, 5), ("team", ("Jesuit", "OR", "hs"), True, 9),
                                              ("athlete", 8, False, 6)]


def test_movement_is_against_a_snapshot_or_nothing():
    assert M.movement({"rating": 120.0, "nation": 41, "state_rank": 2}, None) is None
    mv = M.movement({"rating": 121.4, "nation": 41, "state_rank": 2},
                    {"taken_on": "2026-09-27", "rating": 120.0, "nation": 44, "state_rank": 2})
    assert mv["rating"] == 1.4 and mv["nation"] == 3 and mv["state"] == 0 and "unchanged" not in mv
    assert M.movement({"rating": 1, "nation": None, "state_rank": None},
                      {"rating": 1, "nation": 5, "state_rank": None})["nation"] is None


def test_race_pick_prefers_the_mapped_race_of_their_gender():
    races = [{"div_id": 1, "label": "Varsity", "gender": "F"}, {"div_id": 2, "label": "Varsity", "gender": "M"},
             {"div_id": 3, "label": "JV", "gender": "M"}]
    assert M.pickRace(races, "M")["div_id"] == 2
    assert M.pickRace(races, "M", ran_div=77, mapped=lambda r: [77] if r["div_id"] == 3 else [])["div_id"] == 3
    assert M.pickRace([], "M") is None


def test_a_view_reads_the_days_prediction_and_never_recomputes(monkeypatch):
    hit = {"meets": [], "next": {"name": "Nike"}, "prediction": {"available": True, "seconds": 900}}
    cur = Cur([(r"FROM next_meet_cache", [{"payload": json.dumps(hit)}])])
    monkeypatch.setattr(M, "computeAthleteNext", lambda *a, **k: pytest.fail("the model ran on a cached view"))
    assert M.athleteNext(cur, 7, {"school": "Jesuit"}, datetime.date(2026, 10, 10)) == hit
    # a miss computes once and stores it for the rest of the day
    calls = []
    cur = Cur()
    monkeypatch.setattr(M, "computeAthleteNext", lambda *a, **k: calls.append(1) or {"meets": [], "next": None})
    M.athleteNext(cur, 7, {"school": "Jesuit"}, datetime.date(2026, 10, 10))
    assert calls == [1] and any("INSERT INTO next_meet_cache" in s for s, _ in cur.sql)


def test_track_meets_are_not_auto_predicted_without_an_event():
    out = M._predictAt(Cur(), [7], {"sport": "TF", "meet_id": 1, "source": "anet"}, None)
    assert out[7]["available"] is False and "event" in out[7]["reason"]


def test_my_page_is_private():
    priv = re.search(r"_PRIVATE_PREFIXES = \(([^)]*)\)", read("racecast", "app.py"), re.S).group(1)
    assert '"/account"' in priv and '"/api/"' in priv


# ---------------------------------------------------------------- the shortlist

def test_programs_are_parsed():
    assert SL.parseProgram({"school": "Tufts", "state": "ma"}) == (("Tufts", "MA"), None)
    assert SL.parseProgram({"school": "Tufts"}) == (("Tufts", ""), None)
    assert SL.parseProgram({"school": ""})[1] and SL.parseProgram({"school": "T", "state": "Mass"})[1]


def test_development_is_a_share_of_the_race():
    assert SL.timeShare(10.0, 90.0) == 10.0          # d / (r + d)
    assert SL.timeShare(None, 90) is None
    d = SL.developmentOf([(90.0, 100.0), (100.0, 102.0), (95.0, None)])
    assert d["n"] == 2 and d["median_gain"] == 6.0 and d["median_pct"] == round((10.0 + 100 * 2 / 102) / 2, 1)
    assert SL.developmentOf([]) is None


def test_conference_strength_ranks_the_program_among_its_members():
    rows = [{"school": "A", "state": "MA", "conference": "NESCAC", "median": 110.0},
            {"school": "B", "state": "ME", "conference": "NESCAC", "median": 105.0},
            {"school": "C", "state": "CT", "conference": "NESCAC", "median": 100.0},
            {"school": "D", "state": "NY", "conference": "Liberty", "median": 108.0}]
    assert SL.conferenceOf(rows, "B", "ME") == {"name": "NESCAC", "n": 3, "median": 105.0, "rank": 2, "of": 3}
    assert SL.conferenceOf(rows, "Z", "MA") is None


def test_shortlist_has_no_fixed_cap_only_real_programs_and_needs_csrf(monkeypatch):
    many = [{"school": f"S{i}", "state": "MA", "added_at": None} for i in range(9)]
    cur = Cur([(r"FROM account_shortlist", many), (r"to_regclass", [{"t": "college_recruit"}]),
               (r"FROM college_recruit", [{"x": 1}])])
    fakeDb(monkeypatch, cur)
    c = client(monkeypatch)
    hdr = {"X-CSRF": "tok123", **ORIGIN}
    assert c.post("/api/shortlist", data={"school": "E", "state": "MA"},
                  headers={"X-CSRF": "bad", **ORIGIN}).status_code == 400
    # ★ a tenth program saves: no cap
    r = c.post("/api/shortlist", data={"school": "E", "state": "MA", "action": "save"}, headers=hdr)
    assert r.status_code == 200 and [p for s, p in cur.sql if "INSERT INTO account_shortlist" in s] == [(41, "E", "MA")]
    # ! but only a program the recruiting table has
    cur.answers = [(r"FROM account_shortlist", many), (r"to_regclass", [{"t": "college_recruit"}])]
    r = c.post("/api/shortlist", data={"school": "Made Up U", "state": "MA", "action": "save"}, headers=hdr)
    assert r.status_code == 404 and "not in the recruiting list" in r.get_json()["error"]
    # the comparison page's own remove form: CSRF in the form, then back to the page
    r = c.post("/api/shortlist", data={"school": "S1", "state": "MA", "action": "remove", "csrf": "tok123"},
               headers=ORIGIN)
    assert r.status_code == 302 and r.headers["Location"].startswith("/account/shortlist")
    assert "MAX_PROGRAMS" not in read("racecast", "shortlist.py")


def test_compare_picks_the_ticked_programs_or_all():
    progs = [{"school": "A", "state": "MA"}, {"school": "B", "state": ""}, {"school": "C", "state": "MN"}]
    assert SL.picked(progs, []) == progs
    assert [p["school"] for p in SL.picked(progs, ["B|", "C|MN"])] == ["B", "C"]
    assert SL.picked(progs, ["nope|XX"]) == progs


# ---------------------------------------------------------------- the button

def test_button_asks_nothing_without_the_hint_cookie():
    js = read("racecast", "static", "follow.js")
    init = js[js.index("function init()"):]
    assert init.index("if (!signedIn()) { drawSignedOut(wrap); return; }") < init.index("fetch(")
    assert "rc_si=1" in js and "'X-CSRF': st.csrf" in js and "credentials: 'same-origin'" in js


def test_where_youd_fit_reads_their_quartiles():
    s = {"min": 104.5, "p25": 110.2, "p75": 117.1}
    assert SL.fitOf(118.2, s)["key"] == "top" and SL.fitOf(117.1, s)["words"] == "top quarter of their recruits"
    assert SL.fitOf(112.0, s)["key"] == "in" and SL.fitOf(105.0, s)["key"] == "in"
    assert SL.fitOf(101.0, s) == {"key": "below", "words": "below range"}
    assert SL.fitOf(None, s) is None and SL.fitOf(110, None) is None


def test_follow_button_sits_beside_the_name_and_says_what_it_does():
    js = read("racecast", "static", "follow.js")
    assert "rc-namerow" in js and "querySelector('.rc-hd h1')" in js
    assert "' races, sets a PR or breaks out.'" in js and "'Get an email after each '" in js
    assert "Email me: " in js and "#alerts" in js
    assert "data-first=" in read("racecast", "templates", "athlete.html")


def test_alert_rating_is_on_the_hs_equivalent_scale(monkeypatch):
    import alert_email as AE
    monkeypatch.setattr(AE, "hsScale", lambda pool: 0.5 if pool == "ms_m" else 1.0)
    p = {"type": "race", "pool": "ms_m", "rating": 120.0, "prev_rating": 110.0, "time": 600,
         "flags": {"jump": {"by": 22.0}}}
    facts = dict(AE.raceFacts(p))
    assert facts["Rating"] == "60.0, up 5.0 from the previous race"
    assert facts["Breakout"].startswith("11.0 above")


def test_alert_mail_shows_the_pictures_and_the_team_squad():
    import alert_email as AE
    p = {"type": "race", "sport": "XC", "person_id": 7, "result_id": 1, "name": "M O", "pool": "hs_f",
         "school": "Jesuit", "date": "2026-10-03", "meet_name": "Nike Portland XC", "time": 1104.2,
         "rating": 118.9, "flags": {}, "place": 12, "field": 184,
         "photo": "/static/photos/7-a.jpg", "logo": "/img/school/Jesuit.png?state=OR",
         "team": {"school": "Jesuit", "place": 2, "n_teams": 31, "points": 87,
                  "runners": [{"name": "A C", "place": 4, "time": 1082.0, "person_id": 1},
                              {"name": "M O", "place": 12, "time": 1104.2, "person_id": 7}]}}
    _s, text, html = AE.compose([("M O", "/athlete/7", [p], "athlete", None)], "https://racecast.co",
                                "https://racecast.co/u/x", "T")
    assert 'src="https://racecast.co/static/logo-card.png"' in html
    assert 'src="https://racecast.co/static/photos/7-a.jpg"' in html
    assert "https://racecast.co/img/school/Jesuit.png?state=OR" in html
    assert "Jesuit: 2nd of 31 teams, 87 points" in text and "Team:" not in text
    assert "font-weight:700\">M O</td>" in html          # the athlete's row in the squad
    # an older item without the squad keeps the one Team line
    q = dict(p, team={"school": "Jesuit", "place": 2, "n_teams": 31, "points": 87})
    assert ("Team", "Jesuit 2nd of 31 teams, 87 points") in AE.raceFacts(q)
