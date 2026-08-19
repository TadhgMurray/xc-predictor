#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/fuzzy_pass_fast.py   (supersedes fuzzy_pass.py)
# Purpose: Same STAGE-2 per-meet fuzzy recovery, but BATCHED. The old version ran
#          two queries PER confirmed meet (~33K meets -> ~66K round-trips), which
#          crawls. This pulls ALL confirmed-meet finishers for a sport in TWO
#          queries (anet, tfrrs), groups them by meet-pair in memory, and runs the
#          same scorer. Same result, minutes instead of forever.
#
#          HOW: entity_links holds the confirmed (anet_meet, tfrrs_meet) pairs. We
#          join finishers to those pairs in SQL so we only pull rows in confirmed
#          meets, tagging each row with BOTH meet ids so we can bucket by pair in
#          Python. Then per pair: exact-match filter, then STRONG fuzzy on the
#          unmatched (>=0.85, unambiguous), same as before.
#
#          Run:  python scripts/fuzzy_pass_fast.py            (dry run)
#                python scripts/fuzzy_pass_fast.py --apply    (write links)

import argparse
import re
import sys
from collections import defaultdict
import time 
# NEW:
# fuzz.ratio(a, b, score_cutoff=X) -> similarity 0..100.
# score_cutoff is the key speed feature: if the score can't reach X,
# rapidfuzz aborts the comparison early and returns 0.0 instead.
from rapidfuzz import fuzz

sys.path.insert(0, "scripts")
import psycopg2.extras
from database import getConn

TIME_TOLERANCE = 0.3
FUZZY_NAME_MIN = 85.0   # the LINKING bar -- unchanged, links must clear this
BORDERLINE_MIN = 85.0   # the REPORTING bar -- we *look at* 80..85, never link it
MAX_BORDERLINE_SAMPLES = 50   # cap stored examples so memory stays flat
RESULTS = {"TF": "results_tf", "XC": "results"}


# ================================================================== #
# CHUNK 1 -- DB ACCESS + KEYS
# ================================================================== #


def _query(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _normName(name):
    if not name:
        return ""
    s = name.lower().strip()
    accents = str.maketrans("\u00e1\u00e0\u00e2\u00e4\u00e3\u00e9\u00e8\u00ea\u00eb\u00ed\u00ec\u00ee\u00ef\u00f3\u00f2\u00f4\u00f6\u00f5\u00fa\u00f9\u00fb\u00fc\u00f1\u00e7",
                            "aaaaaeeeeiiiiooooouuuunc")
    s = s.translate(accents)
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _roundTime(t):
    return None if t is None else round(float(t), 1)


def _isRelayName(name):
    if not name or not name.strip():
        return True
    low = name.lower()
    return ("<" in name) or ("relay" in low) or (low.strip() == "team")


def _ticks():
    span = int(TIME_TOLERANCE * 10)
    return [round(d * 0.1, 1) for d in range(-span, span + 1)]



# ================================================================== #
# CHUNK 2 -- BULK LOAD ALL CONFIRMED-MEET FINISHERS (2 queries/sport)
# ================================================================== #
#
# Join finishers to the confirmed meet-pair list in entity_links, so we pull ONLY
# rows in confirmed meets, and tag each with the pair (anet_meet, tfrrs_meet) so we
# can bucket in memory. anet name via athletes join; tfrrs name off the row.


# _loadAnet
# Output: dict {(anet_meet, tfrrs_meet): [(athlete_id, normName, rt), ...]}
# _loadAnet
# Output: dict {(anet_meet, tfrrs_meet): [(athlete_id, normName, rt), ...]}
def _loadAnet(table):
    sql = f"""
        SELECT el.anet_id AS anet_meet, el.tfrrs_id AS tfrrs_meet,
               r.athlete_id,
               (COALESCE(a.first_name,'')||' '||COALESCE(a.last_name,'')) AS name,
               r.time_seconds
        FROM entity_links el
        JOIN {table} r
          ON r.meet_id = el.anet_id AND r.source='anet'
        LEFT JOIN athletes a
          ON a.athlete_id = r.athlete_id AND a.school = r.school
        WHERE el.entity_type='meet' AND el.sport=%s
          AND r.time_seconds IS NOT NULL AND r.time_seconds<>999999
          AND r.place IS NOT NULL AND r.place<>0
          AND r.athlete_id IS NOT NULL
    """
    out = defaultdict(list)
    for am, tm, aid, name, t in _streamQuery(sql, (_sportOf(table),)):
        if _isRelayName(name):
            continue
        out[(am, tm)].append((aid, _normName(name), _roundTime(t)))
    return out


# _loadTfrrs
# Purpose:   bulk-load every tfrrs finisher inside a confirmed meet pair,
#            tagged with the pair so Python can bucket by meet. Streams
#            (server-side cursor) so the full result never sits in RAM.
# Arguments: table -- "results_tf" or "results"; picks the results table.
# Output:    dict {(anet_meet, tfrrs_meet): [(native_id, normName, rt), ...]}
def _loadTfrrs(table):
    sql = f"""
        SELECT el.anet_id AS anet_meet, el.tfrrs_id AS tfrrs_meet,
               r.native_id, r.athlete_name, r.time_seconds
        FROM entity_links el
        JOIN {table} r
          ON r.meet_id = el.tfrrs_id AND r.source='tfrrs'
        WHERE el.entity_type='meet' AND el.sport=%s
          AND r.time_seconds IS NOT NULL          -- tfrrs non-finisher sentinel (NULL, not 999999)
          AND r.place IS NOT NULL AND r.place<>0
          AND r.native_id IS NOT NULL             -- id-less rows can't be linked; see below
    """
    out = defaultdict(list)
    for am, tm, nid, name, t in _streamQuery(sql, (_sportOf(table),)):
        if _isRelayName(name):                    # relay/team entries out, same as anet side
            continue
        out[(am, tm)].append((nid, _normName(name), _roundTime(t)))
    return out


def _sportOf(table):
    return "TF" if table == "results_tf" else "XC"

# _streamQuery
# Purpose:   run one big SELECT without ever holding the full result in RAM.
#            A NAMED cursor makes Postgres keep the result set server-side and
#            hand it over in batches; a plain cursor (what _query uses) makes
#            the driver pull EVERYTHING into a Python list up front.
# Arguments: sql    -- the SELECT text, %s placeholders as usual.
#            params -- tuple of values for the placeholders.
# Output:    a GENERATOR of row tuples. Consume it in a for-loop; rows arrive
#            in batches of `itersize` under the hood, invisibly to the caller.
#            The connection stays open until the generator is exhausted --
#            so consume it fully inside one loop, don't stash it for later.
def _streamQuery(sql, params=()):
    with getConn() as conn:
        # Giving the cursor a NAME is the entire trick: psycopg2 then creates
        # a server-side portal ("DECLARE stream_cur CURSOR FOR ...") instead
        # of buffering the whole result client-side.
        cur = conn.cursor(name="stream_cur")
        cur.itersize = 50_000          # rows fetched per round-trip, behind the scenes
        cur.execute(sql, params)
        for row in cur:                # iterating pulls the next batch as needed
            yield row                  # hand ONE row up to the caller, keep our place

# _loadPairs
# Purpose:   bulk-load both sides' finishers and keep only the meet pairs that
#            have finishers on BOTH sides (one-sided pairs have nothing to
#            match against, so they're dead weight).
# Arguments: table -- "results_tf" or "results", picks the results table.
#            sport -- "TF" or "XC", used only for the print line.
# Output:    (anet_by_pair, tfrrs_by_pair, pairs) -- the two per-pair dicts
#            from the loaders, plus the SET of keys present in both.
def _loadPairs(table, sport):
    print(f"\n--- {sport}: bulk-loading confirmed-meet finishers ---")
    anet_by_pair  = _loadAnet(table)
    tfrrs_by_pair = _loadTfrrs(table)
    pairs = set(anet_by_pair) & set(tfrrs_by_pair)   # dict -> its keys; & = intersection
    print(f"  {len(pairs):,} confirmed meet pairs with finishers both sides")
    return anet_by_pair, tfrrs_by_pair, pairs


# ================================================================== #
# CHUNK 3 -- FUZZY MATCH WITHIN ONE MEET (from memory)  [rapidfuzz]
# ================================================================== #
#
# Threshold note: rapidfuzz scores on 0..100, so the old 0.85 becomes 85.0.



def _buildTfrrs(tfrrs):
    # (unchanged from before)
    by_key, by_time = set(), defaultdict(list)
    for nid, norm, t in tfrrs:
        if not norm or t is None:
            continue
        by_key.add((norm, t))
        by_time[t].append((nid, norm))
    return by_key, by_time


def _hasExact(name, t, by_key, ticks):
    # (unchanged from before)
    return any((name, round(t + dt, 1)) in by_key for dt in ticks)


# _scoreCandidate
# Purpose:   score ONE candidate against the anet name.
# Arguments: name, cand -- two normalized names.
# Output:    similarity 0..100. Anything provably below BORDERLINE_MIN
#            comes back 0.0 (early exit). We cut at 80 not 85 because the
#            borderline report needs to SEE the 80..85 band -- a cutoff of
#            85 would erase exactly the scores we want to inspect.
def _scoreCandidate(name, cand):
    return fuzz.ratio(name, cand, score_cutoff=BORDERLINE_MIN)


# _bestFuzzy
# Purpose:   find the single UNAMBIGUOUS >=85 match, and RECORD (not link)
#            any 80..85 near-misses into `borderline` for the dry-run report.
# Arguments: name, t, by_time, ticks -- as before.
#            borderline -- dict with keys:
#              "count"   -- int, total near-miss scores seen (all of them)
#              "samples" -- list of (anet_name, tfrrs_name, score), capped
#                           at MAX_BORDERLINE_SAMPLES so it can't balloon.
#            Mutated in place; shared across the whole sport run.
# Output:    tfrrs native_id, or None. Borderline hits NEVER produce a link.
def _bestFuzzy(name, t, by_time, ticks, borderline):
    best_nid = None
    n_above  = 0                                  # candidates clearing 85

    for dt in ticks:
        for nid, cand in by_time.get(round(t + dt, 1), []):
            if cand == name:
                continue
            score = _scoreCandidate(name, cand)   # 0.0 if provably < 80
            if score >= FUZZY_NAME_MIN:           # >= 85: a real qualifier
                n_above += 1
                if n_above > 1:
                    return None                   # ambiguous -> bail
                best_nid = nid
            elif score >= BORDERLINE_MIN:         # 80..85: report only
                borderline["count"] += 1
                if len(borderline["samples"]) < MAX_BORDERLINE_SAMPLES:
                    borderline["samples"].append((name, cand, round(score, 1)))

    return best_nid


def _fuzzyLinksForMeet(anet, tfrrs, ticks, borderline):     # <- new param
    by_key, by_time = _buildTfrrs(tfrrs)
    links = []
    for aid, name, t in anet:
        if not name or t is None:
            continue
        if _hasExact(name, t, by_key, ticks):
            continue
        nid = _bestFuzzy(name, t, by_time, ticks, borderline)   # <- forwarded
        if nid is not None:
            links.append((aid, nid))
    return links


# ================================================================== #
# CHUNK 4 -- WRITE
# ================================================================== #


def _writeAthleteLinks(sport, conf_by_pair):
    if not conf_by_pair:
        return 0
    with getConn() as conn:
        cur = conn.cursor()
        psycopg2.extras.execute_values(cur, """
            INSERT INTO entity_links
                (entity_type, sport, anet_id, tfrrs_id, confidence, method)
            VALUES %s
            ON CONFLICT (entity_type, sport, anet_id, tfrrs_id)
            DO UPDATE SET confidence = GREATEST(entity_links.confidence, EXCLUDED.confidence)
        """, [("athlete", sport, a, t, c, "fuzzy-in-meet")
              for (a, t), c in conf_by_pair.items()])
        conn.commit()
    return len(conf_by_pair)


# ================================================================== #
# CHUNK 5 -- DRIVER
# ================================================================== #

# _scoreAllPairs
# Purpose:   run the per-meet fuzzy matcher over every confirmed pair, timing
#            as it goes so a slow run announces itself in the first minute.
# Arguments: pairs         -- set of (anet_meet, tfrrs_meet) keys to score.
#            anet_by_pair  -- dict {pair: [(athlete_id, norm_name, rt), ...]}.
#            tfrrs_by_pair -- dict {pair: [(native_id, norm_name, rt), ...]}.
#            ticks         -- the +/-TIME_TOLERANCE offsets from _ticks().
#            borderline    -- the shared collector dict {"count", "samples"};
#                             mutated in place all the way down in _bestFuzzy.
# Output:    dict {(athlete_id, native_id): set of pairs that produced it}.
#            The set's SIZE later becomes the link's confidence.
def _scoreAllPairs(pairs, anet_by_pair, tfrrs_by_pair, ticks, borderline):
    conf_by_pair = {}
    t0 = time.time()                              # wall-clock anchor for the rate math

    for i, pair in enumerate(pairs, 1):           # enumerate from 1 so i is "meets done"
        links = _fuzzyLinksForMeet(
            anet_by_pair[pair], tfrrs_by_pair[pair], ticks, borderline)
        for (aid, nid) in links:
            # setdefault: fetch the set for this (aid, nid), creating an empty
            # one first if it's the first sighting -- then add this meet pair.
            conf_by_pair.setdefault((aid, nid), set()).add(pair)

        if i % 500 == 0:
            _printProgress(i, len(pairs), t0, len(conf_by_pair))

    return conf_by_pair


# _printProgress
# Purpose:   one progress line with a live rate + ETA, so "is the fix working"
#            is answerable at minute one instead of hour nine.
# Arguments: done    -- meets scored so far.
#            total   -- total meets to score.
#            t0      -- time.time() taken when the loop started.
#            n_links -- unique fuzzy (aid, nid) pairs found so far.
# Output:    none -- prints one line.
def _printProgress(done, total, t0, n_links):
    rate = done / (time.time() - t0)              # meets per second, cumulative
    eta_min = (total - done) / rate / 60          # remaining meets / rate, in minutes
    print(f"    scored {done:,}/{total:,} meets "
          f"({rate:.1f}/s, ~{eta_min:.0f} min left), "
          f"{n_links:,} fuzzy pairs so far")
    
# _printBorderlineReport
# Purpose:   the eyes-first output this whole exercise exists for -- how big is
#            the 80..85 band, and what do real examples in it LOOK like. This is
#            the evidence for the lower-the-threshold decision; nothing here is
#            linked or written.
# Arguments: borderline -- {"count": int, "samples": [(anet, tfrrs, score)]}.
# Output:    none -- prints the count and up to 20 samples.
def _printBorderlineReport(borderline):
    print(f"  borderline (80..85, NOT linked): {borderline['count']:,} scores")
    if not borderline["samples"]:
        return                                    # empty band -> nothing to show
    print(f"  first {min(20, len(borderline['samples']))} examples "
          f"(read these before deciding on the threshold):")
    for a, tname, s in borderline["samples"][:20]:
        # !r = repr(): wraps names in quotes so trailing spaces / odd
        # characters are visible instead of invisible.
        print(f"    {s:5.1f}  {a!r:35s} vs {tname!r}")


# _runSport
# Purpose:   one sport, end to end: load -> score -> report -> (maybe) write.
#            This function makes NO decisions itself -- it is a table of
#            contents. Each line is one phase; the phases live in helpers.
# Arguments: sport -- "TF" or "XC"; picks the results table via RESULTS.
#            apply -- bool. False = dry run (score + report, write nothing).
#                     True  = also write the >=85 links to entity_links.
# Output:    none. Prints throughout; writes rows only when apply=True.
def _runSport(sport, apply):
    table = RESULTS[sport]                        # "results_tf" or "results"

    # PHASE 1: pull both sides' finishers, keep meet pairs present on both.
    anet_by_pair, tfrrs_by_pair, pairs = _loadPairs(table, sport)

    # PHASE 2: the collector. Created HERE, once per sport, then passed DOWN
    # through _scoreAllPairs -> _fuzzyLinksForMeet -> _bestFuzzy, which mutates
    # it. Mutation is the return channel -- nothing passes it back up.
    borderline = {"count": 0, "samples": []}

    # PHASE 3: the actual work. Scores every meet pair; returns the >=85
    # links as {(athlete_id, native_id): set_of_meet_pairs_that_agreed}.
    conf_by_pair = _scoreAllPairs(pairs, anet_by_pair, tfrrs_by_pair,
                                  _ticks(), borderline)

    # PHASE 4: convert "set of agreeing meets" -> its SIZE = the confidence.
    counted = {p: len(ms) for p, ms in conf_by_pair.items()}
    print(f"  fuzzy athlete recoveries: {len(counted):,} unique pairs")

    # PHASE 5: the eyes-first report -- the 80..85 band you asked to see
    # before deciding whether to lower the threshold. Never linked.
    _printBorderlineReport(borderline)

    # PHASE 6: write, only on --apply. Identical to the original file.
    if not apply:
        print("  DRY RUN -- nothing written. Re-run with --apply to save.")
        return
    n = _writeAthleteLinks(sport, counted)
    print(f"  wrote {n:,} fuzzy athlete links (method='fuzzy-in-meet').")


def _parseArgs():
    p = argparse.ArgumentParser(description="Stage-2 per-meet fuzzy recovery (batched).")
    p.add_argument("--sport", choices=["TF", "XC"])
    p.add_argument("--apply", action="store_true")
    return p.parse_args()


def main():
    args = _parseArgs()
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== fuzzy pass (stage 2, batched) -- {mode} ===")
    for sport in ([args.sport] if args.sport else ["TF", "XC"]):
        _runSport(sport, args.apply)
    print("\n=== done. re-run the merge afterward to stamp the fuzzy delta. ===")


if __name__ == "__main__":
    main()