import sys
import re
import argparse
import psycopg2.extras

sys.path.insert(0, "scripts")
from database import getConn

FETCH_BATCH = 20000
SANE_YEAR   = r'^(19|20)[0-9]{2}'


# ===================================================================== #
#  SCHEMA
# ===================================================================== #

_DDL = """
CREATE TABLE IF NOT EXISTS search_index (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL,
    label       text NOT NULL,
    sublabel    text,
    link        text NOT NULL,
    search_text text NOT NULL,
    search_last text,
    sort_year   int  DEFAULT 0,      -- most recent race year (athletes, meets)
    sort_count  int  DEFAULT 0       -- races / athletes / results, per kind
);
"""

# ★ idx_search_trgm IS THE ONE THAT MATTERS. Both surfaces match with
#   `search_text LIKE '%tok%'`, and a LEADING wildcard can only be served by
#   a GIN trigram index -- a btree, text_pattern_ops or not, is useless for it.
#
# ⚠ idx_search_last IS GONE, AND SO IS THE QUERY THAT NEEDED IT. app.py used
#   to match `(search_text LIKE '%tok%' OR search_last LIKE '%tok%')`. The
#   second half had only a btree, so it was unindexable, and an OR is as slow
#   as its worst branch: measured at a Parallel Seq Scan, 5,428,976 rows
#   removed per worker, 1.35 s for the tab counts on one token.
#
#   It could never match anything extra either. Every loader below puts
#   search_last INSIDE search_text -- athletes get the last name, which is a
#   token of "name school"; schools, courses, meets and venues set the two to
#   the identical string. The OR made sense under the old LEFT-ANCHORED match,
#   where "smith" could not prefix-match "john smith northgate" but could
#   prefix-match search_last. Substring matching made it redundant.
#
#   The COLUMN stays -- it is nearly free and it is what a future
#   surname-specific ranking would use -- but nothing indexes or reads it now.
#   An existing database still carries the index; drop it when convenient:
#       DROP INDEX CONCURRENTLY IF EXISTS idx_search_last;
#
# ! idx_search_prefix STILL EARNS ITS PLACE: the ranking asks
#   `search_text LIKE 'tok%'`, which is left-anchored and does use a btree.
_INDEX = """
CREATE INDEX IF NOT EXISTS idx_search_prefix
    ON search_index (search_text text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_search_trgm
    ON search_index USING gin (search_text gin_trgm_ops);
"""


# ===================================================================== #
#  THE ATHLETE QUERY
# ===================================================================== #
#
# All the expensive work (the 33M-row UNION+sort for latest school, the 15M-row
# sort for deterministic name, the race-count aggregate) is PRECOMPUTED into
# three helper tables built once in DBeaver:
#
#     athlete_agg    (person_id, last_year, n_races)
#     athlete_school (person_id, school)
#     athlete_named  (person_id, name)
#
# So this query is three indexed joins on person_id -- seconds, not minutes.
# When the underlying results change, rebuild those three tables (see the SQL
# at the top of the file / in chat). During development they're static.

_ATHLETE_SQL = """
SELECT n.person_id, n.name, s.school, a.last_year, a.n_races
FROM   athlete_named  n
JOIN   athlete_agg    a USING (person_id)
LEFT JOIN athlete_school s USING (person_id)
WHERE  n.name IS NOT NULL
  AND  n.name NOT LIKE '%<%'
"""


# ===================================================================== #
#  HELPERS
# ===================================================================== #

def _stream_cursor(conn, name):
    """Server-side cursor so millions of rows don't load into Python at once."""
    cur = conn.cursor(name=name, cursor_factory=psycopg2.extras.RealDictCursor)
    cur.itersize = FETCH_BATCH
    return cur


def _flush(cur, batch):
    """Insert one batch. Every tuple MUST be 8 elements, in this order:
       (kind, label, sublabel, link, search_text, search_last, sort_year, sort_count)"""
    psycopg2.extras.execute_values(cur, """
        INSERT INTO search_index
            (kind, label, sublabel, link, search_text, search_last, sort_year, sort_count)
        VALUES %s
    """, batch, page_size=1000)
    return len(batch)


_LEADING_YEAR  = re.compile(r'^\s*(19|20)\d{2}\s+')      # "2026 Arcadia..."
_TRAILING_YEAR = re.compile(r'\s+(19|20)\d{2}\s*$')      # "...Arcadia 2026"


def _strip_year(name):
    """Remove a leading/trailing 4-digit year so '2026 Arcadia Invitational'
    and 'Arcadia Invitational' match identically. Year stays in sort_year."""
    n = _LEADING_YEAR.sub('', name or '')
    n = _TRAILING_YEAR.sub('', n)
    return n.strip()


# ===================================================================== #
#  LOADERS
# ===================================================================== #

def _load_athletes(conn):
    """Stream the (now cheap) athlete query, insert in batches."""
    read = _stream_cursor(conn, "athlete_src")
    read.execute(_ATHLETE_SQL)

    write = conn.cursor()
    batch, total = [], 0
    for row in read:
        name   = row["name"]
        school = row.get("school")
        parts  = name.split()
        last   = parts[-1].lower() if parts else ""
        # search_text = name + school so "carcamo northgate" can match
        search_text = f"{name} {school or ''}".strip().lower()

        batch.append((
            "athlete",
            name,
            school,                                  # sublabel = school
            f"/athlete/{row['person_id']}",
            search_text,
            last,                                    # search_last = last name
            row["last_year"] or 0,
            row["n_races"]  or 0,
        ))
        if len(batch) >= 5000:
            total += _flush(write, batch); batch = []
    if batch:
        total += _flush(write, batch)
    read.close()
    conn.commit()
    print(f"  athletes: {total:,}")
    return total


def _load_schools(conn):
    """Distinct schools, sorted by athlete count."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    wr  = conn.cursor()
    cur.execute("""
        SELECT NULLIF(TRIM(school),'') AS school,
               COUNT(DISTINCT person_id) AS n_ath
        FROM (
            SELECT school, person_id FROM results    WHERE person_id IS NOT NULL
            UNION ALL
            SELECT school, person_id FROM results_tf WHERE person_id IS NOT NULL
        ) r
        WHERE COALESCE(TRIM(school),'') <> '' AND school NOT LIKE '%<%'
        GROUP BY 1
    """)
    rows = []
    for r in cur.fetchall():
        s = r["school"]
        if not s:
            continue
        rows.append((
            "school", s, f"{r['n_ath']} athletes",
            f"/search?q={s}&kind=athlete",           # no school page yet -> filtered search
            s.lower(), s.lower(),
            0, r["n_ath"] or 0,
        ))
    _flush(wr, rows); conn.commit(); print(f"  schools: {len(rows):,}")


def _load_courses(conn):
    """XC courses, sorted by result count."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    wr  = conn.cursor()
    cur.execute("""
        SELECT course_name, n_results
        FROM course_difficulties
        WHERE course_name LIKE 'XC:%'
    """)
    rows = []
    for r in cur.fetchall():
        clean = r["course_name"][3:]                 # strip 'XC:'
        if clean:
            rows.append((
                "course", clean, "Cross Country",
                f"/course/{clean}", clean.lower(), clean.lower(),
                0, r["n_results"] or 0,
            ))
    _flush(wr, rows); conn.commit(); print(f"  courses: {len(rows):,}")


def _load_meets(conn):
    """XC + TF meets from precomputed meet_agg_* tables. Fast."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    wr  = conn.cursor()

    def _meet_rows(table, link_fmt):
        cur.execute(f"SELECT meet_id, meet_name, yr, n_ath FROM {table}")
        out = []
        for r in cur.fetchall():
            clean = _strip_year(r["meet_name"])
            if not clean:
                continue
            yr = r["yr"] or 0
            out.append((
                "meet", clean, str(yr) if yr else "",
                link_fmt.format(mid=r["meet_id"]),
                clean.lower(), clean.lower(),
                yr, r["n_ath"] or 0,
            ))
        return out

    xc = _meet_rows("meet_agg_xc", "/meet/xc/{mid}")
    _flush(wr, xc); conn.commit(); print(f"  xc meets: {len(xc):,}")

    tf = _meet_rows("meet_agg_tf", "/meet/tf/{mid}")
    _flush(wr, tf); conn.commit(); print(f"  tf meets: {len(tf):,}")


def _load_venues(conn):
    """TF venues, keyed by location_id + indoor, sorted by result count."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    wr  = conn.cursor()
    cur.execute("""
        SELECT m.location_id,
               COALESCE(m.is_indoor,0) AS indoor,
               MAX(m.meet_name)        AS label_name,
               COUNT(r.result_id)      AS n_res
        FROM meets_tf m
        JOIN results_tf r ON r.meet_id = m.meet_id
                         AND r.div_id  = m.div_id
                         AND r.event_id = m.event_id
        WHERE m.location_id IS NOT NULL
        GROUP BY m.location_id, COALESCE(m.is_indoor,0)
    """)
    rows = []
    for r in cur.fetchall():
        lbl = r["label_name"] or f"Venue {r['location_id']}"
        io  = "in" if r["indoor"] else "out"
        rows.append((
            "venue", lbl, "Indoor" if r["indoor"] else "Outdoor",
            f"/venue/tf/{r['location_id']}/{io}",
            lbl.lower(), lbl.lower(),
            0, r["n_res"] or 0,
        ))
    _flush(wr, rows); conn.commit(); print(f"  tf venues: {len(rows):,}")


# ===================================================================== #
#  MAIN
# ===================================================================== #

_LOADERS = {
    "athletes": _load_athletes,
    "schools":  _load_schools,
    "courses":  _load_courses,
    "meets":    _load_meets,
    "venues":   _load_venues,
}
_KIND = {"athletes":"athlete", "schools":"school", "courses":"course",
         "meets":"meet", "venues":"venue"}


def main():
    ap = argparse.ArgumentParser(description="Rebuild the search index.")
    ap.add_argument("--only", choices=list(_LOADERS))
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            if args.only:
                # partial: wipe just this kind, reload just this loader.
                cur.execute("DELETE FROM search_index WHERE kind = %s",
                            (_KIND[args.only],))
                conn.commit()
            else:
                cur.execute("DROP TABLE IF EXISTS search_index")
                cur.execute(_DDL)
                conn.commit()

        print("building search_index...")
        if args.only:
            _LOADERS[args.only](conn)
        else:
            for fn in _LOADERS.values():
                fn(conn)

        # rebuild indexes only on a full run (partial keeps existing ones)
        if not args.only:
            with conn.cursor() as cur:
                print("building indexes...")
                cur.execute(_INDEX)
            conn.commit()

        print("done.")


if __name__ == "__main__":
    main()