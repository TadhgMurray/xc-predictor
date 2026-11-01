#!/usr/bin/env python3
"""
check_scrape_gaps.py -- ways a meet can go missing, counted. READ-ONLY.

    cd /srv/xc-predictor && set -a && . /etc/xc-predictor.env && set +a && /srv/venv/bin/python scripts/check_scrape_gaps.py

  1. ids below each walk's watermark that were NEVER queued (the walk can
     only start above the watermark, so these are never asked)
  2. athletic.net watermark with and without AthleticLIVE's rows
  3. AthleticLIVE meets landed before their last day (could be short)
  4. timer meets promoted on or before their own day (could be short)
  5. claims stuck in flight (3) and failed meets (2), per feed
  6. athletic.net meets skipped because a timer had them (eyeball these)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

SPAN = int(os.environ.get("GAP_SPAN", "2000"))


def _q(cur, sql, args=()):
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    except Exception as exc:                                        # noqa: BLE001
        cur.connection.rollback()
        return [("error", f"{type(exc).__name__}: {str(exc).splitlines()[0]}")]


def main():
    from database import getConn
    import queue_meets as Q
    with getConn() as conn:
        cur = conn.cursor()

        print(f"1. never-queued ids in the {SPAN:,} below each watermark")
        for source in ("anet", "tfrrs"):
            for sport in ("XC", "TF"):
                try:
                    top = Q.watermark(cur, sport, source)
                except Exception as exc:                            # noqa: BLE001
                    conn.rollback()
                    print(f"   {source}/{sport}: {exc}")
                    continue
                if top is None:
                    print(f"   {source}/{sport}: no results yet")
                    continue
                (n,), = _q(cur, """SELECT count(*) FROM generate_series(%s, %s) g
                                   WHERE NOT EXISTS (SELECT 1 FROM meet_queue q WHERE q.meet_id = g
                                                     AND q.sport = %s AND q.source = %s)""",
                           (max(1, top - SPAN), top, sport, source))
                # ! TFRRS ids skip numbers (the 758 hole ids deleted on
                #   2026-10-31 show here), and its "latest" step queues
                #   new meets from TFRRS's own list -- only anet's dense ids
                #   are a gap by count alone
                print(f"   {source}/{sport}: watermark {top:,}; never queued {n:,}"
                      + (("   <-- look" if source == "anet" else "   (TFRRS ids skip numbers: fine)")
                         if isinstance(n, int) and n > 50 else ""))

        print("2. athletic.net watermark: own rows vs with AthleticLIVE's")
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            for own, allm in _q(cur, f"""SELECT max(meet_id) FILTER (WHERE result_id < 3000000000000),
                                               max(meet_id) FROM {table} WHERE source = 'anet'"""):
                print(f"   {sport}: own {own}, with AthleticLIVE {allm}"
                      + ("   (AthleticLIVE ahead; the walk uses own rows: fine)"
                         if own and allm and allm > own else ""))

        print("3. AthleticLIVE meets landed on or before their last day")
        for row in _q(cur, """SELECT sport, count(*) FROM alive_meets
                              WHERE landed_n > 0 AND landed_at::date <= COALESCE(date_end, meet_date)::date
                              GROUP BY 1 ORDER BY 1"""):
            print(f"   {row}")

        print("4. timer meets promoted on or before their own day")
        for row in _q(cur, """SELECT count(*) FROM meets_ext
                              WHERE status = 'promoted' AND promoted_at::date <= meet_date::date"""):
            print(f"   {row}")

        print("5. queue: in flight (3) and failed (2), per feed "
              "(in flight resets when that scraper next starts)")
        for row in _q(cur, """SELECT source, sport, count(*) FILTER (WHERE scraped = 3) AS in_flight,
                                     count(*) FILTER (WHERE scraped = 2) AS failed
                              FROM meet_queue GROUP BY 1, 2 ORDER BY 1, 2"""):
            print(f"   {row}")

        print("6. athletic.net meets skipped for a timer's copy (newest 15)")
        for row in _q(cur, """SELECT t.meet_id, s.name, s.meet_date, m.meet_name, t.how
                              FROM anet_local_twin t
                              LEFT JOIN anet_meet_seen s USING (meet_id, sport)
                              LEFT JOIN meets_ext m ON m.ext_meet_id = t.ext_meet_id
                              ORDER BY t.matched_at DESC LIMIT 15"""):
            print(f"   {row}")
        conn.rollback()


if __name__ == "__main__":
    main()
