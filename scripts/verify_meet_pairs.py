#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/verify_meet_pairs.py
# Purpose: Answer "do these two meet ids refer to the SAME real meet?" for
#          (anet_meet, tfrrs_meet) pairs -- the ones entity_links thinks are the
#          same. For each pair it shows two kinds of evidence:
#
#            1. METADATA side by side: each meet's name / date / location, pulled
#               from the meet tables (and date from the result rows, which is the
#               one field always present on both sides).
#            2. SHARED FINISHERS: the actual people whose (name, time) appear in
#               BOTH meets. This is the real proof -- two unrelated meets do not
#               share 40 athletes at identical times. Coincidence dies fast as the
#               shared count climbs.
#
#          Run it on specific pairs (--pairs) or on the top-N meet links for a
#          sport (--top). Read-only; touches no source data.
#
#          TABLES, by sport (the same split the pipeline uses):
#            TF  -> results_tf   (anet TF + tfrrs TF share it)
#            XC  -> results       (anet XC + tfrrs XC share it)
#          Both sides are scoped by source, so the same meet_id integer on each
#          side never collides (S0: meet_id alone is not a key).

import argparse
import re
import sys

sys.path.insert(0, "scripts")
from database import getConn

TIME_TOLERANCE = 0.3
RESULTS = {"TF": "results_tf", "XC": "results"}


# ================================================================== #
# CHUNK 1 -- DB ACCESS
# ================================================================== #


# _query
# Purpose: Read-only query -> rows. All SQL funnels through here.
# Arguments: sql (%s placeholders), params tuple.
# Output:  list of tuples.
def _query(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


# ================================================================== #
# CHUNK 2 -- NORMALIZATION (same key the matcher used)
# ================================================================== #


# _normName / _roundTime
# Purpose: Reproduce the matcher's (name, time) key so "shared finisher" here
#          means the same thing it meant during the build.
def _normName(name):
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _roundTime(t):
    return None if t is None else round(float(t), 1)


# ================================================================== #
# CHUNK 3 -- METADATA LOOKUPS (name / date / location per side)
# ================================================================== #


# _meetDate
# Purpose: The earliest result date for a meet -- the one date field guaranteed
#          present on both sources (meet tables sometimes lack it). Strong signal:
#          a true pair shares this date.
# Arguments: table, source ('anet'/'tfrrs'), meet_id.
# Output:  date string or '?'.
def _meetDate(table, source, meet_id):
    rows = _query(
        f"SELECT min(date) FROM {table} WHERE meet_id=%s AND source=%s AND date<>''",
        (meet_id, source),
    )
    return rows[0][0] if rows and rows[0][0] else "?"


# _anetMeetName
# Purpose: anet meet name + location from the right meet table for the sport.
# Arguments: sport, meet_id.
# Output:  "name [location]" string.
def _anetMeetName(sport, meet_id):
    if sport == "XC":
        rows = _query("SELECT meet_name, course_name, state FROM meets "
                      "WHERE meet_id=%s AND source='anet' LIMIT 1", (meet_id,))
        if rows:
            name, course, state = rows[0]
            return f"{name or '?'}  [{course or ''} {state or ''}]".strip()
    else:
        rows = _query("SELECT meet_name, state FROM meets_tf "
                      "WHERE meet_id=%s AND source='anet' LIMIT 1", (meet_id,))
        if rows:
            name, state = rows[0]
            return f"{name or '?'}  [{state or ''}]".strip()
    return "(no anet meta)"


# _tfrrsMeetName
# Purpose: tfrrs meet name + location from meets_tfrrs. That table may be empty
#          / un-analyzed, so this is wrapped to fall back gracefully.
# Arguments: sport, meet_id.
# Output:  "name [location]" string, or a fallback note.
def _tfrrsMeetName(sport, meet_id):
    try:
        rows = _query("SELECT meet_name, venue_name, city, state FROM meets_tfrrs "
                      "WHERE meet_id=%s AND sport=%s LIMIT 1", (meet_id, sport))
        if rows:
            name, venue, city, state = rows[0]
            return f"{name or '?'}  [{venue or ''} {city or ''} {state or ''}]".strip()
    except Exception:
        pass
    return "(no tfrrs meta)"


# ================================================================== #
# CHUNK 4 -- FINISHER LOADERS (for ONE meet) + the overlap
# ================================================================== #


# _anetFinishers
# Purpose: anet finishers for one meet as (normName, roundedTime). anet result
#          rows carry no reliable name, so join athletes (athlete_id, school).
# Arguments: table, meet_id.
# Output:  list of (normName, roundedTime).
def _anetFinishers(table, meet_id):
    rows = _query(
        f"""
        SELECT (COALESCE(a.first_name,'')||' '||COALESCE(a.last_name,'')) AS name,
               r.time_seconds
        FROM {table} r
        LEFT JOIN athletes a ON a.athlete_id=r.athlete_id AND a.school=r.school
        WHERE r.source='anet' AND r.meet_id=%s
          AND r.time_seconds IS NOT NULL AND r.time_seconds<>999999
          AND r.place IS NOT NULL AND r.place<>0
        """,
        (meet_id,),
    )
    return [(_normName(n), _roundTime(t)) for n, t in rows]


# _tfrrsFinishers
# Purpose: tfrrs finishers for one meet as (normName, roundedTime). tfrrs rows
#          carry athlete_name directly -- no join.
# Arguments: table, meet_id.
# Output:  list of (normName, roundedTime).
def _tfrrsFinishers(table, meet_id):
    rows = _query(
        f"""
        SELECT r.athlete_name, r.time_seconds
        FROM {table} r
        WHERE r.source='tfrrs' AND r.meet_id=%s
          AND r.time_seconds IS NOT NULL
          AND r.place IS NOT NULL AND r.place<>0
        """,
        (meet_id,),
    )
    return [(_normName(n), _roundTime(t)) for n, t in rows]


# _sharedFinishers
# Purpose: The (name, time) entries present in BOTH meets, within the tolerance.
#          Same probe the matcher used: index tfrrs by key, check each anet row
#          across the +-0.3s ticks.
# Arguments: anet, tfrrs -- the two finisher lists.
# Output:  list of (name, anet_time, tfrrs_time) for matched people.
def _sharedFinishers(anet, tfrrs):
    index = {}
    for name, t in tfrrs:
        if name and t is not None:
            index.setdefault((name, t), []).append(t)
    ticks = [round(d*0.1, 1) for d in range(-int(TIME_TOLERANCE*10), int(TIME_TOLERANCE*10)+1)]
    shared = []
    for name, t in anet:
        if not name or t is None:
            continue
        for dt in ticks:
            hit = index.get((name, round(t+dt, 1)))
            if hit:
                shared.append((name, t, hit[0]))
                break               # one match per anet finisher is enough
    return shared


# ================================================================== #
# CHUNK 5 -- REPORT ONE PAIR
# ================================================================== #


# _verdict
# Purpose: Turn the shared count into a plain read. High overlap == same meet;
#          a handful == verify; near-zero == probably NOT the same meet.
# Arguments: shared (int), smaller_field (int).
# Output:  verdict string.
def _verdict(shared, smaller_field):
    frac = (shared / smaller_field) if smaller_field else 0.0
    if shared >= 20 or frac >= 0.5:
        return "SAME meet (overlap is far past coincidence)."
    if shared >= 5:
        return "likely same -- decent overlap; glance at the names."
    return "LOW overlap -- check this one; may not correspond."


# _reportPair
# Purpose: Print the full evidence for one (anet_meet, tfrrs_meet) pair.
# Arguments: sport, anet_meet, tfrrs_meet, show_k (how many shared names to list).
# Output:  none (prints).
def _reportPair(sport, anet_meet, tfrrs_meet, show_k):
    table = RESULTS[sport]
    print(f"\n=== {sport}: anet {anet_meet}  <->  tfrrs {tfrrs_meet} ===")

    anet = _anetFinishers(table, anet_meet)
    tfrrs = _tfrrsFinishers(table, tfrrs_meet)
    print(f"  anet : {_anetMeetName(sport, anet_meet)}")
    print(f"         date {_meetDate(table, 'anet', anet_meet)}   "
          f"finishers {len(anet):,}")
    print(f"  tfrrs: {_tfrrsMeetName(sport, tfrrs_meet)}")
    print(f"         date {_meetDate(table, 'tfrrs', tfrrs_meet)}   "
          f"finishers {len(tfrrs):,}")

    shared = _sharedFinishers(anet, tfrrs)
    smaller = min(len(anet), len(tfrrs))
    print(f"  shared finishers (name+time within {TIME_TOLERANCE}s): "
          f"{len(shared):,}  ->  {_verdict(len(shared), smaller)}")
    for name, at, tt in shared[:show_k]:
        print(f"     {name:<28} anet {at:>8.1f}   tfrrs {tt:>8.1f}")


# ================================================================== #
# CHUNK 6 -- WHICH PAIRS TO CHECK + DRIVER
# ================================================================== #


# _parsePairs
# Purpose: Parse --pairs "anet:tfrrs,anet:tfrrs" into integer tuples.
# Arguments: text (the CLI string) or None.
# Output:  list of (anet_meet, tfrrs_meet).
def _parsePairs(text):
    if not text:
        return []
    out = []
    for chunk in text.split(","):
        a, t = chunk.split(":")
        out.append((int(a), int(t)))
    return out


# _topPairs
# Purpose: The top-N meet links by confidence for a sport, when no explicit pairs
#          are given -- a quick "are my strongest meet links real?" sweep.
# Arguments: sport, n.
# Output:  list of (anet_meet, tfrrs_meet).
def _topPairs(sport, n):
    rows = _query(
        """
        SELECT anet_id, tfrrs_id FROM entity_links
        WHERE entity_type='meet' AND sport=%s
        ORDER BY confidence DESC LIMIT %s
        """,
        (sport, n),
    )
    return [(a, t) for a, t in rows]


# _parseArgs
def _parseArgs():
    p = argparse.ArgumentParser(description="Verify anet<->tfrrs meet pairs correspond.")
    p.add_argument("--sport", required=True, choices=["TF", "XC"])
    p.add_argument("--pairs", help='explicit pairs: "183749:3456,240132:24888"')
    p.add_argument("--top", type=int, default=5,
                   help="if no --pairs, check the top-N meet links (default 5).")
    p.add_argument("--show", type=int, default=8,
                   help="how many shared finisher names to print per pair.")
    return p.parse_args()


def main():
    args = _parseArgs()
    pairs = _parsePairs(args.pairs) or _topPairs(args.sport, args.top)
    print(f"=== verify {len(pairs)} {args.sport} meet pair(s) ===")
    for anet_meet, tfrrs_meet in pairs:
        _reportPair(args.sport, anet_meet, tfrrs_meet, args.show)
    print("\n=== done. ===")


if __name__ == "__main__":
    main()