#!/usr/bin/env python3
"""
optout.py -- hide one athlete from the public site at their request.

    /srv/venv/bin/python scripts/optout.py --add 13950482 --note "email 2026-10-11"
    /srv/venv/bin/python scripts/optout.py --remove 13950482
    /srv/venv/bin/python scripts/optout.py --list

★ WHY (owner, 2026-10-11): an athlete asked "remove/unpublish my results".
  The row goes into person_optout and nothing else changes in the database:
  results, ratings and team scores stay exactly as they are, so a team's
  score and every other athlete's rating read the same. The SITE reads the
  table (racecast/person_optout.py): the athlete's own pages answer 410,
  every row naming them reads "Hidden athlete" with no link, and the search
  index, the sitemap and the follow digest leave them out.

! THE SITE READS THE TABLE AT START-UP. After --add or --remove: restart the
  site, then rebuild the search index and the sitemap (printed below), or
  the old entry stays findable until the nightly run rebuilds them.

Read-only with --list (or no arguments).
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import person_optout as optout                           # noqa: E402

# what the owner runs after a change, from the repo root on the server
NEXT_STEPS = """
next:
  1. restart the site so every worker reloads the hidden list:
       sudo systemctl restart xc-predictor
  2. rebuild the search index and the sitemap (or wait for tonight's run,
     steps 13c_search_index / 13d_sitemap):
       cd /srv/xc-predictor
       /srv/venv/bin/python -u racecast/search_index.py
       /srv/venv/bin/python -u racecast/build_sitemap.py
  3. pages the CDN already holds expire on their own (XCP_PAGE_S_MAXAGE);
     purge /athlete/{pid} in Cloudflare to make it immediate.
"""


def add(cur, person_id, note=None):
    optout.ensure(cur)
    cur.execute("""INSERT INTO person_optout (person_id, note) VALUES (%s, %s)
                   ON CONFLICT (person_id) DO UPDATE
                   SET note = COALESCE(EXCLUDED.note, person_optout.note)""",
                (person_id, note))


def remove(cur, person_id):
    optout.ensure(cur)
    cur.execute("DELETE FROM person_optout WHERE person_id = %s", (person_id,))
    return cur.rowcount


def listAll(cur):
    if not optout.present(cur):
        return []
    cur.execute("SELECT person_id, created_at, note FROM person_optout ORDER BY created_at")
    return cur.fetchall()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--add", type=int, metavar="PERSON_ID")
    g.add_argument("--remove", type=int, metavar="PERSON_ID")
    g.add_argument("--list", action="store_true")
    ap.add_argument("--note", help="why (with --add): the request, its date")
    a = ap.parse_args(argv)
    if a.note and not a.add:
        ap.error("--note goes with --add")
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if a.add:
                add(cur, a.add, a.note)
                conn.commit()
                print(f"[optout] {a.add} hidden")
                print(NEXT_STEPS.format(pid=a.add))
                return 0
            if a.remove:
                n = remove(cur, a.remove)
                conn.commit()
                print(f"[optout] {a.remove} " + ("shown again" if n else "was not hidden"))
                if n:
                    print(NEXT_STEPS.format(pid=a.remove))
                return 0
            rows = listAll(cur)
            conn.rollback()
    for pid, at, note in rows:
        print(f"  {pid}  {at:%Y-%m-%d}  {note or ''}")
    print(f"[optout] {len(rows)} hidden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
