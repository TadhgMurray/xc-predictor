# Project: xc-predictor / engine
# File:    school_unit_overrides.py
# Purpose: Hand corrections to the school-unit census, in the same spirit
#          as corrections.py: the parser is a good witness, not a judge.
#
# ★ WHEN TO ADD ONE. Two cases, and they are different:
#
#   1. ALIASES -- the corpus spells one unit two ways and neither is
#      wrong. "ISAL" and "INDEPENDENT SCHOOL ATHLETIC LEAGUE" are the
#      same league; "NCS" and "NORTH COAST" are the same section. Put
#      the SHORT form on the right and every spelling on the left, in
#      _ALIAS, and every school using either lands on one unit.
#
#   2. VERDICTS -- the census got a school wrong and eyes overrule it.
#      Put it in _SCHOOL, keyed (school, state). Only the kinds you
#      name are replaced; the rest of the row still comes from votes.
#
# ! An alias is almost always the better fix. It repairs every school at
#   once and survives a rescrape; a per-school verdict repairs one and
#   has to be revisited when the school moves.

# ---- 1. aliases: many spellings, one unit ---------------------------- #
_ALIAS = {
    "league": {
        "INDEPENDENT SCHOOL ATHLETIC": "ISAL",
        "AUSTIN INTER-PAROCHIAL": "AIPL",
        "CHICAGO PUBLIC": "CPS",
    },
    "section": {
        "NORTH COAST": "NCS",
        "CENTRAL COAST": "CCS",
        "SAC JOAQUIN": "SJS",
        "SAC-JOAQUIN": "SJS",
        "SOUTHERN": "CIF-SS",
        "SAN DIEGO": "SDS",
    },
    "conference": {
        "SO CAL JC": "CALIFORNIA STATE JUNIOR COLLEGE",
        # ★ THE NAMED FORM AND THE ACRONYM ARE ONE CONFERENCE (owner,
        #   2026-09-02: SEC). tfrrs writes both, season by season.
        "SOUTHEASTERN": "SEC",
        "ATLANTIC COAST": "ACC",
        "BIG 10": "BIG TEN",
        "PAC 12": "PAC-12", "PACIFIC-12": "PAC-12", "PACIFIC 12": "PAC-12",
        # ! NOT "PAC" -> "PAC-12": the Presidents' Athletic Conference (DIII)
        #   is spelled PAC and is a real, different conference.
        "AMERICAN ATHLETIC": "AAC", "AMERICAN": "AAC",
        "CONFERENCE USA": "C-USA", "CUSA": "C-USA",
        "MID-AMERICAN": "MAC",
        "SOUTHWESTERN ATHLETIC": "SWAC",
        "MID-EASTERN ATHLETIC": "MEAC",
        "ATLANTIC 10": "A-10", "A10": "A-10",
        "MISSOURI VALLEY": "MVC",
        "WEST COAST": "WCC",
        "WESTERN ATHLETIC": "WAC",
        "COLONIAL ATHLETIC": "CAA", "COLONIAL": "CAA",
        "SOUTHERN": "SOCON",
        "OHIO VALLEY": "OVC",
        "METRO ATLANTIC ATHLETIC": "MAAC", "METRO ATLANTIC": "MAAC",
        "NORTHEAST": "NEC",
        "ATLANTIC SUN": "ASUN",
        "IVY": "IVY LEAGUE",
    },
}

# ---- 1b. the conferences we KNOW exist ------------------------------- #
# ★ A KNOWN CONFERENCE WINS ITS SCHOOL'S LATEST SEASON whenever it was
#   voted at all (check_school_units.current(prefer=...)). The census
#   counts attendance, and a meet named for its host -- "Arkansas State
#   Championships" -- draws more of a school's athletes than the SEC meet
#   it also attends; without this the host out-votes the conference. Names
#   here are the alias-resolved spellings. Not exhaustive on purpose: an
#   unknown name still wins when nothing known was voted.
KNOWN_CONFERENCES = frozenset({
    # NCAA DI
    "SEC", "ACC", "BIG TEN", "BIG 12", "PAC-12", "BIG EAST", "AAC", "C-USA",
    "MAC", "MOUNTAIN WEST", "SUN BELT", "WCC", "WAC", "BIG SKY", "MVC",
    "A-10", "IVY LEAGUE", "PATRIOT", "CAA", "SOCON", "OVC", "BIG SOUTH",
    "ASUN", "HORIZON", "SUMMIT", "MAAC", "NEC", "MEAC", "SWAC",
    "AMERICA EAST", "SOUTHLAND", "BIG WEST",
    # NCAA DII / DIII and NAIA, the ones the corpus spells consistently
    "NESCAC", "NEWMAC", "UAA", "CCIW", "WIAC", "MIAC", "NCAC", "OAC",
    "SCIAC", "CENTENNIAL", "LIBERTY", "LANDMARK", "SUNYAC", "NJAC",
    "MASCAC", "LITTLE EAST", "GNAC", "NWC", "SCAC", "ASC", "HCAC", "IIAC",
    "ARC", "MIAA", "GLIAC", "GLVC", "PSAC", "NE10", "CACC", "SAC", "PBC",
    "GSC", "LSC", "RMAC", "CCAA", "PACWEST", "GAC", "MIAA", "NSIC",
    "SSC", "CIAA", "SIAC", "HEART", "KCAC", "GPAC", "CROSSROADS", "MSC",
    "AII", "CASCADE", "FRONTIER", "SOONER", "RED RIVER", "AAC", "GSAC",
    "SUN", "WOLVERINE-HOOSIER", "CHICAGOLAND", "NAC", "AMCC", "SLIAC",
    "USA SOUTH", "ODAC", "CC", "MAC FREEDOM", "MAC COMMONWEALTH",
    "EMPIRE 8", "SKYLINE", "CUNYAC", "NECC", "CCC", "NAC", "MWC",
    "UMAC", "NACC", "SAA", "PAC", "AEC", "GNAC",
})


# ---- 2. per-school verdicts: eyes overrule the votes ----------------- #
#   (school, state) -> {kind: unit}.  Add a one-line WHY on every entry;
#   an override with no reason is indistinguishable from a typo later.
_SCHOOL = {
    # ("Some High School", "CA"): {"league": "EBAL"},   # moved 2025
}


def aliasFor(kind, unit):
    """The canonical spelling of one unit, or the unit unchanged."""
    return _ALIAS.get(kind, {}).get(unit, unit)


def overrideFor(school, state):
    """{kind: unit} the eyes have fixed for this school, or {}."""
    return _SCHOOL.get((school, (state or "").upper()), {})
