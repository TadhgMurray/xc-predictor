# Project: xc-predictor
# File:    scripts/link_idless_by_name.py
# ---------------------------------------------------------------------------
# THE IDEA
#   Pass 2 of id-less recovery. Pass 1 needed same meet + name + time. This
#   pass uses NAME UNIQUENESS alone: if a normalized name belongs to exactly
#   ONE identified person in the whole corpus, then a leftover id-less tfrrs
#   row carrying that name is that person -- no meet or time needed.
#
#   WEAKER THAN PASS 1 (state it plainly): the only signal is the name. The
#   failure mode is two different real people sharing one rare full name, where
#   only one is identified -> both weld onto that one person. Guards below cut
#   the worst of it but cannot eliminate it. Precision-for-recall, on purpose.
#
#   GUARDS
#     - name must have 2+ tokens (a space) -- kills single-word junk.
#     - uniqueness computed over `athletes` (the anet identity registry):
#       a name qualifies only if exactly ONE person_id bears it.
#     - census prints a COLLISION PROXY: max leftover rows mapping to one
#       person, and how many persons receive more than --warn-n rows. A
#       "unique" name grabbing many rows is the tell to stop and look.
#
#   Read-only by default. --apply stamps, commits, re-censuses. Idempotent
#   (only person_id IS NULL). Count ROWS, not links.
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
    """Light name normalization (lower, trim, collapse whitespace), once per row."""
    return f"regexp_replace(lower(btrim({expr})), '\\s+', ' ', 'g')"


def _buildTemps(cur, cfg, maxPer):
    """
    Build:
      uname_tmp  -- name -> person_id, ONLY where the name is globally unique
                    in `athletes` and has 2+ tokens.
      rem_tmp    -- remaining id-less tfrrs finishers (person_id NULL,
                    athlete_id NULL, place>0), name normalized, 2+ tokens.
      match_tmp  -- rem_tmp joined to uname_tmp on name. One person per name by
                    construction, so no ambiguity column is needed.
    """
    r = cfg["results"]
    anorm = _normSql("(first_name || ' ' || last_name)")
    tnorm = _normSql("t." + cfg["name_col"])

    # Unique anet names -> the one person who holds each.
    cur.execute("DROP TABLE IF EXISTS uname_tmp")
    # ★ WITH THE PERSON'S GENDER (issue 68): a unique name is not a unique
    #   person when the tfrrs row says "Women's 5000" and every anet row of
    #   that name is a boy. The majority gender of the name's athlete rows
    #   rides along; the match below refuses a contradiction.
    cur.execute(f"""
        CREATE TEMP TABLE uname_tmp AS
        SELECT nm, MIN(person_id) AS person_id,
               (SELECT g.gender FROM athletes g
                WHERE {anorm.replace('first_name', 'g.first_name').replace('last_name', 'g.last_name')} = x.nm
                  AND g.gender IN ('M', 'F')
                GROUP BY g.gender ORDER BY count(*) DESC, g.gender DESC
                LIMIT 1) AS gender
        FROM (
            SELECT {anorm} AS nm, person_id
            FROM athletes
            WHERE first_name <> '' AND person_id IS NOT NULL
        ) x
        GROUP BY nm
        HAVING COUNT(DISTINCT person_id) = 1
           AND position(' ' in nm) > 0     -- 2+ tokens
    """)
    cur.execute("CREATE INDEX ON uname_tmp (nm)")

    # Remaining id-less tfrrs finishers.
    cur.execute("DROP TABLE IF EXISTS rem_tmp")
    cur.execute(f"""
        CREATE TEMP TABLE rem_tmp AS
        SELECT t.result_id, {tnorm} AS nm,
               -- the row's own gender, read off the event or division
               -- title where one names it; NULL where neither does
               CASE WHEN COALESCE(t.event_short, '') ~* '(women|girls|\\yw\\y|female)' THEN 'F'
                    WHEN COALESCE(t.event_short, '') ~* '(\\ymen|boys|\\ym\\y|\\ymale)' THEN 'M'
                    ELSE NULL END AS gender
        FROM {r} t
        WHERE t.source = 'tfrrs'
          AND t.person_id IS NULL
          AND t.athlete_id IS NULL
          AND t.place > 0
          AND t.{cfg['time_col']} IS NOT NULL
          AND {tnorm} <> ''
          AND position(' ' in {tnorm}) > 0
          AND (t.event_short IS NULL OR t.event_short !~* 'relay|[0-9]\s*x\s*[0-9]')
    """)
    cur.execute("CREATE INDEX ON rem_tmp (nm)")

    # Match: unique name -> person.
    # Match: unique name -> person, but REJECT any name that maps to more than
    # --max-per rows. Real careers top out in the low tens; school-name and
    # parser-junk strings absorb hundreds. The cap cleanly separates them.
    cur.execute("DROP TABLE IF EXISTS match_tmp")
    cur.execute(f"""
        CREATE TEMP TABLE match_tmp AS
        WITH cand AS (
            SELECT r.result_id, u.person_id, u.nm
            FROM rem_tmp r
            JOIN uname_tmp u ON u.nm = r.nm
            -- a contradiction in gender is close to a proof of a bad link
            -- (issue 68); an unknown on either side still matches
            WHERE r.gender IS NULL OR u.gender IS NULL OR r.gender = u.gender
        ),
        bad AS (
            SELECT nm FROM cand GROUP BY nm HAVING count(*) > {maxPer}
        )
        SELECT c.result_id, c.person_id
        FROM cand c
        WHERE c.nm NOT IN (SELECT nm FROM bad)
    """)


def _census(cur, cfg, warnN):
    """Funnel + collision proxy. Returns stampable count."""
    r = cfg["results"]

    cur.execute(f"""
        SELECT count(*) FROM {r}
        WHERE source='tfrrs' AND person_id IS NULL AND athlete_id IS NULL
          AND place > 0 AND {cfg['time_col']} IS NOT NULL
    """)
    remaining = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM rem_tmp")
    remTok = cur.fetchone()[0]

    cur.execute("SELECT count(*), count(DISTINCT person_id) FROM match_tmp")
    matched, persons = cur.fetchone()

    # Collision proxy: how concentrated are the matches per person?
    cur.execute(f"""
        WITH per AS (SELECT person_id, count(*) c FROM match_tmp GROUP BY person_id)
        SELECT COALESCE(max(c),0),
               count(*) FILTER (WHERE c > {warnN})
        FROM per
    """)
    maxPer, overN = cur.fetchone()

    print(f"  remaining id-less finishers    {remaining:>12,}")
    print(f"    with a 2+ token name         {remTok:>12,}")
    print(f"      matched a UNIQUE name       {matched:>12,}   -> stampable")
    print(f"      distinct persons matched    {persons:>12,}")
    print(f"      max rows to one person      {maxPer:>12,}   (collision proxy)")
    print(f"      persons receiving > {warnN:<3}      {overN:>12,}   (inspect these)")
    return matched


def _apply(cur, cfg):
    """Stamp person_id from match_tmp. Returns rows updated."""
    r = cfg["results"]
    cur.execute(f"""
        UPDATE {r} AS tgt
           SET person_id = m.person_id
          FROM match_tmp m
         WHERE tgt.result_id = m.result_id
           AND tgt.person_id IS NULL
    """)
    return cur.rowcount


def main():
    ap = argparse.ArgumentParser(description="Pass 2: link remaining id-less "
                                 "tfrrs rows by globally-unique name.")
    ap.add_argument("--max-per", type=int, default=50,
                    help="reject any name mapping to more than this many rows (junk guard)")
    ap.add_argument("--sport", choices=["XC", "TF"], required=True)
    ap.add_argument("--warn-n", type=int, default=5,
                    help="flag persons receiving more than this many rows")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    cfg = _sportConfig(args.sport)

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print(f"=== {args.sport}: id-less recovery pass 2 (unique name) ===")
        print("building temp tables...")
        _buildTemps(cur, cfg, args.max_per)

        print("BEFORE:")
        stampable = _census(cur, cfg, args.warn_n)

        if not args.apply:
            print(f"\n[census only] {stampable:,} rows would be stamped. "
                  f"Inspect the collision proxy before --apply.")
            conn.rollback()
            return

        updated = _apply(cur, cfg)
        conn.commit()
        print(f"\n[apply] stamped person_id on {updated:,} rows.")

        print("AFTER (read-back):")
        _buildTemps(cur, cfg, args.max_per)
        _census(cur, cfg, args.warn_n)
        conn.rollback()


if __name__ == "__main__":
    main()