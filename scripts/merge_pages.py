#!/usr/bin/env python3
"""
merge_pages.py -- the owner joins two athlete pages that are one person.

    /srv/venv/bin/python scripts/merge_pages.py --find "Connor Fanberg"      # the pages under a name
    /srv/venv/bin/python scripts/merge_pages.py OLD KEEP                      # DRY RUN: both pages' facts
    /srv/venv/bin/python scripts/merge_pages.py OLD KEEP --write --note "..."  # OLD's races move to KEEP
    /srv/venv/bin/python scripts/link_profile_school.py --undo OLD            # undo

★ THE SAME MECHANISM AS AN APPROVED "same person" FIX (racecast/fixes.py
  applySamePerson): one decision in profile_school_merge, applied by
  link_profile_school.write, which moves every row, leaves a redirect from
  OLD to KEEP and logs the move -- and re-applies it on every run, so no
  relink undoes it. The fix form needs a claimed account; this is the
  owner's direct door to it (2026-10-11: Connor Fanberg's high-school page
  and his college page). Nothing here detects: the owner names both pages.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                   # noqa: E402


def facts(cur, pid):
    cur.execute("""SELECT name FROM athlete_named WHERE person_id = %(p)s
                   UNION ALL SELECT athlete_name FROM results WHERE person_id = %(p)s
                   LIMIT 1""", {"p": pid})
    r = cur.fetchone()
    cur.execute("""SELECT sport, pool, min(year), max(year), sum(n_races), max(school)
                   FROM athlete_season WHERE person_id = %s GROUP BY sport, pool ORDER BY 3""", (pid,))
    return (r[0] if r else None), cur.fetchall()


def show(cur, pid):
    name, seasons = facts(cur, pid)
    print(f"  {pid}  {name or '(no name)'}  https://racecast.co/athlete/{pid}")
    for sport, pool, y0, y1, n, school in seasons:
        print(f"      {sport} {pool:<12} {y0}-{y1}  {n or 0:>3} races  {school or ''}")
    return name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=int, nargs="?", help="the page that goes away")
    ap.add_argument("keep", type=int, nargs="?", help="the page that keeps every race")
    ap.add_argument("--find", metavar="NAME", help="list the pages under this exact name")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--note", default="owner merge")
    a = ap.parse_args(argv)
    with getConn() as conn:
        with conn.cursor() as cur:
            if a.find:
                # ! THE RESULT ROWS TOO (owner, 2026-10-11: --find missed his
                #   college page): athlete_named is the high school registry;
                #   a tfrrs-only college page has its name on its rows alone
                nm = a.find.strip()
                cur.execute("""SELECT person_id FROM athlete_named WHERE lower(name) = lower(%(n)s)
                               UNION
                               SELECT DISTINCT person_id FROM results
                               WHERE  person_id IS NOT NULL AND lower(btrim(athlete_name)) = lower(%(n)s)
                               UNION
                               SELECT DISTINCT person_id FROM results_tf
                               WHERE  person_id IS NOT NULL AND lower(btrim(athlete_name)) = lower(%(n)s)
                               ORDER  BY 1""", {"n": nm})
                ids = [r[0] for r in cur.fetchall()]
                print(f"{len(ids)} page(s) named {a.find!r}:")
                for pid in ids:
                    show(cur, pid)
                return 0
            if not (a.old and a.keep) or a.old == a.keep:
                ap.error("give two different person ids: OLD KEEP")
            print("moves from:")
            name = show(cur, a.old)
            print("into:")
            show(cur, a.keep)
        if not a.write:
            print("\ndry run -- nothing written; --write to merge")
            return 0
        import link_profile_school as LPS
        out = LPS.write(conn, [(a.old, a.keep, name, a.note)])
        print(f"\nmerged {a.old} -> {a.keep}: {out}")
        print(f"undo: /srv/venv/bin/python scripts/link_profile_school.py --undo {a.old}")
        print("then: sudo systemctl restart xc-predictor (and the next pipeline run rebuilds the boards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
