# Project: xc-predictor / tests
# File:    test_ranking_indexes.py
# Purpose: a table never swaps in without the indexes the site needs.
#
# ⚠⚠ THE SITE WENT DOWN FOR THIS (owner, 2026-09-21). ranking_results was
#    live at 135M rows with 5 of its 13 canonical indexes. The missing one
#    that mattered was rr_result_idx: pool_view.stampRowsHs runs on EVERY
#    race, course, compiled and compare page, so every page seq-scanned the
#    whole table. All eight gunicorn workers sat in the same query and the
#    site stopped answering -- and the pipeline had reported no failure at all.
#
#    Two mechanisms, both silent:
#      1. rename() inserted another "_new" on every rebuild, because `name` IS
#         `like` + "_new". The live database still shows
#         idx_athlete_season_new_new_new_new_new_new_new_new_new_person.
#         Postgres truncates at 63 chars, so long names collide -- and
#         CREATE INDEX IF NOT EXISTS then SKIPS a different index silently.
#      2. buildIndexes trusted its own loop. Nothing checked afterwards that
#         the indexes existed.
#
#   python -m unittest tests.test_ranking_indexes
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def renameName(idxname, like, name):
    """The rename() logic, lifted so repeated rebuilds can be exercised."""
    import hashlib
    stable = re.sub(re.escape(like) + r"(?:_new)+", like, idxname)
    newname = (stable if stable.startswith(name)
               else stable.replace(like, name, 1)
               if like in stable else f"{name}_{stable}")
    if len(newname) > 63:
        tag = hashlib.md5(newname.encode()).hexdigest()[:8]
        newname = f"{newname[:54]}_{tag}"
    return newname


class TheShadowNameIsIdempotent(unittest.TestCase):
    """★ THE SAME LIVE INDEX MUST YIELD THE SAME SHADOW NAME however many
    rebuilds it has been through. swapIn leaves the shadow's names on the live
    table, so each run feeds its own output back in."""

    def test_ten_rebuilds_do_not_grow_the_name(self):
        like, name = "athlete_season", "athlete_season_new"
        n = "idx_athlete_season_person"
        seen = set()
        for _ in range(10):
            n2 = renameName(n, like, name)
            seen.add(n2)
            n = n2.replace(name, like, 1)     # what swapIn leaves behind
        self.assertEqual(len(seen), 1, f"name drifted across rebuilds: {seen}")
        self.assertNotIn("new_new", seen.pop())

    def test_the_observed_corruption_is_normalised(self):
        """⚠ THE REAL NAME FROM THE LIVE DATABASE, with nine _new's."""
        broken = ("idx_athlete_season_new_new_new_new_new_new_new_new_new"
                  "_person")
        got = renameName(broken, "athlete_season", "athlete_season_new")
        self.assertEqual(got, "idx_athlete_season_new_person")

    def test_a_too_long_name_is_truncated_uniquely(self):
        """! A PLAIN CUT MERGES TWO DIFFERENT NAMES, and IF NOT EXISTS then
        skips the second index without a word. A hash keeps them apart."""
        a = renameName("rr_" + "x" * 70, "ranking_results", "ranking_results_new")
        b = renameName("rr_" + "x" * 71, "ranking_results", "ranking_results_new")
        self.assertLessEqual(len(a), 63)
        self.assertLessEqual(len(b), 63)
        self.assertNotEqual(a, b)


class TheSwapRefusesABareTable(unittest.TestCase):
    """★★ THE POSTCONDITION IS ASSERTED, NOT TRUSTED. A bare table takes the
    site down; a stale one merely serves yesterday's numbers. So a missing
    index raises BEFORE swapIn."""

    def setUp(self):
        self.src = read("racecast/build_ranking_results.py")

    def test_verify_runs_after_the_build(self):
        self.assertIn("def verifyIndexes(", self.src)
        i = self.src.index("def buildIndexes(")
        body = self.src[i:self.src.index("\ndef _indexSignatures", i)]
        self.assertIn("verifyIndexes(conn, name, like)", body)

    def test_verify_runs_before_the_swap(self):
        """! ORDER IS THE WHOLE POINT."""
        self.assertLess(self.src.index("verifyIndexes(conn, name, like)"),
                        self.src.index("swapIn(conn)"))

    def test_it_compares_signatures_not_names(self):
        """! THE SHADOW'S NAMES ARE DERIVED and the live table's are
        historical; what a query can use is the column list."""
        self.assertIn("def _indexSignatures(", self.src)
        i = self.src.index("def verifyIndexes(")
        body = self.src[i:i + 3000]
        self.assertIn("_sig(c) not in have", body)

    def test_it_rebuilds_before_it_gives_up(self):
        i = self.src.index("def verifyIndexes(")
        body = self.src[i:i + 3000]
        self.assertIn("rebuilding serially", body)

    def test_it_raises_rather_than_swapping(self):
        i = self.src.index("def verifyIndexes(")
        body = self.src[i:i + 3000]
        self.assertIn("raise RuntimeError", body)
        self.assertIn("REFUSING to swap it in", body)


class TheIndexTheSiteCannotLiveWithout(unittest.TestCase):
    """! rr_result_idx IS THE ONE. pool_view.stampRowsHs runs on every race,
    course, compiled and compare page; without it each seq-scans the table."""

    def test_it_is_canonical(self):
        src = read("racecast/build_ranking_results.py")
        i = src.index("_CANONICAL_INDEXES")
        block = src[i:i + 4000]
        self.assertIn('"(result_id)"', block)
        self.assertIn("person_id) INCLUDE", block)

    def test_the_page_query_still_matches_it(self):
        """⚠ IF THE QUERY CHANGES SHAPE the index stops covering it, and the
        symptom is identical: every page scans the table."""
        pv = read("racecast/pool_view.py")
        self.assertIn("FROM ranking_results", pv)
        self.assertIn("result_id = ANY(%s)", pv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
