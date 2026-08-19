# Project: xc-predictor
# File:    scripts/show_nulled.py
# Purpose: READ-ONLY, no DB, no network. Reads refix_gps_cache.json and shows
#          which queries MISSED (became nulls), split into "looks like a real
#          address" (a failed lookup, maybe recoverable) vs "junk name"
#          (correctly nulled). Tells you whether the 341 nulls are mostly bad
#          data or mostly geocoder failures worth retrying.
# USAGE:  python scripts/show_nulled.py
# ============================================================================
import json, os, re, sys

_CACHE = os.path.join("scripts", "refix_gps_cache.json")

# signals that a query names a real place (street number, or a place noun)
_PLACE_WORDS = ("stadium", "park", "field", "school", "college", "university",
                "track", "high", "complex", "center", "centre", "athletic",
                "fairgrounds", "course", "club", "road", "street", "ave",
                "avenue", "drive", "lane", "blvd", "hwy", "route")
_JUNK = ("tbd", "tba", "virtual", "various", "unknown", "n/a", "none",
         "last chance", "invitational", "conference", "regional", "district")


def _looksReal(q):
    ql = q.lower()
    if any(j in ql for j in _JUNK):
        return False
    if re.search(r"\d{2,}", q):                    # a street number / postal
        return True
    if any(w in ql for w in _PLACE_WORDS):         # a place noun
        return True
    # 3+ comma parts (name, city, state, USA) usually = a real address
    return q.count(",") >= 3


def main():
    if not os.path.exists(_CACHE):
        sys.exit(f"  {_CACHE} not found -- run refix_gps.py --apply first")
    cache = json.load(open(_CACHE, encoding="utf-8"))
    hits = {q: v for q, v in cache.items() if v}
    miss = [q for q, v in cache.items() if not v]
    print(f"  cache: {len(cache):,} queries | {len(hits):,} hit | {len(miss):,} miss")

    real = sorted(q for q in miss if _looksReal(q))
    junk = sorted(q for q in miss if not _looksReal(q))
    print(f"\n  of {len(miss)} misses (these are what got NULLED for no-geocode):")
    print(f"    look like REAL addresses (recoverable?): {len(real)}")
    print(f"    look like JUNK names (correctly null):    {len(junk)}")

    print("\n  --- REAL-looking misses (first 40) ---")
    for q in real[:40]:
        print(f"    {q}")
    print("\n  --- JUNK-looking misses (first 15) ---")
    for q in junk[:15]:
        print(f"    {q}")

    # write the real ones out so we can retry just those
    out = os.path.join("scripts", "nulled_real_addresses.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(real))
    print(f"\n  wrote {out} ({len(real)} queries) -- the retry list")


if __name__ == "__main__":
    main()