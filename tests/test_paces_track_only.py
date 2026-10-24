# ★ Paces from track races only, else the rating (owner, 2026-10-23:
#   "these paces are off" -- critical speed 5:39/mi for a 14:48 5K runner).
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
import paces as P                                                 # noqa: E402


def pace_s(per_mile):
    m, s = per_mile.split(":")
    return int(m) * 60 + int(s)


def test_cross_country_races_never_feed_the_line():
    # a 14:48 5K runner's XC season: raw course times, 4828 to 10000 m
    rows = [(2018, "hs_m", "XC", 5000, 888.7), (2018, "hs_m", "XC", 4828, 858.7),
            (2018, "hs_m", "XC", 10000, 1935.4)]
    got = P.seasonLadder(rows)
    assert got["paces"] == [] and "track" in got["reason"]


def test_track_races_make_the_ladder():
    rows = [(2019, "hs_m", "TF", 1609.344, 255.0), (2019, "hs_m", "TF", 3200, 548.0),
            (2019, "hs_m", "XC", 5000, 900.0), (2019, "hs_m", "TF", 400, 52.0)]
    got = P.seasonLadder(rows)
    assert got["basis"] == "track" and got["n_races"] == 2          # the 400 and the XC race left out
    cs = pace_s(got["paces"][1]["per_mile"])
    assert 270 <= cs <= 300                                          # ~4:30-5:00/mi for a 4:15/9:08 runner


def test_no_track_pair_takes_the_rating():
    rows = [(2018, "hs_m", "XC", 5000, 888.7)]
    fb = lambda: (2018, "hs_m", [(1609.344, 252.0), (5000.0, 870.0)])
    got = P.seasonLadder(rows, fb)
    assert got["basis"] == "rating" and got["year"] == 2018
    assert pace_s(got["paces"][1]["per_mile"]) < 300                 # faster than his 5K race pace + 10%
