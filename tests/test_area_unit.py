"""The AREA: the level between league and section that California track
has and cross country does not (owner, 2026-09-02: NCS -> Tri-Valley ->
EBAL). Parsed off track meet names, copied onto the school's XC row,
shown, ranked and filterable like every other unit.

    python tests/test_area_unit.py
"""
import io
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("database", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["database"].getConn = lambda: None
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]
sys.modules["psycopg2.errors"].UndefinedColumn = type("UndefinedColumn",
                                                      (Exception,), {})

import check_school_units as C                                   # noqa: E402
import build_school_units as B                                   # noqa: E402
import school_units as SU                                        # noqa: E402
import rankings as RK                                            # noqa: E402
import build_ranking_results as BR                               # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def kinds(name):
    return dict(C.parseUnits(name, "", college=False, state="CA"))


# 1. the parser
ok(kinds("NCS Tri-Valley Track & Field Championships").get("area") == "TRI-VALLEY"
   and kinds("NCS Tri-Valley Track & Field Championships").get("section") == "NCS",
   "NCS Tri-Valley T&F votes area TRI-VALLEY and section NCS")
ok(kinds("NCS Redwood Empire Championships").get("area") == "REDWOOD EMPIRE",
   "a two-word area")
ok(kinds("Tri-Valley Area Championships").get("area") == "TRI-VALLEY",
   "the '<Area> Area' form")
for name in ("NCS Meet of Champions", "NCS Championships", "CCS Semifinals",
             "NCS Division 1 Cross Country Championships",
             "NCS Track and Field Championships"):
    ok("area" not in kinds(name), f"{name!r} is not an area: {kinds(name)}")
ok(kinds("EBAL Championships").get("league") == "EBAL", "leagues still parse")

# 2. the writer stores it and copies it across sports
ok("area" in B._COLS and "area" in B._HS_KINDS and "area        text" in B._DDL,
   "the writer carries the area column")
i = 3 + B._COLS.index("area")
tf = ["Monte Vista", "CA", "TF"] + [None] * len(B._COLS) + [False, False, 9, "2025"]
xc = ["Monte Vista", "CA", "XC"] + [None] * len(B._COLS) + [False, False, 9, "2025"]
other = ["Elsewhere", "CA", "XC"] + [None] * len(B._COLS) + [False, False, 9, "2025"]
tf[i] = "TRI-VALLEY"
n = B.crossFillAreas([tf, xc, other])
ok(n == 1 and xc[i] == "TRI-VALLEY" and other[i] is None,
   f"the XC row takes the TF row's area, nobody else's: {n} {xc[i]} {other[i]}")
xc2 = list(xc); xc2[i] = "BAY SHORE"
B.crossFillAreas([tf, xc2])
ok(xc2[i] == "BAY SHORE", "a row's own vote is never overwritten")

# 3. shown between league and section, ranked inside its section, filterable
# fbddb2e turned the chips biggest-first; the area still sits between the
# section and the league, whichever way the line reads.
_a, _l, _s = (SU._HS_CHIPS.index(k) for k in ("area", "league", "section_div"))
ok(min(_l, _s) < _a < max(_l, _s), "chip order: area between section and league")
ok("area" in SU._FILTERABLE, "the area is a board filter")
ok(RK.HS_UNITS.index("section_div") < RK.HS_UNITS.index("area")
   < RK.HS_UNITS.index("league") and RK.UNIT_COLUMNS["area"] == ("area",),
   "rankings knows the area as a high-school unit")
ok("area" in BR._UNIT_COLS and
   BR._COLUMNS[BR._COLUMNS.index("class") + 1] == "area",
   "ranking_results denormalises the area right after class")
app = read("racecast", "app.py")
ok('"area": "section"' in app and '"unit:area"' in app
   and app.index('"unit:section_div", "unit:area"') > 0,
   "the rank line ranks the area inside its section, after the section division")
html = read("racecast", "templates", "rankings.html")
js = read("racecast", "static", "rankings.js")
ok('data-field="area"' in html and '"area", "league"' in js
   and 'area: "section"' in js, "the filter UI offers the area under the section")

# 4. every unit list agrees
# (253: section is denormalised too, so every filter column is a row column)
ok(set(RK.HS_UNITS) <= set(SU._FILTERABLE) | {"state_div"}
   and set(RK.UNIT_COLUMNS) <= set(BR._UNIT_COLS),
   "the filter lists agree across files")
ok(kinds("NCS Bay Shore Area Championships").get("area") == "BAY SHORE"
   and [k for k, _ in C.parseUnits("NCS Bay Shore Area Championships", "",
                                    college=False, state="CA")].count("area") == 1,
   "'NCS Bay Shore Area' is one area named BAY SHORE")
ok("area" not in kinds("SJS Sub-Section Championships"),
   "a sub-section round is not an area")
sec = [u for k, u in C.parseUnits("CIF NCS Tri-Valley Championships", "",
                                   college=False, state="CA") if k == "section"]
ok(sec == ["NCS"], f"the CIF rule no longer votes a section named NCS TRI-VALLEY: {sec}")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_area_unit: all checks passed")
