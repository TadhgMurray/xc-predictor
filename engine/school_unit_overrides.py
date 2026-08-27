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
    },
}

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
