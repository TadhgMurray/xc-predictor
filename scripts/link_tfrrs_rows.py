#!/usr/bin/env python3
"""
link_tfrrs_rows.py -- give every new tfrrs row a person.

  Run by hand after a scrape (or, once trusted, as a pipeline step ahead of
  grade_sanity -- that is the owner's call; it rewrites person_id).

    /srv/venv/bin/python scripts/link_tfrrs_rows.py            # DRY RUN
    /srv/venv/bin/python scripts/link_tfrrs_rows.py --apply    # link them
    /srv/venv/bin/python scripts/link_tfrrs_rows.py --undo fanout|mint

★ THE GAP, MEASURED (owner, 2026-09-25, diag_tfrrs_identity.py):

      XC tfrrs   season     rows    person   tfrrs id only   rated
                 2025    398,222   340,353         30,119   73,602
                 2026     79,291         0         69,278        0

  A tfrrs row is scraped with no person_id; only the linking scripts
  (merge_links, stamp_fanout, link_idless_*) ever give it one, and none of
  them runs on a schedule. They were run after past seasons and not since,
  so not one 2026 tfrrs row belongs to a person: no athlete page name, no
  gender from `athletes`, no place in the solve, NO RATING. Every symptom
  of the day -- "all freshman are unknown", race pages "unlinked", the
  compiled view's "?" gender -- is this one gap.

THREE PASSES, IN THIS ORDER, EACH SAFE TO RE-RUN

  1. FAN-OUT BY tfrrs ATHLETE ID. A returning runner's older tfrrs rows
     already carry their person (88% of 2025). Every row of the same tfrrs
     athlete id with no person gets that person -- only when the id's
     stamped rows agree on exactly ONE person (the fan-out guard
     merge_links applies: two persons under one id is a collision, not a
     link). This is most of 2026.

  2. FRESHMEN (scripts/link_freshmen.py): a tfrrs first-year matched to
     their own anet high school senior season. Guarded there.

  3. MINT, FOR WHOEVER IS LEFT. A tfrrs athlete id with no person anywhere
     -- a walk-on, an international, a runner anet never had -- gets one of
     its own, derived from the id so every run mints the same number:
         person_id = 1,000,000,000 + native_id          (tfrrs ids)
                   = 1,500,000,000 + native_id          (directathletics)
     far above any anet id (about 33 million in 2026), and under 2^31 so it
     fits a person_id column whatever its integer width.
     Only identities whose first row is in the last --mint-seasons seasons
     (default 1): an older id with no person was usually QUARANTINED by the
     merge (a contested link), and minting it would split a real person.
     A later link (a re-run of pass 2, or merge_links) simply moves the rows
     from the minted id to the real one.

! LOGGED AND REVERSIBLE. Every stamped row goes to person_link_log (rule
  'fanout' / 'mint'; the freshman pass logs 'freshman') with from_person 0,
  and --undo <rule> puts exactly those rows back to NULL.
"""
import argparse
import datetime
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                  # noqa: E402

MINT_BASE = {"tfrrs": 1_000_000_000, "directathletics": 1_500_000_000}
MINT_MAX_NATIVE = 500_000_000       # past this the two bases would overlap
TABLES = (("XC", "results"), ("TF", "results_tf"))


def _sys(sport, alias="r"):
    """The tfrrs id system of a row: XC rows are all tfrrs ids."""
    return ("'tfrrs'" if sport == "XC"
            else f"COALESCE({alias}.id_system, 'tfrrs')")


def _step(cur, what, sql, params=None):
    t0 = time.time()
    print(f"[tfrrs-link]   {what} ...", end="", flush=True)
    cur.execute(sql, params or {})
    print(f" {time.time() - t0:.0f}s"
          + (f", {cur.rowcount:,} rows" if cur.rowcount >= 0 else ""), flush=True)
    return cur.rowcount


def _log(cur):
    from link_freshmen import LOG_DDL
    cur.execute(LOG_DDL)


def fanout(cur, apply):
    cur.execute("SET LOCAL work_mem = '1GB'")
    cur.execute("DROP TABLE IF EXISTS tl_nat")
    _step(cur, "tfrrs ids and the ONE person their stamped rows agree on", f"""
        CREATE TEMP TABLE tl_nat AS
        SELECT sys, native_id, min(person_id) AS person_id
        FROM (
            SELECT {_sys('XC')} AS sys, r.native_id, r.person_id FROM results r
            WHERE  r.source = 'tfrrs' AND r.native_id IS NOT NULL
              AND  r.person_id IS NOT NULL
            UNION ALL
            SELECT {_sys('TF')}, r.native_id, r.person_id FROM results_tf r
            WHERE  r.source = 'tfrrs' AND r.native_id IS NOT NULL
              AND  r.person_id IS NOT NULL
        ) x
        GROUP  BY sys, native_id
        HAVING count(DISTINCT person_id) = 1
    """)
    cur.execute("CREATE INDEX ON tl_nat (native_id, sys)")
    total = 0
    for sport, table in TABLES:
        if not apply:
            _step(cur, f"{sport}: rows that would be stamped", f"""
                SELECT count(*) FROM {table} r JOIN tl_nat n
                  ON n.native_id = r.native_id AND n.sys = {_sys(sport)}
                WHERE r.source = 'tfrrs' AND r.person_id IS NULL
            """)
            n = cur.fetchone()[0]
            print(f"[tfrrs-link]     {n:,}")
            total += n
            continue
        _step(cur, f"{sport}: logging", f"""
            INSERT INTO person_link_log (sport, result_id, from_person,
                                         to_person, rule)
            SELECT %s, r.result_id, 0, n.person_id, 'fanout'
            FROM   {table} r JOIN tl_nat n
                   ON n.native_id = r.native_id AND n.sys = {_sys(sport)}
            WHERE  r.source = 'tfrrs' AND r.person_id IS NULL
            ON CONFLICT DO NOTHING
        """, (sport,))
        total += _step(cur, f"{sport}: stamping", f"""
            UPDATE {table} r SET person_id = n.person_id
            FROM   tl_nat n
            WHERE  r.source = 'tfrrs' AND r.person_id IS NULL
              AND  n.native_id = r.native_id AND n.sys = {_sys(sport)}
        """)
    return total


def mint(cur, apply, seasons):
    today = datetime.date.today()
    ay = today.year if today.month >= 8 else today.year - 1
    since = f"{ay - seasons + 1}-08-01"
    cur.execute("DROP TABLE IF EXISTS tl_new")
    _step(cur, f"tfrrs ids with no person anywhere, first seen since {since}", f"""
        CREATE TEMP TABLE tl_new AS
        SELECT sys, native_id FROM (
            SELECT {_sys('XC')} AS sys, r.native_id, r.date, r.person_id
            FROM   results r
            WHERE  r.source = 'tfrrs' AND r.native_id IS NOT NULL
            UNION ALL
            SELECT {_sys('TF')}, r.native_id, r.date, r.person_id
            FROM   results_tf r
            WHERE  r.source = 'tfrrs' AND r.native_id IS NOT NULL
        ) x
        GROUP  BY sys, native_id
        HAVING count(person_id) = 0 AND min(date) >= %(since)s
    """, {"since": since})
    cur.execute("DELETE FROM tl_new WHERE sys <> ALL(%s) OR native_id < 1 "
                "OR native_id >= %s", (list(MINT_BASE), MINT_MAX_NATIVE))
    cur.execute("CREATE INDEX ON tl_new (native_id, sys)")
    case = ("CASE n.sys " + " ".join(f"WHEN '{k}' THEN {v}"
                                      for k, v in MINT_BASE.items())
            + " END + n.native_id")
    total = 0
    for sport, table in TABLES:
        if not apply:
            _step(cur, f"{sport}: rows that would get a minted person", f"""
                SELECT count(*) FROM {table} r JOIN tl_new n
                  ON n.native_id = r.native_id AND n.sys = {_sys(sport)}
                WHERE r.source = 'tfrrs' AND r.person_id IS NULL
            """)
            n = cur.fetchone()[0]
            print(f"[tfrrs-link]     {n:,}")
            total += n
            continue
        _step(cur, f"{sport}: logging", f"""
            INSERT INTO person_link_log (sport, result_id, from_person,
                                         to_person, rule)
            SELECT %s, r.result_id, 0, {case}, 'mint'
            FROM   {table} r JOIN tl_new n
                   ON n.native_id = r.native_id AND n.sys = {_sys(sport)}
            WHERE  r.source = 'tfrrs' AND r.person_id IS NULL
            ON CONFLICT DO NOTHING
        """, (sport,))
        total += _step(cur, f"{sport}: minting", f"""
            UPDATE {table} r SET person_id = {case}
            FROM   tl_new n
            WHERE  r.source = 'tfrrs' AND r.person_id IS NULL
              AND  n.native_id = r.native_id AND n.sys = {_sys(sport)}
        """)
    return total


def undo(conn, rule):
    with conn.cursor() as cur:
        for sport, table in TABLES:
            cur.execute(f"""
                UPDATE {table} r SET person_id = NULLIF(l.from_person, 0)
                FROM   person_link_log l
                WHERE  l.sport = %s AND l.rule = %s
                  AND  r.result_id = l.result_id AND r.person_id = l.to_person
            """, (sport, rule))
            print(f"[tfrrs-link] {sport}: {cur.rowcount:,} rows put back")
        cur.execute("DELETE FROM person_link_log WHERE rule = %s", (rule,))
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mint-seasons", type=int, default=1)
    ap.add_argument("--no-freshmen", action="store_true")
    ap.add_argument("--no-mint", action="store_true")
    ap.add_argument("--undo", choices=("fanout", "mint", "freshman"))
    a = ap.parse_args()
    if os.environ.get("XCP_LINK_TFRRS", "1") in ("0", "false"):
        print("[tfrrs-link] XCP_LINK_TFRRS=0 -- skipped")
        return
    with getConn() as conn:
        if a.undo:
            undo(conn, a.undo)
            return
        mode = "APPLY" if a.apply else "DRY RUN"
        print(f"[tfrrs-link] {mode}")
        with conn.cursor() as cur:
            if a.apply:
                _log(cur)
            n = fanout(cur, a.apply)
            print(f"[tfrrs-link] pass 1, fan-out: {n:,} rows")
        conn.commit() if a.apply else conn.rollback()

        if not a.no_freshmen:
            import link_freshmen as L
            now = L.academicYear()
            years = [now - 2, now - 1, now]
            with conn.cursor() as cur:
                fresh_by = L.freshmen(cur, years)
                senior_by = L.seniors(cur, years)
                pairs = []
                for y in years:
                    p, skipped = L.pairsFor(fresh_by[y], senior_by[y])
                    print(f"[tfrrs-link] pass 2, freshmen {y}: {len(p):,} "
                          f"pairs; skipped {skipped}")
                    pairs += p
            conn.rollback()                  # temp tables only
            pairs = L.dedupePairs(pairs)
            if a.apply and pairs:
                moved = L.write(conn, pairs)
                print(f"[tfrrs-link] pass 2: joined {len(pairs):,} freshmen; "
                      f"rows {moved}")

        if not a.no_mint:
            with conn.cursor() as cur:
                n = mint(cur, a.apply, a.mint_seasons)
                print(f"[tfrrs-link] pass 3, mint: {n:,} rows")
            conn.commit() if a.apply else conn.rollback()
        if not a.apply:
            print("[tfrrs-link] dry run -- nothing written; --apply to link")


if __name__ == "__main__":
    main()
