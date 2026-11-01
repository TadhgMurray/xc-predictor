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
#   decide; the anet STATE vetoes (not the team id -- anet carries
#   duplicate teams for one school; see TheStateVetoes).
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

    def test_a_level_word_is_NOT_decoration(self):
        """⚠⚠ THE FIRST DRY RUN (2026-09-17). Treating "middle", "high",
        "college" as noise folded:

            Adrian      <- Adrian College (38)  AND  Adrian Middle School (5)
            Cumberland  <- Cumberland High School AND Cumberland Middle School
            Norwell     <- Norwell Middle School (329 athletes)

        A middle school is not its high school and a college is neither --
        that is the Amherst College / Amherst Regional split this branch is
        about. The shared athletes are REAL (a kid runs the middle school and
        then the high school), which is exactly why the evidence cannot be
        allowed to overrule a level word."""
        for a, b in (("Adrian College", "Adrian Middle School"),
                     ("Cumberland High School", "Cumberland Middle School"),
                     ("Sanford School", "Sanford High School")):
            self.assertIsNone(N.relation(a, b), f"{a} / {b}")
        # a shortening is a prefix QUESTION, needing --prefix and a high bar
        self.assertEqual(N.relation("Williams", "Williams College"), "prefix")


class NotATeam(unittest.TestCase):
    """⚠⚠ THE FIRST DRY RUN folded FORTY spellings of "Unattached" into one
    "school" of several thousand athletes. They share athletes with each
    other because they are the same SENTINEL, not the same roster -- so this
    module's whole premise is void for them."""

    def test_every_spelling_of_unattached_is_refused(self):
        for name in ("Unattached", "unattached", "UNATTACHED",
                     "Unattached (IL)", "Unattached-IN", "Unattached - MT",
                     "42-UNATTACHED", "01 01-Unattached", "-Unattached",
                     "Unattached High School", "unattached-co", "UNAT",
                     "Independent", "Individual", "Alumni", "Open", "None"):
            self.assertFalse(N.mergeable(name), name)

    def test_they_are_not_even_candidates(self):
        names = ["Unattached", "Unattached (IL)", "UNATTACHED", "Unattached-IN"]
        self.assertEqual(M.candidates(names), [])
        self.assertEqual(M.candidates(names, want_prefix=True), [])

    def test_a_two_letter_string_is_a_state_code_or_a_typo(self):
        """"-mi" and "MI" both reduce to "mi", and were merged with each
        other."""
        self.assertFalse(N.mergeable("-mi"))
        self.assertFalse(N.mergeable("MI"))
        self.assertEqual(M.candidates(["-mi", "MI"]), [])

    def test_a_real_school_is_still_mergeable(self):
        for name in ("La Jolla (CA)", "La Jolla-CA", "Adrian College",
                     "Oregon", "Cumberland Middle School"):
            self.assertTrue(N.mergeable(name), name)

    def test_the_list_agrees_with_the_one_the_boards_use(self):
        """panels.isTeamName is the same judgement and is what the boards
        filter on; normalize_distance._NON_SCHOOLS is it again. Copied, not
        imported -- so pinned equal here rather than left to drift."""
        import io as _io
        import re as _re
        with _io.open(os.path.join(_ROOT, "racecast", "panels.py"),
                      encoding="utf-8") as fh:
            panels = fh.read()
        frags = _re.search(r"_NOT_A_TEAM_FRAGMENTS = \(([^)]*)\)", panels).group(1)
        for frag in _re.findall(r'"([^"]+)"', frags):
            self.assertIn(frag, N._NOT_A_TEAM_FRAGMENTS, frag)

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
        # 2026-11-01: the named states now refuse it before the count does
        self.assertTrue("0 shared" in refused[0][7] or "different states" in refused[0][7])

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


class TheStateVetoes(unittest.TestCase):
    """⚠⚠ THE TEAM ID WAS THE WRONG VETO (second dry run, 2026-09-17).
    "different anet teams -> different schools" is false, because anet
    itself carries duplicate teams for one school:

        Peak To Peak / Peak to Peak   117 shared   [19494] vs [37702]
        RHAM         / Rham           110 shared   [14773] vs [58439]
        St Cloud     / St. Cloud      458 shared   [15430] vs [40114]

    Every one is one school spelled twice, refused over two anet rows.

    ★ THE STATE IS THE SIGNAL. A string whose teams span more than one state
      is a name several schools wear and is never merged; two strings each
      in one state, the same state, with a team's worth of shared athletes,
      are one school."""

    def test_two_spellings_in_one_state_are_one_school(self):
        rosters = {"Peak To Peak": set(range(1, 40)),
                   "Peak to Peak": set(range(20, 60))}
        states = {"Peak To Peak": {"CO"}, "Peak to Peak": {"CO"}}
        merged, refused = M.judge(M.candidates(rosters.keys()), rosters, states)
        self.assertEqual(len(merged), 1, refused)

    def test_a_name_several_schools_wear_is_never_merged(self):
        """St Thomas Aquinas: 942 shared athletes, and still not one team."""
        rosters = {"St Thomas Aquinas": set(range(1, 2000)),
                   "St. Thomas Aquinas": set(range(1000, 3000))}
        states = {"St Thomas Aquinas": {"FL", "KS", "NJ"},
                  "St. Thomas Aquinas": {"CA", "NH"}}
        merged, refused = M.judge(M.candidates(rosters.keys()), rosters, states)
        self.assertEqual(merged, [])
        self.assertIn("a name several schools wear", refused[0][7])

    def test_one_wide_string_is_enough_to_refuse(self):
        """"Glendale" spans CA and AZ; "Glendale (CA)" does not. Merging the
        narrow one INTO the wide one would swallow the other Glendales."""
        rosters = {"Glendale": set(range(1, 500)),
                   "Glendale (CA)": set(range(1, 200))}
        states = {"Glendale": {"CA", "AZ"}, "Glendale (CA)": {"CA"}}
        merged, refused = M.judge(M.candidates(rosters.keys()), rosters, states)
        self.assertEqual(merged, [])

    def test_different_states_are_different_schools(self):
        rosters = {"Kingston (WA)": set(range(1, 40)),
                   "Kingston-MO": set(range(1, 40))}
        states = {"Kingston (WA)": {"WA"}, "Kingston-MO": {"MO"}}
        merged, refused = M.judge(M.candidates(rosters.keys()), rosters, states)
        self.assertEqual(merged, [])
        self.assertIn("different states", refused[0][7])

    def test_no_anet_team_is_no_veto(self):
        """A tfrrs-only string has no anet state; the athletes decide alone."""
        rosters = {"La Jolla (CA)": set(range(1, 21)),
                   "La Jolla-CA": set(range(1, 21))}
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, {})
        self.assertEqual(len(merged), 1)
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters,
                            {"La Jolla (CA)": {"CA"}})
        self.assertEqual(len(merged), 1)


class Grouping(unittest.TestCase):

    def test_three_spellings_become_one_group_not_a_chain(self):
        rosters = {"La Jolla": set(range(1, 41)),
                   "La Jolla (CA)": set(range(1, 31)),
                   "La Jolla-CA": set(range(1, 21))}
        # (2026-11-01: an unnamed school spelling with no athletic.net state
        #  needs 80% of its runners; La Jolla's anet team is in CA)
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, {"La Jolla": {"CA"}})
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
        # (2026-11-01: an unnamed school spelling with no athletic.net state
        #  needs 80% of its runners; La Jolla's anet team is in CA)
        merged, _ = M.judge(M.candidates(rosters.keys()), rosters, {"La Jolla": {"CA"}})
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
        # ! the owner's school pin is read first since 2026-10-10
        #   (scripts/person_pins.py); the fold still runs before anything else
        line = "school = canonicalSchool(pinnedSchool(row.person_id, sport, row.date) or row.school)"
        self.assertIn(line, src)
        i = src.index(line)
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


class Exhaustive(unittest.TestCase):
    """2026-11-01 (owner: "try to be exhaustive with variants"), from the
    NESCAC list: Conn College / Connecticut College, Trinity / Trinity
    (Conn.), Wesleyan / Wesleyan (Conn.)."""

    def test_spelling_rules(self):
        for a, b in (("Conn College", "Connecticut College"),
                     ("Trinity (Conn.)", "Trinity"), ("Wesleyan (Conn.)", "Wesleyan"),
                     ("St. Mark's", "Saint Marks"), ("Mt. Hebron", "Mount Hebron"),
                     ("La Jolla HS", "La Jolla High School"), ("Penn St.", "Penn State"),
                     ("N. Central", "North Central"), ("St. Cloud (Minn.)", "St Cloud"),
                     ("Miami (Ohio)", "Miami-OH")):
            self.assertEqual(N.nameKey(a), N.nameKey(b), f"{a} / {b}")
        self.assertEqual(N.statedState("Trinity (Conn.)"), "CT")
        self.assertEqual(N.statedState("St. John's (N.Y.)"), "NY")
        self.assertIsNone(N.statedState("Notre Dame (Club)"))
        self.assertNotEqual(N.nameKey("Ohio St. University"), N.nameKey("Saint Ohio"))

    def test_level_key(self):
        self.assertEqual(N.levelKey("Williams College"), N.levelKey("Williams"))
        self.assertEqual(N.levelKey("University of Michigan"), "michigan")
        self.assertEqual(N.levelKey("La Jolla High School"), "la jolla")

    def _judge(self, rosters, states, levels, **kw):
        pairs = M.candidatesAll(rosters.keys(), level_of=levels, **kw)
        return M.judge(pairs, rosters, states, level_of=levels)

    def test_the_nescac_three(self):
        rosters = {"Conn College": set(range(1, 30)), "Connecticut College": set(range(5, 60)),
                   "Trinity": set(range(100, 900)), "Trinity (Conn.)": set(range(100, 140)),
                   "Wesleyan": set(range(1000, 1400)), "Wesleyan (Conn.)": set(range(1000, 1040))}
        states = {"Trinity": {"CT", "TX", "KY"}, "Wesleyan": {"CT", "GA"},
                  "Connecticut College": {"CT"}}
        levels = {k: "college" for k in rosters}
        merged, refused = self._judge(rosters, states, levels)
        got = {(m[0], m[1]) for m in merged}
        self.assertIn(("Conn College", "Connecticut College"), got)
        self.assertIn(("Trinity", "Trinity (Conn.)"), got)
        self.assertIn(("Wesleyan", "Wesleyan (Conn.)"), got)
        grouped = dict((c, v) for c, v in M.groups(merged, rosters))
        self.assertIn("Connecticut College", grouped)          # the most used

    def test_a_level_pair_needs_the_same_level(self):
        rosters = {"Adrian": set(range(1, 60)), "Adrian College": set(range(1, 60)),
                   "Norwell": set(range(1, 300)), "Norwell Middle School": set(range(1, 300))}
        states = {"Adrian": {"MI"}, "Adrian College": {"MI"},
                  "Norwell": {"MA"}, "Norwell Middle School": {"MA"}}
        levels = {"Adrian": "hs", "Adrian College": "college",
                  "Norwell": "hs", "Norwell Middle School": "ms"}
        merged, refused = self._judge(rosters, states, levels)
        self.assertEqual(merged, [])
        self.assertTrue(all("different levels" in r[7] for r in refused))

    def test_a_school_level_pair_needs_one_known_state(self):
        rosters = {"La Jolla": set(range(1, 60)), "La Jolla High School": set(range(1, 40))}
        levels = {"La Jolla": "hs", "La Jolla High School": "hs"}
        merged, _r = self._judge(rosters, {"La Jolla": {"CA"}, "La Jolla High School": {"CA"}}, levels)
        self.assertEqual(len(merged), 1)
        # one side's state unknown: only on overwhelming evidence (20+, half)
        merged, _r = self._judge(rosters, {"La Jolla": {"CA"}}, levels)
        self.assertEqual(len(merged), 1)
        thin = {"La Jolla": set(range(1, 60)), "La Jolla High School": set(range(50, 90))}
        merged, refused = self._judge(thin, {"La Jolla": {"CA"}}, levels)
        self.assertEqual(merged, [])
        self.assertIn("one known state", refused[0][7])

    def test_the_directory_pairs_colleges_it_places_together(self):
        rosters = {"UW-La Crosse": set(range(1, 50)), "Wisconsin-La Crosse": set(range(10, 80))}
        levels = {k: "college" for k in rosters}
        key = {k: ("University of Wisconsin-La Crosse", "WI") for k in rosters}
        merged, _r = self._judge(rosters, {}, levels, college_key=key)
        self.assertEqual([m[2] for m in merged], ["college"])

    def test_a_wide_high_school_name_still_refuses(self):
        rosters = {"Glendale": set(range(1, 500)), "Glendale (CA)": set(range(1, 200))}
        states = {"Glendale": {"CA", "AZ"}, "Glendale (CA)": {"CA"}}
        merged, _r = self._judge(rosters, states, {k: "hs" for k in rosters})
        self.assertEqual(merged, [])


class SecondDryRun(unittest.TestCase):
    """2026-11-01, the owner's dry run: Concordia (Wis.) chained into
    Concordia (Neb.), Anderson (S.C.) into Anderson (Ind.), Arkansas State
    into Arkansas, Antelope Valley College into the University of Antelope
    Valley -- and Trinity (Conn.), Wesleyan (Conn.), St Johns refused."""

    def _run(self, rosters, states, levels, mix=None, key=None):
        pairs = M.candidatesAll(rosters.keys(), level_of=levels, college_key=key or {})
        merged, refused = M.judge(pairs, rosters, states, level_of=levels, level_mix=mix)
        return merged, refused, M.groups(merged, rosters, states, mix)

    def test_two_named_states_never_merge(self):
        r = {"Anderson (Ind.)": set(range(1, 400)), "Anderson (S.C.)": set(range(1, 300))}
        merged, refused, _g = self._run(r, {}, {k: "college" for k in r})
        self.assertEqual(merged, [])
        self.assertIn("different states", refused[0][7])

    def test_no_chain_through_a_shared_generic_name(self):
        r = {"Concordia (Neb.)": set(range(1, 300)), "Concordia (Wis.)": set(range(500, 1000)),
             "Concordia University": set(range(1, 100)) | set(range(500, 600))}
        lv = {k: "college" for k in r}
        _m, _r, g = self._run(r, {}, lv)
        for canon, variants in g:
            names = {canon} | {v[0] for v in variants}
            self.assertFalse({"Concordia (Neb.)", "Concordia (Wis.)"} <= names, g)

    def test_state_is_another_university_unless_the_runners_say_one(self):
        r = {"Arkansas": set(range(1, 800)), "Arkansas State": set(range(795, 1555))}
        lv = {k: "college" for k in r}
        key = {k: ("University of Arkansas", "AR") for k in r}
        merged, refused, _g = self._run(r, {}, lv, key=key)
        self.assertEqual(merged, [])
        # third look: "Cortland St." / "SUNY Cortland" share 801 of 849
        r = {"Cortland St.": set(range(1, 850)), "SUNY Cortland": set(range(49, 1470))}
        key = {k: ("SUNY Cortland", "NY") for k in r}
        merged, refused, _g = self._run(r, {}, {k: "college" for k in r}, key=key)
        self.assertEqual(len(merged), 1, refused)

    def test_a_name_several_colleges_share_needs_nearly_all(self):
        """Concordia University: 78 of its 148 runners are Nebraska's."""
        r = {"Concordia (Neb.)": set(range(1, 300)),
             "Concordia University": set(range(222, 370))}
        lv = {k: "college" for k in r}
        pairs = M.candidatesAll(r.keys(), level_of=lv)
        merged, refused = M.judge(pairs, r, {}, level_of=lv, generic={"Concordia University"})
        self.assertEqual(merged, [])
        self.assertIn("several schools", refused[0][7])
        # and without the directory's word for it: unnamed into named
        merged, refused = M.judge(pairs, r, {}, level_of=lv)
        self.assertEqual(merged, [])

    def test_a_named_college_picks_a_school_name_that_holds_college_runners(self):
        """Wesleyan: one Georgia high school on athletic.net, plus the CT
        university's runners from tfrrs."""
        r = {"Wesleyan": set(range(1, 1760)), "Wesleyan (Conn.)": set(range(1, 714))}
        st = {"Wesleyan": {"GA"}}
        lv = {"Wesleyan": "hs", "Wesleyan (Conn.)": "college"}
        mix = {"Wesleyan": {"hs": 0.7, "college": 0.3}, "Wesleyan (Conn.)": {"college": 1.0}}
        merged, refused, g = self._run(r, st, lv, mix)
        self.assertEqual(len(merged), 1, refused)
        self.assertEqual(g[0][0], "Wesleyan")

    def test_a_few_middle_school_rows_do_not_make_the_canonical(self):
        r = {"Abingdon": set(range(1, 1036)), "Abingdon High School": set(range(1, 30))}
        st = {"Abingdon": {"VA"}, "Abingdon High School": {"VA"}}
        lv = {k: "hs" for k in r}
        mix = {"Abingdon": {"hs": 1.0}, "Abingdon High School": {"hs": 0.9, "ms": 0.1}}
        _m, _r, g = self._run(r, st, lv, mix)
        self.assertEqual(g[0][0], "Abingdon")

    def test_college_and_university_are_two_schools(self):
        r = {"Antelope Valley College": set(range(1, 400)),
             "University of Antelope Valley": set(range(1, 300))}
        merged, refused, _g = self._run(r, {}, {k: "college" for k in r})
        self.assertEqual(merged, [])

    def test_a_named_college_folds_into_a_wide_mixed_name(self):
        r = {"Trinity": set(range(1, 4000)), "Trinity (Conn.)": set(range(1, 700))}
        st = {"Trinity": {"TX", "KY", "IL"}}
        lv = {"Trinity": "hs", "Trinity (Conn.)": "college"}
        mix = {"Trinity": {"hs": 0.8, "college": 0.2}, "Trinity (Conn.)": {"college": 1.0}}
        merged, refused, g = self._run(r, st, lv, mix)
        self.assertEqual(len(merged), 1, refused)
        self.assertEqual(g[0][0], "Trinity")              # the wide one is the canonical

    def test_decoration_merges_across_ms_and_hs_as_before(self):
        r = {"St Johns": set(range(1, 2400)), "St. Johns": set(range(1, 1200))}
        st = {"St Johns": {"FL"}, "St. Johns": {"FL"}}
        merged, refused, _g = self._run(r, st, {"St Johns": "ms", "St. Johns": "hs"})
        self.assertEqual(len(merged), 1, refused)

    def test_a_school_named_for_a_college(self):
        r = {"Boston College": set(range(1, 2800)), "Boston College High": set(range(1, 560))}
        st = {"Boston College": {"MA"}}
        merged, refused, _g = self._run(r, st, {k: "hs" for k in r})
        self.assertEqual(merged, [])


class FourthLook(unittest.TestCase):
    def test_unnamed_into_named_needs_nearly_all(self):
        r = {"Concordia (Wis.)": set(range(1, 400)), "Concordia": set(range(295, 495))}
        lv = {k: "college" for k in r}
        merged, refused = M.judge(M.candidatesAll(r.keys(), level_of=lv), r, {}, level_of=lv)
        self.assertEqual(merged, [])
        r = {"Alice Lloyd (Ky.)": set(range(1, 300)), "Alice Lloyd": set(range(1, 126)) | {9999}}
        lv = {k: "college" for k in r}
        merged, refused = M.judge(M.candidatesAll(r.keys(), level_of=lv), r, {}, level_of=lv)
        self.assertEqual(len(merged), 1, refused)

    def test_a_stray_college_row_does_not_make_the_canonical(self):
        r = {"Ammons MS": set(range(1, 204)), "Ammons Middle School": set(range(1, 5))}
        st = {k: {"FL"} for k in r}
        mix = {"Ammons MS": {"ms": 1.0}, "Ammons Middle School": {"ms": 0.9, "college": 0.1}}
        lv = {k: "ms" for k in r}
        merged, _r = M.judge(M.candidatesAll(r.keys(), level_of=lv), r, st, level_of=lv, level_mix=mix)
        g = M.groups(merged, r, st, mix)
        self.assertEqual(g[0][0], "Ammons MS")


class FifthLook(unittest.TestCase):
    """2026-11-01, the owner's full dry run."""

    def _m(self, r, lv, st=None, mix=None, key=None):
        p = M.candidatesAll(r.keys(), level_of=lv, college_key=key or {})
        return M.judge(p, r, st or {}, level_of=lv, level_mix=mix)[0]

    def test_names_that_say_two_levels(self):
        r = {"Pearl MS": set(range(1, 92)), "Pearl High School": set(range(31, 91))}
        self.assertEqual(self._m(r, {k: "ms" for k in r}, {k: {"MS"} for k in r}), [])

    def test_two_campuses(self):
        r = {"St. Joseph's (Long Island)": set(range(1, 231)), "St. Joseph's (Bklyn)": set(range(223, 290))}
        key = {k: ("St. Joseph's University", "NY") for k in r}
        self.assertEqual(self._m(r, {k: "college" for k in r}, key=key), [])

    def test_a_state_and_its_namesake_college(self):
        r = {"Louisiana": set(range(1, 19)), "Louisiana College": set(range(1, 118))}
        key = {k: ("x", "LA") for k in r}
        self.assertEqual(self._m(r, {k: "college" for k in r}, key=key), [])

    def test_a_named_college_mostly_spelled_otherwise_stays(self):
        r = {"St. Joseph's": set(range(1, 31)), "St. Joseph's (Pa.)": set(range(4, 557))}
        lv = {"St. Joseph's": "hs", "St. Joseph's (Pa.)": "college"}
        mix = {"St. Joseph's": {"hs": 0.8, "college": 0.2}, "St. Joseph's (Pa.)": {"college": 1.0}}
        self.assertEqual(self._m(r, lv, {"St. Joseph's": {"PA", "IN", "NY"}}, mix), [])
