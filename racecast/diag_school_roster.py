#!/usr/bin/env python3
"""
diag_school_roster.py -- why a school page shows the school but not its people.

    python racecast/diag_school_roster.py "Springville High"
    python racecast/diag_school_roster.py "Springville High" --year 2026
    python racecast/diag_school_roster.py --like springville     # find the name
    python racecast/diag_school_roster.py "Springville High" --deep

★ THE REPORT (owner, 2026-09-21): "all the ppl that are in a school are not
  actually being counted/shown for that school. I see that school on the
  athlete page but they are not on the school page."

★ THE CHAIN THAT CAN DROP THEM, which is what this walks. The school page's
  roster is racecast/school.schoolRoster, and it reads athlete_season:

      results / results_tf          every row as scraped, school string and
        |                           anet team_id on it -- the athlete page
        |                           renders from here, so it always has the
        |                           school
        v
      team_pool.kind                build_team_pool.classify's verdict for
        |                           that team_id
        v
      pool_resolve.resolvePool      kind='pro' arrives as team_pro, UNGATED,
        |                           and sets is_pro -> pool 'pro_m'/'pro_f'
        v
      build_ranking_results         isRankablePool REFUSES a pro pool: "a
        |                           pro_m or pro_f rating is the athlete's
        |                           standing among professionals and has no
        |                           board on the site"
        v
      ranking_results               -- no row
        v
      athlete_season                derived from ranking_results -- no row
        v
      the school page roster        EMPTY, for a school whose athlete pages
                                    all still name it

  So a school pooled pro loses its ENTIRE roster from its own page while
  every one of its athletes still shows the school. That is one bug with two
  faces, and until 2026-09-21 the commonest way in was the <15-athlete rule
  reaching a K-12 school (build_team_pool._SMALL_EXEMPT_LEVELS).

⚠ AND THE HEADER LIES ABOUT IT, WHICH IS WHY IT READS AS "not counted".
  schoolHeader falls back to ranking_results and then to a raw scan of
  results when athlete_season has nothing -- so the page prints "N athletes"
  from the raw rows above an empty roster table. Section C prints all three
  numbers side by side; when they disagree, the disagreement IS the answer.

★ AND THE POOL IS ONLY ONE OF FOUR WAYS TO EMPTY A ROSTER, so section D
  runs the page's OWN filters and prints the drop at each one. The school
  route forces two of them on a visitor who asked for neither:

      app.py:4630   if chips and not state:  state = primary_state
      app.py:4664   if lchips and not level: level = lchips[0]["level"]

  so a namesake cluster's state, or a level chip that came out wrong, can
  filter away a roster that athlete_season holds in full. The third is the
  default season: the page opens on XC of `currentSeason`, which is
  max(year) over the NAME -- a namesake's newer season sets the year for
  everyone. Section D imports schoolRoster, stateChips, levelChips and
  currentSeason rather than reimplementing them, so this cannot drift from
  what the page does.

! CHEAP BY DEFAULT. Sections A-C touch team_pool, team_identity,
  athlete_season and ranking_results only -- all small or indexed on school.
  `results.school` has NO index (scripts/database.py builds three, on
  athlete_id, meet_id and normalized_time), so the raw count is a seq scan of
  both feeds and lives behind --deep with a statement_timeout on it. A query
  in this tree once blocked the whole site for ten hours; that is the reason
  for the flag, not caution for its own sake.

! NOTHING IS WRITTEN. Every statement here reads.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEEP_TIMEOUT_MS = 300_000          # 5 minutes, then it gives up rather than sit


def _val(row, i, key):
    return row[key] if isinstance(row, dict) else row[i]


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(_val(got, 0, "to_regclass"))


# ------------------------------------------------------------------ #
#  find the name first -- a page is reached by an exact string
# ------------------------------------------------------------------ #
def findNames(cur, like, show):
    """Schools whose name matches, from athlete_season and team_identity.

    ! THE EXACT STRING IS THE KEY. schoolRoster filters `s.school = %(school)s`,
      so a name that is off by a suffix or a comma is a different school to
      this page. Searching both tables shows a name that team_identity knows
      and athlete_season does not -- which is itself the bug being looked for.
    """
    pat = f"%{like}%"
    out = []
    if _tableExists(cur, "athlete_season"):
        cur.execute("""
            SELECT school, count(DISTINCT person_id) AS n
            FROM   athlete_season
            WHERE  school ILIKE %(pat)s
            GROUP  BY school ORDER BY n DESC LIMIT %(show)s
        """, {"pat": pat, "show": show})
        out.append(("athlete_season (what the roster reads)", cur.fetchall()))
    if _tableExists(cur, "team_identity"):
        cur.execute("""
            SELECT ti.school, count(*) AS n
            FROM   team_identity ti
            WHERE  ti.school ILIKE %(pat)s
            GROUP  BY ti.school ORDER BY n DESC LIMIT %(show)s
        """, {"pat": pat, "show": show})
        out.append(("team_identity (anet team ids)", cur.fetchall()))
    return out


# ------------------------------------------------------------------ #
#  A -- what team_pool says about this school's team ids
# ------------------------------------------------------------------ #
def poolVerdicts(cur, school):
    if not (_tableExists(cur, "team_pool") and _tableExists(cur, "team_identity")):
        return None
    cur.execute("""
        SELECT tp.team_id, tp.kind, tp.level, tp.n_athletes, tp.n_rows,
               tp.n_pros, tp.reason, ti.state
        FROM   team_identity ti
        JOIN   team_pool tp ON tp.team_id = ti.team_id
        WHERE  lower(btrim(ti.school)) = lower(btrim(%(school)s))
        ORDER  BY tp.n_rows DESC
    """, {"school": school})
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  B -- what the site's own tables hold for the name
# ------------------------------------------------------------------ #
def seasonRows(cur, school, year, sport):
    """(athlete_season, ranking_results) distinct-athlete counts per season."""
    out = {}
    for table in ("athlete_season", "ranking_results"):
        if not _tableExists(cur, table):
            out[table] = None
            continue
        where = ["school = %(school)s"]
        if year is not None:
            where.append("year = %(year)s")
        if sport is not None:
            where.append("sport = %(sport)s")
        cur.execute(f"""
            SELECT year, sport, count(DISTINCT person_id) AS n
            FROM   {table}
            WHERE  {' AND '.join(where)}
            GROUP  BY year, sport ORDER BY year DESC, sport
        """, {"school": school, "year": year, "sport": sport})
        out[table] = cur.fetchall()
    return out


def poolsSeen(cur, school):
    """★ THE POOL THE ROWS LANDED IN, when any of them landed at all. A school
    whose seasons all read pro_m/pro_f is pooled pro; one with no rows at all
    was refused before ranking_results and this comes back empty -- which is
    why section A is the one that answers it."""
    if not _tableExists(cur, "ranking_results"):
        return []
    cur.execute("""
        SELECT pool, count(DISTINCT person_id) AS n
        FROM   ranking_results
        WHERE  school = %(school)s
        GROUP  BY pool ORDER BY n DESC
    """, {"school": school})
    return cur.fetchall()


# ------------------------------------------------------------------ #
#  C -- the raw rows, behind the flag
# ------------------------------------------------------------------ #
def rawCount(cur, school, timeout_ms=DEEP_TIMEOUT_MS):
    cur.execute("SET LOCAL statement_timeout = %s", (timeout_ms,))
    cur.execute("""
        SELECT count(DISTINCT person_id) AS n FROM (
            SELECT person_id FROM results    WHERE school = %(school)s
            UNION ALL
            SELECT person_id FROM results_tf WHERE school = %(school)s
        ) u
    """, {"school": school})
    row = cur.fetchone()
    return int(_val(row, 0, "n") or 0)


# ------------------------------------------------------------------ #
#  D -- the page's own filters, run in the page's own order
# ------------------------------------------------------------------ #
def pageFilters(cur, school, sport, args):
    """What /school/<name>?sport=<sport> would actually show, stage by stage.

    ★ IMPORTED, NOT REIMPLEMENTED. Every function here is the one app.py
      calls; a copy of the level predicate in this file would be a second
      implementation of the page and would disagree with it within a week.
    """
    from school import schoolRoster, currentSeason
    from school_identity import stateChips, levelChips, levelOf

    year = args.year if args.year is not None else currentSeason(cur, school, sport)
    if year is None:
        print(f"     {sport}: athlete_season has no season at all for this "
              f"name -- see A and B")
        return

    chips, primary = stateChips(cur, school)
    state = primary if chips else None
    lchips = levelChips(cur, school, state or primary)
    level = lchips[0]["level"] if lchips else None

    # carry=False: the carry-forward pass only ADDS rows, and mixing it in
    # would hide a filter that had already emptied the raced roster.
    unfiltered = schoolRoster(cur, school, year, sport, carry=False)
    filtered = schoolRoster(cur, school, year, sport, state=state,
                            primary=primary, carry=False)
    after_level = ([r for r in filtered if levelOf(r.get("pool")) in (None, level)]
                   if level else filtered)

    def _drop(before, after):
        """! ANY DROP IS REPORTED, not only a drop to zero. "the people in a
           school are not being counted" is the partial case too -- a chip
           that hides half a roster is the same bug, seen from closer up."""
        gone = len(before) - len(after)
        return f"   <-- {gone:,} dropped here" if gone else ""

    print(f"     {sport} {year}  (the page's default season for this name)")
    print(f"       no filters                       {len(unfiltered):>6,}")
    print(f"       + state chip {str(state or 'none'):<19} "
          f"{len(filtered):>6,}{_drop(unfiltered, filtered)}")
    print(f"       + level chip {str(level or 'none'):<19} "
          f"{len(after_level):>6,}{_drop(filtered, after_level)}")
    if chips:
        print(f"       state chips: "
              + ", ".join(f"{c['state']}({c.get('n', '?')})"
                          for c in chips[:6]))
    if lchips:
        print(f"       level chips: "
              + ", ".join(f"{c['level']}({c.get('n', '?')})"
                          for c in lchips[:6]))
    if unfiltered and not after_level:
        print("\n       ⚠ athlete_season HOLDS this roster and the page "
              "filters it away.\n"
              "         That is not the pooling -- it is the forced chip "
              "above (app.py:4630/4664).")


def report(cur, args):
    school = args.school
    print(f"\n  school: {school!r}")

    # ---- A
    print("\n  A  team_pool -- the verdict on this school's anet team ids")
    rows = poolVerdicts(cur, school)
    if rows is None:
        print("     team_pool or team_identity is missing; build them first.")
    elif not rows:
        print("     no team id in team_identity carries this exact name.")
        print("     (--like finds the spelling the tables actually use)")
    else:
        print(f"     {'team':>8} {'st':<3} {'kind':<8} {'level':<8} "
              f"{'ath':>6} {'pros':>5} {'rows':>9}  reason")
        n_pro = 0
        for r in rows:
            tid, kind, level, nath, nrows, npros, reason, state = (
                _val(r, 0, "team_id"), _val(r, 1, "kind"), _val(r, 2, "level"),
                _val(r, 3, "n_athletes"), _val(r, 4, "n_rows"),
                _val(r, 5, "n_pros"), _val(r, 6, "reason"), _val(r, 7, "state"))
            if kind == "pro":
                n_pro += 1
            print(f"     {tid:>8} {str(state or '--'):<3} {str(kind):<8} "
                  f"{str(level or '--'):<8} {nath:>6} {npros:>5} "
                  f"{nrows:>9,}  {reason}")
        if n_pro:
            print(f"\n     ⚠ {n_pro} of {len(rows)} team ids are pooled PRO. "
                  f"Every row on a pro team id is\n"
                  f"       refused by build_ranking_results.isRankablePool, so "
                  f"those athletes have\n"
                  f"       no athlete_season row and cannot appear on this "
                  f"school's roster --\n"
                  f"       while their own athlete pages, which render from "
                  f"results, still name it.")

    # ---- B
    print("\n  B  the tables the page reads")
    seen = seasonRows(cur, school, args.year, args.sport)
    for table in ("athlete_season", "ranking_results"):
        got = seen.get(table)
        if got is None:
            print(f"     {table:<16} MISSING")
            continue
        total = sum(int(_val(r, 2, "n")) for r in got)
        print(f"     {table:<16} {total:>7,} athlete-seasons over "
              f"{len(got)} (year, sport)")
        for r in got[:args.show]:
            print(f"       {_val(r, 0, 'year')} {_val(r, 1, 'sport'):<3} "
                  f"{int(_val(r, 2, 'n')):>6,}")
        if len(got) > args.show:
            print(f"       ... {len(got) - args.show} more")

    pools = poolsSeen(cur, school)
    if pools:
        print("\n     pools its rows landed in:")
        for r in pools:
            print(f"       {str(_val(r, 0, 'pool')):<22} "
                  f"{int(_val(r, 1, 'n')):>6,}")

    # ---- C
    print("\n  C  the raw rows (what the athlete page renders from)")
    if not args.deep:
        print("     skipped. results.school has no index, so this is a seq "
              "scan of both feeds;\n     pass --deep to run it with a "
              f"{DEEP_TIMEOUT_MS // 1000}s timeout.")
    else:
        print("     scanning results and results_tf ...", flush=True)
        n = None
        try:
            n = rawCount(cur, school, args.timeout * 1000)
        except Exception as exc:                          # noqa: BLE001
            print(f"     gave up: {type(exc).__name__}: {exc}")
            cur.connection.rollback()
        seasons = seen.get("athlete_season") or []
        n_season = len({_val(r, 0, "year") for r in seasons})
        a_total = sum(int(_val(r, 2, "n")) for r in seasons)
        if n is not None:
            print(f"     {n:,} distinct athletes have raced under this name")
        print(f"     athlete_season holds {a_total:,} athlete-seasons across "
              f"{n_season} season(s)")
        if n and not a_total:      # noqa: SIM102 -- n is None when it gave up
            print("\n     ⚠ THIS IS THE REPORTED SYMPTOM. The rows exist and "
                  "the roster is empty.\n"
                  "       schoolHeader falls through to this same raw scan "
                  "when athlete_season\n"
                  "       has nothing, so the page prints an athlete COUNT "
                  "above an empty table.\n"
                  "       Section A says why the rows never reached "
                  "athlete_season.")

    # ---- D
    print("\n  D  the page's own filters, in the page's own order")
    for sport in (("XC", "TF") if args.sport is None else (args.sport,)):
        try:
            pageFilters(cur, school, sport, args)
        except Exception as exc:                          # noqa: BLE001
            print(f"     {sport}: {type(exc).__name__}: {exc}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("school", nargs="?", help="the exact school name the page uses")
    ap.add_argument("--like", help="search for the name instead of reporting on one")
    ap.add_argument("--year", type=int, help="narrow section B to one stored year")
    ap.add_argument("--sport", choices=("XC", "TF"))
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--deep", action="store_true",
                    help="also count the raw results rows (seq scan; see the "
                         "header)")
    ap.add_argument("--timeout", type=int, default=DEEP_TIMEOUT_MS // 1000,
                    help="seconds before --deep gives up (default 300)")
    args = ap.parse_args()
    if not args.school and not args.like:
        ap.error("name a school, or pass --like to search for one")

    # ! RealDictCursor, BECAUSE SECTION D IMPORTS THE PAGE'S OWN FUNCTIONS
    #   and schoolRoster reads r["person_id"]. The route uses the same
    #   factory; a tuple cursor here would make D raise instead of measure.
    import psycopg2.extras
    from database import getConn
    with getConn() as conn:
        with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.like:
                for title, rows in findNames(cur, args.like, args.show):
                    print(f"\n  {title}")
                    if not rows:
                        print("     nothing matched")
                    for r in rows:
                        print(f"     {int(_val(r, 1, 'n')):>7,}  "
                              f"{_val(r, 0, 'school')}")
                if not args.school:
                    print()
                    conn.rollback()
                    return
            report(cur, args)
        conn.rollback()          # read-only by construction
        print("\n  ! nothing was written.\n")


if __name__ == "__main__":
    main()
