#!/usr/bin/env python3
"""
set_race_course.py -- put a cross country race on the course it was really
run on (owner, 2026-11-03: a report that a race was filed at the wrong park).

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/set_race_course.py --race 278515/3000002940809 --course "Other Park"        # DRY RUN
    $P scripts/set_race_course.py --race 278515/3000002940809 --course "Other Park" --write
    $P scripts/set_race_course.py --race 278515/3000002940809 --like 271234/1100000 --write  # copy another race's course
    $P scripts/set_race_course.py --race 278515/3000002940809 --undo --write
    $P scripts/set_race_course.py --list
    $P scripts/set_race_course.py --apply                 # re-apply every pin (the nightly runs this)

★ A PIN, NOT AN EDIT. A re-scrape or a re-land writes the feed's course back
  into meets, so the choice is kept in race_course_pin and put back after
  every scrape (nightly step 04y_course_pins, before the new rows are
  priced). The race's rows are marked for re-pricing so the next normalise
  takes the new course; a full pipeline refits its course and day terms.

★ THE COORDINATES COME WITH THE NAME. A course is a name AND a place
  (course_canonical keys on both), so the race takes the coordinates most
  races filed under that name carry, or those of the --like race. A name no
  race carries is refused unless --lat/--lon give the place.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DDL = """
CREATE TABLE IF NOT EXISTS race_course_pin (
    meet_id     bigint NOT NULL,
    div_id      bigint NOT NULL,
    course_name text   NOT NULL,
    gps_lat     real,
    gps_long    real,
    old_course  text,
    old_lat     real,
    old_long    real,
    note        text,
    pinned_at   timestamptz DEFAULT now(),
    PRIMARY KEY (meet_id, div_id)
)"""


def parseRace(text):
    """(meet_id, div_id) from '278515/3000002940809' or a race URL. Pure."""
    import re
    m = re.search(r"(\d+)/(\d+)\s*$", str(text or "").split("?")[0].split("#")[0].rstrip("/"))
    if not m:
        raise SystemExit(f"--race wants MEET/DIV or a race link, not {text!r}")
    return int(m.group(1)), int(m.group(2))


def placeOf(cur, course):
    """(lat, lon, n) the races filed under this course name most often carry."""
    cur.execute("""SELECT round(gps_lat::numeric, 5), round(gps_long::numeric, 5), count(*)
                   FROM meets WHERE lower(btrim(course_name)) = lower(btrim(%s))
                     AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
                   GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 1""", (course,))
    r = cur.fetchone()
    return (float(r[0]), float(r[1]), int(r[2])) if r else (None, None, 0)


def apply(cur, only=None):
    """Put every pin (or one) back into meets; mark changed races for
    re-pricing. Returns the number of races changed."""
    cur.execute("SELECT to_regclass('public.race_course_pin')")
    if cur.fetchone()[0] is None:
        return 0
    where = "WHERE p.meet_id = %s AND p.div_id = %s" if only else ""
    cur.execute(f"""
        UPDATE meets m SET course_name = p.course_name,
               gps_lat = COALESCE(p.gps_lat, m.gps_lat), gps_long = COALESCE(p.gps_long, m.gps_long)
        FROM   race_course_pin p {where} {'AND' if only else 'WHERE'} m.meet_id = p.meet_id AND m.div_id = p.div_id
          AND  (m.course_name IS DISTINCT FROM p.course_name
                OR (p.gps_lat IS NOT NULL AND m.gps_lat IS DISTINCT FROM p.gps_lat)
                OR (p.gps_long IS NOT NULL AND m.gps_long IS DISTINCT FROM p.gps_long))
        RETURNING m.meet_id, m.div_id
    """, only or ())
    changed = cur.fetchall()
    for meet_id, div_id in changed:
        # the next normalise prices them on the pinned course; the old
        # rating stays shown until then
        cur.execute("""UPDATE results SET normalized_time = NULL
                       WHERE meet_id = %s AND div_id = %s""", (meet_id, div_id))
    return len(changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--race", help="MEET/DIV or the race's link")
    ap.add_argument("--course", help="the course's name as other races carry it")
    ap.add_argument("--like", help="take the course and place of this race (MEET/DIV)")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--note", default="", help="why (the report)")
    ap.add_argument("--undo", action="store_true", help="drop the pin and put the old course back")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply", action="store_true", help="re-apply every pin")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        if a.list:
            cur.execute("SELECT meet_id, div_id, course_name, old_course, note, pinned_at::date "
                        "FROM race_course_pin ORDER BY pinned_at DESC")
            for r in cur.fetchall():
                print(f"  /race/xc/{r[0]}/{r[1]}  {r[3] or '-'} -> {r[2]}  {r[4] or ''}  ({r[5]})")
            conn.commit()
            return 0
        if a.apply:
            n = apply(cur)
            conn.commit()
            print(f"[course pins] {n} race(s) put back on their pinned course")
            return 0
        if not a.race:
            ap.error("--race, --list or --apply")
        meet_id, div_id = parseRace(a.race)
        cur.execute("SELECT meet_name, course_name, gps_lat, gps_long FROM meets "
                    "WHERE meet_id = %s AND div_id = %s LIMIT 1", (meet_id, div_id))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"no race {meet_id}/{div_id} in meets")
        name, old_course, old_lat, old_lon = row
        if a.undo:
            cur.execute("SELECT old_course, old_lat, old_long FROM race_course_pin "
                        "WHERE meet_id = %s AND div_id = %s", (meet_id, div_id))
            pin = cur.fetchone()
            if not pin:
                raise SystemExit("that race has no pin")
            print(f"  {name}: {old_course} -> {pin[0]} (the course before the pin)")
            if a.write:
                cur.execute("DELETE FROM race_course_pin WHERE meet_id = %s AND div_id = %s",
                            (meet_id, div_id))
                cur.execute("UPDATE meets SET course_name = %s, gps_lat = %s, gps_long = %s "
                            "WHERE meet_id = %s AND div_id = %s",
                            (pin[0], pin[1], pin[2], meet_id, div_id))
                cur.execute("UPDATE results SET normalized_time = NULL "
                            "WHERE meet_id = %s AND div_id = %s", (meet_id, div_id))
                conn.commit()
            else:
                conn.rollback()
            return 0
        if a.like:
            lm, ld = parseRace(a.like)
            cur.execute("SELECT course_name, gps_lat, gps_long FROM meets WHERE meet_id = %s "
                        "AND div_id = %s LIMIT 1", (lm, ld))
            like = cur.fetchone()
            if not like or not like[0]:
                raise SystemExit(f"race {lm}/{ld} has no course to copy")
            course, lat, lon, n = like[0], like[1], like[2], None
        elif a.course:
            course = a.course.strip()
            lat, lon, n = placeOf(cur, course)
            if a.lat is not None and a.lon is not None:
                lat, lon = a.lat, a.lon
            if lat is None:
                raise SystemExit(f"no race is filed under {course!r}: check the spelling on its "
                                 f"course page, or give --lat and --lon")
        else:
            raise SystemExit("--course NAME or --like MEET/DIV")
        print(f"  {name} (/race/xc/{meet_id}/{div_id})")
        print(f"    now:  {old_course or '-'}  ({old_lat}, {old_lon})")
        print(f"    pin:  {course}  ({lat}, {lon})" + (f"  -- the place {n} races there carry" if n else ""))
        if not a.write:
            conn.rollback()
            print("  DRY RUN -- --write pins it")
            return 0
        cur.execute("""INSERT INTO race_course_pin (meet_id, div_id, course_name, gps_lat, gps_long,
                                                    old_course, old_lat, old_long, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (meet_id, div_id) DO UPDATE SET course_name = EXCLUDED.course_name,
                           gps_lat = EXCLUDED.gps_lat, gps_long = EXCLUDED.gps_long,
                           note = EXCLUDED.note, pinned_at = now()""",
                    (meet_id, div_id, course, lat, lon, old_course, old_lat, old_lon, a.note or None))
        apply(cur, only=(meet_id, div_id))
        conn.commit()
        print("  pinned. Tonight's nightly re-prices its rows on that course; a full pipeline "
              "refits the course and its race-day term.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
