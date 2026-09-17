# Project: xc-predictor / tests
# File:    test_predictions_sim_ui.py
# Purpose: the page asks for the simulation, shows a Win column only when it
#          got one, and never prints a certainty about a cross country race.
#          The two pure helpers are run in node, so this tests behaviour and
#          not just that the words are in the file.
#
#   python -m pytest -q tests/test_predictions_sim_ui.py
import json
import os
import shutil
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "racecast", "static", "predictions.js")
_CSS = os.path.join(_ROOT, "racecast", "static", "style.css")


def js():
    with open(_JS) as f:
        return f.read()


def _cut(name):
    """One top-level function out of predictions.js, by brace balance."""
    src = js()
    i = src.index(f"function {name}(")
    depth, j = 0, src.index("{", i)
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(name)


def node(expr, *fns):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    prelude = "function esc(v){return v===null||v===undefined?'':String(v)}\n"
    body = prelude + "\n".join(_cut(f) for f in fns) + \
        f"\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run([shutil.which("node"), "-e", body],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_asks_for_the_simulation_on_team_requests():
    assert 'q.set("sim", "1")' in js()


def test_a_probability_never_reads_as_a_certainty():
    """★ 2,000 DRAWS CANNOT TELL 0 FROM 1-IN-5,000, and a page that prints
    "100%" about a cross country race is lying about something nobody should
    believe anyway."""
    got = node("[pct(0), pct(1), pct(0.0001), pct(0.999), pct(0.5), "
               "pct(0.734), pct(null), pct(undefined), pct(NaN)]", "pct")
    assert got[:4] == ["<1%", ">99%", "<1%", ">99%"]
    assert got[4] == "50%" and got[5] == "73%"
    assert got[6] is None and got[7] is None and got[8] is None


def test_the_tooltip_carries_the_range_the_odds_and_who_it_beats():
    teams = [{"team": "Alpha", "school_label": "Alpha"},
             {"team": "Beta", "school_label": "Beta"}]
    t = {"team": "Alpha", "sim": {"score_p10": 28.0, "score_p90": 44.0,
                                 "score_mean": 35.4, "score_sd": 6.1,
                                 "p_win": 0.62, "p_top3": 0.95,
                                 "p_incomplete": 0.0}}
    d = {"sim": {"h2h": {"Alpha": {"Beta": 0.62}}, "draws": 2000}}
    tip = node(f"scoreSpreadTip({json.dumps(t)}, {json.dumps(d)}, "
               f"{json.dumps(teams)})", "pct", "scoreSpreadTip")
    assert "28–44 points in 8 draws out of 10" in tip
    assert "mean 35.4, sd 6.1" in tip
    assert "wins 62%, top three 95%" in tip
    assert "beats Beta 62%" in tip
    # a team that cannot always field five says how often, since that is the
    # difference between fourth place and no score
    t["sim"]["p_incomplete"] = 0.21
    tip2 = node(f"scoreSpreadTip({json.dumps(t)}, {json.dumps(d)}, "
                f"{json.dumps(teams)})", "pct", "scoreSpreadTip")
    assert "cannot field five in 21% of draws" in tip2
    # and a row with no simulation gets no tooltip at all
    assert node(f'scoreSpreadTip({{"team":"Alpha"}}, {json.dumps(d)}, [])',
                "pct", "scoreSpreadTip") == ""


def test_the_win_column_appears_only_when_there_is_something_in_it():
    """sim is opt-in and can fail on its own without costing the table, so
    "asked for it" and "got it" are different questions."""
    src = js()
    assert "const anySim = !!(d.sim && d.sim.available && full.some((t) => t.sim));" in src
    assert 'anySim ? `<th class="pwin"' in src
    assert 'anySim ? `<td class="pwin">' in src
    # and when it failed, the note says so instead of going quiet
    assert 'd.sim.available === false && d.sim.reason' in src


def test_the_score_says_it_has_a_range_behind_it():
    """A title attribute is invisible until you hover something, and nobody
    hovers a number that looks inert."""
    src = js()
    assert 'class="has-tip"' in src and "tip-dot" in src
    css = open(_CSS).read()
    for rule in ("td.pwin", "td.has-tip", ".tip-dot"):
        assert rule in css, rule
    assert "cursor: help" in css
    # ten columns became eleven, so the phone scroll width grows with it
    assert "min-width: 720px" in css and "min-width: 660px" not in css
