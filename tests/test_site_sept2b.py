"""The 2026-09-02 evening batch, pinned by source and by the pure helpers.

    python tests/test_site_sept2b.py
"""
import io
import os
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

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


import tf_points as TP                                           # noqa: E402
ok(TP.prettyEventName("javelin") == "Javelin", "javelin -> Javelin")
ok(TP.prettyEventName("shot put") == "Shot Put", "shot put -> Shot Put")
ok(TP.prettyEventName("1600m") == "1600m", "a distance keeps its unit")
ok(TP.prettyEventName("hj") == "High Jump", "codes still translate")

import school_prs as SP                                          # noqa: E402
ok("event_kind IS NULL" in SP.runningSql("TF", event_kind=True)
   and "event_kind" not in SP.runningSql("TF"),
   "running rows exclude hurdle-kind rows only when the column exists")

import school as SC                                              # noqa: E402
ok(SC.distLabel(4828) == "3 Mile" and SC.distLabel(5000) == "5000m",
   "distance labels")
src = read("racecast", "school.py")
fn = src[src.index("def schoolMeets("):src.index("def currentSeason(")]
ok("GROUP  BY rr.meet_id" in fn and "array_agg(DISTINCT round(rr.distance)::int)" in fn
   and "FROM   ranking_results rr" in fn,
   "the meets table is one row per meet from ranking_results, with distances")
html = read("racecast", "templates", "school.html")
ok("?school={{ school|urlencode }}" in html and "<th>Distances</th>" in html,
   "the school page links each meet with the school pinned and shows distances")

idx = read("scripts", "add_page_indexes.py")
ok('("meets_tf",        "meet_id", "idx_meets_tf_meet", None)' in idx
   and '("meets",           "meet_id", "idx_meets_meet", None)' in idx,
   "the meet-id indexes are in the page-index list")

top = read("racecast", "templates", "_topbar.html")
ok('href="/meets"' in top, "Meets is in the top bar")

app = read("racecast", "app.py")
ok(app.count("hl_school = (request.args.get(\"school\")") == 2
   and 'render_template("race.html", hl_school=hl_school,' in app
   and 'render_template("race_tf.html", hl_school=hl_school,' in app,
   "both race routes take ?school= and pass it to their template")
ok('class="hl-school"' in read("racecast", "templates", "race_tf.html"),
   "the TF race page tints the school's rows too")
ok("school_divs" in app and "school_events" in app
   and "school=school, school_divs=school_divs" in app
   and "school=school, school_events=school_events" in app,
   "both meet routes mark the school's races")
race = read("racecast", "templates", "race.html")
ok('class="hl-school"' in race, "the race page tints the school's rows")
meet = read("racecast", "templates", "meet.html")
ok("school_divs" in meet and "school|urlencode" in meet,
   "the XC meet page marks and forwards the school")
meet_tf = read("racecast", "templates", "meet_tf.html")
ok("school_events" in meet_tf and "school|urlencode" in meet_tf,
   "the TF meet page marks and forwards the school")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_site_sept2b: all checks passed")
