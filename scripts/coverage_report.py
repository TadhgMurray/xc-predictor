#!/usr/bin/env python3
"""
coverage_report.py -- what each source has that we do not, this cross
country season: meets, races within meets, rows within races, and rows
we took in wrong. READ-ONLY (except --nightly, which writes only its own
table, coverage_nightly).

    cd /srv/xc-predictor && set -a && . /etc/xc-predictor.env && set +a
    /srv/venv/bin/python scripts/coverage_report.py                     # the season, every source
    /srv/venv/bin/python scripts/coverage_report.py --by state          # missing meets by state
    /srv/venv/bin/python scripts/coverage_report.py --section meets --source anet --show 40
    /srv/venv/bin/python scripts/coverage_report.py --nightly           # step 01z: one line a source, stored

★ WHY (owner, 2026-11-10: "find what exists on each source but not in our
  database ... by state and week"). Every scraper said what it saved; none
  said what it was offered and did not save. Each source here is
  "expected" (what the source itself listed or answered) against "have"
  (a meet with result rows from that source):

    anet   every meet id the launcher's GetMeetData answered with divisions
           (anet_meet_seen) or that wrote a division (meets), dated in the
           window. "twin" = closed because a timer's copy stands in.
    tfrrs  every XC meet the scrape saw (meets_tfrrs) and every meet the
           state sites' index lists (tfrrs_hs_meets).
    alive  every AthleticLIVE XC meet its index listed (alive_meets).
    timer  every meet page a timer's index offered (ext_seen, from
           2026-11-10 on) and every page landed (meets_ext).

  A meet is "have" for its source when that source's rows are in, and
  "elsewhere" when another source's copy of the same meet is (the twin
  links each lander writes). Missing = expected - have - elsewhere.

! WHY A MEET IS MISSING is the column that makes this a to-do list: the
  queue's state (failed, in flight, asked and empty, never asked), the
  source's own word (athletic.net's HasResults), or a timer page's outcome.

! THE WINDOW ENDS TWO DAYS AGO by default (--lag): a meet run on Saturday is
  not missing on Saturday night.
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

ALIVE_RESULT_BASE = 3_000_000_000_000          # queue_meets.ALIVE_RESULT_BASE
SOURCES = ("anet", "tfrrs", "alive", "timer")
SECTIONS = ("meets", "races", "rows", "misreads")
ISO = r"^\d{4}-\d\d-\d\d"


def seasonStart(today):
    """Aug 1 of this cross country season (Jan-Jul belongs to last fall)."""
    return dt.date(today.year if today.month >= 8 else today.year - 1, 8, 1)


# ---------------------------------------------------------------- helpers

def _has(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    return bool(cur.fetchone()[0])


def _cols(cur, table):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    return {r[0] for r in cur.fetchall()}


def _run(cur, sql, args=()):
    """Rows, or [] and a printed reason: one missing table never costs the
    rest of the report."""
    try:
        cur.execute("SAVEPOINT cov")
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT cov")
        return rows
    except Exception as exc:                                    # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT cov")
        print(f"   (skipped: {type(exc).__name__}: {str(exc).splitlines()[0]})")
        return []


def _table(rows, heads, limit=None):
    rows = list(rows)[:limit] if limit else list(rows)
    if not rows:
        print("   (none)")
        return
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    w = [max(len(h), *(len(c[i]) for c in cells)) for i, h in enumerate(heads)]
    print("   " + "  ".join(h.ljust(w[i]) for i, h in enumerate(heads)))
    for c in cells:
        print("   " + "  ".join(v.rjust(w[i]) if v.replace(".", "").replace("-", "").isdigit()
                                 else v.ljust(w[i]) for i, v in enumerate(c)))


# ---------------------------------------------------------------- meets: one SQL per source
#
# Each returns rows (source, meet key, name, date, state, status, why):
#   status: 'have' | 'elsewhere' | 'missing'

def _anetSql(cur):
    seen = _has(cur, "anet_meet_seen")
    twin = _has(cur, "anet_local_twin")
    qcols = _cols(cur, "meet_queue")
    empty = "q.empty_has_results" if "empty_has_results" in qcols else "NULL::boolean"
    seen_part = ("""SELECT meet_id, name, meet_date::text AS d, state FROM anet_meet_seen
                    WHERE sport = 'XC' AND COALESCE(n_divs, 1) > 0""" if seen else
                 "SELECT NULL::bigint, NULL, NULL, NULL WHERE false")
    twin_sql = ("EXISTS (SELECT 1 FROM anet_local_twin t WHERE t.meet_id = e.meet_id AND t.sport = 'XC')"
                if twin else "false")
    return f"""
    WITH e0 AS (
        {seen_part}
        UNION ALL
        SELECT meet_id, max(meet_name), max(meet_date), max(state) FROM meets
        WHERE source = 'anet' AND meet_date ~ '{ISO}' AND meet_date BETWEEN %(lo)s AND %(hi)s
        GROUP BY meet_id
    ), e AS (
        SELECT meet_id, max(name) AS name, max(left(d, 10)) AS d, max(state) AS state
        FROM e0 WHERE d ~ '{ISO}' AND left(d, 10) BETWEEN %(lo)s AND %(hi)s GROUP BY meet_id
    )
    SELECT 'anet', e.meet_id, e.name, e.d, e.state,
           CASE WHEN EXISTS (SELECT 1 FROM results r WHERE r.meet_id = e.meet_id AND r.source = 'anet'
                               AND r.result_id < {ALIVE_RESULT_BASE}) THEN 'have'
                WHEN EXISTS (SELECT 1 FROM results r WHERE r.meet_id = e.meet_id AND r.source = 'anet')
                     THEN 'elsewhere'
                WHEN {twin_sql} THEN 'elsewhere'
                ELSE 'missing' END,
           CASE WHEN q.scraped IS NULL THEN 'never queued'
                WHEN q.scraped = 2 THEN 'failed'
                WHEN q.scraped = 3 THEN 'in flight'
                WHEN q.scraped = 4 THEN 'answered as no meet'
                WHEN {empty} IS FALSE THEN 'source says no results yet'
                WHEN q.scraped = 1 THEN 'asked, came back empty'
                ELSE 'queued, not asked yet' END
    FROM e LEFT JOIN meet_queue q ON q.meet_id = e.meet_id AND q.sport = 'XC' AND q.source = 'anet'
    """


def _tfrrsSql(cur):
    hs = _has(cur, "tfrrs_hs_meets")
    hs_part = (f"""UNION ALL SELECT meet_id, name, left(date, 10), NULL FROM tfrrs_hs_meets
                   WHERE sport = 'XC' AND date ~ '{ISO}'""" if hs else "")
    return f"""
    WITH e0 AS (
        SELECT meet_id, meet_name AS name, left(date, 10) AS d, state FROM meets_tfrrs
        WHERE sport = 'XC' AND date ~ '{ISO}'
        {hs_part}
    ), e AS (
        SELECT meet_id, max(name) AS name, max(d) AS d, max(state) AS state FROM e0
        WHERE d BETWEEN %(lo)s AND %(hi)s GROUP BY meet_id
    )
    SELECT 'tfrrs', e.meet_id, e.name, e.d, e.state,
           CASE WHEN EXISTS (SELECT 1 FROM results r WHERE r.meet_id = e.meet_id AND r.source = 'tfrrs')
                THEN 'have' ELSE 'missing' END,
           CASE WHEN q.scraped IS NULL THEN 'never queued'
                WHEN q.scraped = 2 THEN 'failed'
                WHEN q.scraped = 3 THEN 'in flight'
                WHEN q.scraped = 4 THEN 'answered as no meet'
                WHEN q.scraped = 1 THEN 'asked, came back empty'
                ELSE 'queued, not asked yet' END
    FROM e LEFT JOIN meet_queue q ON q.meet_id = e.meet_id AND q.sport = 'XC' AND q.source = 'tfrrs'
    """


def _aliveSql(cur):
    return f"""
    SELECT 'alive', a.alive_id, a.name, left(a.meet_date, 10), a.state,
           CASE WHEN COALESCE(a.landed_n, 0) > 0 THEN 'have'
                WHEN EXISTS (SELECT 1 FROM results r
                             WHERE r.meet_id = ANY(COALESCE(a.anet_ids, '{{}}'::bigint[]))
                               AND r.source = 'anet') THEN 'elsewhere'
                ELSE 'missing' END,
           CASE WHEN a.landed_at IS NULL AND a.state IS NULL THEN 'no state on the index (never picked)'
                WHEN a.landed_at IS NULL AND a.landed_note IS NOT NULL
                     THEN 'tried: ' || left(a.landed_note, 50)
                WHEN a.landed_at IS NULL THEN 'not landed yet'
                ELSE 'landed: ' || left(COALESCE(a.landed_note, '0 rows'), 60) END
    FROM alive_meets a
    WHERE a.sport = 'XC' AND a.meet_date ~ '{ISO}' AND left(a.meet_date, 10) BETWEEN %(lo)s AND %(hi)s
    """


def _timerSql(cur):
    seen = _has(cur, "ext_seen")
    seen_part = (f"""UNION ALL
        SELECT s.url, s.source, s.meet_name, left(s.meet_date, 10), NULL, s.outcome
        FROM ext_seen s
        WHERE s.meet_date ~ '{ISO}' AND COALESCE(s.sport, 'XC') = 'XC'
          AND NOT EXISTS (SELECT 1 FROM meets_ext m WHERE m.url = s.url)""" if seen else "")
    return f"""
    WITH e AS (
        SELECT m.url, m.source, m.meet_name AS name, left(m.meet_date, 10) AS d, m.state,
               'landed: ' || m.status AS outcome
        FROM meets_ext m WHERE m.sport = 'XC' AND m.meet_date ~ '{ISO}'
        {seen_part}
    )
    SELECT 'timer', e.source || ' ' || e.url, e.name, e.d, e.state,
           CASE WHEN e.outcome = 'landed: promoted' THEN 'have'
                WHEN e.outcome = 'landed: twin' THEN 'elsewhere'
                ELSE 'missing' END,
           e.outcome
    FROM e WHERE e.d BETWEEN %(lo)s AND %(hi)s
    """


_MEET_SQL = {"anet": (_anetSql, ("meets", "meet_queue")),
             "tfrrs": (_tfrrsSql, ("meets_tfrrs", "meet_queue")),
             "alive": (_aliveSql, ("alive_meets",)),
             "timer": (_timerSql, ("meets_ext",))}


def meetRows(cur, source, lo, hi):
    build, needs = _MEET_SQL[source]
    missing = [t for t in needs if not _has(cur, t)]
    if missing:
        print(f"   {source}: no {', '.join(missing)} table here")
        return []
    return _run(cur, build(cur), {"lo": lo, "hi": hi})


def week(d):
    try:
        day = dt.date.fromisoformat(d)
    except (TypeError, ValueError):
        return "?"
    return (day - dt.timedelta(days=day.weekday())).isoformat()


def summarise(rows, key):
    """{key: [expected, have, elsewhere, missing]}"""
    out = {}
    for r in rows:
        k = key(r)
        c = out.setdefault(k, [0, 0, 0, 0])
        c[0] += 1
        c[{"have": 1, "elsewhere": 2, "missing": 3}[r[5]]] += 1
    return out


def sectionMeets(cur, a, lo, hi, store):
    print(f"\n== MEETS: listed by the source, {lo} .. {hi} (XC) ==")
    for source in a.source:
        rows = meetRows(cur, source, lo, hi)
        store[source] = rows
        if not rows:
            continue
        tot = summarise(rows, lambda r: source)[source]
        pct = 100.0 * (tot[1] + tot[2]) / tot[0] if tot[0] else 0.0
        print(f"\n-- {source}: expected {tot[0]:,}  have {tot[1]:,}  elsewhere {tot[2]:,}  "
              f"MISSING {tot[3]:,}  ({pct:.1f}% covered)")
        if a.by in ("week", "both"):
            s = summarise(rows, lambda r: week(r[3]))
            _table([(k, *v) for k, v in sorted(s.items())],
                   ("week of", "expected", "have", "elsewhere", "missing"))
        if a.by in ("state", "both"):
            s = summarise(rows, lambda r: (r[4] or "?").upper()[:3])
            print()
            _table(sorted(((k, *v) for k, v in s.items()), key=lambda x: -x[4]),
                   ("state", "expected", "have", "elsewhere", "missing"), a.show)
        why = {}
        for r in rows:
            if r[5] == "missing":
                why[r[6] or "?"] = why.get(r[6] or "?", 0) + 1
        print("\n   why missing:")
        _table(sorted(why.items(), key=lambda x: -x[1]), ("reason", "meets"))
        print(f"\n   missing, newest first (up to {a.show}):")
        miss = sorted((r for r in rows if r[5] == "missing"), key=lambda r: (r[3] or ""), reverse=True)
        _table(((r[3], (r[4] or "")[:3], str(r[1])[:70], (r[2] or "")[:44], (r[6] or "")[:34])
                for r in miss), ("date", "st", "id", "name", "why"), a.show)


# ---------------------------------------------------------------- races within meets

def sectionRaces(cur, a, lo, hi):
    print(f"\n== RACES: a meet we have, with a race of it missing ==")
    if "anet" in a.source:
        print("\n-- anet: divisions athletic.net listed (meets rows) with no result rows,"
              " in meets that have rows")
        rows = _run(cur, f"""
            WITH m AS (
                SELECT div_id, meet_id, meet_name, meet_date, state, division, distance
                FROM meets WHERE source = 'anet' AND meet_date ~ '{ISO}'
                  AND left(meet_date, 10) BETWEEN %(lo)s AND %(hi)s
            ), d AS (
                SELECT m.*, EXISTS (SELECT 1 FROM results r WHERE r.meet_id = m.meet_id
                                    AND r.div_id = m.div_id AND r.source = 'anet') AS has_rows
                FROM m
            )
            SELECT meet_id, max(meet_name), max(meet_date), max(state),
                   count(*) AS divs, count(*) FILTER (WHERE NOT has_rows) AS empty,
                   string_agg(CASE WHEN NOT has_rows THEN COALESCE(division, '?') || ' '
                                   || COALESCE(round(distance)::text, '?') END, '; ')
            FROM d GROUP BY meet_id
            HAVING bool_or(has_rows) AND count(*) FILTER (WHERE NOT has_rows) > 0
            ORDER BY 6 DESC, 3 DESC""", {"lo": lo, "hi": hi})
        n = sum(r[5] for r in rows)
        print(f"   {len(rows):,} meets, {n:,} empty divisions")
        _table(((r[2], (r[3] or "")[:3], r[0], (r[1] or "")[:40], r[4], r[5], (r[6] or "")[:50])
                for r in rows), ("date", "st", "meet", "name", "divs", "empty", "empty divisions"), a.show)
    if "tfrrs" in a.source:
        print("\n-- tfrrs: meets with one gender only (a race of the other may be missing),"
              " and races named for both genders (one division, every runner one gender:"
              " fixed in the parser 2026-11-10, these need a re-scrape)")
        rows = _run(cur, f"""
            WITH b AS (
                SELECT t.meet_id, t.meet_name, left(t.date, 10) AS d, t.state, v.value->>'div_name' AS dn
                FROM meets_tfrrs t, jsonb_each(t.division_distances) v
                WHERE t.sport = 'XC' AND t.date ~ '{ISO}' AND left(t.date, 10) BETWEEN %(lo)s AND %(hi)s
            )
            SELECT meet_id, max(meet_name), max(d), max(state),
                   bool_or(dn ~* '(women|girls)') AS f,
                   bool_or(dn ~* '(\\mmen|\\mboys)') AS m,
                   count(*) FILTER (WHERE dn ~* '(women|girls)' AND dn ~* '(\\mmen|\\mboys)'
                                      AND dn !~* 'mixed race') AS both
            FROM b GROUP BY meet_id""", {"lo": lo, "hi": hi})
        one = [r for r in rows if r[4] != r[5] and not _oneGenderName(r[1])]
        both = [r for r in rows if r[6]]
        print(f"   {len(rows):,} meets; one gender only: {len(one):,}; with a both-gender race: {len(both):,}")
        _table(((r[2], (r[3] or "")[:3], r[0], (r[1] or "")[:50], "F only" if r[4] else "M only")
                for r in one), ("date", "st", "meet", "name", "has"), a.show)
        if both:
            print("   both-gender races (re-scrape these):")
            _table(((r[2], r[0], (r[1] or "")[:50], r[6]) for r in both), ("date", "meet", "name", "races"), a.show)
    if "timer" in a.source and _has(cur, "races_ext"):
        print("\n-- timers: landed meets held back (partial / new / held) and races with no rows")
        rows = _run(cur, f"""
            SELECT m.status, count(DISTINCT m.ext_meet_id),
                   count(r.race_n) FILTER (WHERE COALESCE(r.n_rows, 0) = 0)
            FROM meets_ext m LEFT JOIN races_ext r USING (ext_meet_id)
            WHERE m.sport = 'XC' AND m.meet_date ~ '{ISO}' AND left(m.meet_date, 10) BETWEEN %(lo)s AND %(hi)s
            GROUP BY 1 ORDER BY 2 DESC""", {"lo": lo, "hi": hi})
        _table(rows, ("status", "meets", "empty races"))


def _oneGenderName(name):
    n = (name or "").lower()
    return any(w in n for w in ("women", "girls", "boys")) or " men" in f" {n}"


# ---------------------------------------------------------------- rows within races

def sectionRows(cur, a, lo, hi):
    print(f"\n== ROWS: places the race printed that we have no row for ==")
    print("   ! mostly the SOURCE's own partial posting (owner, 2026-11-10: meet 281168, places"
          " to 295, one team's 25 runners on the page) -- not rows we dropped. A race here is"
          " worth another source's copy (AthleticLIVE, a timer), not a re-scrape.")
    print("   one race = a meet's divisions at one distance (the main feed places boys and girls,"
          " or varsity and JV, run together in one sequence); a race whose highest place is 3+"
          " more than the distinct places any of its divisions holds, and 5%+ of the field")
    rows = _run(cur, f"""
        WITH a AS (
            SELECT m.meet_id, m.div_id, round(COALESCE(m.distance, 0))::int AS dist
            FROM meets m WHERE m.source = 'anet' AND m.meet_date ~ '{ISO}'
              AND left(m.meet_date, 10) BETWEEN %(lo)s AND %(hi)s
        ), ar AS (
            SELECT 'anet'::text AS source, a.meet_id, a.dist AS race, r.div_id, r.place
            FROM a JOIN results r ON r.meet_id = a.meet_id AND r.div_id = a.div_id AND r.source = 'anet'
            WHERE r.result_id < {ALIVE_RESULT_BASE}
        ), tr AS (
            SELECT 'tfrrs'::text, r.meet_id, r.div_id::int, r.div_id, r.place
            FROM meets_tfrrs t JOIN results r ON r.meet_id = t.meet_id AND r.source = 'tfrrs'
            WHERE t.sport = 'XC' AND t.date ~ '{ISO}' AND left(t.date, 10) BETWEEN %(lo)s AND %(hi)s
        ), g AS (
            SELECT source, meet_id, race, count(*) AS n, count(DISTINCT div_id) AS divs,
                   count(DISTINCT place) FILTER (WHERE place > 0) AS placed, max(place) AS top
            FROM (SELECT * FROM ar UNION ALL SELECT * FROM tr) x
            GROUP BY 1, 2, 3
        )
        SELECT source, meet_id, race, divs, n, top, top - placed AS gap FROM g
        WHERE top - placed >= 3 AND (top - placed) >= 0.05 * top AND top < 5000
        ORDER BY gap DESC""", {"lo": lo, "hi": hi})
    by = {}
    for r in rows:
        c = by.setdefault(r[0], [0, 0])
        c[0] += 1
        c[1] += r[6]
    _table(((s, v[0], v[1]) for s, v in sorted(by.items())), ("source", "races", "places missing"))
    _table(((r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows),
           ("source", "meet", "race (m / tfrrs div)", "divs", "rows", "top place", "missing"), a.show)


# ---------------------------------------------------------------- misreads

def sectionMisreads(cur, a, lo, hi):
    print(f"\n== MISREADS: rows taken in wrong, by source ==")
    rows = _run(cur, f"""
        WITH m AS (
            SELECT DISTINCT meet_id, source FROM meets
            WHERE meet_date ~ '{ISO}' AND left(meet_date, 10) BETWEEN %(lo)s AND %(hi)s
            UNION
            SELECT meet_id, 'tfrrs' FROM meets_tfrrs
            WHERE sport = 'XC' AND date ~ '{ISO}' AND left(date, 10) BETWEEN %(lo)s AND %(hi)s
        )
        SELECT r.source, count(*),
               count(*) FILTER (WHERE r.time_seconds IS NULL AND r.status IS NULL) AS no_time_no_status,
               count(*) FILTER (WHERE r.time_seconds <= 0) AS zero_time,
               count(*) FILTER (WHERE NULLIF(trim(r.grade), '') IS NULL) AS no_grade,
               count(*) FILTER (WHERE r.school IS NULL OR r.school IN ('', 'Unknown')) AS no_school,
               count(*) FILTER (WHERE r.place IS NULL) AS no_place,
               count(*) FILTER (WHERE r.div_id IS NULL) AS no_division,
               count(*) FILTER (WHERE r.date IS NULL OR r.date !~ '{ISO}') AS no_date
        FROM m JOIN results r ON r.meet_id = m.meet_id AND r.source = m.source
        GROUP BY 1 ORDER BY 2 DESC""", {"lo": lo, "hi": hi})
    _table(rows, ("source", "rows", "no time+status", "time<=0", "no grade", "no school",
                  "no place", "no division", "no date"))

    print("\n-- divisions with no distance, and meets with no date (all seasons: an undated"
          " meet cannot be put in a season at all)")
    rows = _run(cur, f"""
        SELECT source, count(*) FILTER (WHERE distance IS NULL OR distance <= 0),
               count(DISTINCT meet_id) FILTER (WHERE meet_date IS NULL OR meet_date !~ '{ISO}')
        FROM meets GROUP BY 1 ORDER BY 1""")
    _table(rows, ("source", "divisions no distance", "meets no date"))
    rows = _run(cur, f"""
        SELECT m.meet_id, max(m.meet_name), count(*),
               (SELECT max(r.date) FROM results r WHERE r.meet_id = m.meet_id AND r.source = m.source)
        FROM meets m WHERE m.source = 'anet' AND (m.meet_date IS NULL OR m.meet_date !~ '{ISO}')
        GROUP BY m.meet_id, m.source ORDER BY 4 DESC NULLS LAST LIMIT %(n)s""", {"n": a.show})
    if rows:
        print("   undated anet meets, newest rows first (the rows' own date is what the"
              " meets row lacks):")
        _table(rows, ("meet", "name", "divs", "rows' date"))

    print("\n-- a women's race at the men's distance: a meet whose F division is as long as"
          " its longest M division, 7000 m+ (tfrrs blobs)")
    rows = _run(cur, f"""
        WITH b AS (
            SELECT t.meet_id, t.meet_name, left(t.date, 10) AS d, v.value->>'div_name' AS dn,
                   (v.value->>'distance')::float AS dist
            FROM meets_tfrrs t, jsonb_each(t.division_distances) v
            WHERE t.sport = 'XC' AND t.date ~ '{ISO}'
        )
        SELECT meet_id, max(meet_name), max(d),
               max(dist) FILTER (WHERE dn ~* '(women|girls)') AS f,
               max(dist) FILTER (WHERE dn !~* '(women|girls)' AND dn ~* '(\\mmen|\\mboys)') AS m
        FROM b GROUP BY meet_id
        HAVING max(dist) FILTER (WHERE dn ~* '(women|girls)') >= 7000
           AND max(dist) FILTER (WHERE dn ~* '(women|girls)')
               >= max(dist) FILTER (WHERE dn !~* '(women|girls)' AND dn ~* '(\\mmen|\\mboys)')
        ORDER BY 3 DESC""")
    print(f"   {len(rows):,} meets (all seasons)")
    _table(rows, ("meet", "name", "date", "F m", "M m"), a.show)


# ---------------------------------------------------------------- nightly

NIGHTLY_DDL = """
CREATE TABLE IF NOT EXISTS coverage_nightly (
    run_date   date NOT NULL,
    source     text NOT NULL,
    span       text NOT NULL,          -- 'season' / 'last14'
    expected   integer,
    have       integer,
    elsewhere  integer,
    missing    integer,
    at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_date, source, span)
)"""


def nightly(conn, a, today):
    """One line a source, for the season and the last 14 days, stored in
    coverage_nightly and set against the last run. Prints COVERAGE lines the
    nightly log keeps; exits 1 when a source's 14-day coverage fell 5+ points
    since the last run (the step is off the chain: it only says so)."""
    lo_season = seasonStart(today).isoformat()
    hi = (today - dt.timedelta(days=a.lag)).isoformat()
    lo14 = (today - dt.timedelta(days=14 + a.lag)).isoformat()
    worse = []
    with conn.cursor() as cur:
        cur.execute(NIGHTLY_DDL)
        for source in a.source:
            rows = meetRows(cur, source, lo_season, hi)
            for span, lo in (("season", lo_season), ("last14", lo14)):
                part = [r for r in rows if r[3] and r[3] >= lo]
                e, h, el, m = summarise(part, lambda r: 0).get(0, [0, 0, 0, 0])
                cur.execute("""SELECT expected, have, elsewhere FROM coverage_nightly
                               WHERE source = %s AND span = %s AND run_date < %s
                               ORDER BY run_date DESC LIMIT 1""", (source, span, today))
                prev = cur.fetchone()
                pct = 100.0 * (h + el) / e if e else 100.0
                was = (100.0 * (prev[1] + prev[2]) / prev[0] if prev and prev[0] else None)
                print(f"COVERAGE {source:<6} {span:<7} expected {e:6,}  have {h:6,}  elsewhere {el:5,}  "
                      f"missing {m:5,}  {pct:5.1f}%" + (f"  (last run {was:.1f}%)" if was is not None else ""))
                if span == "last14" and was is not None and e >= 20 and pct < was - 5:
                    worse.append(f"{source} {was:.1f}% -> {pct:.1f}%")
                cur.execute("""INSERT INTO coverage_nightly (run_date, source, span, expected, have,
                                                             elsewhere, missing)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (run_date, source, span) DO UPDATE SET
                                 expected = EXCLUDED.expected, have = EXCLUDED.have,
                                 elsewhere = EXCLUDED.elsewhere, missing = EXCLUDED.missing, at = now()""",
                            (today, source, span, e, h, el, m))
    conn.commit()
    if worse:
        print("COVERAGE FELL (last 14 days): " + "; ".join(worse)
              + " -- scripts/coverage_report.py --section meets --source <it> says which meets and why")
        return 1
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", help="first meet date (default: Aug 1 of this season)")
    ap.add_argument("--until", help="last meet date (default: today less --lag days)")
    ap.add_argument("--lag", type=int, default=2, help="days a meet has to post (default 2)")
    ap.add_argument("--source", default=",".join(SOURCES), help="anet,tfrrs,alive,timer")
    ap.add_argument("--section", default=",".join(SECTIONS), help="meets,races,rows,misreads")
    ap.add_argument("--by", choices=("week", "state", "both"), default="both")
    ap.add_argument("--show", type=int, default=25, help="rows per list")
    ap.add_argument("--nightly", action="store_true",
                    help="one line a source, stored in coverage_nightly (the nightly step)")
    a = ap.parse_args(argv)
    a.source = [s for s in a.source.split(",") if s in SOURCES]
    a.section = [s for s in a.section.split(",") if s in SECTIONS]
    today = dt.date.today()
    lo = a.since or seasonStart(today).isoformat()
    hi = a.until or (today - dt.timedelta(days=a.lag)).isoformat()

    from database import getConn
    with getConn() as conn:
        if a.nightly:
            return nightly(conn, a, today)
        try:
            conn.set_session(readonly=True)
        except Exception:                                       # noqa: BLE001
            pass                                                # read-only by what it runs
        with conn.cursor() as cur:
            store = {}
            if "meets" in a.section:
                sectionMeets(cur, a, lo, hi, store)
            if "races" in a.section:
                sectionRaces(cur, a, lo, hi)
            if "rows" in a.section:
                sectionRows(cur, a, lo, hi)
            if "misreads" in a.section:
                sectionMisreads(cur, a, lo, hi)
        conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
