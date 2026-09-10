"""Course difficulty is shown on ONE scale for cross country and track
(owner, 2026-09-02), and the athlete page's Meet column shows the meet.

    python tests/test_difficulty_one_scale.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

import difficulty_view as dv                                     # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


# pin the zero without a database
dv._state["at"] = float("inf")
dv._state["mean"] = 0.0

ok(dv.diffPct(0.04, "XC") == dv.diffPct(0.04, "TF") == "+4.0%",
   "the same raw difficulty reads the same on either sport")
ok(dv.diffPct(0.0, "XC") == "0.0%" and dv.diffPct(-0.02, "TF") == "-2.0%",
   "zero is the AVERAGE TRACK, negative is faster than one")
ok(dv.sportMeanLog("XC") == dv.sportMeanLog("TF") == dv.sportMeanLog(None),
   "one zero, whatever the sport argument says")
ok("typical course" in dv.diffWords(0.04, "TF")
   and "track" not in dv.diffWords(0.04, "TF"),
   "the sentence no longer names a per-sport reference")

src = read("racecast", "difficulty_view.py")
# ⚠ THIS CHECK WAS PASSING ON A TECHNICALITY. It looked for "LIKE 'XC:%%'"
#   to prove the zero query "no longer splits by sport", and kept passing
#   when the query became "LIKE 'TF:%%'" -- which does split by sport. The
#   intent has legitimately changed: the zero IS a track now (owner,
#   2026-09-10, "the average tf course will have difficulty 0.0 and be the
#   baseline"), so the guard reads track cells on purpose. What must stay
#   true is that DISPLAY does not move the number.
ok("GROUP  BY 1" not in src, "the zero query is one number, not per sport")
ok(dv.sportMeanLog() == 0.0 and round(dv.relativePct(0.069), 6) == 6.9,
   "the display is the identity -- it shows what the engine stored")

html = read("racecast", "templates", "_explain.html")
ok('data-title="What a Rating Means"' in html
   and 'data-title="What Course Difficulty Means"' in html,
   "the explainer titles are in Title Case")
ok(all("&" not in m.group(1)
       for m in __import__("re").finditer(r'data-blurb="([^"]*)"', html)),
   "no entity inside a blurb's Jinja string: autoescape printed it verbatim")
import re as _re
for m in _re.finditer(r'data-blurb="([^"]*)"', html):
    ok(len(m.group(1)) <= 170, f"a blurb is short: {len(m.group(1))} chars")
css = read("racecast", "static", "style.css")
i = css.index(".info-i {")
block = css[i:css.index("}", i)]
ok("border: 1px solid #9ca3af" in block and "opacity: 0.8" in block,
   "the glyph is quiet: thin grey ring, faded until hovered")
ok(".info-i::after" in css and "font-style: normal" in css[css.index(".info-i::after"):][:300],
   "the tooltip text is upright")

app = read("racecast", "app.py")
xc_half = app[app.index("-- ================= XC half"):app.index("-- ================= TF half")]
ok("COALESCE(m.meet_name, mt.meet_name," in xc_half
   and "AS course" in xc_half,
   "the XC race row's `meet` is the meet name, with the course beside it")
tf_half = app[app.index("-- ================= TF half"):app.index("-- ================= TF half") + 6000]
ok("NULL::text                    AS course" in tf_half,
   "the TF half carries the matching column so the UNION lines up")
ath = read("racecast", "templates", "athlete.html")
ok("race.course != race.meet" in ath and "meet-course" in ath,
   "the athlete page prints the course under the meet on XC rows")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_difficulty_one_scale: all checks passed")

# the meet page lists its compiled races from one aggregate, not a compile
mc = read("racecast", "meet_compile.py")
ok("def compiledIndex(" in mc and "HAVING count(*) >= 5" in mc,
   "compiledIndex exists and counts scoring teams in SQL")
meet_route = app[app.index('@app.route("/meet/xc/<int:meet_id>")'):
                 app.index('@app.route("/race/xc/<int:meet_id>/compiled')]
ok("compiledIndex(cur, meet_id, source=src)" in meet_route
   and "compiledResults(" not in meet_route,
   "the XC meet page no longer compiles every race for its link list")
print("test_difficulty_one_scale: meet index checks passed")
