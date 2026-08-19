# Project: xc-predictor
# File:    scripts/backfill_tfrrs_location_fuzzy.py
# Purpose: The anet->tfrrs venue bridge that exact matching could not build.
#          tfrrs location_raw mashes venue+city+state+zip into one string, so it
#          never equals anet's clean course_name. This matches them by TOKEN
#          OVERLAP, blocked by state, and PROPOSES inherits for you to review
#          before any write. Run AFTER backfill_tfrrs_location.py.
#
# ============================================================================
# WHY FUZZY, AND WHY IT IS SAFE HERE
# ============================================================================
# The tier-2 audit returned 0 anet->tfrrs exact matches: anet says
# "DeSales University", tfrrs says "desales university center valley pa 18034".
# Levenshtein rates those far apart (length differs 2x); as TOKEN SETS, anet's
# tokens are a SUBSET of tfrrs's. So the score is: what fraction of the anet
# venue's DISTINCTIVE tokens (stopwords dropped) appear in the tfrrs raw string.
# City/state/zip tokens on the tfrrs side are extra, never penalised.
#
# A wrong venue match is silent and permanent, so three guards:
#   BLOCK   only compare within the same state (never TX-anet vs CA-tfrrs). Also
#           collapses the search from 733k x N to ~50 small per-state problems.
#   DISTINCT only distinctive tokens count -- "university"/"park"/"high"/"school"
#           collide everywhere and would match unrelated venues.
#   TIE-BAIL if two anet venues score within --tie-margin of each other for one
#           tfrrs venue, take NEITHER -- send it to the geocode pile. A miss is
#           recoverable; a wrong merge is not.
#
# COORDINATE SOURCE FILTER: an anet venue is a source only if its coordinate is
# a real coordinate -- lat in [-90,90], long in [-180,180], not null-island
# (0,0). US and INTERNATIONAL venues both qualify; we exclude only what is
# provably not a coordinate. anet's meets rows are never modified.
#
# ============================================================================
# OUTPUT
#   propose (default): writes fuzzy_matches_tfrrs.tsv -- every proposed inherit
#       with scores and the coordinate, plus fuzzy_ties_tfrrs.tsv for the bails.
#       No DB write.
#   --apply : writes location_id + gps onto tfrrs meets still NULL whose match
#       score >= --min-score and which are not ties. Idempotent, scoped XC.
#
# USAGE
#   python scripts/backfill_tfrrs_location_fuzzy.py                 # propose
#   python scripts/backfill_tfrrs_location_fuzzy.py --min-score 0.8 --apply
# ============================================================================

import argparse
import collections
import os
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- CONSTANTS
# ================================================================== #
_OUT_MATCHES = "fuzzy_matches_tfrrs.tsv"
_OUT_TIES = "fuzzy_ties_tfrrs.tsv"

# Tokens that appear at so many venues they carry no identifying signal. A match
# must agree on tokens OUTSIDE this set, or "Lincoln High School" matches every
# Lincoln High in the state.
_STOP = {
    "university", "college", "school", "high", "park", "state", "the", "of",
    "invitational", "invite", "classic", "course", "cross", "country", "xc",
    "golf", "club", "community", "center", "centre", "complex", "trail",
    "trails", "hs", "ms", "middle", "elementary", "city", "county", "north",
    "south", "east", "west", "at", "and", "championships", "championship",
    "meet", "run", "open", "campus",
}


# ================================================================== #
# CHUNK 2 -- TOKENIZATION
# ================================================================== #

# _tokens
# Purpose : a venue string -> its set of lowercase word tokens.
# Syntax  : split on non-word runs; a set so overlap is subset math and order /
#           repetition do not matter.
def _tokens(s):
    if not s:
        return set()
    return set(t for t in re.split(r"[^\w]+", s.lower()) if t)


# _distinctive
# Purpose : the identifying tokens of a name -- everything that is not a stopword
#           and not a bare number (zips/street numbers carry no venue identity).
def _distinctive(tok):
    return {t for t in tok if t not in _STOP and not t.isdigit()}


# ================================================================== #
# CHUNK 3 -- SOURCE / TARGET LOADERS
# ================================================================== #

# _validCoord
# Purpose : is (lat, lon) a real coordinate? Excludes out-of-range and null
#           island, nothing else -- international coordinates pass.
def _validCoord(lat, lon):
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:      # (0,0) geocoder-failure sentinel
        return False
    return True


# _loadAnetVenues
# Purpose : distinct anet venues with a VALID coordinate, grouped by state, each
#           carrying its distinctive token set and location_id + gps. One row per
#           (course_name, state, location_id) so we do not weight by meet count.
# Output  : {state: [ {name, dist_tokens, loc, lat, lon}, ... ]}.
def _loadAnetVenues(cur):
    cur.execute("""
        SELECT DISTINCT course_name, upper(coalesce(state,'')) AS st,
               location_id, gps_lat, gps_long
        FROM meets
        WHERE location_id IS NOT NULL AND course_name IS NOT NULL
          AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
    """)
    by_state = collections.defaultdict(list)
    for name, st, loc, lat, lon in cur.fetchall():
        if not _validCoord(lat, lon):
            continue
        dt = _distinctive(_tokens(name))
        if not dt:                       # a name of only stopwords cannot anchor
            continue
        by_state[st].append({"name": name, "dist": dt, "loc": loc,
                             "lat": lat, "lon": lon})
    return by_state


# _loadUnresolvedTfrrs
# Purpose : tfrrs XC meets still without a location_id after the exact backfill --
#           the ones fuzzy is here to place. Carries the raw string + its token
#           set + state.
# Output  : [ {meet, raw, tokens, state}, ... ].
def _loadUnresolvedTfrrs(cur):
    cur.execute("""
        SELECT meet_id, location_raw, upper(coalesce(state,'')) AS st
        FROM meets_tfrrs
        WHERE sport = 'XC' AND location_id IS NULL
    """)
    out = []
    for meet_id, raw, st in cur.fetchall():
        out.append({"meet": meet_id, "raw": raw, "tokens": _tokens(raw),
                    "state": st})
    return out


# ================================================================== #
# CHUNK 4 -- SCORING
# ================================================================== #

# _score
# Purpose : fraction of an anet venue's distinctive tokens present in the tfrrs
#           raw token set. 1.0 = every identifying word of the anet name appears
#           in the tfrrs string. This is the asymmetric containment that handles
#           tfrrs's extra city/state/zip tokens.
def _score(anet_dist, tfrrs_tokens):
    if not anet_dist:
        return 0.0
    hit = sum(1 for t in anet_dist if t in tfrrs_tokens)
    return hit / len(anet_dist)


# _bestTwo
# Purpose : the top two anet candidates for one tfrrs venue, within its state.
# Output  : [(score, venue), (score, venue)] highest first (second may be absent).
def _bestTwo(tf, anet_by_state):
    cands = anet_by_state.get(tf["state"], ())
    scored = []
    for a in cands:
        s = _score(a["dist"], tf["tokens"])
        if s > 0:
            scored.append((s, a))
    scored.sort(key=lambda kv: kv[0], reverse=True)
    return scored[:2]


# ================================================================== #
# CHUNK 5 -- MATCH PASS
# ================================================================== #

# _match
# Purpose : for each unresolved tfrrs meet, propose the best anet venue if it
#           clears min_score AND beats its runner-up by tie_margin. Ties and
#           near-ties are recorded separately and NOT proposed.
# Output  : (proposals, ties). proposal = dict with meet, raw, name, score,
#           runner, loc, lat, lon. tie = (meet, raw, top_score, runner_score).
def _match(tfrrs, anet_by_state, min_score, tie_margin):
    proposals, ties = [], []
    for tf in tfrrs:
        top2 = _bestTwo(tf, anet_by_state)
        if not top2:
            continue
        s1, a1 = top2[0]
        s2 = top2[1][0] if len(top2) > 1 else 0.0
        if s1 < min_score:
            continue
        if s1 - s2 < tie_margin:                 # ambiguous -> bail, do not guess
            ties.append((tf["meet"], tf["raw"], s1, s2))
            continue
        proposals.append({"meet": tf["meet"], "raw": tf["raw"], "name": a1["name"],
                          "score": s1, "runner": s2, "loc": a1["loc"],
                          "lat": a1["lat"], "lon": a1["lon"]})
    return proposals, ties


# ================================================================== #
# CHUNK 6 -- WRITE PROPOSALS / TIES
# ================================================================== #

def _writeProposals(path, proposals):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# tfrrs_meet\tscore\trunner\tlocation_id\tgps_lat\tgps_long\t"
                "anet_course_name\ttfrrs_location_raw\n")
        for p in sorted(proposals, key=lambda p: -p["score"]):
            f.write("\t".join(str(x) for x in (
                p["meet"], f"{p['score']:.2f}", f"{p['runner']:.2f}",
                p["loc"], p["lat"], p["lon"], p["name"], p["raw"])) + "\n")


def _writeTies(path, ties):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# tfrrs_meet\ttop_score\trunner_score\ttfrrs_location_raw"
                "   (ambiguous -> geocode pile)\n")
        for meet, raw, s1, s2 in sorted(ties, key=lambda t: -t[2]):
            f.write(f"{meet}\t{s1:.2f}\t{s2:.2f}\t{raw}\n")


# ================================================================== #
# CHUNK 7 -- APPLY
# ================================================================== #

# _apply
# Purpose : write location_id + gps onto the proposed tfrrs meets. Scoped to XC
#           and location_id IS NULL, so a re-run cannot double-write and cannot
#           overwrite anything the exact backfill placed.
def _apply(conn, proposals):
    if not proposals:
        return 0
    with conn.cursor() as cur:
        for p in proposals:
            cur.execute("""
                UPDATE meets_tfrrs
                SET location_id = %s,
                    gps_lat  = COALESCE(gps_lat,  %s),
                    gps_long = COALESCE(gps_long, %s)
                WHERE meet_id = %s AND sport = 'XC' AND location_id IS NULL
            """, (p["loc"], p["lat"], p["lon"], p["meet"]))
    conn.commit()
    return len(proposals)


# ================================================================== #
# CHUNK 8 -- DRIVER
# ================================================================== #

def _run(apply, min_score, tie_margin, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '512MB'")
        anet = _loadAnetVenues(cur)
        tfrrs = _loadUnresolvedTfrrs(cur)
        n_anet = sum(len(v) for v in anet.values())
        print(f"  anet source venues (valid coord): {n_anet:,} in "
              f"{len(anet)} states")
        print(f"  unresolved tfrrs XC meets:         {len(tfrrs):,}")

        proposals, ties = _match(tfrrs, anet, min_score, tie_margin)
        print(f"  proposed matches (score >= {min_score}, "
              f"margin >= {tie_margin}): {len(proposals):,}")
        print(f"  ambiguous ties (bailed):           {len(ties):,}")

        mp = os.path.join(out_dir, _OUT_MATCHES)
        tp = os.path.join(out_dir, _OUT_TIES)
        _writeProposals(mp, proposals)
        _writeTies(tp, ties)
        print(f"  wrote:\n    {mp}\n    {tp}")

        if apply:
            n = _apply(conn, proposals)
            print(f"  APPLIED {n:,} fuzzy inherits")
            cur.execute("""SELECT count(*) FILTER (WHERE location_id IS NOT NULL),
                                  count(*) FROM meets_tfrrs WHERE sport='XC'""")
            have, tot = cur.fetchone()
            print(f"  meets_tfrrs XC now located: {have:,} / {tot:,}")
        else:
            print("  [dry-run] no rows written -- review the TSV, then --apply")
            conn.rollback()


def main():
    ap = argparse.ArgumentParser(
        description="Fuzzy anet->tfrrs venue matcher. Propose-only by default.")
    ap.add_argument("--min-score", type=float, default=0.8,
                    help="min fraction of anet distinctive tokens found in tfrrs raw")
    ap.add_argument("--tie-margin", type=float, default=0.2,
                    help="top must beat runner-up by this, else bail to geocode pile")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()

    initPool()
    print(f"fuzzy anet->tfrrs venue match  "
          f"({'APPLY' if args.apply else 'PROPOSE (dry run)'})")
    _run(args.apply, args.min_score, args.tie_margin, args.out)


if __name__ == "__main__":
    main()