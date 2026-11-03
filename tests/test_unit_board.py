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


def test_names_and_spellings():
    assert U.prettyName("MOUNTAIN EAST") == "Mountain East"
    assert U.prettyName("IVY LEAGUE") == "Ivy League"
    assert U.prettyName("NESCAC") == "NESCAC" and U.prettyName("NCAA DIII") == "NCAA DIII"
    assert U.prettyName("CENTENNIAL") == "Centennial" and U.prettyName("A-10") == "A-10"
    assert U.normKey("HENLOPEN ATHLETIC") == U.normKey("HENLOPEN") == "henlopen"
    assert U.normKey("NIC-10 GIRLS") == U.normKey("NIC-10")


def test_board_merges_spellings_and_drops_the_other_level():
    rows = [_t("Hopkins", "MD", "CENTENNIAL", 113.0, [113] * 7),
            _t("Haverford", "PA", "CENTENNIAL ATHLETIC", 117.0, [117] * 7),
            _t("Del Val", "DE", "NEW CASTLE COUNTY", 90.0, [90] * 7),
            _t("Wilm", "DE", "NEW CASTLE COUNTY", 88.0, [88] * 7)]
    rows[0]["rank"], rows[1]["rank"] = 15, 6
    got = U.buildBoard(rows, "conference", 5, valid={"centennial": True})
    assert [u["name"] for u in got] == ["Centennial"] or [u["name"] for u in got] == ["Centennial Athletic"]
    u = got[0]
    assert u["n_teams"] == 2 and u["avg"] == 115.0 and u["top25"] == 2
    assert [m["school"] for m in u["members"]] == ["Haverford", "Hopkins"]
    U.sortBoard(got, "name")
    assert got[0]["rank"] == 1


def test_logo_match_second_dry_run():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import unit_logos as L
    assert not L.matches("NEC", "Northeast Conference men's soccer tournament",
                         "The Northeast Conference (NEC) men's soccer tournament is")
    assert not L.matches("MAAC", "Metro Conference", "... the Metro Atlantic Athletic Conference (MAAC) ...")
    assert L.matches("SEC", "Southeastern Conference", "The Southeastern Conference (SEC) is")
    assert L.matches("PSAC", "Pennsylvania State Athletic Conference", "The Pennsylvania State Athletic Conference (PSAC) is")
    assert not L.matches("MIAC", "1962 Minnesota Intercollegiate Athletic Conference football season", "(MIAC)")


def test_the_logo_file_not_the_map():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import unit_logos as L
    assert L.logoFile(["File:Atlantic 10 Conference map.svg", "File:Atlantic 10 Conference logo.svg"]) \
        == "File:Atlantic 10 Conference logo.svg"
    assert L.logoFile(["File:Atlantic 10 Conference map.svg"]) is None


def test_division_bodies():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import unit_logos as L
    assert L.orgFor("NCAA DIII") == "National Collegiate Athletic Association"
    assert L.orgFor("NAIA") and L.orgFor("NJCAA DII")
    assert L.orgFor("Mideast") is None


def test_logo_file_skips_wikipedia_icons_and_member_logos():
    """SEC was pinned to Wikimedia Commons' logo (owner, 2026-11-02)."""
    import unit_logos as L
    sec = ["File:Commons-logo.svg", "File:Alabama Crimson Tide logo.svg",
           "File:Southeastern Conference logo (2024).svg"]
    assert L.logoFile(sec, "Southeastern Conference") == "File:Southeastern Conference logo (2024).svg"
    assert L.logoFile(sec) != "File:Commons-logo.svg"
    assert L.logoFile(["File:Commons-logo.svg"], "Southeastern Conference") is None
    assert L.logoFile(["File:Commons-logo.svg", "File:Alabama logo.svg", "File:Auburn logo.svg"],
                      "Southeastern Conference") is None
    assert L.logoFile(["File:Wiktionary-logo-en-v2.svg", "File:NCAA logo.svg"],
                      "National Collegiate Athletic Association") == "File:NCAA logo.svg"
    assert L.logoFile(["File:Big Ten Conference logo.svg", "File:Big Ten wordmark.svg"],
                      "Big Ten Conference") == "File:Big Ten Conference logo.svg"


def test_infobox_logo_is_the_articles_own_answer():
    """Big Ten has a former, a 2012 and a current logo; ASUN's file is not
    named a logo at all. The infobox says which one it uses."""
    import unit_logos as L
    big10 = "{{Infobox collegiate athletic conference\n| name = Big Ten\n| logo = Big Ten Conference logo (2012).svg\n| logo_size = 200px\n| map = New Big 10 map.svg\n}}"
    assert L.infoboxLogo(big10) == "File:Big Ten Conference logo (2012).svg"
    assert L.infoboxLogo("| image = [[File:ASUN Primary Mark.png|200px]]") == "File:ASUN Primary Mark.png"
    assert L.infoboxLogo("| image = Atlantic 10 Conference map.svg") is None
    assert L.infoboxLogo("| logo = \n| image = Commons-logo.svg") is None
    assert L.infoboxLogo("no infobox") is None
    files = ["File:Big Ten Conference former logo.svg", "File:Big Ten Conference logo.svg",
             "File:Big Ten Conference logo (2012).svg"]
    assert L.logoFile(files, "Big Ten Conference") == "File:Big Ten Conference logo.svg"
    assert L.logoFile(["File:Open Access logo PLoS transparent.svg"], "ASUN Conference") is None


def test_an_event_label_filed_as_the_conference_is_that_conference():
    import unit_board as UB
    assert UB.normKey("GNAC COMBINED EVENTS") == UB.normKey("GNAC") == "gnac"
    assert UB.normKey("BIG EAST INDOOR") == UB.normKey("BIG EAST")


def test_a_conference_championship_names_its_conference():
    import unit_board as UB
    counts = {("conference", "BIG TEN", "", True): 14, ("conference", "BIG EAST", "", True): 2,
              ("league", "SUNKIST", "CA", False): 0}
    assert UB.meetUnitPick("Big Ten Cross Country Championships", counts, 14) == \
        ("conference", "BIG TEN", "", True)
    # named but only two of its schools ran: not the championship
    assert UB.meetUnitPick("Big East Championships", counts, 16) is None
    # an invitational the conference happens to fill a third of is not its meet
    assert UB.meetUnitPick("Big Ten Invitational", {("conference", "BIG TEN", "", True): 5}, 20) is None
    hs = {("league", "SUNKIST", "CA", False): 6, ("conference", "CIF SS", "CA", False): 9}
    assert UB.meetUnitPick("Sunkist League Finals", hs, 8) == ("league", "SUNKIST", "CA", False)
    assert UB.meetUnitPick("", hs, 8) is None


def test_chip_logo_is_only_a_stored_logo(monkeypatch):
    import unit_board as UB
    monkeypatch.setattr(UB, "storedLogoUrl", lambda *a, **k: None)
    assert str(UB.chipLogo("conference", "BIG TEN")) == ""
    monkeypatch.setattr(UB, "storedLogoUrl", lambda *a, **k: "/img/school/x.png")
    assert 'class="unit-chip-logo"' in str(UB.chipLogo("conference", "BIG TEN"))
    assert str(UB.chipLogo("county", "X")) == ""


def test_a_logo_stored_under_another_spelling_is_found(monkeypatch):
    import school_logo
    import unit_board as UB
    m = {"unit:conference:BIG TEN": [("", "abcd1234", "/x.png", "")]}
    monkeypatch.setattr(school_logo, "_CRESTS", {"loaded": True, "map": m, "factory": None, "retry_at": 0})
    assert UB._aliases()[("conference", "big ten")] == "BIG TEN"


def test_meet_logo_reads_the_name_strictly():
    import unit_board as UB
    keys = {("conference", "sec"): "SEC", ("conference", "southern"): "SOUTHERN",
            ("conference", "big ten"): "BIG TEN", ("conference", "big east"): "BIG EAST",
            ("league", "sunkist"): "SUNKIST", ("division", "ncaa di"): "NCAA DI"}
    P = UB.meetLogoPick
    assert P("2026 SEC Cross Country Championships", keys) == ("conference", "SEC")
    assert P("Southern Conference Championships", keys) == ("conference", "SOUTHERN")
    assert P("Southern Indiana Championships", keys) is None       # a word, not SoCon
    assert P("Big Ten Championships", keys) == ("conference", "BIG TEN")
    assert P("Big Ten Invitational", keys) is None                 # not its championship
    assert P("Sunkist League Finals", keys) == ("league", "SUNKIST")
    assert P("NCAA DI Championships", keys) is None                # divisions are not meets' marks
    assert P("", keys) is None


def test_meet_logo_markup_only_with_a_stored_logo(monkeypatch):
    import unit_board as UB
    monkeypatch.setattr(UB, "_aliases", lambda: {("conference", "sec"): "SEC"})
    monkeypatch.setattr(UB, "storedLogoUrl", lambda *a, **k: "/img/school/u.png")
    assert 'class="unit-chip-logo"' in str(UB.meetLogo("SEC Championships"))
    assert str(UB.meetLogo("Harvard Invitational")) == ""
    monkeypatch.setattr(UB, "storedLogoUrl", lambda *a, **k: None)
    assert str(UB.meetLogo("SEC Championships")) == ""   # a fresh key map, a fresh cache


def test_a_college_chip_links_to_the_college_board():
    """Amherst (MA)'s NESCAC chip linked to pool=hs_m (owner, 2026-11-03)."""
    import unit_board as UB
    assert UB.chipHref("division", "NCAA DIII", "MA", False) == "/unit/division/NCAA%20DIII?pool=college_m"
    assert UB.chipHref("conference", "NESCAC", "MA", True) == "/unit/conference/NESCAC?pool=college_m"
    src = open(os.path.join(os.path.dirname(__file__), "..", "racecast", "school_units.py")).read()
    body = src[src.index("def unitsForPerson"):]
    assert '"college": is_college' in body[:6000]


def test_known_conferences_are_named_outright():
    import unit_logos as L
    assert L.knownTitle("ACC") == "Atlantic Coast Conference"
    assert L.knownTitle("A10 XC") == L.knownTitle("A-10") == "Atlantic 10 Conference"
    assert L.knownTitle("MTN WEST") == "Mountain West Conference"
    assert L.knownTitle("NORTHEAST-10") == L.knownTitle("NE10") == "Northeast-10 Conference"
    assert L.knownTitle("CONFERENCE USA") == "Conference USA"
    assert L.knownTitle("PAC") is None and L.knownTitle("MIDWEST") is None


def test_series_meets():
    import unit_board as UB
    assert UB.seriesOf("NXN Nike Cross Nationals") == "NXN"
    assert UB.seriesOf("NXR South Regional Championships") == "NXR SOUTH"
    assert UB.seriesOf("Foot Locker Cross Country South Regional Championships") == "FOOT LOCKER"
    assert UB.seriesOf("Footlocker Nationals") == "FOOT LOCKER"
    assert UB.seriesOf("Dallas Jesuit Classic") is None


def test_one_conference_under_its_spellings():
    """owner, 2026-11-03: "two MTN wests"; Conference USA as C-USA and USA."""
    import unit_board as UB
    assert UB.normKey("MTN WEST") == UB.normKey("Mountain West") == UB.normKey("MW")
    assert UB.normKey("C-USA") == UB.normKey("USA") == UB.normKey("Conference USA")
    assert UB.normKey("A10 XC") == UB.normKey("A-10")
    assert UB.normKey("NE10") == UB.normKey("NORTHEAST-10")


def test_nxr_regions_and_the_repo_marks():
    import unit_board as UB
    import unit_logos as L
    assert UB.seriesOf("NXR Southeast Regional Championships") == "NXR SOUTHEAST"
    assert UB.seriesOf("NXR South Regional Championships") == "NXR SOUTH"
    assert UB.seriesOf("Nike Cross Regionals New York") == "NXR NEW YORK"
    assert UB.seriesOf("NXR Heartland") == "NXR HEARTLAND"
    files = L.seriesFiles()
    assert "NXN" in files and "NXR NEW YORK" in files


def test_state_flags():
    import unit_board as UB
    import unit_logos as L
    assert len(L.STATE_FLAGS) == 51 and L.STATE_FLAGS["GA"] == "Flag of Georgia (U.S. state).svg"
    assert UB.logoKey("state", "ma") == "unit:state:MA"
