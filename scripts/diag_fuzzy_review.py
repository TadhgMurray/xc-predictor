# Project: xc-predictor
# File:    scripts/diag_fuzzy_review.py
# Purpose: READ-ONLY. Review the fuzzy matcher's output AFTER the fact, so 899
#          auto-applied matches and 1,039 bailed ties become a few dozen lines to
#          check instead of ~1,900. Reads the two TSVs the matcher wrote; touches
#          the DB only to resolve tie self-collisions. Writes nothing.
#
# ============================================================================
# TWO CHECKS
# ============================================================================
# 1. SUSPECT MATCHES. The token score can hit 0.85 while the anet name is not
#    really the same venue -- e.g. a two-distinctive-token name where both tokens
#    happen to appear scattered in a long tfrrs raw. A strong SECONDARY signal is
#    whether the anet name's distinctive tokens appear as a CONTIGUOUS run in the
#    tfrrs raw (real venue) or SCATTERED (coincidental). We re-derive both from
#    the TSV and flag the scattered, low-score ones -- the likely false positives.
#
# 2. TIE TRIAGE. A tie was bailed because two anet venues scored within the
#    margin. But if those two are the SAME physical place (two anet location_ids
#    for one venue -- the known collision), the tie is spurious and the meet is
#    actually placeable. We check each tie meet against the DB: do its top anet
#    candidates resolve to coordinates that are close together (same venue) or far
#    apart (genuinely different)? Close => recoverable; far => correctly bailed.
#
# USAGE
#   python scripts/diag_fuzzy_review.py
#   python scripts/diag_fuzzy_review.py --matches scripts/fuzzy_matches_tfrrs.tsv
#   python scripts/diag_fuzzy_review.py --show 60
# ============================================================================

import argparse
import os
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- SAME STOPWORDS / TOKENIZER AS THE MATCHER
# ================================================================== #
# Must match backfill_tfrrs_location_fuzzy or the review judges a different
# grouping than the one that ran.

_STOP = {
    "university", "college", "school", "high", "park", "state", "the", "of",
    "invitational", "invite", "classic", "course", "cross", "country", "xc",
    "golf", "club", "community", "center", "centre", "complex", "trail",
    "trails", "hs", "ms", "middle", "elementary", "city", "county", "north",
    "south", "east", "west", "at", "and", "championships", "championship",
    "meet", "run", "open", "campus",
}


def _tokens(s):
    return set(t for t in re.split(r"[^\w]+", (s or "").lower()) if t)


def _distinctive(tok):
    return {t for t in tok if t not in _STOP and not t.isdigit()}


# ================================================================== #
# CHUNK 2 -- CONTIGUITY TEST
# ================================================================== #

# _contiguous
# Purpose : do the anet name's distinctive tokens appear as an unbroken run,
#           IN ORDER, inside the tfrrs raw? "desales university" inside
#           "...desales university center valley..." is contiguous (real). Two
#           tokens landing 8 words apart is scattered (coincidental).
# Syntax  : build the anet distinctive tokens in their name order, then check the
#           tfrrs token LIST contains that subsequence with no gaps.
# Output  : True if contiguous, else False.
def _contiguous(anet_name, tfrrs_raw):
    a_seq = [t for t in re.split(r"[^\w]+", anet_name.lower())
             if t and t not in _STOP and not t.isdigit()]
    if not a_seq:
        return False
    t_seq = [t for t in re.split(r"[^\w]+", tfrrs_raw.lower()) if t]
    n, m = len(t_seq), len(a_seq)
    for i in range(n - m + 1):
        if t_seq[i:i + m] == a_seq:
            return True
    return False


# ================================================================== #
# CHUNK 3 -- READ THE TSVs
# ================================================================== #

def _readMatches(path):
    """Rows: meet, score, runner, loc, lat, lon, anet_name, tfrrs_raw."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 8:
                continue
            out.append({"meet": int(p[0]), "score": float(p[1]),
                        "runner": float(p[2]), "loc": p[3],
                        "lat": p[4], "lon": p[5], "name": p[6], "raw": p[7]})
    return out


def _readTies(path):
    """Rows: meet, top_score, runner_score, tfrrs_raw."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            out.append({"meet": int(p[0]), "top": float(p[1]),
                        "runner": float(p[2]), "raw": p[3]})
    return out


# ================================================================== #
# CHUNK 4 -- CHECK 1: SUSPECT MATCHES
# ================================================================== #

# _reviewMatches
# Purpose : classify each applied match as SOLID (name is a contiguous run in the
#           raw) or SUSPECT (scattered tokens -- the likely false positives).
def _reviewMatches(matches, show):
    solid, suspect = [], []
    for m in matches:
        (solid if _contiguous(m["name"], m["raw"]) else suspect).append(m)
    print(f"\nMATCHES: {len(matches)} applied")
    print(f"  SOLID   (anet name is a contiguous run in tfrrs raw): {len(solid)}")
    print(f"  SUSPECT (tokens scattered -- check these):            {len(suspect)}")
    suspect.sort(key=lambda m: m["score"])       # weakest first
    print(f"\n  worst {min(show, len(suspect))} suspect matches "
          f"(low score + scattered):")
    print(f"    {'score':>5} {'meet':>8}  anet_name  <=  tfrrs_raw")
    for m in suspect[:show]:
        print(f"    {m['score']:>5.2f} {m['meet']:>8}  {m['name'][:34]!r}"
              f"  <=  {m['raw'][:44]!r}")
    return suspect


# ================================================================== #
# CHUNK 5 -- CHECK 2: TIE TRIAGE
# ================================================================== #

# _haversineKm
def _haversineKm(a, b):
    from math import radians, sin, cos, asin, sqrt
    if None in a or None in b:
        return None
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


# _triageTies
# Purpose : for each bailed tie, find the tfrrs meet's top-2 anet candidates in
#           the DB (same score logic) and measure how far apart their coordinates
#           are. Close => the "tie" is one venue with two location_ids
#           (recoverable); far => genuinely different venues (correctly bailed).
# Detail  : we re-score against anet venues in the meet's state, exactly as the
#           matcher did, so the top-2 match what was bailed.
def _triageTies(cur, ties, show):
    # load anet venues by state once (mirror of the matcher's source)
    cur.execute("""
        SELECT DISTINCT course_name, upper(coalesce(state,'')) AS st,
               location_id, gps_lat, gps_long
        FROM meets
        WHERE location_id IS NOT NULL AND course_name IS NOT NULL
          AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
    """)
    import collections
    by_state = collections.defaultdict(list)
    for name, st, loc, lat, lon in cur.fetchall():
        dt = _distinctive(_tokens(name))
        if dt:
            by_state[st].append((dt, loc, lat, lon))

    # each tie meet's state + raw
    metas = {}
    if ties:
        meets = [t["meet"] for t in ties]
        cur.execute("""
            SELECT meet_id, upper(coalesce(state,'')) AS st, location_raw
            FROM meets_tfrrs WHERE meet_id = ANY(%s) AND sport='XC'
        """, (meets,))
        for meet_id, st, raw in cur.fetchall():
            metas[meet_id] = (st, raw)

    recoverable, genuine, unknown = 0, 0, 0
    detail = []
    for t in ties:
        st, raw = metas.get(t["meet"], (None, None))
        if st is None:
            unknown += 1
            continue
        raw_tok = _tokens(raw)
        scored = []
        for dt, loc, lat, lon in by_state.get(st, ()):
            hit = sum(1 for x in dt if x in raw_tok)
            s = hit / len(dt) if dt else 0
            if s > 0:
                scored.append((s, loc, lat, lon))
        scored.sort(reverse=True, key=lambda x: x[0])
        if len(scored) < 2:
            unknown += 1
            continue
        km = _haversineKm((scored[0][2], scored[0][3]),
                          (scored[1][2], scored[1][3]))
        if km is None:
            unknown += 1
        elif km < 5:
            recoverable += 1
            detail.append((km, t["meet"], raw))
        else:
            genuine += 1

    print(f"\nTIES: {len(ties)} bailed")
    print(f"  RECOVERABLE (top-2 anet coords < 5km -- same venue, two ids): "
          f"{recoverable}")
    print(f"  GENUINE     (top-2 coords far apart -- correctly bailed):     "
          f"{genuine}")
    print(f"  UNKNOWN     (no state / <2 candidates):                       "
          f"{unknown}")
    if recoverable:
        print(f"\n  a few recoverable ties (could be auto-placed with a coord-"
              f"agreement rule):")
        for km, meet, raw in detail[:show]:
            print(f"    {km:>5.1f}km  meet {meet:>8}  {raw[:50]!r}")


# ================================================================== #
# CHUNK 6 -- DRIVER
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Review the fuzzy matcher's applied matches and bailed ties. "
                    "READ-ONLY.")
    ap.add_argument("--matches", default="scripts/fuzzy_matches_tfrrs.tsv")
    ap.add_argument("--ties", default="scripts/fuzzy_ties_tfrrs.tsv")
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()

    if not os.path.exists(args.matches):
        sys.exit(f"matches file not found: {args.matches}")

    initPool()
    matches = _readMatches(args.matches)
    _reviewMatches(matches, args.show)

    if os.path.exists(args.ties):
        ties = _readTies(args.ties)
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL work_mem = '512MB'")
            _triageTies(cur, ties, args.show)
            conn.rollback()
    else:
        print(f"\n  (no ties file at {args.ties})")


if __name__ == "__main__":
    main()