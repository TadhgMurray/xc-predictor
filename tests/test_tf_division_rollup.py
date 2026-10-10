"""
tf_points.scoreMeet standings by division GROUP (sweep 2026-10-10, A11):
varsity-level divisions roll into one standings per gender and come first
(primary); JV / F-S / MS / Para keep their own; unrecognised labels stay
apart. cards.meetTfTeams takes the primary standings.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

from tf_points import scoreMeet                                 # noqa: E402

_ID = [0]


def _row(division, event, school, secs, gender="M", div_id=1, event_id=1):
    _ID[0] += 1
    return {"result_id": _ID[0], "div_id": div_id, "event_id": event_id,
            "event_short": event, "division": division, "school": school,
            "athlete_name": f"A{_ID[0]}", "person_id": _ID[0],
            "gender": gender, "time_seconds": secs, "mark": None,
            "result_kind": "time", "is_field": 0, "is_relay": 0,
            "distance_meters": None}


def _teams(div, g="M"):
    return {t["school"]: t["points"] for t in div["teams"][g]}


def test_varsity_labels_roll_into_one_standings():
    rows = [
        # one 1600 split across "Seeded" and "Unseeded" sections: one field
        _row("Seeded", "Boys 1600", "Alpha", 260, div_id=1, event_id=1),
        _row("Seeded", "Boys 1600", "Beta", 262, div_id=1, event_id=1),
        _row("Unseeded", "Boys 1600", "Gamma", 261, div_id=2, event_id=2),
        # an 800 filed under "Open"
        _row("Open", "Boys 800", "Beta", 120, div_id=3, event_id=3),
        _row("Open", "Boys 800", "Alpha", 121, div_id=3, event_id=3),
    ]
    out = scoreMeet(rows)
    assert len(out["divisions"]) == 1
    d = out["divisions"][0]
    assert d["group"] == "varsity" and d["primary"] is True
    assert d["name"] == "Varsity"
    assert d["labels"] == ["Open", "Seeded", "Unseeded"]
    # 1600 as ONE field: Alpha 10, Gamma 8, Beta 6; 800: Beta 10, Alpha 8
    assert _teams(d) == {"Alpha": 18.0, "Beta": 16.0, "Gamma": 8.0}
    assert len([e for e in d["events"] if e["gender"] == "M"]) == 2


def test_levels_keep_their_own_standings_varsity_first():
    rows = [
        _row("JV", "Boys 1600", "Alpha", 280, div_id=5, event_id=5),
        _row("Frosh/Soph", "Boys 1600", "Beta", 290, div_id=6, event_id=6),
        _row("Middle School", "Boys 1600", "Gamma", 330, div_id=7, event_id=7),
        _row("Para", "Boys 100", "Delta", 15, div_id=8, event_id=8),
        _row("Gold", "Boys 1600", "Eps", 300, div_id=9, event_id=9),
        _row("Varsity", "Boys 1600", "Zeta", 260, div_id=1, event_id=1),
        _row("", "Boys 3200", "Zeta", 560, div_id=2, event_id=2),
    ]
    out = scoreMeet(rows)
    groups = [d["group"] for d in out["divisions"]]
    assert groups == ["varsity", "jv", "fs", "ms", "para", "other:gold"]
    assert [d["primary"] for d in out["divisions"]] == [True] + [False] * 5
    v = out["divisions"][0]
    assert v["name"] == "Varsity"           # blank + "Varsity": the one label
    assert _teams(v) == {"Zeta": 20.0}
    assert out["divisions"][1]["name"] == "JV"
    assert out["divisions"][5]["name"] == "Gold"


def test_single_blank_division_is_all_divisions():
    out = scoreMeet([_row("", "Boys 1600", "Alpha", 260)])
    assert out["divisions"][0]["name"] == "All divisions"
    assert out["divisions"][0]["primary"] is True


def test_unrecognised_labels_never_merge():
    rows = [_row("Gold", "Boys 1600", "Alpha", 260, div_id=1, event_id=1),
            _row("Silver", "Boys 1600", "Beta", 262, div_id=2, event_id=2)]
    out = scoreMeet(rows)
    assert len(out["divisions"]) == 2
    assert all(_teams(d) in ({"Alpha": 10.0}, {"Beta": 10.0})
               for d in out["divisions"])


def test_enroute_still_never_scores():
    rows = [_row("Varsity", "Boys 1600", "Alpha", 260, div_id=1, event_id=1),
            _row("EnRoute", "Boys 1500", "Beta", 240, div_id=2, event_id=2)]
    out = scoreMeet(rows)
    en = [d for d in out["divisions"] if d["group"] != "varsity"][0]
    assert all(t["points"] == 0 for t in en["teams"]["M"])
