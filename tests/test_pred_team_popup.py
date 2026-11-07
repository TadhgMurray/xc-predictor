"""The predictions page's team popup (owner, 2026-11-07: "make it like the
race page where if you click on a team it brings up a little pop-up")."""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_page_carries_the_race_pages_dialog():
    html = read("racecast", "templates", "predictions.html")
    m = re.search(r'<dialog class="rc-teampop pred-teampop".*?</dialog>', html, re.S)
    assert m, "the popup dialog left the page"
    for part in ("rc-pop-h", "rc-pop-t", "rc-pop-fk", "rc-pop-go", "rc-pop-x"):
        assert part in m.group(0), part


def test_every_team_name_opens_it():
    js = read("racecast", "static", "predictions.js")
    # the side cards (both scorings) and both team tables
    assert js.count("teamOpenCell(t, d)") - js.count("function teamOpenCell(t, d)") == 4
    assert not re.search(r"schoolCell\(t\.team, t\.state, t\.school_href, t\.school_label,\s*t\.crest\)\n\s*\}<span", js)


def test_the_popup_reads_the_predictions_own_rows_and_hs_ratings():
    js = read("racecast", "static", "predictions.js")
    body = js[js.index("function openPredTeam"):js.index("function openPredTeam") + 3000]
    assert "predTeamRows(d, t)" in body
    assert "rv(r.rating, r.hs_rating)" in body       # the HS rule (CLAUDE.md)


def test_a_dual_meet_table_is_not_the_pages_result():
    js = read("racecast", "static", "predictions.js")
    i = js.index('closest(".pred-team-open")')
    assert '!op.closest(".team-tool-out")' in js[i:i + 400]
    assert "e.metaKey || e.ctrlKey" in js[i:i + 400]   # a new tab still opens the page
