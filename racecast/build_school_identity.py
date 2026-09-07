"""
build_school_identity.py -- who every athlete and school string IS.

    python racecast/build_school_identity.py

Pipeline step 10b, right after 10_rankings: everything here reads
ranking_results. Two tables out:

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


def mergeCoRacingClusters(cur):
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

    for sc, s1, s2, shared in cur.fetchall():
        small = min(n_races.get((sc, s1), 0), n_races.get((sc, s2), 0))
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
    if not groups:
        print("  school_identity: no co-racing clusters to merge", flush=True)
        return
    merged_schools = sorted({r[0] for r in groups})
    # where a merged school hosts: the state of meets named after it
    # the school's name as a whole word inside the meet name (ARE word
    # boundaries), the name's regex metacharacters escaped
    escape_sql = "regexp_replace(s.school, '([().*+?\\[\\]\\\\^$|])', '\\\\\\1', 'g')"
    cur.execute(f"""
        SELECT s.school, rr.state, count(DISTINCT rr.meet_id)
        FROM   ranking_results rr
        JOIN   (SELECT unnest(%s::text[]) AS school) s ON s.school = rr.school
        LEFT   JOIN meets m ON m.div_id = rr.div_id AND rr.sport = 'XC'
        LEFT   JOIN meets_tfrrs mt ON mt.meet_id = rr.meet_id AND rr.sport = 'XC'
        WHERE  rr.state IS NOT NULL
          AND  COALESCE(m.meet_name, mt.meet_name) ~* ('{chr(92)}m' || {escape_sql} || '{chr(92)}M')
        GROUP  BY 1, 2
    """, (merged_schools,))
    hosted = {}
    for sc, st, n in cur.fetchall():
        if n > hosted.get(sc, (None, 0))[1]:
            hosted[sc] = (st, n)
    # else where most of its rows were run
    cur.execute("""
        SELECT school, state, count(*) FROM ranking_results
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
#   IS. A name in it has its college-pool clusters (half or more of the
#   cluster's athletes in a college pool) collapsed into one row in the
#   directory's state; a high-school cluster wearing the same name
#   ("Washington") is left alone. Without the table, nothing changes.
COLLEGE_SHARE = 0.5


def applyCollegeDirectory(cur):
    cur.execute("SELECT to_regclass('public.college_directory')")
    if cur.fetchone()[0] is None:
        print("  school_identity: no college_directory (scripts/"
              "build_college_directory.py --write); the data rule stands", flush=True)
        return
    from build_college_directory import lookup
    cur.execute("SELECT name_norm, state FROM college_directory")
    directory = {k: v for k, v in cur.fetchall()}
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
        WITH v AS (
            SELECT DISTINCT rr.school, rr.person_id,
                   bool_or(rr.pool LIKE 'college%%') OVER (PARTITION BY rr.school, rr.person_id) AS college
            FROM   ranking_results rr WHERE rr.school = ANY(%s) AND rr.person_id IS NOT NULL)
        SELECT v.school, ph.state, count(*) FILTER (WHERE v.college), count(*)
        FROM   v JOIN person_home_state_new ph USING (person_id)
        GROUP  BY 1, 2
    """, (names,))
    college_clusters = {}
    for school, st, n_col, n in cur.fetchall():
        if n and n_col >= COLLEGE_SHARE * n:
            college_clusters.setdefault(school, []).append(st)
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
        cur.execute("INSERT INTO school_identity_new (school, state, n_athletes, share, is_primary) "
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


def main():
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()

        cur.execute("DROP TABLE IF EXISTS person_home_state_new")
        cur.execute("""
            CREATE TABLE person_home_state_new AS
            SELECT person_id,
                   mode() WITHIN GROUP (ORDER BY state) AS state
            FROM   ranking_results
            WHERE  person_id IS NOT NULL AND state IS NOT NULL
            GROUP  BY person_id
        """)
        cur.execute("""
            ALTER TABLE person_home_state_new
            ADD PRIMARY KEY (person_id)
        """)
        cur.execute("SELECT count(*) FROM person_home_state_new")
        print(f"  person_home_state: {cur.fetchone()[0]:,} athletes "
              f"({(time.time() - t0) / 60:.1f} min)", flush=True)

        # one vote per (school, athlete): an athlete who raced for the
        # school in five seasons is still one athlete of it
        cur.execute("DROP TABLE IF EXISTS school_identity_new")
        cur.execute("""
            CREATE TABLE school_identity_new AS
            WITH votes AS (
                SELECT DISTINCT rr.school, rr.person_id
                FROM   ranking_results rr
                WHERE  COALESCE(TRIM(rr.school), '') <> ''
                  AND  rr.person_id IS NOT NULL
            ),
            clusters AS (
                SELECT v.school, ph.state, count(*) AS n_athletes
                FROM   votes v
                JOIN   person_home_state_new ph USING (person_id)
                GROUP  BY v.school, ph.state
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

        mergeCoRacingClusters(cur)
        applyCollegeDirectory(cur)

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
                          "school_state_alias"):
                    cur.execute(f"DROP TABLE IF EXISTS {t}")
                    cur.execute(f"ALTER TABLE {t}_new RENAME TO {t}")
                cur.execute("ALTER INDEX school_identity_new_school_idx "
                            "RENAME TO school_identity_school_idx")
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
