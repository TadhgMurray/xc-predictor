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

★ THE RULE (the review's): NEVER A HIGH SCHOOL RESULT ON A PERSON WHOSE LEVEL
  ON THAT DATE IS COLLEGE, AND THE REVERSE. One person, one sport, one
  academic year holding rows that can ONLY be college AND rows that can ONLY
  be high school is two people under one person_id. Nobody is a Middlebury
  sophomore and a Middlesex League schoolboy in the same October.

! WHAT COUNTS AS EACH LEVEL -- DEFINITE EVIDENCE ONLY, because a wrong flag
  hides a real race:
    college  a tfrrs row whose grade is the class+ELIGIBILITY form (FR-1 ..
             SR-4: the digit is NCAA eligibility and a high school has none,
             grade_sanity section 1.6), or whose tfrrs team slug says college
             ('CT_college_f_Conn_College').
    hs       a NUMERIC grade 9-12 -- "high school grades are NUMBERS, college
             classes are NAMES" (normalize_distance.GRADE_TO_LEVEL, owner) --
             on a row with a team (anet team 0 is "no team", pool_resolve) and
             no college or club tfrrs slug.
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
  ! A TIE FLAGS BOTH. One race each way is no evidence about which is the
    guest, and a row we cannot place does not belong on a board.

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

def collegeSql(a="r"):
    """A row that can only be a college athlete's: tfrrs, with the
    class+eligibility grade or a college team slug."""
    return (f"({a}.source = 'tfrrs' AND ("
            f"upper(btrim({a}.grade)) ~ '^(FR|SO|JR|SR)-[1-4]$' "
            f"OR split_part(lower({a}.team_slug), '_', 2) = 'college'))")


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
    """The level(s) to flag in one conflicted cell: the one with fewer race
    DAYS, or both on a tie."""
    n_col = len({d for _, d in side[COLLEGE]})
    n_hs = len({d for _, d in side[HS]})
    if n_hs < n_col:
        return (HS,)
    if n_col < n_hs:
        return (COLLEGE,)
    return (COLLEGE, HS)


def decide(rows):
    """{result_id: (person_id, acad, level flagged)} -- every row of the
    minority side of every conflicted cell."""
    flags = {}
    for (pid, ay), side in conflictedCells(rows).items():
        for lv in guests(side):
            for rid, _day in side[lv]:
                flags[rid] = (pid, ay, lv)
    return flags


# ------------------------------------------------------------------ #
#  PREPARE -- run the stages, decide, leave tw_level_conflict
# ------------------------------------------------------------------ #

def prepare(cur, table, sport, explain=False):
    """Leave temp table tw_level_conflict (result_id) on this session and
    return {result_id: (person_id, acad, level)}. twin_flag calls this as the
    staging step of its 'level_conflict' rule, the way prepareCrossDate
    stages dup_cross_date."""
    t0 = time.time()
    for tmp in ("lc_col", "lc_cell", "lc_rows", "tw_level_conflict"):
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
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
    flags = decide(cur.fetchall())
    cur.execute("CREATE TEMP TABLE tw_level_conflict (result_id bigint PRIMARY KEY)")
    if flags:
        cur.execute("INSERT INTO tw_level_conflict "
                    "SELECT unnest(%s::bigint[]) ON CONFLICT DO NOTHING",
                    (sorted(flags),))
    cur.execute("ANALYZE tw_level_conflict")
    persons = {p for p, _a, _l in flags.values()}
    by = defaultdict(int)
    for _p, _a, lv in flags.values():
        by[lv] += 1
    print(f"  [{sport}] level_conflict: {len(persons):,} persons, "
          f"{by[HS]:,} high school rows on college seasons, "
          f"{by[COLLEGE]:,} college rows on high school seasons "
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
    pids = sorted({p for p, _a, _l in flags.values()}) if person is None \
        else [int(person)]
    pids = pids[:limit]
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
        mark = "FLAG" if rid in flags else "    "
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
