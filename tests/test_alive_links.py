"""An AthleticLIVE meet link, under any timer's domain, can be queued
(owner, 2026-11-04: live.usp-sports.com/meets/80057/events/xc/2940261)."""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import fixes as FX   # noqa: E402


def test_athleticlive_links_on_any_domain():
    assert FX.aliveTarget("https://live.usp-sports.com/meets/80057/events/xc/2940261") == (80057, "XC")
    assert FX.aliveTarget("https://dakotatiming.anet.live/meets/41234") == (41234, None)
    assert FX.aliveTarget("https://live.athletic.net/meets/5/events/tf/9") == (5, "TF")
    assert FX.aliveTarget("https://example.com/meets/123") is None          # no events path, not ours
    assert FX.queueTarget("https://live.usp-sports.com/meets/80057/events/xc/2940261") == (80057, "XC", "alive")
    # the feeds' own links still go where they went
    assert FX.queueTarget("https://www.athletic.net/CrossCountry/meet/289504/results") == (289504, "XC", "anet")
    assert FX.queueTarget("https://www.tfrrs.org/results/xc/28639/x") == (28639, "XC", "tfrrs")


def test_the_lander_takes_a_wanted_meet_first():
    src = open(os.path.join(ROOT, "sources", "athleticlive_xc.py"), encoding="utf-8").read()
    assert "COALESCE(am.wanted, false)" in src and "ORDER BY 3 DESC, am.meet_date DESC" in src
    sh = open(os.path.join(ROOT, "deploy", "scrape_local.sh"), encoding="utf-8").read()
    assert sh.index("sources.athleticlive_xc land") < sh.index("sources.athleticlive land")
