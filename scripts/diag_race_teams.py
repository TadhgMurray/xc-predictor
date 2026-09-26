#!/usr/bin/env python3
"""
diag_race_teams.py -- why a race page's team row has points but no places.

    /srv/venv/bin/python scripts/diag_race_teams.py /race/xc/<meet>/<div>
    /srv/venv/bin/python scripts/diag_race_teams.py /race/xc/<meet>/<div> --team "De La Salle"
    (add ?alt=1 to the path when the page URL has it)

★ WHY (owner, 2026-09-26: De La Salle 2nd with 67 points and every scorer
  column '-', again, after the looser graft of 09-25). The race page shows
  the meet's PUBLISHED points and fills the 1-7 columns from its OWN
  scoring of the results, paired by school name (app._graftPublished).
  A published team with blank columns means no computed team was paired
  with it: its runners are missing from the results, or spelled several
  ways so that no spelling has five (each piece is then an incomplete
  team, and incomplete teams give up their places -- which is why the
  other teams' places run 1..N with no gaps).

  This rebuilds the page's scoring exactly as race_xc does and prints:
  every school spelling in the results with its runner count, the
  published teams, the computed teams and the incomplete ones, and the
  pairing -- with, for each unpaired published team, the spellings that
  look like it. READ-ONLY.
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _alike(a, b):
    """Two school spellings that plausibly name one school."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na.startswith(nb) or nb.startswith(na):
        return True
    wa = {w for w in re.findall(r"[a-z]{4,}", (a or "").lower())}
    wb = {w for w in re.findall(r"[a-z]{4,}", (b or "").lower())}
    return bool(wa & wb - {"high", "school", "academy", "valley", "county"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="/race/xc/<meet>/<div>[?alt=N]")
    ap.add_argument("--team", help="only spellings like this name")
    a = ap.parse_args()
    m = re.search(r"/race/xc/(\d+)/(\d+)(?:\?alt=(\d+))?", a.path)
    if not m:
        ap.error("give the race page path, /race/xc/<meet>/<div>")
    meet_id, div_id = int(m.group(1)), int(m.group(2))
    args = {"alt": m.group(3)} if m.group(3) else {}

    import psycopg2.extras
    import app as A
    from meet_compile import (scoreRows, publishedScores, splitCollisionTeams,
                              unsplitTeams, stampSchoolStates)
    from collections import Counter

    with A.getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            src, _, _ = A._xc_meet_sources(cur, meet_id, args, div_id)
            header = A.get_race_header(cur, meet_id, div_id, source=src)
            results = [dict(r) for r in
                       A.get_race_results(cur, meet_id, div_id, source=src)]
            A._borrowTwins(cur, results, "results", meet_id, src)
            stampSchoolStates(cur, results)
            published = publishedScores(cur, meet_id)
            ranked = [{**r, "place": i} for i, r in enumerate(results, 1)
                      if r.get("time_seconds") is not None]
            splitCollisionTeams(cur, ranked)
    computed = unsplitTeams(scoreRows(ranked))
    pub = (published.get((div_id, (header or {}).get("gender")))
           or published.get((div_id, None)) or [])

    print(f"race {meet_id}/{div_id} (source {src or 'any'}): "
          f"{len(results)} results, {len(ranked)} with a time")
    names = Counter(r.get("school") or "(none)" for r in ranked)
    want = a.team
    print("\nSCHOOL SPELLINGS IN THE RESULTS"
          + (f" (like {want!r})" if want else " (runners)"))
    for s, n in names.most_common():
        if want and not _alike(s, want):
            continue
        places = [r["place"] for r in ranked if (r.get("school") or "(none)") == s]
        print(f"  {n:>3}  {s!r:<40} places {places[:8]}{' ...' if len(places) > 8 else ''}")

    print(f"\nPUBLISHED TEAMS ({len(pub)})")
    for t in pub:
        print(f"  {t.get('place', ''):>3}  {t.get('school')!r:<40} {t.get('points')}")
    print(f"\nCOMPUTED TEAMS ({len(computed['teams'])}), "
          f"INCOMPLETE ({len(computed['incomplete'])})")
    for t in computed["teams"]:
        print(f"  team        {t['school']!r:<40} {t['points']}")
    for t in computed["incomplete"]:
        print(f"  incomplete  {t['school']!r:<40} {t['n']} runner(s)")

    graft = A._graftPublished(pub, computed["teams"])
    unpaired = [p for p in pub if id(p) not in graft]
    print(f"\nPAIRING: {len(pub) - len(unpaired)} of {len(pub)} published "
          f"teams got their runners")
    for p in unpaired:
        like = [s for s in names if _alike(s, p.get("school"))]
        print(f"  UNPAIRED {p.get('school')!r} ({p.get('points')} pts); "
              f"spellings in the results that look like it: "
              + (", ".join(f"{s!r} x{names[s]}" for s in like) or "NONE -- "
                 "its runners are not in this race's results"))


if __name__ == "__main__":
    main()
