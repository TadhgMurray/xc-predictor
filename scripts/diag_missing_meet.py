#!/usr/bin/env python3
"""
diag_missing_meet.py -- where did this meet go? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_missing_meet.py woodbridge --year 2026
    /srv/venv/bin/python scripts/diag_missing_meet.py "purple valley" --year 2026 --source tfrrs
    /srv/venv/bin/python scripts/diag_missing_meet.py --id 284512 --source anet
    /srv/venv/bin/python scripts/diag_missing_meet.py --summary

(owner, 2026-10-08: "I see no Purple Valley classic, or woodbridge", after a
full anet scrape and a tfrrs drain both reported finishing.) A meet can be
missing from the site at any of these steps, and each leaves a different trace:

  1. its id was never queued                -> no meet_queue row, no meet row
  2. queued, never reached / still due       -> meet_queue 0 or 3
  3. scraped and FAILED                      -> meet_queue 2 (anet: retried at
                                                the next launch; tfrrs: only
                                                with TFRRS_RETRY_FAILED=1)
  4. asked before it had divisions           -> meet_queue 1, NO meet row, no
                                                results: the name is nowhere,
                                                so a NAME search finds nothing
  5. scheduled, results not posted at ask    -> meet row, 0 results
  6. tfrrs page not recognised as a meet     -> queue row DELETED, nothing left
  7. scraped fine, not on the site yet       -> results > 0 but not in
                                                homepage_recent (nightly build)

Name search covers meets, meets_tf_meta, meets_tf, meets_tfrrs. Per matched
id it prints every meet_queue row (all feeds and sports), result counts,
meet_extras, the TF recovery tables and homepage_recent. When the name is
nowhere -- step 4 or 6 -- look the meet up on the feed in your browser, take
the id from its URL, and pass --id.

--summary counts, per feed and sport, this season's ids by what happened to
them (the season range is queue_meets.seasonRange: the lowest id dated in the
recent window up to the highest id ever queued).

Every table is optional (to_regclass); every statement has a timeout
(DIAG_STATEMENT_TIMEOUT, default 2min) and a timeout skips that one section.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from database import getConn                                    # noqa: E402

TIMEOUT = os.environ.get("DIAG_STATEMENT_TIMEOUT", "2min")
STATE = {0: "due", 1: "done", 2: "FAILED", 3: "in-progress (claimed)",
         4: "not-a-meet (404)"}
ANET_URL = {"XC": "https://www.athletic.net/CrossCountry/meet/{id}",
            "TF": "https://www.athletic.net/TrackAndField/meet/{id}"}
TFRRS_URL = {"XC": "https://www.tfrrs.org/results/xc/{id}",
             "TF": "https://www.tfrrs.org/results/{id}"}


class Db:
    """A cursor that survives a timeout or a missing column: each query runs
    on its own, and a failure prints one line and returns []."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self._tables = {}
        self.cur.execute(f"SET statement_timeout = '{TIMEOUT}'")
        self.conn.commit()          # session-level, so a rollback keeps it

    def has(self, table):
        if table not in self._tables:
            self._tables[table] = bool(self.q1(
                "SELECT to_regclass(%s) IS NOT NULL", (table,)))
        return self._tables[table]

    def hasCol(self, table, col):
        return bool(self.q1("""SELECT 1 FROM information_schema.columns
                               WHERE table_name = %s AND column_name = %s""",
                            (table, col)))

    def q(self, sql, params=(), what=None):
        try:
            self.cur.execute(sql, params)
            rows = self.cur.fetchall()
            self.conn.rollback()    # read-only: hold no snapshot or locks
            return rows
        except Exception as exc:                          # noqa: BLE001
            self.conn.rollback()
            print(f"    ({what or 'query'} skipped: "
                  f"{type(exc).__name__}: {str(exc).splitlines()[0]})",
                  flush=True)
            return []

    def q1(self, sql, params=(), what=None):
        rows = self.q(sql, params, what)
        return rows[0][0] if rows else None

    def close(self):
        try:
            self.cur.execute("RESET statement_timeout")
            self.conn.commit()
        except Exception:                                 # noqa: BLE001
            self.conn.rollback()


def _datePrefix(args):
    return args.date or (str(args.year) if args.year else None)


# --------------------------------------------------------------- name search

def findByName(db, frag, prefix, source):
    """[(source, sport, meet_id, name, date, where)] across the meet tables."""
    pat = f"%{frag}%"
    out = []

    def dated(col):
        # an undated row is kept (and flagged) rather than filtered away: the
        # meets.meet_date column only exists since 2026-09-18
        return (f" AND ({col} LIKE %s OR {col} IS NULL)", (prefix + "%",)) \
            if prefix else ("", ())

    if source in (None, "anet") and db.has("meets"):
        has_src = db.hasCol("meets", "source")
        cl, p = dated("m.meet_date")
        src = "COALESCE(m.source, 'anet')" if has_src else "'anet'"
        for r in db.q(f"""
                SELECT {src}, m.meet_id, min(m.meet_name), min(m.meet_date),
                       count(*), min(m.state)
                FROM   meets m
                WHERE  m.meet_name ILIKE %s{cl}
                GROUP  BY 1, 2 ORDER BY 2""", (pat,) + p, "meets"):
            out.append((r[0], "XC", r[1], r[2], r[3],
                        f"meets ({r[4]} division rows, state {r[5]})"))

    if source in (None, "anet") and db.has("meets_tf_meta"):
        cl, p = dated("t.meet_date")
        for r in db.q(f"""
                SELECT t.meet_id, t.meet_name, t.meet_date, t.state,
                       t.has_results, t.finalized
                FROM   meets_tf_meta t
                WHERE  t.meet_name ILIKE %s{cl} ORDER BY 1""",
                      (pat,) + p, "meets_tf_meta"):
            out.append(("anet", "TF", r[0], r[1], r[2],
                        f"meets_tf_meta (state {r[3]}, has_results {r[4]}, "
                        f"finalized {r[5]})"))

    if source in (None, "anet") and db.has("meets_tf") and not prefix:
        # ! meets_tf has no date, so only on an unfiltered search; with a
        #   year or date, meets_tf_meta (which has one) answers for track.
        seen = {(o[1], o[2]) for o in out}
        for r in db.q("""
                SELECT meet_id, min(meet_name), count(*) FROM meets_tf
                WHERE  meet_name ILIKE %s GROUP BY 1 ORDER BY 1""",
                      (pat,), "meets_tf"):
            if ("TF", r[0]) not in seen:
                out.append(("anet", "TF", r[0], r[1], None,
                            f"meets_tf ({r[2]} event-div rows)"))

    if source in (None, "tfrrs") and db.has("meets_tfrrs"):
        cl, p = dated("t.date")
        for r in db.q(f"""
                SELECT t.sport, t.meet_id, t.meet_name, t.date, t.state,
                       t.venue_name
                FROM   meets_tfrrs t
                WHERE  t.meet_name ILIKE %s{cl} ORDER BY 2""",
                      (pat,) + p, "meets_tfrrs"):
            out.append(("tfrrs", r[0], r[1], r[2], r[3],
                        f"meets_tfrrs (state {r[4]}, venue {r[5]})"))
    return out


# ------------------------------------------------------------- one meet's id

def describeId(db, meet_id, source=None, sport=None):
    print(f"\n  ---- meet_id {meet_id}"
          + (f"  [{source}/{sport}]" if source else ""), flush=True)

    qrows = []
    if db.has("meet_queue"):
        has_src = db.hasCol("meet_queue", "source")
        qrows = db.q(f"""
            SELECT {'source' if has_src else "'anet'"}, sport, scraped
            FROM   meet_queue WHERE meet_id = %s ORDER BY 1, 2""",
                     (meet_id,), "meet_queue")
    if qrows:
        for src, sp, st in qrows:
            print(f"    meet_queue  {src:<5} {sp}  scraped={st} "
                  f"({STATE.get(st, '?')})")
    else:
        print("    meet_queue  NO ROW for any feed or sport "
              "(never queued, or a tfrrs row deleted as 'not a meet')")

    counts = {}
    for table, sp in (("results", "XC"), ("results_tf", "TF")):
        if not db.has(table):
            continue
        for src, n, d0, d1 in db.q(f"""
                SELECT source, count(*), min(date), max(date)
                FROM   {table} WHERE meet_id = %s GROUP BY 1 ORDER BY 1""",
                                   (meet_id,), table):
            counts[(src, sp)] = n
            print(f"    {table:<11} {src:<5} {n:,} rows, dates {d0} .. {d1}")
    if not counts:
        print("    results     NONE in results or results_tf")

    if db.has("meets"):
        for r in db.q("""SELECT count(*), min(meet_name), min(meet_date)
                         FROM meets WHERE meet_id = %s HAVING count(*) > 0""",
                      (meet_id,), "meets"):
            print(f"    meets       {r[0]} division rows, '{r[1]}', {r[2]}")
    if db.has("meets_tf_meta"):
        for r in db.q("""SELECT meet_name, meet_date, has_results, finalized
                         FROM meets_tf_meta WHERE meet_id = %s""",
                      (meet_id,), "meets_tf_meta"):
            print(f"    meets_tf_meta '{r[0]}', {r[1]}, has_results {r[2]}, "
                  f"finalized {r[3]}")
    if db.has("meets_tf"):
        n = db.q1("SELECT count(*) FROM meets_tf WHERE meet_id = %s",
                  (meet_id,), "meets_tf")
        if n:
            print(f"    meets_tf    {n} event-div rows")
    if db.has("meets_tfrrs"):
        for r in db.q("""SELECT sport, meet_name, date FROM meets_tfrrs
                         WHERE meet_id = %s""", (meet_id,), "meets_tfrrs"):
            print(f"    meets_tfrrs {r[0]} '{r[1]}', {r[2]}")
    if db.has("meet_extras"):
        rows = db.q("""SELECT source, sport FROM meet_extras
                       WHERE meet_id = %s""", (meet_id,), "meet_extras")
        if rows:
            print("    meet_extras " + ", ".join(f"{a}/{b}" for a, b in rows))
    if db.has("tf_recovery_queue"):
        rows = db.q("""SELECT scraped, count(*) FROM tf_recovery_queue
                       WHERE meet_id = %s GROUP BY 1""", (meet_id,),
                    "tf_recovery_queue")
        if rows:
            print("    tf_recovery_queue " + ", ".join(
                f"{STATE.get(s, s)}: {n}" for s, n in rows))
    if db.has("tf_scraped_events"):
        n = db.q1("SELECT count(*) FROM tf_scraped_events WHERE meet_id = %s",
                  (meet_id,), "tf_scraped_events")
        if n:
            print(f"    tf_scraped_events {n}")
    if db.has("homepage_recent"):
        rows = db.q("""SELECT sport, rank, n_results FROM homepage_recent
                       WHERE meet_id = %s""", (meet_id,), "homepage_recent")
        print("    homepage_recent " + (", ".join(
            f"{s} rank {r} ({n} results)" for s, r, n in rows) if rows else
            "not in the /meets precompute (nightly; needs results >= its floor)"))

    print("    verdict: " + verdict(qrows, counts, db, meet_id, source, sport))
    urls = []
    for src, sp, _st in qrows or ([(source, sport, None)] if source else []):
        tmpl = (ANET_URL if src == "anet" else TFRRS_URL).get(sp)
        if tmpl:
            urls.append(tmpl.format(id=meet_id))
    if not urls:
        for sp in ([sport] if sport else ["XC", "TF"]):
            urls += [ANET_URL[sp].format(id=meet_id),
                     TFRRS_URL[sp].format(id=meet_id)]
    for u in sorted(set(urls)):
        print(f"    check in your browser: {u}")


def verdict(qrows, counts, db, meet_id, source, sport):
    relevant = [r for r in qrows
                if (source is None or r[0] == source)
                and (sport is None or r[1] == sport)]
    if any(n for (src, sp), n in counts.items()
           if (source is None or src == source)
           and (sport is None or sp == sport)):
        return ("HAS RESULTS -- the scrape has it. If the site does not show "
                "it, the site build / precompute has not run since, or it is "
                "listed under another name/date.")
    if not relevant:
        return ("NEVER QUEUED for this feed/sport (or deleted by tfrrs as "
                "'not a meet'). Above the watermark the next launch walks to "
                "it; below it, requeue_empty_meets.py.")
    states = {r[2] for r in relevant}
    if 3 in states or 0 in states:
        return "QUEUED, NOT SCRAPED YET (due or claimed) -- the next run takes it."
    if 2 in states:
        return ("FAILED on the last attempt -- anet retries it at the next "
                "launch (resetInProgress); tfrrs only with TFRRS_RETRY_FAILED=1 "
                "or requeue_empty_meets.py --source tfrrs. The run log line "
                "for this id says why.")
    if 4 in states:
        return ("THE FEED SAID 'NO MEET HERE' when asked. Re-asked only inside "
                "a forward-walk block (or --include-missing).")
    has_meet_row = any(db.q1(sql, (meet_id,)) for sql in (
        "SELECT 1 FROM meets WHERE meet_id = %s LIMIT 1" if db.has("meets") else None,
        "SELECT 1 FROM meets_tf WHERE meet_id = %s LIMIT 1" if db.has("meets_tf") else None,
        "SELECT 1 FROM meets_tfrrs WHERE meet_id = %s LIMIT 1" if db.has("meets_tfrrs") else None,
    ) if sql)
    if has_meet_row:
        return ("DONE WITH 0 RESULTS, meet row present -- scheduled when asked. "
                "Re-asked at launch while dated within the recent window "
                "(queue_meets.emptyRecent / scheduledToRetry).")
    return ("DONE WITH 0 RESULTS AND NO MEET ROW -- asked before it had "
            "divisions, nothing saved. Before 2026-10-08 nothing re-asked "
            "these once the watermark passed (queue_meets.emptyUnrecorded now "
            "does at launch; requeue_empty_meets.py --apply does it now).")


# ------------------------------------------------------------------ summary

def summary(db, sources, sports):
    import queue_meets as Q
    for source in sources:
        for sport in sports:
            print(f"\n==== {source}/{sport}", flush=True)
            try:
                with db.conn.cursor() as cur:
                    top = Q.watermark(cur, sport, source)
                    lo, hi, how = Q.seasonRange(cur, sport, source)
                db.conn.rollback()
            except Exception as exc:                      # noqa: BLE001
                db.conn.rollback()
                print(f"  (skipped: {type(exc).__name__}: "
                      f"{str(exc).splitlines()[0]})")
                continue
            print(f"  watermark (highest id with results) {top}; season ids "
                  f"{lo}..{hi} ({how})")
            if lo is None or hi is None:
                continue
            t = Q.FEEDS[source][sport]
            meet_exists = Q._meetExistsSql(source, sport)
            rows = db.q(f"""
                SELECT q.scraped,
                       EXISTS (SELECT 1 FROM {t['results']} r
                               WHERE r.meet_id = q.meet_id
                                 AND r.source = q.source)    AS has_results,
                       {meet_exists}                          AS has_meet,
                       count(*)
                FROM   meet_queue q
                WHERE  q.source = %s AND q.sport = %s
                  AND  q.meet_id BETWEEN %s AND %s
                GROUP  BY 1, 2, 3 ORDER BY 1, 2, 3
            """, (source, sport, lo, hi), f"{source}/{sport} queue states")
            buckets = {}
            for st, res, meet, n in rows:
                if st == 1 and res:
                    k = "done, has results"
                elif st == 1 and meet:
                    k = "done, 0 results, meet row (scheduled; re-asked by date)"
                elif st == 1:
                    k = "done, 0 results, NO meet row (stranded before 10-08)"
                else:
                    k = STATE.get(st, f"state {st}")
                buckets[k] = buckets.get(k, 0) + n
            for k, n in sorted(buckets.items(), key=lambda kv: -kv[1]):
                print(f"  {n:>9,}  {k}")
            with db.conn.cursor() as cur:
                try:
                    gone = Q.deletedIds(cur, sport, lo, hi, source)
                except Exception as exc:                  # noqa: BLE001
                    gone = None
                    print(f"  (deleted-id count skipped: {type(exc).__name__})")
            db.conn.rollback()
            if gone is not None:
                print(f"  {len(gone):>9,}  NO queue row and no meet row "
                      f"(tfrrs: deleted as 'not a meet'; anet: never queued)")
            if top is not None:
                with db.conn.cursor() as cur:
                    try:
                        unrec = Q.emptyUnrecorded(cur, sport, top, source=source)
                        print(f"  {len(unrec):>9,}  of which the launcher now "
                              f"re-asks (emptyUnrecorded, last "
                              f"{Q.UNDATED_SPAN:,} ids below the watermark)")
                    except Exception as exc:              # noqa: BLE001
                        print(f"  (emptyUnrecorded skipped: {type(exc).__name__})")
                db.conn.rollback()


def main():
    ap = argparse.ArgumentParser(
        description="Where did this meet go? Read-only.")
    ap.add_argument("name", nargs="?", help="meet name fragment, any case")
    ap.add_argument("--year", type=int, help="meet date year, e.g. 2026")
    ap.add_argument("--date", help="meet date prefix: YYYY-MM or YYYY-MM-DD")
    ap.add_argument("--source", choices=("anet", "tfrrs"))
    ap.add_argument("--sport", choices=("XC", "TF"))
    ap.add_argument("--id", type=int, nargs="+", default=[],
                    help="inspect these meet ids directly")
    ap.add_argument("--summary", action="store_true",
                    help="this season's ids per feed and sport, by outcome")
    args = ap.parse_args()
    if not (args.name or args.id or args.summary):
        ap.error("give a name fragment, --id, or --summary")

    with getConn() as conn:
        db = Db(conn)
        try:
            if args.summary:
                summary(db, [args.source] if args.source else ["anet", "tfrrs"],
                        [args.sport] if args.sport else ["XC", "TF"])

            prefix = _datePrefix(args)
            if args.name:
                print(f"\n==== name ILIKE '%{args.name}%'"
                      + (f", date {prefix}*" if prefix else "")
                      + (f", source {args.source}" if args.source else ""),
                      flush=True)
                hits = findByName(db, args.name, prefix, args.source)
                if args.sport:
                    hits = [h for h in hits if h[1] == args.sport]
                for src, sp, mid, name, date, where in hits:
                    print(f"  {src:<5} {sp}  {mid:>8}  {date or 'undated':<10}  "
                          f"{name}   <- {where}")
                if not hits:
                    print("  NO MATCH in any meet table.")
                    if prefix:
                        older = findByName(db, args.name, None, args.source)
                        if older:
                            print("  Other editions (any date) -- the missing "
                                  "one's id is usually just above last year's:")
                            for src, sp, mid, name, date, _w in older[-10:]:
                                print(f"    {src:<5} {sp}  {mid:>8}  "
                                      f"{date or 'undated':<10}  {name}")
                    print("  A meet asked before it had divisions leaves NO "
                          "name anywhere (step 4 at the top of this script). Find it on the "
                          "feed in your browser, take the id from the URL, "
                          "and re-run with --id <id>. --summary shows how "
                          "many such ids this season has.")
                seen = set()
                for src, sp, mid, *_rest in hits:
                    if (src, sp, mid) not in seen:
                        seen.add((src, sp, mid))
                        describeId(db, mid, src, sp)
            for mid in args.id:
                describeId(db, mid, args.source, args.sport)
        finally:
            db.close()


if __name__ == "__main__":
    main()
