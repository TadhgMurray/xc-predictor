#!/usr/bin/env python3
"""
link_freshmen.py -- join a college freshman's tfrrs identity to their own
high school career on anet.

    /srv/venv/bin/python scripts/link_freshmen.py               # DRY RUN
    /srv/venv/bin/python scripts/link_freshmen.py --show 80     # more pairs
    /srv/venv/bin/python scripts/link_freshmen.py --years 2024 2025 2026
    /srv/venv/bin/python scripts/link_freshmen.py --write       # link them
    /srv/venv/bin/python scripts/link_freshmen.py --undo        # take it back

★ THE HOLE (owner, 2026-09-25: "all freshman are unknown... they have not
  been linked with anet... especially if anet doesn't have the race so it
  can't link"). Every existing pass joins a tfrrs row to an anet person
  through something the two feeds SHARE -- a canon-linked meet and a time
  (link_idless_tfrrs), or a name unique across the WHOLE corpus
  (link_idless_by_name). A freshman shares nothing: the high school career is
  on anet, the college career on tfrrs, and not one race is in both. So the
  freshman is a stranger to their own history -- no name on the predictor,
  a page holding one result, no high school rows behind their rating.

★ THE EVIDENCE THIS USES IS THE CAREER ITSELF. A person who
    - has a SENIOR high school season on anet in academic year Y-1, and
    - a tfrrs person who first races, in college, in academic year Y,
  with the SAME NAME is almost always one runner who graduated. The step
  from grade 12 to college freshman is the one transition every college
  runner makes, and it is exactly the one the shared-race passes cannot see.

⚠ A WRONG MERGE IS WORSE THAN A MISSED ONE (unlink.py says so, and splits
  rather than merges for that reason). So every guard is on the side of
  leaving a pair alone:
    - the name must be UNIQUE ON BOTH SIDES within that year: exactly one
      anet senior of class Y-1 and exactly one tfrrs first-year in Y carry
      it, counting seniors ALREADY linked (two Jake Smiths is no link, even
      if one of them is taken);
    - two tokens at least, letters only after normalising;
    - the genders, where both are known, agree;
    - the tfrrs person has NO row before Y and NO anet row at all (it is a
      stranger, not someone already joined to something);
    - the anet senior has NO tfrrs row (not already joined to a college).

! REVERSIBLE, ROW BY ROW. --write logs every moved result -- (sport,
  result_id, from, to) -- in person_link_log before moving it, and --undo
  puts back exactly those rows. Derived tables (athlete_season, ratings,
  boards) follow on the next pipeline run.

Read-only without --write / --undo.
"""
import argparse
import datetime
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                  # noqa: E402

RULE = "freshman"
SENIOR = ("12", "12th", "sr", "sr.", "senior")


def academicYear(d=None):
    d = d or datetime.date.today()
    return d.year if d.month >= 8 else d.year - 1


def normName(name):
    """'SMITH, Jake' and 'Jake  Smith' -> 'jake smith'; None when it is not a
    two-token name."""
    n = str(name or "").strip()
    if "," in n:
        last, _, first = n.partition(",")
        n = f"{first} {last}"
    n = re.sub(r"[^a-z ]+", "", n.lower().replace("-", " "))
    n = " ".join(n.split())
    return n if len(n.split()) >= 2 else None


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _step(cur, what, sql, params=None):
    """Run one statement and say how long it took -- a scan of a 200M-row
    table is minutes, and silence reads as a hang."""
    import time
    t0 = time.time()
    print(f"[link]   {what} ...", end="", flush=True)
    cur.execute(sql, params or {})
    print(f" {time.time() - t0:.0f}s"
          + (f", {cur.rowcount:,} rows" if cur.rowcount >= 0 else ""),
          flush=True)


# A college freshman's grade, however the feed spells it. The tfrrs side of a
# pair must carry one in its first season: tfrrs also hosts some high school
# and middle school meets, and the first dry run paired high school seniors
# with "first-years" at Forest Park Jr High and Darnell Cookman MS.
FRESHMAN_RE = r"^(fr|freshman|13)"


def freshmen(cur, years):
    """{y: {key: (raw_name, college, gender, n_rows)}} -- tfrrs identities
    whose first race anywhere is in academic year y, graded as a college
    freshman, with no anet row at all. ONE scan per result table.

    ★ AN IDENTITY IS A person_id, OR A tfrrs ATHLETE ID WHEN THERE IS NONE
      (key 'p:<person_id>' or 'n:<id_system>:<native_id>'). The linking
      passes stamp person_id on tfrrs rows after the fact and none of them
      runs on a schedule, so this season's rows mostly carry only the tfrrs
      athlete id -- the first dry run found 0 freshmen in 2026 for exactly
      that reason. Linking by native_id is how merge_links/stamp_fanout
      write a tfrrs person too."""
    lo, hi = f"{min(years)}-08-01", f"{max(years) + 1}-08-01"
    cur.execute("SET LOCAL work_mem = '512MB'")
    cur.execute("DROP TABLE IF EXISTS lf_t")
    cur.execute("DROP TABLE IF EXISTS lf_early")
    key = ("CASE WHEN person_id IS NOT NULL THEN 'p:' || person_id "
           "ELSE 'n:' || COALESCE(id_system, 'tfrrs') || ':' || native_id END")
    _step(cur, "tfrrs rows in those seasons (a scan of both result tables)", f"""
        CREATE TEMP TABLE lf_t AS
        SELECT k,
               min(person_id)                                     AS person_id,
               min(native_id)                                     AS native_id,
               min(id_system)                                     AS id_system,
               min(date)                                          AS first,
               bool_or(lower(btrim(grade)) ~ %(fr)s)              AS freshman,
               mode() WITHIN GROUP (ORDER BY btrim(athlete_name)) AS name,
               mode() WITHIN GROUP (ORDER BY school)              AS school,
               count(*)                                           AS n
        FROM (
            SELECT {key} AS k, person_id, native_id, 'tfrrs'::text AS id_system,
                   date, grade, athlete_name, school
            FROM   results
            WHERE  source = 'tfrrs'
              AND  (person_id IS NOT NULL OR native_id IS NOT NULL)
              AND  date >= %(lo)s AND date < %(hi)s
            UNION ALL
            SELECT {key} AS k, person_id, native_id, id_system,
                   date, grade, athlete_name, school
            FROM   results_tf
            WHERE  source = 'tfrrs'
              AND  (person_id IS NOT NULL OR native_id IS NOT NULL)
              AND  date >= %(lo)s AND date < %(hi)s
              AND  COALESCE(is_relay, 0) = 0
        ) x
        WHERE  NULLIF(btrim(athlete_name), '') IS NOT NULL
        GROUP  BY k
    """, {"lo": lo, "hi": hi, "fr": FRESHMAN_RE})
    _step(cur, "keeping only college freshmen",
          "DELETE FROM lf_t WHERE NOT COALESCE(freshman, false)")
    cur.execute("CREATE INDEX ON lf_t (person_id)")
    cur.execute("ALTER TABLE lf_t ADD COLUMN y int")
    cur.execute("""UPDATE lf_t SET y = CASE WHEN substring(first, 6, 2) >= '08'
                   THEN substring(first, 1, 4)::int
                   ELSE substring(first, 1, 4)::int - 1 END""")
    # a stranger: nothing earlier than that season and nothing on anet
    _step(cur, "dropping linked people with an anet row or an earlier race", """
        DELETE FROM lf_t t
        WHERE t.person_id IS NOT NULL AND (
              EXISTS (SELECT 1 FROM results r WHERE r.person_id = t.person_id
                        AND (r.date < (t.y::text || '-08-01')
                             OR r.source <> 'tfrrs'))
           OR EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = t.person_id
                        AND (r.date < (t.y::text || '-08-01')
                             OR r.source <> 'tfrrs')))
    """)
    # ! ONE GROUPED SCAN, NOT A PROBE PER CANDIDATE: native_id has no index,
    #   so an EXISTS per unlinked freshman would scan the table each time
    _step(cur, "earliest race of each unlinked one (a scan of both tables)", """
        CREATE TEMP TABLE lf_early AS
        SELECT sys, native_id, min(date) AS first_any FROM (
            SELECT 'tfrrs'::text AS sys, native_id, date FROM results
            WHERE  source = 'tfrrs' AND native_id IN
                   (SELECT native_id FROM lf_t WHERE person_id IS NULL)
            UNION ALL
            SELECT COALESCE(id_system, 'tfrrs'), native_id, date FROM results_tf
            WHERE  source = 'tfrrs' AND native_id IN
                   (SELECT native_id FROM lf_t WHERE person_id IS NULL)
        ) x GROUP BY sys, native_id
    """)
    _step(cur, "dropping unlinked ones with an earlier race", """
        DELETE FROM lf_t t USING lf_early e
        WHERE t.person_id IS NULL AND e.native_id = t.native_id
          AND e.sys = t.id_system
          AND e.first_any < (t.y::text || '-08-01')
    """)
    gender = ("(SELECT g.gender FROM person_gender g "
              "WHERE g.person_id = t.person_id LIMIT 1)"
              if _hasTable(cur, "person_gender") else "NULL::text")
    cur.execute(f"SELECT t.y, t.k, t.name, t.school, {gender}, t.n "
                f"FROM lf_t t WHERE t.y = ANY(%s)", (list(years),))
    out = {y: {} for y in years}
    for y, k, nm, sch, g, n in cur.fetchall():
        out[y][k] = (nm, sch, g, n)
    return out


def seniors(cur, years):
    """{y: {person_id: (name, high_school, gender, has_tfrrs)}} -- anet
    persons with a senior high school row in academic year y - 1. One scan
    per table for every year."""
    lo, hi = f"{min(years) - 1}-08-01", f"{max(years)}-08-01"
    cur.execute("DROP TABLE IF EXISTS lf_a")
    _step(cur, "anet senior rows in the seasons before (a scan of both tables)", """
        CREATE TEMP TABLE lf_a AS
        SELECT person_id,
               CASE WHEN substring(max(date), 6, 2) >= '08'
                    THEN substring(max(date), 1, 4)::int + 1
                    ELSE substring(max(date), 1, 4)::int END      AS y,
               mode() WITHIN GROUP (ORDER BY school)              AS school
        FROM (
            SELECT person_id, date, school FROM results
            WHERE  source = 'anet' AND person_id IS NOT NULL
              AND  date >= %(lo)s AND date < %(hi)s
              AND  lower(btrim(grade)) = ANY(%(sr)s)
            UNION ALL
            SELECT person_id, date, school FROM results_tf
            WHERE  source = 'anet' AND person_id IS NOT NULL
              AND  date >= %(lo)s AND date < %(hi)s
              AND  lower(btrim(grade)) = ANY(%(sr)s)
        ) x
        GROUP  BY person_id
    """, {"lo": lo, "hi": hi, "sr": list(SENIOR)})
    cur.execute("CREATE INDEX ON lf_a (person_id)")
    _step(cur, "their names, genders and any tfrrs rows (index probes)", """
        CREATE TEMP TABLE lf_a2 AS
        SELECT a.y, a.person_id,
               (SELECT concat_ws(' ', btrim(x.first_name), btrim(x.last_name))
                FROM athletes x WHERE x.athlete_id = a.person_id
                ORDER BY (NULLIF(btrim(x.last_name), '') IS NOT NULL) DESC
                LIMIT 1)                                   AS name,
               a.school,
               (SELECT x.gender FROM athletes x
                WHERE x.athlete_id = a.person_id LIMIT 1)  AS gender,
               EXISTS (SELECT 1 FROM results r WHERE r.person_id = a.person_id
                         AND r.source = 'tfrrs')
               OR EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = a.person_id
                         AND r.source = 'tfrrs')           AS has_tfrrs
        FROM lf_a a WHERE a.y = ANY(%(ys)s)
    """, {"ys": list(years)})
    cur.execute("SELECT y, person_id, name, school, gender, has_tfrrs FROM lf_a2")
    out = {y: {} for y in years}
    for y, pid, nm, sch, g, ht in cur.fetchall():
        out[y][pid] = (nm, sch, g, ht)
    return out


def _g(v):
    v = str(v or "").strip().upper()[:1]
    return v if v in ("M", "F") else None


def pairsFor(fresh, senior):
    """[(tfrrs_pid, anet_pid, name, college, high_school)] -- names unique on
    both sides, genders agreeing where known, the senior not already joined."""
    t_by, a_by = defaultdict(list), defaultdict(list)
    for pid, (nm, sch, g, n) in fresh.items():
        k = normName(nm)
        if k:
            t_by[k].append(pid)
    for pid, (nm, sch, g, ht) in senior.items():
        k = normName(nm)
        if k:
            a_by[k].append(pid)       # ! linked seniors count toward ambiguity
    out, skipped = [], defaultdict(int)
    for k, ts in t_by.items():
        as_ = a_by.get(k, [])
        if not as_:
            continue
        if len(ts) != 1 or len(as_) != 1:
            skipped["name not unique"] += 1
            continue
        t, a = ts[0], as_[0]
        if senior[a][3]:
            skipped["senior already on tfrrs"] += 1
            continue
        gt, ga = _g(fresh[t][2]), _g(senior[a][2])
        if gt and ga and gt != ga:
            skipped["gender differs"] += 1
            continue
        out.append((t, a, fresh[t][0], fresh[t][1], senior[a][1]))
    return out, dict(skipped)


def dedupePairs(pairs):
    """Across years: a tfrrs person joins once, and an anet senior claimed by
    two different first-years (a repeated senior year) joins neither."""
    from collections import Counter
    by_anet = Counter(a for _t, a, *_ in {p[0]: p for p in pairs}.values())
    seen, out = set(), []
    for p in pairs:
        if p[0] in seen or by_anet[p[1]] > 1:
            continue
        seen.add(p[0])
        out.append(p)
    return out


LOG_DDL = """
CREATE TABLE IF NOT EXISTS person_link_log (
    sport        text        NOT NULL,
    result_id    bigint      NOT NULL,
    from_person  bigint      NOT NULL,
    to_person    bigint      NOT NULL,
    rule         text        NOT NULL,
    linked_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, result_id, rule)
)"""


def write(conn, pairs):
    """Stamp the anet person onto every row of each tfrrs identity: by
    person_id ('p:') or by tfrrs athlete id ('n:system:id', the rows that
    carry no person_id yet -- as merge_links and stamp_fanout write one)."""
    with conn.cursor() as cur:
        cur.execute(LOG_DDL)
        by_p = [(int(t[2:]), a) for t, a, *_ in pairs if t.startswith("p:")]
        by_n = []
        for t, a, *_ in pairs:
            if t.startswith("n:"):
                _n, system, native = t.split(":", 2)
                by_n.append((system, int(native), a))
        from psycopg2.extras import execute_values
        cur.execute("DROP TABLE IF EXISTS lf_map")
        cur.execute("CREATE TEMP TABLE lf_map (from_person bigint PRIMARY KEY,"
                    " to_person bigint NOT NULL)")
        cur.execute("DROP TABLE IF EXISTS lf_nmap")
        cur.execute("CREATE TEMP TABLE lf_nmap (id_system text, native_id bigint,"
                    " to_person bigint NOT NULL, PRIMARY KEY (id_system, native_id))")
        if by_p:
            execute_values(cur, "INSERT INTO lf_map VALUES %s", by_p)
        if by_n:
            execute_values(cur, "INSERT INTO lf_nmap VALUES %s", by_n)
        moved = {}
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            sysx = ("'tfrrs'" if sport == "XC"
                    else "COALESCE(r.id_system, 'tfrrs')")
            # from_person 0 in the log = "had no person_id" (undo -> NULL)
            cur.execute(f"""
                INSERT INTO person_link_log (sport, result_id, from_person,
                                             to_person, rule)
                SELECT %s, r.result_id, m.from_person, m.to_person, %s
                FROM   {table} r JOIN lf_map m ON r.person_id = m.from_person
                UNION ALL
                SELECT %s, r.result_id, 0, n.to_person, %s
                FROM   {table} r JOIN lf_nmap n
                       ON r.source = 'tfrrs' AND r.person_id IS NULL
                      AND r.native_id = n.native_id AND {sysx} = n.id_system
                ON CONFLICT DO NOTHING
            """, (sport, RULE, sport, RULE))
            cur.execute(f"""
                UPDATE {table} r SET person_id = m.to_person
                FROM   lf_map m WHERE r.person_id = m.from_person
            """)
            a = cur.rowcount
            cur.execute(f"""
                UPDATE {table} r SET person_id = n.to_person
                FROM   lf_nmap n
                WHERE  r.source = 'tfrrs' AND r.person_id IS NULL
                  AND  r.native_id = n.native_id AND {sysx} = n.id_system
            """)
            moved[sport] = a + cur.rowcount
    conn.commit()
    return moved


def undo(conn):
    with conn.cursor() as cur:
        if not _hasTable(cur, "person_link_log"):
            print("[link] nothing to undo")
            return
        back = {}
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            cur.execute(f"""
                UPDATE {table} r SET person_id = NULLIF(l.from_person, 0)
                FROM   person_link_log l
                WHERE  l.sport = %s AND l.rule = %s
                  AND  r.result_id = l.result_id AND r.person_id = l.to_person
            """, (sport, RULE))
            back[sport] = cur.rowcount
        cur.execute("DELETE FROM person_link_log WHERE rule = %s", (RULE,))
    conn.commit()
    print(f"[link] undone: {back}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    now = academicYear()
    ap.add_argument("--years", type=int, nargs="+",
                    default=[now - 2, now - 1, now],
                    help="academic years of the FRESHMAN season (default: "
                         "the last three)")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    a = ap.parse_args()

    with getConn() as conn:
        if a.undo:
            undo(conn)
            return
        all_pairs = []
        with conn.cursor() as cur:
            print(f"[link] academic years {a.years}; each step prints when "
                  f"it finishes", flush=True)
            fresh_by = freshmen(cur, a.years)
            senior_by = seniors(cur, a.years)
            for y in a.years:
                fresh, senior = fresh_by[y], senior_by[y]
                pairs, skipped = pairsFor(fresh, senior)
                print(f"[link] {y}: {len(fresh):,} tfrrs first-years with no "
                      f"anet identity, {len(senior):,} anet seniors of {y - 1}; "
                      f"{len(pairs):,} pairs; skipped {skipped}")
                for t, s, nm, col, hs in pairs[:a.show]:
                    print(f"    {nm:<28} {str(hs)[:30]:<30} -> {str(col)[:28]:<28}"
                          f"  tfrrs {t} -> anet person {s}")
                all_pairs += pairs
        conn.rollback()                       # the temp tables only
        # one tfrrs person, one target, across years (a person is a
        # first-year in exactly one year, so this only guards a re-run)
        uniq = dedupePairs(all_pairs)
        if not a.write:
            print(f"[link] dry run: {len(uniq):,} pairs would be joined; "
                  f"--write to join them")
            return
        moved = write(conn, uniq)
        print(f"[link] joined {len(uniq):,} freshmen to their high school "
              f"careers; rows moved {moved}; logged in person_link_log "
              f"(--undo reverses)")


if __name__ == "__main__":
    main()
