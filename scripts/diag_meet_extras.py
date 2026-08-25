"""
diag_meet_extras.py -- what does one TF meet's sidecar data actually hold?

Answers, for a meet id:
  1. Do the meet_extras blobs (eventTypes, relayLegs, teams) exist, what
     shape are their entries, and what does the catalog NAME each of the
     meet's nameless events?
  2. Are the per-result capture columns populated (round, score, heat,
     event_type_id) -- i.e. can rounds and official points come from the
     data instead of from name parsing?
  3. Is there any sign of the multis (pent/hept/dec) among the events?

Usage:  python scripts/diag_meet_extras.py <meet_id>
"""

import json
import sys

sys.path.insert(0, "scripts")
from database import getConn   # noqa: E402


def brief(obj, n=400):
    s = json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + " ..."


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    meet_id = int(sys.argv[1])

    cm = getConn()
    conn = cm.__enter__()
    cur = conn.cursor()

    print(f"=== meet_extras for TF meet {meet_id} ===")
    cur.execute("""
        SELECT teams_json, event_types_json, relay_legs_json
        FROM meet_extras WHERE meet_id = %s AND sport = 'TF'
    """, (meet_id,))
    row = cur.fetchone()
    if row is None:
        print("NO meet_extras row for this meet (older scrape, or XC id).")
        blobs = (None, None, None)
    else:
        blobs = row
    for label, blob in zip(("teams", "eventTypes", "relayLegs"), blobs):
        if blob is None:
            print(f"\n-- {label}: NULL")
            continue
        kind = type(blob).__name__
        n = len(blob) if hasattr(blob, "__len__") else "?"
        print(f"\n-- {label}: {kind}, {n} entries")
        entries = blob if isinstance(blob, list) else [blob]
        for e in entries[:2]:
            print("   ", brief(e))

    print("\n=== per-result capture columns ===")
    cur.execute("""
        SELECT count(*)                                   AS n,
               count(round)                               AS with_round,
               count(score)    FILTER (WHERE score > 0)   AS with_score,
               count(heat)                                AS with_heat,
               count(event_type_id)                       AS with_etid
        FROM results_tf WHERE meet_id = %s
    """, (meet_id,))
    n, w_round, w_score, w_heat, w_etid = cur.fetchone()
    print(f"results: {n} | round: {w_round} | score>0: {w_score} "
          f"| heat: {w_heat} | event_type_id: {w_etid}")

    cur.execute("""
        SELECT DISTINCT round FROM results_tf
        WHERE meet_id = %s AND round IS NOT NULL LIMIT 20
    """, (meet_id,))
    print("distinct round values:", [r[0] for r in cur.fetchall()])

    print("\n=== nameless events -> catalog names ===")
    cur.execute("""
        SELECT r.div_id, r.event_id, r.event_type_id,
               count(*) AS n,
               count(*) FILTER (WHERE r.is_relay = 1)  AS relays,
               count(*) FILTER (WHERE r.is_field = 1)  AS fields
        FROM results_tf r
        JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                        AND m.event_id = r.event_id
        WHERE r.meet_id = %s
          AND NULLIF(TRIM(COALESCE(m.event_short, '')), '') IS NULL
        GROUP BY r.div_id, r.event_id, r.event_type_id
        ORDER BY r.event_id
    """, (meet_id,))
    nameless = cur.fetchall()
    catalog = {}
    et_blob = blobs[1]
    if isinstance(et_blob, list):
        for e in et_blob:
            if isinstance(e, dict):
                tid = next((e[k] for k in ("ID", "Id", "id", "EventTypeID")
                            if e.get(k) is not None), None)
                if tid is not None:
                    catalog[tid] = e
    if not nameless:
        print("(no nameless events at this meet)")
    for div_id, event_id, etid, n, relays, fields in nameless:
        entry = catalog.get(etid)
        print(f"Event {event_id} (div {div_id}, etid {etid}, {n} results, "
              f"relays {relays}, fields {fields}):")
        print("   ", brief(entry) if entry else "NOT IN CATALOG")

    print("\n=== multi hunt (pent/hept/dec anywhere?) ===")
    cur.execute("""
        SELECT DISTINCT m.event_short FROM meets_tf m
        WHERE m.meet_id = %s AND m.event_short IS NOT NULL
    """, (meet_id,))
    shorts = [r[0] for r in cur.fetchall()]
    hits = [s for s in shorts
            if any(w in s.lower() for w in ("pent", "hept", "dec", "multi"))]
    print("event_short hits:", hits or "none")
    if isinstance(et_blob, list):
        cat_hits = [brief(e, 200) for e in et_blob if isinstance(e, dict)
                    and any(w in json.dumps(e, default=str).lower()
                            for w in ("pent", "hept", "decath", "multi"))]
        print("catalog hits:", *cat_hits or ["none"], sep="\n  " if cat_hits else " ")

    cm.__exit__(None, None, None)


if __name__ == "__main__":
    main()
