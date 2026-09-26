"""Shared predictions are saved and named by id (owner, 2026-09-26: "do the
shared prediction links next")."""
import contextlib
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

STORE = {}


class Cur:
    def __init__(self):
        self.last = None

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.last = [("shared_prediction",)]
        elif sql.lstrip().startswith("INSERT INTO shared_prediction"):
            sid, q, extra = params
            STORE.setdefault(sid, (q, extra))
            self.last = []
        elif "SELECT query, extra FROM shared_prediction" in sql:
            got = STORE.get(params[0])
            import json
            self.last = [(got[0], json.loads(got[1]))] if got else []
        else:
            self.last = []

    def fetchone(self):
        return self.last[0] if self.last else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def cursor(self, **k):
        return Cur()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


FIELD = ('[["Jesuit",["1","2","3","4","5","6","7"],7],'
         '["Loyola",["8","9","10","11","12"],5]]')
QUERY = ("mode=rerun&meet_id=238138&sport=XC&div_id=952652&date=2026-10-03"
         "&sim=1&field=" + __import__("urllib.parse").parse.quote(FIELD))


@unittest.skipUnless(_HAVE, "flask not installed")
class Share(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A
        self._saved = A.getConn
        A.getConn = lambda: Conn()
        STORE.clear()
        self.c = A.app.test_client()

    def tearDown(self):
        self.A.getConn = self._saved

    def test_save_then_read_back_the_whole_request(self):
        r = self.c.post("/api/predict/share",
                        json={"query": QUERY, "extra": {"names": {"12": "New Kid"}}})
        self.assertEqual(r.status_code, 200, r.data)
        sid = r.get_json()["id"]
        self.assertIn(f"/predictions?s={sid}", r.get_json()["url"])
        got = self.c.get(f"/api/predict/share/{sid}").get_json()
        self.assertEqual(got["query"], QUERY)
        self.assertEqual(got["extra"]["names"], {"12": "New Kid"})

    def test_the_same_prediction_is_one_link(self):
        a = self.c.post("/api/predict/share", json={"query": QUERY}).get_json()["id"]
        b = self.c.post("/api/predict/share", json={"query": QUERY}).get_json()["id"]
        self.assertEqual(a, b)
        self.assertEqual(len(STORE), 1)

    def test_a_request_without_a_meet_is_refused(self):
        r = self.c.post("/api/predict/share", json={"query": "mode=rerun"})
        self.assertEqual(r.status_code, 400)

    def test_the_page_previews_by_id(self):
        sid = self.c.post("/api/predict/share",
                          json={"query": QUERY}).get_json()["id"]
        html = self.c.get(f"/predictions?s={sid}").get_data(as_text=True)
        self.assertIn(f"/card/predict.png?s={sid}", html)

    def test_an_unknown_id_is_a_404_not_a_crash(self):
        self.assertEqual(self.c.get("/api/predict/share/zzzzzzzzzzzz").status_code, 404)
        self.assertEqual(self.c.get("/api/predict/share/../etc").status_code, 404)


if __name__ == "__main__":
    unittest.main()
