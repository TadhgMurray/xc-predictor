"""The weekly "how we did" loop (owner, 2026-10-10): the forecast frozen
before each posted XC meet (meet_forecast.py, build_meet_forecasts.py), the
recap that scores it after (meet_recap.py, /meet/recap/xc/<id>, /recaps),
its share card, and the 5K column on the pages3 pages. The database is
stubbed at every boundary.

    python -m pytest -q tests/test_meet_forecast_recap.py
"""
import contextlib
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts", ""):
    sys.path.insert(0, os.path.join(ROOT, _p))

D = datetime.date
TODAY = D(2026, 10, 10)                     # a Saturday


# ------------------------------------------------------------------ #
#  fixtures
# ------------------------------------------------------------------ #

def _forecast():
    """A stored race: six runners in predicted order, three teams."""
    names = ["Ann", "Bea", "Cy", "Dee", "Eve", "Flo"]
    runners = [{"person_id": i + 1, "name": n, "school": "AB"[i % 2], "place": i + 1,
                "seconds": 900.0 + 10 * i, "rating": 120.0 - i, "pool": "hs_f"}
               for i, n in enumerate(names)]
    teams = [{"team": "A", "state": "CA", "score": 30, "scorers": []},
             {"team": "B", "state": "CA", "score": 40, "scorers": []},
             {"team": "C", "state": "CA", "score": 50, "scorers": []},
             {"team": "D", "state": "CA", "score": None, "scorers": []}]
    return {"source": "anet", "meet_id": 9, "div_id": 1, "meet_name": "Clovis Invitational",
            "meet_date": "2026-10-03", "state": "CA", "venue": "Woodward Park",
            "race_label": "Varsity Girls", "gender": "F", "distance": 5000.0,
            "edition_meet_id": 8, "edition_date": "2025-10-04", "n_field": 6,
            "runners": runners, "teams": teams, "model_basis": "rating",
            "model_version": "model.pt 2026-10-01 03:12", "made_on": "2026-10-02",
            "score": None}


def _finishers():
    """Bea wins; Ann 2nd; Zed (not predicted) 3rd; Dee 4th; Cy 5th; Eve
    6th; Flo did not run."""
    rows = [(2, "Bea", "B", 905.0), (1, "Ann", "A", 912.0), (77, "Zed", "Z", 915.0),
            (4, "Dee", "B", 925.0), (3, "Cy", "A", 940.0), (5, "Eve", "A", 941.0)]
    return [{"person_id": p, "name": n, "school": s, "time_seconds": t} for p, n, s, t in rows]


ACTUAL_TEAMS = [{"team": "B", "state": "CA", "points": 25},
                {"team": "A", "state": "CA", "points": 33},
                {"team": "C", "state": "CA", "points": 60}]


# ------------------------------------------------------------------ #
#  the freeze
# ------------------------------------------------------------------ #

class Freeze(unittest.TestCase):
    def test_new_then_refresh_once_the_night_before(self):
        import meet_forecast as MF
        sat = D(2026, 10, 17)
        self.assertEqual(MF.forecastAction(sat, D(2026, 10, 12)), "new")
        # stored Monday: nothing more until the night before
        self.assertIsNone(MF.forecastAction(sat, D(2026, 10, 13), D(2026, 10, 12)))
        self.assertEqual(MF.forecastAction(sat, D(2026, 10, 16), D(2026, 10, 12)), "refresh")
        # ! idempotent: the refresh made tonight is not made again tonight
        self.assertIsNone(MF.forecastAction(sat, D(2026, 10, 16), D(2026, 10, 16)))

    def test_never_on_or_after_the_meets_day(self):
        import meet_forecast as MF
        sat = D(2026, 10, 17)
        for today in (sat, sat + datetime.timedelta(days=1), sat + datetime.timedelta(days=30)):
            self.assertIsNone(MF.forecastAction(sat, today))
            self.assertIsNone(MF.forecastAction(sat, today, D(2026, 10, 12)))
        # ISO strings, as the calendar carries them
        self.assertIsNone(MF.forecastAction("2026-10-10", "2026-10-10"))
        self.assertEqual(MF.forecastAction("2026-10-11", "2026-10-10"), "new")

    def test_never_once_the_race_has_results(self):
        import meet_forecast as MF
        self.assertIsNone(MF.forecastAction(D(2026, 10, 17), D(2026, 10, 12), has_results=True))
        self.assertIsNone(MF.forecastAction(D(2026, 10, 17), D(2026, 10, 16), D(2026, 10, 12), True))

    def test_the_upsert_repeats_the_rule_in_sql(self):
        import meet_forecast as MF

        class Rec:
            rowcount = 1

            def execute(self, sql, p=None):
                self.sql, self.p = sql, p
        cur = Rec()
        plan = {"source": "anet", "meet_id": 9, "name": "M", "date": "2026-10-17",
                "edition": {"meet_id": 8, "date": "2025-10-18"}}
        self.assertTrue(MF.saveRace(cur, plan, {"div_id": 1, "label": "V"},
                                    {"available": True, "runners": [], "teams": [], "n_field": 0},
                                    TODAY))
        self.assertEqual(cur.p["today"], "2026-10-10")
        self.assertEqual(cur.p["meet_date"], "2026-10-17")
        sql = " ".join(cur.sql.split())
        # a new row only for a meet still ahead, a stored one only while it
        # is still ahead, unscored, and made on an earlier day
        self.assertIn("WHERE %(meet_date)s::date > %(today)s::date", sql)
        self.assertIn("meet_forecast.meet_date > %(today)s::date", sql)
        self.assertIn("meet_forecast.score IS NULL", sql)
        self.assertIn("meet_forecast.made_on < %(today)s::date", sql)


class BuildStep(unittest.TestCase):
    """build_meet_forecasts.forecastMeet over an in-memory table."""

    def setUp(self):
        import build_meet_forecasts as BMF
        import meet_forecast as MF
        import upcoming_preview as U
        self.BMF, self.store, self.predicted = BMF, {}, []
        self.saved = []

        def patch(mod, name, val):
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, val)
        self.plan = {"meet_id": 9, "source": "anet", "name": "Clovis Invitational",
                     "date": "2026-10-11", "venue": "Woodward Park", "state": "CA",
                     "edition": {"meet_id": 8, "date": "2025-10-12"},
                     "races": [{"div_id": d, "label": f"Race {d}", "gender": "M"} for d in (1, 2, 3)]}
        patch(U, "_meetPlanUncached", lambda cur, mid, src: dict(self.plan))
        patch(MF, "tableReady", lambda cur: True)
        patch(MF, "existing", lambda cur, src, mid: {d: day for (s, m, d), day in self.store.items()})
        self.ran = set()
        patch(MF, "racesWithResults", lambda cur, src, mid: set(self.ran))

        def live(cur, mid, div, src):
            self.predicted.append(div)
            return {"available": True, "runners": [], "teams": [], "n_field": 0}
        patch(U, "livePrediction", live)

        def save(cur, meet, race, pred, today):
            k = (meet["source"], meet["meet_id"], race["div_id"])
            act = MF.forecastAction(meet["date"], today, self.store.get(k))
            if act is None:
                return False
            self.store[k] = today
            return True
        patch(MF, "saveRace", save)

    def tearDown(self):
        for mod, name, val in reversed(self.saved):
            setattr(mod, name, val)

    class _Conn:
        def commit(self):
            pass

        def rollback(self):
            pass

    def run_(self, today):
        return self.BMF.forecastMeet(None, self._Conn(), {"meet_id": 9, "source": "anet",
                                                          "date": self.plan["date"]}, today, False)

    def test_incremental_and_idempotent(self):
        self.ran = {3}                                  # race 3 already has results
        got = self.run_(D(2026, 10, 8))
        self.assertEqual(got["written"], 2)
        self.assertEqual(sorted(self.predicted), [1, 2])
        self.predicted.clear()
        # the same night again: nothing re-predicted
        got = self.run_(D(2026, 10, 8))
        self.assertEqual((got["written"], self.predicted), (0, []))
        # the night before the meet: refreshed once, then not again
        self.assertEqual(self.run_(D(2026, 10, 10))["written"], 2)
        self.predicted.clear()
        self.assertEqual(self.run_(D(2026, 10, 10))["written"], 0)
        self.assertEqual(self.predicted, [])

    def test_no_prediction_on_or_after_the_meets_day(self):
        for today in (D(2026, 10, 11), D(2026, 10, 12), D(2026, 11, 1)):
            got = self.run_(today)
            self.assertEqual(got["written"], 0)
        self.assertEqual(self.predicted, [])
        self.assertEqual(self.store, {})

    def test_the_calendar_drops_meets_on_or_before_today(self):
        import weekend
        saved = weekend.comingUp
        rows = [{"meet_id": i, "sport": sp, "date": d, "n_races": 1, "name": str(i)}
                for i, sp, d in ((1, "XC", "2026-10-10"), (2, "XC", "2026-10-11"),
                                 (3, "TF", "2026-10-11"), (4, "XC", "2026-10-15"))]
        weekend.comingUp = lambda cur, today: [{"date": r["date"], "meets": [r]} for r in rows]
        try:
            got = self.BMF.postedXC(None, TODAY)
            self.assertEqual([m["meet_id"] for m in got], [2, 4])
            self.assertEqual([m["meet_id"] for m in self.BMF.postedXC(None, TODAY, {4})], [4])
        finally:
            weekend.comingUp = saved


# ------------------------------------------------------------------ #
#  the recap's numbers
# ------------------------------------------------------------------ #

class RecapMetrics(unittest.TestCase):
    def test_rank_agreement(self):
        import meet_recap as R
        self.assertAlmostEqual(R.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(R.spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)
        self.assertIsNone(R.spearman([1, 2], [1, 2]))
        self.assertEqual(R.pairsAhead([1, 2, 3], [1, 3, 2]), 2 / 3)

    def test_a_race_against_its_forecast(self):
        import meet_recap as R
        rc = R.raceRecap(_forecast(), _finishers(), ACTUAL_TEAMS)
        self.assertEqual((rc["n_pred"], rc["n_finish"], rc["n_matched"], rc["n_dnr"]), (6, 6, 5, 1))
        # we had Ann, Bea won
        self.assertFalse(rc["picked"])
        self.assertEqual((rc["pick"]["name"], rc["winner"]["name"]), ("Ann", "Bea"))
        # the top six (the field is six): five of ours, Zed not
        self.assertEqual((rc["top_hits"], rc["top_n"]), (5, 6))
        self.assertEqual([o["name"] for o in rc["outside"]], ["Zed"])
        # the miss: |pred - actual| / actual, median of five
        errs = sorted(abs(p - a) / a * 100 for p, a in
                      ((900, 912), (910, 905), (920, 940), (930, 925), (940, 941)))
        self.assertAlmostEqual(rc["median_pct"], errs[2])
        # surprises: Bea beat us most, Cy fell short most
        self.assertEqual(rc["beat"][0]["name"], "Bea")
        self.assertEqual(rc["short"][0]["name"], "Cy")
        self.assertLess(rc["beat"][0]["diff_pct"], 0)
        # teams: we had A, B won; B up one, A down one; D never scored
        self.assertIs(rc["team_picked"], False)
        self.assertEqual([t["team"] for t in rc["teams_up"]], ["B"])
        self.assertEqual([t["team"] for t in rc["teams_down"]], ["A"])
        # the table is in predicted order, Flo did not run
        self.assertEqual(rc["rows"][-1]["name"], "Flo")
        self.assertIsNone(rc["rows"][-1]["act_place"])
        words = " ".join(R.raceWords(rc))
        self.assertIn("Got 5 of the top 6.", words)
        self.assertIn("Missed the winner: we had Ann, Bea won.", words)
        self.assertNotIn("—", words)                 # no em dashes

    def test_no_results_no_recap(self):
        import meet_recap as R
        self.assertIsNone(R.raceRecap(_forecast(), [], []))

    def test_week_sums_leave_small_fields_out(self):
        import meet_recap as R
        rc = R.raceRecap(_forecast(), _finishers(), ACTUAL_TEAMS)
        sc = R.compactScore(rc)
        self.assertEqual(sc["winner"], "Bea")
        hit = dict(sc, picked=True, n_finish=200)
        miss = dict(sc, picked=False, n_finish=200)
        small = dict(sc, picked=True, n_finish=6)
        s = R.summarize([hit, miss, small], min_field=20)
        self.assertEqual((s["races"], s["picked"], s["small"]), (2, 1, 1))
        self.assertEqual(s["picked_pct"], 50.0)
        self.assertEqual(s["runners"], 10)
        line = R.summaryWords(s, "Week of Oct 5 to 11")
        self.assertTrue(line.startswith("Week of Oct 5 to 11: 2 races, median miss "))
        self.assertIn("winner picked 50%", line)
        self.assertIsNone(R.summarize([small], min_field=20))

    def test_weeks_run_monday_to_sunday(self):
        import meet_recap as R
        self.assertEqual(R.weekStart(TODAY), D(2026, 10, 5))
        self.assertEqual(R.weekLabel(D(2026, 10, 5)), "Oct 5 to 11")
        self.assertEqual(R.weekLabel(D(2026, 9, 28)), "Sep 28 to Oct 4")
        self.assertEqual(R.parseWeek("2026-10-08", TODAY), D(2026, 10, 5))
        self.assertEqual(R.parseWeek("junk", TODAY), D(2026, 10, 5))


# ------------------------------------------------------------------ #
#  the pages
# ------------------------------------------------------------------ #

class _Cur:
    connection = None

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return {"t": "x"}

    def fetchall(self):
        return []

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self, *a, **k):
        c = _Cur()
        c.connection = self
        return c

    def rollback(self):
        pass


@contextlib.contextmanager
def _getConn():
    yield _Conn()


def _stubClocks():
    import conversions as cv
    cv._scale["map"] = {(p, s): (pm, -0.025, -0.0253)
                        for p, pm in (("hs_m", 1247.6), ("hs_f", 1390.0)) for s in ("XC", "TF")}
    cv._scale["at"] = 1e18
    cv._offsets["map"], cv._offsets["at"] = {}, 1e18
    cv._gain["map"], cv._gain["at"] = {}, 1e18
    cv._clock_tables.clear()


class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import app as A
        except Exception as exc:                          # noqa: BLE001
            raise unittest.SkipTest(f"app does not import here: {exc}")
        import pages3
        import ttlcache
        import meet_forecast as MF
        import meet_recap as R
        import upcoming_preview as U
        _stubClocks()
        cls.saved = []

        def patch(mod, name, val):
            cls.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, val)
        fc = _forecast()
        patch(pages3, "getConn", _getConn)
        patch(pages3, "_today", lambda: TODAY)
        patch(MF, "loadMeet", lambda cur, mid, src=None: [dict(fc)] if mid == 9 else [])
        patch(MF, "loadRace", lambda cur, mid, div, src=None: MF.asPrediction(fc) if mid == 9 else None)
        patch(MF, "weekRows", lambda cur, lo, hi, st=None: [dict(fc, score=R.compactScore(
            R.raceRecap(fc, _finishers(), ACTUAL_TEAMS)))] if st in (None, "CA") else [])
        patch(MF, "latestScoredDate", lambda cur: D(2026, 10, 3))
        patch(MF, "recapKeys", lambda cur, today: {("anet", 9)})
        patch(R, "actualByDiv", lambda cur, mid, src: {1: _finishers()})
        patch(R, "actualTeams", lambda cur, fin, st, src: ACTUAL_TEAMS)
        patch(R, "_minField", lambda: 5)
        patch(U, "livePrediction", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-predicted")))
        plan = {"meet_id": 9, "source": "anet", "name": "Clovis Invitational", "date": "2026-10-03",
                "venue": "Woodward Park", "state": "CA",
                "edition": {"meet_id": 8, "date": "2025-10-04", "name": "Clovis Invitational"},
                "races": [{"div_id": 1, "label": "Varsity Girls", "distance": 5000, "gender": "F",
                           "matched": "race", "schools": {("A", "CA"): 7}, "persons": set()}]}
        patch(U, "meetPlan", lambda cur, mid, src=None: plan if mid == 9 else None)
        patch(U, "conditions", lambda *a, **k: {})
        patch(U, "courseRecords", lambda *a: None)
        ttlcache.clear()
        cls.plan = plan
        cls.client = A.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for mod, name, val in reversed(cls.saved):
            setattr(mod, name, val)
        import ttlcache
        ttlcache.clear()

    def test_the_recap_page(self):
        r = self.client.get("/meet/recap/xc/9")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Clovis Invitational", html)
        self.assertIn("Got 5 of the top 6.", html)
        self.assertIn("Beat our forecast the most", html)
        self.assertIn("/card/recap/xc/9.png", html)
        self.assertIn("Zed", html)                       # outside the field, listed
        self.assertNotIn("—", html)
        self.assertEqual(self.client.get("/meet/recap/xc/77").status_code, 404)

    def test_the_week(self):
        r = self.client.get("/recaps?week=2026-10-01")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Sep 28 to Oct 4", html)
        self.assertIn('href="/meet/recap/xc/9"', html)
        self.assertIn("No forecasts were stored", self.client.get("/recaps?week=2026-10-01&state=OR")
                      .get_data(as_text=True))
        # no week asked: the newest scored one
        self.assertIn("Sep 28 to Oct 4", self.client.get("/recaps").get_data(as_text=True))

    def test_preview_after_the_meet_goes_to_the_recap(self):
        r = self.client.get("/meet/preview/xc/9")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/meet/recap/xc/9"))

    def test_a_recap_before_the_meet_goes_to_the_preview(self):
        import pages3
        import ttlcache
        saved = pages3._today
        pages3._today = lambda: D(2026, 10, 1)
        ttlcache.clear()
        try:
            r = self.client.get("/meet/recap/xc/9")
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers["Location"].endswith("/meet/preview/xc/9"))
        finally:
            pages3._today = saved
            ttlcache.clear()

    def test_the_stored_forecast_is_what_the_preview_reads(self):
        import upcoming_preview as U
        import ttlcache
        ttlcache.clear()
        pred = U.racePrediction(_Cur(), 9, 1, None)       # livePrediction would raise
        self.assertEqual(pred["frozen"]["made_on"], "2026-10-02")
        api = self.client.get("/api/meet-preview/xc/9/1").get_json()
        self.assertEqual(api["frozen"], "2026-10-02")
        self.assertEqual(api["runners"][0]["name"], "Ann")
        # ★ the 5K column: each runner's rating as a track 5K, and its header
        import conversions as cv
        self.assertEqual(api["runners"][0]["five_k"], cv.fiveK(120.0, "hs_f", "XC"))
        self.assertEqual(api["fk_label"], "5K")

    def test_links_to_the_recap(self):
        import pages3
        with self.A_ctx():
            helpers = pages3._previewHelpers()
            self.assertEqual(helpers["recap_href"](9), "/meet/recap/xc/9")
            self.assertEqual(helpers["recap_href"](9, "anet"), "/meet/recap/xc/9")
            self.assertIsNone(helpers["recap_href"](9, "tfrrs"))
            self.assertIsNone(helpers["recap_href"](10))

    def A_ctx(self):
        import app as A
        return A.app.test_request_context()

    def test_the_card(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("no Pillow")
        import cards
        d = cards.recapCardData(_Cur(), 9)
        self.assertEqual(d["stats"][0], ("WINNERS PICKED", "0 of 1"))
        self.assertEqual([r["name"] for r in d["rows"]], ["Bea", "Ann", "Zed", "Dee", "Cy"])
        self.assertEqual(d["rows"][2]["had"], "not in field")
        png = cards.renderRecapCard(d)
        self.assertTrue(png.startswith(b"\x89PNG"))


class FiveKTemplates(unittest.TestCase):
    """The 5K column on the pages3 pages that show ratings."""

    def test_they_compile_and_use_it(self):
        try:
            import app as A
        except Exception as exc:                          # noqa: BLE001
            self.skipTest(f"app does not import here: {exc}")
        root = os.path.join(ROOT, "racecast")
        for name in ("movers.html", "athlete_profile.html"):
            A.app.jinja_env.get_template(name)
            src = open(os.path.join(root, "templates", name), encoding="utf-8").read()
            self.assertIn("five_k", src, name)
        self.assertIn("pv-fk", open(os.path.join(root, "templates", "meet_preview.html"),
                                    encoding="utf-8").read())
        self.assertIn("five_k", open(os.path.join(root, "static", "next-race.js"),
                                     encoding="utf-8").read())

    def test_next_race_carries_the_5k(self):
        _stubClocks()
        import upcoming_preview as U
        import conversions as cv
        self.assertEqual(U.fiveKOf(121.9, "hs_m"), cv.fiveK(121.9, "hs_m", "XC"))
        self.assertIsNone(U.fiveKOf(None, "hs_m"))
        self.assertIsNone(U.fiveKOf(121.9, None))


if __name__ == "__main__":
    unittest.main()
