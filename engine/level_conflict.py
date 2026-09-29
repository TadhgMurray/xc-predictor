"""
level_conflict.py -- a high school race inside a college athlete's season is
somebody else's race. Flag it, by result_id, so nothing rates, ranks or shows
it as theirs.

    python engine/level_conflict.py                  # report: counts + samples
    python engine/level_conflict.py --show 60        # more samples
    python engine/level_conflict.py --person 123456  # one person, every row
    python engine/level_conflict.py --write          # refresh just this reason

The flag is written into result_twin with reason 'level_conflict' by
engine/twin_flag.py (step 04c, before the pack), like every other exclusion
there; --write here refreshes only this reason's rows, for a run that starts
past 04c. Issue: the NESCAC review, 2026-09-29.

★ WHAT THE REVIEW FOUND. Four NESCAC runners who were in college in autumn
  2025 each carried the SAME high school race, the Middlesex League
  Championship of 2025-10-26:

      Jared Rife   (Middlebury)    15:17.1   131.2   college median 107.5
      Tyler Johnson (Trinity)      15:44.1   127.3                  106.0
      Nick Walker  (Bates)         16:28.5   121.5                  106.8
      Max Bennett  (Conn College)  17:08.0   116.8                   96.8

  and it reached the published season: Nick Walker's 2025 XC season read
  108.9 against ~104-106 from his college races alone.
  ⚠ AND THAT RACE WAS NOT FROM 2025 (the first server run, 2026-09-29). anet
    meet 227716 sits among 2023 meet ids, and its rows' native ids are
    2023-era: it is the four runners' OWN 2023 high school race, stored
    under the wrong year. The fix for those four is the DATE, and it is
    engine/meet_date_fix.py (step 00_meet_dates, before any season is
    read). With the date right, their seasons hold no conflict and this rule
    leaves them alone; it stays for the conflicts a weld makes, which a
    correct date does not cure.

★ THE RULE (the review's): NEVER A HIGH SCHOOL RESULT ON A PERSON WHOSE LEVEL
  ON THAT DATE IS COLLEGE, AND THE REVERSE. One person, one sport, one
  academic year holding rows that can ONLY be college AND rows that can ONLY
  be high school is two people under one person_id. Nobody is a Middlebury
  sophomore and a Middlesex League schoolboy in the same October.

! WHAT COUNTS AS EACH LEVEL -- DEFINITE EVIDENCE ONLY, because a wrong flag
  hides a real race:
    college  a tfrrs row whose team slug says college
             ('CT_college_f_Conn_College'), or -- on a row scraped before the
             slug was kept -- the class+ELIGIBILITY grade form (FR-1 .. SR-6)
             AT A KNOWN COLLEGE (collegeSchools below).
    hs       a NUMERIC grade 9-12 -- "high school grades are NUMBERS, college
             classes are NAMES" (normalize_distance.GRADE_TO_LEVEL, owner) --
             on a row with a team (anet team 0 is "no team", pool_resolve) and
             no college or club tfrrs slug.
  ⚠ THE GRADE FORM ALONE WAS NOT EVIDENCE, AND THE FIRST SERVER RUN SHOWED IT
    (owner's --sport XC --show 40, 2026-09-29). The first cut read FR-1 ..
    SR-4 as college by itself ("the digit is NCAA eligibility and a high
    school has none"). But tfrrs HOSTS high school meets and writes the same
    form for them: persons 14178814 / 14178817 (Winnisquam Regional HS,
    tfrrs 'SR-4', school "Winnisquam"), 12216908 (Westminster Academy,
    'SO-2'), 13978036 / 13978038 (The Benjamin School, 'JR-3') -- high
    schoolers whose own tfrrs rows were read as a college season, tied
    against their anet rows, and lost a race each. The form says which
    SOFTWARE printed the results, not which level ran them. So it now counts
    only with a college signal of its own: the slug, or the school.
  A bare Fr/So/Jr/Sr is NEITHER: it is 9-12 in high school and 13-16 in
  college (grade_sanity 2c). Diego Eseverri's twelfth grade reads '12' on anet
  and 'SR' on tfrrs, the same race twice; that is one person and one level,
  and a bare word must not make it a conflict.

★ HIGH SCHOOL, THEN COLLEGE -- NEVER BACK. A December graduate who races
  indoor track for a college in January is ONE person holding both levels in
  one academic year, legitimately. So a season whose every high school row
  comes BEFORE its first college row is a transition and is left alone. A
  high school row on or after the first college day is not a transition: a
  namesake racing the same autumn interleaves, and the review's rows all sit
  mid-season (October 26, inside a September-November college season).

★ THE MINORITY SIDE IS FLAGGED, COUNTED IN RACE DAYS. The side with fewer
  race days in that (person, sport, season) is the guest: one Middlesex
  League row against eight college races is the high schooler's race on the
  collegian, and eight high school races against one college row is the
  reverse (a college namesake welded onto a real high schooler). DAYS, not
  rows: a race stored by both feeds is one race and must not vote twice
  (grade_sanity's lesson, Diego Eseverri again).
  ! A TIE FLAGS NOTHING, AND IS REPORTED (owner, 2026-09-29, reading the
    first server run). The first cut flagged both sides of a tie -- "a row we
    cannot place does not belong on a board" -- and that deleted a real
    athlete's season: person 6339154, one Kenston HS race and one RPI race,
    lost both. A tie is not evidence about which side is foreign, and a flag
    must be evidence. The tied seasons are counted and printed (TIE in the
    samples) so the link that made them can be looked at and undone by hand.

⚠ WHY A FLAG AND NOT A MOVE. Detaching the rows to their own person_id is
  the better end state and the worse step:
    - unlink.py mints from max(person_id)+1, which is non-idempotent (it is
      kept out of the pipeline for exactly that reason) and now sits inside
      the minted tfrrs range (1,000,000,000 + native id, link_tfrrs_rows),
      where a later mint could collide with it;
    - when a tfrrs career was welded onto a high schooler's ANET id (the
      shape a name link makes), the anet rows are already on their own id --
      "moving them back" is a no-op, and the rows that belong elsewhere are
      the tfrrs ones, whose way home is the linker's own --undo, not a mint;
    - a move changes the ids pages are served under (13c0's redirects) every
      time the verdict changes.
  A flag is rebuilt from scratch on every run, so it follows the data: undo
  the bad link and the flag simply disappears. Nothing is deleted -- the row
  keeps its time in results; only its rating, its place on a board and its
  line on the athlete page go, exactly as for a twin.

⚠ THE KNOWN COST, STATED. A post-collegiate runner whose CLUB rows still read
  grade 12 (Kerem Ayhan's shape, grade_sanity 5b) in the same academic year
  as their last SR-4 race looks like a conflict too, and the smaller side is
  excluded. grade_sanity already moves those seasons to pro from the NEXT
  year on; within the year it is a handful of rows kept off the boards, which
  is the side to err on.
"""
import argparse
import os
import sys
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# ! THE ONE CLOCK. The season is the academic year, August seam, from
#   season_year -- the key grade_sanity, the pack and the boards all use.
from season_year import seasonYearSqlInt                     # noqa: E402

REASON = "level_conflict"
TABLES = {"XC": "results", "TF": "results_tf"}
COLLEGE, HS = "college", "hs"

# a date the season arithmetic can read ('TBA' and friends are skipped, not
# cast -- a cast failure would take the whole rule down)
_DATE_OK = "{a}.date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'"


# ------------------------------------------------------------------ #
#  THE LEVEL OF ONE ROW -- one definition, SQL, used by every caller
# ------------------------------------------------------------------ #
# ★ IMPORTED, NOT MIRRORED. link_freshmen and link_idless_by_name ask the
#   same question ("is this row college / high school?") before they weld,
#   and they call these rather than spelling their own.

# the temp table stageCollegeSchools leaves: the tfrrs school strings that are
# known colleges. collegeSql reads it, so every session that asks the question
# stages it first (prepare, link_freshmen.freshmen, link_idless_by_name).
COLLEGE_SCHOOLS = "lc_college_school"
# ★ -[1-6], NOT -[1-4] (2026-09-29): tfrrs writes redshirt and fifth-year
#   rows as SO-3, JR-4, SR-5, SR-6, and with [1-4] an NCAA DI fifth-year such
#   as Dylan Schubert (Furman, SR-5) read as not-college. grade_sanity and
#   speed_ratings_db were widened the same day.
_ELIGIBILITY = "'^(FR|SO|JR|SR)-[1-6]$'"


def _slugLevelSql(a):
    """The level a tfrrs team slug names ('CT_college_f_Conn_College' ->
    'college'), '' when the row has no slug -- pool_resolve.teamLevelFromSlug
    in SQL."""
    return f"COALESCE(split_part(lower({a}.team_slug), '_', 2), '')"


def collegeSql(a="r"):
    """A row that can only be a college athlete's: tfrrs, AND a college
    signal of its own -- the team slug says college, or (a row with no slug)
    the eligibility grade form at a school stageCollegeSchools knows as a
    college. The grade form alone is a tfrrs-hosted high school meet as often
    as a college one (the header says who it cost). Never NULL: a row with
    no grade or no school is simply not college."""
    slug = _slugLevelSql(a)
    return (f"COALESCE(({a}.source = 'tfrrs' AND ("
            f"{slug} = 'college' "
            f"OR ({slug} = '' "
            f"AND upper(btrim({a}.grade)) ~ {_ELIGIBILITY} "
            f"AND {a}.school IN (SELECT school FROM {COLLEGE_SCHOOLS})))), false)")


# ★ WHICH tfrrs SCHOOL STRINGS ARE COLLEGES -- TWO WITNESSES AND ONE VETO.
#   A slug is tfrrs's own word for a team's level, but only rows scraped
#   since database._migrateResultsAddTeamSlug carry one; the old rows have
#   the display string alone. So a string is a college when
#     - college_directory knows it (build_college_directory.lookup: every
#       NCAA and NAIA institution, the feeds' short forms, a name several
#       colleges share settled only by a state -- unsettled is a miss), or
#     - tfrrs itself put that string on a row with a COLLEGE slug,
#   and NOT when tfrrs ever put it on a row with a HIGH SCHOOL or MIDDLE
#   SCHOOL slug. The veto is the collision guard: the directory matches
#   names, and a high school called Hamilton or Trinity has a college's
#   name. tfrrs calling a team with that exact string a high school is the
#   one fact that settles it, so the string is not evidence of college for
#   any row. (Kenston, Winnisquam, Westminster Academy, The Benjamin School:
#   no college of those names, so no witness, so no college.)
# ⚠ THE COST, STATED. A junior college is in none of the four lists, so a
#   JUCO runner's slugless rows are not college here; a college the feeds
#   spell in a way the directory cannot place is not either. Both are
#   conflicts missed -- a flag not raised -- which is the side to err on.
def collegeSchools(evidence, known):
    """{school strings that are colleges} from
    evidence: [(school, has_college_slug, has_school_slug)] and
    known(school) -> bool, the directory's answer."""
    out = set()
    for school, slug_college, slug_school in evidence:
        if not school or slug_school:
            continue
        if slug_college or known(school):
            out.add(school)
    return out


def stageCollegeSchools(cur, tables=("results", "results_tf"), reuse=True):
    """Leave temp table lc_college_school (school) on this session: one scan
    of each table's tfrrs rows, the directory asked once per distinct string.
    reuse: keep one already staged on this session (twin_flag asks per
    sport on one connection; the answer does not change between them)."""
    if reuse:
        cur.execute("SELECT to_regclass('pg_temp.' || %s)", (COLLEGE_SCHOOLS,))
        if cur.fetchone()[0] is not None:
            return None
    cur.execute(f"DROP TABLE IF EXISTS {COLLEGE_SCHOOLS}")
    slug = _slugLevelSql("t")
    parts = [f"""
        SELECT t.school, {slug} AS lvl FROM {tb} t
        WHERE  t.source = 'tfrrs' AND NULLIF(btrim(t.school), '') IS NOT NULL
          AND  ({slug} IN ('college', 'hs', 'ms')
                OR ({slug} = '' AND upper(btrim(t.grade)) ~ {_ELIGIBILITY}))"""
             for tb in tables]
    cur.execute(f"""
        SELECT school, bool_or(lvl = 'college'), bool_or(lvl IN ('hs', 'ms'))
        FROM ({' UNION ALL '.join(parts)}) x
        GROUP  BY school""")
    evidence = cur.fetchall()
    known = _directory(cur)
    schools = collegeSchools(evidence, known)
    cur.execute(f"CREATE TEMP TABLE {COLLEGE_SCHOOLS} (school text PRIMARY KEY)")
    if schools:
        cur.execute(f"INSERT INTO {COLLEGE_SCHOOLS} SELECT unnest(%s::text[])",
                    (sorted(schools),))
    cur.execute(f"ANALYZE {COLLEGE_SCHOOLS}")
    vetoed = sum(1 for _s, _c, h in evidence if h)
    print(f"  [level] {len(schools):,} tfrrs school strings are colleges "
          f"({vetoed:,} carry a high school slug and never are)", flush=True)
    return schools


def _directory(cur):
    """known(school) -> bool from college_directory; always False (and said)
    when the table is absent -- then only a college slug makes a college."""
    try:
        from build_college_directory import loadDirectory, lookup
    except ImportError:                     # pragma: no cover -- a bare checkout
        loadDirectory = None
    entries = loadDirectory(cur, "name") if loadDirectory else {}
    if not entries:
        print("  [level] ! college_directory absent or empty "
              "(scripts/build_college_directory.py --write): only a college "
              "team slug makes a tfrrs row college", flush=True)
        return lambda school: False
    return lambda school: lookup(entries, school) is not None


def hsSql(a="r"):
    """A row that can only be a high schooler's: a numeric grade 9-12, on a
    team, not on a college or club tfrrs team."""
    return (f"(btrim({a}.grade) ~ '^(9|10|11|12)$' "
            f"AND COALESCE({a}.team_id, -1) <> 0 "
            f"AND COALESCE(split_part(lower({a}.team_slug), '_', 2), '') "
            f"NOT IN ('college', 'club'))")


def levelSql(a="r"):
    """'college' / 'hs' / NULL. College first: a college slug with a stale
    numeric grade is a collegian (hsSql already refuses that slug)."""
    return (f"(CASE WHEN {collegeSql(a)} THEN '{COLLEGE}' "
            f"WHEN {hsSql(a)} THEN '{HS}' END)")


def acadSql(a="r"):
    """The academic year of a row, NULL for a date that is not a date."""
    return (f"(CASE WHEN {_DATE_OK.format(a=a)} "
            f"THEN {seasonYearSqlInt(None, f'{a}.date')} END)")


# ------------------------------------------------------------------ #
#  THE CANDIDATES -- three statements, each small by the time it runs
# ------------------------------------------------------------------ #
# ★ COLLEGE CELLS FIRST. College rows are a few percent of the corpus and
#   high school rows most of it, so the (person, season) cells with any
#   college row are found first (one filtered scan), the high school rows
#   are hashed against that small set (one scan), and only the persons left
#   -- both levels in one season, thousands -- are read row by row through
#   the person_id index.
# ! TEMP TABLES, NOT ONE STATEMENT OF CTEs: twin_flag runs with nested loops
#   off (its SESSION says why), and the last step is the one place a nested
#   loop over the index is the right plan; it turns them back on for that
#   statement alone.

def stagedSql(table):
    """[(label, sql)] -- the statements prepare() runs, in order. The last
    leaves lc_rows (result_id, person_id, acad, day, level)."""
    acad = acadSql("r")
    return [
        ("college cells", f"""
            CREATE TEMP TABLE lc_col AS
            SELECT DISTINCT r.person_id, {acad} AS acad
            FROM   {table} r
            WHERE  r.person_id IS NOT NULL AND r.source = 'tfrrs'
              AND  {_DATE_OK.format(a='r')}
              AND  {collegeSql('r')}"""),
        ("cells holding high school rows too", f"""
            CREATE TEMP TABLE lc_cell AS
            SELECT DISTINCT r.person_id, c.acad
            FROM   {table} r
            JOIN   lc_col c ON c.person_id = r.person_id
                           AND c.acad = {acad}
            WHERE  r.person_id IS NOT NULL
              AND  {_DATE_OK.format(a='r')}
              AND  {hsSql('r')}"""),
        ("their rows", f"""
            CREATE TEMP TABLE lc_rows AS
            SELECT r.result_id, r.person_id, c.acad,
                   substr(r.date, 1, 10) AS day, {levelSql('r')} AS level
            FROM   lc_cell c
            JOIN   {table} r ON r.person_id = c.person_id
            WHERE  {_DATE_OK.format(a='r')}
              AND  {acad} = c.acad
              AND  {levelSql('r')} IS NOT NULL"""),
    ]


# ------------------------------------------------------------------ #
#  THE DECISION -- pure: rows in, result_ids out
# ------------------------------------------------------------------ #

def conflictedCells(rows):
    """{(person_id, acad): {'college': [(result_id, day)], 'hs': [...]}} for
    the cells that are a conflict and not a transition.

    rows: (result_id, person_id, acad, day, level); day is 'YYYY-MM-DD'.
    """
    cells = defaultdict(lambda: {COLLEGE: [], HS: []})
    for rid, pid, ay, day, lv in rows:
        if lv in (COLLEGE, HS) and ay is not None and day:
            cells[(pid, ay)][lv].append((rid, str(day)[:10]))
    out = {}
    for key, side in cells.items():
        col, hs = side[COLLEGE], side[HS]
        if not col or not hs:
            continue
        # high school, then college, never back: a season whose every high
        # school day precedes its first college day is a graduation
        if max(d for _, d in hs) < min(d for _, d in col):
            continue
        out[key] = side
    return out


def guests(side):
    """The level to flag in one conflicted cell: the one with fewer race
    DAYS; () on a tie -- a tie is no evidence which side is foreign."""
    n_col = len({d for _, d in side[COLLEGE]})
    n_hs = len({d for _, d in side[HS]})
    if n_hs < n_col:
        return (HS,)
    if n_col < n_hs:
        return (COLLEGE,)
    return ()


def judge(rows):
    """(flags, ties): flags {result_id: (person_id, acad, level flagged)},
    every row of the minority side of every conflicted cell; ties
    [(person_id, acad)], the conflicted cells left alone for want of a
    majority."""
    flags, ties = {}, []
    for (pid, ay), side in conflictedCells(rows).items():
        lose = guests(side)
        if not lose:
            ties.append((pid, ay))
        for lv in lose:
            for rid, _day in side[lv]:
                flags[rid] = (pid, ay, lv)
    return flags, sorted(ties)


def decide(rows):
    """{result_id: (person_id, acad, level flagged)} -- judge()'s flags."""
    return judge(rows)[0]


# ------------------------------------------------------------------ #
#  PREPARE -- run the stages, decide, leave tw_level_conflict
# ------------------------------------------------------------------ #

def prepare(cur, table, sport, explain=False):
    """Leave temp table tw_level_conflict (result_id) on this session and
    return {result_id: (person_id, acad, level)}. twin_flag calls this as the
    staging step of its 'level_conflict' rule, the way prepareCrossDate
    stages dup_cross_date."""
    t0 = time.time()
    for tmp in ("lc_col", "lc_cell", "lc_rows", "lc_tie", "tw_level_conflict"):
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
    # the school half of collegeSql, once per session (both sports' rows)
    stageCollegeSchools(cur)
    stages = stagedSql(table)
    cur.execute("SHOW enable_nestloop")
    nestloop = cur.fetchone()[0]
    for i, (label, sql) in enumerate(stages):
        last = i == len(stages) - 1
        if last:
            # the one statement a nested loop over person_id is right for
            cur.execute("SET enable_nestloop = on")
        if explain:
            cur.execute(f"EXPLAIN {sql}")
            print(f"  -- {label}")
            for (line,) in cur.fetchall():
                print(f"  {line}")
        cur.execute(sql)
        if last:
            # back to whatever the caller had (twin_flag runs with it off)
            cur.execute(f"SET enable_nestloop = {'on' if nestloop == 'on' else 'off'}")
        if i == 0:
            cur.execute("CREATE INDEX ON lc_col (person_id, acad)")
            cur.execute("ANALYZE lc_col")
        elif i == 1:
            cur.execute("CREATE INDEX ON lc_cell (person_id)")
            cur.execute("ANALYZE lc_cell")
    cur.execute("SELECT result_id, person_id, acad, day, level FROM lc_rows")
    flags, ties = judge(cur.fetchall())
    cur.execute("CREATE TEMP TABLE tw_level_conflict (result_id bigint PRIMARY KEY)")
    if flags:
        cur.execute("INSERT INTO tw_level_conflict "
                    "SELECT unnest(%s::bigint[]) ON CONFLICT DO NOTHING",
                    (sorted(flags),))
    cur.execute("ANALYZE tw_level_conflict")
    # the ties, kept for the report: nothing is flagged for them
    cur.execute("CREATE TEMP TABLE lc_tie (person_id bigint, acad int)")
    if ties:
        cur.execute("INSERT INTO lc_tie SELECT unnest(%s::bigint[]), "
                    "unnest(%s::int[])",
                    ([p for p, _a in ties], [a for _p, a in ties]))
    persons = {p for p, _a, _l in flags.values()}
    by = defaultdict(int)
    for _p, _a, lv in flags.values():
        by[lv] += 1
    print(f"  [{sport}] level_conflict: {len(persons):,} persons, "
          f"{by[HS]:,} high school rows on college seasons, "
          f"{by[COLLEGE]:,} college rows on high school seasons; "
          f"{len(ties):,} tied seasons left alone "
          f"({time.time() - t0:.0f}s)", flush=True)
    return flags


def ruleSql(table, sport):
    """twin_flag's rule body: what prepare() decided."""
    return "SELECT result_id FROM tw_level_conflict"


# ------------------------------------------------------------------ #
#  REPORT / REFRESH
# ------------------------------------------------------------------ #

def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def _samples(cur, table, sport, flags, limit, person=None):
    """Every row of the first `limit` conflicted persons, with where each
    identity came from -- the evidence needed to undo the link that made it."""
    cur.execute("SELECT person_id, acad FROM lc_cell")
    cells = cur.fetchall()
    cur.execute("SELECT person_id, acad FROM lc_tie")
    tied = set(cur.fetchall())
    # the flagged persons first, then the ties -- a tie flags nothing and is
    # printed so the link behind it can be judged by eye
    if person is None:
        flagged = sorted({p for p, _a, _l in flags.values()})
        pids = flagged[:limit] + sorted({p for p, _a in tied}
                                        - set(flagged))[:limit]
    else:
        pids = [int(person)]
    if not pids:
        return
    has_log = _hasTable(cur, "person_link_log")
    log = ("(SELECT string_agg(DISTINCT l.rule, ',') FROM person_link_log l "
           "WHERE l.sport = %s AND l.result_id = r.result_id)"
           if has_log else "NULL::text")
    params = ([sport] if has_log else []) + [pids]
    cur.execute(f"""
        SELECT r.person_id, {acadSql('r')} AS acad, r.result_id, r.source,
               r.athlete_id, r.native_id, r.grade, r.school, r.date,
               r.meet_id, r.time_seconds, {levelSql('r')} AS level, {log}
        FROM   {table} r
        WHERE  r.person_id = ANY(%s)
        ORDER  BY r.person_id, r.date, r.result_id""", params)
    rows = cur.fetchall()
    chosen = set(pids)
    want = {(p, a) for p, a in cells if p in chosen}
    last = None
    for (pid, ay, rid, src, aid, nat, grade, school, date, meet, t, lv,
         rule) in rows:
        if (pid, ay) not in want:
            continue
        if pid != last:
            print(f"\n    person {pid}")
            last = pid
        mark = ("FLAG" if rid in flags
                else "TIE " if (pid, ay) in tied and lv else "    ")
        tt = f"{t:8.1f}" if t is not None else "      --"
        print(f"      {mark} {str(date)[:10]} {src:<6} {str(lv or '-'):<7} "
              f"g={str(grade or '-'):<5} {str(school or '')[:28]:<28} "
              f"meet {meet} t={tt} athlete_id={aid} native={nat} "
              f"linked_by={rule or '-'}")


def refresh(conn, flags_by_sport):
    """Replace this reason's rows in result_twin (short transaction). A row
    another reason already holds keeps it -- the same precedence twin_flag's
    build gives its later rules."""
    with conn.cursor() as cur:
        import twin_flag
        twin_flag.ensureTable(cur)
        cur.execute("DELETE FROM result_twin WHERE reason = %s", (REASON,))
        gone = cur.rowcount
        n = 0
        for sport, flags in flags_by_sport.items():
            if not flags:
                continue
            cur.execute("""
                INSERT INTO result_twin (sport, result_id, reason)
                SELECT %s, unnest(%s::bigint[]), %s
                ON CONFLICT DO NOTHING""", (sport, sorted(flags), REASON))
            n += cur.rowcount
    conn.commit()
    print(f"[level] result_twin: {gone:,} old '{REASON}' rows replaced by {n:,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=15,
                    help="conflicted persons to print, per sport")
    ap.add_argument("--person", type=int, help="print one person's conflicted seasons")
    ap.add_argument("--sport", choices=tuple(TABLES))
    ap.add_argument("--write", action="store_true",
                    help="refresh result_twin's level_conflict rows")
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args()
    from database import getConn
    by_sport = {}
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '1GB'")
            for sport, table in TABLES.items():
                if a.sport and sport != a.sport:
                    continue
                flags = prepare(cur, table, sport, explain=a.explain)
                by_sport[sport] = flags
                if a.show or a.person:
                    _samples(cur, table, sport, flags, a.show or 1, a.person)
        conn.rollback()                                  # temp tables only
        if a.write:
            if a.sport:
                sys.exit("[level] --write rebuilds both sports; drop --sport")
            refresh(conn, by_sport)
        else:
            print("\n[level] report only; twin_flag.py --write (step 04c) or "
                  "--write here puts the flags in result_twin")


if __name__ == "__main__":
    main()
