"""The owner's status page (racecast/site_status.py, /account/status)."""
import contextlib
import datetime
import os
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import site_status as S                                        # noqa: E402

try:
    import flask                                               # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False

SUMMARY = """  04a_link_tfrrs ok (812s)
  05_backfill_xc FAILED after 4410s
  04e_weather_grid skipped (x not found)
  06_fit ok (95s, in parallel)
"""


class Summary(unittest.TestCase):
    def test_reads_ok_failed_and_parallel_lines(self):
        got = S.parseSummary(SUMMARY)
        self.assertEqual([(s["step"], s["ok"], s["seconds"]) for s in got],
                         [("04a_link_tfrrs", True, 812),
                          ("05_backfill_xc", False, 4410),
                          ("06_fit", True, 95)])
        self.assertEqual(got[2]["how"], "in parallel")

    def test_a_run_dir(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "20260925_010203")
            os.makedirs(d)
            open(os.path.join(d, "summary.log"), "w").write(SUMMARY)
            open(os.path.join(d, "05_backfill_xc.log"), "w").write(
                "a\nb\npsycopg2.errors.UndefinedTable: results_tf_new\n")
            open(os.path.join(d, "07_solve.log"), "w").write("solving\n")
            old = os.path.join(root, "20260924_000000")
            os.makedirs(old)
            open(os.path.join(old, "summary.log"), "w").write("  x ok (1s)\n")
            pl = S.pipeline(root)
            run = pl["latest"]
            self.assertEqual(run["dir"], "20260925_010203")
            self.assertEqual(run["failed"], ["05_backfill_xc"])
            self.assertIn("UndefinedTable", run["steps"][1]["tail"][-1])
            self.assertEqual([s["step"] for s in run["open"]], ["07_solve"])
            self.assertEqual(run["started"], datetime.datetime(2026, 9, 25, 1, 2, 3))
            self.assertEqual(len(pl["recent"]), 1)

    def test_no_logs_says_so(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIn("error", S.pipeline(root))

    def test_ago(self):
        self.assertEqual(S.ago(5), "5s")
        self.assertEqual(S.ago(125), "2m")
        self.assertEqual(S.ago(3 * 3600 + 60 * 7), "3h 7m")
        self.assertEqual(S.ago(3 * 86400), "3d")


class BrokenConn:
    def __init__(self):
        self.rolled = 0

    def cursor(self):
        conn = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                raise RuntimeError("relation \"issue_reports\" does not exist")
        return C()

    def rollback(self):
        self.rolled += 1


class Blocks(unittest.TestCase):
    def test_a_failing_block_is_an_error_not_a_crash(self):
        conn = BrokenConn()
        got = S.openReports(conn)
        self.assertIn("issue_reports", got["error"])
        self.assertEqual(conn.rolled, 1)


def _fake_status():
    now = datetime.datetime(2026, 9, 25, 12, 0)
    return {
        "now": now, "season": 2026,
        "pipeline": {"latest": {"dir": "20260925_010203",
                                "started": now - datetime.timedelta(hours=11),
                                "steps": [{"step": "05_backfill_xc", "ok": False,
                                           "seconds": 4410, "how": "",
                                           "tail": ["boom"]}],
                                "open": [{"step": "07_solve", "idle_s": 4000,
                                          "tail": ["solving"]}],
                                "failed": ["05_backfill_xc"], "seconds": 4410},
                     "recent": [{"dir": "20260924_000000", "started": None,
                                 "failed": [], "seconds": 1, "n": 1, "open": 0}]},
        "identity": [{"sport": "XC", "season": 2026, "rows": 79291, "person": 0,
                      "native_only": 69278, "normalized": 0, "rated": 0}],
        "rated": [{"sport": "XC", "source": "anet", "rows": 10, "rated": 8,
                   "latest": "2026-09-20"}],
        "blank": {"blank": 1200, "queued_meets": 44},
        "queries": [{"pid": 7, "app": "psql", "state": "active", "seconds": 900,
                     "wait": "Lock:relation", "query": "SELECT 1"}],
        "reports": {"open": 1, "rows": [{
            "id": 3, "at": datetime.datetime(2026, 9, 25, 10, 0,
                                             tzinfo=datetime.timezone.utc),
            "kind": "identity", "page": "/athlete/5", "detail": "wrong <b>",
            "email": None}]},
    }


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class Route(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A
        self._saved = (A._accounts.currentSession, S.gather, A.getConn)
        S.gather = lambda conn: _fake_status()
        A.getConn = lambda: contextlib.nullcontext(None)
        self.client = A.app.test_client()

    def tearDown(self):
        self.A._accounts.currentSession, S.gather, self.A.getConn = self._saved

    def _as(self, email):
        sess = None if email is None else {
            "csrf": "t", "account": {"email": email}}
        self.A._accounts.currentSession = lambda: sess

    def test_signed_out_goes_to_login(self):
        self._as(None)
        r = self.client.get("/account/status")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_a_reader_is_told_nothing(self):
        os.environ["XCP_ADMIN_EMAILS"] = "owner@example.com"
        self._as("someone@example.com")
        self.assertEqual(self.client.get("/account/status").status_code, 404)

    def test_the_owner_sees_every_block(self):
        os.environ["XCP_ADMIN_EMAILS"] = "owner@example.com"
        self._as("Owner@example.com")
        r = self.client.get("/account/status")
        self.assertEqual(r.status_code, 200, r.data[:500])
        html = r.get_data(as_text=True)
        self.assertIn("1 pipeline step failed", html)
        self.assertIn("1 open report", html)
        for want in ("05_backfill_xc", "probably dead", "79,291", "69,278",
                     "1,200", "Lock:relation", "/athlete/5", "Resolve"):
            self.assertIn(want, html)
        self.assertIn("wrong &lt;b&gt;", html)       # report text is escaped
        self.assertIn("no-store", r.headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()
