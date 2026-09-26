"""
twin_flag.py -- one physical race stored twice, flagged ONCE, by result_id.

    python engine/twin_flag.py            # report: counts and samples
    python engine/twin_flag.py --write    # (re)build result_twin

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


RULES = (("twin_race", twinRaceSql), ("twin_person", twinPersonSql),
         ("dup_same_feed", dupSameFeedSql),
         ("dup_cross_date", dupCrossDateSql),
         ("dup_race_copy", dupRaceCopySql))


def build(conn, write=False):
    with conn.cursor() as cur:
        ensureTable(cur)
        # the rules are hash passes over the whole table; give them memory
        # so they do not spill, for this session only
        for stmt in ("SET work_mem = '2GB'", "SET max_parallel_workers_per_gather = 4"):
            try:
                cur.execute(stmt)
            except Exception:                        # noqa: BLE001
                conn.rollback()
        if write:
            cur.execute("CREATE TABLE result_twin_new (LIKE result_twin INCLUDING ALL)")
        # ! XCP_TWIN_SKIP=dup_cross_date,dup_race_copy skips named rules for
        #   one run (2026-09-06): a rule that stalls should cost the run
        #   that rule, not the whole pipeline. Says so in the log.
        skip = {r.strip() for r in os.environ.get("XCP_TWIN_SKIP", "").split(",") if r.strip()}
        for sport, table in TABLES.items():
            for reason, fn in RULES:
                t0 = time.time()
                if reason in skip:
                    print(f"  [{sport}] {reason:<14} skipped (XCP_TWIN_SKIP)", flush=True)
                    continue
                if reason == "dup_cross_date":
                    prepareCrossDate(cur, table, sport)
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
    args = ap.parse_args()
    with getConn() as conn:
        build(conn, write=args.write)


if __name__ == "__main__":
    main()
