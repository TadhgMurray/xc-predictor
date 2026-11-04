#!/usr/bin/env python3
"""
conference_check.py -- who is in a conference on the site, and who ran its
championship. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/conference_check.py NESCAC
    $P scripts/conference_check.py NESCAC --pool college_f --year 2026

★ WHY (owner, 2026-11-04: "there seems to be a big issue with who is
  actually in a conference. The NESCAC has 11 members" -- the board listed
  nine). A college's conference is not published anywhere the feeds carry;
  build_school_units infers it from the conference championship each school
  ran (school_unit.conference), and the boards read it off team_season.
  This prints the three side by side:
    ran       the schools at this season's (else last season's) meets whose
              name says the conference, and how many runners each sent
    stored    each of those schools' school_unit rows: state, college?,
              conference, votes, season it came from
    board     each one's team_season row for the pool: its conference, or
              why it is not on the conference board
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("conference")
    ap.add_argument("--pool", default="college_m")
    ap.add_argument("--year", type=int, help="season (default: the newest on the board)")
    a = ap.parse_args()

    import psycopg2.extras
    from database import getConn

    conf = a.conference.strip()
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '300s'")
        year = a.year
        if year is None:
            cur.execute("""SELECT max(year) AS y FROM team_season
                           WHERE span = 'season' AND scope = 'usa' AND pool = %s
                             AND sport = 'XC'""", (a.pool,))
            year = cur.fetchone()["y"]
        print(f"{conf}, {a.pool}, season {year}")

        # 1. who ran the conference's meets (this season, else last)
        ran = {}
        for yr in (year, year - 1):
            cur.execute("""
                SELECT r.school, count(DISTINCT r.person_id) AS n,
                       min(m.meet_name) AS meet, min(r.date) AS d
                FROM   meets_tfrrs m
                JOIN   results r ON r.source = 'tfrrs' AND r.meet_id = m.meet_id
                WHERE  m.sport = 'XC' AND m.meet_name ~* %(c)s
                  AND  r.date ~ '^(19|20)[0-9]{2}-'
                  AND  r.date BETWEEN %(lo)s AND %(hi)s
                GROUP  BY r.school
                UNION ALL
                SELECT r.school, count(DISTINCT r.person_id), min(m.meet_name), min(r.date)
                FROM   meets m
                JOIN   results r ON r.source = 'anet' AND r.meet_id = m.meet_id
                WHERE  m.meet_name ~* %(c)s
                  AND  r.date ~ '^(19|20)[0-9]{2}-'
                  AND  r.date BETWEEN %(lo)s AND %(hi)s
                GROUP  BY r.school""",
                        {"c": conf, "lo": f"{yr}-08-15", "hi": f"{yr + 1}-08-14"})
            for row in cur.fetchall():
                if row["school"]:
                    got = ran.setdefault(row["school"], dict(row))
                    got["n"] = max(got["n"], row["n"])
            if ran:
                print(f"\nran a meet named like {conf!r} in the {yr} season "
                      f"(e.g. {next(iter(ran.values()))['meet']!r}):")
                break
        for school, row in sorted(ran.items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {school!r:34} {row['n']:>3} runners  {str(row['d'])[:10]}")
        if not ran:
            print(f"\n! no meet named like {conf!r} in {year} or {year - 1}")

        # 2. the board
        cur.execute("""SELECT school, state, conference, division, top5_mean, n_athletes
                       FROM team_season
                       WHERE span = 'season' AND scope = 'usa' AND pool = %s
                         AND sport = 'XC' AND year = %s
                         AND (upper(conference) = upper(%s) OR school = ANY(%s))""",
                    (a.pool, year, conf, list(ran)))
        board = {}
        for r in cur.fetchall():
            board.setdefault(r["school"], []).append(r)
        on = sorted(s for s, rows in board.items()
                    if any((r["conference"] or "").upper() == conf.upper() for r in rows))
        print(f"\non the {conf} board ({len(on)}): {', '.join(on) or '-'}")

        # 3. each school that ran: stored units and board row
        print("\neach school that ran it:")
        for school in sorted(ran, key=lambda s: -ran[s]["n"]):
            cur.execute("""SELECT state, is_college, conference, division, votes, asof, conflict
                           FROM school_unit WHERE school = %s AND sport = 'XC'
                           ORDER BY votes DESC LIMIT 6""", (school,))
            units = cur.fetchall()
            rows = board.get(school) or []
            verdict = ("ON THE BOARD" if school in on else
                       "NOT ON THE BOARD: no team_season row for the pool" if not rows else
                       "NOT ON THE BOARD: its row reads conference "
                       + ", ".join(repr(r["conference"]) for r in rows))
            print(f"  {school!r}: {verdict}")
            for r in rows:
                print(f"      board  state={r['state']} conference={r['conference']!r} "
                      f"division={r['division']!r} top5={r['top5_mean']} "
                      f"athletes={r['n_athletes']}")
            for u in units:
                print(f"      stored state={u['state']} college={u['is_college']} "
                      f"conference={u['conference']!r} division={u['division']!r} "
                      f"votes={u['votes']} asof={u['asof']} conflict={u['conflict']}")
            if not units:
                print("      stored -- no school_unit row under this name")
        conn.rollback()


if __name__ == "__main__":
    main()
