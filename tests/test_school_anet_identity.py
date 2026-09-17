# Project: xc-predictor / tests
# File:    test_school_anet_identity.py
# Purpose: two schools wearing one name are separated by the states NAMED
#          schools actually sit in -- anet's teams and the college
#          directory -- not by where their athletes happen to race.
#          (owner, 2026-09-16: "Oregon(IL) and Oregon(or) are colliding
#          despite hs vs college ... Williams (CA) vs (MA)".) No database.
#
#   python -m pytest -q tests/test_school_anet_identity.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_school_identity as bsi                            # noqa: E402

_SRC = open(os.path.join(_ROOT, "racecast", "build_school_identity.py")).read()

# what the first run measured, and what the fix has to change
_CONTESTED = {"oregon": {"IL", "OR"}, "williams": {"CA", "MA"}}


def test_a_clusters_state_is_authoritative_only_when_a_named_school_sits_there():
    assert bsi.authStatesOf(_CONTESTED, "Oregon", "IL") == {"IL"}
    assert bsi.authStatesOf(_CONTESTED, " oregon ", "or") == {"OR"}
    assert bsi.authStatesOf(_CONTESTED, "Oregon", "TX") == set()   # a travel state
    assert bsi.authStatesOf(_CONTESTED, "Kingston", "WA") == set() # not contested
    assert bsi.authStatesOf({}, "Oregon", "IL") == set()
    assert bsi.authStatesOf(_CONTESTED, None, "IL") == set()


def test_the_guard_is_on_the_group_because_the_merge_is_transitive():
    """⚠ THE BUG IN THE FIRST VERSION. Refusing the IL-OR pair does not keep
    IL and OR apart: IL merges with CA, CA merges with OR, and all 36 of
    Oregon's home states end in one group with the pair never tested. 737
    refusals fired and Oregon still came out as a single cluster."""
    assert bsi.wouldMergeTwoSchools({"IL"}, {"OR"})
    # a travel state may join a named one, and travel states may join freely
    assert not bsi.wouldMergeTwoSchools({"IL"}, set())
    assert not bsi.wouldMergeTwoSchools(set(), set())
    # ...and once a group HOLDS a named state it carries it: that is what
    # makes the guard transitive
    assert bsi.wouldMergeTwoSchools({"IL"} | set(), {"OR"})


def test_the_merge_carries_each_groups_named_states_and_asks_before_uniting():
    body = _SRC[_SRC.index("def mergeCoRacingClusters("):
                _SRC.index("# ★ THE DIRECTORY OUTRANKS THE DATA FOR A COLLEGE")]
    # the question is asked of find(a)/find(b), not of (s1, s2)
    assert "wouldMergeTwoSchools(authOf(a), authOf(b))" in body
    # and the union is recorded, or the guard would forget on the next pair
    assert "auth[a] = authOf(a) | authOf(b)" in body
    assert body.index("a, b = find(") < body.index("wouldMergeTwoSchools")


def test_a_group_holding_a_named_school_resolves_to_that_state():
    """Without this the group holding OR resolves by row counts, and a
    college's rows are mostly away -- which is how "Oregon (CA)" and
    "Furman (FL)" happened (school_identity.teamState's docstring)."""
    body = _SRC[_SRC.index("    rows, alias = [], []"):]
    assert "if len(named) == 1:" in body
    assert body.index("named = {st for st in states") < body.index("host = hosted.get(sc)")
    assert body.index("if len(named) == 1:") < body.index("elif host and host[1] >= 2")


def test_the_assignment_is_written_down_so_the_page_and_the_counts_agree():
    """⚠ THE HALF THE FIRST ATTEMPT MISSED. The clusters were COUNTED from
    the COALESCE, but the site re-derived membership from person_home_state
    alone (stateFilterSql), so /school/Oregon?state=OR listed the athletes
    who RACE in Oregon, not the ones who run for the university."""
    src = open(os.path.join(_ROOT, "racecast", "school_identity.py")).read()
    assert "def stateFilterSql(alias, state, primary, school=None):" in src
    body = src[src.index('    if not state:\n        return "", {}'):
               src.index("def homeStates(")]
    assert body.index("school_athlete_state") < body.index("person_home_state")
    assert '_LABELS.get("athlete_state")' in body      # absent table: old clause
    assert '_LABELS["athlete_state"] = _tableExists(cur, "school_athlete_state")' in src
    # the four school-page queries pass the school, or the clause cannot fire
    school_py = open(os.path.join(_ROOT, "racecast", "school.py")).read()
    assert school_py.count("stateFilterSql(") == 4          # all four queries
    assert "stateFilterSql(\"s\", state, primary, school)" in school_py
    assert "stateFilterSql(\"rr\", state, primary, school)" in school_py
    # and it is built from si_assign AFTER the merge and the directory folded
    assert "def buildAthleteState(cur, contested):" in _SRC
    assert "LEFT   JOIN school_state_alias_new al" in _SRC
    assert _SRC.index("buildSchoolLevel(cur)\n        conn.commit()\n\n        buildAthleteState")
    assert "\"school_athlete_state\"):" in _SRC            # swapped with the rest


def test_the_directory_places_a_college_season_because_anet_cannot():
    """★ WHY THE FIRST RUN CHANGED NOTHING: every hs-vs-college collision is
    one anet school and one tfrrs school, tfrrs XC rows carry no anet team
    id, and `results` has no team_slug -- so the half being placed was the
    half that was already right."""
    assert "LEFT   JOIN si_dir_state ds ON ds.school = v.school" in _SRC
    assert "CASE WHEN v.college THEN ds.state END" in _SRC
    # anet's own team first, the directory second, the inference last
    start = _SRC.index("            CREATE TEMP TABLE si_assign AS")
    cte = _SRC[start:_SRC.index("CREATE TABLE school_identity_new AS")]
    assert cte.index("ts.state") < cte.index("ds.state") < cte.index("ph.state")
    # and the college-season flag comes from the pool, without a LIKE pattern
    assert "starts_with(rr.pool, 'college')" in _SRC


def test_contested_names_only_so_every_other_name_is_unchanged():
    body = _SRC[_SRC.index("def buildDirStates("):_SRC.index("# ⚠ AND THE PAIRWISE")]
    assert "if key in contested and key in dir_states:" in body
    team = _SRC[_SRC.index("def buildTeamStates("):_SRC.index("# ⚠ AND THE PAIRWISE")]
    assert "lower(btrim(r.school)) = ANY(%s)" in team and "r.team_id <> 0" in team


def test_the_anet_states_come_from_the_rows_not_from_anets_spelling():
    """⚠ OWNER, 2026-09-16, with a link to athletic.net team 21570: "Like idk
    why you think williams isn't on anet". The list grouped anet_team by
    lower(btrim(school)) and matched that against the FEED's school string,
    so a team anet calls "Williams College" never joined rows that say
    "Williams" -- and the college half of the collision went missing over a
    spelling, in the middle of a fix about keying on names. team_id is the
    join that needs no spelling."""
    body = _SRC[_SRC.index("def authoritativeStates("):_SRC.index("def _schoolNames(")]
    assert "JOIN   anet_team t ON t.team_id = r.team_id" in body
    assert "lower(btrim(r.school)) AS name" in body
    # the old shape must not come back
    assert "FROM   anet_team\n            WHERE  school IS NOT NULL" not in body
    # both feeds, and never the unattached sentinel
    assert 'for table in ("results", "results_tf")' in body
    assert "r.team_id <> 0" in body


def test_it_all_degrades_without_either_source():
    body = _SRC[_SRC.index("def authoritativeStates("):_SRC.index("def _schoolNames(")]
    assert "to_regclass('anet_team')" in body
    assert "except Exception as exc" in body          # no directory: keep going
    assert "if len(v) >= 2" in body


def test_the_build_orders_them_before_the_clusters():
    i_auth = _SRC.index("        contested, dir_states = authoritativeStates(cur)")
    i_team = _SRC.index("        buildTeamStates(cur, contested)")
    i_dir = _SRC.index("        buildDirStates(cur, contested, dir_states)")
    i_clusters = _SRC.index("CREATE TABLE school_identity_new AS")
    i_merge = _SRC.index("        mergeCoRacingClusters(cur, contested)")
    assert i_auth < i_team < i_dir < i_clusters < i_merge


def test_the_crest_queue_uses_the_same_assignment():
    """★ THE LOGO (owner, 2026-09-16: "the logos are still the old logo (for
    oregon) ... Williams worked perfectly, they just have no logo anymore").
    school_logo is keyed on school_identity's (school, state) pairs, and
    anet_teams.teams decides which anet TEAM answers for each pair. It read
    person_home_state alone -- where the athlete RACES -- so the modal team
    for (Oregon, OR) was decided by whoever happens to race in Oregon."""
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    body = src[src.index("def _stateSource("):src.index("# ★ THE TEAMS OUR OWN ROWS")]
    assert 'LEFT JOIN school_athlete_state sa' in body
    assert body.index("school_athlete_state") < body.index("person_home_state")
    # and both are optional, so an older database still builds a queue
    assert body.count('_tableExists(cur, "school_athlete_state")') == 1
    assert body.count('_tableExists(cur, "person_home_state")') == 1
    # ...and teams() reads it rather than carrying its own copy
    queue = src[src.index("def teams(cur"):src.index("def parseTeam(")]
    assert "home, state_expr = _stateSource(cur)" in queue


def test_the_college_level_is_the_majority_not_any_race():
    """★ OWNER, 2026-09-16: "Oregon (OR) contains hsers still". bool_or meant
    ANY college-pooled season under the name placed the athlete at the
    college -- and the pooling is the thing still being fixed, so one
    mis-pooled race moved an Illinois high schooler onto the university's
    roster."""
    assert "bool_or(starts_with" not in _SRC
    body = _SRC[_SRC.index("            WITH levels AS ("):
                _SRC.index("CREATE TABLE school_identity_new AS")]
    assert "sum(rr.n_races) AS n" in body
    assert "DISTINCT ON (school, person_id)" in body
    assert "ORDER BY school, person_id, n DESC, college DESC" in body


def test_an_athlete_with_no_racing_state_is_still_placed_by_their_team():
    """They had no si_assign row at all, so the site's COALESCE fell through
    to the PRIMARY cluster -- which for a contested name is now sometimes
    the college."""
    body = _SRC[_SRC.index("            CREATE TEMP TABLE si_assign AS"):
                _SRC.index("CREATE TABLE school_identity_new AS")]
    assert "LEFT   JOIN person_home_state_new ph USING (person_id)" in body
    assert "IS NOT NULL" in body.split("WHERE")[-1]


def test_a_split_name_does_not_cost_a_full_rescrape():
    """★ OWNER, 2026-09-16: "so that anet_teams script is gonna take 38 hrs.
    Do we have to rerun the entire thing?" No. Manners paces one request per
    second PER HOST, so --redo's two or three API calls against
    www.athletic.net for 40,927 teams is a day and a half. What changed is
    the (school, state) pairs, and mascot_url is already stored."""
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    body = src[src.index("def teams(cur"):src.index("def parseTeam(")]
    # the queue carries the stored URL and can ask for only the gaps
    assert "a.mascot_url" in body and "{gap}" in body
    assert "NOT EXISTS (SELECT 1 FROM school_logo g" in body
    # the loop can then skip anet entirely
    loop = src[src.index("            for i, (school, state, team_id, stored_url)"):
               src.index("                    team = mergeTeams(*parts)")]
    assert "if args.logos_only:" in loop
    assert loop.index("if args.logos_only:") < loop.index("manners.get(")
    assert '"MascotUrl": stored_url' in loop
    # ...and must not claim to have refreshed metadata it never asked for
    assert "if args.write and not args.logos_only:\n                        storeTeam" in src
    assert "redo=args.redo or args.logos_only" in src


def test_the_teams_our_rows_name_can_be_fetched_at_all():
    """★ MEASURED, 2026-09-16: anet team 21570 is Williams College, it is NOT
    in anet_team, and results_tf names it 16,434 times with results another
    4,356. teams() asks for the modal team of a (school, state) pair that
    ALREADY EXISTS in school_identity, so a cluster that did not exist until
    the rebuild split the name was never on any list -- no level, no state,
    no mascot, which is what kept the college half invisible."""
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    body = src[src.index("def unfetchedTeams("):src.index("def teams(cur")]
    assert "LEFT   JOIN anet_team a ON a.team_id = b.team_id" in body
    assert "WHERE  a.team_id IS NULL" in body
    assert "ORDER  BY total.n DESC" in body            # biggest first
    assert "t.team_id <> 0" in body                    # never the sentinel
    # the crest key is the team's own modal (school, state)
    assert "DISTINCT ON (team_id) team_id, school, state" in body
    # and both queues ask one source for that state
    assert src.count("def _stateSource(") == 1
    assert src.count("_stateSource(cur)") == 2
    assert "unfetchedTeams(cur, args.limit, args.min_rows)" in src


def test_a_crest_is_never_filed_under_an_empty_state():
    """⚠ FROM THE FIRST --unfetched RUN: "Exeter ()", "Eastlake ()". A
    school_logo row with no state is the fallback crestState serves for ANY
    mention of the name, so filing one MAKES a name-wide badge -- the exact
    failure the split exists to fix. The metadata is still fetched."""
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    guard = 'if png and not (state or "").strip():'
    assert guard in src
    # before the write, not after
    assert src.index(guard) < src.index("name = writeFile(school, state, png")
    assert "stateless += 1" in src and "had no state to file one under" in src


def test_the_queue_can_be_sized_without_fetching_anything():
    """--dry-run still makes its requests (that is what it is for). Sizing a
    job must not: the first --unfetched run fetched 5 teams just to find out
    how many there were."""
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    assert '"--queue-only"' in src
    assert src.index("if args.queue_only:") < src.index("manners = Manners(rate=args.rate)")
    assert "args.queue_only" in src[src.index("if not (args.write"):
                                    src.index("season = args.season")]


def test_a_tfrrs_school_links_to_an_anet_team_by_its_athletes():
    """★ THE OWNER'S PLAN (2026-09-16): "combine with tfrrs using our already
    combined athletes that contain both schools with races from tfrrs and
    anet ... any remaining tfrrs schools just put as their own schools"."""
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    import link_tfrrs_to_anet as L
    teams = {21570: ("Williams College", "MA"), 685: ("Williams", "CA"),
             999: ("Thin", "NY"), 1: ("A", "AL"), 2: ("B", "AK")}
    counted = {
        # the case: 40 athletes agree, and one stray high-school vote loses
        "Williams College": {21570: [40, 6], 685: [1, 1]},
        "Thin College": {999: [3, 1]},            # a witness, not a majority
        "Split College": {1: [10, 2], 2: [9, 2]},  # no clear winner
    }
    links, rejected = L.decide(counted, teams)
    assert [(r[0], r[1]) for r in links] == [("Williams College", 21570)]
    assert {r[0] for r in rejected} == {"Thin College", "Split College"}
    # the bars are what does it, and they are arguable from the outside
    assert L.MIN_ATHLETES >= 3 and 0.5 < L.MIN_SHARE <= 1.0
    tight, _r = L.decide(counted, teams, min_athletes=100)
    assert tight == []


def test_only_anet_college_teams_are_candidates():
    """⚠ THE TRAP: a person's HIGH SCHOOL anet rows and their COLLEGE tfrrs
    rows share a calendar year -- spring track, then autumn cross country --
    so a shared person and year alone would marry a high school to a
    college."""
    src = open(os.path.join(_ROOT, "scripts", "link_tfrrs_to_anet.py")).read()
    body = src[src.index("def collegeTeams("):src.index("_SQL = ")]
    assert 'if lv == "college"' in body and "loadTeamLevels" in body
    # the votes query can only see those teams
    assert "r.team_id = ANY(%(teams)s)" in src
    assert "r.source = 'tfrrs'" in src
    assert "anet.yr = tf.yr" in src


def test_a_state_belongs_to_the_school_not_to_its_athletes():
    """★ OWNER, 2026-09-16: "The state of a school comes from the anet gps.
    Otherwise it comes from most raced state, and only the most raced state.
    It should be a school still, and it should be stable for athletes across
    races." Clustering a name by its ATHLETES' home states made identity a
    property of whoever raced -- MIT's away meets gave it a CT cluster and
    the scoring split then scored it as another team."""
    assert "def buildNameStates(cur, contested):" in _SRC
    body = _SRC[_SRC.index("def buildNameStates("):_SRC.index("def buildDirStates(")]
    # ONE state per name, by races, deterministic on a tie
    assert "row_number() OVER (PARTITION BY rr.school" in body
    assert "ORDER BY sum(rr.n_races) DESC," in body and "rr.state) AS rk" in body
    assert "WHERE  rk = 1" in body
    assert "lower(btrim(rr.school)) = ANY(%s)" in body          # contested only
    # and the assignment prefers it over the athlete's home state
    cte = _SRC[_SRC.index("            CREATE TEMP TABLE si_assign AS"):
               _SRC.index("CREATE TABLE school_identity_new AS")]
    assert cte.index("ns.state") < cte.index("ph.state")
    assert "LEFT   JOIN si_name_state ns ON ns.school = v.school" in cte
    assert _SRC.index("buildNameStates(cur, contested)\n") > _SRC.index("buildDirStates(cur, contested, dir_states)\n")


def test_anet_never_replaces_a_crest_on_a_two_institution_pair():
    """★ OWNER, 2026-09-16: "Amherst college changed from actual to the
    falcons logo". The queue picks the MODAL team of a (school, state), and
    Amherst Regional High School has far more rows than Amherst College --
    both (Amherst, MA) -- so the high school won and, because "ANET WINS" is
    the default, its mascot replaced the college's real athletics-site
    crest."""
    with open(os.path.join(_ROOT, "scripts", "anet_teams.py")) as fh:
        src = fh.read()
    assert "multi_level = set()" in src
    assert "FROM school_level" in src and "HAVING count(*) >= 2" in src
    assert "contested_key = not lv and (school, state) in multi_level" in src
    assert "keep = args.keep_better or contested_key" in src
    # it may still FILL an empty key -- that coin flip is already taken
    body = src[src.index("keep = args.keep_better"):]
    assert "kindRank(\"anet\") > kindRank(" in body[:200]


def test_the_two_institution_guard_survives_replace():
    """⚠ AND --replace SKIPPED THE WHOLE BLOCK, so --keep-better beside it was
    silently a no-op -- which is what the handoff's own recommended command
    passed (`--redo --replace --keep-better`, 2026-09-17). The pair guard is
    not a preference, it is arithmetic: with an UNKNOWN level the crest key is
    (school, state) alone and one key holds one crest, so replacing a
    better-ranked one there is a loss, not a swap. With a known level the two
    institutions are separate rows and anet wins as intended."""
    with open(os.path.join(_ROOT, "scripts", "anet_teams.py")) as fh:
        src = fh.read()
    assert ("if png and args.write and (not args.replace\n"
            "                                               or contested_key):"
            in src)
    # the placeholder rule IS a preference, and --replace does override it
    assert "elif not args.replace and sharedAlready(cur, sha):" in src


def test_replace_and_keep_better_cannot_be_passed_together():
    """They are contradictory instructions. Refused, rather than one of them
    being dropped without a word."""
    with open(os.path.join(_ROOT, "scripts", "anet_teams.py")) as fh:
        src = fh.read()
    assert "if args.replace and args.keep_better:" in src
    assert "contradict each other" in src


# ===================================================================== #
#  THE tfrrs LINK -- the leg that was built and never read               #
# ===================================================================== #
#
# ⚠ THE HOLE, STATED EXACTLY (owner, 2026-09-17: "Oregon(or) ... hasn't been
#   separated according to team id", and "tfrrs/anet not linked (could this
#   be an issue with 1)" -- it is, they are the same issue).
#
#   authoritativeStates learned a name's states two ways, and BOTH abstain on
#   precisely the schools this file exists for:
#
#     * anet's leg reads the states of the anet teams THE ROWS USE, via
#       results.team_id. A tfrrs XC row carries no anet team id, so the
#       University of Oregon contributed nothing and "oregon" came back {IL}.
#     * the college directory's lookup() answers None rather than guess when
#       two states share a name -- which is the definition of contested.
#
#   {IL} is ONE state, so `len(v) >= 2` was false and "oregon" was not a
#   contested name at all. si_team_state, si_dir_state and si_name_state are
#   every one of them built for contested names ONLY, so not one of them ran,
#   and the clustering fell all the way back to athlete home states: one
#   Oregon, IL, share 1.0000, with the university folded into it.
#
# ★ school_team_link CLOSES IT. scripts/link_tfrrs_to_anet.py has built that
#   table since 2026-09-16 and nothing read it (handoff §3.3, "still
#   unwired"). It is the owner's own plan: combine by team id, carried across
#   the feeds by the athletes who appear in both.

def test_the_link_feeds_the_contested_list():
    """Without it the name is never even contested, and every other leg in
    this file is dead code for exactly the schools it was written for."""
    assert "SELECT to_regclass('school_team_link')" in _SRC
    assert "lower(btrim(tfrrs_school)), upper(btrim(state))" in _SRC
    assert "by_name.setdefault(name, set()).add(st)" in _SRC


def test_the_link_is_its_own_assignment_leg():
    assert "def buildLinkStates(cur, contested):" in _SRC
    assert "buildLinkStates(cur, contested)" in _SRC
    assert "LEFT   JOIN si_link_state ls ON ls.school = v.school" in _SRC


def test_the_link_outranks_the_directory_and_loses_to_the_athlete_s_own_team():
    """★ ts FIRST, THEN ls, THEN ds. The link is a claim about the STRING,
    and the string "Oregon" is worn by both Oregons -- so an athlete with
    their own anet team id is placed by it and never reaches the link."""
    i = _SRC.index("SELECT v.school, v.person_id,")
    order = _SRC[i:i + 400]
    assert order.index("ts.state") < order.index("ls.state") < order.index("ds.state")
    assert order.index("ls.state") < order.index("ns.state")
    assert order.index("ns.state") < order.index("ph.state")


def test_the_link_leg_is_gated_on_a_college_season():
    """The second belt: school_team_link only ever names COLLEGE teams
    (link_tfrrs_to_anet refuses a high school candidate outright), so a high
    schooler wearing the same string must not be placed by it."""
    assert "CASE WHEN v.college THEN ls.state END" in _SRC


def test_an_absent_link_table_is_todays_behaviour():
    """It is optional: a database that has never run link_tfrrs_to_anet must
    build exactly the clusters it built before."""
    i = _SRC.index("def buildLinkStates(")
    body = _SRC[i:_SRC.index("\ndef buildTeamStates", i)]
    assert "if cur.fetchone()[0] is None:" in body
    assert "return 0" in body


def test_the_pipeline_builds_the_link_before_it_reads_it():
    with open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")) as fh:
        sh = fh.read()
    assert sh.index("10b0_tfrrs_link") < sh.index("10b_school_ids")
    assert "scripts/link_tfrrs_to_anet.py --write" in sh
