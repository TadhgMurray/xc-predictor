#!/usr/bin/env python3
"""
diag_race_difficulty.py -- why a race page shows no course difficulty (or
an odd one).

    /srv/venv/bin/python scripts/diag_race_difficulty.py "/race/xc/280314/1116228?r=69975426"

★ WHY (owner, 2026-09-26: "race with no difficulty?" at that URL, and "races
  with 130 results, 130 athletes only run once, getting 20% difficulty").
  The race header finds its difficulty in three joins (app.get_race_header):
  the meet row gives the course name and GPS; course_canonical matches the
  NAME and the GPS to 5 decimals; course_difficulties holds the cell at the
  race's distance rounded to 100 m. This prints each step and says which one
  came up empty -- or, when a difficulty exists, how many of the race's
  runners have any OTHER rated race (the linkage the solve had to place it).
  READ-ONLY.

★ AND WHICH OF THE ENGINE'S CHECKS THE RACE PASSED (owner, 2026-09-26:
  "you chose an arbitrary number and didn't fix the issue. What are the
  checks?"). Step 5 replays bracket_engine.cellStep's rules on this race
  from the database: the voters (top half of the field by rating, and only
  the ones with another XC race in the same season within +-30 days), what
  each voter's other races say about this course, the >= 3 voter floor, the
  n/(n+5) weight, and the course's other race days that the cell is pulled
  toward. It approximates the engine (no form curve, no tilt), so read its
  numbers to a point or so; what it is for is WHO decided the difficulty.

    --scan: the same question over a whole season -- every XC race whose
    published difficulty is at or above --min, with how many of its top half
    could vote. Races that could not vote are showing a prior, not a
    measurement.

        /srv/venv/bin/python scripts/diag_race_difficulty.py --scan --season 2025 --min 0.15
"""
import argparse
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")


WINDOW = 30          # deploy/solve_env.sh XCP_BRACKET_WINDOW
TOP = 0.5            # run_joint --bracket-top
MIN_VOTERS = 3       # bracket_engine.fit min_voters
RACE_SAT = 5.0       # bracket_engine.RACE_SAT
PRIOR_RACES = 2.0    # bracket_engine.PRIOR_RACES
_ISO = r"'^\d{4}-\d{2}-\d{2}'"


def _safeDate(col):
    return f"(CASE WHEN {col} ~ {_ISO} THEN left({col}, 10)::date END)"


def fieldAndVoters(cur, A, meet_id, div_id, src):
    """This race's rows, the top half by rating, and for every runner the
    same person's other XC rows in the same season within the window."""
    cur.execute(f"""
        SELECT r.person_id, r.date, r.normalized_time, r.speed_rating,
               {A._name_sql('r')} AS name
        FROM results r
        {A._athlete_lateral('r')}
        WHERE r.meet_id = %s AND r.div_id = %s
          AND (%s::text IS NULL OR r.source = %s)
          AND r.normalized_time > 0 AND r.person_id IS NOT NULL
    """, (meet_id, div_id, src, src))
    field = cur.fetchall()
    if not field:
        return [], [], {}
    # the engine ranks by rating, a missing one counting as 100
    field.sort(key=lambda r: -(float(r["speed_rating"]) if r["speed_rating"]
                               is not None else 100.0))
    top = field[:math.ceil(TOP * len(field))]
    day = str(field[0]["date"])[:10]
    cur.execute(f"""
        SELECT o.person_id, o.meet_id, o.div_id, o.source, o.date, o.normalized_time
        FROM results o
        WHERE o.person_id = ANY(%s) AND o.normalized_time > 0
          AND left(o.date, 4) = %s
          AND abs({_safeDate('o.date')} - %s::date) <= %s
          AND NOT (o.meet_id = %s AND o.div_id = %s
                   AND o.source IS NOT DISTINCT FROM %s)
    """, ([r["person_id"] for r in field], day[:4], day, WINDOW,
          meet_id, div_id, src))
    others = {}
    for o in cur.fetchall():
        others.setdefault(o["person_id"], []).append(o)
    return field, top, others


def _keep(w, k, prior_races=PRIOR_RACES):
    """What a course with ONE race day keeps of that race's reading, as
    bracket_engine.cellStep computes it: the history is the race shrunk by k,
    and the cell is the race pulled toward that history by prior_races."""
    base = w / (w + k)
    return (w + prior_races * base) / (w + prior_races)


def bracketReading(cur, A, meet_id, div_id, src, hdr):
    """Step 5: the engine's checks, replayed on this race."""
    field, top, others = fieldAndVoters(cur, A, meet_id, div_id, src)
    if not field:
        print("\n  5. no rows with a normalized time: the engine never saw this race")
        return
    linked_all = sum(1 for r in field if r["person_id"] in others)
    voters = [r for r in top if r["person_id"] in others]
    print(f"\n  5. the engine's checks, replayed (window +-{WINDOW} days, same season):")
    print(f"     {len(field)} runners with a normalized time; {linked_all} of them "
          f"have another XC race in the window")
    print(f"     voters = the top half by rating ({len(top)}) who have one: "
          f"{len(voters)}")
    diff_cache = {}

    def diffOf(o):
        k = (o["meet_id"], o["div_id"], o["source"])
        if k not in diff_cache:
            h = A.get_race_header(cur, o["meet_id"], o["div_id"], source=o["source"])
            diff_cache[k] = (h or {}).get("difficulty"), (h or {}).get("meet_name")
        return diff_cache[k]

    readings = []
    same_day = 0
    here_day = str(field[0]["date"])[:10]
    for v in voters:
        comps = []
        for o in others[v["person_id"]]:
            d, name = diffOf(o)
            if d is None:
                continue
            comps.append((o, float(d), name))
            if str(o["date"])[:10] == here_day:
                same_day += 1
        if not comps:
            continue
        a_i = sum(math.log(float(o["normalized_time"])) - math.log1p(d)
                  for o, d, _n in comps) / len(comps)
        r_i = math.log(float(v["normalized_time"])) - a_i
        readings.append(r_i)
        where = "; ".join(f"{str(o['date'])[:10]} {(_n or '?')[:28]} "
                          f"({100 * d:+.1f}%)" for o, d, _n in comps[:3])
        print(f"       {(v['name'] or '?')[:24]:<24} rating {v['speed_rating'] or 0:6.1f}  "
              f"says {100 * math.expm1(r_i):+6.1f}%   vs {where}")
    pub = hdr.get("difficulty")
    n = len(readings)
    print()
    if same_day:
        print(f"     ⚠ {same_day} of those comparisons are races ON THE SAME DAY: "
              "the same race stored twice (two meet ids) votes for itself")
    if n < MIN_VOTERS:
        print(f"     CHECK FAILED: {n} voter(s), the engine needs {MIN_VOTERS}. This "
              "race cast NO vote on its course.")
        print("     So the published number is not this race: it is the course's "
              "other race days\n     (listed below), or its place's, or -- with "
              "none -- the plain cross-country level.")
    else:
        mean_r = sum(readings) / n
        w = n / (n + RACE_SAT)
        print(f"     CHECK PASSED: {n} voters, weight {n}/({n}+{RACE_SAT:g}) = {w:.2f} "
              f"of one race. Their mean says {100 * math.expm1(mean_r):+.1f}%.")
        print(f"     The cell is pulled toward the course's history by "
              f"{PRIOR_RACES:g} races' worth, and that history toward the average "
              "course by the fitted prior k.\n     A course with no other race day "
              "has only this race as its history, so ONLY k holds it back.\n"
              "     Share of this reading it keeps (the rest is the average course):  "
              + "   ".join(f"k={k:g}: {100 * _keep(w, k):.0f}%" for k in (0.25, 1, 4, 8))
              + "\n     k is in the solve log:  "
              "grep -h 'on one race keeping' logs/*/08_golive.log | tail -3")
    if pub is not None:
        print(f"     published difficulty {100 * float(pub):+.1f}%")
    courseHistory(cur, hdr)


def courseHistory(cur, hdr):
    """The other race days at this course and distance cell (anet meets)."""
    if not hdr.get("course_name") or not hdr.get("cell_distance_m"):
        return
    cur.execute(f"""
        SELECT m.meet_id, m.div_id, m.meet_name,
               (SELECT min(r.date) FROM results r WHERE r.meet_id = m.meet_id
                  AND r.div_id = m.div_id AND r.source = 'anet') AS date,
               (SELECT count(*) FROM results r WHERE r.meet_id = m.meet_id
                  AND r.div_id = m.div_id AND r.source = 'anet'
                  AND r.normalized_time > 0) AS n
        FROM meets m
        JOIN course_canonical cc
          ON cc.course_name = m.course_name
         AND round(cc.gps_lat::numeric, 5) = round(m.gps_lat::numeric, 5)
         AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
        LEFT JOIN dist_override dov ON dov.meet_id = m.meet_id AND dov.div_id = m.div_id
        WHERE cc.canonical_id = %s
          AND (round(COALESCE(dov.distance::real, m.distance) / 100.0) * 100)::int = %s
        ORDER BY 4
    """, (hdr["canonical_id"], hdr["cell_distance_m"]))
    rows = [r for r in cur.fetchall() if r["n"]]
    print(f"\n  6. every race day on this course at {hdr['cell_distance_m']} m "
          f"(anet; the course's history): {len(rows)}")
    for r in rows[:40]:
        print(f"     {str(r['date'])[:10]}  /race/xc/{r['meet_id']}/{r['div_id']}  "
              f"{r['n']:>4} rows  {(r['meet_name'] or '')[:40]}")
    if len(rows) > 40:
        print(f"     ... {len(rows) - 40} more")


def scan(season, min_diff, min_runners, limit):
    import psycopg2.extras
    import app as A
    with A.getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                WITH races AS (
                    SELECT meet_id, div_id, source, min(date) AS date, count(*) AS n
                    FROM results
                    WHERE left(date, 4) = %(season)s AND normalized_time > 0
                    GROUP BY 1, 2, 3 HAVING count(*) >= %(min_n)s)
                SELECT r.meet_id, r.div_id, r.source, r.date, r.n,
                       {A._xc_course_sql('r')} AS course_name,
                       cd.difficulty
                FROM races r
                LEFT JOIN meets m
                       ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                      AND m.source = r.source
                {A._tfrrs_join('r')}{A._dist_override_join('r')}
                JOIN course_canonical cc
                  ON cc.course_name = {A._xc_course_sql('r')}
                 AND round(cc.gps_lat::numeric, 5)
                   = round(COALESCE(m.gps_lat, mt.gps_lat)::numeric, 5)
                 AND round(cc.gps_long::numeric, 5)
                   = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
                JOIN course_difficulties cd
                  ON cd.canonical_id = cc.canonical_id
                 AND cd.distance_m = (round({A._xc_distance_sql('r')} / 100.0) * 100)::int
                WHERE cd.difficulty >= %(min)s
                ORDER BY cd.difficulty DESC
                LIMIT %(limit)s
            """, {"season": str(season), "min_n": min_runners, "min": min_diff,
                  "limit": limit})
            hits = cur.fetchall()
            print(f"{len(hits)} XC races in {season} with {min_runners}+ rows and a "
                  f"published difficulty of {100 * min_diff:+.0f}% or more:\n")
            print(f"  {'difficulty':>10}  {'rows':>5}  {'linked':>6}  {'voters':>6}  "
                  f"verdict        race")
            for h in hits:
                field, top, others = fieldAndVoters(cur, A, h["meet_id"],
                                                    h["div_id"], h["source"])
                linked = sum(1 for r in field if r["person_id"] in others)
                nv = sum(1 for r in top if r["person_id"] in others)
                verdict = ("NO VOTE" if nv < MIN_VOTERS else
                           "thin vote" if nv < 10 else "measured")
                print(f"  {100 * float(h['difficulty']):>+9.1f}%  {h['n']:>5}  "
                      f"{linked:>6}  {nv:>6}  {verdict:<13}  "
                      f"/race/xc/{h['meet_id']}/{h['div_id']}  {str(h['date'])[:10]}  "
                      f"{(h['course_name'] or '')[:30]}")
            print("\n  NO VOTE: fewer than 3 of the top half had another race within "
                  f"+-{WINDOW} days, so the number\n  is the course's history or "
                  "the level. Run a race through this script for the detail.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--min", type=float, default=0.15,
                    help="--scan: lowest published difficulty to list (0.15 = +15%%)")
    ap.add_argument("--min-rows", type=int, default=50)
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    if a.scan:
        import datetime
        scan(a.season or datetime.date.today().year, a.min, a.min_rows, a.limit)
        return
    if not a.path:
        ap.error("give a /race/xc/<meet>/<div> path, or --scan")
    m = re.search(r"/race/xc/(\d+)/(\d+)", a.path)
    if not m:
        ap.error("give a /race/xc/<meet>/<div> path")
    meet_id, div_id = int(m.group(1)), int(m.group(2))
    args = dict(re.findall(r"[?&](\w+)=([^&]+)", a.path))

    import psycopg2.extras
    import app as A
    with A.getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            src, idx, others = A._xc_meet_sources(cur, meet_id, args, div_id)
            print(f"race {meet_id}/{div_id}: source picked {src!r} "
                  f"(alt {idx}; {len(others)} other meet(s) share this id)")
            hdr = A.get_race_header(cur, meet_id, div_id, source=src)
            if not hdr:
                print("  no header at all -- no results for this race and source")
                return
            print(f"  header: course {hdr.get('course_name')!r}, distance "
                  f"{hdr.get('distance')}, difficulty {hdr.get('difficulty')}, "
                  f"cell distance {hdr.get('cell_distance_m')}, source {hdr.get('source')}")

            cur.execute("""SELECT course_name, gps_lat, gps_long, distance, source
                           FROM meets WHERE meet_id = %s AND div_id = %s""",
                        (meet_id, div_id))
            mrows = cur.fetchall()
            cur.execute("""SELECT venue_name AS course_name, gps_lat, gps_long,
                                  NULL::real AS distance, 'tfrrs' AS source
                           FROM meets_tfrrs WHERE meet_id = %s AND sport = 'XC'""",
                        (meet_id,))
            mrows += cur.fetchall()
            print(f"\n  1. meet rows ({len(mrows)}):")
            for r in mrows:
                print(f"     {r['source']:<6} course {r['course_name']!r}  gps "
                      f"({r['gps_lat']}, {r['gps_long']})  distance {r['distance']}")
            if not mrows:
                print("     NONE -- no meet row, so no course and no GPS: no difficulty")
            names = sorted({r["course_name"] for r in mrows if r["course_name"]})
            if not names:
                print("     no course name on any meet row: no difficulty")
                return

            cur.execute("""SELECT canonical_id, course_name, gps_lat, gps_long
                           FROM course_canonical WHERE course_name = ANY(%s)""",
                        (names,))
            canon = cur.fetchall()
            print(f"\n  2. course_canonical rows with that name ({len(canon)}):")
            gps = {(round(float(r['gps_lat']), 5), round(float(r['gps_long']), 5))
                   for r in mrows if r['gps_lat'] is not None and r['gps_long'] is not None}
            for c in canon:
                key = (round(float(c['gps_lat']), 5), round(float(c['gps_long']), 5)) \
                    if c['gps_lat'] is not None else None
                hit = "MATCHES this meet's GPS" if key in gps else "gps differs"
                print(f"     canon {c['canonical_id']}  ({c['gps_lat']}, "
                      f"{c['gps_long']})  {hit}")
            if not canon:
                print("     NONE -- the course was never canonicalised (it has no "
                      "cell in any solve)")
            matched = [c for c in canon if c['gps_lat'] is not None and
                       (round(float(c['gps_lat']), 5),
                        round(float(c['gps_long']), 5)) in gps]
            if canon and not matched:
                print("     none matches this meet's GPS to 5 decimals: THIS is why "
                      "the page shows no difficulty")

            ids = [c["canonical_id"] for c in (matched or canon)]
            if ids:
                # SELECT *: the live table's columns beyond these three vary
                cur.execute("""SELECT * FROM course_difficulties
                               WHERE canonical_id = ANY(%s)
                               ORDER BY canonical_id, distance_m""", (ids,))
                cells = cur.fetchall()
                print(f"\n  3. course_difficulties cells for those courses ({len(cells)}):")
                want = (round(float(hdr['distance']) / 100.0) * 100
                        if hdr.get('distance') else None)
                for c in cells:
                    mark = "  <- this race's distance" if c['distance_m'] == want else ""
                    extra = "  ".join(f"{k} {c[k]}" for k in c
                                      if k.startswith("n_"))
                    print(f"     canon {c['canonical_id']}  {c['distance_m']} m  "
                          f"difficulty {c['difficulty']}  {extra}{mark}")
                if want and not any(c['distance_m'] == want for c in cells):
                    print(f"     no cell at {want} m: the solve did not place a "
                          "cell here (too few linked runners?), so no difficulty")

            # the linkage the solve had: runners with ANY other rated race
            cur.execute("""
                SELECT count(DISTINCT r.person_id) AS runners,
                       count(DISTINCT r.person_id) FILTER (WHERE EXISTS (
                           SELECT 1 FROM results o
                           WHERE o.person_id = r.person_id
                             AND o.speed_rating IS NOT NULL
                             AND NOT (o.meet_id = r.meet_id AND o.div_id = r.div_id)))
                         AS linked_xc,
                       count(DISTINCT r.person_id) FILTER (WHERE EXISTS (
                           SELECT 1 FROM results_tf o
                           WHERE o.person_id = r.person_id
                             AND o.speed_rating IS NOT NULL)) AS linked_tf
                FROM results r
                WHERE r.meet_id = %s AND r.div_id = %s
                  AND (%s::text IS NULL OR r.source = %s)
                  AND r.person_id IS NOT NULL
            """, (meet_id, div_id, src, src))
            lk = cur.fetchone()
            print(f"\n  4. linkage: {lk['runners']} runners with a person; "
                  f"{lk['linked_xc']} have another rated XC race, "
                  f"{lk['linked_tf']} have a rated track race")
            if lk["runners"] and not lk["linked_xc"] and not lk["linked_tf"]:
                print("     NOBODY here has another rated race: the course cannot "
                      "be placed from this race alone -- any difficulty it shows "
                      "is a prior, not a measurement")
            if hdr.get("canonical_id"):
                bracketReading(cur, A, meet_id, div_id, src, hdr)


if __name__ == "__main__":
    main()
