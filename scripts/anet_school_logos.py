#!/usr/bin/env python3
"""
anet_school_logos.py -- school crests from Athletic.net's team images (305).

    python scripts/anet_school_logos.py --url '<template with {team}>' --write

We already store anet's TeamID on every result row, so there is no crawl
here: no team pages are read, no search is run, nothing is discovered. One
image request per school, once, cached forever. Ordered biggest programme
first, so a partial run is still the useful part.

The logos are the SCHOOLS' marks -- anet hosts them, it does not own them
-- so what is at stake is anet's terms and anet's bandwidth, which is why
this runs at one request a second against a single host and never repeats.

--url takes the image URL with {team} where the id goes, or set
XCP_ANET_LOGO_URL. If the first ABORT_AFTER requests all fail the run stops
and says so, rather than spending an evening on a wrong template.

Fills gaps by default; --replace prefers the anet mark over a crest already
scraped from a school's own site.
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scrape_school_logos import (            # noqa: E402
    DDL, Manners, _tableExists, markShared, normalise, record, writeFile)

ABORT_AFTER = 20


def teamIds(cur, replace=False, limit=None, state=None):
    """[(school, state, team_id)] -- the anet team each school's athletes
    actually raced under, biggest programme first.

    The modal team per (school, home state), not per school string: two
    real schools share the name "Kingston" and anet gives them two ids,
    which is exactly the split school_identity already draws."""
    cur.execute(DDL)
    if not _tableExists(cur, "school_identity"):
        raise SystemExit("school_identity is missing; run pipeline step 10b first")
    home = ("LEFT JOIN person_home_state h ON h.person_id = t.person_id"
            if _tableExists(cur, "person_home_state") else "")
    state_expr = "COALESCE(h.state, '')" if home else "''"
    keep = "" if replace else "AND (l.status IS DISTINCT FROM 'ok' OR l.path IS NULL)"
    where_state = "AND si.state = %(state)s" if state else ""
    lim = "LIMIT %(limit)s" if limit else ""
    cur.execute(f"""
        WITH t AS (
            SELECT school, team_id, person_id FROM results
            WHERE  team_id IS NOT NULL AND school IS NOT NULL
            UNION ALL
            SELECT school, team_id, person_id FROM results_tf
            WHERE  team_id IS NOT NULL AND school IS NOT NULL
        ), counted AS (
            SELECT t.school, {state_expr} AS state, t.team_id, count(*) AS n
            FROM   t {home}
            GROUP  BY 1, 2, 3
        ), modal AS (
            SELECT DISTINCT ON (school, state) school, state, team_id
            FROM   counted ORDER BY school, state, n DESC
        )
        SELECT si.school, si.state, modal.team_id
        FROM   school_identity si
        JOIN   modal ON modal.school = si.school AND modal.state = si.state
        LEFT   JOIN school_logo l ON l.school = si.school AND l.state = si.state
        WHERE  si.n_athletes >= 3
          AND  COALESCE(lower(l.override), '') <> 'none'
          {keep} {where_state}
        ORDER  BY si.n_athletes DESC
        {lim}
    """, {"limit": limit, "state": (state or "").upper()})
    return [tuple(r[k] for k in ("school", "state", "team_id"))
            if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("XCP_ANET_LOGO_URL"),
                    help="image URL template, {team} where the id goes")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace", action="store_true",
                    help="prefer the anet mark over a crest already scraped")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--state", default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests (default 1; one host)")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()
    if not (args.write or args.dry_run):
        ap.error("pass --dry-run or --write")
    if not args.url or "{team}" not in args.url:
        ap.error("--url needs the image URL with {team} in it "
                 "(open any anet team page and copy the logo's address)")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            todo = teamIds(cur, args.replace, args.limit, args.state)
            conn.commit()
            print(f"  {len(todo):,} schools with an anet team id, "
                  f"biggest first, {args.rate}s apart "
                  f"(~{len(todo) * args.rate / 3600:.1f} h)", flush=True)

            manners, got, missed = Manners(rate=args.rate), 0, 0
            t0 = time.time()
            for i, (school, state, team) in enumerate(todo, 1):
                url = args.url.format(team=team)
                raw, ctype = manners.get(url)
                png, sha, why = (normalise(raw, ctype=ctype, kind="icon")
                                 if raw is not None else (None, None, ctype))
                if png:
                    got += 1
                    if args.write:
                        name = writeFile(school, state, png, args.dir)
                        record(cur, school, state, name, url, "anet", sha, "ok")
                else:
                    missed += 1
                    if args.write:
                        record(cur, school, state, None, None, None, None,
                               f"none: anet {why}"[:60])
                if i == ABORT_AFTER and got == 0:
                    conn.rollback()
                    raise SystemExit(
                        f"  {ABORT_AFTER} requests, no image ({why}). "
                        f"The URL template is wrong -- nothing was written.")
                if args.write and i % 100 == 0:
                    conn.commit()
                if i % 100 == 0 or args.dry_run:
                    rate = i / max(1e-9, time.time() - t0)
                    print(f"  [{i:,}/{len(todo):,}] {got:,} kept, {missed:,} without"
                          f" · {rate * 3600:,.0f}/h · last {school} ({state}) "
                          f"{'ok' if png else why}", flush=True)
            if args.write:
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by several schools "
                      f"(anet's placeholder lands here)")
            print(f"  done: {got:,} crests, {missed:,} without, "
                  f"{(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
