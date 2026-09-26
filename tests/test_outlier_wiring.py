"""rating_outlier is read: the boards leave flagged rows out, the predictor's
rating basis skips them, the pipeline builds the table before the boards
(owner, 2026-09-26: "do the outlier table too")."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


class Conn:
    def __init__(self, have):
        self.have = have

    def cursor(self):
        have = self.have

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                pass

            def fetchone(self):
                return ("rating_outlier" if have else None,)
        return C()


class Wiring(unittest.TestCase):
    def test_board_build_anti_joins_the_table_when_it_exists(self):
        import build_ranking_results as B
        self.assertIn("rating_outlier ro", B._outlierClause(Conn(True), "TF"))
        self.assertIn("ro.sport = 'TF'", B._outlierClause(Conn(True), "TF"))
        self.assertEqual(B._outlierClause(Conn(False), "XC"), "")

    def test_the_pipeline_builds_it_before_the_boards(self):
        sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
        self.assertLess(sh.index("step 09d_outliers"),
                        sh.index("step 10_rankings_prepare"))
        self.assertLess(sh.index("step 09b_fill"), sh.index("step 09d_outliers"))

    def test_predictor_skips_flagged_races(self):
        import predict as P
        P._HAS_OUTLIERS = True
        try:
            self.assertIn("rating_outlier ro", P._outlierFilter(None, "XC"))
        finally:
            P._HAS_OUTLIERS = None


if __name__ == "__main__":
    unittest.main()
