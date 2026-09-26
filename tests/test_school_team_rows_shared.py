# Project: xc-predictor / tests
# File:    test_school_team_rows_shared.py
# Purpose: authoritativeStates and buildTeamStates answer exactly what they
#          did when each scanned results and results_tf on its own.
#
# ⚠⚠ WHY OLD AGAINST NEW (2026-09-26). The two used to make four full passes
#    over the raw tables (12 GB + 72 GB, twice) with the same join to
#    anet_team; they now share one aggregate, si_team_raw, built by
#    _teamRows, and each applies its own filter when it reads it. The filters
#    are NOT the same -- authoritativeStates drops blank schools and keeps
#    rows with no person, buildTeamStates keeps blank schools and drops rows
#    with no person -- so the shared table has to be wide enough for both
#    and each reader has to narrow it back exactly. The two queries below
#    are the old ones, verbatim, and both answers are compared in full on a
#    corpus built to sit on every one of those edges.
#
#   python -m unittest tests.test_school_team_rows_shared
#
#   Needs a scratch Postgres: XCP_TEST_PG_HOST (a socket dir or host),
#   XCP_TEST_PG_PORT, XCP_TEST_PG_DB. It creates and drops results /
#   results_tf / anet_team there -- NEVER point it at the site's database.
#   Without the variables it is skipped.
import os
import random
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PG = os.environ.get("XCP_TEST_PG_HOST")

if _PG:
    os.environ.update(XCP_DB_HOST=_PG,
                      XCP_DB_PORT=os.environ.get("XCP_TEST_PG_PORT", "5432"),
                      XCP_DB_NAME=os.environ.get("XCP_TEST_PG_DB", "xcp_test"),
                      XCP_DB_USER=os.environ.get("XCP_TEST_PG_USER", "postgres"),
                      XCP_DB_PASSWORD=os.environ.get("XCP_TEST_PG_PASSWORD", "x"))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))


# ---- the two scans this replaced, verbatim ------------------------------ #
_OLD_AUTH = """
    SELECT lower(btrim(r.school)) AS name,
           upper(btrim(COALESCE(t.anet_state, t.state))) AS st
    FROM   {table} r
    JOIN   anet_team t ON t.team_id = r.team_id
    WHERE  r.team_id IS NOT NULL AND r.team_id <> 0
      AND  r.school IS NOT NULL AND btrim(r.school) <> ''
      AND  COALESCE(t.anet_state, t.state) IS NOT NULL
      AND  btrim(COALESCE(t.anet_state, t.state)) <> ''
    GROUP  BY 1, 2
"""

_OLD_TEAM = """
    INSERT INTO old_team_raw (person_id, school, state, n)
    SELECT r.person_id, r.school,
           upper(btrim(COALESCE(t.anet_state, t.state))), count(*)
    FROM   {table} r
    JOIN   anet_team t ON t.team_id = r.team_id
    WHERE  r.person_id IS NOT NULL
      AND  r.team_id IS NOT NULL AND r.team_id <> 0
      AND  r.school IS NOT NULL
      AND  COALESCE(t.anet_state, t.state) IS NOT NULL
      AND  btrim(COALESCE(t.anet_state, t.state)) <> ''
    GROUP  BY 1, 2, 3
"""

_OLD_TEAM_STATE = """
    SELECT person_id, school, state FROM (
        SELECT person_id, school, state,
               row_number() OVER (PARTITION BY person_id, school
                                  ORDER BY sum(n) DESC, state) AS rk
        FROM   old_team_raw GROUP BY 1, 2, 3) x
    WHERE  rk = 1
"""

_SCHOOLS = ["Oregon", "oregon", " Oregon ", "Williams", "WILLIAMS ", "", "  ",
            None, "Kingston", "Penn State", "Highland", "St. Mary's"]
_TEAMS = [  # team_id, school, state (our guess), anet_state (anet's)
    (1, "Oregon HS", "IL", "IL"), (2, "Univ of Oregon", "CA", "OR"),
    (3, "Williams", "ca ", None), (4, "Williams College", "NY", " ma"),
    (5, "Kingston", "WA", None), (6, "Kingston", "MO", "MO"),
    (7, "Penn State", "PA", "PA"), (8, "Highland", None, None),
    (9, "Highland", "  ", ""), (10, "Highland", "UT", "  "),
    (11, "St. Mary's", "CA", "CA"), (12, "St. Mary's", "TX", "tx"),
]


def _corpus(seed, n=3000):
    rnd = random.Random(seed)
    ids = [t[0] for t in _TEAMS] + [0, None, 99]      # 0, NULL, and no team
    return [(rnd.choice([None] + list(range(1, 60))),
             rnd.choice(_SCHOOLS), rnd.choice(ids)) for _ in range(n)]


# ! THE REAL psycopg2 FOR THE LENGTH OF A TEST. Several test modules put
#   stand-ins for psycopg2 / database into sys.modules when they are
#   collected (setdefault, so whoever is first wins), and under a full
#   pytest run those stand-ins are what `import psycopg2` returns here --
#   no connect, no execute_values. Swap the real package in, put the
#   stand-ins back afterwards so their owners keep what they installed.
def _realPsycopg2(test):
    names = [k for k in list(sys.modules)
             if k == "psycopg2" or k.startswith("psycopg2.")]
    if all(hasattr(sys.modules.get("psycopg2"), a)
           for a in ("connect", "extensions")):
        import psycopg2
        return psycopg2
    saved = {k: sys.modules.pop(k) for k in names}

    def restore():
        for k in [k for k in sys.modules
                  if k == "psycopg2" or k.startswith("psycopg2.")]:
            del sys.modules[k]
        sys.modules.update(saved)
    test.addCleanup(restore)
    import psycopg2
    import psycopg2.extras                                        # noqa: F401
    return psycopg2


@unittest.skipUnless(_PG, "set XCP_TEST_PG_HOST to a scratch Postgres")
class SharedScanAnswersBoth(unittest.TestCase):

    def setUp(self):
        # ! A PLAIN CONNECTION, NOT database.getConn: other test modules
        #   stub `database` in sys.modules, and these functions only need
        #   a cursor.
        psycopg2 = _realPsycopg2(self)
        import build_school_identity as bsi
        self.bsi = bsi
        self.conn = psycopg2.connect(
            host=_PG, port=os.environ.get("XCP_TEST_PG_PORT", "5432"),
            dbname=os.environ.get("XCP_TEST_PG_DB", "xcp_test"),
            user=os.environ.get("XCP_TEST_PG_USER", "postgres"),
            password=os.environ.get("XCP_TEST_PG_PASSWORD", "x"))
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.cur.execute("DROP TABLE IF EXISTS results, results_tf, "
                         "anet_team, school_team_link, college_directory, "
                         "athlete_season")
        self.conn.commit()
        self.conn.close()

    def _load(self, seed, tf_has_team=True, xc_has_team=True):
        cur = self.cur
        cur.execute("DROP TABLE IF EXISTS results, results_tf, anet_team, "
                    "school_team_link, college_directory, athlete_season")
        cur.execute("CREATE TABLE anet_team (team_id bigint PRIMARY KEY, "
                    "school text, state text, anet_state text)")
        cur.executemany("INSERT INTO anet_team VALUES (%s,%s,%s,%s)", _TEAMS)
        for table, has, s in (("results", xc_has_team, seed),
                              ("results_tf", tf_has_team, seed + 1)):
            cur.execute(f"CREATE TABLE {table} (person_id bigint, "
                        f"school text{', team_id bigint' if has else ''})")
            rows = _corpus(s)
            if has:
                cur.executemany(f"INSERT INTO {table} VALUES (%s,%s,%s)", rows)
            else:
                cur.executemany(f"INSERT INTO {table} VALUES (%s,%s)",
                                [r[:2] for r in rows])
        # athlete_season is read for the directory leg's names; empty here
        cur.execute("CREATE TABLE athlete_season (school text)")
        self.conn.commit()
        return [t for t, has in (("results", xc_has_team),
                                 ("results_tf", tf_has_team)) if has]

    def _old(self, tables):
        cur = self.cur
        by_name = {}
        for table in tables:
            cur.execute(_OLD_AUTH.format(table=table))
            for name, st in cur.fetchall():
                by_name.setdefault(name, set()).add(st)
        cur.execute("DROP TABLE IF EXISTS old_team_raw")
        cur.execute("CREATE TEMP TABLE old_team_raw "
                    "(person_id bigint, school text, state text, n bigint)")
        for table in tables:
            cur.execute(_OLD_TEAM.format(table=table))
        cur.execute(_OLD_TEAM_STATE)
        team = sorted(cur.fetchall(), key=repr)
        return {k: v for k, v in by_name.items() if len(v) >= 2}, team

    def _check(self, seed, **kw):
        tables = self._load(seed, **kw)
        want_contested, want_team = self._old(tables)
        contested, dir_states = self.bsi.authoritativeStates(self.cur)
        n = self.bsi.buildTeamStates(self.cur, contested)
        self.cur.execute("SELECT person_id, school, state FROM si_team_state")
        got_team = sorted(self.cur.fetchall(), key=repr)
        self.assertEqual(contested, want_contested)
        self.assertEqual(dir_states, {})
        self.assertEqual(got_team, want_team)
        self.assertEqual(n, len(want_team))
        return want_contested, want_team

    def test_both_tables(self):
        contested, team = self._check(11)
        # the corpus must reach the edges it claims to
        self.assertIn("oregon", contested)
        self.assertIn("williams", contested)
        self.assertTrue(any(s is not None and not s.strip()
                            for _p, s, _st in team),
                        "a blank school must reach si_team_state")

    def test_other_seeds(self):
        for seed in (21, 31, 41):
            self._check(seed)
            self.conn.rollback()

    def test_one_table_without_team_id(self):
        self._check(51, xc_has_team=False)

    def test_built_once_and_rebuilt_after_a_rollback(self):
        self._load(61)
        self.bsi.authoritativeStates(self.cur)
        self.cur.execute("SELECT count(*) FROM si_team_raw")
        n = self.cur.fetchone()[0]
        self.bsi._teamRows(self.cur)                   # second call: reused
        self.cur.execute("SELECT count(*) FROM si_team_raw")
        self.assertEqual(self.cur.fetchone()[0], n)
        self.conn.rollback()                           # takes the table
        self.bsi.buildTeamStates(self.cur)
        self.cur.execute("SELECT count(*) FROM si_team_raw")
        self.assertEqual(self.cur.fetchone()[0], n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
