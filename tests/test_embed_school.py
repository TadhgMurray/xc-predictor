"""The team widget (2026-10-05): /embed/school/<name> may be framed by any
site and no other page may; it shows each pool's top seven who have raced
this season, the team's board ranks and the newest meets."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

try:
    import flask                                               # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


class _Cur:
    def __init__(self):
        self.rows = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, p=None):
        self.rows = ([{"scope": "usa", "pool": "hs_m", "rank": 40},
                      {"scope": "CA", "pool": "hs_m", "rank": 3}]
                     if "team_season" in sql else [])

    def fetchall(self): return self.rows


class _Conn:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self, **k): return _Cur()
    def rollback(self): pass


@unittest.skipUnless(_HAVE, "flask not installed")
class EmbedSchool(unittest.TestCase):
    def setUp(self):
        import app as A
        import school
        import school_identity
        self.A, self.S, self.I = A, school, school_identity
        self.saved = [(A, "_inMaintenance"), (A, "getConn"), (school, "schoolHeader"),
                      (school, "schoolRoster"), (school, "schoolMeets"),
                      (school, "currentSeason"), (school_identity, "stateChips"),
                      (school_identity, "levelChips")]
        self.saved = [(m, k, getattr(m, k)) for m, k in self.saved]
        A._inMaintenance = lambda: False
        A.getConn = lambda *a, **k: _Conn()
        school.schoolHeader = lambda cur, s, *a: {"state": "CA"}
        school.currentSeason = lambda cur, s, sport, *a: 2026
        school.schoolMeets = lambda cur, s, sport, **k: [
            {"meet_id": 9, "meet_name": "Clovis Invite", "date": "2026-10-03"}]
        boys = [{"person_id": i, "name": f"Boy {i}", "grade": "11", "pool": "hs_m",
                 "mean_rating": 120 - i, "best_rating": 121 - i} for i in range(9)]
        school.schoolRoster = lambda cur, s, y, sport, **k: boys + [
            {"person_id": 99, "name": "Carried", "pool": "hs_m", "mean_rating": 200,
             "carried": True},
            {"person_id": 50, "name": "Girl", "grade": "10", "pool": "hs_f", "mean_rating": 110}]
        school_identity.stateChips = lambda cur, s, include=None: ([], "CA")
        school_identity.levelChips = lambda cur, s, st: []
        self.c = A.app.test_client()

    def tearDown(self):
        for m, k, v in self.saved:
            setattr(m, k, v)

    def test_frameable_and_contents(self):
        r = self.c.get("/embed/school/Clovis%20North?sport=XC")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("X-Frame-Options", r.headers)
        self.assertIn("frame-ancestors *", r.headers.get("Content-Security-Policy", ""))
        html = r.data.decode()
        self.assertIn("Boy 6", html)
        self.assertNotIn("Boy 7", html)          # top seven only
        self.assertNotIn("Carried", html)        # has not raced this season
        self.assertIn("Girl", html)
        self.assertIn("#40 in the nation", html)
        self.assertIn("#3 in CA", html)
        self.assertIn("Clovis Invite", html)
        self.assertIn('<base target="_blank">', html)

    def test_other_pages_stay_unframeable(self):
        r = self.c.get("/robots.txt")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")

    def test_dark_theme(self):
        html = self.c.get("/embed/school/Clovis%20North?theme=dark").data.decode()
        self.assertIn('data-theme="dark"', html)


if __name__ == "__main__":
    unittest.main()
