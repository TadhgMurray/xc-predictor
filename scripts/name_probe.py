#!/usr/bin/env python3
"""
name_probe.py -- for ONE meet, what the nameless rows are. Read-only.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/name_probe.py --sport TF --meet 255955
    /srv/venv/bin/python scripts/name_probe.py --sport XC --meet 282062

★ WHY (2026-10-02). scripts/unknown_names.py: 12.2M track rows (6.4%) have no
  person and no name -- some meets almost entirely (255955: 20,717 of 21,206)
  -- and 262,760 XC rows of the 2026 season sit on blank athlete
  placeholders. The track scraper does save athlete records, so these rows
  came another way. This splits one meet's nameless rows by what they are
  (relay, field event, event), when they were scraped against the named
  rows, whether the athlete_id is set at all, and whether an athletes row
  exists for it (blank, or none) -- and prints a sample.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402

NAMED = """EXISTS (SELECT 1 FROM athletes a
                   WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
                     AND NULLIF(btrim(concat_ws(' ', a.first_name, a.last_name)), '') IS NOT NULL)
           OR NULLIF(btrim(r.athlete_name), '') IS NOT NULL"""


def cols(cur, table):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    return {r[0] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", choices=("XC", "TF"), required=True)
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--sample", type=int, default=12)
    a = ap.parse_args()
    table = "results" if a.sport == "XC" else "results_tf"
    with getConn() as conn:
        cur = conn.cursor()
        have = cols(cur, table)
        extra = [c for c in ("source", "is_relay", "is_field", "event_short", "scraped_at",
                             "id_system", "status", "school") if c in have]
        sel = ", ".join(f"r.{c}" for c in extra)
        cur.execute(f"""
            CREATE TEMP TABLE np AS
            SELECT r.result_id, r.athlete_id, r.person_id, r.athlete_name, {sel},
                   ({NAMED}) AS named,
                   EXISTS (SELECT 1 FROM athletes a WHERE a.athlete_id = r.athlete_id) AS has_row
            FROM {table} r WHERE r.meet_id = %s""", (a.meet,))
        cur.execute("SELECT count(*), count(*) FILTER (WHERE NOT named) FROM np")
        n, nameless = cur.fetchone()
        print(f"[probe] {a.sport} meet {a.meet}: {n:,} rows, {nameless:,} nameless")
        if not n:
            return
        cur.execute("""SELECT named, athlete_id IS NULL, person_id IS NULL, has_row, count(*)
                       FROM np GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC""")
        print("  named | athlete_id null | person_id null | athletes row exists | rows")
        for r in cur.fetchall():
            print(f"  {str(r[0]):<5} | {str(r[1]):<15} | {str(r[2]):<14} | {str(r[3]):<19} | {r[4]:,}")
        for c in ("source", "is_relay", "is_field", "event_short", "id_system", "status"):
            if c in extra:
                cur.execute(f"""SELECT {c}, count(*) FILTER (WHERE named), count(*) FILTER (WHERE NOT named)
                                FROM np GROUP BY 1 ORDER BY 3 DESC LIMIT 12""")
                print(f"  by {c} (named / nameless): " + "; ".join(
                    f"{v}: {x:,}/{y:,}" for v, x, y in cur.fetchall()))
        if "scraped_at" in extra:
            cur.execute("""SELECT left(scraped_at::text, 10), count(*) FILTER (WHERE named),
                                  count(*) FILTER (WHERE NOT named)
                           FROM np GROUP BY 1 ORDER BY 1""")
            print("  by scrape day (named / nameless): " + "; ".join(
                f"{d}: {x:,}/{y:,}" for d, x, y in cur.fetchall()))
        cur.execute(f"""SELECT result_id, athlete_id, person_id, athlete_name, {", ".join(extra)}
                        FROM np WHERE NOT named ORDER BY result_id LIMIT %s""", (a.sample,))
        names = ["result_id", "athlete_id", "person_id", "athlete_name"] + extra
        print("  sample nameless rows:")
        for r in cur.fetchall():
            print("    " + "  ".join(f"{k}={v!s:.28}" for k, v in zip(names, r)))
        # the athletes rows behind a few nameless athlete_ids
        cur.execute("""SELECT DISTINCT athlete_id FROM np
                       WHERE NOT named AND athlete_id IS NOT NULL LIMIT 5""")
        ids = [r[0] for r in cur.fetchall()]
        if ids:
            cur.execute("""SELECT athlete_id, first_name, last_name, gender, school, person_id
                           FROM athletes WHERE athlete_id = ANY(%s) ORDER BY 1""", (ids,))
            rows = cur.fetchall()
            print(f"  athletes rows for {ids}: " + (", ".join(str(r) for r in rows) or "none"))
            kind = "cross-country" if a.sport == "XC" else "track-and-field-outdoor"
            print("  open on athletic.net (is the name public there?):")
            for i in ids[:3]:
                print(f"    https://www.athletic.net/athlete/{i}/{kind}/")
        conn.rollback()


if __name__ == "__main__":
    main()
