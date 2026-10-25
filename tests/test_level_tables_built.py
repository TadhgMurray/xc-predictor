# Project: xc-predictor / tests
# File:    test_level_tables_built.py
# Purpose: The level tables the backfill, the pack and the boards read are
#          rebuilt by the pipeline, in an order no step can read across.
#
# ★ WHY (2026-10-25: "the tables nothing builds"). athlete_season_level,
#   college_first_season, upperclass_first_season and race_top_level were
#   read every run and built only by hand. The graph (school_level_graph,
#   race_level, race_top_level) is staged as <name>_next in the background
#   and swapped in by the NEXT run's first steps; the season levels run after
#   the person steps and grade_sanity and are collected before 05.
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


class LevelTablesBuilt(unittest.TestCase):
    def setUp(self):
        self.sh = read("deploy/run_pipeline.sh")
        self.lg = read("engine/level_graph.py")

    def at(self, pattern):
        m = re.search(pattern, self.sh, re.M)
        self.assertIsNotNone(m, pattern)
        return m.start()

    def test_the_staged_graph_goes_live_before_anything_reads_it(self):
        swap = self.at(r"^step 00c_level_swap .*level_graph\.py --swap-staged")
        for reader in (r"^step 03_pro_flag", r"^step 04_grade_sanity", r"steps2 05_backfill_xc"):
            self.assertLess(swap, self.at(reader))

    def test_the_graph_is_rebuilt_in_the_background_as_next(self):
        self.at(r"^bgstep 01c_level_graph .*level_graph\.py --stage")
        self.assertIn('_SUFFIX["s"] = "_next"', self.lg)
        for nm in ("school_level_graph", "race_level", "race_top_level"):
            self.assertIn(f'"{nm}"', self.lg[self.lg.index("_STAGED = ("):][:120])

    def test_the_graph_lets_go_of_results_before_it_propagates(self):
        main = self.lg[self.lg.index("def main(live=False):"):]
        self.assertLess(main.index("conn.commit()"), main.index("propagate(cur)"))

    def test_the_season_levels_run_after_the_person_steps_and_before_05(self):
        start = self.at(r"^bgstep 04h_season_levels .*college_flag\.py --write.*season_level\.py --write")
        self.assertLess(self.at(r"^step 04_grade_sanity"), start)
        self.assertLess(self.at(r"^step 04d_gender"), start)
        join = self.at(r"^bgjoin 04h_season_levels")
        self.assertLess(join, self.at(r"steps2 05_backfill_xc"))


if __name__ == "__main__":
    unittest.main()
