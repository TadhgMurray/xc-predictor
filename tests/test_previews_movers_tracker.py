"""The 2026-10-10 features (pages3.py): next-race preview, meet preview,
movers, the team season tracker, the comps fan and the recruiting one-pager.
The pure rules are pinned here; the routes are run through the Flask test
client with every database boundary replaced, so a template that cannot
render fails here and not on the site."""
import contextlib
import datetime
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", ""):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")


class UpcomingRules(unittest.TestCase):
    def test_words_and_links(self):
        import upcoming_preview as U
        self.assertEqual(U.dateLabel("2026-10-17"), "Sat Oct 17")
        self.assertEqual(U.dateLabel("junk"), "")
        self.assertEqual(U.clock(902.6), "15:03")
        self.assertIsNone(U.clock(None))
        self.assertEqual(U.ordinal(12), "12th")
        self.assertEqual(U.ordinal(23), "23rd")
        self.assertEqual(U.previewHref(5, "tfrrs"), "/meet/preview/xc/5?src=tfrrs")
        self.assertEqual(U.previewHref(5, "anet"), "/meet/preview/xc/5")
        self.assertEqual(U.predictHref(5, 2, None), "/predictions?meet_id=5&sport=XC&div_id=2")

    def test_place_among_the_projected_field(self):
        import upcoming_preview as U
        self.assertEqual(U.placeAmong(900.0, [880, 899.9, 900.0, 950, None]), 3)
        self.assertIsNone(U.placeAmong(None, [1, 2]))

    def test_race_order_prefers_evidence(self):
        import upcoming_preview as U
        varsity = {"label": "Varsity Boys", "persons": set(), "school_n": 7}
        jv = {"label": "JV Boys", "persons": {42}, "school_n": 12}
        # ran the JV race last time: that is the evidence that counts
        self.assertGreater(U.raceOrder(jv, 42, False), U.raceOrder(varsity, 42, True))
        # no history: in the predicted field beats not
        self.assertGreater(U.raceOrder(varsity, 7, True), U.raceOrder(dict(jv, persons=set()), 7, False))
        # nothing else: the varsity label beats the JV one with more runners
        self.assertGreater(U.raceOrder(varsity, 7, False), U.raceOrder(dict(jv, persons=set()), 7, False))

    def test_team_place_counts_scoring_teams(self):
        import upcoming_preview as U
        pred = {"teams": [{"team": "A", "state": "CA", "score": 30},
                          {"team": "B", "state": "CA", "score": None},
                          {"team": "C", "state": "OR", "score": 61}]}
        self.assertEqual(U.teamPlace(pred, "C", "OR"), (2, 61, 2))
        self.assertEqual(U.teamPlace(pred, "B", "CA"), (None, None, 2))


class TrackerRules(unittest.TestCase):
    def test_season_rating_is_the_boards_rule(self):
        import team_tracker as T
        from above_level import _quantile
        self.assertAlmostEqual(T.seasonRating([100, 110, 120]), _quantile([100, 110, 120], 0.8))
        # a race 20+ under the median is dropped first
        self.assertAlmostEqual(T.seasonRating([120, 121, 122, 90]),
                               _quantile([120, 121, 122], 0.8))
        self.assertIsNone(T.seasonRating([]))

    def test_weeks_end_on_sunday(self):
        import team_tracker as T
        w = T.weekEnds("2026-08-29", "2026-09-10")       # a Saturday, a Thursday
        self.assertEqual(w, [datetime.date(2026, 8, 30), datetime.date(2026, 9, 6),
                             datetime.date(2026, 9, 13)])
        self.assertTrue(all(d.weekday() == 6 for d in w))

    def test_team_week(self):
        import team_tracker as T
        out = T.teamWeek([120, 118, 125, 110, 115, 100, 99, 98])
        self.assertAlmostEqual(out["top5"], (125 + 120 + 118 + 115 + 110) / 5)
        self.assertAlmostEqual(out["gap15"], 15)
        self.assertEqual(len(out["top7"]), 7)
        self.assertIsNone(T.teamWeek([120, 118])["top5"])

    def test_replay_races_the_field_each_week(self):
        import team_tracker as T
        rows = []
        pid = 0
        for school, base in (("Fast", 130), ("Slow", 110)):
            for i in range(7):
                pid += 1
                rows.append({"person_id": pid, "school": school, "grade": "11",
                             "day": "2026-09-05", "rating": base - i, "meet_id": 1})
        # the slow team improves past the fast one in week two
        for i in range(7):
            rows.append({"person_id": 8 + i, "school": "Slow", "grade": "11",
                         "day": "2026-09-12", "rating": 150 - i, "meet_id": 2})
        pts = T.replay(rows, "Slow", T.weekEnds("2026-09-05", "2026-09-12"))
        self.assertEqual([p["place"] for p in pts], [2, 1])
        self.assertEqual(pts[1]["meets"], [2])
        self.assertEqual(pts[0]["n_teams"], 2)


class MoversRules(unittest.TestCase):
    def test_rank_changes_window(self):
        import movers as M
        rows = [{"person_id": 1, "now": 3, "prev": 10},
                {"person_id": 2, "now": 40, "prev": 60},     # entered and rose
                {"person_id": 3, "now": 55, "prev": 20},     # fell off the window
                {"person_id": 4, "now": 5, "prev": None},    # first ranked
                {"person_id": 5, "now": 7, "prev": 7}]
        risers, fallers, entered = M.rankChanges(rows, 50)
        self.assertEqual([r["person_id"] for r in risers], [2, 1])
        self.assertEqual([r["person_id"] for r in fallers], [3])
        self.assertEqual([r["person_id"] for r in entered], [4, 2])

    def test_above_level(self):
        import movers as M
        hist = {1: [("2026-09-01", 100.0), ("2026-09-08", 101.0), ("2026-09-15", 99.0)],
                2: [("2026-09-01", 120.0), ("2026-09-08", 121.0), ("2026-09-15", 119.0)]}
        win = [{"person_id": 1, "day": "2026-10-04", "rating": 108.0},
               {"person_id": 2, "day": "2026-10-04", "rating": 120.5}]
        sigma, rows = M.aboveLevel(win, hist)
        self.assertGreater(sigma, 0)
        self.assertEqual([r["person_id"] for r in rows], [1])
        self.assertGreater(rows[0]["vs_level"], sigma)

    def test_sentences_fire_only_on_facts(self):
        import movers as M
        self.assertEqual(M.sentences({"risers": [], "entered": [], "breakouts": [], "above": []},
                                     "Ohio", 50), [])
        d = {"risers": [{"name": "A B", "school": "X", "change": 9, "now": 4},
                        {"name": "C D", "school": "Y", "change": 9, "now": 11}],
             "entered": [], "breakouts": [], "above": []}
        s = str(M.sentences(d, "Ohio", 50)[0])
        self.assertIn("rose 9 places to 4th in Ohio", s)
        self.assertIn("level with 1 other", s)


class CompsFan(unittest.TestCase):
    def test_spread_has_the_fan_only_with_enough_comps(self):
        import comps
        few = comps._spread([1.0, 2.0, 3.0])
        self.assertIsNone(few["p10"])
        many = comps._spread([float(v) for v in range(1, 22)])
        self.assertAlmostEqual(many["p10"], 3.0)
        self.assertAlmostEqual(many["p90"], 19.0)
        self.assertAlmostEqual(many["mean"], 11.0)

    def test_next_season_fan(self):
        import comps
        res = {"subject": {"year": 2026, "sport": "XC", "grade": 10, "rating": 120.0},
               "next": comps._spread([float(v) for v in range(110, 131)])}
        fan = comps.nextSeasonFan(res)
        self.assertEqual(fan["year"], 2027)
        self.assertEqual(fan["fan_q"], [0.1, 0.9])
        self.assertIsNone(comps.nextSeasonFan({"subject": {}, "next": None}))


class ProfileFit(unittest.TestCase):
    def test_top_fit_is_fastest_in_recruit_range(self):
        import profile_page as PP
        rows = [{"school": s, "division": "NCAA DIII", "min": m - 9, "p25": m - 4,
                 "median": m, "p75": m + 3, "max": m + 8}
                for s, m in (("Reach", 140.0), ("Fit", 123.0), ("Easy", 110.0), ("Edge", 124.0))]
        out = PP.topFit(rows, 120.0, n=2)
        self.assertEqual([r["school"] for r in out], ["Edge", "Fit"])


# ------------------------------------------------------------------ #
#  the routes, with the database replaced at every boundary
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


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import app as A
        except Exception as exc:                          # noqa: BLE001
            raise unittest.SkipTest(f"app does not import here: {exc}")
        import pages3, predict, ttlcache
        import upcoming_preview as U
        import movers as M
        import team_tracker as T
        import breakouts as B
        cls.A = A
        cls.saved = []

        def patch(mod, name, val):
            cls.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, val)
        patch(pages3, "getConn", _getConn)
        patch(pages3, "_today", lambda: datetime.date(2026, 10, 10))
        patch(predict, "_currentSeason", lambda cur, sport: 2026)
        plan = {"meet_id": 9, "source": "anet", "name": "Clovis Invitational", "date": "2026-10-17",
                "venue": "Woodward Park", "state": "CA",
                "edition": {"meet_id": 8, "date": "2025-10-11", "name": "Clovis Invitational"},
                "races": [{"div_id": 1, "label": "Varsity Boys", "distance": 5000, "gender": "M",
                           "matched": "race", "schools": {("A", "CA"): 7}, "persons": set()}]}
        patch(U, "meetPlan", lambda cur, mid, src=None: plan if mid == 9 else None)
        patch(U, "conditions", lambda *a, **k: {"difficulty": None, "forecast_text": "14°C"})
        patch(U, "courseRecords", lambda *a: None)
        pred = {"available": True, "n_field": 2,
                "teams": [{"team": "A", "state": "CA", "score": 15, "scorers": []}],
                "runners": [{"person_id": 1, "name": "R One", "school": "A", "place": 1, "seconds": 900.0},
                            {"person_id": 2, "name": "R Two", "school": "A", "place": 2, "seconds": 905.0}]}
        patch(U, "racePrediction", lambda *a, **k: pred)
        patch(M, "_weeks", lambda *a: [])
        patch(B, "fromTable", lambda *a, **k: {"anchor": None, "breakouts": []})
        patch(B, "latestDates", lambda cur: {})
        patch(T, "_division", lambda *a: (None, []))
        patch(T, "_rows", lambda *a: [{"person_id": i, "school": "A", "grade": "11",
                                       "day": "2026-09-05", "rating": 120.0 - i, "meet_id": 1}
                                      for i in range(7)])
        patch(T, "_names", lambda cur, ids: {})
        patch(T, "_meetNames", lambda cur, ids: {})
        ttlcache.clear()
        cls.client = A.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for mod, name, val in reversed(cls.saved):
            setattr(mod, name, val)
        import ttlcache
        ttlcache.clear()

    def test_meet_preview_renders_and_api_is_public(self):
        r = self.client.get("/meet/preview/xc/9")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Clovis Invitational", r.data)
        self.assertIn(b"the last edition", r.data)
        self.assertEqual(self.client.get("/meet/preview/xc/77").status_code, 404)
        api = self.client.get("/api/meet-preview/xc/9/1")
        self.assertEqual(api.get_json()["teams"][0]["place"], 1)
        self.assertIn("public", api.headers["Cache-Control"])

    def test_movers_pages(self):
        self.assertEqual(self.client.get("/movers").status_code, 200)
        r = self.client.get("/movers?state=CA&pool=hs_f&sport=xc")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"This week in California", r.data)
        self.assertIn(b"two weekly snapshots", r.data)

    def test_team_season_page(self):
        r = self.client.get("/school/A/season?pool=hs_m&state=CA")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Week by week", r.data)
        self.assertEqual(self.client.get("/school/Unattached/season").status_code, 404)


if __name__ == "__main__":
    unittest.main()
