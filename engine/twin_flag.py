"""
twin_flag.py -- one physical race stored twice, flagged ONCE, by result_id.

    python engine/twin_flag.py            # report: counts and samples
    python engine/twin_flag.py --write    # (re)build result_twin
    python engine/twin_flag.py --explain TF:dup_race_copy   # one rule's plan, not run
    XCP_TWIN_SKIP=level_conflict python engine/twin_flag.py --write   # without it

Pipeline step 04c, before the pack. Issues 15 and 94.

★ ONE TABLE, EVERY READER. The engine already anti-joined cross-feed twins
  keyed on (person_id, canon_meet_id) and kept the anet copy -- but a twin
  attached to ANOTHER person_id (the Cam Kuss shape: the tfrrs copy linked to
  a different or unresolved id) never matched, and fill_ratings then PRICED
  the survivor, so it reached the boards and the page anyway. Excluding a
  row in one reader is not a dedup. This writes the verdict down:

      result_twin (sport, result_id, reason)

  and the engine (speed_ratings_db), the pricer (fill_ratings), the boards
  (build_ranking_results) and the athlete page (app.get_races) all
  anti-join it. Empty table, no effect.

  reason
    twin_race     a tfrrs row at a canon-linked meet where an anet row has
                  the SAME finishing place and the SAME time to the tenth
                  -- whoever the two rows are attached to. The person keyed
                  rule cannot see these; this is the 88.1% diag_twin_person
                  measured. Place AND time together: a place alone repeats
                  across divisions, a time alone repeats across people.
    twin_person   the old rule, kept so the table is complete: a tfrrs row
                  whose person has an anet row at the same canon meet.
    dup_same_feed the later result_id of two rows in ONE feed that agree on
                  person, meet, division (and event, on track), date and
                  time to the tenth. Two rows, one run.
    dup_same_day  one run under two meet entries on one day whose names
                  differ (2026-09-28; see dupSameDaySql).
    dup_converted a track race stored as run and again converted to the
                  neighbouring distance, 2 mile / 3200 (dupConvertedSql).
    xc_placeholder a track race on the XC calendar (xcPlaceholderSql).
    level_conflict a high school row inside a college athlete's season, or
                  the reverse: one person_id, one sport, one academic year,
                  rows that can only be college AND rows that can only be
                  high school. The minority side is someone else's race
                  (engine/level_conflict.py says how it is decided).

! ANET IS ALWAYS THE SURVIVOR of a cross-feed pair, as before: it carries
  athlete_id and grade; tfrrs XC has athlete_id NULL on every row.
! NOTHING IS DELETED. A flagged row stays in results with its time; only
  its rating, its place on a board and its line on the page go. --write
  rebuilds the whole table from scratch, so a re-scrape cannot leave a
  stale flag behind.
"""
import argparse
import os
import time
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

TABLES = {"XC": "results", "TF": "results_tf"}

_DDL = """
CREATE TABLE IF NOT EXISTS result_twin (
    sport     text   NOT NULL,
    result_id bigint NOT NULL,
    reason    text   NOT NULL,
    PRIMARY KEY (sport, result_id)
)
"""


def ensureTable(cur):
    """The table, possibly empty. Every reader calls this before it
    anti-joins, so a database that has never run --write still works."""
    cur.execute(_DDL)


def twinRaceSql(table, sport):
    """tfrrs rows matched by (canon meet, place, time to 0.1s) to an anet
    row. The anet side is restricted to canon meets that have tfrrs rows at
    all, so the hash join stays small."""
    return f"""
        WITH linked AS (
            SELECT DISTINCT canon_meet_id FROM {table}
            WHERE  source = 'tfrrs' AND canon_meet_id IS NOT NULL
              AND  time_seconds IS NOT NULL
        ),
        anet AS (
            SELECT DISTINCT a.canon_meet_id, a.place,
                   round(a.time_seconds::numeric, 1) AS rt
            FROM   {table} a
            JOIN   linked l ON l.canon_meet_id = a.canon_meet_id
            WHERE  a.source = 'anet' AND a.place > 0
              AND  a.time_seconds IS NOT NULL AND a.time_seconds < 100000
        )
        SELECT t.result_id
        FROM   {table} t
        JOIN   anet a ON a.canon_meet_id = t.canon_meet_id
                     AND a.place = t.place
                     AND a.rt = round(t.time_seconds::numeric, 1)
        WHERE  t.source = 'tfrrs' AND t.place > 0
          AND  t.time_seconds IS NOT NULL
    """


def twinPersonSql(table, sport):
    """The engine's original rule: a tfrrs row whose person also has an anet
    row at the same canon meet."""
    return f"""
        SELECT t.result_id
        FROM   {table} t
        WHERE  t.source = 'tfrrs' AND t.canon_meet_id IS NOT NULL
          AND  t.person_id IS NOT NULL
          AND  EXISTS (SELECT 1 FROM {table} a
                       WHERE a.source = 'anet'
                         AND a.person_id = t.person_id
                         AND a.canon_meet_id = t.canon_meet_id)
    """


def dupSameFeedSql(table, sport):
    """The later result_id of an exact duplicate inside one feed.

    ! A GROUP, NOT A WINDOW (2026-09-06, "make it faster"). row_number()
      over seven columns sorted the whole table; grouping the same seven
      columns is one hash pass, and only keys that occur twice come out.
      The row that survives is the lowest result_id, as before."""
    event = ", event_id" if sport == "TF" else ""
    return f"""
        WITH keys AS (
            SELECT person_id, source, meet_id, div_id{event}, date,
                   round(time_seconds::numeric, 1) AS rt,
                   min(result_id) AS keep
            FROM   {table}
            WHERE  person_id IS NOT NULL AND time_seconds IS NOT NULL
              AND  time_seconds < 100000
            GROUP  BY person_id, source, meet_id, div_id{event}, date,
                      round(time_seconds::numeric, 1)
            HAVING count(*) > 1)
        SELECT r.result_id
        FROM   {table} r
        JOIN   keys k ON k.person_id = r.person_id AND k.source = r.source
                     AND k.meet_id IS NOT DISTINCT FROM r.meet_id
                     AND k.div_id IS NOT DISTINCT FROM r.div_id
                     {'AND k.event_id IS NOT DISTINCT FROM r.event_id' if event else ''}
                     AND k.date IS NOT DISTINCT FROM r.date
                     AND k.rt = round(r.time_seconds::numeric, 1)
        WHERE  r.result_id <> k.keep
          AND  r.time_seconds IS NOT NULL AND r.time_seconds < 100000
    """


def _nameNorm(col):
    return f"lower(regexp_replace({col}, '[^A-Za-z0-9]+', ' ', 'g'))"


def crossDateMeetsSql(table, sport):
    """The meets that appear as a PAIR: same normalised name, another meet
    id, first dates within 21 days. Materialised into a temp table by
    prepareCrossDate before the row-level rule runs.

    ★ GENERIC NAMES ARE NOT PAIRS (2026-09-06, third cut). "Home Meet",
      "Dual Meet", "Invitational", "Tri Meet" carry hundreds of meet ids
      inside any three-week window, and a self-join on the name turns each
      of them into tens of thousands of pairs; the row-level join then saw
      most of the track table again, which is where run16 stalled. A real
      double listing is one meet under two or three ids, so a meet paired
      with more than MAX_PAIRS others is a generic name and is skipped."""
    if sport == "XC":
        names = f"""
            SELECT meet_id, min({_nameNorm('meet_name')}) AS mname
            FROM   meets
            WHERE  meet_name IS NOT NULL AND meet_id IS NOT NULL
            GROUP  BY meet_id"""
    else:
        names = f"""
            SELECT meet_id, min({_nameNorm('meet_name')}) AS mname
            FROM   meets_tf
            WHERE  meet_name IS NOT NULL AND meet_id IS NOT NULL
            GROUP  BY meet_id"""
    return f"""
        WITH names0 AS ({names}),
        -- ⚠ THE CAP IS APPLIED BEFORE THE SELF-JOIN. A name on 20,000 meet
        --   ids ("dual meet") joined to itself is 400 million rows before
        --   the date test sees one of them; several such names is an hour.
        --   A real recurring meet has one id a year, a few at most, so a
        --   name on more than MAX_NAME_IDS ids is generic and never joins.
        names AS (
            SELECT meet_id, mname
            FROM   (SELECT meet_id, mname,
                           count(*) OVER (PARTITION BY mname) AS k
                    FROM   names0) x
            WHERE  k <= {MAX_NAME_IDS}),
        sized AS (
            SELECT meet_id, count(*) AS n,
                   min(CASE WHEN date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                            THEN substr(date, 1, 10)::date END) AS d0
            FROM   {table}
            WHERE  meet_id IS NOT NULL
            GROUP  BY meet_id),
        pairs AS (
            SELECT a.meet_id, a.mname, count(*) AS n_pairs
            FROM   names a
            JOIN   sized sa ON sa.meet_id = a.meet_id
            JOIN   names b ON b.mname = a.mname AND b.meet_id <> a.meet_id
            JOIN   sized sb ON sb.meet_id = b.meet_id
            WHERE  sa.d0 IS NOT NULL AND sb.d0 IS NOT NULL
              AND  abs(sb.d0 - sa.d0) <= 21
            GROUP  BY a.meet_id, a.mname)
        SELECT p.meet_id, p.mname, s.n
        FROM   pairs p JOIN sized s ON s.meet_id = p.meet_id
        WHERE  p.n_pairs <= {MAX_PAIRS}
    """


MAX_PAIRS = 12        # a meet paired with more within 21 days is a generic name
MAX_NAME_IDS = 80     # a name on more meet ids than this, ever, is a generic name


def prepareCrossDate(cur, table, sport):
    """Build tw_cross_meets (one temp table per sport, replaced each call)
    and say how big it is, so a stall is at least visible."""
    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS tw_cross_meets")
    cur.execute(f"CREATE TEMP TABLE tw_cross_meets AS {crossDateMeetsSql(table, sport)}")
    cur.execute("CREATE INDEX ON tw_cross_meets (meet_id)")
    cur.execute("ANALYZE tw_cross_meets")
    cur.execute("SELECT count(*), count(DISTINCT mname) FROM tw_cross_meets")
    n, names = cur.fetchone()
    print(f"  [{sport}] dup_cross_date: {n:,} meets in {names:,} name pairs "
          f"({time.time() - t0:.0f}s)", flush=True)


def dupCrossDateSql(table, sport):
    """One run stored under two meet entries (issue 158): the same person,
    feed, meet NAME, finishing place and time to the tenth, within three
    weeks -- a meet listed twice, once with the wrong date, or twice on
    the same date under two ids. The copy inside the BIGGER meet entry
    survives (the real listing has the whole field); ties go to the lower
    result_id. XC reads the name from anet's meets; tfrrs XC twins are
    the cross-feed rules' business.

    ★ THE PAIR OF MEETS IS FOUND FIRST, BY NAME AND DATE, BEFORE ANY RESULT
      ROW IS READ (second cut, 2026-09-06), and since the third cut it is
      a temp table (tw_cross_meets, prepareCrossDate) with generic names
      dropped, built and counted before this runs. Only rows of those
      meets reach the row-level work, through an indexed join."""
    feed = "AND r.source = 'anet'" if sport == "XC" else ""
    return f"""
        WITH cand AS (
            SELECT r.result_id, r.person_id, r.source, t.n,
                   CASE WHEN r.date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                        THEN substr(r.date, 1, 10)::date END          AS d,
                   t.mname, r.place, round(r.time_seconds::numeric, 1) AS rt
            FROM   tw_cross_meets t
            JOIN   {table} r ON r.meet_id = t.meet_id
            WHERE  r.person_id IS NOT NULL AND r.place > 0
              AND  r.time_seconds IS NOT NULL AND r.time_seconds < 100000
              {feed}),
        keys AS (
            SELECT person_id, source, mname, place, rt
            FROM   cand GROUP BY 1, 2, 3, 4, 5 HAVING count(*) > 1),
        c2 AS (
            SELECT c.* FROM cand c
            JOIN   keys k ON k.person_id = c.person_id AND k.source = c.source
                         AND k.mname = c.mname AND k.place = c.place AND k.rt = c.rt)
        SELECT DISTINCT a.result_id
        FROM   c2 a
        JOIN   c2 b ON b.person_id = a.person_id AND b.source = a.source
                   AND b.mname = a.mname AND b.place = a.place
                   AND b.rt = a.rt AND b.result_id <> a.result_id
                   AND a.d IS NOT NULL AND b.d IS NOT NULL
                   AND abs(b.d - a.d) <= 21
        WHERE  b.n > a.n OR (b.n = a.n AND b.result_id < a.result_id)
    """


def dupRaceCopySql(table, sport):
    """A whole race listed twice (owner, 2026-09-06): "if a race is exactly
    the same, remove the later race", and "the entire race, not individual
    rows -- individual rows lead to noise (prelims and finals), an entire
    race doesn't". Two divisions of one feed within 90 days whose fields
    coincide: at least 5 finishers with the same (person, time to the
    tenth) and at least 90% of the smaller division matched. The LOSER is
    the later date; on the same date the smaller division (the bigger has
    the whole field), then the higher (meet_id, div_id). EVERY row of the
    loser is flagged, matched or not. A prelim and its final share people
    but not times, so they never reach 90%.

    ★ THE SELF-JOIN SEES ONLY REPEATED KEYS (2026-09-06). A (person, feed,
      tenth) that occurs once in the whole table cannot pair with
      anything, and that is nearly every row; one hash pass finds the
      keys that occur twice, and only their rows are joined. The division
      sizes and the final flagging still read every row, as they must."""
    ev = ", event_id" if sport == "TF" else ""
    ev_eq = " AND l.event_id = r.event_id" if ev else ""
    base_where = f"""
            WHERE  person_id IS NOT NULL AND time_seconds IS NOT NULL
              AND  time_seconds < 100000
              AND  date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'"""
    key_a = "a.meet_id, a.div_id" + (", a.event_id" if ev else "")
    key_b = "b.meet_id, b.div_id" + (", b.event_id" if ev else "")
    sel_a = "a.meet_id AS ma, a.div_id AS da" + (", a.event_id AS ea" if ev else "")
    sel_b = "b.meet_id AS mb, b.div_id AS db" + (", b.event_id AS eb" if ev else "")
    # ⚠ ON TRACK THE KEY CARRIES THE EVENT, AND A KEY SEEN MORE THAN 8
    #   TIMES IS A COMMON TIME, NOT A COPY (2026-09-06). A sprinter runs
    #   11.2 twenty times a season; keyed on (person, feed, tenth) alone
    #   that is 190 pairs per athlete and the pair join was heading for
    #   the hour cross-date took. A copied race is the same event, and
    #   two or three listings of it, never twenty.
    kev = ", event_id" if ev else ""
    kev_eq = " AND k.event_id IS NOT DISTINCT FROM r.event_id" if ev else ""
    pev_eq = " AND b.event_id IS NOT DISTINCT FROM a.event_id" if ev else ""
    return f"""
        WITH keys AS (
            SELECT person_id, source{kev}, round(time_seconds::numeric, 1) AS rt
            FROM   {table}
            {base_where}
            GROUP  BY 1, 2, 3{', 4' if ev else ''} HAVING count(*) BETWEEN 2 AND 8),
        rows_ AS (
            SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id{', r.event_id' if ev else ''},
                   substr(r.date, 1, 10)::date AS d, k.rt
            FROM   {table} r
            JOIN   keys k ON k.person_id = r.person_id AND k.source = r.source
                         AND k.rt = round(r.time_seconds::numeric, 1){kev_eq}
            WHERE  r.date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
              AND  r.time_seconds IS NOT NULL AND r.time_seconds < 100000),
        size AS (
            SELECT source, meet_id, div_id{ev}, count(*) AS n,
                   min(substr(date, 1, 10)::date) AS d
            FROM   {table}
            {base_where}
            GROUP  BY source, meet_id, div_id{ev}),
        pair AS (
            SELECT a.source, {sel_a}, {sel_b}, count(*) AS shared
            FROM   rows_ a
            JOIN   rows_ b ON b.person_id = a.person_id AND b.source = a.source
                          AND b.rt = a.rt{pev_eq}
                          AND ({key_b}) <> ({key_a})
                          AND abs(b.d - a.d) <= 90
            GROUP  BY 1, 2, 3, 4, 5{', 6, 7' if ev else ''}),
        loser AS (
            SELECT p.source, p.ma AS meet_id, p.da AS div_id{', p.ea AS event_id' if ev else ''}
            FROM   pair p
            JOIN   size sa ON sa.source = p.source AND sa.meet_id = p.ma AND sa.div_id = p.da{' AND sa.event_id = p.ea' if ev else ''}
            JOIN   size sb ON sb.source = p.source AND sb.meet_id = p.mb AND sb.div_id = p.db{' AND sb.event_id = p.eb' if ev else ''}
            WHERE  p.shared >= 5 AND p.shared * 10 >= least(sa.n, sb.n) * 9
              AND  (sa.d > sb.d
                    OR (sa.d = sb.d AND (sa.n < sb.n
                        OR (sa.n = sb.n AND (p.ma, p.da) > (p.mb, p.db))))))
        SELECT DISTINCT r.result_id
        FROM   {table} r
        JOIN   loser l ON l.source = r.source AND l.meet_id = r.meet_id
                      AND l.div_id = r.div_id{ev_eq}
        {base_where}
    """


def dupSameDaySql(table, sport):
    """One run listed under two meet ENTRIES on one day whose names differ
    (owner's page, 2026-09-28: "38th Mariner-XC-Invitational" and "38th P.
    Wilder Mariner XC Invitational", 2021-10-16, 23rd in 18:55.1 in both).
    dup_cross_date needs the same normalised name, so it never paired them.
    Same person, feed, date, finishing place and time to the tenth (and
    event, on the track) under two meet ids: nobody finishes two races on
    one day in the same place to the tenth. The copy in the SMALLER meet
    entry goes (the real listing has the whole field); ties to the higher
    result_id."""
    ev = ", r.event_id" if sport == "TF" else ""
    ev_k = ", event_id" if sport == "TF" else ""
    ev_eq = " AND b.event_id IS NOT DISTINCT FROM a.event_id" if sport == "TF" else ""
    return f"""
        WITH keys AS (
            SELECT person_id, source, date, place{ev_k},
                   round(time_seconds::numeric, 1) AS rt
            FROM   {table}
            WHERE  person_id IS NOT NULL AND place > 0 AND time_seconds IS NOT NULL
              AND  time_seconds < 100000 AND date IS NOT NULL
            GROUP  BY person_id, source, date, place{ev_k}, round(time_seconds::numeric, 1)
            HAVING min(meet_id) <> max(meet_id)),   -- two meet ids, one hash pass
        cand AS (
            SELECT r.result_id, r.person_id, r.source, r.date, r.place{ev},
                   r.meet_id, k.rt
            FROM   {table} r
            JOIN   keys k ON k.person_id = r.person_id AND k.source = r.source
                         AND k.date = r.date AND k.place = r.place
                         AND k.rt = round(r.time_seconds::numeric, 1)
                         {'AND k.event_id IS NOT DISTINCT FROM r.event_id' if sport == 'TF' else ''}),
        size AS (
            SELECT meet_id, count(*) AS n FROM {table}
            WHERE  meet_id IN (SELECT DISTINCT meet_id FROM cand)
            GROUP  BY meet_id)
        SELECT DISTINCT a.result_id
        FROM   cand a JOIN size sa ON sa.meet_id = a.meet_id
        JOIN   cand b ON b.person_id = a.person_id AND b.source = a.source
                     AND b.date = a.date AND b.place = a.place AND b.rt = a.rt
                     AND b.meet_id <> a.meet_id{ev_eq}
        JOIN   size sb ON sb.meet_id = b.meet_id
        WHERE  sb.n > sa.n OR (sb.n = sa.n AND b.result_id < a.result_id)
    """


# a 2 mile is 3218.69 m against 3200, a mile 1609.34 against 1600: both 1.005838
CONVERSION = 3218.688 / 3200.0
CONVERSION_TOL = 0.0012


def dupConvertedSql(table, sport):
    """One track race stored twice, once as run and once CONVERTED to the
    neighbouring distance (Michael Rynne, JAMBAR Dec 13, 2025: "2miles
    9:35.09" and "3200m 9:31.74" -- 9:35.09 x 3200/3218.7 = 9:31.6). Same
    person, feed, meet and date, two different events, and the slower time
    over the faster by the mile/metric ratio (1.00584, the same for the
    mile and 1600 as for the 2 mile and 3200) within CONVERSION_TOL. Two
    real races in one day never sit on that exact ratio. The later-entered
    row (higher result_id) goes. Track only; relays out."""
    if sport != "TF":
        return f"SELECT result_id FROM {table} WHERE false"
    lo, hi = CONVERSION - CONVERSION_TOL, CONVERSION + CONVERSION_TOL
    return f"""
        WITH base AS (
            SELECT result_id, person_id, source, meet_id, date, event_short,
                   time_seconds AS t
            FROM   {table}
            WHERE  person_id IS NOT NULL AND time_seconds > 200
              AND  time_seconds < 100000 AND COALESCE(is_relay, 0) = 0
              AND  COALESCE(is_field, 0) = 0),
        -- only a (person, meet, day) with two different events can pair:
        -- one hash pass, and the self-join sees those rows alone
        keys AS (
            SELECT person_id, source, meet_id, date FROM base
            GROUP  BY 1, 2, 3, 4
            HAVING min(event_short) <> max(event_short)),
        r AS (
            SELECT b.* FROM base b
            JOIN   keys k ON k.person_id = b.person_id AND k.source = b.source
                         AND k.meet_id = b.meet_id AND k.date = b.date)
        SELECT DISTINCT CASE WHEN a.result_id > b.result_id THEN a.result_id
                             ELSE b.result_id END AS result_id
        FROM   r a
        JOIN   r b ON b.person_id = a.person_id AND b.source = a.source
                  AND b.meet_id = a.meet_id AND b.date = a.date
                  AND b.event_short IS DISTINCT FROM a.event_short
                  AND b.t > a.t
                  AND b.t / a.t BETWEEN {lo:.6f} AND {hi:.6f}
    """


def xcPlaceholderSql(table, sport):
    """A TRACK race put on the cross country calendar (owner's page,
    2026-09-28: "Mid-Season Mania 1600m Invitational (XC Calendar
    Placeholder)", a 1600 in the XC season with a course difficulty, and
    the same race again in track). The meet says what it is in its name;
    its XC rows are not cross country."""
    if sport != "XC":
        return f"SELECT result_id FROM {table} WHERE false"
    return f"""
        SELECT r.result_id
        FROM   {table} r
        WHERE  r.meet_id IN (SELECT DISTINCT meet_id FROM meets
                             WHERE  meet_name ILIKE '%%placeholder%%'
                               AND  meet_id IS NOT NULL)
    """

# ★ NOT A TWIN, AND IN THIS TABLE ANYWAY (the NESCAC review, 2026-09-29:
#   four college runners each carrying the same 2025 Middlesex League high
#   school race, and one of them a season 3 points high because of it). A
#   high school race on a college season is a row that is not this person's,
#   and "a row that is not this person's" is what every reader already
#   anti-joins here -- the engine, the pricer, the boards and the athlete
#   page. A new table would need all four taught a new join, and the one
#   that was missed would be the leak. LAST, so a row that is also a twin is
#   filed as a twin.
import level_conflict as LC                                     # noqa: E402

RULES = (("twin_race", twinRaceSql), ("twin_person", twinPersonSql),
         ("dup_same_feed", dupSameFeedSql),
         ("dup_cross_date", dupCrossDateSql),
         ("dup_race_copy", dupRaceCopySql),
         ("dup_same_day", dupSameDaySql),
         ("dup_converted", dupConvertedSql),
         ("xc_placeholder", xcPlaceholderSql),
         (LC.REASON, LC.ruleSql))


def prepareRule(cur, table, sport, reason, explain=False):
    """The staging a rule needs before its SELECT can run: the meet pairs of
    dup_cross_date, the decided rows of level_conflict. Nothing for the
    rest."""
    if reason == "dup_cross_date":
        prepareCrossDate(cur, table, sport)
    elif reason == LC.REASON:
        LC.prepare(cur, table, sport, explain=explain)


# ★ NO NESTED LOOPS (2026-09-27: run stuck over 24 h in track's
#   dup_race_copy, a rule that took 557 s on 2026-09-07). The rules group
#   and self-join CTEs, and Postgres cannot estimate a CTE built from a
#   HAVING: EXPLAIN on a 4M-row copy put rows_ at ONE row and joined
#   rows_ a to rows_ b in a nested loop -- every row against every row.
#   On the server rows_ is tens of millions, so that is n^2, forever.
#   Every join in every rule has an equality key, so with nested loops off
#   each one is a hash join: the plan the rules were written for.
SESSION = ("SET work_mem = '2GB'", "SET max_parallel_workers_per_gather = 4",
           "SET enable_nestloop = off")

# ★ NO RULE GETS TO HOLD THE PIPELINE. A rule that runs past this many
#   seconds is cancelled and its flags from the LAST run are carried over
#   (stale by one run is better than a day lost). XCP_TWIN_RULE_TIMEOUT=0
#   turns the limit off.
RULE_TIMEOUT = int(os.environ.get("XCP_TWIN_RULE_TIMEOUT", "7200"))


def _session(conn, cur):
    """The rules' settings, for this session only: memory so the hash
    passes do not spill, and no nested loops (SESSION says why)."""
    for stmt in SESSION:
        try:
            cur.execute(stmt)
        except Exception:                        # noqa: BLE001
            conn.rollback()


def carryOver(cur, sport, reason):
    """Last run's flags for one rule, into result_twin_new. The number
    carried; 0 when there is no last run."""
    cur.execute("SELECT to_regclass('result_twin')")
    if cur.fetchone()[0] is None:
        return 0
    cur.execute("""
        INSERT INTO result_twin_new (sport, result_id, reason)
        SELECT sport, result_id, reason FROM result_twin
        WHERE  sport = %s AND reason = %s
        ON CONFLICT DO NOTHING""", (sport, reason))
    return cur.rowcount


def explain(conn, which):
    """Print the plan (no run) of one rule, e.g. TF:dup_race_copy, under
    the settings build() uses. dup_cross_date builds its meet pairs first."""
    sport, _, reason = which.partition(":")
    fn = dict(RULES)[reason]
    table = TABLES[sport]
    with conn.cursor() as cur:
        _session(conn, cur)
        prepareRule(cur, table, sport, reason, explain=True)
        cur.execute(f"EXPLAIN {fn(table, sport)}")
        for (line,) in cur.fetchall():
            print(line)
    conn.rollback()


def build(conn, write=False):
    import psycopg2.errors
    with conn.cursor() as cur:
        ensureTable(cur)
        _session(conn, cur)
        if write:
            cur.execute("CREATE TABLE result_twin_new (LIKE result_twin INCLUDING ALL)")
        # ! XCP_TWIN_SKIP=dup_cross_date,dup_race_copy skips named rules for
        #   one run (2026-09-06): a rule that stalls should cost the run
        #   that rule, not the whole pipeline. Says so in the log; since
        #   2026-09-27 a skipped rule keeps its flags from the last run.
        skip = {r.strip() for r in os.environ.get("XCP_TWIN_SKIP", "").split(",") if r.strip()}
        for sport, table in TABLES.items():
            for reason, fn in RULES:
                t0 = time.time()
                if reason in skip:
                    kept = carryOver(cur, sport, reason) if write else 0
                    print(f"  [{sport}] {reason:<14} skipped (XCP_TWIN_SKIP); "
                          f"{kept:,} flags kept from the last run", flush=True)
                    continue
                cur.execute("SAVEPOINT twin_rule")
                cur.execute(f"SET statement_timeout = {int(RULE_TIMEOUT * 1000)}")
                try:
                    prepareRule(cur, table, sport, reason)
                    if write:
                        # earlier reasons win the primary key: a row that is a
                        # cross-feed twin is filed as one, not as a feed dup
                        cur.execute(f"""
                            INSERT INTO result_twin_new (sport, result_id, reason)
                            SELECT %s, s.result_id, %s FROM ({fn(table, sport)}) s
                            ON CONFLICT DO NOTHING
                        """, (sport, reason))
                        n = cur.rowcount
                    else:
                        cur.execute(f"SELECT count(*) FROM ({fn(table, sport)}) s")
                        n = cur.fetchone()[0]
                except psycopg2.errors.QueryCanceled:
                    # the SET above is undone with the savepoint
                    cur.execute("ROLLBACK TO SAVEPOINT twin_rule")
                    kept = carryOver(cur, sport, reason) if write else 0
                    print(f"  [{sport}] {reason:<14} TIMED OUT after {time.time() - t0:.0f}s "
                          f"(XCP_TWIN_RULE_TIMEOUT={RULE_TIMEOUT}); {kept:,} flags kept "
                          "from the last run", flush=True)
                    continue
                cur.execute("RESET statement_timeout")
                cur.execute("RELEASE SAVEPOINT twin_rule")
                print(f"  [{sport}] {reason:<14} {n:>12,}  ({time.time() - t0:.0f}s)",
                      flush=True)
        if write:
            # short-lock swap with retries, then ANALYZE outside the lock
            # (database.swapTable says why a bare DROP was a site outage)
            from database import swapTable
            swapTable(conn, "result_twin", "result_twin_new")
            cur.execute("ANALYZE result_twin")
            cur.execute("SELECT count(*) FROM result_twin")
            print(f"  result_twin: {cur.fetchone()[0]:,} rows")
        else:
            print("  (report only; --write rebuilds result_twin)")
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--explain", metavar="SPORT:RULE",
                    help="print one rule's plan without running it, e.g. TF:dup_race_copy")
    args = ap.parse_args()
    with getConn() as conn:
        if args.explain:
            explain(conn, args.explain)
        else:
            build(conn, write=args.write)


if __name__ == "__main__":
    main()
