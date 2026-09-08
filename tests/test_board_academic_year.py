"""The /rankings boards show and filter the stored academic year.

Owner, 2026-09-08: "year should be academic year" ... "Just change for
rankings."

season_year.py stores ONE clock -- August to July, named for the year it
opens in. Every surface used to add one for track on the way out ("nobody
calls the Dec 2025 - Jul 2026 season 2025"), and did it consistently:
display AND filter, so a Year chip always selected the season it named.
The /rankings page now shows and filters the academic year instead, on all
four boards.

⚠ SO THE SITE IS DELIBERATELY SPLIT, and this file is where that is
  written down. The athlete page, the school page and the share cards keep
  the label (app.season_label, school.py, cards.py): spring 2026 reads
  "2026 TF" there and "2025" on a board. That was raised before the change
  and chosen anyway. It is not an oversight, and closing it means moving
  those three together.

! THE PART THAT IS NOT A PREFERENCE: display and filter move as one. Show
  one and filter the other and a Year chip silently selects the wrong
  season, which is the failure the old convention existed to avoid.

    python tests/test_board_academic_year.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def _src(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


RK = _src("racecast", "rankings.py")
TE = _src("racecast", "teams.py")
JS = _src("racecast", "static", "rankings.js")


# ---- 1. no offset left anywhere on the rankings page ------------------ #
# ! CODE ONLY, VIA tokenize. Dropping lines that START with # missed
#   docstrings, and boardYear's docstring legitimately explains the label
#   as "year + 1 for track" -- which failed this check for saying so.
def _codeOnly(src):
    import io as _io, tokenize, token as _t
    out = []
    for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING, _t.NEWLINE,
                        tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            continue
        out.append(tok.string)
    return " ".join(out)


for name, src in (("rankings.py", RK), ("teams.py", TE)):
    code = _codeOnly(src)
    ok("year + 1" not in code,
       f"{name} still adds one to a track year somewhere in code")
    ok("year_tf" not in code,
       f"{name} still converts a label year back to the stored one")

import rankings                                                  # noqa: E402
ok(rankings._YEAR_LABEL == "year",
   f"_YEAR_LABEL is {rankings._YEAR_LABEL!r}; the boards show the stored year")


# ---- 2. display and filter agree ------------------------------------- #
#   The whole point. If the board selects s.year but filters year-1, a Year
#   chip picks the season before the one it names.
params = {}
where = rankings._whereClauses({"pool": "hs_m", "sport": "XC", "year": [2025],
                                "board": "ability"}, params, with_dates=False)
ok(" AND year = ANY(%(year)s)" in where,
   f"the year filter is not a plain equality on the stored year: {where!r}")
ok(params.get("year") == [2025],
   "the year must be bound as given, with no conversion")

# the three ability/perf/pr sorts and the teams sort read the bare column
for expr, label in ((rankings._SORTS_ABILITY["year"][0], "ability"),
                    (rankings._SORTS_PERFORMANCE["year"][0], "performance"),
                    (rankings._SORTS_PR["year"][0], "pr")):
    ok("CASE" not in expr,
       f"the {label} board still sorts on a converted year: {expr!r}")

import teams                                                     # noqa: E402
ok("CASE" not in teams._SORTS["year"][0],
   f"the teams board still sorts on a converted year: "
   f"{teams._SORTS['year'][0]!r}")

p2 = {}
w2 = teams._fieldWhere({"span": "season", "board_scope": "usa", "pool": "hs_m",
                        "sport": "XC", "state": [], "year": [2025],
                        "min_athletes": 5,
                        **{k: [] for k in rankings.UNIT_FILTERS}}, p2)
ok("t.year = ANY(%(year)s)" in w2,
   "the teams tab must filter the stored year too -- it shares one Year "
   "control with the athlete boards")
ok(p2.get("year") == [2025], "the teams year must be bound as given")


# ---- 3. the Year list offers academic years -------------------------- #
#   Offering a year the boards cannot hold puts an always-empty option at
#   the top of the list every autumn.
m = re.search(r"const YEARS = \(\(\) => \{(.*?)\}\)\(\);", JS, re.S)
ok(m is not None, "the YEARS list is gone")
if m:
    body = m.group(1)
    ok("getFullYear() + 1" not in body,
       "the Year list still runs a year ahead for track")
    ok("getMonth()" in body,
       "the newest academic year depends on the month: it opens in August")


# ---- 4. the one boundary that crosses conventions is converted -------- #
#   school_prs still speaks the label and links into a board that does not.
APP = _src("racecast", "app.py")
HTML = _src("racecast", "templates", "school_prs.html")
ok("board_year" in APP and "storedYear" in APP,
   "app.py must convert the label year for the rankings link")
# ! SCOPED TO THE LINK. data.year is still right everywhere else on this
#   page -- the year bar and the empty-state message are the PAGE's own
#   year, and the page kept the label.
link = next(l for l in HTML.split("\n") if "/rankings?" in l)
ok("board_year" in link,
   f"the View all link must carry the converted year: {link.strip()[:120]}")
ok("data.year }}" not in link,
   "the View all link must not carry the page's label year")
ok("{% if data.year %}" in HTML,
   "the page's own year bar should still use the label")


# ---- 5. the pages that KEPT the label still have it ------------------- #
#   Not a leftover -- the owner's choice. If these ever lose it, the split
#   documented at the top of this file has closed by accident rather than
#   by decision.
ok("year + 1" in _src("racecast", "app.py"),
   "app.season_label should still add one for track (the athlete page)")
ok("year_col} + 1" in _src("racecast", "school.py"),
   "school.py should still label track seasons with the year they end in")


if __name__ == "__main__":
    for m_ in failed:
        print("FAIL:", m_)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
