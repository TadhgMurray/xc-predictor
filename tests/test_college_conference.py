"""A college's conference is a membership unit, never the host of a meet
(owner, 2026-09-02: an Arkansas athlete's rank line read "ARKANSAS STATE"
where the SEC belongs).

    python tests/test_college_conference.py
"""
import os
import sys
import types
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("database", "psycopg2", "psycopg2.extras"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["database"].getConn = lambda: None
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]

import check_school_units as C                                   # noqa: E402
import school_unit_overrides as OV                               # noqa: E402
import build_school_units as B                                   # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def conf(name):
    return [u for k, u in C.parseUnits(name, "", college=True, state="AR")
            if k == "conference"]


# 1. a host-named or state-named meet is not a conference
for name in ("Arkansas State Championships",
             "Arkansas State Outdoor Championships",
             "Arkansas Outdoor Championships",
             "Texas State Championships",
             "Ohio University Championships"):
    ok(conf(name) == [], f"{name!r} must not yield a conference, got {conf(name)}")

# 2. the real ones still do
ok(conf("SEC Cross Country Championships") == ["SEC"], "SEC acronym")
ok(conf("SEC Championships") == ["SEC"], "bare SEC")
ok(conf("Big Ten Outdoor Track & Field Championships") == ["BIG TEN"], "Big Ten")
ok(conf("Pac-12 Cross Country Championships") == ["PAC-12"], "Pac-12")
ok(conf("Southeastern Conference Championships") == ["SOUTHEASTERN"],
   "the named form parses (the alias folds it)")
ok(OV.aliasFor("conference", "SOUTHEASTERN") == "SEC", "SOUTHEASTERN -> SEC")
ok(OV.aliasFor("conference", "PAC") == "PAC",
   "PAC (Presidents' Athletic Conference) is NOT folded into the Pac-12")
ok("SEC" in OV.KNOWN_CONFERENCES and "PAC" in OV.KNOWN_CONFERENCES,
   "both are known conferences")

# 3. a known conference wins the latest season over a bigger host meet
# years are TEXT in the votes, as the census reads them
votes = Counter({("ARKANSAS STATE", "2025"): 9, ("SEC", "2025"): 4,
                 ("SEC", "2024"): 7})
unit, yr, clash = C.current(votes)
ok(unit == "ARKANSAS STATE" and clash,
   "by count alone the host meet wins and the conflict is flagged")
unit, yr, clash = C.current(votes, prefer=OV.KNOWN_CONFERENCES)
ok(unit == "SEC" and yr == "2025" and clash,
   "with the known set the SEC wins its season, conflict still reported")

# 4. and the writer applies both the alias and the preference
row = B._rowFor("Arkansas", "AR", {
    "division": Counter({("NCAA DI", "2025"): 30}),
    "region": Counter({("SOUTH CENTRAL", "2025"): 12}),
    "conference": Counter({("ARKANSAS STATE", "2025"): 9,
                           ("SOUTHEASTERN", "2025"): 3, ("SEC", "2025"): 2}),
})
cells = dict(zip(B._COLS, row[2:2 + len(B._COLS)]))
ok(cells["conference"] == "SEC" and cells["division"] == "NCAA DI"
   and cells["region"] == "SOUTH CENTRAL",
   f"the stored row says SEC: {cells}")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_college_conference: all checks passed")
