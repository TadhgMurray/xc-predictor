"""racecast/activity.py: the owner's activity feed (2026-10-22).

    python -m pytest -q tests/test_activity.py
"""
import contextlib
import datetime
import json
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
          os.path.join(_ROOT, "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

import activity as AC                                           # noqa: E402


def test_nightly_summary_lines_both_shapes():
    text = "  01_scrape ok (120s)\n  02_link FAILED (exit 1) after 7s\nnoise\n"
    assert AC.parseNightly(text) == [
        {"step": "01_scrape", "ok": True, "seconds": 120},
        {"step": "02_link", "ok": False, "seconds": 7}]


def test_run_event_levels_and_start_time():
    ok = AC.runEvent("pipeline", "20261022_135400", [{"step": "a", "ok": True, "seconds": 3700}])
    assert ok["level"] == "ok" and ok["at"] == datetime.datetime(2026, 10, 22, 13, 54)
    assert "1h 1m" in ok["summary"]
    bad = AC.runEvent("nightly", "nightly_20261022_020000",
                      [{"step": "a", "ok": True, "seconds": 5}, {"step": "b", "ok": False, "seconds": 1}])
    assert bad["level"] == "bad" and "1 failed: b" in bad["summary"]
    assert bad["at"] == datetime.datetime(2026, 10, 22, 2, 0)
    assert AC.runEvent("pipeline", "20261022_000000", [], ["10_solve"])["level"] == "warn"


def test_verdict_directions():
    assert AC.verdict(2.0, 1.9, "low") == "better"
    assert AC.verdict(85.0, 84.0, "high") == "worse"
    assert AC.verdict(-0.5, 0.3, "abs") == "better"
    assert AC.verdict(1.0, 1.0, "low") == "same"
    assert AC.verdict(1.0, 2.0, None) is None and AC.verdict(None, 2.0, "low") is None


def test_accuracy_event_against_the_run_before():
    specs = [("sd", "held-out sd", "low"), ("order", "order", "high"), ("info", "info", None)]
    prev = {"run": "20261019_143556", "at": "2026-10-19T18:08:00",
            "metrics": {"sd": 0.032, "order": 85.9, "info": 1.0}}
    cur = {"run": "20261022_135400", "at": "2026-10-22T18:00:00",
           "metrics": {"sd": 0.030, "order": 85.5, "info": 2.0}}
    e = AC.accuracyEvent(cur, prev, specs)
    assert e["at"] == datetime.datetime(2026, 10, 22, 18, 0)
    assert e["summary"].startswith("1 better, 1 worse")
    assert any(d.startswith("+ held-out sd") for d in e["detail"])
    assert any(d.startswith("- order") for d in e["detail"])
    assert AC.accuracyEvent(prev, None, specs)["summary"] == "first run on record"


def test_scrape_days_group_and_name():
    rows = [("XC", "anet", 5, "2026-10-17", 300, "2026-10-21 03:00:00+00"),
            ("TF", "tfrrs", 9, "2026-10-18", 50, "2026-10-21 04:00:00"),
            ("XC", "tfrrs", 7, "2026-10-17", 900, "2026-10-20 02:00:00")]
    ev = AC.scrapeDays(rows, {("XC", "anet", 5): "Big Invite"})
    by = {e["title"][:18]: e for e in ev}
    d21 = by["Scraped 2026-10-21"]
    assert "2 meets, 350 results" in d21["title"]
    assert d21["at"] == datetime.datetime(2026, 10, 21, 4, 0)
    assert "Big Invite" in d21["detail"][0] and d21["links"][0] == ("Big Invite", "/meet/xc/5")
    assert ("meet 9", "/meet/tf/9") in d21["links"]


def test_mark_new_since_last_visit():
    t = datetime.datetime(2026, 10, 22, 12)
    ev = [{"at": t}, {"at": t - datetime.timedelta(hours=5)}]
    assert AC.markNew(ev, t - datetime.timedelta(hours=1)) == 1 and ev[0]["new"] and not ev[1]["new"]
    assert AC.markNew(ev, None) == 2


def test_feed_reads_logs_and_history(tmp_path, monkeypatch):
    run = tmp_path / "20261022_010000"
    run.mkdir()
    (run / "summary.log").write_text("  01_a ok (5s)\n  02_b FAILED after 3s\n")
    night = tmp_path / "nightly_20261022_020000"
    night.mkdir()
    (night / "SUMMARY.txt").write_text("  01_scrape ok (9s)\n")
    hist = tmp_path / "run_history.jsonl"
    hist.write_text(json.dumps({"run": "20261022_010000", "at": "2026-10-22T05:00:00",
                                "metrics": {"holdout_race_sd": 0.03}}) + "\n")
    monkeypatch.setattr(AC, "LOG_ROOT", str(tmp_path))
    monkeypatch.setattr(AC, "pipelineEvents", lambda log_root=str(tmp_path), runs=10:
                        AC.__dict__["_pipelineEventsOrig"](str(tmp_path), runs))
    monkeypatch.setattr(AC, "accuracyEvents", lambda history=str(hist), runs=10:
                        AC.__dict__["_accuracyEventsOrig"](str(hist), runs))
    monkeypatch.setattr(AC, "scrapeEvents", lambda conn, days=3, limit=400: [])
    monkeypatch.setattr(AC, "kailoEvents", lambda store=None, days=14: [])
    fd = AC.feed(None, None)
    kinds = [e["kind"] for e in fd["events"]]
    assert kinds == ["accuracy", "nightly", "pipeline"] and fd["n_new"] == 3
    assert fd["events"][2]["level"] == "bad" and not fd["errors"]


AC._pipelineEventsOrig = AC.pipelineEvents
AC._accuracyEventsOrig = AC.accuracyEvents


def test_the_admin_page_shows_the_feed_and_hides_from_readers(monkeypatch):
    import app as A
    t = datetime.datetime.now() - datetime.timedelta(minutes=5)
    fd = {"events": [{"at": t, "kind": "pipeline", "level": "bad", "new": True,
                      "title": "Full pipeline 20261022_010000", "summary": "1 failed: 02_b <x>",
                      "detail": ["FAIL 02_b (3s)"], "links": [("Big Invite", "/meet/xc/5")]}],
          "n_new": 1, "errors": {"scraped": "timeout"}}
    monkeypatch.setattr(AC, "feed", lambda conn, seen=None: fd)
    monkeypatch.setattr(A, "getConn", lambda: contextlib.nullcontext(None))
    monkeypatch.setenv("XCP_ADMIN_EMAILS", "owner@example.com")
    client = A.app.test_client()
    monkeypatch.setattr(A._accounts, "currentSession",
                        lambda: {"csrf": "t", "account": {"email": "someone@example.com"}})
    assert client.get("/account/status/activity").status_code == 404
    monkeypatch.setattr(A._accounts, "currentSession",
                        lambda: {"csrf": "t", "account": {"email": "owner@example.com"}})
    r = client.get("/account/status/activity")
    assert r.status_code == 200, r.data[:400]
    html = r.get_data(as_text=True)
    assert "1 new (first visit" in html and "NEW</span>" in html
    assert "1 failed: 02_b &lt;x&gt;" in html and 'href="/meet/xc/5"' in html
    assert "Could not read the scraped events: timeout" in html
    assert "rc_activity_seen=" in r.headers.get("Set-Cookie", "")


def test_public_events_only_runs_that_published():
    t = datetime.datetime(2026, 10, 22, 3)
    # a failed report AFTER the swap: the ratings are live -- recomputed
    live = AC.publicEvent({"at": t, "kind": "pipeline", "level": "bad", "published": True,
                           "summary": "1 failed: 16b_lacctic"})
    assert live["title"] == "Every rating recomputed" and "16b" not in json.dumps(live, default=str)
    # stopped before the swap: nothing changed for a reader
    assert AC.publicEvent({"at": t, "kind": "pipeline", "level": "bad", "published": False}) is None
    assert AC.publicEvent({"at": t, "kind": "pipeline", "level": "warn", "published": False}) is None
    run = AC.publicEvent({"at": t, "kind": "pipeline", "level": "warn", "active": True})
    assert run["title"] == "Full ratings update in progress"
    assert AC.publicEvent({"at": t, "kind": "nightly", "level": "bad", "published": True})["title"] == "New results rated"
    assert AC.publicEvent({"at": t, "kind": "nightly", "level": "ok", "published": False}) is None
    assert AC.published([{"step": "10_rankings_finish", "ok": True}, {"step": "16b", "ok": False}])
    assert not AC.published([{"step": "10_rankings_finish", "ok": False}])
    assert AC.publicEvent({"at": t, "kind": "kailo", "level": "info"}) is None
    # accuracy is said only on its published run's entry, never alone
    assert AC.publicEvent({"at": t, "kind": "accuracy", "metrics": {"holdout_race_sd": 0.03}}) is None
    assert AC.accuracyLines({"holdout_race_sd": 0.0301}, {"holdout_race_sd": 0.0312}) == \
        ["Typical miss on a race the ratings had not seen: 3.01% (was 3.12%)"]
    sc = AC.scrapeDays([("XC", "tfrrs", 7, "2026-10-17", 900, "2026-10-20 02:00:00"),
                        ("TF", "anet", 8, "2062-10-18", 40, "2026-10-20 03:00:00"),
                        ("XC", "anet", 9, "2026-10-18", 60, "2026-10-20 03:30:00")],
                       {("XC", "tfrrs", 7): "Pre-Nats"})[0]
    pub = AC.publicEvent(sc)
    # the newest REAL race date (not the typo'd 2062), and no meet list
    assert pub["title"] == "New results through Sun, Oct 18"
    assert pub["summary"] == "3 meets, 1,000 results added (cross country 2, track 1)"
    assert pub["items"] == [] and "Pre-Nats" not in json.dumps(pub, default=str)


def test_latest_race_day():
    assert AC.latestRaceDay(["2026-10-17", "2026-10-18", "2026-11-30"], "2026-10-20") == "2026-10-18"
    assert AC.latestRaceDay([None, "1900-01-01"], "2026-10-20") is None


def test_recompute_text_says_what_moved_and_why(monkeypatch):
    import public_changes as PC
    monkeypatch.setattr(PC, "CHANGES", [("2026-10-20", "Old note."), ("2026-10-22", "Course note.")])
    e = {"kind": "pipeline", "level": "ok", "published": True, "at": datetime.datetime(2026, 10, 22, 9),
         "metrics": {"holdout_race_sd": 0.0301}, "prev_metrics": {"holdout_race_sd": 0.0312},
         "changes": {"previous_run_at": "2026-10-20T14:00:00", "new_results": 48210,
                     "athletes": [{"level": "hs", "sport": "XC", "compared": 400000, "median_abs": 0.31,
                                   "mean": 0.1, "moved_2": 21400, "moved_5": 3100, "up": 230000,
                                   "down": 160000, "new": 9000},
                                  {"level": "college", "sport": "XC", "compared": 60000, "median_abs": 0.52,
                                   "mean": 0.4, "moved_2": 4100, "moved_5": 300, "up": 40000,
                                   "down": 19000, "new": 0},
                                  {"level": "elem", "sport": "XC", "compared": 80, "median_abs": 1,
                                   "mean": 0, "moved_2": 9, "moved_5": 1, "up": 40, "down": 40, "new": 0}],
                     "courses": {"compared": 30000, "moved_1pct": 212, "new": 37,
                                 "biggest": [{"course": "Greiner Family XC Course", "distance_m": 8000,
                                              "change_pct": -2.14}]}}}
    p = AC.publicEvent(e)
    assert p["title"] == "Every rating recomputed"
    # only the accuracy, the method and big pool moves (owner: "too much")
    assert p["summary"] == "Typical miss on a race the ratings had not seen: 3.01% (was 3.12%)"
    assert p["summary_verdict"] == "improved" and p["acc"] == []
    assert p["detail"] == ["What we changed: Course note."]          # no level is 0.5+ off
    e["changes"]["athletes"][1]["mean"] = 0.62
    p = AC.publicEvent(e)
    assert p["detail"] == ["What we changed: Course note.",
                           "College cross country ratings rose 0.6 points on average"]
    assert "Greiner" not in json.dumps(p, default=str) and "48,210" not in json.dumps(p, default=str)


def test_a_full_runs_accuracy_rides_on_it():
    t = datetime.datetime(2026, 10, 22, 9)
    ev = [{"kind": "pipeline", "run": "20261022_010000", "at": t},
          {"kind": "accuracy", "run": "20261022_010000", "at": t, "metrics": {"x": 1},
           "prev_metrics": {"x": 2}},
          {"kind": "accuracy", "run": "20261001_010000", "at": t, "metrics": {"holdout_race_sd": 0.03}}]
    AC.attachAccuracy(ev)
    assert ev[0]["metrics"] == {"x": 1} and ev[1]["carried"] and not ev[2].get("carried")
    assert AC.publicEvent(ev[1]) is None and AC.publicEvent(ev[2]) is None


def test_the_public_page_and_latest_api(monkeypatch):
    import app as A
    t = datetime.datetime(2026, 10, 22, 3, 0, 0)
    pub = {"events": [{"at": t, "kind": "scraped", "level": "info", "title": "New results: 1 meet, 900 results",
                       "summary": "1 meet, 900 results added", "detail": ["a line"], "items": []}],
           "n_new": 1, "latest": t}
    monkeypatch.setattr(AC, "publicFeedCached", lambda get_conn: pub)
    client = A.app.test_client()
    r = client.get("/updates")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "What&#39;s new" in html or "What's new" in html
    assert 'data-at="2026-10-22T03:00:00"' in html and "a line" in html
    assert "rc_updates_seen" in html                       # marked in the browser
    assert "public" in r.headers.get("Cache-Control", "")  # edge-cacheable
    j = client.get("/api/updates/latest")
    assert j.get_json() == {"latest": "2026-10-22T03:00:00"}
    assert j.headers["Cache-Control"] == "public, max-age=300"


def test_accuracy_lines_use_every_test_a_run_has():
    m = {"backtest_blend_mae": 2.97, "backtest_blend_order": 0.922, "lacctic_order_ours": 86.1,
         "lacctic_5000_ours": 2.12, "owner_3200": 138.0}
    p = {"backtest_blend_mae": 3.44, "backtest_blend_order": 0.922, "lacctic_order_ours": 85.9}
    rows = AC.accuracyRows(m, p)
    assert rows[0] == ("Predicted race times, the last two weeks' races: typical miss: 2.97% (was 3.44%)", "improved")
    assert rows[1] == ("Predicted finishing order, the same races: 92.2", None)        # unchanged
    assert rows[2] == ("Finishing order called right from ratings, college races: 86.1% (was 85.9%)", "improved")
    assert rows[3] == ("Cross country ratings against the same runners' track 5000s: 2.12%", None)
    assert AC.accuracyRows({"lacctic_order_ours": 85.0}, {"lacctic_order_ours": 86.0})[0][1] == "worse"
    assert len(rows) == 4 and not any("LACCTiC" in t or "owner" in t for t, _v in rows)
    assert all(why and way.endswith("is better") for _l, way, why in AC.metricGuide())
