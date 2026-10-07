# Project: xc-predictor / tests
# File:    test_school_bands_field.py
# Purpose: the athlete chart's school bands (athlete_chart_data._seasonSchools,
#          athlete-charts.js schoolRuns) and the race page's field on one
#          scale (race-page.js drawField).
#
# ★ OWNER, 2026-10-07, on the round-3 mockups: "I do like the idea of putting
#   their school on the graph, and I kind of like the field on one scale."
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
from athlete_chart_data import build_chart_data                 # noqa: E402


def _race(date, sport, school, season, v=120.0):
    return dict(date=date, sport=sport, speed_rating=v, season_label=season, school=school)


def test_each_point_carries_its_seasons_school():
    races = [_race("2023-09-01", "XC", "Walt Whitman (MD)", "2023"),
             _race("2023-09-08", "XC", "Walt Whitman (MD)", "2023"),
             _race("2023-09-15", "XC", "MD Elite Club", "2023"),      # one club race
             _race("2023-09-22", "XC", "Unattached", "2023"),
             _race("2024-04-01", "TF", "", "2024"),                   # no school at all
             _race("2025-09-08", "XC", "Amherst (MA)", "2025")]
    got = [(p["d"], p.get("sc")) for p in build_chart_data(races)["all_rating"]]
    assert got == [("2023-09-01", "Walt Whitman"), ("2023-09-08", "Walt Whitman"),
                   ("2023-09-15", "Walt Whitman"), ("2023-09-22", "Walt Whitman"),
                   ("2024-04-01", None), ("2025-09-08", "Amherst")]


def _js(path, fn):
    src = open(os.path.join(ROOT, path), encoding="utf-8").read()
    start = src.index(f"function {fn}(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


def test_school_runs_absorb_seasons_without_a_school():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    fn = _js("racecast/static/athlete-charts.js", "schoolRuns")
    pts = [{"sc": None}, {"sc": "Pyle"}, {"sc": "Pyle"}, {"sc": None},
           {"sc": "Whitman"}, {"sc": "Amherst"}]
    out = subprocess.run(["node", "-e", fn + f"; console.log(JSON.stringify(schoolRuns({json.dumps(pts)})))"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [{"school": "Pyle", "start": 0, "end": 3},
                               {"school": "Whitman", "start": 4, "end": 4},
                               {"school": "Amherst", "start": 5, "end": 5}]


def test_the_race_page_has_the_field_and_redraws_it_on_a_team_or_scale_change():
    tpl = open(os.path.join(ROOT, "racecast", "templates", "race.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "racecast", "static", "race-page.js"), encoding="utf-8").read()
    assert '<figure class="rc-field" hidden>' in tpl
    light = _js("racecast/static/race-page.js", "light")
    assert "drawField();" in light
    assert re.search(r'addEventListener\("rc-scale-change"[^\n]*drawField', js)
    # the team key's hidden state separator never reaches the reader
    assert '.split(/[\\u0000\\uFFFD]/)[0]' in light
