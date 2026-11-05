#!/usr/bin/env python3
"""
school_label_audit.py -- every school name raced this season, checked for
the state (and so the crest and the link) a page would give it.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/school_label_audit.py                 # this season, both sports
    $P scripts/school_label_audit.py --year 2025 --show 100

★ WHY (owner, 2026-11-05: "You need to make sure this issue never pops up
  again. I need to never have another issue with logos or schools").
  "Georgetown TX" and "Notre Dame WI" on the college women's board: a name
  two institutions share, labelled by the biggest cluster of athletes under
  it -- a high school -- on a row that was a college's. The board now asks
  by the row's pool (school_identity.teamState); this finds every name where
  the answers can still disagree, before a page shows it.

★ WHAT IT LISTS, per (school, level) with athletes this season:
    college_elsewhere  a college-level name whose primary state (what a page
                       with no level in hand shows) is not one of the
                       college directory's states for it: every such page
                       shows the college under a high school's state
    college_unknown    a college-level name the directory does not know (a
                       club, unattached or alumni entry is not one): no
                       state, crest or link can be resolved for it by level
  READ-ONLY. Exit 1 when anything is listed, so the nightly's summary shows it.
"""
import argparse
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re

_NOT_COLLEGE = re.compile(r"\bclub\b|unattached|unaffiliated|unknown|alumni|\btc\b|"
                          r"track club|running club|academy", re.I)

_SQL = """
    SELECT school, split_part(split_part(pool, '|', 1), '_', 1) AS level,
           count(DISTINCT person_id) AS n
    FROM   athlete_season
    WHERE  year = %(y)s AND NULLIF(btrim(school), '') IS NOT NULL
    GROUP  BY 1, 2
"""


def classify(school, level, primary, candidates, clusters):
    """The finding for one (school, level), or None. Pure.
    primary: the name's primary state; candidates: the college directory's
    states for the name ([] when unknown); clusters: {state: share}."""
    if level in ("college", "pro"):
        if not candidates:
            # a club, an unattached entry or an alumni team is no college
            return None if _NOT_COLLEGE.search(school or "") else "college_unknown"
        if primary and primary not in candidates:
            return "college_elsewhere"
        return None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=None, help="athlete_season.year (default: newest)")
    ap.add_argument("--show", type=int, default=60)
    a = ap.parse_args(argv)
    from database import getConn
    import school_identity as S
    S.loadLabels(getConn, force=True)
    college = S._LABELS.get("college") or {}
    try:
        from build_college_directory import candidateStates
    except ImportError:
        candidateStates = None
    with getConn() as conn:
        with conn.cursor() as cur:
            if a.year is None:
                cur.execute("SELECT max(year) FROM athlete_season")
                a.year = cur.fetchone()[0]
            cur.execute(_SQL, {"y": a.year})
            rows = cur.fetchall()
        conn.rollback()
    found = defaultdict(list)
    clusters_all = S._LABELS.get("clusters") or {}
    for school, level, n in rows:
        cands = list(candidateStates(college, school) or []) if candidateStates and college else []
        kind = classify(school, level, S.primaryState(school), cands, clusters_all.get(school))
        if kind:
            found[kind].append((int(n), school, level, S.primaryState(school), cands))
    print(f"[school audit] {len(rows):,} (school, level) pairs raced in {a.year}")
    for kind, label in (("college_elsewhere", "college names a page without the level shows "
                                              "under another institution's state"),
                        ("college_unknown", "college names the directory does not know")):
        got = sorted(found.get(kind, []), reverse=True)
        print(f"\n  {kind}: {len(got):,} {label}")
        for n, school, level, prim, cands in got[:a.show]:
            print(f"    {n:>6,} athletes  {school!r:<36} {level:<8} shown {prim or '-':<3}  "
                  f"college directory {','.join(cands) or '-'}")
    return 1 if any(found.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
