"""
diag_twin_person.py -- one physical race, stored twice, under TWO DIFFERENT
person_ids. Issue #15.

    python scripts/diag_twin_person.py                  # report
    python scripts/diag_twin_person.py --days 3
    python scripts/diag_twin_person.py --person 12345   # one athlete
    python scripts/diag_twin_person.py --write          # drop the tfrrs copies
    python scripts/diag_twin_person.py --unwrite        # remove what --write wrote

Run from the PROJECT ROOT. Reads results + meets; --write appends to
corrections.py and nothing else.

★ THIS IS NOT A DEDUP FAILURE. IT IS AN IDENTITY FAILURE UPSTREAM OF DEDUP,
  AND THAT IS THE WHOLE POINT OF THE FILE.

  The engine already removes cross-feed twins -- speed_ratings_db._dedupJoin
  anti-joins on (person_id, canon_meet_id) and keeps the anet copy. That rule
  cannot fire here, for two independent reasons, either of which alone
  defeats it:

    1. THE person_id DIFFERS, in one of TWO ways -- and the corpus says the
       second is by far the commoner. Either the tfrrs copy was attached to
       ANOTHER REAL ATHLETE (the Cam Kuss case, which puts a fabricated 157
       on somebody else's page), or it was never linked to the anet identity
       at all and sits on an unresolved tfrrs id of its own. Measured: the
       matched tfrrs ids are overwhelmingly small integers -- 73597, 382688,
       486299 -- against six- and eight-digit anet ids, which is the
       unlinked shape. Either way `tw.person_id = r.person_id` never matches
       and the twin survives as a second, separately-pooled athlete.
    2. THE DATES DIFFER BY A DAY. Measured on the Cam Kuss pair: 2023-09-15
       against 09-16, 2023-10-06 against 10-07, 2024-10-04 against 10-05 --
       same time to the tenth, same finishing place, same venue. Anything
       keyed on the date, canon_meet_id included, sees two meets.

  So building more dedup does not fix this, and #9c's canon-key sweep will
  not catch it.

★ THOUGH 88.1% OF THE PAIRS DO SHARE A canon_meet_id, AND THAT POINTS AT A
  BETTER FIX THAN THIS FILE. The meets ARE linked; only the person is not.
  So widening speed_ratings_db's twin rule to match on (canon_meet_id, time,
  place) INSTEAD OF (canon_meet_id, person_id) would remove most of these at
  the engine, permanently and without a generated list -- where the block
  this file appends is a snapshot that goes stale the moment the scrapers
  add more. Recorded here rather than done here: it changes what the pack
  contains, which is a pipeline-run decision, not a diagnostic's.

★ THE SIGNAL IS (venue, time, place), AND ALL THREE ARE LOAD-BEARING.
  Two athletes cannot both finish 49th in one race, so an exact finishing
  position beside an exact time turns "suspiciously similar" into "one row
  written twice" -- with no name matching and no fuzzy scoring anywhere.

⚠ AND THE VENUE IS NOT OPTIONAL, WHICH THE FIRST VERSION OF THIS FILE GOT
  WRONG AT SOME COST. It joined on (time, place) alone so that the
  one-day-apart case could still match, and reported 307,466 duplicates
  across 161,413 people -- Jonathon Bacigalupi at Killens Pond paired with
  Nick Riley at Cass Benton Park, one tfrrs id matching three unrelated anet
  ones. Pre-2015 times are recorded to WHOLE SECONDS, so the hundredths
  carry no information, and 15th place in 20:12 recurs across every
  September Saturday in the corpus. The venue is what makes the rest mean
  anything; the date is the field allowed to disagree, not the place.

⚠ WHAT IT COSTS WHEN IT IS WRONG, AND WHY --write ONLY DROPS. The clean
  repair is to move the rows to the right person. This does not do that:
  deciding two ids are the same person is a far harder claim than deciding a
  row is a duplicate, and unlink.py refuses the symmetric version of it for
  the same reason ("a wrong merge is far more damaging than a missed one").
  --write only adds the tfrrs copy to _RESULT_DROP_XC, which stops it being
  RATED. The wrong athlete keeps a page showing races that are not theirs --
  cosmetic, and reversible by deleting one generated block -- but the
  fabricated rating goes away, and the fabricated rating is the harm: the
  same 15:45.5 came out 157.0 under the twin and 129.4 under Campbell Kuss.
"""

import argparse
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from psycopg2.extras import RealDictCursor

# ! database IS IMPORTED LAZILY, INSIDE main(), AND THAT IS NOT STYLE.
#   `import database` pulls in config, which now RAISES when no password is
#   configured -- deliberately, so a tool can never connect to the wrong
#   corpus by accident. But --unwrite exists to UNDO a bad write, and the
#   moment you need it most is mid-incident, in a fresh shell that has not
#   sourced /etc/xc-predictor.env. A module-level import made the undo path
#   depend on the very configuration the incident may have disturbed.
#   Measured the hard way: --unwrite died on `RuntimeError: no database
#   password` while 137,045 wrongly-dropped result_ids sat in corrections.py.

MARKER = "# --- generated by scripts/diag_twin_person.py ---"

# How far apart the two feeds may date the same race. Measured at ONE day on
# the Kuss pair; 2 is one day of slack on top of the observed error.
DEFAULT_DAYS = 2

# ★ HOW MANY MATCHED RACES A PAIR OF ids NEEDS BEFORE IT MAY BE WRITTEN.
#
#   Same venue, same date, same finishing place is one race -- UNLESS the
#   meet ran several divisions, where a 20th place exists in each. Pre-2015
#   times are whole seconds (1300.00, 1411.00), so two divisions colliding on
#   both the time and the place is not impossible, and a single matched race
#   cannot be told apart from that.
#
#   A PAIR OF ids matched repeatedly can. James Kiernan's anet id matched
#   tfrrs 382688 nine times, across Franklin Park, Van Cortlandt, Siena and
#   Twin Ponds, over three seasons -- coincidence does not repeat nine times
#   at nine venues. This is propose_distances' corroboration rule in another
#   costume: the arithmetic finds the candidate, a second independent
#   occurrence is what promotes it to a fact.
#
# ⚠ IT IS A WRITE GATE, NOT A REPORT GATE. Single-race pairs stay in the
#   listing, because a real one-off mis-attachment is exactly the Kuss shape
#   and a human should still see it.
MIN_PAIR_RACES = 2

# ! TIME TO THE HUNDREDTH, VIA AN INTEGER. Rounding two REALs and comparing
#   them invites a float equality that fails on the last bit; multiplying to
#   centiseconds and casting to bigint makes the key exact and indexable.
_KEYS = """
    DROP TABLE IF EXISTS twin_key;
    CREATE UNLOGGED TABLE twin_key AS
        SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id,
               r.canon_meet_id,
               r.date::date                            AS d,
               (r.time_seconds * 100)::bigint          AS cs,
               r.place,
               -- ★ THE VENUE, AND IT IS THE GATE THAT MAKES THE REST MEAN
               --   ANYTHING. The first version of this file joined on
               --   (time, place) alone, so a race could match another race
               --   at a different course on the same weekend. Measured on
               --   the corpus: 307,466 "duplicates" across 161,413 people --
               --   Jonathon Bacigalupi at Killens Pond paired with Nick
               --   Riley at Cass Benton Park. Pre-2015 times are recorded to
               --   WHOLE SECONDS, so the hundredths carry no information and
               --   15th place in 20:12 happens many times every September.
               --
               -- ⚠ AND IT CANNOT BE STRING EQUALITY, because the two feeds
               --   write the same venue differently -- "WakeMed Soccer Park"
               --   against "WakeMed Soccer Park -- Cary NC". Exact matching
               --   would reject the very case this file was written for. The
               --   first 12 alphanumerics survive that suffix and still
               --   separate one park from another.
               left(regexp_replace(
                        lower(COALESCE(ma.course_name, mt.venue_name, '')),
                        '[^a-z0-9]', '', 'g'), 12)     AS venue_key
        FROM   results r
        LEFT   JOIN meets ma
                    ON ma.div_id = r.div_id AND r.source = 'anet'
        LEFT   JOIN meets_tfrrs mt
                    ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
        WHERE  r.person_id IS NOT NULL
          AND  r.time_seconds IS NOT NULL AND r.time_seconds > 0
          -- ⚠ place 0 AND NULL BOTH MEAN "NOT RECORDED", and treating either
          --   as a position would match every unplaced row against every
          --   other one. The test has no strength without it, so rows
          --   without a place are simply not examined.
          AND  r.place IS NOT NULL AND r.place > 0;
    DELETE FROM twin_key WHERE venue_key = '';
    CREATE INDEX ON twin_key (cs, place, venue_key);
    CREATE INDEX ON twin_key (person_id);
    ANALYZE twin_key;
"""

# ! anet ON THE LEFT, tfrrs ON THE RIGHT, so each pair is produced ONCE and
#   the survivor is decided by the same rule the engine's own dedup uses:
#   keep anet, it carries athlete_id and grade where tfrrs XC has neither.
_PAIRS = """
    SELECT a.result_id      AS anet_id,
           t.result_id      AS tfrrs_id,
           a.person_id      AS anet_person,
           t.person_id      AS tfrrs_person,
           a.d              AS anet_date,
           t.d              AS tfrrs_date,
           a.cs / 100.0     AS secs,
           a.place,
           a.meet_id        AS anet_meet,
           t.meet_id        AS tfrrs_meet,
           (a.canon_meet_id IS NOT NULL
            AND a.canon_meet_id = t.canon_meet_id) AS shares_canon,
           a.venue_key,
           ma.course_name,
           an.first_name, an.last_name,
           -- how many OTHER races each id holds, so a whole-career
           -- duplication reads differently from a single stray row
           (SELECT count(*) FROM twin_key k WHERE k.person_id = t.person_id)
                            AS twin_n_races,
           -- ! THE WINDOW RUNS OVER THE WHOLE RESULT SET, so this is how
           --   many races THIS pair of ids matched on -- computed in one
           --   pass rather than by a correlated subquery per row.
           count(*) OVER (PARTITION BY a.person_id, t.person_id)
                            AS pair_races
    FROM   twin_key a
    JOIN   twin_key t
           ON  t.cs    = a.cs
           AND t.place = a.place
           -- ★ SAME GROUND, ALWAYS -- even when the dates disagree. See
           --   venue_key: without this the test has no discriminating power.
           AND t.venue_key = a.venue_key
           AND t.d BETWEEN a.d - %(days)s AND a.d + %(days)s
           -- ★ THE DEFECT ITSELF: same race, different person.
           AND t.person_id <> a.person_id
    LEFT   JOIN meets ma ON ma.div_id = a.div_id
    LEFT   JOIN LATERAL (
        SELECT at.first_name, at.last_name
        FROM   athletes at
        WHERE  at.athlete_id = (SELECT r2.athlete_id FROM results r2
                                WHERE r2.result_id = a.result_id)
        LIMIT  1
    ) an ON TRUE
    WHERE  a.source = 'anet' AND t.source = 'tfrrs'
      AND (%(person)s IS NULL
           OR a.person_id = %(person)s OR t.person_id = %(person)s)
    ORDER  BY t.person_id, a.d
"""


# writable
# Purpose: is this pair safe to act on without a human reading it?
# Detail:  ⚠ THE UNIQUENESS TEST IS THE ONE THAT MATTERS. A time and a place
#          that recur across MANY pairs is a sign the key is degenerate for
#          that race (a timing system writing 0.00, a whole field sharing a
#          placeholder), and dropping on it would delete real rows in bulk.
#          Only a pair that stands alone is acted on.
def writable(pair, seen_key):
    # ! THE UNIQUENESS KEY CARRIES THE VENUE TOO. Keyed on (place, time)
    #   alone it counted collisions across the whole corpus, so a genuine
    #   duplicate at a busy venue was held back by an unrelated race
    #   elsewhere that happened to share a time.
    if seen_key[(pair["place"], round(pair["secs"], 2),
                 pair["venue_key"])] != 1:
        return False
    # ★ AND THE PAIR ITSELF MUST BE CORROBORATED. See MIN_PAIR_RACES.
    if pair["pair_races"] < MIN_PAIR_RACES:
        return False
    return abs((pair["anet_date"] - pair["tfrrs_date"]).days) <= DEFAULT_DAYS


# stripGenerated
# Purpose: remove every block this tool has ever appended.
# Detail:
#   ⚠ THIS EXISTS BECAUSE THE FIRST VERSION SHIPPED A BAD KEY AND WAS RUN.
#     Before the venue gate, the detector matched on (time, place) alone and
#     reported 307,466 pairs; --write took the 137,045 that cleared its bar
#     and appended them to _RESULT_DROP_XC, and the next backfill duly
#     refused to rate 137,045 mostly-legitimate races. The corrected detector
#     finds 5,577 pairs and writes at most 1,527.
#
#   ! A GENERATED BLOCK MUST BE REMOVABLE AS A UNIT, which is the whole
#     reason it carries a MARKER and lives in its own dict. Deleting the
#     lines one by one would be indistinguishable from deleting a
#     hand-written drop somebody meant.
#
#   ! AND THE MERGE LINES GO WITH IT. `_RESULT_DROP_XC.update(...)` after a
#     deleted dict is a NameError on import, and corrections.py is imported
#     by the whole backfill -- the exact hazard propose_distances.
#     stripDeadBlocks was written for.
def stripGenerated(path=os.path.join("engine", "corrections.py")):
    text = io.open(path, encoding="utf-8", newline="").read()
    io.open(path + ".bak", "w", encoding="utf-8", newline="").write(text)
    lines = text.splitlines(keepends=True)
    out, i, blocks, dropped = [], 0, 0, 0
    while i < len(lines):
        if lines[i].startswith(MARKER):
            blocks += 1
            # Walk back over the comment header this tool wrote above it.
            while out and (out[-1].lstrip().startswith("#") or not out[-1].strip()):
                out.pop()
            # Forward to the end of the block: the dict, its closing brace at
            # column zero, and the two merge lines that follow.
            while i < len(lines) and not lines[i].startswith("}"):
                dropped += lines[i].strip().rstrip(",").isdigit()
                i += 1
            i += 1                                  # the closing brace
            while i < len(lines) and (
                    lines[i].startswith("_RESULT_DROP_XC.update(")
                    or lines[i].startswith("del _RESULT_DROP_ADDITIONS")):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    io.open(path, "w", encoding="utf-8", newline="").write("".join(out))
    print(f"[twin] removed {blocks} generated block(s), {dropped:,} result_ids")
    print(f"       backup at {path}.bak")
    print("       ⚠ those rows stay unrated until backfill_normalize reruns")
    return dropped


def appendDrops(pairs, path=os.path.join("engine", "corrections.py")):
    """Add the tfrrs copies to _RESULT_DROP_XC. Same block shape as every
    other generated section: its own name, merged, then deleted.

    ⚠ WITHOUT THE UPDATE LINE THE SET BELOW IS DECORATION -- the mistake
      propose_distances shipped for months. _RESULT_DROP_XC is a SET of
      result_ids that backfill_normalize membership-tests in its hot loop.
    """
    ids = sorted({p["tfrrs_id"] for p in pairs})
    if not ids:
        print("[twin] nothing clears the write bar")
        return 0
    text = io.open(path, encoding="utf-8", newline="").read()
    io.open(path + ".bak", "w", encoding="utf-8", newline="").write(text)
    lines = [f"\n\n{MARKER}",
             f"# {len(ids)} tfrrs rows that duplicate an anet row of the same",
             "# race (same time to 1/100s, same finishing place, dates within",
             f"# {DEFAULT_DAYS} days) but were attached to a DIFFERENT person_id,",
             "# so neither the backfill's nor the engine's twin rule could see",
             "# them. See scripts/diag_twin_person.py.",
             "_RESULT_DROP_ADDITIONS = {"]
    lines += [f"    {i}," for i in ids]
    lines.append("}")
    lines.append("_RESULT_DROP_XC.update(_RESULT_DROP_ADDITIONS)")
    lines.append("del _RESULT_DROP_ADDITIONS")
    io.open(path, "a", encoding="utf-8", newline="").write("\n".join(lines) + "\n")
    print(f"[twin] appended {len(ids):,} result_ids to _RESULT_DROP_XC")
    print(f"       backup at {path}.bak")
    print("       live on the next backfill_normalize --apply")
    return len(ids)


def main():
    ap = argparse.ArgumentParser(
        description="One race under two person_ids. Read only unless --write.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"date slack between the feeds (default {DEFAULT_DAYS})")
    ap.add_argument("--person", type=int, default=None)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--no-rebuild", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="append the tfrrs copies to _RESULT_DROP_XC")
    ap.add_argument("--unwrite", action="store_true",
                    help="remove every block this tool has appended, and stop")
    args = ap.parse_args()

    # ! FIRST, AND IT TOUCHES NO DATABASE. Undoing a bad write must not
    #   require the corpus to be readable, or a mistake made during a
    #   pipeline run could not be undone until the pipeline finished.
    if args.unwrite:
        stripGenerated()
        return

    from database import getConn          # see the note at the imports
    with getConn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if not args.no_rebuild:
            print("[twin] building twin_key ...")
            cur.execute(_KEYS)
            conn.commit()

        cur.execute(_PAIRS, {"days": args.days,
                             "person": str(args.person) if args.person else None})
        pairs = [dict(r) for r in cur.fetchall()]

    if not pairs:
        print("no cross-person duplicates found")
        return

    from collections import Counter
    seen_key = Counter((p["place"], round(p["secs"], 2), p["venue_key"])
                       for p in pairs)

    people = {p["tfrrs_person"] for p in pairs}
    same_day = sum(1 for p in pairs if p["anet_date"] == p["tfrrs_date"])
    canon = sum(1 for p in pairs if p["shares_canon"])
    ok = [p for p in pairs if writable(p, seen_key)]

    print(f"\n{len(pairs):,} duplicate races across {len(people):,} wrong "
          f"person_ids")
    print(f"  dated identically: {same_day:,}  "
          f"-- the rest are the one-day-apart case")
    # ★ THE NUMBER THAT DECIDES WHETHER #9c WOULD EVER HAVE HELPED. If this is
    #   near zero, a canon_meet_id sweep could not have found these and the
    #   fix has to live at the identity layer, as this file argues.
    print(f"  sharing a canon_meet_id: {canon:,} "
          f"({100.0 * canon / len(pairs):.1f}%)")
    print(f"  clearing the write bar: {len(ok):,}")

    for p in pairs[:args.limit]:
        who = " ".join(x for x in (p["first_name"], p["last_name"]) if x)
        flag = ""
        if p["pair_races"] < MIN_PAIR_RACES:
            flag = "   [held: pair matched once]"
        elif not writable(p, seen_key):
            flag = "   [held: key not unique]"
        print(f"  {p['anet_date']} / {p['tfrrs_date']}  {p['secs']:8.2f}s  "
              f"{p['place']:>4}th  {(who or '?')[:20]:<20} "
              f"person {p['anet_person']} vs {p['tfrrs_person']} "
              f"(pair x{p['pair_races']}, id has {p['twin_n_races']})  "
              f"{(p['course_name'] or '')[:26]}{flag}")
    if len(pairs) > args.limit:
        print(f"  ... and {len(pairs) - args.limit:,} more")

    if args.write:
        appendDrops(ok)
    else:
        print(f"\n       DRY RUN -- {len(ok):,} would be dropped. "
              f"Pass --write.")


if __name__ == "__main__":
    main()
