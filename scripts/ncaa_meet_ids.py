#!/usr/bin/env python3
"""
ncaa_meet_ids.py -- the TFRRS ids of a season's NCAA Division I and III cross
country regionals and championships, from an index file of TFRRS's newest
meets. READ-ONLY (a CSV in, two id lists out).

    /srv/venv/bin/python -m sources.tfrrs_hs index --host www.tfrrs.org --pages 10 \\
        --out /tmp/tfrrs_latest.csv
    /srv/venv/bin/python scripts/ncaa_meet_ids.py /tmp/tfrrs_latest.csv --year 2026

★ WHY (owner, 2026-11-23: "scrape only the regional and national meets, just
  so we can have full confidence"). The holdout test scrapes exactly these
  (TFRRS_ONLY_MEETS in tfrrs/driver/launch_tfrrs.py): the regionals first,
  the ratings rebuilt on them, then the championships, never rated. The ids
  come from TFRRS's own results list -- the index walk the nightly already
  makes (sources.tfrrs_hs), one page a request, ten seconds apart.

Prints each meet found with its date, then REGIONALS=... and NATIONALS=...
lines to paste into TFRRS_ONLY_MEETS. Says which regions it did not find
(9 Division I, 10 Division III).
"""
import argparse
import csv
import re
import sys

DI = re.compile(r"division\s+i(?![iv])|\bd-?i\b(?!i)", re.I)
DIII = re.compile(r"division\s+iii|\bd-?iii\b", re.I)
REGIONS = {"DI": 9, "DIII": 10}


def classify(name):
    """('DI' | 'DIII', 'regional' | 'national') for an NCAA DI or DIII cross
    country regional or championship name, else None. Pure."""
    n = name or ""
    if not re.search(r"\bncaa\b", n, re.I) or not re.search(r"champ", n, re.I):
        return None
    div = "DIII" if DIII.search(n) else "DI" if DI.search(n) else None
    if div is None:
        return None
    kind = "regional" if re.search(r"region", n, re.I) else "national"
    if kind == "national" and re.search(r"pre-?nat|pre-?champ", n, re.I):
        return None
    return div, kind


def pick(rows, year):
    """{(div, kind): [(meet_id, date, name)]} from index rows (dicts with
    meet_id, sport, date, name) of cross country in `year`. Pure."""
    out = {}
    for r in rows:
        if (r.get("sport") or "").upper() != "XC" or not str(r.get("date") or "").startswith(str(year)):
            continue
        c = classify(r.get("name"))
        if c:
            out.setdefault(c, []).append((int(r["meet_id"]), r["date"], r["name"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("index", help="CSV from `python -m sources.tfrrs_hs index --out`")
    ap.add_argument("--year", type=int, required=True)
    a = ap.parse_args(argv)
    with open(a.index, newline="", encoding="utf-8") as f:
        got = pick(list(csv.DictReader(f)), a.year)
    reg, nat = [], []
    for div in ("DI", "DIII"):
        for kind in ("regional", "national"):
            meets = sorted(got.get((div, kind)) or [], key=lambda m: (m[1], m[2]))
            print(f"\n{div} {kind}s: {len(meets)}")
            for mid, day, name in meets:
                print(f"  {mid:>6}  {day}  {name}")
            (reg if kind == "regional" else nat).extend(m[0] for m in meets)
        n = len(got.get((div, "regional")) or [])
        if n < REGIONS[div]:
            print(f"  ! {REGIONS[div] - n} {div} regional(s) not in the index -- "
                  f"more --pages, or look them up on tfrrs.org")
        if not got.get((div, "national")):
            print(f"  ! no {div} championship in the index")
    print(f"\nREGIONALS={','.join(map(str, reg))}")
    print(f"NATIONALS={','.join(map(str, nat))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
