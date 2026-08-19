# Project: xc-predictor
# File:    scripts/backfill_tfrrs_location.py
# Purpose: Give tfrrs XC meets a `location_id` (and gps) WITHOUT calling a
#          geocoder for anything we can already resolve. Three tiers, in strict
#          precedence, then a list of true leftovers for you to geocode by hand.
#
# ============================================================================
# WHY THIS SHAPE
# ============================================================================
# anet venues are already keyed: meets.location_id, ~99% of which carry gps.
# tfrrs has NO location_id column at all, and gps_lat/long are 100% null. But a
# tfrrs meet is often the SAME physical meet as an anet one (dedup stamped
# results.canon_meet_id), and even when it is not, its COURSE may already be
# resolved because a DIFFERENT tfrrs meet at the same venue was canon-linked.
# So we resolve identity first and geocode only what is genuinely new.
#
#   TIER 1  canon bridge (certain, no matching)
#           tfrrs meet -> its results' canon_meet_id -> anet meet -> location_id
#           + gps. This is a proven same-meet identity, so it is exact.
#
#   TIER 2  course propagation (the "already resolved elsewhere" case)
#           Build (norm(location_raw), state) -> (location_id, gps) from EVERY
#           course we now know: anet's own venues AND the tier-1 tfrrs stamps.
#           Stamp it onto tfrrs meets that tier 1 could not reach but whose
#           (location_raw, state) matches a known course. EXACT key match only
#           (normalized) -- no fuzzy distance, because a wrong venue merge is
#           silent and permanent. Loosen later behind --fuzzy if ever needed.
#
#   TIER 3  leftovers -> geocode
#           Courses that resolved through nobody. Emit ONCE per
#           (norm(location_raw), state) with name/city/state/raw, so you pay a
#           geocoder once per physical venue, not once per meet.
#
# Precedence is tier1 > tier2 > tier3: a proven identity beats a name match beats
# a guess. A meet resolved by an earlier tier is never touched by a later one.
#
# ============================================================================
# WHAT IT WRITES  (only with --apply; dry-run prints the same counts and does not
# write). All writes are to meets_tfrrs, scoped to sport='XC', idempotent (only
# rows whose location_id IS NULL are ever updated).
#   migration : ALTER TABLE meets_tfrrs ADD COLUMN location_id bigint  (if absent)
#   tier 1/2  : UPDATE meets_tfrrs SET location_id, gps_lat, gps_long
#   tier 3    : writes geocode_todo_tfrrs.tsv (no DB write)
#
# USAGE
#   python scripts/backfill_tfrrs_location.py                 # dry run
#   python scripts/backfill_tfrrs_location.py --apply
#   python scripts/backfill_tfrrs_location.py --apply --no-gps  # location_id only
# ============================================================================

import argparse
import os
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- CONSTANTS
# ================================================================== #
_GEO_TODO = "geocode_todo_tfrrs.tsv"


# ================================================================== #
# CHUNK 2 -- NAME NORMALIZATION (the tier-2 key)
# ================================================================== #

# _normRaw
# Purpose : collapse a location_raw string to a stable match key. Conservative
#           on purpose -- lowercase, strip accents-free punctuation, squeeze
#           whitespace. NOT fuzzy: two venues match only if their raw strings
#           agree after this. A silent wrong merge is worse than a missed one.
# Syntax  : re.sub with \s+ collapses runs of whitespace; the [^\w\s] strip
#           removes commas/periods/parens that jitter between sources.
# Output  : the key string, or None when the input is empty.
def _normRaw(raw):
    if not raw:
        return None
    s = raw.strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)        # punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()    # squeeze
    return s or None


# _key
# Purpose : the composite propagation key. State disambiguates same-named venues
#           in different states ("Central Park" exists in many).
def _key(raw, state):
    n = _normRaw(raw)
    if n is None:
        return None
    return (n, (state or "").strip().upper() or None)


# ================================================================== #
# CHUNK 3 -- MIGRATION
# ================================================================== #

# _columnExists / _ensureColumn
# Purpose : add meets_tfrrs.location_id if it is missing. Idempotent: checks
#           information_schema first, so a re-run is a no-op. gps columns already
#           exist (verified), so only location_id is added.
def _columnExists(cur, table, column):
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return cur.fetchone() is not None


def _ensureColumn(conn, dry_run):
    with conn.cursor() as cur:
        if _columnExists(cur, "meets_tfrrs", "location_id"):
            print("  [migration] meets_tfrrs.location_id already exists")
            return
        if dry_run:
            print("  [migration] would ADD COLUMN meets_tfrrs.location_id bigint")
            return
        cur.execute("ALTER TABLE meets_tfrrs ADD COLUMN location_id bigint")
    conn.commit()
    print("  [migration] added meets_tfrrs.location_id bigint")


# ================================================================== #
# CHUNK 4 -- TIER 1: CANON BRIDGE
# ================================================================== #

# _tier1
# Purpose : resolve tfrrs meets that share a canon_meet_id with an anet meet, to
#           that anet meet's location_id + gps.
# Syntax  : the link lives on results, not on meets_tfrrs. So we go
#           results(tfrrs) -> canon_meet_id -> anet meets.  canon_meet_id is the
#           anet meet_id (per the schema notes), so we join it straight to
#           meets.meet_id. We pick ONE anet venue per tfrrs meet via DISTINCT ON
#           (most rows win) in case a canon group spans venues.
# Detail  : returns rows to stamp: (tfrrs_meet_id, location_id, gps_lat, gps_long).
#           Only tfrrs meets still NULL are eligible (idempotent re-run).
def _tier1(cur):
    cur.execute("""
        WITH link AS (
            -- each tfrrs meet's dominant canon_meet_id (mode by row count)
            SELECT DISTINCT ON (r.meet_id)
                   r.meet_id AS tf_meet, r.canon_meet_id AS canon
            FROM results r
            WHERE r.source = 'tfrrs' AND r.canon_meet_id IS NOT NULL
            GROUP BY r.meet_id, r.canon_meet_id
            ORDER BY r.meet_id, count(*) DESC
        )
        SELECT l.tf_meet,
               a.location_id, a.gps_lat, a.gps_long
        FROM link l
        JOIN meets_tfrrs mt ON mt.meet_id = l.tf_meet AND mt.sport = 'XC'
                           AND mt.location_id IS NULL
        JOIN LATERAL (
            SELECT m.location_id, m.gps_lat, m.gps_long
            FROM meets m
            WHERE m.meet_id = l.canon AND m.location_id IS NOT NULL
            LIMIT 1
        ) a ON TRUE
    """)
    return cur.fetchall()


# ================================================================== #
# CHUNK 5 -- KNOWN-COURSE INDEX (feeds tier 2)
# ================================================================== #

# _knownCourses
# Purpose : (norm_raw, state) -> (location_id, gps_lat, gps_long) built from
#           every course we can already place: anet's venues AND the tfrrs meets
#           tier 1 just resolved. When one key has several location_ids (name
#           collision across venues), we keep the MOST COMMON one and count the
#           conflicts so they can be reported rather than silently picked.
# Arguments: cur; tier1_stamps -- [(tf_meet, loc, lat, lon)] from _tier1, so
#            their courses join the index immediately (propagation source).
# Output  : (index dict, n_conflicts).
def _knownCourses(cur, tier1_stamps):
    counts = {}                     # key -> {(loc,lat,lon): n}

    def add(key, loc, lat, lon):
        if key is None or loc is None:
            return
        counts.setdefault(key, {})
        counts[key][(loc, lat, lon)] = counts[key].get((loc, lat, lon), 0) + 1

    # anet venues: meets carries location_raw? No -- anet venue text is
    # course_name. We key anet on (norm(course_name), state) so anet and tfrrs
    # meet on a comparable string. (tfrrs side keys on location_raw.)
    cur.execute("""
        SELECT course_name, state, location_id, gps_lat, gps_long
        FROM meets
        WHERE location_id IS NOT NULL
    """)
    for name, state, loc, lat, lon in cur.fetchall():
        add(_key(name, state), loc, lat, lon)

    # tfrrs courses tier 1 just resolved: key on THEIR location_raw + state, so a
    # different tfrrs meet at the same raw string can inherit it in tier 2.
    if tier1_stamps:
        meets = tuple(s[0] for s in tier1_stamps)
        stamp_by_meet = {s[0]: (s[1], s[2], s[3]) for s in tier1_stamps}
        cur.execute("""
            SELECT meet_id, location_raw, state
            FROM meets_tfrrs
            WHERE sport = 'XC' AND meet_id = ANY(%s)
        """, (list(meets),))
        for meet_id, raw, state in cur.fetchall():
            loc, lat, lon = stamp_by_meet[meet_id]
            add(_key(raw, state), loc, lat, lon)

    index, conflicts = {}, 0
    for key, variants in counts.items():
        if len(variants) > 1:
            conflicts += 1
        best = max(variants.items(), key=lambda kv: kv[1])[0]   # most common
        index[key] = best
    return index, conflicts


# ================================================================== #
# CHUNK 6 -- TIER 2: COURSE PROPAGATION
# ================================================================== #

# _tier2
# Purpose : unresolved tfrrs XC meets whose (norm location_raw, state) matches a
#           known course inherit its location_id + gps. Excludes meets tier 1 is
#           about to stamp (they are resolved) so precedence holds.
# Output  : [(tf_meet, loc, lat, lon)].
def _tier2(cur, known, tier1_meets):
    cur.execute("""
        SELECT meet_id, location_raw, state
        FROM meets_tfrrs
        WHERE sport = 'XC' AND location_id IS NULL
    """)
    stamps = []
    for meet_id, raw, state in cur.fetchall():
        if meet_id in tier1_meets:
            continue
        hit = known.get(_key(raw, state))
        if hit is not None:
            loc, lat, lon = hit
            stamps.append((meet_id, loc, lat, lon))
    return stamps


# ================================================================== #
# CHUNK 7 -- APPLY STAMPS
# ================================================================== #

# _applyStamps
# Purpose : write a batch of (meet, loc, lat, lon) to meets_tfrrs. gps is written
#           only when write_gps and the value is non-null (a location_id with no
#           coordinates still stamps the id). Idempotent: the WHERE re-checks NULL
#           so a concurrent/re-run cannot double-write.
# Syntax  : execute_values would be tidier, but a plain loop over a few thousand
#           rows is clear and the volume is tiny (< ~16k). One commit at the end.
def _applyStamps(conn, stamps, write_gps):
    if not stamps:
        return 0
    with conn.cursor() as cur:
        for meet_id, loc, lat, lon in stamps:
            if write_gps:
                cur.execute("""
                    UPDATE meets_tfrrs
                    SET location_id = %s,
                        gps_lat  = COALESCE(gps_lat,  %s),
                        gps_long = COALESCE(gps_long, %s)
                    WHERE meet_id = %s AND sport = 'XC' AND location_id IS NULL
                """, (loc, lat, lon, meet_id))
            else:
                cur.execute("""
                    UPDATE meets_tfrrs SET location_id = %s
                    WHERE meet_id = %s AND sport = 'XC' AND location_id IS NULL
                """, (loc, meet_id))
    conn.commit()
    return len(stamps)


# ================================================================== #
# CHUNK 8 -- TIER 3: THE GEOCODE LIST
# ================================================================== #

# _tier3
# Purpose : the true leftovers -- tfrrs XC meets STILL null after tiers 1-2 --
#           collapsed to ONE row per (norm location_raw, state), with a
#           representative name/city/raw and the meet count, written to a TSV.
#           No DB write. This is what a geocoder consumes.
def _tier3(cur, resolved_meets, out_dir):
    cur.execute("""
        SELECT meet_id, location_raw, venue_name, city, state
        FROM meets_tfrrs
        WHERE sport = 'XC' AND location_id IS NULL
    """)
    clusters = {}
    for meet_id, raw, venue, city, state in cur.fetchall():
        if meet_id in resolved_meets:
            continue
        key = _key(raw, state)
        if key is None:
            key = ("__no_raw__", meet_id)     # ungroupable -> its own bucket
        c = clusters.setdefault(key, {"n": 0, "raw": raw, "venue": venue,
                                      "city": city, "state": state})
        c["n"] += 1

    path = os.path.join(out_dir, _GEO_TODO)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# n_meets\tstate\tcity\tvenue_name\tlocation_raw\t"
                "gps_lat\tgps_long   (fill the last two with your geocoder)\n")
        for c in sorted(clusters.values(), key=lambda c: -c["n"]):
            f.write("\t".join(str(x) for x in (
                c["n"], c["state"] or "", c["city"] or "",
                c["venue"] or "", c["raw"] or "", "", "")) + "\n")
    return len(clusters), path


# ================================================================== #
# CHUNK 9 -- DRIVER
# ================================================================== #

def _run(apply, write_gps, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    dry = not apply
    with getConn() as conn:
        _ensureColumn(conn, dry)

        # If the column does not exist yet in a dry run, every query below that
        # reads meets_tfrrs.location_id would fail. So in a dry run without the
        # column we describe the plan and stop.
        with conn.cursor() as cur:
            has_col = _columnExists(cur, "meets_tfrrs", "location_id")
        if not has_col:
            print("  [dry-run] location_id not present yet; run --apply once to add"
                  " it, then dry-run again to preview the tier counts.")
            return

        with conn.cursor() as cur:
            cur.execute("SET LOCAL work_mem = '512MB'")
            t1 = _tier1(cur)
            t1_meets = {s[0] for s in t1}
            print(f"  TIER 1 (canon bridge):      {len(t1):,} tfrrs meets")

            known, conflicts = _knownCourses(cur, t1)
            print(f"  known-course index:         {len(known):,} venues "
                  f"({conflicts} name/state collisions, most-common kept)")

            t2 = _tier2(cur, known, t1_meets)
            print(f"  TIER 2 (course propagation):{len(t2):,} tfrrs meets")

        if apply:
            n1 = _applyStamps(conn, t1, write_gps)
            n2 = _applyStamps(conn, t2, write_gps)
            print(f"  applied: tier1={n1:,}  tier2={n2:,}  "
                  f"(gps {'written' if write_gps else 'skipped'})")
        else:
            print("  [dry-run] no rows written")

        resolved = t1_meets | {s[0] for s in t2}
        with conn.cursor() as cur:
            n_clusters, path = _tier3(cur, resolved, out_dir)
        print(f"  TIER 3 (geocode leftovers): {n_clusters:,} distinct venues"
              f"\n    wrote {path}")

        # reconciliation
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) FILTER (WHERE location_id IS NOT NULL),
                                  count(*)
                           FROM meets_tfrrs WHERE sport='XC'""")
            have, tot = cur.fetchone()
        print(f"\n  meets_tfrrs XC now located: {have:,} / {tot:,} "
              f"({'after apply' if apply else 'before apply -- dry run'})")


def main():
    ap = argparse.ArgumentParser(
        description="Backfill tfrrs XC meet location_id via canon-borrow + course "
                    "propagation; list the rest for geocoding. Dry-run default.")
    ap.add_argument("--apply", action="store_true",
                    help="write to meets_tfrrs (default: dry run, no writes)")
    ap.add_argument("--no-gps", action="store_true",
                    help="stamp location_id only, do not copy gps")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()

    initPool()
    print("backfill: tfrrs XC location_id  "
          f"({'APPLY' if args.apply else 'DRY RUN'})")
    _run(args.apply, not args.no_gps, args.out)


if __name__ == "__main__":
    main()