"""Conferences, leagues, sections and divisions ranked against each other
(owner, 2026-11-02)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import unit_board as U                                            # noqa: E402


def _t(school, state, conf, avg, ratings):
    return {"school": school, "state": state, "conference": conf, "league": conf,
            "top5_mean": avg, "ratings": ratings}


ROWS = [
    _t("Williams", "MA", "NESCAC", 120.0, [125, 122, 120, 118, 115, 110, 108]),
    _t("Middlebury", "VT", "NESCAC", 118.0, [123, 120, 118, 116, 113, 109, 105]),
    _t("Bates", "ME", "NESCAC", 110.0, [114, 112, 110, 108, 106, 100, 99]),
    _t("WashU", "MO", "UAA", 121.0, [126, 124, 121, 118, 116, 112, 110]),
    _t("Chicago", "IL", "UAA", 112.0, [118, 114, 111, 110, 107, 104, 101]),
    _t("Lonely", "OR", "Solo", 130.0, [131, 130, 130, 129, 128, 120, 119]),
]


def test_top_teams_average_and_the_race():
    got = U.rankUnits(ROWS, "conference", n=2)
    names = [u["unit"] for u in got]
    assert names == ["NESCAC", "UAA"]                  # a one-team unit is not a unit
    nescac = got[0]
    assert nescac["top_avg"] == 119.0 and nescac["top_n"] == 2 and nescac["n_teams"] == 3
    assert nescac["best_team"] == "Williams"
    assert {u["raced_rank"] for u in got} == {1, 2}
    assert all("ratings" not in u for u in got)


def test_raced_sort():
    got = U.rankUnits(ROWS, "conference", n=3)
    by = U.sortUnits(got, "raced")
    assert [u["raced_rank"] for u in by] == [1, 2]


def test_high_school_units_are_per_state():
    rows = [_t("A", "CA", "Central", 100, [100] * 7), _t("B", "CA", "Central", 101, [101] * 7),
            _t("C", "OH", "Central", 102, [102] * 7), _t("D", "OH", "Central", 103, [103] * 7)]
    got = U.rankUnits(rows, "league")
    assert sorted((u["unit"], u["state"]) for u in got) == [("Central", "CA"), ("Central", "OH")]
    assert U.unitKey("conference", "NESCAC", "MA") == ("NESCAC", None)


def test_badge_initials():
    assert U.initials("Middlesex League") == "ML"
    assert U.initials("NESCAC") == "NESC"
    assert U.initials("NCAA DIII") == "DIII"
    assert U.initials("Big Ten") == "BT"
    svg = U.badgeSvg("NESCAC")
    assert svg.startswith("<svg") and ">NESC</text>" in svg
    assert U.badgeSvg("<b>x</b>").count("<b>") == 0          # only letters reach the markup


def test_links():
    assert U.unitHref("league", "Middlesex League", "MA", "hs_m", "XC") == \
        "/unit/league/Middlesex%20League?state=MA&pool=hs_m&sport=xc"
    assert U.unitHref("conference", "NESCAC", "MA", "college_m") == "/unit/conference/NESCAC?pool=college_m"
    assert U.chipHref("county", "Essex", "MA") is None
    assert U.chipHref("league", "X", None) is None
    assert U.chipHref("conference", "UAA", None).startswith("/unit/conference/UAA")


def test_within_filter_is_whitelisted():
    args = {}
    w = U._where("MA", {"division": "NCAA DIII", "evil; drop": "x"}, args)
    assert "upper(division) = upper(%(w0)s)" in w and "evil" not in w
    assert args == {"state": "MA", "w0": "NCAA DIII"}


def test_own_logo_first_then_the_badge(monkeypatch):
    import school_logo
    assert U.logoKey("conference", "nescac") == "unit:conference:NESCAC"
    monkeypatch.setattr(school_logo, "crestUrl", lambda *a, **k: None)
    assert U.unitMark("conference", "NESCAC").startswith("<svg")
    monkeypatch.setattr(school_logo, "crestUrl",
                        lambda key, state, px=None: f"/img/school/{key}.png?v=1")
    m = U.unitMark("conference", "NESCAC")
    assert m.startswith("<img") and "unit:conference:NESCAC" in m


def test_wikipedia_titles():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import unit_logos as L
    assert L.looksLikeConference("New England Small College Athletic Conference")
    assert L.looksLikeConference("Mountain East Conference")
    assert not L.looksLikeConference("Williams College")


def test_logo_match_is_strict():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import unit_logos as L
    assert L.matches("NEC", "Northeast Conference", "The Northeast Conference (NEC) is a collegiate")
    assert L.matches("A-10", "Atlantic 10 Conference", "The Atlantic 10 Conference (A-10) is")
    assert not L.matches("A-10", "Pac-12 Conference", "The Pac-12 Conference is")
    assert not L.matches("MAC", "Great Midwest Athletic Conference", "The Great Midwest Athletic Conference (G-MAC) is")
    assert not L.matches("EAST", "Big East Conference", "The Big East Conference is")
    assert L.matches("IVY LEAGUE", "Ivy League", "")
    assert L.matches("MIDDLE ATLANTIC", "Middle Atlantic Conferences", "")
    assert not L.matches("EMPIRE 8", "Presidents' Athletic Conference", "The PAC is")
