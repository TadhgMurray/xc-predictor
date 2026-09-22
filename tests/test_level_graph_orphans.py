"""A result row with no meet_id or div_id has no race, and must not become
one.

⚠ 2026-09-22: level_graph failed after 30 minutes with
    NotNullViolation: null value in column "race" of relation "race_level"
    DETAIL: Failing row contains (null, hs, 1, 1).
  The race key is md5(meet_id || ':' || div_id || ...), and || with a NULL is
  NULL, so every orphan row in the corpus landed in ONE race keyed NULL.
  race_level's primary key refused it and the whole step rolled back --
  school_level_graph, race_level and race_top_level with it.

   python -m unittest tests.test_level_graph_orphans

   The build test needs a scratch Postgres (XCP_TEST_PG_HOST / _PORT / _DB,
   as tests/test_rr_index_names.py). It creates and drops results and
   results_tf there -- NEVER point it at the site's database.
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PG = os.environ.get("XCP_TEST_PG_HOST")


class _Capture:
    """A cursor that records SQL and answers every fetch with zeros."""
    def __init__(self):
        self.sql = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        return (0, 0, 0)


def _lg():
    sys.path.insert(0, os.path.join(_ROOT, "engine"))
    import level_graph
    return level_graph


class OrphanRowsHaveNoRace(unittest.TestCase):

    def test_both_scans_require_a_meet_and_a_division(self):
        cur = _Capture()
        _lg().buildGraph(cur)
        scans = [s for s in cur.sql if "INSERT INTO tmp_race_team" in s]
        self.assertEqual(len(scans), 2, "one scan per results table")
        for s in scans:
            self.assertIn("r.meet_id IS NOT NULL", s)
            self.assertIn("r.div_id IS NOT NULL", s)

    @unittest.skipUnless(_PG, "set XCP_TEST_PG_HOST to a scratch Postgres")
    def test_race_levels_build_with_orphans_in_the_corpus(self):
        os.environ.update(
            XCP_DB_HOST=_PG,
            XCP_DB_PORT=os.environ.get("XCP_TEST_PG_PORT", "5432"),
            XCP_DB_NAME=os.environ.get("XCP_TEST_PG_DB", "xcp_test"),
            XCP_DB_USER=os.environ.get("XCP_TEST_PG_USER", "postgres"),
            XCP_DB_PASSWORD=os.environ.get("XCP_TEST_PG_PASSWORD", "x"),
            XCP_DB_QUIET="1")
        sys.path.insert(0, os.path.join(_ROOT, "scripts"))
        from database import getConn
        lg = _lg()
        with getConn() as conn, conn.cursor() as cur:
            for t in ("results", "results_tf"):
                cur.execute(f"DROP TABLE IF EXISTS {t}; CREATE TABLE {t} "
                            f"(meet_id int, div_id int, source text, "
                            f" school text, team_id int)")
                rows = [(m, 1, None, f"School {k}", 100 + k)
                        for m in (1, 2) for k in range(6)]
                rows += [(None, 1, None, f"School {k}", 100 + k)
                         for k in range(6)]
                rows += [(3, None, None, f"School {k}", 100 + k)
                         for k in range(6)]
                cur.executemany(f"INSERT INTO {t} VALUES (%s,%s,%s,%s,%s)",
                                rows)
            lg.buildGraph(cur, min_teams=2)
            cur.execute("SELECT count(*) FROM tmp_race_team WHERE race IS NULL")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("CREATE TEMP TABLE tmp_level (school text, level text)")
            cur.execute("INSERT INTO tmp_level "
                        "SELECT DISTINCT school, 'hs' FROM tmp_race_team")
            self.assertEqual(lg.raceLevels(cur), 2)    # was NotNullViolation
            conn.rollback()


if __name__ == "__main__":
    unittest.main()
