# Project: xc-predictor
# File:    scripts/diag_geocode_misses.py
# Purpose: READ-ONLY. Explain why geocoded venues failed. Reads the cache
#          (geocode_cache.json) and the venue list (geocode_venues.csv) the
#          geocoder already wrote -- no DB, no network. Buckets the misses so you
#          can see whether they are fixable (bad address parsing -> Census could
#          get them) or genuinely unfindable (junk / too-vague strings).
#
# USAGE
#   python scripts/diag_geocode_misses.py
#   python scripts/diag_geocode_misses.py --show 40      # more samples per bucket
#   python scripts/diag_geocode_misses.py --out scripts  # where the files live
# ============================================================================

import argparse
import csv
import json
import os
import re
import sys


# ================================================================== #
# CHUNK 1 -- LOAD THE ARTIFACTS THE GEOCODER WROTE
# ================================================================== #

def _load(out_dir):
    """Read venues CSV + cache. Returns (venues_by_key, cache) or exits."""
    vpath = os.path.join(out_dir, "geocode_venues.csv")
    cpath = os.path.join(out_dir, "geocode_cache.json")
    if not os.path.exists(vpath):
        sys.exit(f"missing {vpath} -- run 'collect' first")
    if not os.path.exists(cpath):
        sys.exit(f"missing {cpath} -- run 'geocode' first")
    with open(vpath, encoding="utf-8") as f:
        venues = {row["key"]: row for row in csv.DictReader(f)}
    with open(cpath, encoding="utf-8") as f:
        cache = json.load(f)
    return venues, cache


# ================================================================== #
# CHUNK 2 -- CLASSIFY A MISS
# ================================================================== #

# _classify
# Purpose : bucket ONE missed venue by what its raw string looks like, so the
#           misses split into actionable groups instead of one opaque pile.
#   no-state-parsed : geocoder had no usable state -> weak query (fixable: better
#                     state recovery, or accept Nominatim-only)
#   has-address     : the raw has a street number + a state -> Census SHOULD have
#                     resolved it; a miss here means the parser mangled it (the
#                     highest-value fixable bucket)
#   name-with-state : a real venue name + state but no street -> Nominatim
#                     territory; misses are hard but sometimes recoverable
#   sparse          : very short / few letters -> probably unfindable
#   junk            : TBD/TBA/blank-ish -> never findable
def _classify(row):
    raw = (row.get("raw") or "").strip()
    state = (row.get("state") or "").strip()
    low = raw.lower()

    if not raw or re.fullmatch(r"(tbd|tba|n/?a|unknown|home|course|-|\.)*", low):
        return "junk"

    letters = len(re.sub(r"[^a-z]", "", low))
    has_street = bool(re.search(r"\d+\s+[a-z]", low))
    has_zip = bool(re.search(r"\b\d{5}\b", raw))
    has_state = bool(state) or bool(re.search(r",\s*[a-z]{2}\b", low))

    if letters < 5:
        return "sparse"
    if has_street and (has_state or has_zip):
        return "has-address"          # Census should have gotten this
    if not has_state:
        return "no-state-parsed"
    return "name-with-state"


# ================================================================== #
# CHUNK 3 -- REPORT
# ================================================================== #

def _report(venues, cache, show):
    placed, missed, untried = [], [], []
    for key, row in venues.items():
        if row.get("empty") == "1":
            continue                       # empties are expected null, skip
        if key not in cache:
            untried.append(row)            # never attempted (errored mid-run)
        elif cache[key]:
            placed.append(row)             # has coords
        else:
            missed.append(row)             # cached null = tried, not found

    print(f"\nGEOCODE OUTCOME (excluding empties)")
    print(f"  placed  : {len(placed)}")
    print(f"  missed  : {len(missed)}  (tried, no result)")
    print(f"  untried : {len(untried)} (never attempted -- errored or interrupted)")

    # bucket the misses
    buckets = {}
    for row in missed:
        buckets.setdefault(_classify(row), []).append(row)

    print(f"\nWHY THE {len(missed)} MISSES FAILED")
    order = ["has-address", "name-with-state", "no-state-parsed", "sparse", "junk"]
    hint = {
        "has-address":    "Census SHOULD resolve -> parser mangled it (FIXABLE)",
        "name-with-state":"named venue, Nominatim territory (sometimes fixable)",
        "no-state-parsed":"weak query, no state (fixable: state recovery)",
        "sparse":         "too little text (mostly unfindable)",
        "junk":           "TBD/blank-ish (never findable)",
    }
    for b in order:
        rows = buckets.get(b, [])
        if rows:
            print(f"  {b:<16} {len(rows):>4}  -- {hint[b]}")

    # show samples of the two most actionable buckets
    for b in ("has-address", "no-state-parsed"):
        rows = buckets.get(b, [])
        if not rows:
            continue
        print(f"\n  sample '{b}' (up to {show}) -- raw string | parsed street/city/state/zip:")
        for row in rows[:show]:
            print(f"    {row.get('raw','')[:52]!r}")
            print(f"       -> street={row.get('street','')!r} "
                  f"city={row.get('city','')!r} state={row.get('state','')!r} "
                  f"zip={row.get('zip','')!r}")

    if untried:
        print(f"\n  {len(untried)} UNTRIED venues -- re-running 'geocode' will attempt"
              f" these (they errored, e.g. a transient Nominatim timeout).")
        for row in untried[:show]:
            print(f"    {row.get('raw','')[:60]!r}")


# ================================================================== #
# CHUNK 4 -- CLI
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Explain why tfrrs venues weren't geocoded. Read-only.")
    ap.add_argument("--out", default="scripts")
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args()
    venues, cache = _load(args.out)
    _report(venues, cache, args.show)


if __name__ == "__main__":
    main()