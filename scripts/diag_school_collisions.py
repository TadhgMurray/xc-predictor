#!/usr/bin/env python3
"""
diag_school_collisions.py -- two schools wearing one name, and what the
pooling does about it.

    python scripts/diag_school_collisions.py               # B and C, cheap
    python scripts/diag_school_collisions.py --rows        # + row counts
    python scripts/diag_school_collisions.py --zero        # + the team_id=0 census
    python scripts/diag_school_collisions.py --show 40

★ WHY (owner, 2026-09-16: "we need to fix schools coming together ...
  currently Oregon(IL) and Oregon(or) are colliding despite hs vs college,
  and this happens to Williams (CA) vs (MA)").

  The pool of a row whose grade cannot answer comes from levelForSchool,
  which reads school_level_graph and school_levels.pkl -- both keyed on the
  NORMALISED NAME and nothing else. So one string carries one level for
  every school wearing it, and the other school's gradeless rows are pooled
  on the wrong one. anet's own table has the identity the name lacks: a
  team_id per school, with its level, state and city.

SECTIONS
  A  team_id = 0 -- is it really "no team"? (--zero; scans results)
  B  the collisions: one name, several anet teams, different levels
  C  what the name map says about each of those teams, i.e. how many are
     levelled wrong today. No results scan: anet_team and the pickle.
  D  what school_identity currently says about those names: its clusters,
     which home states were folded into which, and whether anet's states
     survived. This is the before/after for the identity fix -- run it,
     rebuild (pipeline 10b), run it again.

⚠ A AND --rows SCAN results. B and C do not touch it at all, so the default
  run is seconds. Nothing is written.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _rows(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def sectionZero(cur):
    """A: what a team_id of 0 actually is."""
    print("\n=== A. team_id = 0 (anet's \"no team\") =================")
    for table in ("results", "results_tf"):
        try:
            got = _rows(cur, f"""
                SELECT count(*), count(DISTINCT person_id),
                       count(*) FILTER (WHERE grade ~ '^[0-9]+$'),
                       count(*) FILTER (WHERE grade IS NULL
                                          OR btrim(grade) IN ('', '-'))
                FROM   {table} WHERE team_id = 0""")
            n, people, numeric, gradeless = got[0]
            print(f"  {table:<11} {n:>10,} rows  {people:>9,} people  "
                  f"{numeric:>9,} with a numeric grade  {gradeless:>9,} gradeless")
            if not n:
                continue
            print(f"    top schools on team_id = 0:")
            for school, cnt in _rows(cur, f"""
                    SELECT school, count(*) FROM {table}
                    WHERE team_id = 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""):
                print(f"      {cnt:>8,}  {school!r}")
        except Exception as exc:                                  # noqa: BLE001
            print(f"  {table}: {type(exc).__name__}: {exc}")


def collisions(cur, min_teams=2):
    """[(name, [(team_id, level_code, state, city)])] for names anet gives
    several teams with MORE THAN ONE level. anet_team only."""
    got = _rows(cur, """
        SELECT lower(btrim(school)) AS name, team_id, level,
               COALESCE(state, anet_state), city
        FROM   anet_team
        WHERE  school IS NOT NULL AND btrim(school) <> '' AND level IS NOT NULL
          AND  lower(btrim(school)) IN (
                 SELECT lower(btrim(school)) FROM anet_team
                 WHERE  school IS NOT NULL AND level IS NOT NULL
                 GROUP  BY 1
                 HAVING count(*) >= %s AND count(DISTINCT level) > 1)""",
                (min_teams,))
    by_name = {}
    for name, team_id, level, state, city in got:
        by_name.setdefault(name, []).append((int(team_id), int(level), state, city))
    return by_name


def rowCounts(cur, team_ids):
    """{team_id: n results rows}. Scans results once; --rows only."""
    out = {}
    for table in ("results", "results_tf"):
        try:
            for team_id, n in _rows(cur, f"""
                    SELECT team_id, count(*) FROM {table}
                    WHERE team_id IS NOT NULL GROUP BY 1"""):
                if int(team_id) in team_ids:
                    out[int(team_id)] = out.get(int(team_id), 0) + int(n)
        except Exception as exc:                                  # noqa: BLE001
            print(f"  ({table} row counts unavailable: {type(exc).__name__}: {exc})")
    return out


# ★ THE BEFORE/AFTER. school_identity is the table the site's labels,
#   crests and links all resolve through (school_identity.contextState,
#   teamState, schoolHref). A collision shows up here as ONE cluster where
#   anet names two schools, or as a school_state_alias row folding one
#   school's state into the other's.
def sectionD(ranked, meaning, show):
    print("\n=== D. what school_identity says about those names ========")
    from database import getConn
    names = [n for n, _t in ranked[:show]]
    if not names:
        print("  (no collisions to look up)")
        return
    try:
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('school_identity')")
            if cur.fetchone()[0] is None:
                print("  school_identity does not exist -- run pipeline 10b")
                return
            cur.execute("""
                SELECT lower(btrim(school)), state, n_athletes, share, is_primary
                FROM   school_identity
                WHERE  lower(btrim(school)) = ANY(%s)""", (names,))
            clusters = {}
            for name, st, n, share, prim in cur.fetchall():
                clusters.setdefault(name, []).append((st, n, float(share or 0), prim))
            folds = {}
            cur.execute("SELECT to_regclass('school_state_alias')")
            if cur.fetchone()[0] is not None:
                cur.execute("""
                    SELECT lower(btrim(school)), home_state, state
                    FROM   school_state_alias
                    WHERE  lower(btrim(school)) = ANY(%s)""", (names,))
                for name, home, st in cur.fetchall():
                    folds.setdefault(name, []).append((home, st))
    except Exception as exc:                                      # noqa: BLE001
        print(f"  unavailable: {type(exc).__name__}: {exc}")
        return

    for name, teams in ranked[:show]:
        anet = sorted({(st or "--", meaning.get(code, f"code {code}"))
                       for _t, code, st, _c in teams})
        got = sorted(clusters.get(name, []), key=lambda r: -r[1])
        print(f"\n  {name!r}")
        print("      anet      " + "  ".join(f"{st}:{lv}" for st, lv in anet))
        if not got:
            print("      identity  (no cluster at all)")
        else:
            print("      identity  " + "  ".join(
                f"{st}:{n}{'*' if prim else ''}" for st, n, _sh, prim in got))
        for home, st in sorted(folds.get(name, [])):
            print(f"      folded    {home} -> {st}")
        anet_states = {st for st, _lv in anet}
        have = {st for st, *_ in got}
        missing = sorted(anet_states - have - {"--"})
        if missing:
            print(f"      ⚠ anet names a school in {', '.join(missing)} and the "
                  "identity has no cluster there")
    print("\n  * = is_primary, the state every stateless mention of the name "
          "renders and links as.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zero", action="store_true", help="section A (scans results)")
    ap.add_argument("--rows", action="store_true",
                    help="row counts per colliding team (scans results)")
    ap.add_argument("--show", type=int, default=25, help="collisions listed")
    args = ap.parse_args()

    from database import getConn
    import normalize_distance as nd
    from speed_ratings_db import loadTeamLevels

    by_team, meaning, _tally = loadTeamLevels()
    print(f"[diag] anet level codes -> {meaning}")

    with getConn() as conn, conn.cursor() as cur:
        if args.zero:
            sectionZero(cur)
        print("\n=== B. one name, several anet teams, different levels ====")
        found = collisions(cur)
        counts = rowCounts(cur, {t for v in found.values() for t, *_ in v}) if args.rows else {}

    # ★ ORDERED BY HOW MUCH IT COSTS, not alphabetically: a collision on a
    #   name nobody races under is a curiosity.
    def weight(item):
        return (sum(counts.get(t, 0) for t, *_ in item[1]) if counts
                else len(item[1]))

    ranked = sorted(found.items(), key=weight, reverse=True)
    print(f"  {len(found):,} school names carry anet teams at MORE THAN ONE level")
    for name, teams in ranked[:args.show]:
        tag = f"  [{weight((name, teams)):,} rows]" if counts else ""
        print(f"\n  {name!r}{tag}")
        for team_id, code, state, city in sorted(teams):
            lvl = meaning.get(code, f"code {code}")
            n = f"{counts.get(team_id, 0):,} rows" if counts else ""
            print(f"      team {team_id:<9} {lvl:<8} {state or '--':<4} "
                  f"{(city or ''):<18} {n}")

    # ----------------------------------------------------------------- #
    sectionD(ranked, meaning, args.show)

    # ----------------------------------------------------------------- #
    print("\n=== C. what the NAME map says about those teams ==========")
    print("  (levelForSchool: school_level_graph, then school_levels.pkl --"
          " one answer per name)")
    wrong = agree = unknown = 0
    examples = []
    for name, teams in ranked:
        name_level = nd.levelForSchool(name)
        for team_id, code, state, city in teams:
            feed = meaning.get(code)
            if feed is None or feed == "club":
                continue
            if name_level is None:
                unknown += 1
            elif name_level == feed:
                agree += 1
            else:
                wrong += 1
                if len(examples) < args.show:
                    examples.append((name, team_id, feed, name_level, state,
                                     counts.get(team_id, 0)))
    total = wrong + agree + unknown
    print(f"  {total:,} colliding teams with a feed level: "
          f"{agree:,} the name map agrees with, {wrong:,} it gets WRONG, "
          f"{unknown:,} it has no answer for")
    for name, team_id, feed, name_level, state, n in examples:
        print(f"      {name!r:<28} team {team_id:<9} {state or '--':<4} "
              f"feed says {feed:<8} the name map says {name_level:<8}"
              + (f"  {n:,} rows" if counts else ""))
    print("\n  A wrong row here is a gradeless or untrusted-grade row pooled "
          "on the other school's level.\n  pool_resolve now prefers the feed's "
          "level for exactly these rows (team_level).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
