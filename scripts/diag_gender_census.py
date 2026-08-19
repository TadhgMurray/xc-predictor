# Project: xc-predictor
# File:    scripts/diag_gender_census.py
# Purpose: READ-ONLY. Quantify the three gender/identity questions of 7/14:
#
#   A. IDENTITY LINKAGE -- how many rows have NO athlete link (athlete_id AND
#      person_id both NULL), by table and source, and how many of those still
#      carry a native_id (proof the source HAD an id we failed to link -- the
#      scraper-workstream prize, sized in rows).
#
#   B. TITLE-GENDER COVERAGE -- of the tfrrs blob's division titles, how many
#      parse to M/F with the CURRENT parser, how many carry no signal, and --
#      the actionable part -- the most common unparsed title words, i.e. the
#      exact wordlist an extended parser should learn. Row-weighted too:
#      every unlinked tfrrs XC row is classified by whether the blob could,
#      couldn't, or might-with-a-better-parser rescue its gender.
#
#   C. MIXED-TITLE INVENTORY -- divisions whose title signals BOTH genders or
#      says mixed/coed. These must NEVER receive a division-wide gender (by
#      override OR parser); they are per-result territory. Listed by name.
#
# Writes: nothing. Note section A scans results_tf (191M rows) once --
# expect a few minutes on that step.
#
# USAGE:  python scripts\diag_gender_census.py

import re
import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- THE PARSERS (current one mirrored exactly, + extensions)
# ================================================================== #

# _genderCurrent : byte-for-byte the logic of backfill's _genderFromDivName,
#   so section B measures the REAL pipeline, not an approximation.
def _genderCurrent(name):
    if not name:
        return None
    t = name.lower()
    if "women" in t or "girls" in t or "female" in t:
        return "F"
    if "men" in t or "boys" in t or "male" in t:
        return "M"
    return None


# _genderExtended : candidate additions, kept SEPARATE so section B can report
#   "rescuable by a better parser" without touching the pipeline. Word-boundary
#   regexes because single letters ('W', 'M') and short tokens ('wmn') would
#   otherwise match inside innocent words. Order: mixed first (a mixed title
#   must never resolve to one gender), then F, then M.
_MIXED_RE = re.compile(r"\b(mixed|coed|co-ed|open)\b", re.I)
_F_RE = re.compile(r"\b(w|f|girl|lady|ladies|wmn|women'?s?|female)\b", re.I)
_M_RE = re.compile(r"\b(m|b|boy|men'?s?|male)\b", re.I)

def _genderExtended(name):
    if not name:
        return None
    if _MIXED_RE.search(name) or (_F_RE.search(name) and _M_RE.search(name)):
        return "MIXED"
    if _F_RE.search(name):
        return "F"
    if _M_RE.search(name):
        return "M"
    return None


# ================================================================== #
# CHUNK 2 -- SECTION A: identity linkage by table x source
# ================================================================== #

# One aggregation pass per table. FILTER (WHERE ...) is Postgres conditional
# counting: several tallies in a single scan instead of five scans.
def _identityCensus(cur, table):
    cur.execute(f"""
        SELECT source,
               count(*)                                                   AS total,
               count(*) FILTER (WHERE athlete_id IS NULL)                 AS a_null,
               count(*) FILTER (WHERE person_id IS NULL)                  AS p_null,
               count(*) FILTER (WHERE athlete_id IS NULL
                                  AND person_id IS NULL)                  AS unlinked,
               count(*) FILTER (WHERE athlete_id IS NULL
                                  AND person_id IS NULL
                                  AND native_id IS NOT NULL)              AS unlinked_with_native
        FROM {table} GROUP BY source ORDER BY source
    """)
    print(f"\n== A. identity linkage: {table} ==")
    print(f"  {'source':<8} {'total':>12} {'unlinked':>12} {'of which native_id':>20}")
    for src, total, a, p, unlinked, with_native in cur.fetchall():
        pct = 100.0 * unlinked / total if total else 0
        print(f"  {str(src):<8} {total:>12,} {unlinked:>12,} ({pct:4.1f}%) "
              f"{with_native:>14,}")


# ================================================================== #
# CHUNK 3 -- SECTION B: blob title coverage, entry- and row-weighted
# ================================================================== #

def _loadBlob(cur):
    """Same source as backfill's _loadTfrrsBlobDistances, div_name included."""
    cur.execute("""
        SELECT meet_id, division_distances
        FROM meets_tfrrs WHERE division_distances IS NOT NULL
    """)
    out = {}
    for meet_id, blob in cur.fetchall():
        if not blob:
            continue
        for div_str, info in blob.items():
            if isinstance(info, dict):
                try:
                    out[(meet_id, int(div_str))] = info.get("div_name")
                except (TypeError, ValueError):
                    continue
    return out


def _titleCoverage(blob):
    print(f"\n== B1. blob title coverage ({len(blob):,} (meet,div) entries) ==")
    tally = {"F": 0, "M": 0, None: 0}
    rescuable, unparsed = {}, {}
    for key, name in blob.items():
        cur_g = _genderCurrent(name)
        tally[cur_g] += 1
        if cur_g is None:
            ext = _genderExtended(name)
            if ext in ("F", "M"):
                rescuable[key] = (name, ext)
            else:
                # normalize the title so counts aggregate ('Race #1' ~ 'Race #2')
                norm = re.sub(r"\d+", "#", (name or "").lower()).strip()
                unparsed[norm] = unparsed.get(norm, 0) + 1
    print(f"  current parser: F={tally['F']:,}  M={tally['M']:,}  "
          f"no-signal={tally[None]:,}")
    print(f"  of the no-signal entries, extended parser would resolve: "
          f"{len(rescuable):,}")
    print("  top unparsed title shapes (the wordlist to learn or ignore):")
    for norm, n in sorted(unparsed.items(), key=lambda kv: -kv[1])[:20]:
        print(f"    {n:>6,}  {norm[:70]!r}")
    return rescuable


def _rowWeighted(cur, blob, rescuable):
    """Every UNLINKED tfrrs XC row lands in exactly one bucket: blob resolves
    it today / a better parser would / title exists but carries nothing /
    no blob entry at all. THE prioritization numbers."""
    cur.execute("""
        SELECT meet_id, div_id, count(*)
        FROM results
        WHERE source = 'tfrrs' AND athlete_id IS NULL AND person_id IS NULL
        GROUP BY meet_id, div_id
    """)
    buckets = {"resolved_by_title": 0, "parser_rescuable": 0,
               "title_no_signal": 0, "no_blob_entry": 0}
    worst_no_signal = []
    for meet, div, n in cur.fetchall():
        key = (meet, div)
        if key not in blob:
            buckets["no_blob_entry"] += n
        elif _genderCurrent(blob[key]) in ("F", "M"):
            buckets["resolved_by_title"] += n
        elif key in rescuable:
            buckets["parser_rescuable"] += n
        else:
            buckets["title_no_signal"] += n
            worst_no_signal.append((n, meet, div, blob[key]))
    print("\n== B2. unlinked tfrrs XC rows, by gender-resolution fate ==")
    for k, v in buckets.items():
        print(f"  {k:<20} {v:>12,}")
    print("  biggest no-signal divisions (per-result pin / page-check queue):")
    for n, meet, div, name in sorted(worst_no_signal, reverse=True)[:10]:
        print(f"    {n:>6,} rows  meet/div {meet}/{div}  title={name!r}")


# ================================================================== #
# CHUNK 4 -- SECTION C: mixed-title inventory
# ================================================================== #

def _mixedInventory(blob):
    mixed = {k: v for k, v in blob.items() if _genderExtended(v) == "MIXED"}
    print(f"\n== C. mixed/coed titles: {len(mixed):,} divisions ==")
    print("  RULE: these may NEVER receive a division-wide gender (override or")
    print("  parser). Linked rows resolve via athletes table; unlinked rows are")
    print("  per-result-pin or unknown_pool. Examples:")
    for (meet, div), name in list(mixed.items())[:15]:
        print(f"    meet/div {meet}/{div}  title={name!r}")


def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        for t in ("results", "results_tf"):
            _identityCensus(cur, t)
        blob = _loadBlob(cur)
        rescuable = _titleCoverage(blob)
        _rowWeighted(cur, blob, rescuable)
        _mixedInventory(blob)
        conn.rollback()


if __name__ == "__main__":
    main()