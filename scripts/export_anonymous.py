#!/usr/bin/env python3
"""
export_anonymous.py -- every result as an anonymous CSV, for someone who
asks for "all the data" (owner, 2026-11-03: a school project).

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/export_anonymous.py --out /tmp/racecast_results.csv.gz
    $P scripts/export_anonymous.py --out /tmp/xc.csv.gz --sport XC --since 2024-08-01
    $P scripts/export_anonymous.py --out /tmp/xc.csv.gz --exact-dates

★ WHAT IS IN IT, ONE ROW PER RESULT
    athlete     a random number: the same runner is the same number all
                through ONE file, and a different number in the next file
    sex         M / F
    level       hs / college / ms / open (the board the row is on)
    grade       as the feed printed it
    state       the school's state
    season      the cross country / track season year
    week        the Monday of the race's week (--exact-dates: the day)
    sport       XC / TF
    event       metres for a run, or the field / hurdle event's name
    race        a random number per race (one division of one meet)
    course      a random number per cross country course
    time_seconds, mark_m

★ WHAT IS NOT, ON PURPOSE
    - No names, no schools, no meet or course names, no source ids: a minor
      is a name, a school and a grade, and an id is one search away from
      the name.
    - Nothing Racecast works out (owner: "do not give what Racecast
      calculates"): no rating, no normalised time, no course difficulty.
    - No one who opted out (person_optout), and the rankings' own row
      filters (duplicates across feeds, wheelchair, impossible times): the
      rows come from ranking_results.

! THE NUMBERS CANNOT BE TURNED BACK INTO PEOPLE. Each export draws a fresh
  secret key, hashes every id with it (HMAC-SHA256), prints nothing of it
  and throws it away. Two exports cannot be joined either.
! THE WEEK, NOT THE DAY, by default: a course, a day and a time is enough
  to find a runner in the public results; a week across a whole state's
  meets mostly is not.
"""
import argparse
import csv
import datetime
import gzip
import hashlib
import hmac
import os
import secrets
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

COLUMNS = ("athlete", "sex", "level", "grade", "state", "season", "week", "sport",
           "event", "race", "course", "time_seconds", "mark_m")
BATCH = 50_000


class Anonymiser:
    """Random-looking, stable-within-one-export numbers for ids. The key is
    drawn here and never leaves the object."""

    def __init__(self, key=None):
        self._key = key or secrets.token_bytes(32)

    def num(self, kind, value):
        if value is None or value == "":
            return ""
        d = hmac.new(self._key, f"{kind}:{value}".encode("utf-8"), hashlib.sha256).digest()
        return str(int.from_bytes(d[:6], "big"))           # 48 bits: no collisions to speak of


def poolParts(pool):
    """('M', 'hs') for 'hs_m'; ('', '') when it says nothing. Pure."""
    p = str(pool or "").split("|", 1)[0].lower()
    level, _, sex = p.rpartition("_")
    if sex not in ("m", "f"):
        return "", level or p
    return sex.upper(), level or ""


def weekOf(day, exact=False):
    """'2026-10-12' for any day of that week (Monday); the day itself with
    exact. '' for no date. Pure."""
    s = str(day or "")[:10]
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return ""
    return d.isoformat() if exact else (d - datetime.timedelta(days=d.weekday())).isoformat()


def eventOf(sport, distance, event_kind):
    """Metres for a flat run; the kind for hurdles, steeple and field. Pure."""
    if event_kind:
        if distance and event_kind in ("hurdles", "steeple"):
            return f"{int(round(float(distance)))}m {event_kind}"
        return str(event_kind)
    if distance:
        return str(int(round(float(distance))))
    return ""


def toRow(r, anon, exact=False):
    """One CSV row from one ranking_results row (a dict). Pure but for anon."""
    sex, level = poolParts(r.get("pool"))
    sport = r.get("sport") or ""
    race = (anon.num("race", f"{sport}:{r.get('meet_id')}:{r.get('div_id')}:{r.get('event_id')}")
            if r.get("meet_id") is not None else "")
    course = anon.num("course", (r.get("course_name") or "").strip().lower()) \
        if sport == "XC" and (r.get("course_name") or "").strip() else ""
    t = r.get("time_seconds")
    mark = r.get("mark")
    return {
        "athlete": anon.num("person", r.get("person_id")),
        "sex": sex, "level": level,
        "grade": r.get("grade") or "",
        "state": (r.get("state") or "").upper(),
        "season": r.get("year") or "",
        "week": weekOf(r.get("race_date"), exact),
        "sport": sport,
        "event": eventOf(sport, r.get("distance"), r.get("event_kind")),
        "race": race, "course": course,
        "time_seconds": "" if t is None or float(t) >= 99999 else f"{float(t):.2f}",
        "mark_m": "" if mark is None else f"{float(mark):.2f}",
    }


_SQL = """
    SELECT rr.sport, rr.person_id, rr.pool, rr.race_date, rr.year, rr.state, rr.grade,
           rr.meet_id, rr.div_id, rr.event_id, rr.time_seconds, rr.distance,
           rr.event_kind, rr.mark,
           CASE WHEN rr.sport = 'XC' THEN m.course_name END AS course_name
    FROM   ranking_results rr
    LEFT   JOIN meets m ON rr.sport = 'XC' AND m.meet_id = rr.meet_id AND m.div_id = rr.div_id
    WHERE  rr.person_id IS NOT NULL
      {optout}
      {sport}
      {since}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="a .csv or .csv.gz path")
    ap.add_argument("--sport", choices=("XC", "TF"))
    ap.add_argument("--since", help="first race date, YYYY-MM-DD")
    ap.add_argument("--exact-dates", action="store_true",
                    help="the race's day instead of its week (easier to trace back to a runner)")
    a = ap.parse_args()

    import psycopg2.extras
    from database import getConn
    anon = Anonymiser()
    opener = gzip.open if a.out.endswith(".gz") else open
    n = 0
    people = set()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.person_optout')")
            has_optout = cur.fetchone()[0] is not None
        sql = _SQL.format(
            optout=("AND NOT EXISTS (SELECT 1 FROM person_optout o WHERE o.person_id = rr.person_id)"
                    if has_optout else ""),
            sport="AND rr.sport = %(sport)s" if a.sport else "",
            since="AND rr.race_date >= %(since)s" if a.since else "")
        # a named cursor streams: the table does not have to fit in memory
        with conn.cursor(name="export_anonymous",
                         cursor_factory=psycopg2.extras.RealDictCursor) as cur, \
                opener(a.out, "wt", newline="") as fh:
            cur.itersize = BATCH
            cur.execute(sql, {"sport": a.sport, "since": a.since})
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for r in cur:
                row = toRow(r, anon, a.exact_dates)
                w.writerow(row)
                people.add(row["athlete"])
                n += 1
                if n % 1_000_000 == 0:
                    print(f"  {n:,} rows", flush=True)
        conn.rollback()
    print(f"[export] {n:,} results, {len(people):,} athletes -> {a.out}  "
          f"(no names, schools, meets, ids or Racecast numbers; the key is gone)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
