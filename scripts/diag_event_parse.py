# Project: xc-predictor
# File:    scripts/diag_event_parse.py
# Purpose: READ-ONLY. Find out WHY distanceFromEventShort returns None for
#          313 of the 500 worst TF divisions -- by printing the strings it
#          choked on, not by theorising about them.
#
# WHAT WE KNOW
#   triage --sport TF --from-scored --limit 500  ->  ov=None on 313 rows.
#   backfill_normalize._resolveDistanceGender resolves TF distance ONLY via
#       return distanceFromEventShort(row[_EVENT_SHORT])
#   so a None there means the row got NO distance and was skipped entirely
#   (the backfill's no_distance / insane_distance census). Those rows never
#   reach the ruler at all -- which is a bigger problem than a wrong label.
#
# WHAT THIS PRINTS
#   1. corpus-wide: how many results_tf rows have an event_short the parser
#      cannot read, and how many rows that costs
#   2. the TOP FAILING STRINGS by row count -- the actual fix list
#   3. the strings it CAN read, so the pattern that works is visible next to
#      the pattern that does not
#   4. for the flagged crop specifically: which divisions are unparseable
#
# USAGE (~40 sec)
#   python scripts\diag_event_parse.py

import os
import sys
from collections import Counter

sys.path.insert(0, "engine")
from database import getConn, initPool
# The SAME parser the backfill uses. Import, never reimplement.
from event_parse import distanceFromEventShort


# ================================================================== #
# CHUNK 1 -- RUN THE PARSER OVER EVERY DISTINCT EVENT NAME
# ================================================================== #

def _distinctEvents(cur):
    """
    Purpose : every distinct event_short and how many rows carry it.
    Output  : [(event_short, n_rows), ...] most common first.
    Why     : the fix list is ordered by ROWS, not by distinct strings -- one
              bad name on 400k rows matters more than 200 names on 3 rows each.
    """
    cur.execute("""
        SELECT event_short, count(*) AS n
        FROM results_tf
        GROUP BY event_short
        ORDER BY n DESC
    """)
    return cur.fetchall()


def _tryParse(ev):
    """
    Purpose : what does the backfill get for this string?
    Output  : (distance|None, error|None). distanceFromEventShort returns
              (metres, gender) per backfill's usage; take [0]. An EXCEPTION is
              different from a None -- one is a crash, the other a miss.
    """
    try:
        res = distanceFromEventShort(ev)
        dist = res[0] if isinstance(res, tuple) else res
        return dist, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _split(events):
    """
    Purpose : partition every event name into parsed / unparsed / crashed.
    Output  : three lists of (event_short, n_rows, extra).
    """
    ok, miss, crash = [], [], []
    for ev, n in events:
        dist, err = _tryParse(ev)
        if err:
            crash.append((ev, n, err))
        elif dist is None:
            miss.append((ev, n, None))
        else:
            ok.append((ev, n, dist))
    return ok, miss, crash


# ================================================================== #
# CHUNK 2 -- THE FLAGGED CROP SPECIFICALLY
# ================================================================== #

def _flaggedPairs(path, limit=500):
    """Purpose : the same crop triage ran, header-driven."""
    if not os.path.exists(path):
        return []
    head, rows = None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                cols = line.lstrip("#").strip().split("\t")
                head = {n.strip(): i for i, n in enumerate(cols)}
                continue
            if not line.strip() or head is None:
                continue
            p = line.rstrip("\n").split("\t")
            try:
                rows.append((abs(float(p[head["z"]])),
                             int(p[head["meet_id"]]), int(p[head["div_id"]])))
            except (ValueError, KeyError, IndexError):
                continue
    rows.sort(reverse=True)
    return [(m, d) for _z, m, d in rows[:limit]]


def _cropEvents(cur, pairs):
    """Purpose : the event_short of each flagged division."""
    values = ",".join(cur.mogrify("(%s,%s)", p).decode() for p in pairs)
    cur.execute(f"""
        SELECT DISTINCT ON (r.meet_id, r.div_id)
               r.meet_id, r.div_id, r.event_short, count(*) OVER
               (PARTITION BY r.meet_id, r.div_id) AS n
        FROM results_tf r
        JOIN (VALUES {values}) v(m, d) ON v.m = r.meet_id AND v.d = r.div_id
        ORDER BY r.meet_id, r.div_id
    """)
    return cur.fetchall()


# ================================================================== #
# CHUNK 3 -- REPORT
# ================================================================== #

def _pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "n/a"


def _reportCorpus(ok, miss, crash):
    rows_ok = sum(n for _e, n, _x in ok)
    rows_miss = sum(n for _e, n, _x in miss)
    rows_crash = sum(n for _e, n, _x in crash)
    total = rows_ok + rows_miss + rows_crash

    print("\n" + "=" * 70)
    print("  CORPUS: can distanceFromEventShort read results_tf.event_short?")
    print("=" * 70)
    print(f"    distinct names   parsed {len(ok):>6,}   MISS {len(miss):>6,}"
          f"   CRASH {len(crash):>4,}")
    print(f"    rows             parsed {rows_ok:>10,} ({_pct(rows_ok, total)})")
    print(f"                     MISS   {rows_miss:>10,} ({_pct(rows_miss, total)})"
          f"   <- these rows get NO distance and are SKIPPED")
    print(f"                     CRASH  {rows_crash:>10,} ({_pct(rows_crash, total)})")

    if crash:
        print("\n    !! the parser THROWS on these -- fix first:")
        for ev, n, err in crash[:10]:
            print(f"      {str(ev)[:40]:<42} {n:>8,}  {err[:60]}")

    print("\n  TOP UNPARSEABLE NAMES BY ROW COUNT -- this is the fix list:")
    print(f"      {'event_short':<44} {'rows':>10}")
    for ev, n, _x in miss[:30]:
        print(f"      {repr(ev)[:42]:<44} {n:>10,}")

    print("\n  FOR CONTRAST -- names it reads fine:")
    print(f"      {'event_short':<44} {'rows':>10} {'->':>3} {'metres':>8}")
    for ev, n, dist in ok[:12]:
        print(f"      {repr(ev)[:42]:<44} {n:>10,} {'->':>3} {dist:>8}")


def _reportCrop(cur, pairs):
    print("\n" + "=" * 70)
    print("  THE FLAGGED CROP: which of the worst 500 are unparseable?")
    print("=" * 70)
    if not pairs:
        print("    scored_div_tf.tsv not found")
        return
    rows = _cropEvents(cur, pairs)
    bad = []
    for meet, div, ev, n in rows:
        dist, err = _tryParse(ev)
        if dist is None:
            bad.append((meet, div, ev, n))
    print(f"    {len(bad):,} of {len(rows):,} flagged divisions have an "
          f"event_short the parser cannot read")
    print(f"\n      {'meet':>8} {'div':>6} {'rows':>6}  event_short")
    for meet, div, ev, n in bad[:25]:
        print(f"      {meet:>8} {div:>6} {n:>6}  {repr(ev)}")

    c = Counter(ev for _m, _d, ev, _n in bad)
    print(f"\n    the {len(c)} distinct unreadable names in this crop:")
    for ev, k in c.most_common(20):
        print(f"      {repr(ev)[:50]:<52} {k:>4} divisions")


def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print("scanning every distinct event_short in results_tf ...")
        events = _distinctEvents(cur)
        print(f"  {len(events):,} distinct event_short values")

        ok, miss, crash = _split(events)
        _reportCorpus(ok, miss, crash)

        pairs = _flaggedPairs(os.path.join("scripts", "scored_div_tf.tsv"))
        _reportCrop(cur, pairs)
        conn.rollback()                   # read-only, always

    print("\n" + "=" * 70)
    print("  READ: a MISS means the row got no distance at all and the backfill\n"
          "  SKIPPED it -- it never reached the ruler. That is worse than a bad\n"
          "  label. The 'TOP UNPARSEABLE NAMES' list above IS the TF fix, and\n"
          "  it is a change to event_parse.distanceFromEventShort, not to any\n"
          "  override table.")
    print("=" * 70)


if __name__ == "__main__":
    main()