"""Download CSV (owner, 2026-10-05): the table as shown, a link back per
row, safe in a spreadsheet, and the rankings API answers ?format=csv."""
import csv
import io
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


def _read(text):
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


@unittest.skipUnless(_HAVE, "flask not installed")
class CsvExport(unittest.TestCase):
    def test_ranking_rows_rank_from_the_page_offset(self):
        import csv_export as C
        rows = [{"name": "A", "school": "X", "rating": 101.234, "person_id": 7, "year": 2025},
                {"name": "B", "school": "Y", "rating": 99.0, "person_id": 8, "year": 2025}]
        out = _read(C.toCsv(rows, C.rankingColumns("ability", "https://racecast.co", 50)))
        self.assertEqual(out[0][:3], ["Rank", "Name", "School"])
        self.assertEqual(out[1][0], "51")
        self.assertEqual(out[2][0], "52")
        self.assertIn("101.2", out[1])
        self.assertEqual(out[1][-1], "https://racecast.co/athlete/7")
        # nobody has an HS-equivalent stamp: no empty column for it
        self.assertNotIn("Rating (HS equivalent)", out[0])

    def test_formula_cells_are_text(self):
        import csv_export as C
        out = _read(C.toCsv([{"name": "=HYPERLINK(\"x\")", "time_seconds": 900.5}],
                            C.raceXcColumns("https://racecast.co")))
        self.assertTrue(out[1][1].startswith("'="))
        self.assertEqual(out[1][out[0].index("Time")], "15:00.50")

    def test_time_format(self):
        import csv_export as C
        self.assertEqual(C.fmtTime(236.14), "3:56.14")
        self.assertEqual(C.fmtTime(3725.0), "1:02:05.0")
        self.assertIsNone(C.fmtTime(999999))
        self.assertIsNone(C.fmtTime(None))

    def test_school_prs_flatten(self):
        import csv_export as C
        secs = [{"label": "5000m", "tables": {"M": [{"name": "A", "display_mark": "15:00"}],
                                              "F": [{"name": "B", "display_mark": "17:00"},
                                                    {"name": "C", "display_mark": "17:30"}]}}]
        out = _read(C.toCsv(C.flattenSchoolPrs(secs), C.schoolPrColumns("https://r.co")))
        self.assertEqual(len(out), 4)
        self.assertEqual(out[3][:4], ["5000m", "Girls/Women", "2", "C"])

    def test_tf_sections_carry_their_heat(self):
        import csv_export as C
        secs = [{"label": "Heat 1", "rows": [{"athlete_name": "A", "sec_place": 1,
                                              "display_result": "4:10.00", "person_id": 3}]},
                {"label": "Heat 2", "rows": [{"is_relay": True, "relay_legs": [
                    {"name": "P"}, {"name": "Q"}], "sec_place": 1, "person_id": 9}]}]
        out = _read(C.toCsv(C.flattenTfSections(secs), C.raceTfColumns("https://r.co")))
        self.assertEqual(out[1][0], "Heat 1")
        self.assertEqual(out[2][2], "P; Q")
        self.assertEqual(out[2][-1], "")          # a relay links no single athlete


class _RankingsStub(unittest.TestCase):
    """The rankings route with the database stubbed out; no tests of its own."""
    def setUp(self):
        import app as A
        self.A = A
        self._saved = {k: getattr(A, k) for k in ("_inMaintenance", "getConn",
                                                  "getAbilityRankings")}
        A._inMaintenance = lambda: False

        class _Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False

        class _Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def cursor(s, **k): return _Cur()
            def rollback(s): pass
        A.getConn = lambda *a, **k: _Conn()
        A.getAbilityRankings = lambda cur, f: [
            {"name": "Zoë", "school": "Rockford", "rating": 150.0, "person_id": 1,
             "pool": "hs_m", "sport": "XC", "year": 2025}]
        import school_units
        self._units = school_units.applyUnitFilters
        school_units.applyUnitFilters = lambda cur, f, args: None
        self.c = A.app.test_client()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(self.A, k, v)
        import school_units
        school_units.applyUnitFilters = self._units



@unittest.skipUnless(_HAVE, "flask not installed")
class RankingsCsvRoute(_RankingsStub):
    def test_csv_is_an_attachment_of_the_same_rows(self):
        r = self.c.get("/api/rankings?board=ability&sport=XC&pool=hs_m&format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertIn("XC-hs_m", r.headers["Content-Disposition"])
        out = _read(r.data.decode("utf-8"))
        self.assertEqual(out[1][1], "Zoë")

    def test_json_unchanged_without_format(self):
        r = self.c.get("/api/rankings?board=ability&sport=XC&pool=hs_m")
        self.assertEqual(r.mimetype, "application/json")

    def test_csv_href_keeps_the_query(self):
        with self.A.app.test_request_context("/race/xc/5/6?alt=1&format=json"):
            self.assertEqual(self.A.csvHref(), "/race/xc/5/6?alt=1&format=csv")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_HAVE, "flask not installed")
class RankingsCsvWholeBoard(_RankingsStub):
    """&all=1: the whole board under the filters, refused past the ceiling."""
    def setUp(self):
        super().setUp()
        self.saved_count = self.A.countRows
        self.seen = {}

        def board(cur, f):
            self.seen.update(limit=f["limit"], offset=f["offset"])
            return [{"name": f"R{i}", "rating": 100.0 - i, "person_id": i, "pool": "hs_m",
                     "sport": "XC"} for i in range(f["limit"])]
        self.A.getAbilityRankings = board

    def tearDown(self):
        self.A.countRows = self.saved_count
        super().tearDown()

    def test_whole_board_is_one_query_of_the_counted_length(self):
        self.A.countRows = lambda cur, f: 3
        r = self.c.get("/api/rankings?board=ability&sport=XC&pool=hs_m&offset=50&format=csv&all=1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.seen, {"limit": 3, "offset": 0})
        self.assertEqual(len(_read(r.data.decode("utf-8"))), 4)
        self.assertIn("-all.csv", r.headers["Content-Disposition"])

    def test_too_long_is_refused_with_the_reason(self):
        import csv_export as C
        self.A.countRows = lambda cur, f: C.CSV_ALL_MAX + 1
        r = self.c.get("/api/rankings?board=ability&sport=XC&pool=hs_m&format=csv&all=1")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"Narrow it", r.data)

    def test_uncountable_is_refused(self):
        self.A.countRows = lambda cur, f: None
        r = self.c.get("/api/rankings?board=ability&sport=XC&pool=hs_m&format=csv&all=1")
        self.assertEqual(r.status_code, 400)
