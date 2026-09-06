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
    """The later result_id of an exact duplicate inside one feed."""
    event = ", event_id" if sport == "TF" else ""
    return f"""
        SELECT result_id FROM (
            SELECT result_id,
                   row_number() OVER (
                       PARTITION BY person_id, source, meet_id, div_id{event},
                                    date, round(time_seconds::numeric, 1)
                       ORDER BY result_id) AS rn
            FROM   {table}
            WHERE  person_id IS NOT NULL AND time_seconds IS NOT NULL
              AND  time_seconds < 100000
        ) s
        WHERE rn > 1
    """


def dupCrossDateSql(table, sport):
    """One run stored under two meet entries (issue 158): the same person,
    feed, meet NAME, finishing place and time to the tenth, within three
    weeks -- a meet listed twice, once with the wrong date, or twice on
    the same date under two ids. The copy inside the BIGGER meet entry
    survives (the real listing has the whole field); ties go to the lower
    result_id. XC reads the name from anet's meets; tfrrs XC twins are
    the cross-feed rules' business."""
    if sport == "XC":
        name_join = ("JOIN meets m ON m.div_id = r.div_id "
                     "AND r.source = 'anet'")
    else:
        name_join = ("JOIN meets_tf m ON m.div_id = r.div_id "
                     "AND m.event_id = r.event_id")
    return f"""
        WITH sized AS (
            SELECT meet_id, count(*) AS n FROM {table}
            WHERE  meet_id IS NOT NULL GROUP BY meet_id),
        cand AS (
            SELECT r.result_id, r.person_id, r.source, s.n,
                   CASE WHEN r.date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                        THEN substr(r.date, 1, 10)::date END          AS d,
                   lower(regexp_replace(m.meet_name, '[^A-Za-z0-9]+', ' ', 'g'))
                                                                      AS mname,
                   r.place, round(r.time_seconds::numeric, 1)         AS rt
            FROM   {table} r
            {name_join}
            JOIN   sized s ON s.meet_id = r.meet_id
            WHERE  r.person_id IS NOT NULL AND r.place > 0
              AND  r.time_seconds IS NOT NULL AND r.time_seconds < 100000
              AND  m.meet_name IS NOT NULL)
        SELECT DISTINCT a.result_id
        FROM   cand a
        JOIN   cand b ON b.person_id = a.person_id AND b.source = a.source
                     AND b.mname = a.mname AND b.place = a.place
                     AND b.rt = a.rt AND b.result_id <> a.result_id
                     AND a.d IS NOT NULL AND b.d IS NOT NULL
                     AND abs(b.d - a.d) <= 21
        WHERE  b.n > a.n OR (b.n = a.n AND b.result_id < a.result_id)
    """


def dupSameDaySql(table, sport):
    """The same run under two meet NAMES on one day (2026-09-06): the
    same person, feed and date, the time equal to the tenth -- "38th
    Mariner-XC-Invitational" and "38th P. Wilder Mariner XC Invitational"
    both carried the owner's 18:55.1, and a merged person kept three copies
    of one race. Two different races by one person on one day with the
    same time to 0.1 s do not happen. No name, no place: those are what
    differ between the copies. The copy in the bigger meet entry
    survives; ties to the lower result_id."""
    return f"""
        WITH sized AS (
            SELECT meet_id, count(*) AS n FROM {table}
            WHERE  meet_id IS NOT NULL GROUP BY meet_id),
        cand AS (
            SELECT r.result_id, r.person_id, r.source, s.n,
                   substr(r.date, 1, 10)                          AS d,
                   round(r.time_seconds::numeric, 1)              AS rt
            FROM   {table} r
            JOIN   sized s ON s.meet_id = r.meet_id
            WHERE  r.person_id IS NOT NULL
              AND  r.time_seconds IS NOT NULL AND r.time_seconds < 100000
              AND  r.date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}')
        SELECT DISTINCT a.result_id
        FROM   cand a
        JOIN   cand b ON b.person_id = a.person_id AND b.source = a.source
                     AND b.d = a.d AND b.rt = a.rt
                     AND b.result_id <> a.result_id
        WHERE  b.n > a.n OR (b.n = a.n AND b.result_id < a.result_id)
    """


RULES = (("twin_race", twinRaceSql), ("twin_person", twinPersonSql),
         ("dup_same_feed", dupSameFeedSql),
         ("dup_cross_date", dupCrossDateSql),
         ("dup_same_day", dupSameDaySql))


def build(conn, write=False):
    with conn.cursor() as cur:
        ensureTable(cur)
        if write:
            cur.execute("CREATE TABLE result_twin_new (LIKE result_twin INCLUDING ALL)")
        for sport, table in TABLES.items():
            for reason, fn in RULES:
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
                print(f"  [{sport}] {reason:<14} {n:>12,}", flush=True)
        if write:
            cur.execute("DROP TABLE result_twin")
            cur.execute("ALTER TABLE result_twin_new RENAME TO result_twin")
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
