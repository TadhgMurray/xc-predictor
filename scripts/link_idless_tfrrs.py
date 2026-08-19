# Project: xc-predictor
# File:    scripts/link_idless_tfrrs.py   (v2 -- temp-table, index-friendly)
# ---------------------------------------------------------------------------
# THE IDEA (unchanged; only the MECHANICS are faster)
#   Id-less tfrrs finishers (name+time, athlete_id/person_id NULL) sit at
#   canon-linked meets. At the same canon meet, anet rows carry person_id.
#   Exactly one anet person with same normalized name + time within TOL => stamp.
#   anet names live in `athletes` (athlete_id, school); tfrrs names are inline.
#
#   WHY V2: v1 ran the name regex once per candidate PAIR (millions of calls at
#   TF scale, no index). v2 normalizes each name ONCE into indexed temp tables,
#   then matches temp-to-temp. Same results; expensive step paid once.
# ---------------------------------------------------------------------------

import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool


def _sportConfig(sport):
    """Table/column names that differ by sport."""
    S = sport.upper()
    return {
        "results": "results" if S == "XC" else "results_tf",
        "name_col": "athlete_name",
        "time_col": "time_seconds",
    }


def _normSql(expr):
    """SQL that light-normalizes a name (lower, trim, collapse whitespace).
    Used only in the temp-table builds, so it runs once per row."""
    return f"regexp_replace(lower(btrim({expr})), '\\s+', ' ', 'g')"


def _buildTemps(cur, cfg, tol):
    """Create idless_tmp, anet_tmp, match_tmp on this session. Names normalized
    once; final match is an indexed temp-to-temp join."""
    r = cfg["results"]
    tnorm = _normSql("t." + cfg["name_col"])
    anorm = _normSql("(an.first_name || ' ' || an.last_name)")
    tcol = cfg["time_col"]

    # 1. id-less tfrrs finishers at canon-linked meets -- normalize name ONCE.
    cur.execute("DROP TABLE IF EXISTS idless_tmp")
    cur.execute(f"""
        CREATE TEMP TABLE idless_tmp AS
        SELECT t.result_id, t.canon_meet_id, {tnorm} AS nm, t.{tcol} AS tt
        FROM {r} t
        WHERE t.source = 'tfrrs'
          AND t.person_id IS NULL
          AND t.athlete_id IS NULL
          AND t.canon_meet_id IS NOT NULL
          AND t.place > 0
          AND t.{tcol} IS NOT NULL
          AND {tnorm} <> ''
    """)
    cur.execute("CREATE INDEX ON idless_tmp (canon_meet_id, nm)")

    # 2. anet persons AT THOSE MEETS ONLY (keeps the athletes join small).
    cur.execute("DROP TABLE IF EXISTS anet_tmp")
    cur.execute(f"""
        CREATE TEMP TABLE anet_tmp AS
        SELECT a.canon_meet_id, a.person_id, {anorm} AS nm, a.{tcol} AS tt
        FROM {r} a
        JOIN athletes an
          ON an.athlete_id = a.athlete_id
         AND an.school     = a.school
        WHERE a.source = 'anet'
          AND a.person_id IS NOT NULL
          AND a.{tcol} IS NOT NULL
          AND a.canon_meet_id IN (SELECT DISTINCT canon_meet_id FROM idless_tmp)
    """)
    cur.execute("CREATE INDEX ON anet_tmp (canon_meet_id, nm)")

    # 3. the match: indexed temp-to-temp. One row per id-less result.
    cur.execute("DROP TABLE IF EXISTS match_tmp")
    cur.execute(f"""
        CREATE TEMP TABLE match_tmp AS
        SELECT i.result_id,
               MIN(a.person_id)             AS person_id,
               COUNT(DISTINCT a.person_id)  AS n_persons
        FROM idless_tmp i
        JOIN anet_tmp a
          ON a.canon_meet_id = i.canon_meet_id
         AND a.nm            = i.nm
         AND abs(a.tt - i.tt) <= {tol}
        GROUP BY i.result_id
    """)


def _census(cur, cfg):
    """Print the funnel from the temp tables. Returns the stampable count."""
    r = cfg["results"]
    cur.execute(f"""
        SELECT count(*) FROM {r}
        WHERE source = 'tfrrs' AND person_id IS NULL AND athlete_id IS NULL
          AND place > 0 AND {cfg['time_col']} IS NOT NULL
    """)
    idless = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM idless_tmp")
    atLinked = cur.fetchone()[0]

    cur.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE n_persons = 1),
               count(*) FILTER (WHERE n_persons > 1)
        FROM match_tmp
    """)
    matched, uniq, ambig = cur.fetchone()

    print(f"  id-less tfrrs finishers        {idless:>12,}")
    print(f"    at a canon-linked meet       {atLinked:>12,}")
    print(f"      name+time matched anet      {matched:>12,}")
    print(f"        UNIQUE  -> stampable      {uniq:>12,}")
    print(f"        ambiguous (>=2, bail)     {ambig:>12,}")
    return uniq


def _apply(cur, cfg):
    """Stamp matched person_id onto the UNIQUE matches only. Returns rows updated."""
    r = cfg["results"]
    cur.execute(f"""
        UPDATE {r} AS tgt
           SET person_id = m.person_id
          FROM match_tmp m
         WHERE tgt.result_id = m.result_id
           AND m.n_persons = 1
           AND tgt.person_id IS NULL
    """)
    return cur.rowcount


def main():
    ap = argparse.ArgumentParser(description="Link id-less tfrrs finishers by "
                                 "unique (name,time) match at canon-linked meets.")
    ap.add_argument("--sport", choices=["XC", "TF"], required=True)
    ap.add_argument("--tol", type=float, default=0.3,
                    help="time-match tolerance in seconds (default 0.3)")
    ap.add_argument("--apply", action="store_true",
                    help="write the stamps (default is read-only census)")
    args = ap.parse_args()
    cfg = _sportConfig(args.sport)

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print(f"=== {args.sport}: id-less tfrrs recovery (tol={args.tol}s) ===")
        print("building temp tables (normalize-once)...")
        _buildTemps(cur, cfg, args.tol)

        print("BEFORE:")
        stampable = _census(cur, cfg)

        if not args.apply:
            print(f"\n[census only] {stampable:,} rows would be stamped. "
                  f"Re-run with --apply to write.")
            conn.rollback()
            return

        updated = _apply(cur, cfg)
        conn.commit()
        print(f"\n[apply] stamped person_id on {updated:,} rows.")

        print("AFTER (read-back post-check):")
        _buildTemps(cur, cfg, args.tol)
        _census(cur, cfg)
        conn.rollback()


if __name__ == "__main__":
    main()