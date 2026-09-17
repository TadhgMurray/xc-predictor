# Project: xc-predictor / tests
# File:    test_school_name_variants.py
# Purpose: two spellings, one team -- and the four schools called Oregon that
#          must NOT be merged. No database.
#
#   python -m pytest -q tests/test_school_name_variants.py
#   python tests/test_school_name_variants.py
#
# ★ THE CASE (owner, 2026-09-17): one athlete, twice on one board.
#     15  Chiara Dailey  La Jolla (CA)  9:53.38
#     16  Chiara Dailey  La Jolla-CA    9:49.57
#
# ⚠ AND THE CASE AGAINST THE OBVIOUS FIX, from the same message: "Oregon" is
#   a word prefix of "Oregon Episcopal", "Oregon Clay" and "Oregon School for
#   the Deaf". A substring rule merges four schools in three states and undoes
#   the split the rest of this session built. The name proposes; the athletes
#   decide; an anet team id vetoes.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import school_name as N                                          # noqa: E402
import merge_school_names as M                                   # noqa: E402


class Decoration(unittest.TestCase):
    """The four shapes the feeds actually write."""

    def test_every_shape_reduces_to_the_same_base(self):
        for spelling in ("La Jolla (CA)", "La Jolla [CA]", "La Jolla-CA",
                         "La Jolla - CA", "La Jolla, CA", "La Jolla"):
            self.assertEqual(N.nameKey(spelling), "la jolla", spelling)

    def test_the_owners_two_rows_are_one_team(self):
        self.assertEqual(N.relation("La Jolla (CA)", "La Jolla-CA"), "same")

    def test_a_trailing_level_word_is_decoration_too(self):
        self.assertEqual(N.relation("La Jolla (CA)", "La Jolla HS"), "same")
        self.assertEqual(N.relation("Williams", "Williams College"), "same")

    def test_the_code_has_to_be_a_real_state(self):
        """"Mid-Pacific" and "Tri-Valley" end in a hyphen and letters too."""
        for name in ("Mid-Pacific", "Tri-Valley", "Washington-Liberty",
                     "Notre Dame-XY"):
            self.assertIsNone(N.splitStateSuffix(name)[1], name)

    def test_a_decoration_is_never_the_whole_name(self):
        self.assertEqual(N.splitStateSuffix("(CA)"), ("(CA)", None))

    def test_a_bare_trailing_code_is_not_a_decoration(self):
        """No delimiter, no decision: "Washington" and every school whose
        last word happens to be two letters would be at risk."""
        base, st = N.splitStateSuffix("La Jolla CA")
        self.assertIsNone(st)
        self.assertEqual(base, "La Jolla CA")

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(N.relation("la  jolla (ca)", "La Jolla-CA"), "same")

    def test_a_name_is_not_a_variant_of_itself(self):
        self.assertIsNone(N.relation("La Jolla (CA)", "La Jolla (CA)"))


class NotTheSameSchool(unittest.TestCase):
    """The Oregons. A word prefix is a QUESTION, never an answer."""

    def test_the_four_oregons_are_never_merely_the_same(self):
        for other in ("Oregon Episcopal", "Oregon Clay",
                      "Oregon School for the Deaf"):
            self.assertEqual(N.relation("Oregon", other), "prefix", other)

    def test_a_character_prefix_is_not_a_word_prefix(self):
        self.assertIsNone(N.relation("Oregon", "Oregonian Academy"))

    def test_prefix_pairs_are_not_even_candidates_by_default(self):
        names = ["Oregon", "Oregon Episcopal", "Oregon Clay"]
        self.assertEqual(M.candidates(names), [])
        # Oregon is a prefix of each of the other two; those two are neither
        # a prefix nor a respelling of each other
        self.assertEqual(M.candidates(names, want_prefix=True),
                         [("Oregon", "Oregon Clay", "prefix"),
                          ("Oregon", "Oregon Episcopal", "prefix")])


class TheAthletesDecide(unittest.TestCase):

    def test_the_owners_case_merges(self):
        rosters = {"La Jolla (CA)": set(range(1, 21)),
                   "La Jolla-CA": set(range(15, 31))}   # 6 shared of 16
        pairs = M.candidates(rosters.keys())
        merged, refused = M.judge(pairs, rosters, {})
        self.assertEqual(len(merged), 1, refused)
        self.assertEqual(merged[0][5], 6)

    def test_two_teams_that_share_nobody_do_not_merge(self):
        rosters = {"Kingston (WA)": set(range(1, 40)),
                   "Kingston-MO": set(range(100, 140))}
        merged, refused = M.judge(M.candidates(rosters.keys()), rosters, {})
        self.assertEqual(merged, [])
        self.assertIn("0 shared", refused[0][7])

    def test_one_transfer_is_not_a_merge(self):
        """MIN_FRACTION: a single shared athlete between two big rosters is
        somebody who moved, not two spellings."""
        rosters = {"Fairview (CO)": set(range(1, 61)),
                   "Fairview-CO": set(range(60, 120))}   # exactly 1 shared
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, {})
        self.assertEqual(merged, [])

    def test_a_prefix_needs_overwhelming_evidence(self):
        rosters = {"Oregon": set(range(1, 101)),
                   "Oregon Episcopal": set(range(95, 125))}   # 6 shared of 30
        pairs = M.candidates(rosters.keys(), want_prefix=True)
        merged, refused = M.judge(pairs, rosters, {})
        self.assertEqual(merged, [], "a prefix must not merge on a few shared")
        self.assertIn("needs 10", refused[0][7])


class TheTeamIdVetoes(unittest.TestCase):

    def test_different_anet_teams_are_two_schools_whatever_the_names(self):
        rosters = {"La Jolla (CA)": set(range(1, 21)),
                   "La Jolla-CA": set(range(1, 21))}     # identical rosters
        teams = {"La Jolla (CA)": {111}, "La Jolla-CA": {222}}
        merged, refused = M.judge(M.candidates(rosters.keys()), rosters, teams)
        self.assertEqual(merged, [])
        self.assertIn("different anet teams", refused[0][7])

    def test_the_same_team_id_does_not_block(self):
        rosters = {"La Jolla (CA)": set(range(1, 21)),
                   "La Jolla-CA": set(range(1, 21))}
        teams = {"La Jolla (CA)": {111}, "La Jolla-CA": {111}}
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, teams)
        self.assertEqual(len(merged), 1)

    def test_a_missing_team_id_is_no_veto(self):
        rosters = {"La Jolla (CA)": set(range(1, 21)),
                   "La Jolla-CA": set(range(1, 21))}
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters,
                            {"La Jolla (CA)": {111}})
        self.assertEqual(len(merged), 1)


class Grouping(unittest.TestCase):

    def test_three_spellings_become_one_group_not_a_chain(self):
        rosters = {"La Jolla": set(range(1, 41)),
                   "La Jolla (CA)": set(range(1, 31)),
                   "La Jolla-CA": set(range(1, 21))}
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, {})
        grouped = M.groups(merged, rosters)
        self.assertEqual(len(grouped), 1)
        canon, variants = grouped[0]
        self.assertEqual(canon, "La Jolla")            # the most-used spelling
        self.assertEqual(sorted(v[0] for v in variants),
                         ["La Jolla (CA)", "La Jolla-CA"])

    def test_the_canonical_is_the_most_used_not_the_tidiest(self):
        self.assertEqual(M.canonicalOf([("La Jolla", 3), ("La Jolla-CA", 40)]),
                         "La Jolla-CA")

    def test_ties_are_stable(self):
        self.assertEqual(M.canonicalOf([("B School", 5), ("A", 5)]), "A")
        self.assertIsNone(M.canonicalOf([]))

    def test_every_variant_resolves_in_one_lookup(self):
        """No variant may also be some other row's canonical."""
        rosters = {"La Jolla": set(range(1, 41)),
                   "La Jolla (CA)": set(range(1, 31)),
                   "La Jolla-CA": set(range(1, 21))}
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, {})
        grouped = M.groups(merged, rosters)
        canons = {c for c, _ in grouped}
        variants = {v[0] for _c, vs in grouped for v in vs}
        self.assertFalse(canons & variants)


class Scale(unittest.TestCase):

    def test_the_candidate_search_is_bucketed_not_quadratic(self):
        """Hundreds of thousands of school strings: every-to-every is not a
        slow query, it is an impossible one."""
        import time
        # ⚠ EVERY ONE OF THESE SHARES A FIRST WORD, which is what the first
        #   version bucketed on -- it took 90 seconds on this input.
        names = [f"Saint {i} Academy" for i in range(20000)] + \
                ["La Jolla (CA)", "La Jolla-CA"]
        t0 = time.time()
        pairs = M.candidates(names, want_prefix=True)
        el = time.time() - t0
        self.assertEqual([p for p in pairs if "Jolla" in p[0]],
                         [("La Jolla (CA)", "La Jolla-CA", "same")])
        self.assertLess(el, 5.0, f"{el:.1f}s on {len(names):,} names")


class Wiring(unittest.TestCase):
    """The alias only matters where it is applied."""

    @staticmethod
    def _read(*parts):
        import io as _io
        with _io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_boards_fold_the_name_before_anything_reads_it(self):
        src = self._read("racecast", "build_ranking_results.py")
        self.assertIn("school = canonicalSchool(row.school)", src)
        i = src.index("school = canonicalSchool(row.school)")
        after = src[i:i + 400]
        self.assertLess(after.index("_isNonSchoolCached(school)"),
                        after.index("_is_dodea(school)"))

    def test_every_stream_process_loads_it(self):
        """The four --stage stream runs are separate processes; a load in
        `prepare` would leave all four folding nothing."""
        src = self._read("racecast", "build_ranking_results.py")
        i = src.index('if stage in (None, "stream"):')
        self.assertIn("loadNameAliases(_cur)", src[i:i + 900])

    def test_an_absent_table_is_todays_behaviour(self):
        src = self._read("racecast", "build_ranking_results.py")
        i = src.index("def loadNameAliases(")
        body = src[i:src.index("\ndef canonicalSchool", i)]
        self.assertIn("SELECT to_regclass('school_name_alias')", body)
        self.assertIn("return 0", body)

    def test_the_raw_tables_are_never_rewritten(self):
        """The standing rule: the feeds keep their own spelling, so the undo
        is a rerun with the table dropped."""
        src = self._read("scripts", "merge_school_names.py")
        for forbidden in ("UPDATE results", "UPDATE results_tf",
                          "SET school ="):
            self.assertNotIn(forbidden, src)

    def test_the_pipeline_builds_it_before_the_boards(self):
        sh = self._read("deploy", "run_pipeline.sh")
        self.assertLess(sh.index("09c_school_names"),
                        sh.index("10_rankings_prepare"))
        self.assertIn("scripts/merge_school_names.py --write", sh)

    def test_a_chain_is_resolved_and_cannot_loop(self):
        flat = N.resolveChains({"A": "B", "B": "C", "X": "Y", "Y": "X"})
        self.assertEqual(flat["A"], "C")
        self.assertEqual(flat["B"], "C")
        # a cycle resolves to a fixed point rather than hanging
        self.assertIn(flat["X"], ("X", "Y"))
        self.assertEqual(N.resolveChains({}), {})

    def test_the_board_loader_flattens_through_the_pure_helper(self):
        src = self._read("racecast", "build_ranking_results.py")
        self.assertIn("from school_name import resolveChains", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
