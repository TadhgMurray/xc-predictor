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
    doc = _load("event_2906134.json")
    for k in ("d", "me", "mt"):
        doc.get("_source", doc).pop(k, None)              # a document stating none
    p = X.plan(276933, ALIVE, [ev, evs[1]], {ev["id"]: doc})
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


def test_each_race_on_its_own_day():
    """2026-11-02, Mt. SAC: Friday and Saturday races, all landed on Friday."""
    alive = {"meet_date": "2026-10-23", "date_end": "2026-10-24"}
    assert X.eventDay({"start": "2026-10-24T00:51:00Z"}, alive) == "2026-10-23"   # 5:51 pm PDT
    assert X.eventDay({"start": "2026-10-24T14:20:00Z"}, alive) == "2026-10-24"   # 7:20 am PDT
    assert X.eventDay({"start": "2026-10-26T14:20:00Z"}, alive) == "2026-10-24"   # kept in range
    assert X.eventDay({}, alive) is None


def test_plan_dates_each_race_and_takes_anets_venue():
    evs = X.xcEvents(_load("summary_76201.json"))
    ev = dict(evs[0], start="2026-08-18T15:00:00Z")
    alive = dict(ALIVE, date_end="2026-08-18")
    meta = {"Location": {"ID": 9, "Name": "Anet Park", "Lat": 1.0, "Long": 2.0,
                         "State": "IL", "Indoor": False}}
    p = X.plan(276933, alive, [ev], {ev["id"]: _load("event_2906134.json")}, meta=meta)
    mi = p["meets_to_save"][0][0]
    assert mi["MeetDate"] == "2026-08-18" and mi["Location"]["Name"] == "Anet Park"
    assert p["results_to_save"][0][1]["MeetDate"] == "2026-08-18"


def test_races_athletic_net_has_are_not_landed_again():
    evs = X.xcEvents(_load("summary_76201.json"))
    p = X.plan(276933, ALIVE, evs[:1], {2906134: _load("event_2906134.json")})
    ids = {int(t[0]["AthleteID"]) for t in p["results_to_save"] if t[0].get("AthleteID")}
    assert X.dropCovered(p, set()) == 0 and p["results_to_save"]
    assert X.dropCovered(p, ids) == 1
    assert not p["results_to_save"] and not p["meets_to_save"]


def test_athletic_nets_later_copy_wins():
    assert X.anetCopied({1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, set(range(1, 10)))
    assert not X.anetCopied({1, 2, 3, 4}, {1, 2})
    assert not X.anetCopied(set(), {1})


def test_scrape_local_dedupes_after_recheck():
    src = open(os.path.join(ROOT, "deploy", "scrape_local.sh"), encoding="utf-8").read()
    assert src.index("athleticlive_xc recheck") < src.index("athleticlive_xc dedupe") \
        < src.index("athleticlive cover")


def test_distance_from_the_results_document():
    assert X.docMeters({"_source": {"d": "5000", "me": "meter", "mt": "5000"}}) == 5000.0
    assert X.docMeters({"d": "1.7", "me": "mile"}) == 2735.9
    assert round(X.docMeters(_load("event_2906134.json"))) == 2736
    assert X.docMeters({"mt": "4000"}) == 4000.0
    assert X.docMeters({"d": "", "me": "meter"}) is None and X.docMeters(None) is None


def test_document_distance_beats_a_bare_label():
    evs = X.xcEvents(_load("summary_76201.json"))
    ev = dict(evs[0], ab="", name="Fastest JV Boys")
    doc = _load("event_2906134.json")
    p = X.plan(276933, ALIVE, [ev], {ev["id"]: doc})
    assert round(p["meets_to_save"][0][3]["Meters"]) == 2736


def test_track_rows_are_dated_by_their_race_too():
    alive = {"meet_date": "2026-05-08", "date_end": "2026-05-09"}
    ev = {"id": 1, "kind": "ind", "field": False, "round": None, "gender": "M",
          "start": "2026-05-09T16:00:00Z"}

    class _S:
        div_id, event_id, event_short, division = 7, 3, "1600", "Varsity"
    doc = _load("event_2906134.json")
    m2s, _a, res, _l = A.buildSave({"MeetDate": "2026-05-08"}, [ev], {1: _S()}, {1: doc}, alive=alive)
    assert res and all(t[1]["MeetDate"] == "2026-05-09" for t in res)
    assert X.eventDay is A.eventDay


def test_distance_units_spelled_out():
    assert X.docMeters({"d": "8", "me": "kilometres"}) == 8000.0
    assert X.docMeters({"d": "5000", "me": "metres"}) == 5000.0
