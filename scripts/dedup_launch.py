# Project: xc-predictor
# File:    dedup/merge_links.py
# Purpose: Turn the verified links in entity_links into a usable "these are the
#          same entity" signal, by stamping a shared CANONICAL id onto both the
#          anet and tfrrs rows of each high-confidence link. This is the "merge"
#          half of dedup — but it is deliberately the SOFT kind:
#
#            - it NEVER deletes a row and NEVER overwrites source data
#            - it only WRITES a person_id (athletes) / canon_meet_id (meets),
#              columns that exist precisely to say "for analysis, treat these
#              two source rows as one"
#            - it is fully REVERSIBLE: null the stamped column back out and the
#              two sources are independent again
#
#          Why soft, not a hard merge: anet and tfrrs each hold fields the other
#          lacks (anet's athlete_id lineage; tfrrs's native_id, gender, splits).
#          A physical merge destroys one side's provenance. A shared id keeps
#          both rows on disk and lets the engine read THROUGH the pointer.
#
#          Runs only ABOVE a confidence threshold (a constant below). Confidence
#          is how many confirmed meets agree on a link; low-confidence links are
#          coin flips and are left unstamped until a human raises the bar.

import sys

sys.path.insert(0, "scripts")
import psycopg2.extras
from database import getConn
import time

# ------------------------------------------------------------------ #
# CONSTANTS — the merge dials, named once so they don't drift
# ------------------------------------------------------------------ #

# Minimum confidence (agreeing confirmed-meets) for a link to be acted on.
# This is the ONE number you set AFTER running inspect_entity_links.py: pick
# the level where the sampled links stop looking trustworthy and put it here.
# Start conservative; you can always lower it and re-run (the stamp is additive).
# Per-sport minimum confidence. XC conf-1 measured ~99% clean (merge it); TF
# conf-1 ~80% (club/transfer noise + real collisions), so TF starts at 2 (~96%).
MIN_CONFIDENCE_ATHLETE = {"TF": 1, "XC": 1}   # tuned by school-agreement
MIN_CONFIDENCE_MEET    = {"TF": 2, "XC": 2}  # meets share hundreds; kill the collision slab

# Per-source result tables, by sport — the same split the rest of the pipeline
# uses (anet TF + tfrrs TF share results_tf; anet XC + tfrrs XC share results).
RESULTS_TABLE = {"TF": "results_tf", "XC": "results"}

# DRY RUN guard. While True, the script COUNTS what it would stamp and prints a
# sample, but writes nothing. Flip to False only after the dry run looks right.
DRY_RUN = False


# ------------------------------------------------------------------ #
# CHUNK 1 — DB ACCESS: the only functions that touch the database
# ------------------------------------------------------------------ #
#
# Two helpers. _readLinks pulls the rows we'll act on; _stampPairs does the one
# write pattern (set a canonical id on both sides of a link). Every other
# function builds arguments for these, so the SQL lives in exactly two places.


# _threshold
# Purpose: the confidence floor for one entity_type + sport. Meets need a much
#          higher bar than athletes on TF (times collide, so weak meet links are
#          coincidences); athletes were tuned separately by school-agreement.
def _threshold(entity_type, sport):
    d = MIN_CONFIDENCE_MEET if entity_type == "meet" else MIN_CONFIDENCE_ATHLETE
    return d[sport]

# _readLinks
# Purpose: Fetch the links of one entity type that clear the confidence bar — the
#          exact set this run will stamp. Pulled once, in confidence order, so the
#          dry run can show the strongest first.
# Arguments:
#           entity_type: 'athlete' or 'meet' — which kind of link to read.
#           sport:       'TF' or 'XC' — links are stored per sport.
# Output:  list of (anet_id, tfrrs_id, confidence) tuples, confidence-descending,
#          all at or above MIN_CONFIDENCE.
def _readLinks(entity_type: str, sport: str) -> list:
    threshold = _threshold(entity_type, sport)       # per-sport now
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT anet_id, tfrrs_id, confidence
            FROM entity_links
            WHERE entity_type = %s AND sport = %s AND confidence >= %s
            ORDER BY confidence DESC
            """,
            (entity_type, sport, threshold),
        )
        return cur.fetchall()
    
# _phase
# Purpose: Run one statement, printing a start line and an elapsed+rowcount line,
#          so each stamp phase is visible. Indented to sit under the merge's
#          per-entity output.
# Arguments: cur; label (what this phase is); sql; params.
# Output:  cur.rowcount.
def _phase(cur, label, sql, params=None):
    print(f"      -> {label} ...", flush=True)     # flush so it shows immediately
    t0 = time.time()
    cur.execute(sql, params or ())
    n = cur.rowcount
    dt = time.time() - t0
    tail = f"{n:,} rows" if (n is not None and n >= 0) else "done"
    print(f"         [{dt:6.1f}s] {tail}", flush=True)
    return n


# _stampPairs
# Purpose: Write one canonical id onto BOTH source rows of each link, set-based.
# Arguments:
#           table:     results table to update.
#           id_col:    canonical column to write ('person_id'/'canon_meet_id').
#           anet_key:  anet match column ('athlete_id'/'meet_id').
#           tfrrs_key: tfrrs match column ('native_id'/'meet_id').
#           pairs:     list of (anet_id, tfrrs_id, canonical_id).
# Output:  int -- number of links stamped (== len(pairs)).
def _stampPairs(table, id_col, anet_key, tfrrs_key, pairs):
    if not pairs:
        return 0
    print(f"      stamping {len(pairs):,} links into {table}.{id_col}", flush=True)

    BATCH = 20000  # stage keys per batch

    def _batchedUpdate(cur, conn, side_label, stage_key, row_key, source):
        # index + range over the stage key we're joining on this pass
        cur.execute(f"CREATE INDEX IF NOT EXISTS _stage_{stage_key}_idx ON _stage ({stage_key})")
        cur.execute("SELECT min(seq), max(seq) FROM _stage")
        lo, hi = cur.fetchone()
        if lo is None:
            return 0
        total, k = 0, lo
        while k <= hi:
            k2 = k + BATCH
            n = _phase(cur, f"{side_label} keys [{k:,} .. {k2:,})", f"""
                UPDATE {table} AS r SET {id_col} = s.canon
                FROM _stage s
                WHERE r.{row_key} = s.{stage_key} AND r.source = %s
                    AND s.seq >= %s AND s.seq < %s
                    AND r.{id_col} IS DISTINCT FROM s.canon
            """, (source, k, k2))
            conn.commit()          # each batch is durable; crash loses only one batch
            total += n
            k = k2
        print(f"      {side_label} total: {total:,} rows", flush=True)
        return total

    with getConn() as conn:
        cur = conn.cursor()

        # plain temp table: batch commits must NOT drop it (no ON COMMIT DROP);
        # it still disappears when this connection closes.
        _phase(cur, "create temp stage", """
            DROP TABLE IF EXISTS _stage;
            CREATE TEMP TABLE _stage (
                seq bigserial,
                anet_key bigint, tfrrs_key bigint, canon bigint
            )
        """)

        print(f"      -> load {len(pairs):,} pairs into stage ...", flush=True)
        t0 = time.time()
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO _stage (anet_key, tfrrs_key, canon) VALUES %s",
            [(a, t, canon) for (a, t, canon) in pairs],
            page_size=10000,
        )
        print(f"         [{time.time()-t0:6.1f}s] loaded", flush=True)

        _phase(cur, "analyze stage", "ANALYZE _stage")

        _batchedUpdate(cur, conn, "update anet side",  "anet_key",  anet_key,  "anet")
        _batchedUpdate(cur, conn, "update tfrrs side", "tfrrs_key", tfrrs_key, "tfrrs")

        conn.commit()   # covers the stage-index/housekeeping tail
        print(f"      committed.", flush=True)
    return len(pairs)


# ------------------------------------------------------------------ #
# CHUNK 2 — CANONICAL ID: decide the shared value a link collapses to
# ------------------------------------------------------------------ #
#
# When anet-X and tfrrs-Y are the same entity, they need ONE id to share. We
# reuse the ANET id as the canonical value: anet ids are the stable, positive,
# already-everywhere identifiers, so adopting them means existing anet-keyed
# data needs no remapping — only the tfrrs side learns the anet id. (tfrrs keeps
# its own native_id too; canonical is an ADDITION, not a replacement.)


# _canonicalId
# Purpose: Pick the shared id a link collapses to. One rule, one place, so meets
#          and athletes canonicalize the same way and the choice can't drift.
# Arguments:
#           anet_id:  the anet side's id (athlete_id or meet_id).
#           tfrrs_id: the tfrrs side's id (unused today — see note).
# Output:  the canonical id (currently always the anet id).
def _canonicalId(anet_id, tfrrs_id):
    # Reuse the anet id. tfrrs_id is passed in so the rule is easy to change
    # later (e.g. mint a fresh person_id namespace) without touching callers.
    return anet_id


# _pairsWithCanonical
# Purpose: Attach the canonical id to each (anet_id, tfrrs_id) link, producing the
#          (anet_id, tfrrs_id, canonical_id) triples _stampPairs writes.
# Arguments:
#           links: the (anet_id, tfrrs_id, confidence) rows from _readLinks.
# Output:  list of (anet_id, tfrrs_id, canonical_id) triples (confidence dropped —
#          it gated entry already; the stamp itself doesn't need it).
def _pairsWithCanonical(links) -> list:
    return [(a, t, _canonicalId(a, t)) for (a, t, _conf) in links]

# ------------------------------------------------------------------ #
# CHUNK 2b -- FAN-OUT GUARD: keep links 1:1 before stamping
# ------------------------------------------------------------------ #
#
# WHY THIS EXISTS:
# A safe link is 1:1 -- one anet id <-> one tfrrs id. But entity_links can hold
# fan-out: the SAME tfrrs id linked to two different anet ids (or vice-versa).
# If we stamped both, _stampPairs would write one canonical id, then overwrite it
# with the other (execute_values gives no order), silently welding two different
# people under whichever won. This guard removes that risk BEFORE any write.
#
# THE RULE (deliberately strict, because a bad merge poisons ratings):
#   - group the links by each side's id
#   - an id that appears ONCE is unambiguous -> keep its link
#   - an id that appears MORE than once is contested -> drop ALL its links to a
#     quarantine (we do NOT guess which partner is right here; confidence alone
#     can't separate "same person, two tfrrs profiles" from "two real people who
#     collided", so we refuse rather than risk welding strangers)
# The dropped links aren't lost -- they're returned so the preview can report
# how many were held back for a later, smarter pass.
 
 
# _countById
# Purpose: Count how many links each id (on one side) participates in. The index
#          says which side: 0 = anet_id, 1 = tfrrs_id in the (anet,tfrrs,conf) tuple.
# Arguments: links (list of (anet_id, tfrrs_id, confidence)); side_index (0 or 1).
# Output:  dict {id: how_many_links_it_appears_in}.
def _countById(links, side_index):
    counts = {}
    for row in links:
        key = row[side_index]
        counts[key] = counts.get(key, 0) + 1   # +1 per link this id is in
    return counts
 
 
def _resolveFanout(links, entity_type):
    anet_count  = _countById(links, 0)        # partners per anet id
    tfrrs_count = _countById(links, 1)        # partners per tfrrs id
    clean, quarantined = [], []
    for (a, t, conf) in links:
        if entity_type == "meet":
            # one anet meet -> many tfrrs fragments is the CORRECT consolidation;
            # only a tfrrs fragment claimed by >1 anet meet is ambiguous.
            ok = tfrrs_count[t] == 1
        else:
            # athletes: a real person is 1:1 on BOTH sides.
            ok = anet_count[a] == 1 and tfrrs_count[t] == 1
        (clean if ok else quarantined).append((a, t, conf))
    return clean, quarantined


# ------------------------------------------------------------------ #
# CHUNK 3 — DRY RUN: show what WOULD be stamped, write nothing
# ------------------------------------------------------------------ #
#
# The merge is the first step in this whole dedup that MUTATES result rows, so it
# defaults to a dry run: print the count and a sample at the top (strongest) and
# bottom (weakest above threshold) of the set, so you can confirm the threshold
# is sane before any write. The sample resolves ids to names so it's readable.


# _previewName
# Purpose: Best-effort human label for a link, so the dry-run sample is readable
#          instead of a wall of ids. anet name via athletes; tfrrs via the row.
# Arguments:
#           entity_type, anet_id, tfrrs_id, table: enough to look each side up.
# Output:  a short "anetName <-> tfrrsName" string (meets show ids, which is fine).
def _previewName(entity_type, anet_id, tfrrs_id, table) -> str:
    if entity_type != "athlete":
        return f"anet meet {anet_id} <-> tfrrs meet {tfrrs_id}"
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT first_name, last_name FROM athletes WHERE athlete_id = %s LIMIT 1",
            (anet_id,),
        )
        a = cur.fetchone()
        cur.execute(
            f"SELECT athlete_name FROM {table} WHERE native_id = %s AND source = 'tfrrs' LIMIT 1",
            (tfrrs_id,),
        )
        t = cur.fetchone()
    anet_name  = f"{(a[0] or '') if a else ''} {(a[1] or '') if a else ''}".strip() or "?"
    tfrrs_name = (t[0] if t else None) or "?"
    return f"{anet_name!r} <-> {tfrrs_name!r}"


# _previewSet
# Purpose: Print the dry-run summary for one (entity_type, sport): how many links
#          clear the bar, and a few samples from the strong and weak ends so you
#          can judge the threshold.
# Arguments:
#           entity_type, sport, links, table: the set and where to resolve names.
# Output:  none (prints).
def _previewSet(entity_type, sport, links, table) -> None:
    threshold = _threshold(entity_type, sport)
    print(f"  {entity_type}/{sport}: {len(links):,} links >= confidence {threshold}")
    if not links:
        return
    # links is confidence-descending; show the top 5 and the bottom 5 (the
    # weakest that still cleared the bar — the riskiest the merge would act on).
    head = links[:5]
    tail = links[-5:] if len(links) > 5 else []
    for a, t, conf in head:
        print(f"    [conf {conf:>3}] {_previewName(entity_type, a, t, table)}")
    if tail:
        print("    ...")
        for a, t, conf in tail:
            print(f"    [conf {conf:>3}] {_previewName(entity_type, a, t, table)}")


# ------------------------------------------------------------------ #
# CHUNK 4 — MAIN: per sport, per entity, preview then (maybe) stamp
# ------------------------------------------------------------------ #
#
# main() walks both sports and both entity types. For each it reads the
# above-threshold links, previews them, and — only if DRY_RUN is off — stamps the
# canonical id onto both sides. Athletes write person_id; meets write
# canon_meet_id. The two column/key sets are the only thing that differs between
# the athlete and meet passes, so they're passed as plain arguments to one path.


# _mergeEntity
# Purpose: Run one entity type for one sport end to end: read -> preview ->
#          (stamp). The single place the athlete vs meet specifics are supplied.
# Arguments:
#           entity_type: 'athlete' or 'meet'.
#           sport:       'TF' or 'XC'.
#           id_col:      canonical column to write ('person_id'/'canon_meet_id').
#           anet_key:    anet match column ('athlete_id'/'meet_id').
#           tfrrs_key:   tfrrs match column ('native_id'/'meet_id').
# Output:  none (prints; writes only when DRY_RUN is False).
def _mergeEntity(entity_type, sport, id_col, anet_key, tfrrs_key) -> None:
    table = RESULTS_TABLE[sport]
    links = _readLinks(entity_type, sport)
    clean, quarantined = _resolveFanout(links, entity_type)        # <-- strip fan-out first
    print(f"    fan-out held back: {len(quarantined):,}  |  1:1-safe: {len(clean):,}")
    _previewSet(entity_type, sport, clean, table)     # preview only what we'll stamp

    if DRY_RUN:
        return

    links = clean 

    pairs = _pairsWithCanonical(links)
    n = _stampPairs(table, id_col, anet_key, tfrrs_key, pairs)
    print(f"    stamped {n:,} {entity_type} links into {table}.{id_col}")


# main
# Purpose: The full merge: both sports, athletes then meets, preview-then-stamp.
#          Reads entity_links + result rows; writes only the canonical id columns,
#          and only when DRY_RUN is False.
# Output:  none.
def main():
    mode = "DRY RUN (no writes)" if DRY_RUN else "LIVE (stamping ids)"
    print(f"=== merge entity_links — {mode}, athlete {MIN_CONFIDENCE_ATHLETE}, meet {MIN_CONFIDENCE_MEET} ===")

    for sport in RESULTS_TABLE:
        print(f"\n--- {sport} ---")
        # Athletes: stamp person_id, matching anet athlete_id <-> tfrrs native_id.
        _mergeEntity("athlete", sport,
                     id_col="person_id", anet_key="athlete_id", tfrrs_key="native_id")
        # Meets: stamp canon_meet_id, matching anet meet_id <-> tfrrs meet_id.
        # (Same integer on each side is fine — they're DIFFERENT source rows,
        # scoped by source in the UPDATE, so no id-space collision.)
        _mergeEntity("meet", sport,
                     id_col="canon_meet_id", anet_key="meet_id", tfrrs_key="meet_id")

    if DRY_RUN:
        print("\nDRY RUN done. If the samples look right, set DRY_RUN = False and re-run.")
    else:
        print("\nDone. To undo: NULL out person_id / canon_meet_id for source='tfrrs'.")


if __name__ == "__main__":
    main()