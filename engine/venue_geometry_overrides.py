# Project: xc-predictor
# File:    engine/venue_geometry_overrides.py
# Purpose: Correct venue geometry that is WRONG AS STORED because the stored
#          value is date-blind. meets_tf_meta.track_type is a current-state
#          venue attribute stamped onto every historical meet; for venues
#          that were renovated (Clemson: flat 2003 -> banked Jan 2017) every
#          pre-renovation meet from BOTH sources carries the wrong type. This
#          module is a curated table of VERIFIED (venue, date-range) facts +
#          one pure function loaders call on geometry fields after joining.
#
#          Read-time correction, NOT a data fix: we never UPDATE scraped
#          tables (evidence stays evidence — the four-layer model). Deleting
#          an entry fully reverses it.
#
#          Rule semantics (one shape covers both cases):
#            renovated : date-ranged rule sets the historically-correct
#                        fields (usually type; Iowa also corrects length)
#            date=None : NO rule fires — a row we can't place in time can't
#                        be placed on either side of a renovation
#          Hydraulic venues (Ocean Breeze, Norton, post-2018 Michigan) get
#          NO entry: banking varies per meet, but the measured base rate
#          (3 contrary claims / 569 Ocean Breeze meets) says the Banked
#          meta is ~99.5% right — blanking it would trade ~6 mislabels for
#          hundreds of lost corrections. Per-meet-claim handling is [OPEN].
#
#          FILLING THE TABLE: location_id values and date boundaries come
#          from scripts/census_venue_renovations.py + one web verification
#          per venue (renovation announcements, the facility's own page).
#          An entry enters this table ONLY after both. Unverified suspects
#          stay in the census script, not here.

# ------------------------------------------------------------------ #
# THE CURATED TABLE
# ------------------------------------------------------------------ #
# Shape:  {location_id: {"venue": str, "rules": [rule, ...]}}
# Rule:   {"from": ISO str or None,   # inclusive lower bound (None = -inf)
#          "to":   ISO str or None,   # EXCLUSIVE upper bound (None = +inf)
#          "when": {field: value},    # ALL must match the row, e.g. the
#                                     # is_indoor gate (a campus location_id
#                                     # can cover outdoor AND indoor tracks —
#                                     # never flip the outdoor 400)
#          "set":  {field: value},    # fields to overwrite on a hit
#          "why":  str}               # the verification, in one line
# First matching rule wins; order rules most-specific first.
#
# All five entries below: location_id from census_venue_renovations
# (2026-07-04 run), boundary from the facility's own announcement, verified
# 2026-07-04. The flag census's date spans corroborate every boundary (each
# venue's Flat titles stop at or before its renovation).

OVERRIDES = {
    63253: {   # anet loc: "Clemson University" (indoor facility)
        "venue": "Clemson Indoor Track & Field Complex",
        "rules": [
            {"from": None, "to": "2017-01-07",
             "when": {"is_indoor": 1},
             "set":  {"track_type": "Flat"},
             "why":  "flat 200m Mondo from Dec 2003 build; banked 200m "
                     "grand-opened 2017-01-07 (clemsontigers.com)"},
        ],
    },
    125509: {  # anet loc: "University of Michigan" (indoor)
        "venue": "U-M Indoor Track Building (old + new)",
        "rules": [
            {"from": None, "to": "2018-01-01",
             "when": {"is_indoor": 1},
             "set":  {"track_type": "Flat"},
             "why":  "old building flat 200, final meet 2017-01-14; new "
                     "hydraulic banked 200 first event 2018-01-13 "
                     "(mgoblue.com) — post-2018 Banked meta stands"},
        ],
    },
    108522: {  # anet loc: "University of Houston" (Yeoman FH)
        "venue": "Yeoman Fieldhouse",
        "rules": [
            {"from": None, "to": "2019-01-01",
             "when": {"is_indoor": 1},
             "set":  {"track_type": "Flat"},
             "why":  "flat 200m Mondo from 1995 build; banked oval "
                     "installed 2018, first meet 2019 (uhcougars.com)"},
        ],
    },
    72643: {   # anet loc: "University of Missouri" (Hearnes FH)
        "venue": "Hearnes Center Fieldhouse",
        "rules": [
            {"from": None, "to": "2024-07-01",
             "when": {"is_indoor": 1},
             "set":  {"track_type": "Flat"},
             "why":  "flat 200m 1972-2024 (nearly ALL rows in our data); "
                     "banked Mondo installed summer 2024, first meet "
                     "2025-01-11 (mutigers.com)"},
        ],
    },
    85120: {   # anet loc: "The University of Iowa" (Rec Building)
        "venue": "Hawkeye Indoor Track / Recreation Building",
        "rules": [
            # TWO rules: the stored meta (Banked 299) is a chimera — old
            # track's length + new track's banking. Each era fixes the
            # field the meta got wrong FOR IT.
            {"from": None, "to": "2016-07-01",
             "when": {"is_indoor": 1},
             "set":  {"track_type": "Flat"},          # old flat ~300; the
                                                      # 299 length stands
             "why":  "old flat oversized track; building closed 2016-07 "
                     "for the banked install (littlevillagemag.com)"},
            {"from": "2016-07-01", "to": None,
             "when": {"is_indoor": 1},
             "set":  {"track_type": "Banked", "track_length": 200.0},
             "why":  "the 2016 Portland Worlds banked 200 installed fall "
                     "2016 — the stored 299 length is the OLD track's; "
                     "corrects rows onto the banked-200 model"},
        ],
    },
}

# ------------------------------------------------------------------ #
# THE MATCHER (three tiny predicates + one walker)
# ------------------------------------------------------------------ #

# _inRange
# Purpose:   Is a date inside a rule's [from, to) window? ISO date strings
#            order chronologically as plain strings, so no parsing needed.
# Arguments: date — ISO text 'YYYY-MM-DD' (caller guarantees not None);
#            lo, hi — the rule bounds, either may be None = unbounded.
# Output:    True when lo <= date < hi (with None bounds always passing).
def _inRange(date, lo, hi):
    if lo is not None and date < lo:    # string compare IS date compare (ISO)
        return False
    if hi is not None and date >= hi:   # 'to' is exclusive: a meet ON the
        return False                    # reopening date is post-renovation
    return True


# _whenMatches
# Purpose:   Does the row satisfy a rule's gate? Every condition must hold.
# Arguments: when — {field: required value} (e.g. {"is_indoor": 1});
#            fields — the row's geometry dict (missing keys count as None).
# Output:    True when every condition matches exactly.
def _whenMatches(when, fields):
    return all(fields.get(k) == v for k, v in when.items())


# _findRule
# Purpose:   Walk one venue's rules in order; first full match wins.
# Arguments: rules — the venue's rule list; fields — geometry dict;
#            date — ISO text or None.
# Output:    the matching rule dict, or None (no date = no match, by design:
#            an undatable row cannot be placed relative to a renovation).
def _findRule(rules, fields, date):
    if not date:
        return None
    for rule in rules:
        if (_inRange(date, rule["from"], rule["to"])
                and _whenMatches(rule["when"], fields)):
            return rule
    return None


# _applyRule
# Purpose:   Produce the corrected fields. PURE — builds a new dict, never
#            mutates the caller's (loaders reuse row dicts across steps).
# Arguments: fields — the row's geometry dict; rule — a matched rule.
# Output:    a new dict = fields with the rule's "set" entries overwriting.
def _applyRule(fields, rule):
    corrected = dict(fields)            # shallow copy = the purity guarantee
    corrected.update(rule["set"])       # overwrite only the "set" keys
    return corrected

# ------------------------------------------------------------------ #
# THE PUBLIC ENTRY
# ------------------------------------------------------------------ #

# applyVenueOverride
# Purpose:   The one call sites use: correct a row's geometry if a verified
#            venue fact applies; hand it back untouched otherwise. Cheap on
#            the miss path (one dict lookup), so callers apply it to EVERY
#            row unconditionally — no caller-side venue logic.
# Arguments: fields      — {"track_type","track_length","is_indoor", ...}
#                          as joined from meets_tf_meta / tfrrs_meet_geometry
#                          (extra keys tolerated and preserved);
#            location_id — the venue key from the same join (None = no
#                          override possible, returned as-is);
#            date        — the MEET's date, ISO text or None.
# Output:    (fields', hit) — the (possibly corrected) dict and the matched
#            rule or None, so loaders can LEDGER override hits per venue.
def applyVenueOverride(fields, location_id, date):
    entry = OVERRIDES.get(location_id)  # miss path: one dict lookup, done
    if entry is None:
        return fields, None
    rule = _findRule(entry["rules"], fields, date)
    if rule is None:
        return fields, None
    return _applyRule(fields, rule), rule

# ------------------------------------------------------------------ #
# CLI: THE IMPACT CENSUS  (python engine/venue_geometry_overrides.py)
# ------------------------------------------------------------------ #
# Everything ABOVE this line is a pure library: no imports, no DB, so
# loaders import it for free. Everything BELOW runs only as a script.
# The census fetches each override venue's real meets and pushes them
# through applyVenueOverride ITSELF — so the numbers it prints are, by
# construction, what the override will actually do (the matcher exercised,
# never re-stated in SQL — mirror drift can't lie here). Read-only.

# _loadVenueMeets
# Purpose:   Every anet meet at one override venue, carrying exactly the
#            fields the matcher reads. A venue is O(100) meets — fetchall
#            is fine. (tfrrs stamped meets at these venues are also
#            corrected at read time once the loaders are wired; censusing
#            them here needs a meets_tfrrs date join — [OPEN], small.)
# Arguments: conn — open connection; loc_id — an OVERRIDES key.
# Output:    list of (meet_id, meet_date, fields-dict).
def _loadVenueMeets(conn, loc_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, meet_date, track_type, track_length, is_indoor
            FROM meets_tf_meta
            WHERE source = 'anet' AND location_id = %s
        """, (loc_id,))
        return [(mid, d, {"track_type": tt, "track_length": tl,
                          "is_indoor": ind})
                for mid, d, tt, tl, ind in cur]


# _tallyEntry
# Purpose:   One venue's meets through the REAL matcher: which rule (if
#            any) claims each meet, plus a first-seen before/after sample
#            per rule and a per-reason miss count.
# Arguments: loc_id — the OVERRIDES key; meets — _loadVenueMeets output.
# Output:    (hits, misses, samples)
#            hits    — {rule_index: [meet_id, ...]}
#            misses  — {"no_date": n, "no_rule": n}  (no_rule = out of
#                      every window or failed a gate — post-boundary meets
#                      land here, which is CORRECT, not a problem)
#            samples — {rule_index: (meet_id, date, before, after)}.
def _tallyEntry(loc_id, meets):
    rules = OVERRIDES[loc_id]["rules"]
    hits, samples = {}, {}
    misses = {"no_date": 0, "no_rule": 0}
    for mid, date, fields in meets:
        corrected, rule = applyVenueOverride(fields, loc_id, date)
        if rule is None:
            misses["no_date" if not date else "no_rule"] += 1
            continue
        idx = rules.index(rule)         # dicts compare by content; the
                                        # matched rule IS one of these
        hits.setdefault(idx, []).append(mid)
        if idx not in samples:          # keep the first hit as the sample
            samples[idx] = (mid, date, fields, corrected)
    return hits, misses, samples


# _countResults
# Purpose:   The impact in ROWS: how many anet results sit behind one
#            rule's corrected meets. = ANY(array) instead of IN (tuple):
#            psycopg2 adapts a Python list to a SQL array, and — unlike
#            IN () — an EMPTY list is valid SQL that matches nothing.
# Arguments: conn; meet_ids — list of corrected meet ids for one rule.
# Output:    int row count.
def _countResults(conn, meet_ids):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM results_tf
            WHERE source = 'anet' AND meet_id = ANY(%s)
        """, (list(meet_ids),))
        return cur.fetchone()[0]


# _reportEntry
# Purpose:   One venue's block: per rule — window, correction, meets,
#            result rows, one before/after card; then the miss lines.
# Arguments: loc_id — the entry key; hits/misses/samples — _tallyEntry
#            output; result_counts — {rule_index: n} (empty if skipped).
def _reportEntry(loc_id, hits, misses, samples, result_counts):
    entry = OVERRIDES[loc_id]
    print(f"\n== {entry['venue']}  (loc {loc_id}) ==")
    for idx, rule in enumerate(entry["rules"]):
        mids = hits.get(idx, [])
        span = f"{rule['from'] or '-inf'}..{rule['to'] or '+inf'}"
        print(f"  rule {idx} [{span}]  set={rule['set']}")
        print(f"    meets corrected : {len(mids):,}")
        if idx in result_counts:
            print(f"    results touched : {result_counts[idx]:,}")
        if idx in samples:
            mid, date, before, after = samples[idx]
            print(f"    sample meet {mid} ({date}):")
            print(f"      before {before}")
            print(f"      after  {after}")
    print(f"  untouched meets: post-boundary/gated={misses['no_rule']:,}"
          f"  undatable={misses['no_date']:,}")


def main():
    # CLI-only imports: keeping them INSIDE main is what keeps the library
    # half import-free — a loader importing applyVenueOverride must never
    # drag in argparse or a DB connection it didn't ask for.
    import sys
    import argparse
    sys.path.insert(0, "scripts")       # database.py lives there
    from database import getConn

    parser = argparse.ArgumentParser(
        description="Impact census for the venue geometry overrides "
                    "(read-only; exercises the real matcher)")
    parser.add_argument("--skip-results", action="store_true",
                        help="skip the per-rule results_tf row counts "
                             "(faster; meets counts only)")
    args = parser.parse_args()

    with getConn() as conn:
        for loc_id in OVERRIDES:
            meets = _loadVenueMeets(conn, loc_id)
            hits, misses, samples = _tallyEntry(loc_id, meets)
            counts = {}
            if not args.skip_results:
                for idx, mids in hits.items():
                    counts[idx] = _countResults(conn, mids)
            _reportEntry(loc_id, hits, misses, samples, counts)


if __name__ == "__main__":
    main()