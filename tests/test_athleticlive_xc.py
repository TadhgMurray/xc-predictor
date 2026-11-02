"""sources/athleticlive_xc.py: AthleticLIVE cross country lands in the
athletic.net XC shape (owner, 2026-11-01)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from sources import athleticlive as A, athleticlive_xc as X  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "alive_xc")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


ALIVE = {"name": "Test Invitational", "meet_date": "2026-08-17", "venue": "Park, Town",
         "state": "IL", "levels": [], "indoor": False}


def test_xc_races_found_in_summary():
    evs = X.xcEvents(_load("summary_76201.json"))
    assert [e["id"] for e in evs] == [2906134, 2906135]


def test_plan_has_anet_xc_shape():
    evs = X.xcEvents(_load("summary_76201.json"))
    p = X.plan(276933, ALIVE, evs[:1], {2906134: _load("event_2906134.json")})
    (mi, div_id, _ev, div), = p["meets_to_save"]
    assert mi["ID"] == 276933 and div_id == A.DIV_BASE + 2906134
    assert round(div["Meters"]) == 2736 and div["Division"] == "Boys - Everyone"
    r = p["results_to_save"][0]
    assert len(r) == 8 and r[0]["IDResult"] >= A.RESULT_BASE
    assert r[0]["SortValue"] == 639.3 and r[0]["AthleteID"] == 1000 and r[0]["Place"] == 1
    assert A.holdUnknown(p) == 0 and len(p["results_to_save"]) == 6


def test_runner_without_anet_id_holds_meet_when_too_many():
    evs = X.xcEvents(_load("summary_76201.json"))
    doc = _load("event_2906134.json")
    for x in doc["_source"]["r"][:3]:
        x["a"]["ani"] = None
    p = X.plan(276933, ALIVE, evs[:1], {2906134: doc})
    A.holdUnknown(p)
    assert p["results_to_save"] == [] and p["held"]["whole meet"]


def test_scrape_local_lands_xc_before_cover():
    sh = open(os.path.join(ROOT, "deploy", "scrape_local.sh"), encoding="utf-8").read()
    assert sh.index("athleticlive_xc land") < sh.index("athleticlive cover")


def test_a_combined_list_goes_and_the_divisions_stay():
    """2026-11-01, meet 274532: the same runner and time in his division
    and in the combined list over the divisions."""
    a = {(1, 800), (2, 810), (3, 820)}
    b = {(4, 805), (5, 815)}
    m = a | b
    assert X.mergedEvents({"a": a, "b": b, "m": m}) == {"m"}
    # only one division posted beside the combined list: the division goes
    assert X.mergedEvents({"a": a, "m": m}) == {"a"}
    # separate races keep each other
    assert X.mergedEvents({"a": a, "b": b}) == set()
    # the same list twice: one stays
    assert len(X.mergedEvents({"a": a, "a2": set(a)})) == 1


def test_plan_skips_the_combined_list():
    evs = X.xcEvents(_load("summary_76201.json"))
    doc = _load("event_2906134.json")
    comb = dict(evs[0], id=999)
    p = X.plan(276933, ALIVE, [evs[0], comb], {2906134: doc, 999: doc})
    assert len(p["meets_to_save"]) == 1 and len(p["results_to_save"]) == 6
    assert p["skipped"]["combined list (its runners are in the other races)"] == 1


def test_athletic_net_short_of_athleticlive_is_requeued():
    """2026-11-01, Mt. SAC: athletic.net half posted when scraped."""
    assert X.anetShort(1200, 2600)
    assert not X.anetShort(2550, 2600)          # within 10%
    assert not X.anetShort(0, 5)                # too few to say
    evs = X.xcEvents(_load("summary_76201.json"))
    n = X.aliveCount(evs[:1], {2906134: _load("event_2906134.json")})
    assert n == 6


def test_a_label_with_no_distance_takes_the_meets():
    evs = X.xcEvents(_load("summary_76201.json"))
    ev = dict(evs[0], ab="", name="Fastest JV Boys")
    p = X.plan(276933, ALIVE, [ev, evs[1]], {ev["id"]: _load("event_2906134.json")})
    assert round(p["meets_to_save"][0][3]["Meters"]) == round(X.metersOf(evs[1]["ab"] or evs[1]["name"]))


def test_meet_distance_only_when_unanimous():
    assert X.meetDistance([{"name": "5K Varsity"}, {"name": "Fastest JV Boys"}]) == 5000.0
    assert X.meetDistance([{"name": "5K Varsity"}, {"name": "2 Mile MS"}]) is None
    assert X.meetDistance([{"name": "JV Boys"}]) is None


def test_venue_distance_needs_a_dominant_one():
    assert X.dominant({5000: 9, 4000: 1}, min_n=3) == 5000
    assert X.dominant({5000: 6, 4000: 4}, min_n=3) is None
    assert X.dominant({5000: 2}, min_n=3) is None


def test_times_must_fit_the_distance():
    assert X.plausiblePace([989.4, 1050, 1100], 5000)          # 16:29 for 5K
    assert not X.plausiblePace([600, 650, 700], 5000)          # 10:00 for 5K
    assert not X.plausiblePace([989.4, 1050, 1100], 1600)      # a 1600 median of 17:30
    assert not X.plausiblePace([], 5000)


def test_nightly_gives_distances_before_normalising():
    src = open(os.path.join(ROOT, "deploy", "nightly_update.sh"), encoding="utf-8").read()
    assert src.index("athleticlive_xc distances --write") < src.index("step 05_normalize_new")
