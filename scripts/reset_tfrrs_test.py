# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/reset_tfrrs_xc_test.py
# Purpose: Reset a SMALL number of tfrrs XC meets to scraped=0 so the tfrrs
#          launcher re-scrapes them with the new div_id + division_distances
#          capture in place. Deliberately does only a FEW first (default 3) so you
#          can verify the fix on a small batch before resetting everything.
#
#   WHAT WE'RE TESTING
#   ------------------
#   The capture fix now: (a) stamps a per-meet linear div_id onto each tfrrs XC
#   result row, and (b) writes a {div_id: {div_name, distance}} blob onto the
#   meet's meets_tfrrs row. This reset makes a few meets eligible for re-scrape so
#   we can confirm, after, that:
#       results.div_id           is populated (was NULL) for those meets, and
#       meets_tfrrs.division_distances is a non-null blob for those meets.
#
#   WHY PICK NULL-DIV MEETS (the verifiable set)
#   --------------------------------------------
#   By default we pick tfrrs XC meets that CURRENTLY have result rows with
#   div_id NULL. Those are the meets where the fix will make a VISIBLE change
#   (NULL -> a real div_id, and a fresh blob), so they're the right ones to
#   eyeball. --meets lets you name specific ids instead.
#
#   THE QUEUE (same shape as anet, source-scoped to tfrrs)
#   -----------------------------------------------------
#   tfrrs meets live in meet_queue with source='tfrrs'; scraped=0 means unscraped
#   (0=unscraped,1=done,2=failed,3=in-progress). The launcher claims scraped=0
#   rows. So the reset sets scraped=0 for the triple (meet_id, 'XC', 'tfrrs').
#   Identity is the full PK (meet_id, sport, source) — never a bare meet_id.
#
#   THE HIERARCHY
#   -------------
#     results (source='tfrrs', div_id NULL)  -> pick a few DISTINCT meet_ids
#        |
#        v
#     meet_queue (meet_id, 'XC', 'tfrrs')    -> upsert scraped = 0
#        |
#        v
#     tfrrs launcher claims scraped=0        -> re-scrape with the fix
#
#   SAFETY: DRY-RUN BY DEFAULT (prints the plan, writes nothing). --apply writes.
#   Run with the tfrrs launcher OFF so a claim can't race the reset.
#
#   RUN:
#       python scripts/reset_tfrrs_xc_test.py                  # dry run, 3 meets
#       python scripts/reset_tfrrs_xc_test.py --apply          # reset 3 meets
#       python scripts/reset_tfrrs_xc_test.py --limit 5 --apply
#       python scripts/reset_tfrrs_xc_test.py --meets 12345 67890 --apply

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn, initPool


# The fixed half of the queue key for every row we touch: tfrrs XC.
_SPORT = "XC"
_SOURCE = "tfrrs"


# ================================================================== #
# STEP 1 — PICK THE TEST MEETS.  Either the ids the user named, or a
#   sample of tfrrs XC meets that currently have NULL-div rows (the
#   verifiable ones — the fix will visibly change them).
# ================================================================== #

def _sampleTfrrsXcMeets(cur, limit):
    """
    Purpose : return up to `limit` DISTINCT tfrrs XC meet_ids that currently have
              at least one result row with div_id NULL (so the fix will visibly
              populate div_id + a blob for them).
    Arguments: cur — read cursor; limit — how many meet_ids.
    Output  : list of meet_id ints.

    Syntax / mechanics:
      - WHERE source='tfrrs' AND div_id IS NULL: the tfrrs rows the fix targets.
      - DISTINCT meet_id: we reset MEETS, not rows — one queue row per meet.
      - LIMIT %s: only a few, for the test batch.
      - No ORDER BY: any such meets are fine to test; ordering would cost a sort.
    """
    cur.execute("""
        SELECT DISTINCT meet_id
        FROM results
        WHERE source = %s AND div_id IS NULL
        LIMIT %s
    """, (_SOURCE, limit))
    return [row[0] for row in cur.fetchall()]


# ================================================================== #
# STEP 2 — BUILD THE KEY TRIPLES.  Pure function: each bare meet_id
#   becomes its full (meet_id, sport, source) queue key. Enforces the
#   "never a bare meet_id" rule.
# ================================================================== #

def _buildTriples(meet_ids):
    """
    Purpose : map each meet_id to its full meet_queue key (meet_id, 'XC', 'tfrrs').
    Argument: meet_ids — list of meet_id ints.
    Output  : list of (meet_id, sport, source) tuples, de-duplicated.

    Mechanics: dict.fromkeys preserves order while dropping duplicate ids (an
    ordered set), then we attach the fixed sport/source. Tuple order matches the
    PK column order so the SQL binds positionally.
    """
    unique_ids = list(dict.fromkeys(meet_ids))
    return [(mid, _SPORT, _SOURCE) for mid in unique_ids]


# ================================================================== #
# STEP 3 — READ CURRENT STATE.  The before-state of each triple, so the
#   write is never silent: you SEE whether each was done(1)/failed(2)/
#   in-progress(3)/absent before we set it to 0.
# ================================================================== #

_STATUS_LABEL = {
    0: "unscraped", 1: "done", 2: "failed", 3: "in-progress", 4: "skipped",
    None: "ABSENT (no queue row)",
}


def _readCurrentState(cur, triples):
    """
    Purpose : current scraped value for each target triple (None => not in queue).
    Arguments: cur — read cursor; triples — (meet_id, sport, source) list.
    Output  : dict {(meet_id, sport, source): scraped_int_or_None}.

    Mechanics:
      - Seed the dict with None for EVERY triple, then overwrite the ones the
        query finds. A triple with no queue row keeps None -> "absent". A plain
        SELECT (returning only existing rows) couldn't distinguish absent from
        present; seeding-then-overwriting is how we tell them apart.
      - One query, scoped to our meet_ids AND the fixed sport/source.
    """
    meet_ids = [mid for (mid, _s, _src) in triples]
    state = {t: None for t in triples}
    if not meet_ids:
        return state
    cur.execute("""
        SELECT meet_id, scraped FROM meet_queue
        WHERE meet_id = ANY(%s) AND sport = %s AND source = %s
    """, (meet_ids, _SPORT, _SOURCE))
    for meet_id, scraped in cur.fetchall():
        state[(meet_id, _SPORT, _SOURCE)] = scraped
    return state


# ================================================================== #
# STEP 4 — PREVIEW.  Print exactly which meets will be reset and their
#   current status, before any write.
# ================================================================== #

def _printPlan(triples, state, apply):
    """
    Purpose : show the meets to reset and their current status, before writing.
    Arguments: triples — target list; state — before-state; apply — writing?
    Output  : None (prints).

    Mechanics: one line per triple (sorted by meet_id for stable output),
    translating the status int to a word via _STATUS_LABEL.
    """
    mode = "APPLY" if apply else "DRY RUN"
    print("=" * 70)
    print(f"RESET tfrrs XC meets -> scraped=0   ({mode})   sport={_SPORT} source={_SOURCE}")
    print("=" * 70)
    print(f"meets to reset: {len(triples)}")
    print("-" * 70)
    for (meet_id, _s, _src) in sorted(triples, key=lambda t: t[0]):
        current = state[(meet_id, _SPORT, _SOURCE)]
        label = _STATUS_LABEL.get(current, f"unknown({current})")
        print(f"  meet_id={meet_id:<12} currently: {label:<24} -> will set scraped=0")


# ================================================================== #
# STEP 5 — WRITE.  Upsert scraped=0 for each triple, in ONE transaction.
#   INSERT ... ON CONFLICT (meet_id, sport, source) handles both present
#   rows (flip to 0) and absent rows (create at 0).
# ================================================================== #

def _applyReset(conn, triples):
    """
    Purpose : set scraped=0 for every target triple, creating the row if absent.
    Arguments: conn — write connection; triples — (meet_id, sport, source) list.
    Output  : number of rows affected (int).

    Mechanics / syntax:
      - One cursor, loop the upserts, ONE commit -> atomic (all-or-nothing).
      - ON CONFLICT (meet_id, sport, source) is the LIVE PK (the full triple), so
        a present row's DO UPDATE fires; an absent row INSERTs at 0.
      - source is supplied on insert; only scraped changes on conflict.
      - rowcount summed as a written-count check.
    """
    cur = conn.cursor()
    written = 0
    for meet_id, sport, source in triples:
        cur.execute("""
            INSERT INTO meet_queue (meet_id, sport, scraped, source)
            VALUES (%s, %s, 0, %s)
            ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0
        """, (meet_id, sport, source))
        written += cur.rowcount
    conn.commit()
    return written


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _resolveMeetIds(cur, named, limit):
    """
    Purpose : decide which meet_ids to reset — the named ones, or a sampled few.
    Arguments: cur — read cursor; named — list from --meets (or None);
               limit — sample size when not named.
    Output  : list of meet_id ints.

    If --meets was given we use exactly those (test specific meets); otherwise we
    sample `limit` NULL-div tfrrs XC meets (the verifiable default set).
    """
    if named:
        return named
    return _sampleTfrrsXcMeets(cur, limit)


def _run(conn, named, limit, apply):
    """
    Purpose : resolve meets -> triples -> read state -> preview -> (optionally) write.
    Arguments: conn — connection; named — --meets or None; limit — sample size;
               apply — write iff True.
    Output  : None (prints; writes only under --apply).
    """
    with conn.cursor() as cur:
        meet_ids = _resolveMeetIds(cur, named, limit)
        triples = _buildTriples(meet_ids)
        state = _readCurrentState(cur, triples)

    _printPlan(triples, state, apply)

    if not triples:
        print("-" * 70)
        print("nothing to reset (no matching tfrrs XC meets found).")
        return

    if apply:
        written = _applyReset(conn, triples)
        print("-" * 70)
        print(f"APPLIED: {written} meet_queue row(s) set to scraped=0.")
        print("Start the tfrrs launcher to re-scrape them, then verify:")
        print("  - results.div_id populated for these meets")
        print("  - meets_tfrrs.division_distances is a non-null blob for these meets")
    else:
        print("-" * 70)
        print("DRY RUN: nothing written. Re-run with --apply to reset.")


def main():
    ap = argparse.ArgumentParser(
        description="Reset a few tfrrs XC meets to scraped=0 for testing (dry-run default).")
    ap.add_argument("--apply", action="store_true",
                    help="write the reset (default: dry run, writes nothing).")
    ap.add_argument("--limit", type=int, default=3,
                    help="how many tfrrs XC meets to sample when --meets not given (default 3).")
    ap.add_argument("--meets", type=int, nargs="+", default=None,
                    help="specific meet_ids to reset (overrides sampling).")
    args = ap.parse_args()

    initPool()
    conn = getConn()
    if not hasattr(conn, "cursor") and hasattr(conn, "__enter__"):
        conn = conn.__enter__()

    _run(conn, args.meets, args.limit, args.apply)


if __name__ == "__main__":
    main()