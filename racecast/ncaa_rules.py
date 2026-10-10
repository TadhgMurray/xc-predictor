# Project: xc-predictor / racecast
# File:    ncaa_rules.py
# Purpose: the NCAA cross country championship selection rules, per
#          division, as data (owner approved 2026-10-10: college nationals
#          projections). build_ncaa_projection.py and ncaa_select.py read
#          this; nothing else in the project restates a rule.
#
# ★ SOURCES, READ 2026-10-10 (the PDFs are on ncaaorg.s3.amazonaws.com under
#   championships/sports/crosstrack/<d1|d2|d3>/crosscountry/):
#     D1   2026-27D1XCC_PrechampionshipsManual.pdf   -- the 2026 season's own
#     D2   2024-25D2XCC_PreChampsManual.pdf          -- the newest D2 manual
#          that could be found; 2025-26D2XCC_ParticipantsManualFinals.pdf
#          repeats its counts (34 teams, top three per region, ten
#          at-large, top five / next eight individuals) for 2025. NO
#          2026-27 D2 manual was found.
#     D3   2026-27D3XCC_PreChampsManual.pdf          -- the 2026 season's own
#   Every number below cites its line. `confidence` says how sure:
#     "manual"   read off the season's own manual
#     "older"    read off an older manual; the rule may have moved since
#     "derived"  computed from a manual's formula (a date), not printed
#     "model"    OUR approximation of something the manual leaves to the
#                committee's judgment -- the page says so where it shows it
#
# ⚠ WHAT IS NOT MODELLED, AND WHERE IT WOULD BITE:
#   - D1 criterion 5 ("top individuals on the teams under consideration ...
#     as determined by the committee") is judgment; a tie that survives
#     criteria 1-4 falls to our projected team strength.
#   - D1's push needs the committee's "permanently blocking" call; see
#     ncaa_select.kolasSelect for the rule we apply in its place.
#   - D2's criterion 4 and D3's whole at-large list are committee judgment
#     over listed criteria; ncaa_select says how each is approximated.
#   - Wins from meets not yet run (conference meets, the rest of the
#     window) cannot be counted: early in the season every team's wins are
#     low and the at-large order is mostly the regional order.
import datetime

# The divisions as athlete_season / team_season spell them (the census in
# scripts/check_school_units.py writes "NCAA D" + roman), and the URL slug.
DIVISIONS = ("d1", "d2", "d3")
UNIT = {"d1": "NCAA DI", "d2": "NCAA DII", "d3": "NCAA DIII"}
LABEL = {"d1": "Division I", "d2": "Division II", "d3": "Division III"}
GENDERS = {"men": ("M", "college_m", "Men"), "women": ("F", "college_f", "Women")}


def _d(s):
    return datetime.date.fromisoformat(s)


RULES = {
    # ------------------------------------------------------------------ D1
    "d1": {
        "season": 2026,
        "source": "NCAA 2026-27 Division I Cross Country Pre-Championships Manual",
        "confidence": "manual",
        # "Great Lakes Region ... West Region" -- CHAMPIONSHIP DATES AND SITES
        "regions": ["Great Lakes", "Mid-Atlantic", "Midwest", "Mountain", "Northeast",
                    "South", "South Central", "Southeast", "West"],
        "region_aliases": {},
        # "Friday, Nov. 13 NCAA Division I ... Regional Championships";
        # "Nov. 21, 2026 - NCAA Division I ... Championships" (Terre Haute)
        "regional_date": _d("2026-11-13"),
        "nationals_date": _d("2026-11-21"),
        "nationals_site": "LaVern Gibson Championship Cross Country Course, Terre Haute, Ind.",
        # Section 2-1: "a maximum of 32 (five-to-seven-person) teams and 38
        # individuals per gender. Eighteen teams automatically qualify ... top
        # two teams at any of the nine regional races. The remaining 14
        # at-large teams ..."
        "teams_total": 32,
        "auto_per_region": 2,
        "at_large_teams": 14,
        "method": "kolas",
        # Section 2-4 criterion 2a: wins "between the ninth weekend before the
        # NCAA regional meet (i.e., Friday, Saturday and Sunday, Sept. 11-13,
        # 2026) and the conclusion of NCAA Regional Cross Country
        # Championships" -- the regionals THEMSELVES are inside the window.
        "window_start": _d("2026-09-11"),
        "window_includes_regionals": True,
        # "the course length must not be less than 75% of the championship
        # race distance"; "Men's races will be 10,000 meters ... Women's races
        # will be 6,000 meters"
        "champ_distance": {"M": 10000, "F": 6000},
        "min_distance_fraction": 0.75,
        # "A Teams/B Teams": wins only against an opponent's "A" team -- "if
        # four or more of those individuals who compete at the regional
        # qualifying meet started the regular-season race". The winner may be
        # A or B ("An institution's 'A' team or 'B' team can earn at-large
        # points for having beaten another institution's 'A' Team").
        "a_team_min_starters": 4,
        "a_team_applies_to": "loser",
        # Section 2-2: "If one race is run, only one set of results should be
        # reported ... it can change who beats who" -- a win is read off the
        # whole race's team standings, every team in it scoring.
        "h2h_scoring": "race",
        # "teams will be given credit ... for beating a team that starts as a
        # team but does not finish as a team"
        "dnf_team_is_beaten": True,
        # Individuals: "the first four student-athletes, not on a qualifying
        # team, to finish will be automatic qualifiers ... must finish in the
        # top 25 within their region"; "two at-large individuals by
        # identifying the highest nonqualifying individual finishers at the
        # regional meets" (also top 25), more when regions fall short of four,
        # "to maintain the maximum number of 255 participants per gender".
        # ! 255 is the manual's own figure beside "32 teams ... = 224,
        #   Individuals = 38, Total = 262" in the same section; the two do not
        #   agree. We keep 38 individuals (the format paragraph and the total).
        "ind_auto_per_region": 4,
        "ind_auto_top": 25,
        "ind_at_large": 2,
        "ind_total": 38,
        "ind_method": "d1",
        "notes": [
            "Kolas wins (\"points\"): one per win, at a race inside the window, over a team "
            "already in the field (the 18 automatic qualifiers and every at-large team picked so "
            "far); totals are recounted after every pick.",
            "Under consideration: the highest regional finisher not yet in from each region; a "
            "team cannot jump a team that beat it at its own regional.",
            "Ties: head-to-head, then record against common Division I opponents, then regional "
            "place, then the point gap to that region's second-place team.",
        ],
    },
    # ------------------------------------------------------------------ D2
    "d2": {
        "season": 2026,
        "source": "NCAA 2024-25 Division II Cross Country Pre-Championships Manual "
                  "(no 2026-27 edition found)",
        "confidence": "older",
        # Appendix A region headers / "Atlantic Region ... West Region"
        "regions": ["Atlantic", "Central", "East", "Midwest", "South", "South Central",
                    "Southeast", "West"],
        "region_aliases": {},
        # ⚠ DERIVED, NOT PRINTED. DATE FORMULA: "regional qualifying meets are
        #   held on a Saturday two weeks before the championships. The
        #   championships finals are held the Saturday before Thanksgiving"
        #   (a Festival year differs; 2025 was one, finals Nov. 29). 2026's
        #   Thanksgiving is Nov. 26, so finals Nov. 21 and regionals Nov. 7.
        "regional_date": _d("2026-11-07"),
        "nationals_date": _d("2026-11-21"),
        "dates_confidence": "derived",
        "nationals_site": None,
        # Appendix B: "A total of 34 teams will be selected"; "The top three
        # teams from each regional meet will automatically advance (24
        # teams)"; "Ten at-large teams"
        "teams_total": 34,
        "auto_per_region": 3,
        "at_large_teams": 10,
        "method": "d2",
        # "Late-season Performance -- ... meets starting with the date that is
        # seven weeks (51 days) out from the NCAA Division II Regional
        # Championships ... and concluding after the culmination of the NCAA
        # Division II Regional Championships"
        "window_days": 51,
        "window_includes_regionals": True,
        # "minimum race distance of 5,000 meters for women and 7,000 meters
        # for men"
        "min_distance": {"M": 7000, "F": 5000},
        # "An 'A-Team' is defined as having at least five of the members of
        # the seven that represent each institution at their NCAA Regional
        # ... No 'B' team results can count against or help a team"
        "a_team_min_starters": 5,
        "a_team_applies_to": "both",
        # "Breaking Team Ties ... a team could lose to another team in a large
        # meet with various non-Division II schools factored into the
        # scoring, but still beat them head-to-head if the two teams were
        # scored only against each other as a dual meet."
        # ⚠ The manual says this under TIES; we score every D2 head-to-head
        #   as that dual (confidence: model).
        "h2h_scoring": "dual",
        "dnf_team_is_beaten": False,
        # "1. The top two individuals who are not part of a qualifying team
        # will automatically advance (16 individuals). 2. All individuals who
        # finish in the top five at the regional meet and are not part of a
        # qualifying team ... 3. The next eight individuals will be selected
        # at-large" by "[# of team qualifiers from the region] / [Individual
        # regional placing]"
        "ind_auto_per_region": 2,
        "ind_auto_top_all": 5,
        "ind_at_large": 8,
        "ind_method": "d2",
        "notes": [
            "Under consideration: the highest regional finisher not yet in from each of the "
            "eight regions. One is picked per round and that region's next team moves up.",
            "A team with a losing record against another team under consideration -- head to "
            "head, plus results through common opponents (\"A beat C, C beat B\") -- cannot be "
            "the pick. Head-to-head here is the two teams scored as a dual meet.",
            "Then the regional point-gap ratio (score of the team just ahead, if it went in "
            "at-large, over this team's score), then the committee's judgment.",
        ],
    },
    # ------------------------------------------------------------------ D3
    "d3": {
        "season": 2026,
        "source": "NCAA 2026-27 Division III Cross Country Pre-Championships Manual",
        "confidence": "manual",
        # Men's/Women's Sponsorship headers "REGION I/NORTHEAST" ... "REGION
        # X/WEST". ! The WOMEN'S list heads region I "REGION I/EAST"; the
        #   committee roster and the men's list say NORTHEAST. One region.
        "regions": ["Northeast", "Mideast", "Niagara", "Mid-Atlantic", "Metro",
                    "Great Lakes", "South", "North", "Midwest", "West"],
        "region_aliases": {"East": "Northeast"},
        # ⚠ TEN REGIONS ARE NEW. The data's regions were voted by past
        #   regionals under the old alignment, whose names overlap the new
        #   ones (Great Lakes, Mideast, Midwest, West ...) with different
        #   members -- so for D3 a data region is NOT trusted: the manual's
        #   list or the override file places a team, or nothing does.
        "data_regions": False,
        # "All dates for regionals are Nov. 14"; "Championships ... Nov. 21 at
        # Bill Huyck Championship Cross Country Course"
        "regional_date": _d("2026-11-14"),
        "nationals_date": _d("2026-11-21"),
        "nationals_site": "Bill Huyck Championship Cross Country Course",
        # Section 2-1: "Teams (32 per gender) ... 10 teams will automatically
        # qualify (one per region ...). 22 at-large teams"
        "teams_total": 32,
        "auto_per_region": 1,
        "at_large_teams": 22,
        "method": "d3",
        # "late-season performance (a team's meet that occurs on or after
        # Sept. 18, including regionals)"
        "window_start": _d("2026-09-18"),
        "window_includes_regionals": True,
        # "The race length must be at least 5 kilometers for the women, and 7
        # kilometers for the men."
        "min_distance": {"M": 7000, "F": 5000},
        # "A team runs at least five of its student-athletes who will compete
        # for their institution at regionals."
        "a_team_min_starters": 5,
        "a_team_applies_to": "both",
        # "The meet must be scored in accordance with the scoring procedures
        # in the NCAA ... Rules Book" -- the whole race, as D1.
        "h2h_scoring": "race",
        "dnf_team_is_beaten": False,
        # "70 individuals will automatically qualify (top seven per region
        # ...); Remove the team championship qualifiers from regionals and
        # renumber the remaining individuals"
        "ind_auto_per_region": 7,
        "ind_at_large": 0,
        "ind_method": "d3",
        "notes": [
            "The ten regional winners are in. The committee picks 22 more, and a team can never "
            "go in ahead of a team that beat it at its own regional.",
            "The listed criteria are regional place, head-to-head with teams already in the "
            "field and other at-large candidates, common opponents, and the point gaps at "
            "regionals and late-season meets. The manual gives no formula: we use head-to-head "
            "wins over teams already in, then head-to-head among tied teams, then common "
            "opponents, then regional place.",
        ],
    },
}


def rules(division):
    return RULES[division]


def window(division):
    """(first day, last day) of the results that count for at-large
    selection. The last day is the regionals."""
    r = RULES[division]
    hi = r["regional_date"]
    if "window_start" in r:
        return r["window_start"], hi
    return hi - datetime.timedelta(days=r["window_days"]), hi


def minDistance(division, gender):
    """Shortest race (metres) whose results count, or 0."""
    r = RULES[division]
    if "min_distance" in r:
        return r["min_distance"][gender]
    if "champ_distance" in r:
        return r["champ_distance"][gender] * r["min_distance_fraction"]
    return 0


def regionKey(name):
    """Letters only, upper case: 'Mid-Atlantic', 'MID ATLANTIC' and
    'MIDATLANTIC' are one key."""
    return "".join(ch for ch in (name or "").upper() if "A" <= ch <= "Z")


def canonicalRegion(division, name):
    """The division's own spelling of a region, or None when `name` is not
    one of its regions (an old alignment's name, a typo, another division's
    region)."""
    if not name:
        return None
    r = RULES[division]
    k = regionKey(name)
    for alias, canon in r["region_aliases"].items():
        if regionKey(alias) == k:
            return canon
    for reg in r["regions"]:
        if regionKey(reg) == k:
            return reg
    return None


def nIndividualsMax(division):
    """The individual quota the page states (None when the rules set only
    a floor)."""
    r = RULES[division]
    if r.get("ind_total"):
        return r["ind_total"]
    if r["ind_method"] == "d3":
        return r["ind_auto_per_region"] * len(r["regions"])
    return None


def check():
    """The arithmetic the manuals state, asserted (tests call this)."""
    for div, r in RULES.items():
        auto = r["auto_per_region"] * len(r["regions"])
        assert auto + r["at_large_teams"] == r["teams_total"], div
    assert len(RULES["d1"]["regions"]) == 9
    assert len(RULES["d2"]["regions"]) == 8
    assert len(RULES["d3"]["regions"]) == 10
    return True
