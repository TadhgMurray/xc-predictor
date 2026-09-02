"""The race-count floor: three up to 2026 TF, none from 2026 XC on.

    python tests/test_season_floor.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

import season_floor as SF                                        # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


# the rule, in stored years: 2026 TF is stored 2025 and keeps the floor
ok(SF.floorFor(2025) == 3, "2025 stored (2025 XC, 2026 TF) keeps 3")
ok(SF.floorFor(2026) == 1, "2026 stored (2026 XC, 2027 TF) has no floor")
ok(SF.floorFor(2031) == 1, "later years have none")
ok(SF.floorFor(None) == 3, "unknown year keeps the floor")
ok(SF.floorLabel(2025) == "(3+ races)", "settled seasons label the floor")
ok(SF.floorLabel(2026) == "", "open seasons say nothing")

sql = SF.floorSql(explicit=False)
ok("OR s.year >= 2026" in sql and "%(min_races)s" in sql,
   "the default floor exempts open seasons")
ok(SF.floorSql(explicit=True) == "s.n_races >= %(min_races)s",
   "a typed minimum applies to every row")
ok("p.n_races" in SF.floorSql(False, alias="p"), "alias is honoured")

ok(SF.percentileWords(41, 8123) == "top 1%", "41 of 8123 rounds up to 1")
ok(SF.percentileWords(163, 8123) == "top 3%", "163 of 8123 is 2.0..% -> 3")
ok(SF.percentileWords(5000, 8000) == "top 63%", "past the median still reads")
ok(SF.percentileWords(None, 10) is None and SF.percentileWords(3, 0) is None,
   "no rank or no board -> nothing")
ok(SF.clockFor(947.4) == "15:47" and SF.clockFor(3605) == "1:00:05",
   "m:ss, whole seconds")
ok(SF.poolWords("hs_m|XC") == "high-school boys", "pool words strip the sport")

# the readers all go through the module
rk = read("racecast", "rankings.py")
ok("s.n_races >= %(min_races)s" not in rk, "rankings.py has no bare floor")
ok(rk.count("floorSql(") >= 4, "every board query uses floorSql")
ok('"min_races_explicit"' in rk, "parseFilters records whether it was typed")
app = read("racecast", "app.py")
ok("floorFor(season[\"year\"])" in app, "the rank line is held to the same floor")
ok("percentileWords(" in app and "clockFor(" in app, "header phrases built")
pn = read("racecast", "panels.py")
ok("OR s.year >= %(open_from)s" in pn, "home panels exempt open seasons in SQL")
ok("floorFor(" in pn, "and in the python gate")
js = read("racecast", "static", "rankings.js")
ok('dataset.touched' in js.split("min_races", 1)[1][:3000]
   and 'q.set("min_races", $("min_races").value || defaultMinRaces())' not in js,
   "an untouched box sends no min_races")
tpl = read("racecast", "templates", "athlete.html")
ok('class="flag-legend"' in tpl and 'class="rl-floor"' in tpl
   and 's-equiv' in tpl, "athlete page carries legend, floor label, equiv line")
ab = read("racecast", "templates", "about.html")
ok('id="glossary"' in ab and "<dt>Difficulty</dt>" in ab, "about glossary")
css = read("racecast", "static", "style.css")
ok(".flag-legend" in css and ".glossary dt" in css and ".s-equiv" in css,
   "styles present")

if failed:
    for f in failed:
        print("FAIL:", f)
    sys.exit(1)
print("ok", flush=True)
