# ★ A SIDE CARD STEPS ASIDE ONLY FOR WHAT IT REPEATS (owner, 2026-10-23: the
#   course page's Team bests was "the only tab without the recent meets on
#   the side ... make it so that things like that don't happen all over the
#   site"). The rule went by the tab's name; now it goes by the card.
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


def test_no_stylesheet_hides_the_side_card_by_a_tab_name():
    for name in os.listdir(os.path.join(ROOT, "racecast", "static")):
        if not name.endswith(".css"):
            continue
        css = re.sub(r"/\*.*?\*/", "", read("racecast", "static", name), flags=re.S)
        for rule in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            sel, body = rule
            if ".rc-side" in sel and "display: none" in body and "rc-on-" in sel:
                # the one tab rule left: On the track keeps only its finishers card
                assert "rc-on-track .rc-side > :not(.rc-conv)" in sel, (name, sel.strip())


def test_the_script_asks_the_card_not_the_tab():
    js = read("racecast", "static", "race-page.js")
    assert ".rc-side[data-repeats]" in js and 'toggle("rc-side-away", away)' in js
    assert 'name === "teams"' not in js
    assert '!!root.querySelector(".rc-conv")' in js


def test_only_team_score_cards_say_they_repeat_the_teams_tab():
    for name in os.listdir(os.path.join(ROOT, "racecast", "templates")):
        html = read("racecast", "templates", name)
        for m in re.finditer(r'<aside class="rc-side"[^>]*data-repeats="teams"[^>]*>(.{0,400})', html, re.S):
            assert re.search(r"Team (scores|points)", m.group(1)), name
    for tpl in ("course.html", "records.html", "home.html"):
        assert "data-repeats" not in read("racecast", "templates", tpl), tpl
    js = read("racecast", "static", "predictions.js")
    assert js.count('<aside class="rc-side" data-repeats="teams">') == 2
