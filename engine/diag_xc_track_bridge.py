#!/usr/bin/env python3
"""
diag_xc_track_bridge.py -- can the flat-400 reference anchor CROSS COUNTRY?

    python engine/diag_xc_track_bridge.py
    python engine/diag_xc_track_bridge.py --window 30 45 60

★★ THE QUESTION, AND WHY IT IS A COUNT RATHER THAN AN ARGUMENT (owner,
   2026-09-20: "give Xc an absolute anchor too sure (although shouldn't it just
   be a track still?)").

   Track now has an absolute anchor: a flat outdoor 400m oval IS 0.0, and
   indoor sits +0.3% off it by assertion. Cross country has none. The bracket
   engine groups cells by (sport, era) and pins each group's vote-weighted mean
   to zero, so XC's zero is its OWN mean -- a see-saw. If California's courses
   are heavily raced and genuinely fast, holding the mean at zero MATHEMATICALLY
   REQUIRES everything else to go negative. That is the owner's standing
   complaint, and it is a property of the constraint, not of the courses.

   Merging the gauge groups would let the track reference anchor XC directly.
   The objection on record is mu: "The seasons never overlap, so the
   fall-to-spring difference is surface PLUS six months of fitness,
   inseparably. It is a definition."

⚠ THAT OBJECTION IS ABOUT OUTDOOR TRACK, AND INDOOR IS NOT OUTDOOR. XC
  finishes in early December (NXN, Foot Locker); indoor season STARTS in
  December. The gap is weeks, not six months -- and it is inside the bracket
  window (21-45 days). Indoor is itself now pinned to the absolute scale. So
  there is a candidate chain:

      XC --(athletes racing both within the window)--> indoor --(+0.3%)--> 0.0

  Whether that chain carries any weight is the question this script answers.

★ WHAT IT MEASURES, AND WHAT EACH NUMBER DECIDES:
    bridging athlete-seasons   athletes with an XC race and a TRACK race whose
                               dates are within `window` days. These are the
                               only rows that can transmit the anchor.
    bridging pairs             the actual within-window XC/track race pairs --
                               the edges, which is what the engine's local
                               level comparison uses.
    share of XC rows reachable the fraction of XC rows belonging to an athlete
                               who bridges. A large share means XC is connected
                               to the anchored side and merging the gauge
                               groups is identified; a small one means it would
                               drift, and XC needs its own reference class
                               instead.
    indoor vs outdoor split    which side the bridge lands on. Bridging only to
                               OUTDOOR track is the six-month case mu exists
                               for and does NOT count as evidence.

! READ-ONLY. It writes nothing and changes no rating.
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_WINDOWS = (21, 30, 45, 60)
DEFAULT_SINCE = 2010
DEFAULT_TIMEOUT_S = 1800          # 30 min per statement, then it gives up loudly
DEFAULT_SAMPLE = 100              # percent of athletes

# ⚠ date IS TEXT. `date::date` throws on anything that is not a real date, and
#   so does substr(date,1,4)::int on 'unknown'. The guard and the cast must not
#   be separable by the planner, which is what a MATERIALIZED subquery buys.
_DATE_OK = r"^(19|20)[0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def _hasColumn(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _say(msg):
    print(f"  ... {msg}", flush=True)


def _timed(cur, label, sql, params=None):
    t0 = time.time()
    cur.execute(sql, params or {})
    n = cur.rowcount
    _say(f"{label}: {max(n, 0):,} rows, {time.time() - t0:.0f}s")
    return n


def build(cur, since=DEFAULT_SINCE, sample=DEFAULT_SAMPLE,
          timeout_s=DEFAULT_TIMEOUT_S):
    """The two indexed day-tables every window is then answered from.

    ⚠⚠ WHY THIS IS NOT ONE QUERY ANY MORE (owner, 2026-09-20: "I have a
       feeling the bridge is gonna hang"). It would have.

       The previous version reduced each side to DISTINCT (person, day) and
       joined them on person_id with a date range. That reduction does almost
       nothing to the XC side -- an athlete races cross country at most once a
       day, so distinct person-days is very nearly the row count -- and the
       join then hash-matches an athlete's ENTIRE CAREER against itself before
       the range predicate narrows anything. Ten years is ~150 XC days against
       ~200 track days: thirty thousand pairs per athlete, of which a handful
       are inside the window. That is the same explosion as the first version,
       one grain further down.

    ★ THE SHAPE THAT WORKS: narrow the PEOPLE first, then ask each XC day a
      yes/no question through an index.

        1. b_people  athletes with rows in BOTH tables. Most cross-country
                     runners are not in results_tf at all, and they cannot
                     carry the anchor by definition, so they leave here --
                     before anything expensive touches them. No date cast in
                     this step at all, which also makes it the cheapest.
        2. b_xc/b_tf race days for those people only, indexed on
                     (person_id, d).
        3. per window an EXISTS per XC day, which is an index probe. No cross
           product is ever materialised.

    ! AND IT CANNOT RUN FOREVER. statement_timeout is set on the connection,
      so a step that is going to take all night is killed and SAYS which step
      it was, instead of being discovered tomorrow. --sample gets an answer
      first: shares under a hash sample of PEOPLE are unbiased, so 5% answers
      the question at a twentieth of the cost.
    """
    cur.execute(f"SET statement_timeout = '{int(timeout_s) * 1000}'")
    have_meta = _hasTable(cur, "meets_tf") and _hasColumn(cur, "meets_tf", "is_indoor")
    # ⚠ is_indoor IS AN INTEGER (the owner's traceback). ::int first, so the
    #   expression is right whether the column is an int or a bool.
    indoor_sel = ("COALESCE(m.is_indoor::int, 0) <> 0" if have_meta
                  else "NULL::boolean")
    indoor_join = ("LEFT JOIN meets_tf m ON m.meet_id = t.meet_id "
                   "AND m.div_id = t.div_id") if have_meta else ""
    # ! A HASH ON THE PERSON, NOT A LIMIT. Sampling rows would bias every
    #   share toward athletes who race a lot; sampling PEOPLE does not.
    # ⚠⚠ %% AND NOT %, BECAUSE psycopg2 OWNS THE PERCENT SIGN. Every execute
    #    below passes a params dict, so the driver runs its own interpolation
    #    over the string first and a bare `% 100` is read as a broken
    #    placeholder -- "unsupported format character". Doubling it is how a
    #    literal modulo survives into the SQL. Caught by running --sample
    #    against a real Postgres; it is invisible to any amount of reading.
    samp = ("" if sample >= 100
            else f"AND (abs(hashtext(person_id::text)) %% 100) < {int(sample)}")

    cur.execute("DROP TABLE IF EXISTS b_people, b_xc, b_tf")
    _timed(cur, "athletes in both sports", f"""
        CREATE TEMP TABLE b_people AS
        SELECT person_id FROM (
            SELECT DISTINCT person_id FROM results
             WHERE person_id IS NOT NULL {samp}
            INTERSECT
            SELECT DISTINCT person_id FROM results_tf
             WHERE person_id IS NOT NULL {samp}
        ) q
    """)
    cur.execute("CREATE INDEX ON b_people (person_id)")
    cur.execute("ANALYZE b_people")

    # ★ THE ROW COUNT RIDES ALONG. The gauge is VOTE-WEIGHTED, so the share
    #   that decides this is a share of ROWS; carrying count(*) here costs
    #   nothing and saves a second pass over results later.
    _timed(cur, "XC race days (bridging-capable athletes)", f"""
        CREATE TEMP TABLE b_xc AS
        WITH src AS MATERIALIZED (
            SELECT r.person_id, r.date
            FROM   results r JOIN b_people p USING (person_id)
            WHERE  r.date ~ %(re)s AND substr(r.date, 1, 4)::int >= %(since)s
        )
        SELECT person_id, date::date AS d, count(*)::bigint AS n_rows
        FROM   src GROUP BY 1, 2
    """, {"re": _DATE_OK, "since": since})
    cur.execute("CREATE INDEX ON b_xc (person_id, d)")
    cur.execute("ANALYZE b_xc")

    _timed(cur, "track race days", f"""
        CREATE TEMP TABLE b_tf AS
        WITH src AS MATERIALIZED (
            SELECT t.person_id, t.date, {indoor_sel} AS indoor
            FROM   results_tf t
            JOIN   b_people p ON p.person_id = t.person_id
            {indoor_join}
            WHERE  t.date ~ %(re)s AND substr(t.date, 1, 4)::int >= %(since)s
        )
        SELECT DISTINCT person_id, date::date AS d, indoor FROM src
    """, {"re": _DATE_OK, "since": since})
    cur.execute("CREATE INDEX ON b_tf (person_id, d)")
    cur.execute("ANALYZE b_tf")

    # The denominator: every XC row in scope, bridging-capable or not.
    _say("counting all XC rows in scope...")
    cur.execute(f"""
        SELECT count(*) FROM results
        WHERE person_id IS NOT NULL AND date ~ %(re)s
          AND substr(date, 1, 4)::int >= %(since)s {samp}
    """, {"re": _DATE_OK, "since": since})
    total_rows = int(cur.fetchone()[0] or 0)
    _say(f"XC rows in scope: {total_rows:,}")
    return {"total_rows": total_rows, "has_surface": have_meta,
            "sample": sample, "since": since}


def measure(cur, window):
    """One window, answered by index probes. No cross product."""
    t0 = time.time()
    cur.execute("""
        SELECT count(*)                                        AS xc_days,
               sum(n_rows)                                     AS xc_rows,
               count(*) FILTER (WHERE ind OR outd)             AS bridged_days,
               sum(n_rows) FILTER (WHERE ind OR outd)          AS bridged_rows,
               count(*) FILTER (WHERE ind)                     AS indoor_days,
               sum(n_rows) FILTER (WHERE ind)                  AS indoor_rows,
               count(*) FILTER (WHERE outd AND NOT COALESCE(ind, false))
                                                               AS outdoor_only_days
        FROM (
            SELECT x.n_rows,
                   EXISTS (SELECT 1 FROM b_tf t
                            WHERE t.person_id = x.person_id
                              AND t.d BETWEEN x.d - %(w)s AND x.d + %(w)s
                              AND t.indoor)                    AS ind,
                   EXISTS (SELECT 1 FROM b_tf t
                            WHERE t.person_id = x.person_id
                              AND t.d BETWEEN x.d - %(w)s AND x.d + %(w)s
                              AND NOT COALESCE(t.indoor, false)) AS outd
            FROM   b_xc x
        ) q
    """, {"w": window})
    keys = ("xc_days", "xc_rows", "bridged_days", "bridged_rows",
            "indoor_days", "indoor_rows", "outdoor_only_days")
    out = dict(zip(keys, [int(v or 0) for v in cur.fetchone()]))
    out["window"] = window
    _say(f"window {window}d: {time.time() - t0:.0f}s")
    return out


def report(rows, meta):
    total = max(meta["total_rows"], 1)
    print(f"\n[bridge] can the flat-400 reference reach CROSS COUNTRY?")
    print(f"  seasons {meta['since']}+, {meta['sample']}% of athletes, "
          f"{meta['total_rows']:,} XC rows in scope\n")
    print(f"  {'window':>7} {'bridged XC rows':>17} {'share':>8} "
          f"{'via INDOOR':>13} {'share':>8}")
    for r in rows:
        print(f"  {r['window']:>7} {r['bridged_rows']:>17,} "
              f"{100.0 * r['bridged_rows'] / total:>7.1f}% "
              f"{r['indoor_rows']:>13,} "
              f"{100.0 * r['indoor_rows'] / total:>7.1f}%")
    if not meta["has_surface"]:
        print("\n  ⚠ meets_tf has no is_indoor here, so 'via INDOOR' is "
              "UNKNOWN and reads 0.\n    The bridged column is then an upper "
              "bound only.")

    # ★★ THE VERDICT ON THE INDOOR COLUMN, NOT THE HEADLINE. An XC-to-OUTDOOR
    #    pair spans fall to spring: fitness plus surface, inseparably, which is
    #    the reason mu is a definition rather than a measurement. Only the
    #    indoor bridge is evidence about surface, so only it can decide this.
    best = max(rows, key=lambda r: r["indoor_rows"])
    share = best["indoor_rows"] / total
    print(f"\n  BEST WINDOW {best['window']}d: {100.0 * share:.1f}% of XC rows "
          f"sit within {best['window']} days of an INDOOR race by the same "
          f"athlete.")
    if share >= 0.25:
        print("  -> FAT ENOUGH. Merging the gauge groups is identified: the "
              "flat-400\n     reference reaches XC through indoor. Score it "
              "on the holdout before\n     it becomes the default.")
    elif share >= 0.05:
        print("  -> THIN. The bridge exists but carries little weight; merging "
              "would let\n     XC drift on a small subset. Prefer a "
              "hand-named XC reference class.")
    else:
        print("  -> ABSENT. XC cannot be anchored to the track reference by "
              "iteration.\n     It needs its own reference class -- named "
              "courses held at an asserted\n     value -- which is the only "
              "remaining option that breaks the see-saw.")
    b = best["bridged_rows"] / total
    if b > share * 1.5:
        print(f"\n  ! the headline 'bridged' share is {100.0 * b:.1f}%, well "
              f"above the indoor {100.0 * share:.1f}%.")
        print("    The difference is XC-to-OUTDOOR pairs, which are the "
              "fall-to-spring gap\n    and NOT evidence about surface. Read "
              "the indoor column.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=int, nargs="+", default=list(DEFAULT_WINDOWS))
    ap.add_argument("--since", type=int, default=DEFAULT_SINCE,
                    help=f"earliest season (default {DEFAULT_SINCE})")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help="percent of ATHLETES to use (default 100). Shares "
                         "under a person hash are unbiased, so --sample 5 "
                         "answers the question at a twentieth of the cost -- "
                         "run that first")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help=f"per-statement seconds before it gives up "
                         f"(default {DEFAULT_TIMEOUT_S})")
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        try:
            meta = build(cur, args.since, args.sample, args.timeout)
            rows = [measure(cur, w) for w in args.window]
        except Exception as exc:                          # noqa: BLE001
            # ! WHICH STEP, NOT JUST "IT FAILED". A timeout here is a fact
            #   about the corpus (or about work_mem), and the next run wants
            #   to know where to point --sample.
            print(f"\n[bridge] STOPPED: {type(exc).__name__}: {exc}")
            print("  If that is a statement timeout, re-run with "
                  "--sample 5 (or a smaller --since) first;\n  the shares are "
                  "unbiased under a person hash, so a sample answers the "
                  "question.")
            raise SystemExit(1)
    report(rows, meta)


if __name__ == "__main__":
    main()
