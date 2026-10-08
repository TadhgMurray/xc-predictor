#!/usr/bin/env python3
"""
which_drop.py -- which block of engine/corrections.py drops a result, and why.

    /srv/venv/bin/python scripts/which_drop.py --person 32309981
    /srv/venv/bin/python scripts/which_drop.py --person 32309981 --sport TF
    /srv/venv/bin/python scripts/which_drop.py 123456 234567

★ WHY (owner, 2026-10-08): Soheib Dissa's 2025-10-18 college 8k was
  unrated -- why_unrated --replay said "SKIP: manual_drop" on the anet row
  and "dedup_twin" on the tfrrs copy, so one entry in _RESULT_DROP killed
  both. corrections.py is server-only (gitignored) and append-structured:
  hand-curated sets first, then generated blocks (triage, twin, amnesty
  pardons), each headed by its own comment. This prints, per dropped id, the
  line that names it and the comment block above it -- the block that
  convicted it -- and whether a later block pardoned it.

! READ ONLY. It reads corrections.py as text and, with --person, the
  person's result ids from the database.
"""
import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
PATH = os.path.join(_ROOT, "engine", "corrections.py")


def _header(lines, i):
    """(name assigned, comment block above it) for the set holding line i:
    walk up to the assignment, skip blank lines, take the comments."""
    j = i
    while j > 0 and not re.match(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*=", lines[j]):
        j -= 1
    name = lines[j].split("=")[0].strip()
    k = j - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    head = []
    while k >= 0 and lines[k].lstrip().startswith("#"):
        head.insert(0, lines[k].rstrip())
        k -= 1
    return name, head[-8:]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", type=int)
    ap.add_argument("--person", type=int)
    ap.add_argument("--sport", default="XC", choices=("XC", "TF"))
    a = ap.parse_args()

    from corrections import _RESULT_DROP_BY_SPORT
    live = _RESULT_DROP_BY_SPORT[a.sport]
    ids = list(a.ids)
    if a.person:
        from database import getConn
        table = "results" if a.sport == "XC" else "results_tf"
        with getConn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT result_id, date, school FROM {table} "
                        f"WHERE person_id = %s ORDER BY date", (a.person,))
            rows = cur.fetchall()
            conn.rollback()
        for rid, d, sch in rows:
            if rid in live:
                print(f"  {d}  {sch!r}  result {rid}: DROPPED")
                ids.append(rid)
        print(f"{len(ids)} of {len(rows)} {a.sport} rows are in the live drop set\n")

    lines = open(PATH, encoding="utf-8").read().splitlines()
    for rid in ids:
        pat = re.compile(rf"(?<![0-9-]){rid}(?![0-9])")
        hits = [i for i, l in enumerate(lines) if pat.search(l)]
        print(f"=== result {rid}: {'IN' if rid in live else 'not in'} the live "
              f"{a.sport} drop set; named on {len(hits)} line(s)")
        for i in hits:
            name, head = _header(lines, i)
            print(f"  line {i + 1}, in {name}:")
            for h in head:
                print(f"      {h}")
        print()


if __name__ == "__main__":
    main()
