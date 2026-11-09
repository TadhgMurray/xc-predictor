"""racecast/training_log.py: a runner's own runs from their watch's files
(owner, 2026-11-08). The readers, the run filter, the weeks. No database.

    python -m pytest -q tests/test_training_log.py
"""
import datetime as dt
import gzip
import os
import struct
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "scripts"):
    p = os.path.join(_ROOT, _p)
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest                                                # noqa: E402
import training_log as T                                     # noqa: E402

UTC = dt.timezone.utc
_FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=UTC)


def fitFile(start, sport=1, dist_m=8046.7, secs=2700.0, hr=(152, 178), ascent=40):
    """A minimal FIT file: one session message (global 18)."""
    fields = [(2, 4, 0x86), (5, 1, 0x00), (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86),
              (16, 1, 0x02), (17, 1, 0x02), (22, 2, 0x84)]
    defn = struct.pack("<BBBHB", 0x40, 0, 0, 18, len(fields)) + b"".join(
        struct.pack("<BBB", *f) for f in fields)
    ts = int((start - _FIT_EPOCH).total_seconds())
    data = struct.pack("<BIBIIIBBH", 0x00, ts, sport, int(secs * 1000), int(secs * 1000),
                       int(dist_m * 100), hr[0], hr[1], ascent)
    body = defn + data
    header = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0)
    return header + body + b"\x00\x00"


GPX = b"""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
 <trk><type>running</type><trkseg>
  <trkpt lat="40.0000" lon="-75.0000"><ele>10</ele><time>2026-10-01T12:00:00Z</time>
    <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>140</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
  <trkpt lat="40.0090" lon="-75.0000"><ele>20</ele><time>2026-10-01T12:05:00Z</time>
    <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>160</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
 </trkseg></trk></gpx>"""

TCX = b"""<?xml version="1.0"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities><Activity Sport="Running"><Id>2026-10-02T13:00:00Z</Id>
  <Lap StartTime="2026-10-02T13:00:00Z"><TotalTimeSeconds>600</TotalTimeSeconds><DistanceMeters>2000</DistanceMeters>
   <AverageHeartRateBpm><Value>150</Value></AverageHeartRateBpm><MaximumHeartRateBpm><Value>170</Value></MaximumHeartRateBpm></Lap>
  <Lap StartTime="2026-10-02T13:10:00Z"><TotalTimeSeconds>600</TotalTimeSeconds><DistanceMeters>2100</DistanceMeters>
   <AverageHeartRateBpm><Value>160</Value></AverageHeartRateBpm><MaximumHeartRateBpm><Value>175</Value></MaximumHeartRateBpm></Lap>
 </Activity></Activities></TrainingCenterDatabase>"""


def test_a_watch_fit_file_is_read_to_one_run():
    pytest.importorskip("fitdecode")
    start = dt.datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    runs, why = T.readFile("Morning_Run.fit", fitFile(start))
    assert why is None and len(runs) == 1
    r = runs[0]
    assert r["started_at"] == start and r["sport"] == "run" and r["source"] == "fit"
    assert r["distance_m"] == pytest.approx(8046.7, abs=0.01)
    assert r["moving_s"] == pytest.approx(2700.0) and r["avg_hr"] == 152 and r["max_hr"] == 178
    assert r["ascent_m"] == 40


def test_a_ride_is_skipped_and_a_gz_is_opened():
    pytest.importorskip("fitdecode")
    start = dt.datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    assert T.readFile("ride.fit", fitFile(start, sport=2)) == ([], "not a run")
    runs, why = T.readFile("run.fit.gz", gzip.compress(fitFile(start)))
    assert why is None and len(runs) == 1


def test_gpx_is_summed_and_no_track_is_kept():
    pytest.importorskip("defusedxml")
    runs, why = T.readFile("a.gpx", GPX)
    assert why is None and len(runs) == 1
    r = runs[0]
    assert r["distance_m"] == pytest.approx(1000.8, abs=2.0)           # 0.009 deg of latitude
    assert r["elapsed_s"] == 300 and r["avg_hr"] == 150 and r["max_hr"] == 160
    assert r["ascent_m"] == 10
    assert not any(k in r for k in ("lat", "lon", "points", "track"))


def test_tcx_laps_are_summed():
    pytest.importorskip("defusedxml")
    runs, why = T.readFile("b.tcx", TCX)
    assert why is None
    r = runs[0]
    assert r["distance_m"] == 4100 and r["moving_s"] == 1200
    assert r["avg_hr"] == pytest.approx(155) and r["max_hr"] == 175
    assert r["started_at"] == dt.datetime(2026, 10, 2, 13, 0, tzinfo=UTC)


def test_xml_with_entities_is_refused():
    pytest.importorskip("defusedxml")
    bomb = (b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaa">]>'
            b'<gpx><trk><trkseg>&a;</trkseg></trk></gpx>')
    assert T.readFile("bomb.gpx", bomb) == ([], "could not be read")


def test_what_is_not_a_file_or_not_a_run():
    assert T.readFile("x.txt", b"hello") == ([], "not a FIT, GPX or TCX file")
    assert T.readFile("big.fit", b"0" * (T.MAX_FILE_BYTES + 1)) == ([], "file too large")
    t0 = dt.datetime(2026, 10, 1, tzinfo=UTC)
    run = {"sport": "running", "started_at": t0, "distance_m": 5000.0, "moving_s": 1200.0}
    assert T.whyNot(run) is None
    assert T.whyNot(dict(run, sport="cycling")) == "not a run"
    assert T.whyNot(dict(run, moving_s=600.0)) == "too fast to be a run on foot"   # 2:00/km
    assert T.whyNot(dict(run, distance_m=0)) == "no distance or time"
    assert T.whyNot(dict(run, sport="generic", sub_sport="treadmill")) is None


def test_the_same_run_from_two_exports_is_one():
    t0 = dt.datetime(2026, 10, 1, 12, 0, 31, tzinfo=UTC)
    a = {"started_at": t0, "distance_m": 8046.0}
    assert T.dedupeKey(a) == T.dedupeKey({"started_at": t0, "distance_m": 8030.0})
    assert T.dedupeKey(a) != T.dedupeKey({"started_at": t0, "distance_m": 9000.0})


def test_the_weeks_newest_first_with_empty_weeks():
    today = dt.date(2026, 11, 8)                       # a Sunday
    rows = [{"started_at": dt.datetime(2026, 11, 3, 12, tzinfo=UTC), "distance_m": 1609.344 * 5,
             "moving_s": 2400.0},
            {"started_at": dt.datetime(2026, 11, 5, 12, tzinfo=UTC), "distance_m": 1609.344 * 10,
             "moving_s": 4800.0},
            {"started_at": dt.datetime(2026, 10, 20, 12, tzinfo=UTC), "distance_m": 1609.344 * 3,
             "moving_s": 1500.0},
            {"started_at": dt.datetime(2025, 1, 1, tzinfo=UTC), "distance_m": 1609.344}]
    w = T.weeklyTotals(rows, today, weeks=4)
    assert [x["week"] for x in w] == [dt.date(2026, 11, 2), dt.date(2026, 10, 26),
                                       dt.date(2026, 10, 19), dt.date(2026, 10, 12)]
    assert w[0]["miles"] == pytest.approx(15.0) and w[0]["runs"] == 2 and w[0]["longest"] == pytest.approx(10.0)
    assert w[1]["runs"] == 0 and w[2]["miles"] == pytest.approx(3.0)
    assert w[0]["hours"] == pytest.approx(2.0)


def test_pace_and_climb():
    assert T.paceText(1609.344, 452) == "7:32/mi"
    assert T.paceText(0, 100) == ""
    assert T.climb([10, 11, 10, 12, 16, 15, 20]) == pytest.approx(11.0)   # 10->16, 15->20; wiggles under 3 m ignored


def test_the_research_door_is_shut_while_the_switch_is_off(monkeypatch):
    monkeypatch.delenv("XCP_TRAINING_RESEARCH", raising=False)

    class _Cur:
        def execute(self, *a):
            raise AssertionError("read while the opt-in is off")
    assert T.researchActivities(_Cur()) == []


# ---- the routes, on a stand-in database ------------------------------------- #

def _app(monkeypatch, runners, stored, rows=()):
    import contextlib
    import types
    if "database" not in sys.modules:
        _db = types.ModuleType("database")
        _db.getConn = contextlib.nullcontext
        sys.modules["database"] = _db
    flask = pytest.importorskip("flask")
    import accounts as A
    app = flask.Flask(__name__, template_folder=os.path.join(_ROOT, "racecast", "templates"))
    app.jinja_env.globals["static_v"] = lambda f: "/static/" + f
    app.jinja_env.globals["site_origin"] = "https://racecast.co"
    import seo as _seo
    _seo.install(app, "https://racecast.co")
    for ep in ("predictions_page", "about_page", "report_page", "compare_page",
               "schools_directory", "conversions_page", "coaches_page",
               "cuts.wit_index", "pages2.projections_index",
               "pages2.breakouts_page", "scale.scale_page", "goal.goal_page"):
        app.add_url_rule("/" + ep, ep, lambda: "")
    app.register_blueprint(T.bp)

    class Cur:
        def execute(self, sql, args=None):
            self.sql = sql

        def fetchall(self):
            return [dict(r) for r in rows] if "FROM   training_activity" in self.sql else []

        def fetchone(self):
            return None

    class Conn:
        def commit(self):
            pass

        def rollback(self):
            pass

    @contextlib.contextmanager
    def fakeDb():
        yield Conn(), Cur()
    sess = {"csrf": "tok", "account": {"id": 7, "email": "kid@x.co", "name": "Kid"}}
    monkeypatch.setattr(A, "_db", fakeDb)
    monkeypatch.setattr(A, "currentSession", lambda: sess)
    monkeypatch.setattr(A, "logEvent", lambda *a, **k: None)
    monkeypatch.setattr(T, "tablesReady", lambda cur: True)
    monkeypatch.setattr(T, "runnersFor", lambda cur, aid: runners)

    def store(cur, aid, pid, runs):
        stored.append((aid, pid, runs))
        return len(runs), 0
    monkeypatch.setattr(T, "storeRuns", store)
    return app


def test_the_upload_keeps_runs_for_the_accounts_own_runner(monkeypatch):
    pytest.importorskip("defusedxml")
    import io as _io
    stored = []
    c = _app(monkeypatch, [(42, "Kid")], stored).test_client()
    hdr = {"Origin": "http://localhost"}

    def send(pid, csrf="tok", files=(("a.gpx", GPX), ("notes.txt", b"hi"))):
        data = {"csrf": csrf, "person_id": str(pid),
                "files": [(_io.BytesIO(b), n) for n, b in files]}
        return c.post("/account/training/upload", data=data, headers=hdr,
                      content_type="multipart/form-data")
    r = send(42)
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and j["added"] == 1
    assert j["skipped"] == [{"file": "notes.txt", "why": "not a FIT, GPX or TCX file"}]
    assert stored[0][0] == 7 and stored[0][1] == 42
    assert send(99).status_code == 400                       # not this account's runner
    assert send(42, csrf="nope").status_code == 400          # not from this site
    assert len(stored) == 1


def test_the_page_shows_the_weeks_and_hides_the_research_switch(monkeypatch):
    monkeypatch.delenv("XCP_TRAINING_RESEARCH", raising=False)
    now = dt.datetime.now(UTC)
    rows = [{"started_at": now, "distance_m": 1609.344 * 6, "elapsed_s": 2700.0, "moving_s": 2640.0,
             "avg_hr": 151.0, "max_hr": 175.0, "ascent_m": 30.0, "source": "fit"}]
    c = _app(monkeypatch, [(42, "Kid")], [], rows).test_client()
    html = c.get("/account/training").get_data(as_text=True)
    assert "Your training" in html and "6.0" in html and "7:20/mi" in html
    assert "Help improve predictions" not in html
    monkeypatch.setenv("XCP_TRAINING_RESEARCH", "1")
    assert "Help improve predictions" in c.get("/account/training").get_data(as_text=True)


def test_no_runner_linked_means_link_first(monkeypatch):
    c = _app(monkeypatch, [], []).test_client()
    assert "Link your athlete page first" in c.get("/account/training").get_data(as_text=True)
