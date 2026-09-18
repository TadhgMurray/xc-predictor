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
                    "_family", "_isRosterish", "sharedShas"):
                exec(ast.get_source_segment(src, node), ns)
            if isinstance(node, ast.Assign) and getattr(
                    node.targets[0], "id", "") in ("SHARED_MIN",
                                                   "_ROSTER_PREFIXES"):
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


# ⚠⚠ A SCRIPT THAT PRINTS NOTHING FOR MINUTES LOOKS BROKEN (owner,
#    2026-09-18: "this script is taking forever not printing any output").
#    anet_teams builds its queue by grouping results UNION results_tf -- tens
#    of millions of rows -- and every progress print was on the far side of
#    that. Two fixes: say so first, and with --missing do not group the whole
#    corpus to then throw almost all of it away.
class TheQueueBuildIsNotSilent(unittest.TestCase):
    def _src(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "anet_teams.py"),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_it_says_so_before_the_slow_query_not_after(self):
        src = self._src()
        self.assertIn("building the queue", src)
        self.assertLess(src.index("building the queue"),
                        src.index("todo = (unfetchedTeams("))
        # and reports how long it took, so "forever" becomes a number
        self.assertIn("queue built in", src)

    def test_missing_pushes_the_school_filter_into_the_grouping(self):
        src = self._src()
        self.assertIn("want = \"\"", src)
        self.assertIn("if missing and _tableExists(cur, \"school_logo\"):", src)
        # both legs of the UNION, or the cheap side still scans everything
        self.assertEqual(src.count("{want}"), 2)

    # ! IT MUST NOT CHANGE THE ANSWER: the same three conditions loadCrests
    #   serves on, so the schools kept are exactly those --missing wants.
    def test_the_filter_uses_the_same_conditions_as_the_gap_test(self):
        src = self._src()
        i = src.index("want = \"\"")
        block = src[i:src.index("where_state =", i)]
        for cond in ("g2.path IS NOT NULL", "g2.status = 'ok'",
                     "NOT g2.shared OR g2.override IS NOT NULL",
                     "si2.n_athletes >= 3"):
            self.assertIn(cond, block)


# ⚠⚠ THE FAMILY FIX SUPPRESSED PENN STATE AGAIN (owner, 2026-09-18: the PA
#    row came back shared = True). _family takes the first two words, so each
#    unattached spelling invented a family: "unat penn", "una penn", "u penn",
#    "penn state" -- four, and SHARED_MIN is four. A roster status means "no
#    team" and cannot be evidence that a picture is a district placeholder.
class ARosterStatusIsNotAnInstitution(unittest.TestCase):
    def _fns(self):
        import io
        import os
        import re as _re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        ns = {"re": _re}
        i = src.index("_ROSTER_PREFIXES =")
        exec(src[i:src.index("def sharedShas")], ns)     # noqa: S102
        return ns["_family"], ns["_isRosterish"]

    def test_the_unattached_spellings_add_no_family(self):
        family, rosterish = self._fns()
        names = ["Penn State", "Penn State Behrend", "UNAT-Penn State",
                 "Unat-Penn State", "UNA-Penn State", "U-Penn State Altoona",
                 "Penn State-Berks"]
        fams = {family(n) for n in names if not rosterish(n)}
        self.assertEqual(fams, {"penn state"})

    # ! AND A SCHOOL WHOSE NAME MERELY STARTS WITH THOSE LETTERS IS A SCHOOL.
    #   "Una" (AL) and "Unalakleet" (AK) are real, and so is every
    #   "University ..." -- the marker has to be a WHOLE first word.
    def test_a_real_school_is_not_mistaken_for_a_roster_status(self):
        _family, rosterish = self._fns()
        for name in ("Una", "Unalakleet", "Union", "University High",
                     "Universal Academy", "Unatego"):
            self.assertFalse(rosterish(name), name)

    def test_both_counters_skip_them(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        # sharedShas (the sweep) and sharedAlready (the write-time guard) must
        # agree, or one refuses a crest the other would not have flagged.
        for fn in ("def sharedShas(", "def sharedAlready("):
            body = src[src.index(fn):]
            body = body[:body.index("\ndef ", 10)]
            self.assertIn("_isRosterish", body, fn)


# ★ ONE SCHOOL, AND A WAY TO MAKE IT RE-FETCH (owner, 2026-09-18: fixing a
#   single Oregon row queued 10,996 teams at 3.1 hours, and the DELETE it
#   needed first could not be run because psql had no usable role).
class OneSchoolCanBeTargetedAndForgotten(unittest.TestCase):
    def _src(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "anet_teams.py"),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_the_school_filter_reaches_the_query(self):
        src = self._src()
        self.assertIn('ap.add_argument("--school"', src)
        self.assertIn('where_school = "AND si.school = %(school)s"', src)
        self.assertIn("{where_school}", src)
        self.assertIn("school=args.school", src)

    # ⚠⚠ FORGET MUST HAPPEN BEFORE ANY FETCH, or --missing skips the pair (it
    #    still has a crest) and the keep-better guards refuse to overwrite --
    #    which is exactly how a refetch looked like it ran and changed nothing.
    def test_forget_runs_before_the_first_image_request(self):
        src = self._src()
        self.assertLess(src.index("if args.forget and args.write and todo:"),
                        src.index("img, ctype = manners.get(url)"))

    # ! AN OVERRIDE IS A DECISION. --forget must not discard one.
    def test_forget_never_deletes_an_override(self):
        src = self._src()
        i = src.index("if args.forget and args.write and todo:")
        self.assertIn("override IS NULL", src[i:i + 700])

    def test_forget_needs_write(self):
        self.assertIn("--forget needs --write", self._src())


# ⚠⚠⚠ anet CRESTS ARE NEVER SUPPRESSED BY SHARING -- the third and last answer
#    to "Penn State has no logo" (owner, 2026-09-18, reported five times). Two
#    earlier rules counted the wrong thing and failed identically:
#
#      count NAMES    -> 4 (Penn State, PSU-Abington, PSU-Berks, PSU-Harrisburg)
#      count FAMILIES -> still 4; no word rule knows PSU is Penn State
#      count TEAMS    -> still 4; those campuses ARE four anet teams and anet
#                        serves them all the same Nittany Lions image
#
#    The premise was wrong, not the threshold. An anet mascot_url is anet's
#    answer FOR THAT TEAM ID, per team by construction: if anet hands team
#    21255 that picture it IS its crest, however many siblings share it.
#    `shared` is for crests scraped from WEBSITES, where one district site
#    serves one logo to schools it belongs to none of.
class AnetCrestsAreNeverSuppressedBySharing(unittest.TestCase):
    def _src(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "scrape_school_logos.py"),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_the_sweep_only_counts_non_anet_rows(self):
        src = self._src()
        body = src[src.index("def markShared("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("COALESCE(kind, '') <> 'anet'", body)
        # and it only FLAGS non-anet rows: one picture can be both a
        # district's placeholder and some team's real mascot
        self.assertEqual(body.count("COALESCE(kind, '') <> 'anet'"), 2)

    # ! THE WRITE-TIME GUARD MUST AGREE WITH THE SWEEP, or it refuses a crest
    #   the sweep would then not have flagged.
    def test_the_write_guard_exempts_anet_too(self):
        src = self._src()
        body = src[src.index("def sharedAlready("):]
        body = body[:body.index("\ndef ")]
        self.assertIn('if kind == "anet":', body)
        self.assertIn("return False", body)

    def test_the_anet_writer_passes_its_kind(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "scripts", "anet_teams.py"),
                     encoding="utf-8") as fh:
            self.assertIn('sharedAlready(cur, sha, kind="anet")', fh.read())

    # ★ FAMILIES STILL DECIDE THE WEBSITE CRESTS. The district-placeholder
    #   problem is real; it is just not anet's.
    def test_families_still_apply_to_website_crests(self):
        src = self._src()
        body = src[src.index("def sharedShas("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("_family(school)", body)
        self.assertIn("_isRosterish(school)", body)

    # ! AND A BAD anet IMAGE IS AN OVERRIDE, not a threshold.
    def test_the_escape_hatch_is_documented(self):
        self.assertIn("override = 'none'", self._src())


if __name__ == "__main__":
    unittest.main(verbosity=2)
