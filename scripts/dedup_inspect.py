#!/usr/bin/env python3
"""inspect_entity_links.py - read-only. Look at what build_entity_links produced
BEFORE trusting it or merging on it. Three views:

  1. Distribution: how many athlete/meet links exist at each confidence level.
     Confidence = how many confirmed meets agree on the pairing. This is what you
     pick a merge threshold from - high-confidence links are safe, confidence-1
     links are coin flips.
  2. Samples at each confidence level, with the actual names/ids, so you can
     eyeball whether the matches look real.
  3. Known-pair check: did the validated anet 271870 <-> tfrrs 27301 meet show
     up as a meet link? If yes with a high shared-count, the pipeline works.

Nothing here writes. Run it, read it, THEN decide a threshold.
"""

import sys
sys.path.insert(0, "scripts")
from database import getConn

# the validated pair, to confirm the pipeline caught it
KNOWN_ANET_MEET = 271870
KNOWN_TFRRS_MEET = 27301


def _query(sql, params=()):
    """Read-only query -> list of tuples."""
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _tableExists():
    """True if entity_links exists (build_entity_links was run)."""
    return _query("""
        SELECT 1 FROM information_schema.tables WHERE table_name = 'entity_links'
    """) != []


def _distribution(entity_type):
    """Print link count per confidence level for one entity type.

    Reading it: a big pile at confidence 1 and a thin tail at high confidence is
    normal. The high-confidence rows are your safe-to-merge set; where you draw
    the line is the threshold decision.
    """
    print(f"  {entity_type} links by confidence (count agreeing meets):")
    rows = _query("""
        SELECT sport, confidence, count(*)
        FROM entity_links WHERE entity_type = %s
        GROUP BY sport, confidence ORDER BY sport, confidence DESC
    """, (entity_type,))
    if not rows:
        print("    (none)")
    for sport, conf, n in rows:
        print(f"    {sport}  conf={conf:>3}  {n:>10,}")


def _samples(entity_type, confidence, limit=8):
    """Print a few links at a given confidence, resolving ids to names.

    For athletes: shows the anet name (via athletes) and tfrrs name (via
    athletes_tfrrs if present) so you can see if they're really the same person.
    For meets: shows both meet names from meets / meets_tfrrs.
    """
    print(f"  sample {entity_type} links at confidence {confidence}:")
    rows = _query("""
        SELECT sport, anet_id, tfrrs_id FROM entity_links
        WHERE entity_type = %s AND confidence = %s LIMIT %s
    """, (entity_type, confidence, limit))
    if not rows:
        print("    (none at this level)")
        return
    for sport, anet_id, tfrrs_id in rows:
        if entity_type == "athlete":
            anet_name  = _anetAthleteName(anet_id)
            tfrrs_name = _tfrrsAthleteName(tfrrs_id)
            print(f"    {sport}  anet#{anet_id} {anet_name!r}  <->  "
                  f"tfrrs#{tfrrs_id} {tfrrs_name!r}")
        else:
            print(f"    {sport}  anet meet {anet_id}  <->  tfrrs meet {tfrrs_id}")


def _anetAthleteName(athlete_id):
    """Best-effort anet name for an athlete_id (may have several schools)."""
    rows = _query("""
        SELECT DISTINCT first_name, last_name FROM athletes
        WHERE athlete_id = %s LIMIT 1
    """, (athlete_id,))
    if not rows:
        return "?"
    first, last = rows[0]
    return f"{first or ''} {last or ''}".strip() or "?"


def _tfrrsAthleteName(native_id):
    """tfrrs name from athletes_tfrrs if it exists, else from a result row."""
    try:
        rows = _query("SELECT athlete_name FROM athletes_tfrrs WHERE native_id=%s LIMIT 1",
                      (native_id,))
        if rows:
            return rows[0][0]
    except Exception:
        pass
    rows = _query("""
        SELECT athlete_name FROM results_tf WHERE native_id=%s AND source='tfrrs' LIMIT 1
    """, (native_id,))
    return rows[0][0] if rows else "?"


def _knownPairCheck():
    """Did the validated meet pair get linked? The pipeline's smoke test."""
    print(f"  known-pair check (anet {KNOWN_ANET_MEET} <-> tfrrs {KNOWN_TFRRS_MEET}):")
    rows = _query("""
        SELECT sport, confidence FROM entity_links
        WHERE entity_type = 'meet' AND anet_id = %s AND tfrrs_id = %s
    """, (KNOWN_ANET_MEET, KNOWN_TFRRS_MEET))
    if rows:
        sport, conf = rows[0]
        print(f"    FOUND - {sport}, shared finishers = {conf}.  Pipeline works.")
    else:
        print("    NOT found - either below MIN_SHARED_RESULTS, a name/time format")
        print("    gap, or the meet ids differ from what we assumed. Worth a look.")


def main():
    print("=== entity_links inspector (read-only) ===\n")
    if not _tableExists():
        print("  entity_links doesn't exist yet - run build_entity_links.py first.")
        return

    _distribution("meet")
    print()
    _distribution("athlete")
    print()
    _knownPairCheck()
    print()
    # sample the top and bottom of the athlete confidence range to compare quality
    _samples("athlete", confidence=1)        # the shakiest links
    print()
    high = _query("SELECT max(confidence) FROM entity_links WHERE entity_type='athlete'")
    top = high[0][0] if high and high[0][0] else 1
    if top > 1:
        _samples("athlete", confidence=top)  # the strongest links
    print("\n=== done. Pick a merge threshold from the distribution + samples. ===")


if __name__ == "__main__":
    main()