# Project: xc-predictor
# File:    scripts/fix_anet_gps_manual.py
# Purpose: The anet no-gps set collapsed to ~3 venues (44x AFNORTH overseas +
#          an MN and a YT singleton). Too small for a geocoding pipeline -- this
#          stamps hand-verified coordinates directly. Dry-run default.
#
# You must FILL IN the coordinates below (look each up once). AFNORTH is the NATO
# base at Brunssum, Netherlands; the MN/YT rows print so you can identify them.
#
# location_id handling mirrors the geocoders:
#   * a venue that already has a location_id -> write gps to it (no mint)
#   * a venue with NO location_id           -> mint at 800,000,000+ (anet-geocode
#     range, distinct from tfrrs's 900M)
#
# USAGE
#   python scripts/fix_anet_gps_manual.py            # show the venues + plan
#   python scripts/fix_anet_gps_manual.py --apply    # write (after filling coords)
# ============================================================================

import argparse
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool

_MINT_BASE = 800_000_000

# key = normalized course_name ; value = (lat, lon) or None until you fill it.
# Leave a value None to SKIP that venue (it stays unresolved).
#
# ★★ These are BASE/INSTALLATION or VENUE CENTERS (not the exact XC start line),
#    web-verified where a source is cited. A base can span a few km, so treat as
#    approximate. Confidence: [H] verified coordinate from Wikidata/official,
#    [M] identified venue, coordinate approximate, [L] region known, point rough.
_COORDS = {
    # --- Germany / Europe (DoDEA) ---
    "afnorth home course": (50.9469, 5.9700),                 # [M] AFNORTH Intl Sch, Brunssum NL
    "patch american hs home course": (48.7364, 9.0811),       # [H] Patch Barracks, Vaihingen (Wikidata)
    "kaiserslsautern hs home course": (49.4265, 7.6975),      # [H] Vogelweh, Kaiserslautern (Wikipedia)
    "bitburg hs home course": (49.9453, 6.5650),              # [H] Bitburg HS (Wikidata 49 56 43N 6 33 54E)
    "heidelberg american home course": (49.3988, 8.6724),     # [M] Heidelberg
    "ansbach hs new course 2008": (49.3000, 10.5700),         # [M] Ansbach
    "schweinfurt home course": (50.0490, 10.2210),            # [M] Schweinfurt
    "oberursel": (50.2010, 8.5780),                           # [M] Oberursel
    "tompkins barracks": (49.3830, 8.5700),                   # [L] Schwetzingen area
    "st john s belgium home course": (50.6900, 4.2100),      # [L] SHAPE/Waterloo area
    "gw home course": None,                                   # [?] "GW" unidentified

    # --- Okinawa / Japan (DoDEA Pacific) ---
    "kadena grass course": (26.3517, 127.7694),               # [H] Kadena AB (Wikidata 26 21 6N 127 46 10E)
    "kishaba housing course": (26.3300, 127.7900),            # [M] Kubasaki/Kishaba, Kitanakagusuku
    "kishaba housing area new 2011 course": (26.3300, 127.7900),  # [M] same area
    "futenma habu trail course": (26.2740, 127.7560),         # [M] MCAS Futenma
    "zampa course": (26.4370, 127.7150),                      # [M] Cape Zanpa, Yomitan
    "zanpa": (26.4370, 127.7150),                             # [M] Cape Zanpa, Yomitan
    "okinawa": (26.3517, 127.7694),                           # [L] Okinawa races cluster at Kadena
    "otaka ryokuchi koen": (35.1050, 136.9450),               # [M] Nagoya
    "sayama ko west trail": (35.7660, 139.4100),              # [M] Lake Sayama, Tokyo
    "kibogaoka cultural park": (35.0450, 136.1150),          # [M] Ryuo/Yasu, Shiga (Kibogaoka Bunka Koen)
    "momoishi beach park": (40.6600, 141.4300),              # [M] Oirase, Aomori (near Misawa AB)
    "shio ashiya beach sogo undo koen ashiya": (34.7067, 135.3088),  # [H] Ashiya Sogo Park, Hyogo (2markers)
    "kelly field": (26.3517, 127.7694),                      # [M] Kadena AB course cluster
    "sis tancheon course": (37.4400, 127.1200),               # [L] Seoul (Tancheon)

    # --- Korea ---
    "daegu american course": (35.8450, 128.5700),             # [M] Camp Walker, Daegu
    "camp walker": (35.8450, 128.5700),                       # [M] Camp Walker, Daegu
    "camp george course": (35.8450, 128.5700),                # [L] Daegu area
    "korean int l school home course": (37.4570, 127.1280),   # [M] Seoul Intl Sch, Seongnam
    "nc kinnick ikego race": (35.2900, 139.6100),            # [L] Ikego, Zushi Japan

    # --- USA ---
    "eagan high school": (44.8041, -93.1500),                 # [H] Eagan, MN (4185 Braddock Trail)

    # --- Guam ---
    "guam high course": (13.4830, 144.7960),                  # [M] Guam
    "tiyan course": (13.4830, 144.7970),                      # [M] Tiyan, Guam
    "nd ipan beach": (13.3900, 144.7600),                     # [L] Ipan, Talofofo

    # --- China ---
    "shanghai links": (31.2300, 121.7570),                    # [H] Shanghai Links GC, Pudong (golfcourse.wiki)
    "ics stadium course": (31.2300, 121.7570),                # [L] Shanghai intl (SAS Pudong area)
    "ics non stadium course": (31.2300, 121.7570),            # [L] Shanghai intl (SAS Pudong area)
    "bianjing park": (34.7970, 114.3070),                     # [M] Kaifeng, Henan (Bianjing = old name)

    # --- clearly unlocatable: leave None ---
    "tbd": None,
    "edgren tbd": None,
    "iwakuni unspecified location": None,
    "course for far east 1994": None,
    "harvest course": None,
    "fd course": None,
    "risner": (26.3517, 127.7694),                           # [M] Kadena AB (Risner/McDonald Stadium)
    "jfk s modified course": None,
}


def _norm(name):
    import re
    if not name:
        return None
    s = re.sub(r"[^\w\s]", " ", name.strip().lower())
    return re.sub(r"\s+", " ", s).strip() or None


def _run(do_write):
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT div_id, course_name, state, location_id
                FROM meets
                WHERE gps_lat IS NULL AND course_name IS NOT NULL
            """)
            rows = cur.fetchall()
            conn.rollback()

        # group by normalized name -> div_ids + any existing location_id
        venues = {}
        for div_id, name, state, loc in rows:
            k = _norm(name)
            v = venues.setdefault(k, {"name": name, "state": state,
                                      "divs": [], "loc": None})
            v["divs"].append(div_id)
            if loc is not None and v["loc"] is None:
                v["loc"] = loc

        print(f"  {len(rows)} no-gps rows -> {len(venues)} venues:")
        for k, v in venues.items():
            have = _COORDS.get(k)
            print(f"    {v['name']!r} ({v['state']}) divs={len(v['divs'])} "
                  f"loc={v['loc']}  coords={'SET' if have else 'MISSING -> fill _COORDS'}")

        no_loc = sorted(k for k, v in venues.items()
                        if v["loc"] is None and _COORDS.get(k))
        mint = {k: _MINT_BASE + i for i, k in enumerate(no_loc)}

        if not do_write:
            print("\n  [dry-run] fill any MISSING coords in _COORDS, then --apply")
            return

        with conn.cursor() as cur:
            for k, v in venues.items():
                c = _COORDS.get(k)
                if not c:
                    continue
                if v["loc"] is not None:
                    cur.execute("""UPDATE meets SET gps_lat=%s, gps_long=%s
                                   WHERE div_id = ANY(%s) AND gps_lat IS NULL""",
                                (c[0], c[1], v["divs"]))
                else:
                    cur.execute("""UPDATE meets
                                   SET gps_lat=%s, gps_long=%s, location_id=%s
                                   WHERE div_id = ANY(%s) AND gps_lat IS NULL""",
                                (c[0], c[1], mint[k], v["divs"]))
        conn.commit()
        print("  applied.")


def main():
    ap = argparse.ArgumentParser(description="Hand-stamp anet no-gps venues.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    initPool()
    _run(args.apply)


if __name__ == "__main__":
    main()