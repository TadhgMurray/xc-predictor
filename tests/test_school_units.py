# Project: xc-predictor / tests
# File:    test_school_units.py
# Purpose: Lock the two unit hierarchies down. They are DIFFERENT, they
#          were each an explicit owner ruling, and nothing in the code
#          stops a later edit quietly merging them.
#
#     python tests/test_school_units.py          # exit 0 = all pass
#
# ★ HIGH SCHOOL: a division is NOT its own unit. You are in NCS
#   Division 1 and, separately, in CA Division 4. Order runs widest-first
#   down each branch: state, state's division, section, section's
#   division.
#
# ★ COLLEGE: a division IS its own unit, and it is the TOP of the
#   hierarchy. Order is national, division, region, conference.
#
#   The HS rule ("never show a bare division") must never be applied to
#   college, where "NCAA DIII" standing alone is exactly right.

import sys

sys.path[:0] = ["racecast", "scripts", "engine"]

from database import getConn                                # noqa: E402
from school_units import unitsFor                            # noqa: E402

_FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"          got  {got}\n          want {want}")
        _FAIL.append(label)


def main():
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO school_unit (school, state, sport, league, section,
                section_div, state_unit, state_div, conference, region,
                division, is_college, conflict, votes, asof) VALUES
            ('T_HS','CA','XC','EBAL','NCS','1','CA','4',NULL,NULL,NULL,
             false,false,40,'2025'),
            ('T_HS_NODIV','OR','XC','TRICO','SOUTH',NULL,NULL,NULL,NULL,
             NULL,NULL,false,false,9,'2025'),
            ('T_COL','NY','XC',NULL,NULL,NULL,NULL,NULL,'LIBERTY','MIDEAST',
             'NCAA DIII',true,false,24,'2025')""")
        # a class that only repeats the section division (issue 134)
        cur.execute("""
            INSERT INTO school_unit (school, state, sport, league, section,
                section_div, state_unit, state_div, class, is_college,
                conflict, votes, asof) VALUES
            ('T_HS_DUP','CA','XC','EBAL','NCS','2','CA','2','2',false,false,
             30,'2025'),
            ('T_HS_TX','TX','XC','D12','REGION II',NULL,NULL,NULL,'6A',false,
             false,30,'2025')""")

        def labels(school, st, **kw):
            return [u["label"] for u in unitsFor(cur, school, st, **kw)]

        def kinds(school, st, **kw):
            return [u["kind"] for u in unitsFor(cur, school, st, **kw)]

        # ---- college: division is its own unit, and it leads ---------- #
        check("college order is division, region, conference",
              kinds("T_COL", "NY"), ["division", "region", "conference"])
        check("college division stands alone, uncased",
              labels("T_COL", "NY"), ["NCAA DIII", "MIDEAST", "LIBERTY"])
        check("college long form keeps the org code intact",
              labels("T_COL", "NY", long=True),
              ["NCAA DIII", "Mideast Region", "Liberty"])
        # the HS collapse rule must not touch college
        check("college is identical collapsed or not",
              labels("T_COL", "NY"), labels("T_COL", "NY", collapse=False))

        # ---- high school: a division always names its parent ---------- #
        check("HS chips qualify every division, drop the bare section, "
              "biggest first",
              labels("T_HS", "CA"), ["CA D4", "NCS D1", "EBAL"])
        check("HS long form spells the parent out",
              labels("T_HS", "CA", long=True),
              ["CA Division 4", "North Coast Section Division 1", "EBAL"])
        check("a digit class that repeats the division is not a chip",
              labels("T_HS_DUP", "CA"), ["CA D2", "NCS D2", "EBAL"])
        check("a real class shows as Class 6A, before the section",
              labels("T_HS_TX", "TX"), ["Class 6A", "REGION II", "D12"])
        check("HS ranking keeps the section as its own scope",
              sorted(kinds("T_HS", "CA", collapse=False)),
              ["league", "section", "section_div", "state_div"])
        check("HS with no divisions still shows its section",
              labels("T_HS_NODIV", "OR"), ["SOUTH", "TRICO"])

        # ---- the rule that must never cross over ---------------------- #
        bare = [u for u in unitsFor(cur, "T_HS", "CA")
                if u["kind"].endswith("_div")
                and u["label"].strip().upper().lstrip("D").isdigit()]
        check("no HS division is ever shown bare", bare, [])

        conn.rollback()

    print(f"\n  {len(_FAIL)} failed\n")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
