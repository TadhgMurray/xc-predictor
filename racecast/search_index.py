import sys
import re
import argparse
from urllib.parse import quote

import psycopg2.extras

sys.path.insert(0, "scripts")
from database import getConn

FETCH_BATCH = 20000
SANE_YEAR   = r'^(19|20)[0-9]{2}'


# ===================================================================== #
#  SCHEMA
# ===================================================================== #

# ★ BUILT INTO A SHADOW TABLE AND SWAPPED (2026-09-02). A full rebuild
#   dropped search_index first and reloaded it over several minutes, so
#   the site's search was empty for the whole build. The loaders write to
#   _TARGET; a full run points it at search_index_new and renames at the
#   end, inside one transaction, so readers see the old index or the new.
_TARGET = "search_index"

_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
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
-- the extension must exist before gin_trgm_ops means anything; a fresh
-- server without it fails the index create with a cryptic operator-class
-- error (harmless everywhere it is already installed)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS {prefix}_prefix
    ON {table} (search_text text_pattern_ops);
CREATE INDEX IF NOT EXISTS {prefix}_trgm
    ON {table} USING gin (search_text gin_trgm_ops);
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

# {ph_join}/{ph_col} fill in the home-state join only when pipeline 10b
# has built person_home_state -- the state that qualifies the school in
# the sublabel and makes "jackson highland ut" findable.
_ATHLETE_SQL = """
SELECT n.person_id, n.name, s.school, a.last_year, a.n_races{ph_col}
FROM   athlete_named  n
JOIN   athlete_agg    a USING (person_id)
LEFT JOIN athlete_school s USING (person_id)
{ph_join}
WHERE  n.name IS NOT NULL
  AND  n.name NOT LIKE '%<%'
"""


# ===================================================================== #
#  HELPERS
# ===================================================================== #

def _identity(conn):
    """{school: [(state, n_athletes, share, is_primary)]} from
    school_identity (pipeline 10b), empty when it is not built yet --
    the index then carries plain names, exactly as before."""
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.school_identity')")
    if cur.fetchone()[0] is None:
        return {}
    cur.execute("""
        SELECT school, state, n_athletes, share, is_primary
        FROM   school_identity
        ORDER  BY school, n_athletes DESC
    """)
    out = {}
    for s, st, n, share, prim in cur.fetchall():
        out.setdefault(s, []).append((st, n, float(share), prim))
    return out


def _hasHomeStates(conn):
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.person_home_state')")
    return cur.fetchone()[0] is not None


def _stream_cursor(conn, name):
    """Server-side cursor so millions of rows don't load into Python at once."""
    cur = conn.cursor(name=name, cursor_factory=psycopg2.extras.RealDictCursor)
    cur.itersize = FETCH_BATCH
    return cur


def _flush(cur, batch):
    """Insert one batch. Every tuple MUST be 8 elements, in this order:
       (kind, label, sublabel, link, search_text, search_last, sort_year, sort_count)"""
    psycopg2.extras.execute_values(cur, f"""
        INSERT INTO {_TARGET}
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
    has_ph = _hasHomeStates(conn)
    read = _stream_cursor(conn, "athlete_src")
    read.execute(_ATHLETE_SQL.format(
        ph_col=", ph.state AS home_state" if has_ph else ", NULL AS home_state",
        ph_join="LEFT JOIN person_home_state ph USING (person_id)"
                if has_ph else ""))

    write = conn.cursor()
    batch, total = [], 0
    for row in read:
        name   = row["name"]
        school = row.get("school")
        st     = row.get("home_state")
        # the school qualifies with the athlete's own home state --
        # "Highland (UT)" -- the site-wide label convention
        if school and st:
            school = f"{school} ({st})"
        parts  = name.split()
        last   = parts[-1].lower() if parts else ""
        # search_text = name + school (+ state) so "carcamo northgate"
        # and "jackson highland ut" both match
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


# ! THE INVERSE OF THE LABEL _load_schools BUILDS, AND IT LIVES HERE FOR THAT
#   REASON. Schools are indexed as f"{s} ({st})" -- "Tufts (MA)" -- because
#   that is the site-wide display convention. But ranking_results.school
#   stores the BARE string, so any picker that filters by school has to send
#   "Tufts". A copy of this rule in rankings.js would be a copy that drifts
#   the first time the label changes.
#
# ⚠ TWO UPPERCASE LETTERS IN TRAILING PARENTHESES, ANCHORED. Loose enough and
#   it would eat a real name; a school called "Academy (Old)" keeps its
#   suffix because "Old" is not two capitals.
_STATE_SUFFIX = re.compile(r"\s+\(([A-Z]{2})\)$")


def bareSchool(label):
    """'Tufts (MA)' -> 'Tufts'. Unlabelled names come back untouched."""
    if not label:
        return label
    return _STATE_SUFFIX.sub("", label)


def _load_schools(conn):
    """One row per real-world school, sorted by athlete count.

    ★ SPLIT NAMES GET A ROW PER STATE. school_identity clusters a
      name's athletes by home state; where two clusters are real
      (>= 3 athletes, >= 10% share), "Highland" becomes "Highland (UT)"
      and "Highland (CA)", each linking the page scoped to its state --
      the search box stops being a coin flip. Every other school gets
      its primary "(ST)" in the label and search text, the site-wide
      convention."""
    ident = _identity(conn)
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
    n_split = 0
    for r in cur.fetchall():
        s = r["school"]
        if not s:
            continue
        # The school PAGE. This linked a filtered athlete search from
        # before /school existed, and the stale index outlived the
        # route by months -- scripts/fix_school_search_links.py
        # repoints an already-built index without a full rebuild.
        link = f"/school/{quote(s, safe='')}"
        clusters = ident.get(s) or []
        real = [c for c in clusters if c[1] >= 3 and c[2] >= 0.10]
        if len(real) >= 2:
            n_split += 1
            for st, n, _share, _prim in real:
                rows.append((
                    "school", f"{s} ({st})", f"{n} athletes",
                    f"{link}?state={st}",
                    f"{s} {st}".lower(), s.lower(),
                    0, n,
                ))
            continue
        st = clusters[0][0] if clusters else None
        rows.append((
            "school", f"{s} ({st})" if st else s, f"{r['n_ath']} athletes",
            link,
            f"{s} {st}".lower() if st else s.lower(), s.lower(),
            0, r["n_ath"] or 0,
        ))
    _flush(wr, rows); conn.commit()
    print(f"  schools: {len(rows):,} ({n_split:,} names split by state)")


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


# ★ THE meet_agg_* TABLES ARE BUILT HERE, EVERY RUN. Nothing else in the
#   repository creates them: meet_agg_xc was a hand-made table that never
#   followed the corpus, and meet_agg_tf never existed at all -- the
#   2026-09-03 run's step 13c died on it (UndefinedTable). One aggregate
#   per sport, keyed the way the meet pages are (/meet/xc/<meet_id>,
#   /meet/tf/<meet_id>): the name, the first year raced, and the row count
#   as the search weight (count(*) rather than distinct people: the
#   ordering is the same and the distinct is the expensive half of a
#   121M-row group-by). Built beside, then swapped, so the sitemap step
#   that reads them never sees an empty table.
_MEET_AGG = {
    "meet_agg_xc": """
        SELECT r.meet_id,
               min(m.meet_name)                 AS meet_name,
               min(substr(r.date, 1, 4))::int   AS yr,
               count(*)                         AS n_ath
        FROM   results r
        JOIN   meets m ON m.div_id = r.div_id
        WHERE  r.meet_id IS NOT NULL AND m.meet_name IS NOT NULL
        GROUP  BY r.meet_id
    """,
    "meet_agg_tf": """
        SELECT r.meet_id,
               min(m.meet_name)                 AS meet_name,
               min(substr(r.date, 1, 4))::int   AS yr,
               count(*)                         AS n_ath
        FROM   results_tf r
        JOIN   meets_tf m ON m.div_id = r.div_id AND m.event_id = r.event_id
        WHERE  r.meet_id IS NOT NULL AND m.meet_name IS NOT NULL
        GROUP  BY r.meet_id
    """,
}


def _ensure_meet_agg(conn):
    with conn.cursor() as cur:
        for table, sql in _MEET_AGG.items():
            cur.execute(f"DROP TABLE IF EXISTS {table}_new")
            cur.execute(f"CREATE TABLE {table}_new AS {sql}")
            cur.execute(f"ALTER TABLE {table}_new ADD PRIMARY KEY (meet_id)")
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            cur.execute(f"ANALYZE {table}")
            conn.commit()
            cur.execute(f"SELECT count(*) FROM {table}")
            print(f"  {table}: {cur.fetchone()[0]:,} meets (rebuilt)")


def _load_meets(conn):
    """XC + TF meets from the meet_agg_* tables, rebuilt first."""
    _ensure_meet_agg(conn)
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

# ★ UNITS (issue 34): a league, section, area, conference, region or
#   division is searchable and lands on the rankings board narrowed to it.
#   Read from school_unit -- the resolved units, one row per school -- so a
#   unit exists here exactly when some school is in it. Numbered divisions
#   (section_div, state_div, class) are not searchable on their own: "2"
#   names nothing.
_UNIT_KINDS_HS = ("league", "area", "section", "district", "county")
_UNIT_KINDS_COLLEGE = ("conference", "region", "division")


def unitLink(kind, unit, state, college):
    """The rankings board narrowed to one unit. High-school units are
    scoped to their state, college units are nationwide (app.unitArgs
    makes the same split for the athlete page's chips)."""
    from urllib.parse import quote
    pool = "college_m" if college else "hs_m"
    q = f"/rankings?board=ability&pool={pool}&{kind}={quote(unit)}"
    if not college and state:
        q += f"&state={quote(state)}"
    return q


def _load_units(conn):
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('school_unit')")
    if cur.fetchone()[0] is None:
        print("  units: school_unit absent, skipped")
        return
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'school_unit'""")
    have = {r[0] for r in cur.fetchall()}
    wr = conn.cursor()
    out = []
    for college, kinds in ((False, _UNIT_KINDS_HS), (True, _UNIT_KINDS_COLLEGE)):
        for kind in kinds:
            if kind not in have:
                continue
            cur.execute(f"""
                SELECT "{kind}", state, count(DISTINCT school)
                FROM   school_unit
                WHERE  "{kind}" IS NOT NULL AND is_college = %s
                GROUP  BY "{kind}", state
            """, (college,))
            for unit, state, n in cur.fetchall():
                label = str(unit)
                sub = f"{kind}{' · ' + state if state and not college else ''}"
                sub += f" · {n} school{'s' if n != 1 else ''}"
                out.append((
                    "unit", label, sub,
                    unitLink(kind, str(unit), state, college),
                    label.lower(), label.lower(), 0, int(n),
                ))
    _flush(wr, out); conn.commit()
    print(f"  units: {len(out):,}")


_LOADERS = {
    "athletes": _load_athletes,
    "schools":  _load_schools,
    "courses":  _load_courses,
    "meets":    _load_meets,
    "venues":   _load_venues,
    "units":    _load_units,
}
_KIND = {"athletes":"athlete", "schools":"school", "courses":"course",
         "meets":"meet", "venues":"venue", "units":"unit"}


def main():
    ap = argparse.ArgumentParser(description="Rebuild the search index.")
    ap.add_argument("--only", choices=list(_LOADERS))
    args = ap.parse_args()

    global _TARGET
    with getConn() as conn:
        with conn.cursor() as cur:
            if args.only:
                # partial: wipe just this kind, reload just this loader,
                # in place -- a kind is small and the gap is seconds
                _TARGET = "search_index"
                cur.execute(_DDL.format(table=_TARGET))
                cur.execute("DELETE FROM search_index WHERE kind = %s",
                            (_KIND[args.only],))
                conn.commit()
            else:
                _TARGET = "search_index_new"
                cur.execute("DROP TABLE IF EXISTS search_index_new")
                cur.execute(_DDL.format(table=_TARGET))
                conn.commit()

        print(f"building {_TARGET}...")
        if args.only:
            _LOADERS[args.only](conn)
        else:
            for fn in _LOADERS.values():
                fn(conn)

        if not args.only:
            with conn.cursor() as cur:
                print("building indexes...")
                cur.execute(_INDEX.format(table="search_index_new",
                                          prefix="idx_search_new"))
                conn.commit()
                # the swap, one transaction: old index or new, never none.
                # The indexes are renamed with the table so the next run's
                # CREATE INDEX IF NOT EXISTS makes fresh ones.
                cur.execute("DROP TABLE IF EXISTS search_index")
                cur.execute("ALTER TABLE search_index_new RENAME TO search_index")
                cur.execute("ALTER INDEX idx_search_new_prefix RENAME TO idx_search_prefix")
                cur.execute("ALTER INDEX idx_search_new_trgm RENAME TO idx_search_trgm")
                cur.execute("ANALYZE search_index")
            conn.commit()
            print("search_index: swapped in")

        print("done.")


if __name__ == "__main__":
    main()