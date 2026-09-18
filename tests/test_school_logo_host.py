# Project: xc-predictor / tests
# File:    test_school_logo_host.py
# Purpose: plausibleHost -- which addresses are allowed to give a crest.
#
#   python -m pytest -q tests/test_school_logo_host.py
#   python tests/test_school_logo_host.py
#
# ★ THE TWO FAILURES THIS SITS BETWEEN, and they pull opposite ways.
#
#   2026-09-17, the owner: "it was giving random ass pictures that are not on
#   anet. Lawrence hs got the world athletics picture?" -- a wrong
#   school_website row returns that domain's logo, faithfully. plausibleHost
#   was written to refuse those.
#
#   2026-09-17, the owner again: "Penn state still has no logo at all." The
#   same test threw away most of the COLLEGE corpus, because a college
#   athletics site is branded by its MASCOT and not by its school:
#   gopsusports.com, goducks.com, rolltide.com, guhoyas.com, gohuskies.com,
#   hawkeyesports.com, und.com, cuse.com, byucougars.com. Not one carries a
#   name token, none is .edu, none reads as "schooly".
#
# ★ WHAT SEPARATES THEM IS THE ADDRESS'S PROVENANCE, NOT ITS HOSTNAME. The
#   Lawrence row was a GUESS (Wikidata, or a search). Penn State's row was
#   written by anet_teams.storeAddress from anet's WebsiteSport, keyed on the
#   TEAM ID -- the same construction that already exempts the anet mascot
#   itself. So the name test applies to guesses and the blocklist applies to
#   everything.
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scrape_school_logos as S                                    # noqa: E402


# the real athletics domains of real programmes in the corpus
COLLEGES = [
    ("Penn State", "https://gopsusports.com/"),
    ("Oregon", "https://goducks.com/"),
    ("Alabama", "https://rolltide.com/"),
    ("Notre Dame", "https://und.com/"),
    ("Syracuse", "https://cuse.com/"),
    ("Washington", "https://gohuskies.com/"),
    ("Georgetown", "https://guhoyas.com/"),
    ("Iowa", "https://hawkeyesports.com/"),
    ("BYU", "https://byucougars.com/"),
]


class Guessed(unittest.TestCase):
    """An address nobody vouched for still has to earn the crest."""

    def test_a_governing_body_is_never_a_school(self):
        self.assertFalse(
            S.plausibleHost("Lawrence", "https://worldathletics.org/logo.png",
                            "school"))

    def test_an_aggregator_is_never_a_school(self):
        for host in ("https://www.milesplit.com/", "https://maxpreps.com/",
                     "https://www.tfrrs.org/"):
            self.assertFalse(S.plausibleHost("Lawrence", host, "school"), host)

    def test_the_school_s_own_name_earns_it(self):
        self.assertTrue(
            S.plausibleHost("Stanford", "https://gostanford.com/", "school"))

    def test_a_dot_edu_earns_it(self):
        self.assertTrue(S.plausibleHost("Penn State", "https://www.psu.edu/",
                                        "school"))

    def test_an_anet_crest_is_exempt_whatever_the_host(self):
        self.assertTrue(
            S.plausibleHost("Anything", "https://lh3.googleusercontent.com/x",
                            "anet"))


class DistrictCodes(unittest.TestCase):
    """⚠ "sd" WAS A BARE SUBSTRING of a run-together hostname, so the guard
    the owner asked for admitted newsdaily.com, wisdomtree.com,
    sportsdesk.net and kidsdirect.org. A district code is a WORD."""

    def test_a_two_letter_code_inside_a_longer_word_is_not_a_district(self):
        for url in ("https://newsdaily.com/", "https://wisdomtree.com/",
                    "https://sportsdesk.net/", "https://kidsdirect.org/",
                    "https://goldsmiths.com/"):
            self.assertFalse(S.plausibleHost("Lawrence", url, "school"), url)

    def test_the_district_hosts_that_really_exist_still_pass(self):
        for url in ("https://usd497.org/", "https://sd44.bc.ca/",
                    "https://www.cusd.org/", "https://north-isd.net/",
                    "https://www.ccsd.net/"):
            self.assertTrue(S.plausibleHost("Anytown", url, "school"), url)

    def test_the_long_words_are_still_substrings(self):
        for url in ("https://mydistrict.org/", "https://someacademy.com/",
                    "https://lawrenceschools.org/"):
            self.assertTrue(S.plausibleHost("Anytown", url, "school"), url)


class Provenance(unittest.TestCase):
    """The Penn State half: an address anet resolved by team id."""

    def test_every_one_of_these_is_refused_as_a_guess(self):
        """The measurement. Without provenance the name test refuses most of
        the college corpus -- which is the bug, stated as a number."""
        refused = [(sc, url) for sc, url in COLLEGES
                   if not S.plausibleHost(sc, url, "school")]
        self.assertEqual(len(refused), len(COLLEGES),
                         f"these now pass the NAME test on their own: {refused}")

    def test_and_every_one_is_allowed_when_anet_gave_the_address(self):
        for sc, url in COLLEGES:
            self.assertTrue(S.plausibleHost(sc, url, "school", source="anet"),
                            f"{sc} {url}")

    def test_a_hand_set_address_is_trusted_too(self):
        self.assertTrue(S.plausibleHost("Penn State", "https://gopsusports.com/",
                                        "school", source="manual"))

    def test_provenance_does_not_defeat_the_blocklist(self):
        """anet's WebsiteSport pointing at a vendor or a governing body is
        still nobody's crest -- trust the ADDRESS, not the host."""
        for url in ("https://worldathletics.org/", "https://www.wordpress.com/",
                    "https://x.com/pennstate", "https://www.milesplit.com/"):
            self.assertFalse(
                S.plausibleHost("Penn State", url, "school", source="anet"), url)

    def test_an_unknown_source_is_still_a_guess(self):
        for source in (None, "", "wikidata", "search", "directory"):
            self.assertFalse(
                S.plausibleHost("Penn State", "https://gopsusports.com/",
                                "school", source=source), repr(source))


def _scraperSource():
    with io.open(os.path.join(_ROOT, "scripts", "scrape_school_logos.py"),
                 encoding="utf-8") as fh:
        return fh.read()


class Wiring(unittest.TestCase):
    """The provenance has to actually reach the check."""

    def test_the_queue_selects_the_source(self):
        src = _scraperSource()
        self.assertIn("l.status, w.source", src)
        self.assertIn('"etag", "modified", "status", "source")', src)

    def test_the_worker_passes_it_and_tolerates_a_short_row(self):
        src = _scraperSource()
        self.assertIn("source = row[9] if len(row) > 9 else None", src)
        self.assertIn("school=(None if override_url else school),\n"
                      "                                    source=source)", src)

    def test_fetchLogo_hands_it_to_the_check(self):
        src = _scraperSource()
        self.assertIn("plausibleHost(school, url, kind, source)", src)


# ★ THE WRONG-LOGO SUBSET (owner, 2026-09-18): "it's taken some logos not
#   actually from the anet site (or it's taken them from a similar named
#   school) ... is there some way to only scrape these subsets so I don't have
#   to keep hammering anet?"
#
#   The answer is in the rows already. `kind` records where a crest came from:
#   'anet*' means the team page for a resolved team id -- the school itself --
#   and everything else came off a WEBSITE picked by matching a name against a
#   domain, which is how a similarly-named neighbour hands over its logo. The
#   team id being right is no protection, because it was never consulted for
#   those.
class TheSuspectSelector(unittest.TestCase):

    @staticmethod
    def _body():
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def suspectPairs(")
        return src[i:src.index("\ndef targets(", i)]

    def test_it_keeps_anet_crests(self):
        body = self._body()
        self.assertIn('kind.split(":")[0] == "anet"', body)
        self.assertIn("continue", body)

    def test_it_reads_only_stored_rows(self):
        """No network: a suspect sweep must not cost a request to PLAN.

        ! NAMED CALLS, NOT THE WORD "fetch" -- cur.fetchall() is a database
          read and an earlier version of this test failed on it.
        """
        body = self._body()
        self.assertIn("FROM   school_logo", body)
        for call in ("requests.", "urlopen", "Manners(", "workOne(",
                     "httpGet", "session."):
            with self.subTest(call=call):
                self.assertNotIn(call, body)

    def test_it_also_flags_a_host_that_no_longer_passes(self):
        body = self._body()
        self.assertIn("plausibleHost(", body)

    # ! DEDUPED ACROSS LEVELS. school_logo is keyed (school, state, level) but
    #   targets() matches on (school, state), so a school with a crest per
    #   level would be queued several times.
    def test_pairs_are_deduped(self):
        body = self._body()
        self.assertIn("seen", body)
        self.assertIn("if pair not in seen:", body)

    def test_the_flag_implies_redo(self):
        """Without it the refresh window skips the rows being re-asked."""
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('ap.add_argument("--suspect"', src)
        i = src.index("if args.suspect:")
        self.assertIn("args.redo = True", src[i:i + 300])

    def test_the_worst_are_queued_first(self):
        """The implausible ones lead, so --limit covers them first."""
        body = self._body()
        self.assertIn("for pair in implausible + off_anet:", body)


# ★★ THE REAL WRONG-LOGO BUG (owner, 2026-09-18): "the issue isn't that we're
#    not getting it from anet but that we're getting the wrong one from anet
#    (this applies to wake forest and oregon)".
#
#    scrape_school_logos.py's KIND_RANK comment asserts an anet crest "cannot
#    be the wrong school's, it is fetched BY TEAM ID". That is true only if the
#    team id belongs to this school -- and anet_teams.teams() chose it by
#    ATHLETE MODALITY, which is an inference, while anet_team.anet_state has
#    held anet's own answer all along.
class TheCrestTeamMustBeThisSchoolsTeam(unittest.TestCase):

    @staticmethod
    def _query():
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "anet_teams.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def teams(")
        return src[i:src.index("\ndef parseTeam(", i)]

    def test_anets_own_state_decides_first(self):
        q = self._query()
        self.assertIn("anet_keyed", q)
        self.assertIn("at.anet_state", q)
        # and it outranks the athlete-modal guess
        self.assertIn("COALESCE(ak.team_id, modal.team_id, link.team_id)", q)

    def test_the_team_is_matched_to_the_clusters_state(self):
        q = self._query()
        self.assertIn("ak.state = si.state", q)

    # ⚠ NO BADGE BEATS ANOTHER SCHOOL'S BADGE. Where anet states the team's
    #   state and it disagrees with the cluster, the row must be refused.
    def test_a_team_anet_places_elsewhere_is_refused(self):
        q = self._query()
        self.assertIn("upper(btrim(a.anet_state)) = si.state", q)

    # ! AN UNKNOWN anet_state STILL PASSES. Most of the corpus has one, and
    #   refusing the blanks would strip crests that are right.
    def test_an_unknown_state_is_not_refused(self):
        q = self._query()
        self.assertIn("COALESCE(btrim(a.anet_state), '') = ''", q)

    def test_the_modal_pick_survives_as_a_fallback(self):
        """A name anet places nowhere still gets its athlete-modal team."""
        q = self._query()
        self.assertIn("modal.team_id", q)
        self.assertIn("link.team_id", q)


class ThereIsAWayToAskWhyACrestIsMissing(unittest.TestCase):
    """"Penn State still doesn't have a logo" has been reported three times and
    answered with three different guesses at which gate excluded it."""

    def test_the_diagnostic_exists(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertTrue(os.path.exists(
            os.path.join(root, "scripts", "diag_crest.py")))

    def test_it_covers_every_gate(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "diag_crest.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        for gate in ("school_identity", "anet_team", "school_team_link",
                     "school_logo", "n_athletes >= 3", "shared"):
            with self.subTest(gate=gate):
                self.assertIn(gate, src)


# ★★ WHY PENN STATE HAD NO LOGO, found by diag_crest rather than by a fifth
#    guess (owner, 2026-09-18). Two separate faults, both in the STORE rather
#    than the fetch -- its crest was correct the whole time.
class OneUniversitysCampusesAreNotFourSchools(unittest.TestCase):
    """anet serves the same Nittany Lions image for Penn State and its
    campuses, so SHARED_MIN counted four distinct NAMES, called the crest a
    district placeholder, and the site drew nothing."""

    def setUp(self):
        import re as _re
        import io
        import os
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        ns = {"re": _re}
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and node.name in (
                    "_family", "sharedShas"):
                exec(ast.get_source_segment(src, node), ns)
            if isinstance(node, ast.Assign) and getattr(
                    node.targets[0], "id", "") == "SHARED_MIN":
                exec(ast.get_source_segment(src, node), ns)
        self.family = ns["_family"]
        self.sharedShas = ns["sharedShas"]

    def test_campuses_collapse_to_one_family(self):
        fams = {self.family(n) for n in (
            "Penn State", "Penn State Berks", "Penn State Shenango",
            "Penn State-Behrend", "Penn State Abington")}
        self.assertEqual(len(fams), 1)

    def test_one_universitys_crest_is_not_a_placeholder(self):
        rows = [(n, "sha1") for n in (
            "Penn State", "Penn State Berks", "Penn State Shenango",
            "Penn State Abington", "Penn State Altoona")]
        self.assertNotIn("sha1", self.sharedShas(rows))

    # ! AND THE RULE THE SWEEP EXISTS FOR IS UNTOUCHED.
    def test_a_governing_body_logo_still_flags(self):
        rows = [(n, "sha2") for n in (
            "Acme High", "Brookside", "Cedar Valley", "Dunmore Area",
            "Easton")]
        self.assertIn("sha2", self.sharedShas(rows))

    # ! CONSERVATIVE ON PURPOSE: a district placeholder across a town's
    #   schools must behave exactly as before, so two words, not a prefix.
    def test_a_towns_schools_are_still_separate_families(self):
        self.assertNotEqual(self.family("Lincoln High"),
                            self.family("Lincoln Middle"))

    def test_the_install_check_counts_the_same_way(self):
        """sharedAlready refusing on NAMES while the sweep counts FAMILIES
        would refuse a crest the sweep would not have flagged."""
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def sharedAlready(")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertIn("_family(", body)
        self.assertNotIn("count(DISTINCT school)", body)


# ⚠⚠ THE PRUNE IS GONE AND MUST NOT COME BACK ON THAT THEORY (2026-09-18).
#    --prune-stale deleted school_logo rows whose (school, state) was not a
#    school_identity cluster. Two dry runs said 12,268 then 9,923 rows,
#    nearly all live club crests. crestState falls back to the CALLER'S own
#    state for any name the identity cannot place -- every club -- so a row
#    under a non-cluster state is the row that answers, not a leftover.
#    Penn State's real cause was the `shared` flag, fixed in _family.
class ThePruneStaysRemoved(unittest.TestCase):
    def _src(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_no_prune_flag_and_no_stale_query(self):
        src = self._src()
        self.assertNotIn('add_argument("--prune-stale"', src)
        self.assertNotIn("def staleRows(", src)
        self.assertNotIn("def pruneStale(", src)

    def test_the_reason_is_written_down_where_the_code_was(self):
        src = self._src()
        self.assertIn("THERE WAS A --prune-stale HERE", src)
        self.assertIn("Do not rebuild it on this theory.", src)

    # ★ THE READ PATH IS THE EVIDENCE, so pin the line the argument rests on.
    def test_the_caller_state_floor_still_exists(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "racecast", "school_logo.py"),
                     encoding="utf-8") as fh:
            self.assertIn('st = (st or state or "").upper()', fh.read())


# ★ TWO HALVES, AND ONLY ONE OF THEM LANDED FIRST (owner, 2026-09-18:
#   "oregon and wake forest still wrong"). anet_keyed stopped the queue
#   handing a pair another state's team; the rows an earlier run already
#   wrote there were never removed, and the queue now refuses to re-file
#   them, so the wrong crest survived every rerun ("0 crests, 2 missed").
class AMisplacedCrestCanBeUnfiled(unittest.TestCase):
    def _src(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "anet_teams.py"),
                     encoding="utf-8") as fh:
            return fh.read()

    def _sql(self):
        """The query text only. ⚠ NOT the docstring: asserting on prose is
        how a test about a JOIN failed on the word "NOT EXISTS" in a comment
        explaining why the JOIN replaced one."""
        src = self._src()
        body = src[src.index("def misplacedCrests(cur):"):]
        body = body[:body.index("\ndef unfileMisplaced")]
        return body[body.index("cur.execute(f"):]

    def test_it_exists_and_is_wired(self):
        src = self._src()
        self.assertIn("def misplacedCrests(cur):", src)
        self.assertIn("def unfileMisplaced(cur, rows):", src)
        self.assertIn('ap.add_argument("--unfile-misplaced"', src)
        self.assertIn("if args.unfile_misplaced:", src)

    # ★ THE PROOF IS anet'S OWN RECORD, which is what makes deleting safe
    #   here and unsafe in the --prune-stale tombstone: school_logo.
    #   source_url IS anet_team.mascot_url, so the row names its own team.
    # ⚠⚠ THE TWO URLS ARE NOT THE SAME STRING, and a plain equality join
    #    returns a clean, wrong "0 stored crests" (owner, 2026-09-18).
    #    anet_team.mascot_url is protocol-relative; school_logo.source_url
    #    is what mascotUrls() fetched -- scheme added, "=s512" appended.
    def test_it_matches_the_row_to_the_team_by_the_image_it_fetched(self):
        src = self._src()
        body = self._sql()
        self.assertIn("_URL_KEY.format(c='t.mascot_url')", body)
        self.assertIn("_URL_KEY.format(c='l.source_url')", body)
        self.assertNotIn("t.mascot_url = l.source_url", body)
        # the state comparison, now expressed as the group's own question
        self.assertIn("bool_or(tk.st = lk.state)", body)
        self.assertIn("WHERE  NOT j.owned_here", body)

    def test_the_normaliser_undoes_both_differences(self):
        src = self._src()
        i = src.index("_URL_KEY = (")
        tpl = src[i:src.index("\n\n", i)]
        self.assertIn("'^//', 'https://'", tpl)
        self.assertIn("'=s[0-9]+$', ''", tpl)

    # ★ A ZERO MUST BE TELLABLE FROM A BROKEN JOIN. That is what made the
    #   first version look like good news.
    def test_it_reports_join_reach_and_refuses_a_total_miss(self):
        src = self._src()
        i = src.index("if args.unfile_misplaced:")
        block = src[i:src.index("season = args.season", i)]
        self.assertIn("bad, (have, matched) = misplacedCrests(cur)", block)
        self.assertIn("if have and matched is None and not bad:", block)
        self.assertIn("the join is broken", block)

    # ⚠⚠ AND NORMALISING BOTH SIDES KILLS EVERY INDEX (owner: "15 mins
    #    nothing printed"). A correlated NOT EXISTS over anet_team with a
    #    regex on both columns re-scans every team per crest row. The keys
    #    are built once and the question is a GROUP BY.
    def test_the_join_is_one_pass_not_a_subquery_per_row(self):
        src = self._src()
        body = self._sql()
        self.assertIn("MATERIALIZED", body)
        self.assertIn("JOIN tk ON tk.k = lk.k", body)
        self.assertNotIn("NOT EXISTS", body)

    # ⚠ ONE PICTURE CAN BE SEVERAL TEAMS'. If any team wearing it is one
    #   anet puts in this state, the row is right.
    def test_a_shared_image_owned_here_too_is_left_alone(self):
        src = self._src()
        body = self._sql()
        self.assertIn("bool_or(tk.st = lk.state)", body)
        self.assertIn("WHERE  NOT j.owned_here", body)

    def test_it_never_touches_an_override_or_a_stateless_row(self):
        src = self._src()
        body = self._sql()
        self.assertIn("l.override IS NULL", body)
        self.assertIn("COALESCE(btrim(l.state), '') <> ''", body)

    # ⚠⚠ A STATE MISMATCH ALONE IS NOT THE BUG (owner, 2026-09-18: 9,299
    #    rows, almost all clubs). 3DElite is filed under CA, FL, KS and NC
    #    and anet has exactly ONE 3DElite team, in OK -- that crest is
    #    right, because a club races in four states and is registered in
    #    one. Placement is identity for a SCHOOL, not for a club. What makes
    #    Oregon (WI) different is that anet has ANOTHER "Oregon" team in WI.
    def test_it_requires_anet_to_have_a_team_of_that_name_in_that_state(self):
        body = self._sql()
        self.assertIn("named AS MATERIALIZED", body)
        self.assertIn("n.namekey = j.namekey AND n.st = j.state", body)
        self.assertIn("EXISTS (SELECT 1 FROM named n", body)

    def test_it_needs_write_to_delete(self):
        src = self._src()
        i = src.index("if args.unfile_misplaced:")
        block = src[i:src.index("season = args.season", i)]
        self.assertIn("DRY RUN", block)
        self.assertIn("if args.write and bad:", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
