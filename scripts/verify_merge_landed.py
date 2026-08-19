"""
============================================================================
 verify_dedup_landed.py -- did every stamp we PROMISED actually land?
============================================================================

 THE SITUATION
 -------------
 The original verify_merge_landed.py had two bugs this saga exposed:
   1. it sampled links from entity_links WITHOUT the fan-out filter, so
      it audited links the merge deliberately refused (the false FAIL
      that started the whole investigation), and
   2. it keyed tfrrs rows on athlete_id -- which is NULL on 100% of
      tfrrs rows; the real key is native_id.

 This replacement verifies exactly what the pipeline promised, three
 populations, each sampled and READ BACK from real rows:

   A. MERGE-CLEAN athlete links (1:1 both sides, conf >= threshold):
      anet rows carry person_id = anet_id AND tfrrs rows (by native_id)
      carry the same.
   B. FAN-OUT STAMPS (fanout_resolutions decision='stamp'): tfrrs rows
      carry person_id = anet_id.
   C. MEET links under the meets' ASYMMETRIC rule (one anet meet may
      absorb many tfrrs fragments; only a tfrrs meet claimed by >1 anet
      meet is ambiguous), conf >= meet threshold: both sides carry
      canon_meet_id = anet meet id.

 Plus ONE aggregate (samples are smoke alarms; this is the survey):
 stamp-decision links whose tfrrs side has ZERO stamped rows -- expect
 ~0 (known-benign residue: tfrrs ids with no rows at all).

 The fan-out logic is RE-DERIVED here rather than imported from
 merge_links: a verifier must not import the thing it audits, or a bug
 in the shared code hides from both.

 READ-ONLY. Usage:
     python scripts/verify_dedup_landed.py
     python scripts/verify_dedup_landed.py --sport XC --samples 8
============================================================================
"""

# ===========================================================================
# CHUNK 1: IMPORTS + CONSTANTS
# ===========================================================================

import argparse
import random
from collections import defaultdict

from database import getConn

# Sport decides the TABLE, source decides the FILTER (§0 invariant).
SPORT_TABLE = {"XC": "results", "TF": "results_tf"}

# !! KEEP IN SYNC WITH merge_links.py !! (duplicated, not imported -- see
# header). If the merge's thresholds change, change these or the verifier
# audits the wrong population.
MIN_CONFIDENCE_ATHLETE = {"TF": 1, "XC": 1}
MIN_CONFIDENCE_MEET    = {"TF": 2, "XC": 2}


# ===========================================================================
# CHUNK 2: LINK LOADING + THE FAN-OUT SPLIT (re-derived on purpose)
# ===========================================================================

def _loadLinks(cur, entity_type, sport, threshold):
    # -----------------------------------------------------------------
    # Purpose:  the links of one type/sport at or above the confidence
    #           bar -- the population the merge read.
    # Arguments: cur; entity_type ('athlete'/'meet'); sport; threshold.
    # Output:   list of (anet_id, tfrrs_id, confidence).
    # -----------------------------------------------------------------
    cur.execute(
        "SELECT anet_id, tfrrs_id, confidence FROM entity_links "
        "WHERE entity_type = %s AND sport = %s AND confidence >= %s",
        (entity_type, sport, threshold),
    )
    return cur.fetchall()


def _cleanAthleteLinks(links):
    # -----------------------------------------------------------------
    # Purpose:  the merge's athlete rule, re-derived: a link is CLEAN
    #           (was stamped by the merge) iff BOTH its ids appear in
    #           exactly one link. Everything else went to quarantine
    #           (and then to the resolver -- verified separately).
    # Arguments: links -- list of (anet_id, tfrrs_id, confidence).
    # Output:   the clean subset, same shape.
    # -----------------------------------------------------------------
    anet_count, tfrrs_count = defaultdict(int), defaultdict(int)
    for a, t, _ in links:
        anet_count[a]  += 1
        tfrrs_count[t] += 1
    return [(a, t, c) for (a, t, c) in links
            if anet_count[a] == 1 and tfrrs_count[t] == 1]


def _cleanMeetLinks(links):
    # -----------------------------------------------------------------
    # Purpose:  the merge's MEET rule, which is ASYMMETRIC: one anet
    #           meet absorbing many tfrrs fragments is the CORRECT
    #           consolidation; only a tfrrs fragment claimed by more
    #           than one anet meet is ambiguous.
    # Arguments: links -- list of (anet_id, tfrrs_id, confidence).
    # Output:   the clean subset, same shape.
    # -----------------------------------------------------------------
    tfrrs_count = defaultdict(int)
    for _, t, _ in links:
        tfrrs_count[t] += 1
    return [(a, t, c) for (a, t, c) in links if tfrrs_count[t] == 1]


# ===========================================================================
# CHUNK 3: ROW CHECKERS -- read the actual rows, keyed CORRECTLY
# ===========================================================================

def _anetRowCheck(cur, table, id_col, anet_id, canon_col, canon):
    # -----------------------------------------------------------------
    # Purpose:  count the anet side's rows and how many carry the
    #           canonical id. Generic over athlete (athlete_id /
    #           person_id) and meet (meet_id / canon_meet_id) so one
    #           checker serves both passes.
    # Arguments:
    #   cur; table -- results table
    #   id_col     -- 'athlete_id' or 'meet_id' (constants only)
    #   anet_id    -- the anet-side id
    #   canon_col  -- 'person_id' or 'canon_meet_id'
    #   canon      -- the expected canonical value (= anet_id here)
    # Output:  (n_rows, n_carrying) -- total rows, rows with the canon.
    # -----------------------------------------------------------------
    cur.execute(
        f"SELECT count(*), "
        f"       count(*) FILTER (WHERE {canon_col} = %s) "
        f"FROM {table} WHERE {id_col} = %s AND source = 'anet'",
        (canon, anet_id),
    )
    return cur.fetchone()


def _tfrrsRowCheck(cur, table, id_col, tfrrs_id, canon_col, canon):
    # -----------------------------------------------------------------
    # Purpose:  same for the tfrrs side -- keyed on NATIVE_ID for
    #           athletes (athlete_id is NULL on 100% of tfrrs rows:
    #           the original verifier's fatal assumption) and meet_id
    #           for meets.
    # Arguments: as _anetRowCheck, but id_col is 'native_id'/'meet_id'.
    # Output:   (n_rows, n_carrying).
    # -----------------------------------------------------------------
    cur.execute(
        f"SELECT count(*), "
        f"       count(*) FILTER (WHERE {canon_col} = %s) "
        f"FROM {table} WHERE {id_col} = %s AND source = 'tfrrs'",
        (canon, tfrrs_id),
    )
    return cur.fetchone()


def _judge(label, a_rows, a_ok, t_rows, t_ok):
    # -----------------------------------------------------------------
    # Purpose:  one PASS/FAIL line from the four counts. PASS = both
    #           sides have rows and every row carries the canon.
    #           A zero-row tfrrs side is NOTED, not failed -- ids with
    #           no rows are the known-benign residue.
    # Arguments: label (printable link description); the four counts.
    # Output:   True if passed (for the failure tally).
    # -----------------------------------------------------------------
    if t_rows == 0:
        print(f"    NOTE {label}: tfrrs side has 0 rows (known-benign)")
        return True
    ok = (a_rows > 0 and a_ok == a_rows and t_ok == t_rows)
    print(f"    {'PASS' if ok else 'FAIL'} {label}: "
          f"anet {a_ok}/{a_rows} rows, tfrrs {t_ok}/{t_rows} rows")
    return ok


# ===========================================================================
# CHUNK 4: THE THREE SAMPLE PASSES
# ===========================================================================

def _verifyCleanAthletes(cur, sport, n_samples):
    # -----------------------------------------------------------------
    # Purpose:  pass A -- sample the merge's clean 1:1 athlete links;
    #           both sides must carry person_id = anet_id.
    # Arguments: cur; sport; n_samples.
    # Output:   failures count.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    links = _loadLinks(cur, "athlete", sport, MIN_CONFIDENCE_ATHLETE[sport])
    clean = _cleanAthleteLinks(links)
    print(f"  A. merge-clean athlete links ({len(clean):,} total):")
    fails = 0
    # random.sample: n distinct picks without replacement; min() guards
    # the (theoretical) case of fewer clean links than samples.
    for a, t, conf in random.sample(clean, min(n_samples, len(clean))):
        a_rows, a_ok = _anetRowCheck(cur, table, "athlete_id", a, "person_id", a)
        t_rows, t_ok = _tfrrsRowCheck(cur, table, "native_id",  t, "person_id", a)
        fails += 0 if _judge(f"anet {a} <-> tfrrs {t} (conf {conf})",
                             a_rows, a_ok, t_rows, t_ok) else 1
    return fails


def _verifyFanoutStamps(cur, sport, n_samples):
    # -----------------------------------------------------------------
    # Purpose:  pass B -- sample the resolver's stamp verdicts from
    #           fanout_resolutions; tfrrs rows must carry person_id =
    #           anet_id. (anet side is seeding, verified by backfill's
    #           own post-check -- not re-verified per sample.)
    # Arguments: cur; sport; n_samples.
    # Output:   failures count.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    cur.execute(
        "SELECT anet_id, tfrrs_id, rule FROM fanout_resolutions "
        "WHERE sport = %s AND decision = 'stamp' "
        "ORDER BY random() LIMIT %s",
        (sport, n_samples),
    )
    rows = cur.fetchall()
    print(f"  B. fan-out stamp verdicts:")
    fails = 0
    for a, t, rule in rows:
        t_rows, t_ok = _tfrrsRowCheck(cur, table, "native_id", t, "person_id", a)
        # anet counts passed as 1/1: pass A + the backfill post-check
        # already cover the anet side; _judge only needs tfrrs here.
        fails += 0 if _judge(f"anet {a} <-> tfrrs {t} [{rule}]",
                             1, 1, t_rows, t_ok) else 1
    return fails


def _verifyMeets(cur, sport, n_samples):
    # -----------------------------------------------------------------
    # Purpose:  pass C -- sample clean meet links (asymmetric rule);
    #           both sides must carry canon_meet_id = anet meet id.
    # Arguments: cur; sport; n_samples.
    # Output:   failures count.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    links = _loadLinks(cur, "meet", sport, MIN_CONFIDENCE_MEET[sport])
    clean = _cleanMeetLinks(links)
    print(f"  C. merge-clean meet links ({len(clean):,} total):")
    fails = 0
    for a, t, conf in random.sample(clean, min(n_samples, len(clean))):
        a_rows, a_ok = _anetRowCheck(cur, table, "meet_id", a, "canon_meet_id", a)
        t_rows, t_ok = _tfrrsRowCheck(cur, table, "meet_id", t, "canon_meet_id", a)
        fails += 0 if _judge(f"anet meet {a} <-> tfrrs meet {t} (shared {conf})",
                             a_rows, a_ok, t_rows, t_ok) else 1
    return fails


# ===========================================================================
# CHUNK 5: THE AGGREGATE + MAIN
# ===========================================================================

def _unstampedAggregate(cur, sport):
    # -----------------------------------------------------------------
    # Purpose:  the survey behind the smoke alarms: stamp-decision
    #           links whose tfrrs side has ZERO rows carrying the
    #           canon. Expect ~0; small residue = zero-row ids.
    # Arguments: cur; sport.
    # Output:   int.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    cur.execute(
        f"SELECT count(*) FROM fanout_resolutions f "
        f"WHERE f.sport = %s AND f.decision = 'stamp' "
        f"  AND NOT EXISTS ("
        f"    SELECT 1 FROM {table} r "
        f"    WHERE r.native_id = f.tfrrs_id AND r.source = 'tfrrs' "
        f"      AND r.person_id = f.anet_id)",
        (sport,),
    )
    return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(
        description="Verify dedup stamps landed: clean links, fan-out stamps, meets.")
    parser.add_argument("--sport", choices=["XC", "TF"], default=None)
    parser.add_argument("--samples", type=int, default=5,
                        help="samples per pass (default 5)")
    args = parser.parse_args()

    sports = [args.sport] if args.sport else ["XC", "TF"]
    print("=== dedup landing verification v2 (read-only) ===")
    total_fails = 0
    with getConn() as conn:
        cur = conn.cursor()
        for sport in sports:
            print(f"\n--- {sport} ---")
            total_fails += _verifyCleanAthletes(cur, sport, args.samples)
            total_fails += _verifyFanoutStamps(cur, sport, args.samples)
            total_fails += _verifyMeets(cur, sport, args.samples)
            n = _unstampedAggregate(cur, sport)
            print(f"  D. aggregate: {n:,} stamp-decision links with no stamped "
                  f"tfrrs rows (expect ~0; residue = zero-row ids)")

    print(f"\n=== {total_fails} FAILURES ===" if total_fails
          else "\n=== all sampled stamps landed ===")


if __name__ == "__main__":
    main()