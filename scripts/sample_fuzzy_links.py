"""
============================================================================
 sample_fuzzy_links.py -- eyeball the ACCEPTED fuzzy links before merging
============================================================================

 THE SITUATION
 -------------
 fuzzy_pass --apply wrote method='fuzzy-in-meet' links to entity_links.
 The dry run showed the REJECTED band (80..85); nobody has ever looked
 at the ACCEPTED (>=85) population -- and the re-merge will stamp its
 conf-1 links at the same bar as exact conf-1 links (the merge doesn't
 read method). This shows random accepted links WITH NAMES, split by
 confidence, so the "does fuzzy conf-1 deserve the conf-1 bar?" decision
 is made with eyes instead of by omission.

 What to look for: name pairs that are plausible variants of one person
 (spelling drift, accents, middle names) = good. Same-surname different-
 first-name pairs (the sibling trap) = the failure mode; a few of those
 at conf 1 argues for a method-aware floor of 2 in the merge.

 READ-ONLY. Usage:
     python scripts/sample_fuzzy_links.py
     python scripts/sample_fuzzy_links.py --sport XC --samples 15
============================================================================
"""

# ===========================================================================
# CHUNK 1: IMPORTS + CONSTANTS
# ===========================================================================

import argparse

from database import getConn

# Sport decides the TABLE, source decides the FILTER (§0 invariant).
SPORT_TABLE = {"XC": "results", "TF": "results_tf"}


# ===========================================================================
# CHUNK 2: NAME RESOLVERS (same lookups as the other inspectors)
# ===========================================================================

def _anetName(cur, anet_id):
    # -----------------------------------------------------------------
    # Purpose:  one display name for an anet athlete (any of their
    #           (athlete_id, school) rows' names serves -- and remember
    #           variants exist, so a mismatch with the tfrrs name may
    #           be a LIMIT-1 artifact, not a bad link).
    # Arguments: cur; anet_id.
    # Output:   'First Last' or '?'.
    # -----------------------------------------------------------------
    cur.execute("SELECT first_name, last_name FROM athletes "
                "WHERE athlete_id = %s LIMIT 1", (anet_id,))
    row = cur.fetchone()
    if not row:
        return "?"
    return f"{row[0] or ''} {row[1] or ''}".strip() or "?"


def _tfrrsName(cur, table, tfrrs_id):
    # -----------------------------------------------------------------
    # Purpose:  tfrrs display name off any result row (no tfrrs
    #           athletes table exists).
    # Arguments: cur; table; tfrrs_id.
    # Output:   name string or '?'.
    # -----------------------------------------------------------------
    cur.execute(f"SELECT athlete_name FROM {table} "
                f"WHERE native_id = %s AND source = 'tfrrs' LIMIT 1",
                (tfrrs_id,))
    row = cur.fetchone()
    return (row[0] if row else None) or "?"


# ===========================================================================
# CHUNK 3: THE SAMPLER -- per confidence bucket, names resolved
# ===========================================================================

def _confidenceSpread(cur, sport):
    # -----------------------------------------------------------------
    # Purpose:  how the fuzzy links distribute over confidence -- most
    #           will be conf 1 (one agreeing meet); the size of that
    #           bucket IS the stakes of the floor decision.
    # Arguments: cur; sport.
    # Output:   list of (confidence, count), ascending.
    # -----------------------------------------------------------------
    cur.execute(
        "SELECT confidence, count(*) FROM entity_links "
        "WHERE entity_type = 'athlete' AND sport = %s "
        "  AND method = 'fuzzy-in-meet' "
        "GROUP BY confidence ORDER BY confidence",
        (sport,),
    )
    return cur.fetchall()


def _sampleBucket(cur, sport, confidence, limit):
    # -----------------------------------------------------------------
    # Purpose:  random accepted links at ONE confidence, with names --
    #           the judgeable view. Random, not first-N, so the sample
    #           isn't one corner of id space (the borderline report's
    #           first-50 cap has exactly that bias; this doesn't).
    # Arguments: cur; sport; confidence; limit.
    # Output:   None -- prints.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    cur.execute(
        "SELECT anet_id, tfrrs_id FROM entity_links "
        "WHERE entity_type = 'athlete' AND sport = %s "
        "  AND method = 'fuzzy-in-meet' AND confidence = %s "
        "ORDER BY random() LIMIT %s",
        (sport, confidence, limit),
    )
    for anet_id, tfrrs_id in cur.fetchall():
        a = _anetName(cur, anet_id)
        t = _tfrrsName(cur, table, tfrrs_id)
        print(f"      {a!r:32s} <-> {t!r}")


# ===========================================================================
# CHUNK 4: MAIN
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Sample accepted fuzzy links with names, by confidence.")
    parser.add_argument("--sport", choices=["XC", "TF"], default=None)
    parser.add_argument("--samples", type=int, default=10,
                        help="samples per confidence bucket shown (default 10)")
    args = parser.parse_args()

    sports = [args.sport] if args.sport else ["XC", "TF"]
    print("=== accepted fuzzy links sampler (read-only) ===")
    with getConn() as conn:
        cur = conn.cursor()
        for sport in sports:
            print(f"\n--- {sport} ---")
            spread = _confidenceSpread(cur, sport)
            if not spread:
                print("  no fuzzy-in-meet links found -- run fuzzy --apply first")
                continue
            print("  confidence spread: " +
                  ", ".join(f"conf {c}: {n:,}" for c, n in spread))
            # sample the RISKY bucket (conf 1) heavily and the safest
            # (highest conf) lightly for contrast.
            print(f"  --- conf 1 samples (the floor decision) ---")
            _sampleBucket(cur, sport, 1, args.samples)
            top = spread[-1][0]            # last row of ascending spread = max conf
            if top > 1:
                print(f"  --- conf {top} samples (the safest, for contrast) ---")
                _sampleBucket(cur, sport, top, min(args.samples, 5))

    print("\n=== read: variant spellings of one person = good; same surname,"
          "\n    DIFFERENT first name (siblings) at conf 1 = raise the floor. ===")


if __name__ == "__main__":
    main()