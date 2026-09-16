"""
build_school_identity.py -- who every athlete and school string IS.

    python racecast/build_school_identity.py

Pipeline step 10b, right after 10_rankings: everything here reads
athlete_season (the boards' own per-season table). Two tables out:

    person_home_state (person_id PK, state)
        an athlete's HOME state = the state they race in most.
        `state` on a row is the venue's, and kids race mostly at home,
        so the mode is the home state -- one Arcadia trip cannot move
        a Utah kid.

    school_identity   (school, state, n_athletes, share, is_primary)
        each school STRING's athletes clustered by home state. One
        cluster = a normal school; two real clusters = two schools
        wearing one string ("Highland" UT and CA), which is what the
        state chips, the meet-scoring split and the search index read.

Swap discipline: build into _new, rename over the old -- readers keep
the previous tables until the new ones are whole.
"""

import sys
import time

import psycopg2.errors           # LockNotAvailable / DeadlockDetected

sys.path.insert(0, "scripts")

from database import getConn

# ! THE SAME PATIENCE build_ranking_results AND backfill_normalize USE. Three
#   seconds is long enough for a normal page and short enough that a stuck
#   one does not hold the swap; twenty attempts at fifteen seconds is five
#   minutes of trying, which outlasts any single request.
_SWAP_LOCK_TIMEOUT = "3s"
_SWAP_ATTEMPTS = 20
_SWAP_BACKOFF = 15


# ★ ONE SCHOOL WEARING SEVERAL HOME STATES (owner, 2026-09-06). A home
#   state is where an athlete races most, and a college team races away
#   most weekends: BYU's athletes came out CA, UT and CO by their own
#   modes, and the clustering made three schools of one, each with its
#   own page and the biggest -- CA -- as the label. The tell is that the
#   three "schools" ran the same races on the same days. So: two clusters
#   of one name whose athletes appear in the same races are one school,
#   merged; and the merged school's state is where it HOSTS (the meets
#   named after it), else the state most of its rows were run in. Two
#   Kingstons, WA and MO, never share a race and stay two schools.
#   school_state_alias (school, home_state, state) says which resolved
#   state each original cluster went to, for readers keyed on an
#   athlete's home state.
MERGE_MIN_SHARED = 3          # races two clusters ran together
MERGE_MIN_FRACTION = 0.20     # ...as a share of the smaller cluster's races


# ★ ANET KNOWS WHERE ITS SCHOOLS ARE, AND WE WERE GUESSING (owner,
#   2026-09-16: "we need to use the school locations from anet and the
#   school ids/names to separate schools (where id != 0) and separate them
#   by location otherwise ... currently Oregon(IL) and Oregon(or) are
#   colliding despite hs vs college, and this happens to Williams (CA) vs
#   (MA)").
#
# ⚠ THE HEADER OF school_identity.py SAYS "THE DATA HAS NO SCHOOL IDS".
#   That stopped being true when scripts/anet_teams.py landed: anet_team
#   carries a team_id per school with its own level, state, city and zip,
#   and `results.team_id` puts every anet row on one of them. The home-state
#   inference is still the only answer for tfrrs and for anet rows with no
#   team, but it must not outvote an id.
#
#   And the inference cannot separate these cases even in principle:
#     * an athlete has ONE home state, so a kid who ran high school in CA
#       and then Williams College in MA is one state for both names;
#     * a college's home state is a travel mode (Air Force came out OK,
#       Oregon CA, Furman FL -- see school_identity.teamState);
#     * two schools of one name in one state never separate at all.
#
# ! CONTESTED NAMES ONLY. A name anet places in a single state has nothing
#   to disambiguate, and the scan below is a filtered one for that reason:
#   with every name it would be a full pass over results for no gain.
def anetContestedStates(cur):
    """{normalised school name: {state: n teams}} for names anet gives
    teams in MORE THAN ONE state. Empty without anet_team."""
    cur.execute("SELECT to_regclass('anet_team')")
    if cur.fetchone()[0] is None:
        print("  school_identity: no anet_team -- school states stay inferred",
              flush=True)
        return {}
    cur.execute("""
        SELECT lower(btrim(school)) AS name,
               upper(btrim(COALESCE(state, anet_state))) AS st,
               count(*)
        FROM   anet_team
        WHERE  school IS NOT NULL AND btrim(school) <> ''
          AND  COALESCE(state, anet_state) IS NOT NULL
          AND  btrim(COALESCE(state, anet_state)) <> ''
        GROUP  BY 1, 2
    """)
    by_name = {}
    for name, st, n in cur.fetchall():
        by_name.setdefault(name, {})[st] = int(n)
    contested = {k: v for k, v in by_name.items() if len(v) >= 2}
    print(f"  school_identity: anet places {len(by_name):,} school names; "
          f"{len(contested):,} of them in more than one state", flush=True)
    return contested


def buildTeamStates(cur, contested):
    """si_team_state(person_id, school, state): where the athlete's OWN
    anet team for that school string is, for contested names. The modal
    state across both sports' rows, so one stray row cannot move it.

    ⚠ ONE FILTERED PASS OVER results AND results_tf. Everything else in
      this step reads athlete_season for a reason (owner, 2026-09-15: the
      gateway timeout), but team_id exists only on the raw tables and the
      name filter is what keeps it cheap.
    """
    cur.execute("DROP TABLE IF EXISTS si_team_state")
    cur.execute("CREATE TEMP TABLE si_team_state "
                "(person_id bigint, school text, state text)")
    if not contested:
        return 0
    names = sorted(contested)
    cur.execute("DROP TABLE IF EXISTS si_team_raw")
    cur.execute("CREATE TEMP TABLE si_team_raw "
                "(person_id bigint, school text, state text, n bigint)")
    for table in ("results", "results_tf"):
        cur.execute(f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
              AND column_name = 'team_id'
        """, (table,))
        if cur.fetchone() is None:
            continue
        cur.execute(f"""
            INSERT INTO si_team_raw (person_id, school, state, n)
            SELECT r.person_id, r.school,
                   upper(btrim(COALESCE(t.state, t.anet_state))), count(*)
            FROM   {table} r
            JOIN   anet_team t ON t.team_id = r.team_id
            WHERE  r.person_id IS NOT NULL
              AND  r.team_id IS NOT NULL AND r.team_id <> 0
              AND  r.school IS NOT NULL
              AND  lower(btrim(r.school)) = ANY(%s)
              AND  COALESCE(t.state, t.anet_state) IS NOT NULL
              AND  btrim(COALESCE(t.state, t.anet_state)) <> ''
            GROUP  BY 1, 2, 3
        """, (names,))
    cur.execute("""
        INSERT INTO si_team_state (person_id, school, state)
        SELECT person_id, school, state FROM (
            SELECT person_id, school, state,
                   row_number() OVER (PARTITION BY person_id, school
                                      ORDER BY sum(n) DESC, state) AS rk
            FROM   si_team_raw GROUP BY 1, 2, 3) x
        WHERE  rk = 1
    """)
    cur.execute("CREATE INDEX si_team_state_idx ON si_team_state (person_id, school)")
    cur.execute("SELECT count(*) FROM si_team_state")
    n = cur.fetchone()[0]
    print(f"  school_identity: {n:,} (athlete, school) pairs placed by anet's "
          f"own team id rather than by where the athlete races", flush=True)
    return n


# ! PURE, SO IT CAN BE TESTED WITHOUT A DATABASE. Two clusters of one name
#   that anet places in two different states are two SCHOOLS, and the
#   co-racing merge must not join them however many meets they share --
#   Oregon (IL) and Oregon (OR) are the case, and a shared meet_id between
#   the feeds or a transfer's rows is enough to start a merge that then
#   spreads by union-find.
def anetSaysTwoSchools(contested, school, state_a, state_b):
    """True when anet names teams for `school` in BOTH states."""
    states = contested.get((school or "").strip().lower())
    if not states:
        return False
    return (state_a or "").upper() in states and (state_b or "").upper() in states


def mergeCoRacingClusters(cur, contested=None):
    from school_identity import MIN_ATHLETES, MIN_SHARE
    t0 = time.time()
    cur.execute("""
        SELECT school FROM school_identity_new
        WHERE  n_athletes >= %s AND share >= %s
        GROUP  BY school HAVING count(*) >= 2
    """, (MIN_ATHLETES, MIN_SHARE))
    schools = [r[0] for r in cur.fetchall()]
    cur.execute("DROP TABLE IF EXISTS school_state_alias_new")
    cur.execute("""
        CREATE TABLE school_state_alias_new (
            school text NOT NULL, home_state text NOT NULL, state text NOT NULL,
            PRIMARY KEY (school, home_state))
    """)
    if not schools:
        return
    # the races each cluster appeared in, and the races two clusters shared
    cur.execute("DROP TABLE IF EXISTS si_races")
    cur.execute("""
        CREATE TEMP TABLE si_races AS
        SELECT DISTINCT rr.school, ph.state, rr.sport, rr.meet_id, rr.race_date
        FROM   ranking_results rr
        JOIN   person_home_state_new ph USING (person_id)
        WHERE  rr.school = ANY(%s) AND rr.meet_id IS NOT NULL
    """, (schools,))
    cur.execute("SELECT school, state, count(*) FROM si_races GROUP BY 1, 2")
    n_races = {(sc, st): n for sc, st, n in cur.fetchall()}
    cur.execute("""
        SELECT a.school, a.state, b.state, count(*)
        FROM   si_races a
        JOIN   si_races b ON b.school = a.school AND b.sport = a.sport
                         AND b.meet_id = a.meet_id AND b.race_date = a.race_date
                         AND b.state > a.state
        GROUP  BY 1, 2, 3
    """)
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    refused = 0
    for sc, s1, s2, shared in cur.fetchall():
        small = min(n_races.get((sc, s1), 0), n_races.get((sc, s2), 0))
        # ★ ANET'S WORD BEATS A SHARED MEET (see anetSaysTwoSchools)
        if anetSaysTwoSchools(contested or {}, sc, s1, s2):
            refused += 1
            continue
        if shared >= MERGE_MIN_SHARED and shared >= MERGE_MIN_FRACTION * small:
            a, b = find((sc, s1)), find((sc, s2))
            if a != b:
                parent[b] = a
    groups = {}
    for key in list(parent) + [k for k in n_races if k not in parent]:
        root = find(key)
        if root != key or key in parent:
            groups.setdefault(root, set()).add(key)
    groups = {r: m | {r} for r, m in groups.items() if len(m | {r}) >= 2}
    if refused:
        print(f"  school_identity: {refused:,} co-racing merges refused -- anet "
              f"names teams for the name in both states", flush=True)
    if not groups:
        print("  school_identity: no co-racing clusters to merge", flush=True)
        return
    merged_schools = sorted({r[0] for r in groups})
    # where a merged school hosts: the state of meets named after it
    # the school's name as a whole word inside the meet name (ARE word
    # boundaries), the name's regex metacharacters escaped
    escape_sql = "regexp_replace(s.school, '([().*+?\\[\\]\\\\^$|])', '\\\\\\1', 'g')"
    # ! rr.school = ANY(...) so idx_rr_school answers it; the unnest join
    #   it was planned as a full scan of the boards table (2026-09-15)
    cur.execute(f"""
        SELECT s.school, rr.state, count(DISTINCT rr.meet_id)
        FROM   ranking_results rr
        JOIN   (SELECT unnest(%s::text[]) AS school) s ON s.school = rr.school
        LEFT   JOIN meets m ON m.div_id = rr.div_id AND rr.sport = 'XC'
        LEFT   JOIN meets_tfrrs mt ON mt.meet_id = rr.meet_id AND rr.sport = 'XC'
        WHERE  rr.school = ANY(%s)
          AND  rr.state IS NOT NULL
          AND  COALESCE(m.meet_name, mt.meet_name) ~* ('{chr(92)}m' || {escape_sql} || '{chr(92)}M')
        GROUP  BY 1, 2
    """, (merged_schools, merged_schools))
    hosted = {}
    for sc, st, n in cur.fetchall():
        if n > hosted.get(sc, (None, 0))[1]:
            hosted[sc] = (st, n)
    # else where most of its rows were run
    cur.execute("""
        SELECT school, state, sum(n_races) FROM athlete_season
        WHERE  school = ANY(%s) AND state IS NOT NULL GROUP BY 1, 2
    """, (merged_schools,))
    row_state = {}
    for sc, st, n in cur.fetchall():
        row_state.setdefault(sc, {})[st] = n
    cur.execute("SELECT school, state, n_athletes FROM school_identity_new "
                "WHERE school = ANY(%s)", (merged_schools,))
    n_ath = {(sc, st): n for sc, st, n in cur.fetchall()}

    rows, alias = [], []
    for root, members in groups.items():
        sc = root[0]
        states = {st for _sc, st in members}
        host = hosted.get(sc)
        if host and host[1] >= 2 and host[0] in states:
            state = host[0]
        else:
            counts = {st: row_state.get(sc, {}).get(st, 0) for st in states}
            state = max(counts, key=lambda st: (counts[st], n_ath.get((sc, st), 0)))
        total = sum(n_ath.get(m, 0) for m in members)
        rows.append((sc, state, total))
        alias.extend((sc, st, state) for st in states)
    # rewrite the merged schools' rows: one per group, the others as they were
    cur.execute("DROP TABLE IF EXISTS si_merged")
    cur.execute("CREATE TEMP TABLE si_merged (school text, state text, n_athletes int)")
    cur.executemany("INSERT INTO si_merged VALUES (%s, %s, %s)", rows)
    cur.executemany("INSERT INTO school_state_alias_new VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING", alias)
    cur.execute("""
        DELETE FROM school_identity_new si
        USING  school_state_alias_new a
        WHERE  a.school = si.school AND a.home_state = si.state
    """)
    cur.execute("""
        INSERT INTO school_identity_new (school, state, n_athletes, share, is_primary)
        SELECT school, state, n_athletes, 0, false FROM si_merged
    """)
    cur.execute("""
        UPDATE school_identity_new si SET
            share = round(si.n_athletes::numeric / t.total, 4),
            is_primary = (si.n_athletes = t.top AND si.state = t.top_state)
        FROM (SELECT school, sum(n_athletes) AS total, max(n_athletes) AS top,
                     (array_agg(state ORDER BY n_athletes DESC, state))[1] AS top_state
              FROM school_identity_new WHERE school = ANY(%s) GROUP BY school) t
        WHERE t.school = si.school
    """, (merged_schools,))
    print(f"  school_identity: {len(groups):,} co-racing groups merged over "
          f"{len(merged_schools):,} names ({len(alias):,} home states folded; "
          f"{sum(1 for g in groups if hosted.get(g[0], (None, 0))[1] >= 2):,} "
          f"placed by the meets they host) in {time.time() - t0:.0f}s", flush=True)


# ★ THE DIRECTORY OUTRANKS THE DATA FOR A COLLEGE (owner, 2026-09-06: "add
#   directory"). college_directory (scripts/build_college_directory.py,
#   NCAA D1-D3 and NAIA from Wikipedia, ~2,000 names) says where a college
#   IS: the pass PLACES the college's cluster in the directory's state.
#
# ⚠ IT PLACES ONE CLUSTER. IT DOES NOT SWALLOW THE OTHERS (owner,
#   2026-09-14: "oregon/williams are still incorrect, and I see others,
#   such as a hser being on Baylor"). It used to collect EVERY cluster of
#   the name that read college, delete them all, and insert one row in the
#   directory's state -- so Baylor School (a Tennessee high school) was
#   deleted into Baylor University (TX), and its high schoolers appeared on
#   a college roster. Oregon (IL) and Williams went the same way. After
#   that fold the name has ONE cluster, which is why no amount of fixing
#   the crest or the ?state= validation could separate the two Oregons:
#   the identity table itself said there was only one.
#
# ! AND THE LEGITIMATE MERGE HAS ALREADY HAPPENED. The case this pass
#   exists for -- BYU's athletes coming out CA, UT and CO by their own home
#   states -- is a scatter of ONE squad, and a scatter of one squad SHARES
#   RACES. mergeCoRacingClusters runs before this and has already merged
#   it. So a name that still has several clusters here has clusters that
#   never raced each other, which is the definition of different schools.
#   Placing one and leaving the rest is therefore not a weaker rule than
#   the old one; it is the rule the old one was reaching for.
COLLEGE_SHARE = 0.5

# ! ONE LEVEL PER ATHLETE, THE ONE THEY MOSTLY RACED AT THIS SCHOOL. The
#   test was bool_or(pool LIKE 'college%') -- ANY college-pooled row made
#   the athlete a collegian, so the mis-pooled seasons this codebase
#   already tracks (the nine in one DIII race, logged under school_level)
#   were enough to turn a prep school's cluster college and feed it to the
#   university of the same name. A majority of an athlete's rows at the
#   school is the same rule buildSchoolLevel uses, and it cannot be moved
#   by one bad season.


def applyCollegeDirectory(cur):
    cur.execute("SELECT to_regclass('public.college_directory')")
    if cur.fetchone()[0] is None:
        print("  school_identity: no college_directory (scripts/"
              "build_college_directory.py --write); the data rule stands", flush=True)
        return
    from build_college_directory import lookup, loadDirectory
    directory = loadDirectory(cur, "state")
    cur.execute("SELECT DISTINCT school FROM school_identity_new")
    hits = {}
    for (school,) in cur.fetchall():
        st = lookup(directory, school)
        if st:
            hits[school] = st
    if not hits:
        print("  school_identity: college_directory matched no school name", flush=True)
        return
    names = sorted(hits)
    # which of each name's clusters are college clusters
    cur.execute("""
        WITH rows AS (
            SELECT rr.school, rr.person_id,
                   (rr.pool LIKE 'college%%') AS college, sum(rr.n_races) AS n
            FROM   athlete_season rr
            WHERE  rr.school = ANY(%s) AND rr.person_id IS NOT NULL
            GROUP  BY 1, 2, 3
        ),
        -- the level the athlete MOSTLY raced at this school; ties go to
        -- college, which is the only way a one-season collegian counts
        v AS (
            SELECT DISTINCT ON (school, person_id) school, person_id, college
            FROM   rows ORDER BY school, person_id, n DESC, college DESC
        )
        SELECT v.school, ph.state, count(*) FILTER (WHERE v.college), count(*)
        FROM   v JOIN person_home_state_new ph USING (person_id)
        GROUP  BY 1, 2
    """, (names,))
    # ! THE CLUSTER TO PLACE, NOT THE CLUSTERS TO EAT. The directory's own
    #   state wins when the name has a cluster there; otherwise the biggest
    #   college-reading cluster is the college and everything else is left
    #   exactly as it was.
    best = {}
    for school, st, n_col, n in cur.fetchall():
        if not n or n_col < COLLEGE_SHARE * n:
            continue
        target = hits[school]
        # (is the directory's own state, how big) -- max() picks the
        # directory state if it reads college, else the largest that does
        rank = (st == target, n)
        if school not in best or rank > best[school][0]:
            best[school] = (rank, st)
    college_clusters = {school: [st] for school, (_r, st) in best.items()}
    # the alias table may already fold some of these (the co-racing merge):
    # follow it, so every original home state lands on the directory state
    cur.execute("SELECT school, home_state, state FROM school_state_alias_new "
                "WHERE school = ANY(%s)", (names,))
    folded = {}
    for school, home, st in cur.fetchall():
        folded.setdefault((school, st), set()).add(home)
    n_rows = n_alias = 0
    for school, states in college_clusters.items():
        target = hits[school]
        cur.execute("SELECT state, n_athletes FROM school_identity_new "
                    "WHERE school = %s AND state = ANY(%s)", (school, states))
        got = cur.fetchall()
        if not got:
            continue
        total = sum(n for _st, n in got)
        homes = set()
        for st, _n in got:
            homes.add(st)
            homes |= folded.get((school, st), set())
        cur.execute("DELETE FROM school_identity_new WHERE school = %s AND state = ANY(%s)",
                    (school, states))
        # ⚠ MERGE INTO THE TARGET ROW, NEVER INSERT A SECOND ONE. When the
        #   placed cluster is not already in the directory's state the name
        #   may still HAVE a row there, and a blind INSERT would leave two
        #   rows keyed (school, target) -- two of everything downstream.
        cur.execute("UPDATE school_identity_new SET n_athletes = n_athletes + %s "
                    "WHERE school = %s AND state = %s", (total, school, target))
        if not cur.rowcount:
            cur.execute("INSERT INTO school_identity_new "
                        "(school, state, n_athletes, share, is_primary) "
                        "VALUES (%s, %s, %s, 0, false)", (school, target, total))
        cur.execute("DELETE FROM school_state_alias_new WHERE school = %s AND home_state = ANY(%s)",
                    (school, sorted(homes)))
        cur.executemany("INSERT INTO school_state_alias_new VALUES (%s, %s, %s)",
                        [(school, h, target) for h in sorted(homes) if h != target])
        n_rows += 1
        n_alias += len(homes)
    cur.execute("""
        UPDATE school_identity_new si SET
            share = round(si.n_athletes::numeric / t.total, 4),
            is_primary = (si.n_athletes = t.top AND si.state = t.top_state)
        FROM (SELECT school, sum(n_athletes) AS total, max(n_athletes) AS top,
                     (array_agg(state ORDER BY n_athletes DESC, state))[1] AS top_state
              FROM school_identity_new WHERE school = ANY(%s) GROUP BY school) t
        WHERE t.school = si.school
    """, (names,))
    print(f"  school_identity: college_directory placed {n_rows:,} of {len(hits):,} "
          f"matched names ({n_alias:,} home states folded)", flush=True)


# ===================================================================== #
#  LEVEL: WHICH INSTITUTION, NOT JUST WHICH STATE                       #
# ===================================================================== #

# ★★ LEVEL IS PART OF WHO A SCHOOL IS (owner, 2026-09-13). Amherst (MA)
#    was ONE page holding Amherst COLLEGE -- eight NESCAC runners -- and
#    Amherst Regional MIDDLE SCHOOL, seventeen seventh and eighth graders.
#    Two institutions, one name, one state, and so one page, one crest,
#    and a set of units that said NESCAC over a middle schooler. A home
#    state cannot separate them. The level can, and the pool carries it.
#
# ★★ AND IT MATTERS FAR BEYOND THE PAGE -- READ THIS BEFORE TOUCHING
#    ANYTHING ABOUT POOLS. ★★
#
#    The pool is the most load-bearing thing in the engine: it decides
#    which ratings are comparable, which board an athlete lands on, which
#    HS-equivalent factor applies, and how a season is normalised. It is
#    inferred per ATHLETE-SEASON from grade and meet context, and it is
#    wrong often enough to have its own diagnostics.
#
#    A school's LEVEL is the missing constraint. A middle school has no
#    college seniors; a NESCAC programme has no seventh graders. Once
#    (school, state, level) is a real entity with its own roster, a pool
#    that disagrees with its school's level becomes a DETECTABLE error
#    instead of an invisible one -- and anet's Level (anet_team.level, one
#    per team_id, scripts/anet_teams.py) is an INDEPENDENT witness to the
#    same fact, so the two can be cross-examined without either being
#    assumed correct.
#
#    That is the road to settling pools once and for all. This table is
#    the first piece of it. Do not drop it for looking cosmetic.
#
# ★★ A LIVE CASE TO CHECK THE FIX AGAINST (owner, 2026-09-13). Kept
#    concrete on purpose: when the pool work lands, these should stop
#    being wrong, and if they do not the work is not done.
#
#    NCAA DIII men's race (Amherst scored 318). Every one of these ran a
#    COLLEGE race and was pooled as a HIGH SCHOOLER, so each shows a
#    school-grade number and NO RATING at all:
#
#       47   Stan Craig          grade 11   Amherst (MA)   24:49.3
#       88   Jonathan Cobb       grade 12   Lynchburg (VA) 25:13.9
#       99   Jacob Slater        grade 11   Case Western   25:18.6
#      105   Nathaniel Aronson   grade 10   Bates (ME)     25:22.2
#      202   Zach Utz            grade 12   Middlebury     26:03.3
#      209   Lucas Guidone       grade 12   Hope (MI)      26:05.7
#      262   Brandon Massman     grade 12   UW-Whitewater  26:44.7
#      290   Everett Mosher      grade 10   WPI (MA)       29:01.4
#      291   Robert Cooper       grade 11   Wash. & Lee    30:43.2
#
#    Stan Craig was Amherst's NUMBER ONE SCORER, so the error is not
#    confined to one athlete's page: it silently removes the top runner
#    from a team's rating. Nine of them in a single race is the rate to
#    beat, and every one is a row whose school's LEVEL is college while
#    its own pool says hs -- the exact disagreement school_level makes
#    detectable.
#
# ! A SEPARATE TABLE, NOT A COLUMN ON school_identity -- DELIBERATELY.
#   The identity table's key is (school, state) and two passes above
#   collapse states into one row (co-racing merge, college directory).
#   Re-keying it on level means rewriting both of those, untested,
#   underneath every school page on the site. This is additive: absent,
#   everything behaves exactly as it did. Promote it into the key once it
#   has been read against real data.
# most of a level's athletes belonging somewhere else makes it a holding
# pen rather than a school. Set high on purpose: a real school does lose
# the odd transfer, and calling a school a bucket deletes it from the page.
BUCKET_SHARE = 0.60

_LEVEL_DDL = """
CREATE TABLE school_level_new AS
WITH seasons AS (
    SELECT rr.school, rr.person_id,
           split_part(COALESCE(NULLIF(rr.pool, ''), 'hs'), '_', 1) AS level,
           sum(rr.n_races) AS n
    FROM   athlete_season rr
    WHERE  COALESCE(TRIM(rr.school), '') <> '' AND rr.person_id IS NOT NULL
    GROUP  BY 1, 2, 3
),
-- ! ONE LEVEL PER ATHLETE, the one they raced most under. Without this a
--   single mis-pooled season mints an institution, which is the exact
--   failure this table exists to detect.
per_person AS (
    SELECT DISTINCT ON (school, person_id) school, person_id, level
    FROM   seasons ORDER BY school, person_id, n DESC, level
),
-- ★ WHOSE SCHOOL IS THIS REALLY? (owner, 2026-09-13: "Arkansas having
--   college and hs, when it should just be college and Arkansas is just
--   for indiv ppl at state"). At a state meet an unattached runner's team
--   cell is the STATE NAME, so "Arkansas" collects a few hundred high
--   schoolers who each belong to a real high school -- and the University
--   of Arkansas ends up sharing a page with them.
--
--   The tell is not the count and not the share: it is that every one of
--   those athletes races under a DIFFERENT name the rest of the time.
--   Amherst Regional Middle School's athletes race as "Amherst" always;
--   the state-meet crowd races as "Arkansas" once. So per athlete, at
--   THIS level, which school do they mostly run for?
--
-- ! AT THIS LEVEL, not overall. A college freshman has four high-school
--   seasons behind them, so their career-modal school is their high
--   school and every college in the country would look like a bucket.
--   Within a level the question is the right one -- and it makes the
--   phantom "hs" row a mis-pooled college athlete leaves behind read as
--   a bucket too, which it is.
belongs AS (
    SELECT DISTINCT ON (person_id, level) person_id, level, school
    FROM  (SELECT person_id, level, school, sum(n) AS n
           FROM seasons GROUP BY 1, 2, 3) x
    ORDER BY person_id, level, n DESC, school
),
clusters AS (
    SELECT p.school, COALESCE(a.state, ph.state) AS state, p.level,
           count(*) AS n_athletes,
           count(*) FILTER (
               WHERE b.school IS DISTINCT FROM p.school) AS n_elsewhere
    FROM   per_person p
    JOIN   person_home_state_new ph USING (person_id)
    LEFT   JOIN belongs b
           ON b.person_id = p.person_id AND b.level = p.level
    -- follow the same state folding the identity table did, so the two
    -- agree on which cluster a school is in
    LEFT   JOIN school_state_alias_new a
           ON a.school = p.school AND a.home_state = ph.state
    GROUP  BY 1, 2, 3
)
SELECT school, state, level, n_athletes, n_elsewhere,
       round(n_athletes::numeric
             / sum(n_athletes) OVER (PARTITION BY school, state), 4) AS share,
       -- ! A BUCKET IS NEVER THE PRIMARY LEVEL, however big it is. The
       --   state-meet crowd outnumbers the university, and the primary
       --   level is what everything downstream calls the school.
       (row_number() OVER (PARTITION BY school, state
            ORDER BY (n_elsewhere >= {bucket} * n_athletes),
                     n_athletes DESC, level) = 1)            AS is_primary,
       (n_elsewhere >= {bucket} * n_athletes)                AS is_bucket
FROM   clusters
"""


def buildSchoolLevel(cur):
    """school_level (school, state, level, n_athletes, share, is_primary).

    A row per level a (school, state) has athletes at. Most schools have
    one and nothing changes for them; a name covering two institutions has
    two that both clear MIN_ATHLETES and MIN_SHARE, and readers can then
    tell them apart."""
    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS school_level_new")
    cur.execute(_LEVEL_DDL.format(bucket=BUCKET_SHARE))
    cur.execute("CREATE INDEX school_level_new_idx ON school_level_new (school, state)")
    from school_identity import MIN_ATHLETES, MIN_SHARE
    cur.execute("""
        SELECT count(*) FROM (
            SELECT school, state FROM school_level_new
            WHERE n_athletes >= %s AND share >= %s AND NOT is_bucket
            GROUP BY school, state HAVING count(*) >= 2
        ) x
    """, (MIN_ATHLETES, MIN_SHARE))
    split = cur.fetchone()[0]
    cur.execute("SELECT count(*), count(*) FILTER (WHERE is_bucket) "
                "FROM school_level_new")
    n, buckets = cur.fetchone()
    print(f"  school_level: {n:,} (school, state, level) rows, "
          f"{split:,} schools cover more than one institution, "
          f"{buckets:,} levels are holding pens (the state-meet unattached) "
          f"in {time.time() - t0:.0f}s", flush=True)


def main():
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()

        # ★ FROM athlete_season, NOT ranking_results (owner, 2026-09-15: "it
        #   is specifically the school ids step that is so slow, I get a
        #   gateway timeout"). This step read the 61.6M-row, 23 GB boards
        #   table SEVEN times end to end -- one scan per question -- and
        #   every scan pushed the site's pages out of the OS cache and put
        #   the disk to work for the pipeline while the site's eight workers
        #   queued behind it until nginx gave up. athlete_season is built
        #   from the same rows by 10_rankings_finish, one row per
        #   (person, pool, sport, year) with the season's school, state and
        #   race count -- 12.9M rows, a twentieth of the bytes -- and every
        #   question this step asks is a question about athletes and their
        #   seasons, not about rows. The same answers, from the table made
        #   for them. The two reads that need meets (the co-racing merge)
        #   filter ranking_results by school through idx_rr_school.
        # ★ AND A COMMIT PER PHASE. The whole build used to be one
        #   transaction, so the swap's retry loop rolled back the finished
        #   build on a lock timeout and the next attempt renamed tables
        #   that no longer existed.
        cur.execute("DROP TABLE IF EXISTS person_home_state_new")
        cur.execute("""
            CREATE TABLE person_home_state_new AS
            SELECT person_id, state
            FROM  (SELECT person_id, state,
                          row_number() OVER (PARTITION BY person_id
                                             ORDER BY sum(n_races) DESC, state) AS rk
                   FROM   athlete_season
                   WHERE  person_id IS NOT NULL AND state IS NOT NULL
                   GROUP  BY person_id, state) x
            WHERE  rk = 1
        """)
        cur.execute("""
            ALTER TABLE person_home_state_new
            ADD PRIMARY KEY (person_id)
        """)
        cur.execute("SELECT count(*) FROM person_home_state_new")
        print(f"  person_home_state: {cur.fetchone()[0]:,} athletes "
              f"({(time.time() - t0) / 60:.1f} min)", flush=True)
        conn.commit()

        # ★ ANET FIRST: which names it places in two states, and where
        #   each athlete's own team for those names is. Both feed the
        #   clusters CTE below and the co-racing merge after it.
        contested = anetContestedStates(cur)
        buildTeamStates(cur, contested)

        # one vote per (school, athlete): an athlete who raced for the
        # school in five seasons is still one athlete of it
        cur.execute("DROP TABLE IF EXISTS school_identity_new")
        cur.execute("""
            CREATE TABLE school_identity_new AS
            WITH votes AS (
                SELECT DISTINCT rr.school, rr.person_id
                FROM   athlete_season rr
                WHERE  COALESCE(TRIM(rr.school), '') <> ''
                  AND  rr.person_id IS NOT NULL
            ),
            clusters AS (
                -- ★ ANET'S STATE FOR THE ATHLETE'S OWN TEAM FIRST, the
                --   home-state inference only where there is no team id
                --   (tfrrs, team_id = 0, a name anet places in one state).
                --   See anetContestedStates and buildTeamStates.
                SELECT v.school, COALESCE(ts.state, ph.state) AS state,
                       count(*) AS n_athletes
                FROM   votes v
                JOIN   person_home_state_new ph USING (person_id)
                LEFT   JOIN si_team_state ts ON ts.person_id = v.person_id
                                            AND ts.school = v.school
                GROUP  BY v.school, COALESCE(ts.state, ph.state)
            )
            SELECT school, state, n_athletes,
                   round(n_athletes::numeric
                         / sum(n_athletes) OVER (PARTITION BY school),
                         4)                                   AS share,
                   (row_number() OVER (PARTITION BY school
                        ORDER BY n_athletes DESC, state) = 1) AS is_primary
            FROM   clusters
        """)
        cur.execute("""
            CREATE INDEX school_identity_new_school_idx
            ON school_identity_new (school)
        """)
        cur.execute("SELECT count(*), count(DISTINCT school) "
                    "FROM school_identity_new")
        n, ns = cur.fetchone()
        cur.execute("""
            SELECT count(*) FROM (
                SELECT school FROM school_identity_new
                WHERE n_athletes >= 3 AND share >= 0.10
                GROUP BY school HAVING count(*) >= 2
            ) x
        """)
        multi = cur.fetchone()[0]
        print(f"  school_identity: {ns:,} schools, {n:,} clusters, "
              f"{multi:,} names split across states", flush=True)
        conn.commit()

        mergeCoRacingClusters(cur, contested)
        conn.commit()
        applyCollegeDirectory(cur)
        conn.commit()
        buildSchoolLevel(cur)
        conn.commit()

        # ---- the swap: old tables serve until the new ones are whole ----
        #
        # ⚠ THIS HAD NO RETRY AT ALL, AND IT DEADLOCKED (2026-09-01). The
        #   DROPs take ACCESS EXCLUSIVE on two tables the running site reads,
        #   in a fixed order; a page holding them in the other order is an
        #   ABBA deadlock, and Postgres kills whichever transaction it picks
        #   -- here, this one, after the whole build was already done.
        #
        # ! SAME SHAPE AS build_ranking_results.swapIn, deliberately: lock
        #   BOTH tables in one statement so the failure mode is a timeout
        #   rather than a deadlock, be impatient rather than queueing (a
        #   waiting ACCESS EXCLUSIVE makes every NEW reader queue behind it,
        #   so one slow page would freeze the site), and treat timeout and
        #   deadlock as the same retryable "a reader was in the way".
        #
        # ⚠ ALL OF IT IN ONE TRANSACTION. Half a swap leaves school_identity
        #   dropped with nothing in its place, and every page that renders a
        #   school label 500s.
        for attempt in range(1, _SWAP_ATTEMPTS + 1):
            try:
                cur.execute("BEGIN")
                cur.execute(f"SET LOCAL lock_timeout = '{_SWAP_LOCK_TIMEOUT}'")
                cur.execute("LOCK TABLE person_home_state, school_identity "
                            "IN ACCESS EXCLUSIVE MODE")
                cur.execute("DROP TABLE IF EXISTS school_state_alias")
                for t in ("person_home_state", "school_identity",
                          "school_state_alias", "school_level"):
                    cur.execute(f"DROP TABLE IF EXISTS {t}")
                    cur.execute(f"ALTER TABLE {t}_new RENAME TO {t}")
                cur.execute("ALTER INDEX school_identity_new_school_idx "
                            "RENAME TO school_identity_school_idx")
                cur.execute("ALTER INDEX school_level_new_idx "
                            "RENAME TO school_level_idx")
                cur.execute("ALTER TABLE person_home_state RENAME CONSTRAINT "
                            "person_home_state_new_pkey TO "
                            "person_home_state_pkey")
                conn.commit()
                break
            except (psycopg2.errors.LockNotAvailable,
                    psycopg2.errors.DeadlockDetected):
                conn.rollback()
                if attempt == _SWAP_ATTEMPTS:
                    # ! THE _new TABLES SURVIVE, so a rerun redoes the build
                    #   rather than leaving the site without these tables.
                    raise RuntimeError(
                        "school_identity swap: could not take ACCESS "
                        f"EXCLUSIVE in {_SWAP_ATTEMPTS} attempts. Check "
                        "pg_stat_activity for a long read.")
                print(f"  readers hold the tables, attempt {attempt}"
                      f"/{_SWAP_ATTEMPTS} -- retrying in {_SWAP_BACKOFF}s",
                      flush=True)
                time.sleep(_SWAP_BACKOFF)

        # ANALYZE after the commit, not inside it: it takes no exclusive lock
        # and holding the swap open for it would defeat the point.
        cur.execute("ANALYZE person_home_state")
        cur.execute("ANALYZE school_identity")
        conn.commit()

    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
