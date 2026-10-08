#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/requeue_empty_meets.py
# Purpose: Put this season's meets that no pass will ever ask again back in
#          meet_queue. DRY RUN unless --apply. Writes meet_queue ONLY: it
#          scrapes nothing, so it is safe beside a pipeline -- the next
#          launcher run does the fetching.
#
#     set -a; . /etc/xc-predictor.env; set +a
#     /srv/venv/bin/python scripts/requeue_empty_meets.py                 # dry run
#     /srv/venv/bin/python scripts/requeue_empty_meets.py --apply
#     /srv/venv/bin/python scripts/requeue_empty_meets.py --source anet --sport XC
#
# ★ THE OWNER (2026-10-08): "I see no Purple Valley classic, or woodbridge."
#   Two populations are stranded, one per feed:
#
#   anet  -- DONE (1) with 0 results and NO meet row: a meet asked before it
#            had divisions, so nothing was saved to carry its name or date,
#            and every re-ask pass starts from that row. queue_meets.
#            emptyUnrecorded; the launcher now requeues these itself at
#            start-up, so --apply here only does it sooner.
#   tfrrs -- ids whose queue row was DELETED ("event - deleted") and FAILED
#            (2) rows, inside this season's id range. run_tfrrs deletes any
#            page it does not recognise as a meet, and the tfrrs launcher
#            only ever resets state 3, so neither comes back below the
#            watermark. queue_meets.deletedIds / seasonRange.
#
# ! anet FAILURES (2) ARE NOT TOUCHED: database.resetInProgress turns every
#   one back to 0 at the start of each anet run already. They are counted.
# ! --include-missing ALSO re-asks anet state 4 ("no meet here") in the season
#   range. Before 2026-09-26 a meet whose Cloudflare retries ran out could be
#   written 4 (see launcher.runSession), and nothing below the watermark
#   re-asks a 4. Off by default: most 4s are real 404s and each costs a fetch.

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from database import getConn                                    # noqa: E402
import queue_meets as Q                                         # noqa: E402

TIMEOUT = os.environ.get("DIAG_STATEMENT_TIMEOUT", "5min")


def _sample(ids, n=12):
    head = ", ".join(str(i) for i in ids[:n])
    return head + (f", ... (+{len(ids) - n:,})" if len(ids) > n else "")


def _count(cur, source, sport, states, lo, hi):
    cur.execute("""SELECT count(*) FROM meet_queue
                   WHERE source = %s AND sport = %s AND scraped = ANY(%s)
                     AND meet_id BETWEEN %s AND %s""",
                (source, sport, list(states), lo, hi))
    return cur.fetchone()[0]


def _ids(cur, source, sport, states, lo, hi):
    cur.execute("""SELECT meet_id FROM meet_queue
                   WHERE source = %s AND sport = %s AND scraped = ANY(%s)
                     AND meet_id BETWEEN %s AND %s ORDER BY meet_id""",
                (source, sport, list(states), lo, hi))
    return [r[0] for r in cur.fetchall()]


def plan(cur, source, sport, include_missing=False):
    """{'update': [ids to set to 0], 'insert': [ids to add at 0], ...}."""
    out = {"update": [], "insert": [], "lines": []}
    say = out["lines"].append
    top = Q.watermark(cur, sport, source)
    lo, hi, how = Q.seasonRange(cur, sport, source)
    say(f"[{source}/{sport}] watermark {top}; season ids {lo}..{hi} ({how})")
    if top is None or lo is None or hi is None:
        say(f"[{source}/{sport}]   nothing to plan from")
        return out

    if source == "anet":
        unrec = Q.emptyUnrecorded(cur, sport, top, source=source)
        say(f"[{source}/{sport}]   done, 0 results, no meet row "
            f"(last {Q.UNDATED_SPAN:,} ids below the watermark): {len(unrec):,}"
            + (f"  [{_sample(unrec)}]" if unrec else ""))
        out["update"] += unrec
        failed = _count(cur, source, sport, (2, 3), lo, hi)
        say(f"[{source}/{sport}]   failed/stranded in season range: {failed:,} "
            f"(reset by the next anet launch itself -- not touched)")
        if include_missing:
            miss = _ids(cur, source, sport, (4,), lo, top)
            say(f"[{source}/{sport}]   'no meet here' (4) at or below the "
                f"watermark: {len(miss):,}")
            out["update"] += miss
    else:
        failed = _ids(cur, source, sport, (2,), lo, hi)
        say(f"[{source}/{sport}]   failed (2) in season range: {len(failed):,}"
            + (f"  [{_sample(failed)}]" if failed else ""))
        out["update"] += failed
        gone = Q.deletedIds(cur, sport, lo, min(hi, top), source)
        say(f"[{source}/{sport}]   deleted ('event - deleted') at or below the "
            f"watermark: {len(gone):,}" + (f"  [{_sample(gone)}]" if gone else ""))
        out["insert"] += gone
    out["update"] = sorted(set(out["update"]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=Q.SOURCES)
    ap.add_argument("--sport", choices=Q.SPORTS, default="XC",
                    help="default XC (the season in progress)")
    ap.add_argument("--include-missing", action="store_true",
                    help="anet: also re-ask state 4 in the season range")
    ap.add_argument("--apply", action="store_true",
                    help="write meet_queue (default: dry run)")
    args = ap.parse_args()

    sources = [args.source] if args.source else list(Q.SOURCES)
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{TIMEOUT}'")
            for source in sources:
                p = plan(cur, source, args.sport, args.include_missing)
                for line in p["lines"]:
                    print(line, flush=True)
                if not args.apply:
                    continue
                n_up = Q.requeue(cur, args.sport, p["update"], source) \
                    if p["update"] else 0
                n_in = 0
                if p["insert"]:
                    cur.execute("""
                        INSERT INTO meet_queue (meet_id, sport, source, scraped)
                        SELECT g, %s, %s, 0 FROM unnest(%s::bigint[]) g
                        ON CONFLICT (meet_id, sport, source) DO NOTHING
                    """, (args.sport, source, p["insert"]))
                    n_in = cur.rowcount
                print(f"[{source}/{args.sport}]   APPLIED: {n_up:,} set to due, "
                      f"{n_in:,} rows added at due", flush=True)
        if args.apply:
            conn.commit()
            print("\nCommitted. The next launcher run (anet: scripts/launcher.py;"
                  " tfrrs: tfrrs/driver/launch_tfrrs.py) scrapes them.")
        else:
            conn.rollback()
            print("\nDRY RUN -- nothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
