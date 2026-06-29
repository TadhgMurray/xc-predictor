# Project: xc-predictor
# File:    scripts/diag_xc_40_recheck.py
# Purpose: After resetting + rescraping the 40 sampled XC neither meets, verify
#          what landed: (1) per-meet status — did each meet save results / meets
#          rows / team scores; and (2) full per-COLUMN non-NULL coverage across
#          every column of results / meets / meet_extras for these 40 meets, so a
#          silently-unsaved column shows up. Decides whether the full XC path is
#          clean before we commit the 14k.
#
# Everything scoped to these 40 ids AND (source='anet', sport='xc' where it
# applies) — identity is (meet_id, sport, source), never meet_id alone.
#
# Read-only. Run from project root:  python scripts/diag_xc_40_recheck.py

import sys
sys.path.insert(0, "scripts")
from database import getConn

MEET_IDS = [
    183941, 101693, 274854, 246509, 105427, 209902, 267461, 274867, 209968,
    175877, 211703, 196014, 200318, 251717, 100947, 10800, 20919, 7645, 271500,
    25964, 6151, 175214, 190815, 12369, 170229, 232846, 246832, 274624, 5622,
    21387, 252446, 263527, 137509, 188919, 227380, 18256, 242386, 166221,
    146621, 274203,
]


# _pct
# Purpose:   Format "n  (xx.x%)" against a total, guarding divide-by-zero.
def _pct(n, total) -> str:
    share = (100.0 * n / total) if total else 0.0
    return f"{n:>8,}  ({share:5.1f}%)"


# _mapGroupBy
# Purpose:   Build {key: value} from a 2-column (key, count/flag) query.
# Arguments: cur, sql, params.
# Output:    dict.
def _mapGroupBy(cur, sql, params) -> dict:
    cur.execute(sql, params)
    return {row[0]: row[1] for row in cur.fetchall()}


# _perMeetStatus
# Purpose:   Section 1 — one line per meet: queue scraped state, # result rows,
#            # meets (div) rows, whether team_scores landed. The headline answer
#            to "did the rescrape actually save this meet?" Watch for scraped=1
#            with results=0 — that's "ran and saved nothing" = a live save bug.
# Arguments: cur — open cursor.
# Output:    None (prints).
def _perMeetStatus(cur) -> None:
    print("=" * 60)
    print("1. per-meet status (the 40)")
    print("=" * 60)
    queue = _mapGroupBy(cur, "SELECT meet_id, scraped FROM meet_queue "
                             "WHERE sport='XC' AND source='anet' AND meet_id=ANY(%s)",
                        (MEET_IDS,))
    nres = _mapGroupBy(cur, "SELECT meet_id, COUNT(*) FROM results "
                            "WHERE source='anet' AND meet_id=ANY(%s) GROUP BY meet_id",
                       (MEET_IDS,))
    ndiv = _mapGroupBy(cur, "SELECT meet_id, COUNT(*) FROM meets "
                            "WHERE source='anet' AND meet_id=ANY(%s) GROUP BY meet_id",
                       (MEET_IDS,))
    extra = _mapGroupBy(cur, "SELECT meet_id, (team_scores_json IS NOT NULL)::int "
                             "FROM meet_extras WHERE source='anet' AND sport='xc' "
                             "AND meet_id=ANY(%s)", (MEET_IDS,))

    have_results = have_div = 0
    for mid in MEET_IDS:
        r = nres.get(mid, 0)
        d = ndiv.get(mid, 0)
        have_results += 1 if r else 0
        have_div += 1 if d else 0
        print(f"  {mid:>8}  scraped={str(queue.get(mid,'-')):>2}  "
              f"results={r:>5}  div_rows={d:>4}  team_scores={extra.get(mid,0)}")
    print(f"  -- of {len(MEET_IDS)}: {have_results} now have results, "
          f"{have_div} have meets rows --")


# _coverage
# Purpose:   Sections 2-4 — non-NULL coverage of EVERY column of one table over
#            the rows belonging to these 40 meets. Columns are enumerated from
#            the catalog so it is literally all of them; COUNT(col) = non-null
#            count. A column stuck at 0% is one the saver isn't writing.
# Arguments: cur — open cursor; table — table name; extra_filter — the
#            source/sport scoping clause for that table (trusted literal).
# Output:    None (prints).
def _coverage(cur, table, extra_filter) -> None:
    print("\n" + "=" * 60)
    print(f"coverage: {table}")
    print("=" * 60)
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=%s
                   ORDER BY ordinal_position""", (table,))
    cols = [r[0] for r in cur.fetchall()]

    # COUNT(*) for the denominator, then COUNT("col") (non-null) for each column.
    # Identifiers are catalog-sourced + double-quoted; meet_ids is parameterized.
    selects = ['COUNT(*)'] + [f'COUNT("{c}")' for c in cols]
    sql = (f'SELECT {", ".join(selects)} FROM "{table}" '
           f'WHERE meet_id = ANY(%s) {extra_filter}')
    cur.execute(sql, (MEET_IDS,))
    row = cur.fetchone()

    total = row[0]
    print(f"  rows: {total:,}")
    if not total:
        print("  (no rows yet — rescrape may not have run for these)")
        return
    for col, n in zip(cols, row[1:]):
        print(f"    {col:<22} {_pct(n, total)}")


# main
# Purpose:   Run the per-meet status then full coverage for the three XC tables.
def main() -> None:
    with getConn() as conn:
        cur = conn.cursor()
        _perMeetStatus(cur)
        _coverage(cur, "results", "AND source='anet'")
        _coverage(cur, "meets", "AND source='anet'")
        _coverage(cur, "meet_extras", "AND source='anet' AND sport='xc'")


if __name__ == "__main__":
    main()