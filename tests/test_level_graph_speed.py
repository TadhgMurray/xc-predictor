# Project: xc-predictor / tests
# File:    test_level_graph_speed.py
# Purpose: level_graph's cost, and the junk rule it must not change while
#          getting cheaper.
#
# ⚠⚠ IT TOOK 2h24m AND THEN FAILED (owner's run, 2026-09-20), losing
#    school_level_graph and race_level with it. Three things were wrong:
#    SQL that Postgres cannot run, a session with 8MB of temp buffers for a
#    temp table holding tens of millions of rows, and a case-insensitive
#    REGEX evaluated per row over 225M rows, twice.
#
#   python -m unittest tests.test_level_graph_speed
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _src():
    import sys
    sys.path.insert(0, os.path.join(_ROOT, "engine"))
    import level_graph as lg
    return lg


# ★ THE BATTERY. Real spellings from the corpus plus the adversarial
#   near-misses -- names that START like junk but are schools. The SQL and the
#   regex must agree on every one, and the ones that matter most are the ones
#   that must be KEPT.
CASES = [
    "", " ", "\t", "\n", "  \t \n ", "0", "00", "una", "UNA", "Una", "unat",
    "UNAT-On Athletics Club", "unattached", "Unattached", "UNATTACHED",
    "11-Unattached", "unattached runner", "individual", "Individual",
    "INDIVIDUALS", "n/a", "N/A", "na", "NA", "?", "???", "? ?",
    "Boston University", "St. John's", "Oregon", "Naperville", "Nation Ford",
    "Unatego", "Individual Medley HS", "0 High School", " Herriman ",
    "Una Vista HS",
]


def _regexKeeps(s, junk):
    """What the old `btrim(school) !~* junk` kept."""
    return re.search(junk, s.strip(" "), re.IGNORECASE) is None


def _sqlKeeps(s):
    """A Python model of _JUNK_SQL: lower(btrim(school, ' \\t\\r\\n')) and the
    equality / LIKE tests."""
    t = s.strip(" \t\r\n").lower()
    if t == "" or t in ("0", "una", "n/a", "na"):
        return False
    if "unattached" in t or t.startswith("unat") or "individual" in t:
        return False
    if t.replace("?", "") == "":
        return False
    return True


class TheCheapJunkTestSaysTheSameThing(unittest.TestCase):
    """★ A FASTER PREDICATE THAT KEEPS DIFFERENT ROWS IS NOT AN OPTIMISATION,
    it is a silent change to the level graph -- and the graph decides pools."""

    def setUp(self):
        self.lg = _src()

    def test_every_spelling_agrees(self):
        for s in CASES:
            self.assertEqual(_regexKeeps(s, self.lg._JUNK), _sqlKeeps(s),
                             f"{s!r} is treated differently")

    def test_the_near_misses_are_kept(self):
        """! THE ONES THAT MATTER. A school whose name merely STARTS like junk
        must survive both."""
        for s in ("Naperville", "Nation Ford", "Una Vista HS",
                  "Boston University", " Herriman "):
            self.assertTrue(_sqlKeeps(s), s)

    def test_the_real_junk_is_dropped(self):
        for s in ("", "\t", "0", "una", "UNATTACHED", "11-Unattached",
                  "individual", "n/a", "???"):
            self.assertFalse(_sqlKeeps(s), s)

    def test_the_wider_trim_is_deliberate(self):
        r"""! THE REGEX'S \s COVERS TAB AND NEWLINE and btrim's default does
        not, so the SQL trims them explicitly -- otherwise a tab-only school
        would survive a test that used to drop it."""
        self.assertIn(r"E' \t\r\n'", read("engine/level_graph.py"))

    def test_the_regex_is_gone_from_the_hot_path(self):
        src = read("engine/level_graph.py")
        i = src.index("INSERT INTO tmp_race_team")
        self.assertNotIn("!~*", src[i:i + 900])


class TheLikePatternsAreParameters(unittest.TestCase):
    """⚠ psycopg2 OWNS THE PERCENT SIGN. A bare '%unattached%' in the SQL TEXT
    becomes a broken placeholder the moment anything passes params to the same
    execute. Keeping the wildcards in VALUES means the hazard cannot come back
    when someone adds an argument later -- the same trap already cost a run in
    diag_xc_track_bridge."""

    def test_no_wildcards_in_the_sql_text(self):
        lg = _src()
        self.assertNotIn("'%unattached%'", lg._JUNK_SQL)
        self.assertIn("%(junk_unattached)s", lg._JUNK_SQL)

    def test_the_params_are_passed(self):
        src = read("engine/level_graph.py")
        self.assertIn("_JUNK_PARAMS", src)
        i = src.index("INSERT INTO tmp_race_team")
        self.assertIn("_JUNK_PARAMS", src[i:i + 900])


class TheSessionIsTunedBeforeItTouchesATempTable(unittest.TestCase):
    """★ temp_buffers IS THE ONE THAT MATTERS. tmp_race_team is a TEMP table
    of tens of millions of rows, and a temp table lives in LOCAL buffers --
    8MB by default, not the 16GB shared_buffers this server is tuned for."""

    def setUp(self):
        self.src = read("engine/level_graph.py")

    def test_it_sets_the_memory_that_matters(self):
        for name in ("temp_buffers", "work_mem", "maintenance_work_mem",
                     "max_parallel_workers_per_gather"):
            self.assertIn(name, self.src)

    def test_tuning_runs_before_the_preflight(self):
        """! temp_buffers CANNOT BE CHANGED once the session has touched a
        temp table, and the preflight creates two."""
        # ! INSIDE main(), NOT ANYWHERE IN THE FILE -- `def preflight(cur):`
        #   sits above main() and a bare .index() found the definition.
        i = self.src.index("def main(live=False):")
        body = self.src[i:]
        self.assertLess(body.index("tune(cur)"), body.index("preflight(cur)"))
        self.assertLess(body.index("preflight(cur)"), body.index("buildGraph(cur)"))

    def test_a_refused_setting_does_not_fail_the_run(self):
        """! A BOX WITH LESS RAM IS SLOWER, NOT BROKEN -- and it says which
        setting was refused so the next run can lower it."""
        i = self.src.index("def tune(")
        body = self.src[i:self.src.index("\ndef ", i + 10)]
        self.assertIn("REFUSED", body)
        self.assertIn("except Exception", body)

    def test_every_value_is_overridable(self):
        for env in ("XCP_PG_TEMP_BUFFERS", "XCP_PG_WORK_MEM",
                    "XCP_PG_MAINT_WORK_MEM", "XCP_PG_PARALLEL"):
            self.assertIn(env, self.src)


class TheLockAdviceRunsOnThisMachine(unittest.TestCase):
    """⚠ fuser AND lsof ARE NOT INSTALLED on the server. Advice that does not
    run on the machine printing it is not advice."""

    def test_the_chain_points_at_the_script_that_works(self):
        chain = read("scripts/overnight_fit_pool_solve.sh")
        self.assertIn("who_holds_the_lock.sh", chain)
        self.assertNotIn("fuser -v .pipeline.lock", chain)

    def test_the_script_uses_proc_and_nothing_else(self):
        src = read("scripts/who_holds_the_lock.sh")
        self.assertIn("/proc/", src)
        for tool in ("fuser", "lsof"):
            self.assertNotIn(f"\n{tool} ", src)

    def test_it_refuses_to_recommend_deleting_the_lock(self):
        """! flock RELEASES NOTHING WHEN THE FILE IS DELETED, and a second
        pipeline then starts on top of the first."""
        src = read("scripts/who_holds_the_lock.sh")
        self.assertIn("Do NOT rm the lock file", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
