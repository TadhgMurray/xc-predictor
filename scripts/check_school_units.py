# Project: xc-predictor / scripts
# File:    check_school_units.py
# Purpose: Infer every school's league / section / district / class from the
#          championship meets it actually attends. Read-only census -- the
#          writer comes after this proves precision on real names.
#
#     python scripts/check_school_units.py                     # XC census
#     python scripts/check_school_units.py --sport TF
#     python scripts/check_school_units.py --school "De La Salle"
#     python scripts/check_school_units.py --unparsed           # worst names
#
# ★ THE LCD MEET IS THE ROSTER (owner's insight, 2026-08-27). Nobody has to
#   enter an invitational, but every program contests its lowest-common-
#   denominator championship -- league finals, district, sectional -- on the
#   way toward state/nationals. So membership is inferred ONLY from
#   late-season championship-gated meets: a school racing the "EBAL
#   Championships" is in the EBAL, full stop. Invitationals never vote.
#
# ★ DIV IS LOST ALONG THE WAY -- GUARD IT (owner's warning). The chain runs
#   league -> section(/div) -> state(/div), and the division token drops off
#   meet names unpredictably ("NCS Championships" one year, "NCS Division I
#   Championships" the next; sometimes it only survives in the RACE title).
#   Therefore: every unit KIND keeps its own independent vote counter, the
#   class/div token is harvested from meet name AND division title, votes
#   only ever ACCUMULATE (a bare name cannot blank an earlier div), and a
#   school whose class votes conflict is FLAGGED, never averaged.

import argparse
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                          # noqa: E402

# late-season windows: the championship chain toward state/nationals
_WINDOW = {"XC": (10, 12), "TF": (5, 6)}

# ---- the parser ------------------------------------------------------- #
# A name must pass the championship gate, then each detector extracts an
# independent (kind, unit) fact. Class/div is its own kind and is also
# probed on the division/race title.

_CHAMP_RX = re.compile(
    r"champ|meet of champions|finals?\b|\bstate meet\b|"
    # the qualifying rounds ARE the LCD meets in most states
    r"sectionals?\b|regionals?\b|districts?\b", re.I)
# invitationals, plus championship-NAMED meets that are not school units:
# club postseason (USATF/AAU Junior Olympics), shoe-company nationals,
# foreign systems (OFSAA/provincials, ekiden), regional all-comers
_NEVER_RX = re.compile(
    r"invit|classic|festival|preview|opener|relays\b|scrimmage|jamboree|"
    r"time trial|last chance|qualifier meet|carnival|series\b|"
    r"usatf|\baau\b|junior olympic|foot ?locker|\bnike\b|\bnxn\b|"
    r"new balance|adidas|runninglane|brooks\b|hoka\b|garrett companies|"
    r"mitca|ekiden|ofsaa|provincial|\byouth\b|xc town|festival of champions|"
    # ! "new england" killed the NCAA DIII New England REGIONAL along
    #   with the all-comers meet it was written for -- regionals vote
    r"new england(?!.*region)|all japan|\bbc hs\b|"
    # round 3 (2026-08-27 corpus): youth orgs, foreign systems, national
    # finals and regional all-comers that are not membership units.
    # NCAA NATIONALS excluded; NCAA REGIONALS still vote (region + class).
    r"\bcyo\b|insp?orts|mayor's cup|alberta|manitoba|canadian|toronto|"
    r"british columbia|neicaaa|track houston|"
    # coast all-comers, but never the West Coast CONFERENCE (a real league)
    r"\b(?:east|west) coast (?:track|champ|national)|"
    r"midwest meet of champions|larry steeb|honor roll|\busa junior\b|"
    r"\busa youth\b|\busa (?:outdoor|indoor|track|national)|"
    # round 4: club meets, coaches-assoc MoCs, more foreign, venue-named
    r"\bclub\b|\botca\b|mid-?east meet of champions|hay bale|"
    r"bc high school|saskatch|elysian park|"
    # round 5: NAIA/NJCAA/NIRCA NATIONALS (regionals still vote),
    # sub-varsity and grade-school meets, Kinney (old Foot Locker),
    # Ontario ROPSSAA, Fraser Valley (BC)
    r"\bnirca\b|"
    r"\bkinney\b|\bropssaa\b|fraser valley|\bfrosh\b|freshman|"
    r"\bjv\b|junior varsity|\bes[-/ ]?ms\b|elementary", re.I)

# ★ NATIONALS VOTE DIVISION, AND ONLY DIVISION (2026-08-27). A school at
#   the NCAA DIII meet IS DIII -- that is the least ambiguous division
#   evidence in the whole corpus, and excluding these names outright threw
#   it away. What nationals are NOT is a membership unit: they name no
#   conference and no region, so those kinds stay unvoted here.
#   The (?!.*region) lookaheads keep REGIONALS on the normal path, where
#   they vote region and division both.
_NATIONALS_RX = re.compile(
    r"ncaa\s+d\S*(?!.*region)|\bnaia\b(?!.*region)|"
    r"\bnjcaa\b(?!.*region)", re.I)

# class/div tokens, meet name or race title:  5A, AAA, Class B, Division
# III, D3, Group 2 (NJ), Open Division
_CLASS_RX = [
    re.compile(r"\b([1-9]-?A{1,4})\b"),
    # bare AA/AAA/AAAA (PA, TN, GA) -- two chars minimum, since a lone
    # "A" turns up inside too much else to trust
    re.compile(r"\b(A{2,4})\b"),
    # letters cover CT-style L/M/S/LL too; \b keeps "Class Championships"
    # from matching (no boundary three letters into "Championships")
    re.compile(r"\bclass\s+([A-Z]{1,3}|[1-9][A-D]?)\b", re.I),
    re.compile(r"\bdivision\s+(I{1,3}V?|VI?|[1-9]|One|Two|Three|Four|Five)\b",
               re.I),
    re.compile(r"\bD-?([1-5])\b"),
    re.compile(r"\bgroup\s+([1-4])\b", re.I),
    re.compile(r"\b(open)\s+division\b", re.I),
]

# unit detectors, most specific first. Each returns (kind, unit-string).
# "X League/Conference Championships" keeps X as the unit; a bare all-caps
# acronym before "Championships" (EBAL, WCAL, MVAL) is a league too.
_SECTION_ACRONYMS = {"NCS", "CCS", "CIF-SS", "CIFSS", "SJS", "SDS"}
# ! NOT a unit by itself: CIF is the state body, "STATE" is the state rule's
#   job, and a section acronym must not double-vote as a league.
_NOT_A_LEAGUE = _SECTION_ACRONYMS | {"CIF", "STATE", "NCAA", "NAIA", "NJCAA",
                                     "USATF", "AAU",
                                     # level words, not leagues
                                     "MIDDLE SCHOOL", "HIGH SCHOOL",
                                     "ELEMENTARY", "JUNIOR HIGH",
                                     # "Liberty League Cross Country Only"
                                     "CROSS COUNTRY ONLY", "CROSS COUNTRY",
                                     "TRACK AND FIELD", "TRACK & FIELD",
                                     # ECAC/IC4A run championships for many
                                     # conferences at once -- an organiser,
                                     # not a membership unit. It was voting
                                     # itself against Ivy, Patriot and MAAC.
                                     "ECAC", "IC4A", "ECAC/IC4A", "IC4A/ECAC",
                                     # generic, and everywhere
                                     "CITY", "COUNTY", "OPEN", "VARSITY"}

# a school DISTRICT running its own meet is not a league: FCPS (Frederick
# County Public Schools), LPS (Lincoln), LAUSD, CKSD, SKSD, DPS.
_DISTRICT_RX = re.compile(r"^[A-Z]{1,4}(?:PS|SD|USD|ISD)$")

# coaches associations run MoCs and invitationals; they are organisers,
# not leagues (NJCTC, MSCCA, TSTCA, KTCCCA, RGVCCCA ...)
_COACHES_RX = re.compile(r"^[A-Z]{2,6}(?:TCCCA|CCCA|CTCA|CTC|TCA|CCA)$")

# state athletic associations: an explicit list plus the suffix families
# (…SIAA, …PHSAA/PHAA, …SHSAA, …HSAA). Their bare championships and Meet
# of Champions ARE the state series (NJSIAA MoC, NYSPHSAA Champs).
_ASSOC_RX = re.compile(
    r"\b(?:[A-Z]{1,5}(?:SIAA|PHS?AA|SHSAA?|SHSL|HSAA|HSSA)|UIL|OSAA|WIAA|"
    r"GHSA|FHSAA|OHSAA|PIAA|VHSL|TSSAA|KHSAA|LHSAA|AHSAA|IHSAA?|MSHSL|"
    r"MHSAA|MSHSAA|MIAA|HHSAA|SDHSAA|NDHSAA|WVSSAC|SCHSL|NCHSAA|"
    r"NHIAA|DIAA|CIAC|ASAA|VISAA|NCSAA|SCISA|NYSAIS|MPSSAA)\b")

# US state names: the LAST-RESORT state rule ("Michigan Meet of Champions",
# "Nebraska Championship Meet") -- applied only when NO other unit matched,
# so "Mississippi Valley Conference" stays a league, never a state vote.
_STATE_NAME_RX = re.compile(
    r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|"
    r"Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
    r"Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
    r"Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|"
    r"New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|"
    r"Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
    r"South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
    r"West Virginia|Wisconsin|Wyoming)\b", re.I)

# ! FEED IS NOT LEVEL. Maryland, Florida and other HS state series ride
#   the tfrrs feed: an association acronym or an HS marker in the name
#   forces the HS parser regardless of feed.
_HS_MARK_RX = re.compile(
    r"high school|\bh\.?s\.?\b|middle school|\bm\.?s\.?\b|"
    r"\bclass\s+[A-Z0-9]|\bgroup\s+[0-9]|\b[1-6]A\b", re.I)


# ★ COLLEGE-NESS IS THE SCHOOL'S, NOT THE MEET NAME'S (owner's rule:
#   "decided by the school's pool"). ranking_results already carries the
#   engine's own pool verdict per row, so the level is READ rather than
#   guessed -- which is what stopped four Massachusetts high schools
#   (Bay State Conference) printing in the college table, and what puts
#   Tufts on the college parser instead of reading "Division III" as an
#   HS league named III with class 3.
#
#   Keyed (school, state) first because "Columbia" is both a university
#   and a high school; school-only is the fallback, and a school that
#   never reached a board falls through to the NAME heuristic below.
_LEVEL_MIN = 3                # below this many board rows, trust nothing


def _levelMaps(cur):
    cur.execute("SELECT to_regclass('ranking_results')")
    if cur.fetchone()[0] is None:
        return {}, {}
    cur.execute("""
        SELECT upper(TRIM(school)), upper(COALESCE(state, '')),
               count(*) FILTER (WHERE pool LIKE 'college!_%' ESCAPE '!'),
               count(*)
        FROM   ranking_results
        WHERE  COALESCE(TRIM(school), '') <> ''
        GROUP  BY 1, 2""")
    pair, solo = {}, {}
    for school, st, coll, n in cur.fetchall():
        pair[(school, st)] = (coll, n)
        c0, n0 = solo.get(school, (0, 0))
        solo[school] = (c0 + coll, n0 + n)
    return pair, solo


def _schoolIsCollege(school, st, maps):
    """True/False from the school's own pool mix, or None if unknown."""
    pair, solo = maps
    key = upper = (school or "").strip().upper()
    for got in (pair.get((upper, (st or "").upper())), solo.get(key)):
        if got and got[1] >= _LEVEL_MIN:
            return got[0] * 2 >= got[1]
    return None


# Canadian provinces (and other non-US codes that ride the feeds). Their
# leagues are real, but they are not the unit system this table models,
# and they were voting HWIAC against SOSSA on Ontario schools forever.
_FOREIGN_ST = {"ON", "BC", "AB", "SK", "MB", "QC", "NS", "NB", "NL",
               "PE", "YT", "NT", "NU"}

_STATE_CODE = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI",
    "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
    "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND",
    "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY"}


def _isCollegeName(name):
    n = name or ""
    return not (_ASSOC_RX.search(n) or _HS_MARK_RX.search(n))


# ---- COLLEGE (tfrrs feed): the owner's hierarchy is
#          division -> region -> conference
# "Division III" is the DIVISION unit (NCAA DIII), never a class token;
# regionals are named ("Great Lakes Regional") and vote region;
# conference championships vote conference. HS rules do not apply.
_ROMAN = {"1": "I", "2": "II", "3": "III"}
_COLLEGE_ORG_RX = re.compile(r"\b(NCAA|NAIA|NJCAA|USCAA|CCCAA)\b")
_COLLEGE_DIV_RX = re.compile(
    r"\b(?:division\s*|D)[-. ]?(I{1,3}|[123])\b", re.I)
_COLLEGE_REGION_RX = re.compile(
    r"\b([\w .&'-]+?)\s+region(?:al)?s?\b", re.I)


# ★ A HOST IS NOT A CONFERENCE (owner, 2026-09-02: an Arkansas athlete's
#   rank line read "ARKANSAS STATE #3" where the SEC belongs). The
#   mixed-case rule below reads the title-cased phrase before "Champ" as
#   the conference, and "Arkansas State Championships" -- a meet named for
#   its host, or for the state -- fits it perfectly. A phrase that names a
#   US state, or a school ("... State", "University", "College"), is a
#   place or a host, never a membership unit, whichever rule produced it.
_NOT_A_CONFERENCE_RX = re.compile(
    r"\b(?:STATE|UNIVERSITY|UNIV|COLLEGE|INSTITUTE|ACADEMY|TECH)\b")


def _hostNotConference(unit):
    return bool(_NOT_A_CONFERENCE_RX.search(unit)
                or _STATE_NAME_RX.search(unit))


def _collegeUnits(name):
    facts = []

    org = _COLLEGE_ORG_RX.search(name)
    dm = _COLLEGE_DIV_RX.search(name)
    if dm:
        div = _ROMAN.get(dm.group(1).upper(), dm.group(1).upper())
        facts.append(("division",
                      f"{org.group(1) if org else 'NCAA'} D{div}"))
    elif org and org.group(1) != "NCAA" \
            and not _COLLEGE_REGION_RX.search(name):
        # ! only when the name is NOT a regional: "GCAA/NJCAA Region 17"
        #   names the org but says nothing about DI/DII/DIII, and the
        #   bare org then fought the real "NJCAA DI National" verdict.
        facts.append(("division", org.group(1)))
    rm = _COLLEGE_REGION_RX.search(name)
    if rm:
        unit = _cleanUnit("region", rm.group(1).upper())
        unit = re.sub(r"^(?:NCAA|NAIA|NJCAA|USCAA|CCCAA)\s*", "", unit)
        unit = re.sub(r"^D(?:IVISION)?[-. ]?(?:I{1,3}|[123])\s*", "",
                      unit).strip(" -")
        # sport words ride along on both ends ("MIAA XC Regional")
        unit = re.sub(r"^(?:MEN'S|WOMEN'S|OUTDOOR|INDOOR|XC|CROSS[- ]?"
                      r"COUNTRY|TRACK|FIELD|T&F|AND|&)\s+", "", unit)
        unit = re.sub(r"(?:\s+(?:MEN'S|WOMEN'S|OUTDOOR|INDOOR|XC|CROSS"
                      r"[- ]?COUNTRY|TRACK|FIELD|T&F|AND|&))+$", "", unit)
        # ! A BARE "Regionals" NAMES NO REGION -- the same lesson STATE
        #   taught. An empty capture, a division token or a lone number
        #   is not a unit, and "REGION" as a region is worse than none.
        if unit and not re.fullmatch(r"D?(?:I{1,3}|IV|V|[1-9]\d?)", unit) \
                and re.search(r"[A-Z]{3}", unit):
            # "MIAA Regional", "Michigan Intercollegiate ... Regional":
            # the body running its own regional is a CONFERENCE, and
            # calling it a region loses the hierarchy the owner set.
            if _ASSOC_RX.search(unit) or re.search(
                    r"\b(?:ASSOCIATION|CONFERENCE)$", unit):
                facts.append(("conference", unit))
            else:
                facts.append(("region", unit))
    for kind, rx, grp in _UNIT_RULES:
        if kind != "league":
            continue
        m = rx.search(name)
        if not m or not m.group(grp):
            continue
        unit = _cleanUnit("league", m.group(grp).strip().upper())
        if not unit or unit in _NOT_A_LEAGUE or _ASSOC_RX.search(unit) \
                or re.fullmatch(r"D?(?:I{1,3}|[1-3])", unit) \
                or _hostNotConference(unit):
            continue
        facts.append(("conference", unit))
        break

    # mixed-case conference names carry no League/Conference word at all
    # ("Big Ten Outdoor Track & Field Championships"): on the COLLEGE feed
    # a leading title-cased phrase before the sport words + Champ is the
    # conference. HS never gets this rule -- it would eat city MoCs.
    if not any(k == "conference" for k, _ in facts):
        lm = re.match(
            r"\s*(?:the\s+)?([A-Z][\w'&.-]*(?:\s+(?:[A-Z][\w'&.-]*|[0-9]{1,2})){0,3})\s+"
            r"(?:(?i:men's|women's|outdoor|indoor|cross[- ]?country|xc|"
            r"track(?:\s*(?:&|and)\s*field)?|t&f|and|field)\s+)*"
            r"(?i:champ)", name)
        if lm:
            unit = _cleanUnit("league", lm.group(1).upper())
            # the title-cased phrase greedily swallows capitalized sport
            # words ("Big Ten Outdoor") -- trim them off the tail
            unit = re.sub(
                r"(?:\s+(?:OUTDOOR|INDOOR|XC|CROSS[- ]?COUNTRY|TRACK|"
                r"FIELD|T&F|AND|&|MEN'S|WOMEN'S))+$", "", unit)
            unit = re.sub(r"\s+ONLY$", "", unit)
            if unit and unit not in _NOT_A_LEAGUE \
                    and not _ASSOC_RX.search(unit) \
                    and not _COLLEGE_ORG_RX.search(unit) \
                    and not re.fullmatch(r"D?(?:I{1,3}|[1-3])", unit) \
                    and "REGION" not in unit and "DIVISION" not in unit \
                    and not _hostNotConference(unit):
                facts.append(("conference", unit))
    return facts



_UNIT_RULES = [
    ("state",    re.compile(r"\b(state|all-state|federation)\b", re.I),
     None),
    ("state",    _ASSOC_RX, None),
    ("section",  re.compile(r"\b([\w .&'-]+?)\s+section(?:al)?s?\b", re.I), 1),
    ("section",  re.compile(r"\b(NCS|CCS|CIF-?SS|SJS|SDS)\b"), 1),
    # "CIF Sac-Joaquin Cross Country Championships": the section name sits
    # directly after CIF with no "Section" word at all
    ("section",  re.compile(r"\bCIF\s+([A-Z][\w .&'-]+?)\s+"
                            r"(?i:cross|xc|x-|track|champ|finals)"), 1),
    # NY-style numbered sections: "Section 5", "Section XI". LAST of the
    # section rules and skipped when a NAMED section already matched --
    # "WPIAL Section IV" fed both rules and voted WPIAL against IV.
    ("section#", re.compile(r"\bsection\s+([IVX0-9]{1,4})\b", re.I), 1),
    ("district", re.compile(r"\bdistrict\s*([\dA-Z-]{0,6})\b", re.I), 1),
    # (TX writes "District 7-3A" = district 7, class 3A -- split and the
    #  leading zero stripped in _cleanUnit so 07 and 7 are one district)
    ("region",   re.compile(r"\bregion(?:al)?s?\s*([\dA-Z-]{0,4})\b", re.I),
     1),
    # county championships are a real unit (Orange County, Bergen County)
    ("county",   re.compile(r"\b([\w .'-]+?)\s+county\b", re.I), 1),
    # ! ...but not TRI/BI/MULTI County, which are league names wearing
    #   the word county -- filtered in parseUnits by _NOT_A_COUNTY.
    # ★ THE AREA: the level BETWEEN league and section that California
    #   track has and cross country does not (owner, 2026-09-02: NCS ->
    #   Tri-Valley -> EBAL). Named two ways: "<Section> <Area>
    #   Championships" ("NCS Tri-Valley Championships", "NCS Redwood Empire
    #   Championships", "CCS Semifinals" is NOT one) and "<Area> Area
    #   Championships". The section rule still votes the section off the
    #   same name; the writer copies the area onto the school's other-sport
    #   row, because membership in an area is a fact about the school.
    ("area",     re.compile(
        r"\b(?:NCS|CCS|CIF-?SS|SJS|SDS)\s+"
        r"([A-Z][\w'&.-]*(?:[ -][A-Z][\w'&.-]*){0,2})\s+"
        r"(?:(?i:cross[- ]?country|xc|track(?:\s*(?:&|and)\s*field)?|"
        r"t&f|outdoor|indoor|area)\s+)*"
        r"(?i:champ|meet\b|finals?)"), 1),
    ("area",     re.compile(r"\b([\w'&.-]+(?:[ -][\w'&.-]+){0,2})\s+area\s+"
                            r"(?i:champ|meet\b|finals?)", re.I), 1),
    ("league",   re.compile(r"\b([\w .&'-]+?)\s+league\b", re.I), 1),
    ("league",   re.compile(r"\b([\w .&'-]+?)\s+conference\b", re.I), 1),

    ("league",   re.compile(r"\b(PSAL|CHSAA|CHSFL|CPS|BCPS)\b"), 1),
    # bare all-caps acronym before Champ/Finals = a league (EBAL, WCAL,
    # and with the sport-word filler allowed: "MAC Cross Country
    # Championships", "SEC XC Championship Meet", "NJIC Divisional") --
    # scoped (?i:) so "Championships" matches while the acronym stays
    # case-sensitive; the exclusion set stops section/state double-votes
    ("league",   re.compile(
        r"\b([A-Z]{3,6})\s+"
        r"(?:(?i:cross[- ]?country|xc|x-country|cc|track(?:\s*(?:&|and)\s*"
        r"field)?|t&f|outdoor|indoor|division(?:al)?)\s+)*"
        r"(?i:champ|finals?)"), 1),
]

_NOT_A_COUNTY = {"TRI", "BI", "MULTI", "DUAL", "ALL", "INTER"}
_NOT_AN_AREA = {"MOC", "TRACK", "CROSS", "XC", "OPEN", "VARSITY", "JV",
                "FROSH", "STATE", "CIF", "NCS", "CCS", "SJS", "SDS", "AREA",
                "TOP", "ALL", "QUALIFYING", "QUALIFIER", "MASTERS", "MASTER"}


_YEAR_RX = re.compile(r"\b(?:19|20)\d\d\b")
_ORDINAL_RX = re.compile(r"\b\d+(?:st|nd|rd|th)\s+annual\b", re.I)


def _cleanUnit(kind, unit):
    """Strip year prefixes, ordinals and the redundant CIF prefix so
    '2022 CIF LOS ANGELES' and 'CIF NORTH COAST' collapse toward their
    real names. Alias merging (NCS = NORTH COAST) is the writer's job."""
    unit = _YEAR_RX.sub("", unit)
    unit = _ORDINAL_RX.sub("", unit)
    unit = re.sub(r"\s{2,}", " ", unit).strip(" -")
    if kind == "district":
        unit = re.sub(r"-[1-6]A$", "", unit)        # TX: 7-3A -> 7
        unit = re.sub(r"^0+(?=\d)", "", unit)       # 07 -> 7
    if kind in ("league", "conference"):
        # "METRO LEAGUE" and "METRO", "AMAC CHAMPIONSHIP-AP" and "AMAC"
        # are one unit voting against itself every season.
        unit = re.sub(r"\s+(?:LEAGUE|CONFERENCE|CHAMPIONSHIPS?|MEET|"
                      r"FINALS?)\b.*$", "", unit).strip(" -")
    if kind == "section":
        unit = re.sub(r"^CIF[- ]?", "", unit).strip(" -")
        # ★ THE CALIFORNIA ! WALL (2026-08-27). The bare-CIF rule
        #   ("CIF Central Section Division II Championships") captures
        #   greedily up to Champ and produced a SECOND section fact,
        #   "CENTRAL SECTION DIVISION II", which then fought the real
        #   "CENTRAL" for the same kind every single season. The section
        #   name ends where the words Section or Division begin.
        unit = re.split(r"\s+(?:SECTIONS?|DIVISIONS?)\b", unit)[0].strip(" -")
    return unit


def parseUnits(meet_name, div_title, college=False, state=None):
    """[(kind, unit)] independent facts from one meet+race title pair.
    Empty when the championship gate fails. college=True (the tfrrs
    feed) uses the division -> region -> conference hierarchy instead
    of the HS rules."""
    name = (meet_name or "").strip()
    # ★ THE PARENTHETICAL IS AN ALIAS, NOT A SECOND LEAGUE. "ISAL
    #   Championship (Independent School Athletic League)" voted ISAL
    #   against its own expansion every season. The acronym outside the
    #   brackets wins because that is what the short names elsewhere use.
    name = re.sub(r"\s*\([^)]*\)", " ", name).strip()
    if (state or "").upper() in _FOREIGN_ST:
        return []
    if not name or not _CHAMP_RX.search(name) or _NEVER_RX.search(name):
        return []
    if _NATIONALS_RX.search(name):
        # division is real evidence; conference/region are not named here
        return [f for f in _collegeUnits(name)
                if f[0] == "division"] if college else []
    if college:
        return _collegeUnits(name)
    # ★ A COMBINED MEET NAMES NO ONE LEAGUE. "Northern Conference /
    #   Longs Peak League Championships", "East Valley, Valley Mission,
    #   Northern, Western League Finals" and "EAL-SRL Championships" are
    #   several leagues sharing a race, exactly like a 2A/1A class
    #   combine -- the school could be in any of them, so none votes.
    multi_league = (
        len(re.findall(r"\b(?:league|conference)\b", name, re.I)) > 1
        or name.count(",") >= 2
        or re.search(r"\b[A-Z]{2,4}-[A-Z]{2,4}\b", name))

    facts = []
    # ! A bare "Sectionals" names no section -- but it still says the
    #   division token belongs to the SECTION, not to a class. Voting and
    #   scoping are different jobs; round 7 conflated them and sent 24k
    #   rows to the unparsed list.
    bare_kinds = set()
    for kind, rx, grp in _UNIT_RULES:
        m = rx.search(name)
        if not m:
            continue
        # an empty capture (bare "Regionals") keeps the KIND as the unit
        if kind == "section#":
            if any(k == "section" for k, _ in facts):
                continue
            kind = "section"
        unit = (m.group(grp).strip() if grp and m.group(grp)
                else ("STATE" if kind == "state" else kind.upper()))
        unit = _cleanUnit(kind, unit.upper()) or kind.upper()
        # ! "NCAA Division III Championships" put a league named III on
        #   every college school the name heuristic misrouted. A bare
        #   division token is never a league on either path.
        if kind == "league" and multi_league:
            continue
        if kind == "league" and (unit in _NOT_A_LEAGUE
                                 or re.fullmatch(r"A{1,4}", unit)
                                 or re.fullmatch(r"[IVXL]+", unit)
                                 or _DISTRICT_RX.match(unit)
                                 or "SCHOOL DISTRICT" in unit
                                 or _COACHES_RX.match(unit)
                                 or _ASSOC_RX.search(unit)
                                 or re.fullmatch(r"D?(?:I{1,3}|IV|V|[1-6])",
                                                 unit)):
            continue
        if kind == "county" and unit in _NOT_A_COUNTY:
            continue
        # an area is a NAME: strip the section's own acronym off the front
        # and the word AREA off the end ("NCS Bay Shore Area" -> BAY SHORE),
        # then refuse a division token, a class, the section's own
        # meet-of-champions, a sub-section round or a sport word
        if kind == "area":
            unit = re.sub(r"^(?:CIF\s+)?(?:NCS|CCS|CIF-?SS|SJS|SDS)\s+", "",
                          unit)
            unit = re.sub(r"\s+AREA$", "", unit).strip()
            if (not unit or unit in _NOT_AN_AREA
                    or re.fullmatch(r"D(?:IVISION)?\s*[1-6]|[1-6]A", unit)
                    or re.search(r"\b(?:MEET|CHAMP|DIVISION|CLASS|SECTION|"
                                 r"SEMI|PRELIM|FINAL|TRIAL|QUALIF)", unit)):
                continue
        # ! THE CIF RULE CAPTURES EVERYTHING UP TO THE SPORT WORD, so "CIF
        #   NCS Tri-Valley Championships" voted a section named NCS
        #   TRI-VALLEY against NCS itself. A section that starts with a
        #   known acronym IS that acronym; the rest is the area's business.
        if kind == "section":
            m2 = re.match(r"^(NCS|CCS|CIF-?SS|SJS|SDS)\b", unit)
            if m2:
                unit = m2.group(1)


        # "CIF State ..." is the state meet, not a section named STATE
        if kind == "section" and unit in ("STATE", "CIF"):
            continue
        # ! A BARE KIND WORD IS NOT A UNIT. "League Championships" with
        #   no name in front votes LEAGUE, which then fights the real
        #   league. Same lesson as STATE and REGION. State is exempt --
        #   main resolves a bare STATE to the meet's own state code.
        if kind != "state" and unit == kind.upper():
            bare_kinds.add(kind)
            continue
        facts.append((kind, unit))
    # ★ PEEL GLUED CLASS PREFIXES off unit names ("2A EVERGREEN",
    #   "4A KINGCO"): the unit is the rest, and a SINGLE peeled token is
    #   class evidence -- a multi-class combine ("2A & 1A KINGCO") names a
    #   shared meet and votes no class at all.
    peeled, fixed = [], []
    for kind, unit in facts:
        if kind in ("league", "county", "section"):
            m = re.match(r"^([1-6]A(?:\s*[-&/]\s*[1-6]A)*)\s+(.+)$", unit)
            if m:
                unit = m.group(2).strip()
                if not re.search(r"[-&/]", m.group(1)):
                    peeled.append(("class", m.group(1)))
        fixed.append((kind, unit))
    facts = fixed + peeled
    # ! A LEAGUE NAMED ...STATE IS NOT A STATE MEET. "Bay State
    #   Conference", "Golden State League" tripped the bare state rule
    #   and voted a phantom state series onto every member school.
    if any(k == "state" and u == "STATE" for k, u in facts) and any(
            k != "state" and "STATE" in u for k, u in facts):
        facts = [(k, u) for k, u in facts
                 if not (k == "state" and u == "STATE")]
    # last resort: a bare state name in a championship title ("Michigan
    # Meet of Champions", "Nebraska Championship Meet") is the state
    # series -- but ONLY when no real unit matched, so "Mississippi
    # Valley Conference" stays a league.
    facts = list(dict.fromkeys(facts))   # two rules, one fact, one vote
    if not any(k != "class" for k, _ in facts):
        sm = _STATE_NAME_RX.search(name)
        if sm:
            # ★ ONE SPELLING. "Alabama" and AL were two units fighting
            #   for the same kind every season -- the code wins, because
            #   that is what the bare-STATE rule resolves to.
            code = _STATE_CODE.get(sm.group(1).upper())
            # ! AND IT MUST BE THIS MEET'S STATE. "Mississippi 8
            #   Conference" is a MINNESOTA league; the bare state name
            #   inside a league title is not a state series.
            st_here = (state or "").upper()
            if not (code and st_here and code != st_here):
                facts.append(("state", code or sm.group(1).upper()))
    # ! class/div from BOTH the meet name and the race/division title --
    #   the token survives in whichever one kept it.
    for src in (name, div_title or ""):
        # a multi-class combine in the NAME ("2A & 1A KingCo") votes no
        # class -- the school could be either; the race title still may
        # (both sources, and a hyphen is a combine separator too:
        #  "2A-3A District Meet" names a shared meet, not a class)
        if re.search(r"[1-6]A\s*[-&/]\s*[1-6]A", src):
            continue
        for rx in _CLASS_RX:
            m = rx.search(src)
            if m:
                tok = m.group(1).upper()
                # Roman and Arabic name the SAME class -- normalize, or
                # "II" vs "2" reads as a conflict every season
                tok = {"I": "1", "II": "2", "III": "3", "IV": "4",
                       "V": "5", "ONE": "1", "TWO": "2", "THREE": "3",
                       "FOUR": "4", "FIVE": "5"}.get(tok, tok)
                # ★ THE OWNER'S ORIGINAL SPEC: state/div and section/div
                #   are SEPARATE facts (California carries both at once,
                #   and one 'class' kind made them fight every season).
                #   The div attaches to the unit that carried it.
                kinds_here = {k for k, _ in facts} | bare_kinds
                dk = ("section_div" if "section" in kinds_here else
                      "state_div" if "state" in kinds_here else "class")
                facts.append((dk, tok))
                break
        else:
            continue
        break
    return facts


# ---- the votes -------------------------------------------------------- #
def _rows(cur, sport, lo, hi):
    """DISTINCT (school, season, meet_name, div_title, state) attendance
    rows from late-season meets, both feeds."""
    if sport == "XC":
        cur.execute("""
            SELECT DISTINCT r.school, substr(r.date, 1, 4) AS yr,
                   m.meet_name, m.division, m.state, r.source
            FROM results r
            JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                        AND m.source = r.source
            WHERE COALESCE(TRIM(r.school), '') <> ''
              AND substr(r.date, 6, 2)::int BETWEEN %(lo)s AND %(hi)s
              AND m.meet_name ~* 'champ|finals|meet of champions'
        """, {"lo": lo, "hi": hi})
        yield from cur.fetchall()
        cur.execute("""
            SELECT DISTINCT r.school, substr(r.date, 1, 4) AS yr,
                   mt.meet_name, NULL::text AS division, mt.state,
                   'tfrrs'::text AS source
            FROM results r
            JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                               AND mt.source = 'tfrrs'
            WHERE r.source = 'tfrrs'
              AND COALESCE(TRIM(r.school), '') <> ''
              AND substr(r.date, 6, 2)::int BETWEEN %(lo)s AND %(hi)s
              AND mt.meet_name ~* 'champ|finals|meet of champions'
        """, {"lo": lo, "hi": hi})
        yield from cur.fetchall()
    else:
        cur.execute("""
            -- ! MEET-GRAIN LATERAL, not the (div, event) triple: result ids
            --   drift between scrape vintages and the exact join erased
            --   modern college meets entirely (Tufts stopped at 2016).
            --   meet_name is meet-level, so any row of the meet names it.
            SELECT DISTINCT r.school, substr(r.date, 1, 4) AS yr,
                   m.meet_name, m.division, m.state, r.source
            FROM results_tf r
            JOIN LATERAL (
                SELECT mm.meet_name, mm.division, mm.state
                FROM meets_tf mm
                WHERE mm.meet_id = r.meet_id AND mm.source = r.source
                LIMIT 1
            ) m ON m.meet_name ~* 'champ|finals|meet of champions'
            WHERE COALESCE(TRIM(r.school), '') <> ''
              AND substr(r.date, 6, 2)::int BETWEEN %(lo)s AND %(hi)s
        """, {"lo": lo, "hi": hi})
        yield from cur.fetchall()


# ---- resolution (shared by the census and the writer) ---------------- #
#   The owner's rule: the most recent season is the end-all. Older
#   seasons are provenance, never the answer.
def current(counter, prefer=None):
    """(unit, latest_yr, conflict) or None.

    ★ prefer: a set of units that win the latest season whenever any of
      them was voted at all, whatever the counts (the writer passes the
      known college conferences, alias-resolved). A meet named for its
      host can out-vote the real conference championship -- the host
      meet is bigger -- and the count is then the wrong judge. The
      conflict flag still reports the disagreement."""
    if not counter:
        return None
    latest = max(yr for (_u, yr) in counter)
    in_latest = Counter()
    for (u, yr), n in counter.items():
        if yr == latest:
            in_latest[u] += n
    top = in_latest.most_common()
    conflict = len(top) > 1 and top[1][1] >= 2
    if prefer:
        known = [(u, n) for u, n in top if u in prefer]
        if known:
            return known[0][0], latest, conflict
    return top[0][0], latest, conflict



def rivals(counter):
    """Every unit voted in the LATEST season, richest first."""
    latest = max(yr for (_u, yr) in counter)
    out = Counter()
    for (u, yr), n in counter.items():
        if yr == latest:
            out[u] += n
    return out.most_common()


def buildVotes(cur, sport, state_filter=None):
    """(votes, seasons, unit_eg, stats) over one sport's late season.

    votes[(school, state)][kind][(unit, year)] = n. This is the single
    place the corpus is turned into units -- the census prints it, the
    writer stores it, and neither can drift from the other."""
    lo, hi = _WINDOW[sport]
    votes = defaultdict(lambda: defaultdict(Counter))
    seasons = defaultdict(set)
    unit_eg = {}
    st_ = {"rows": 0, "excluded": 0, "hits": 0, "lvl": 0, "name": 0}
    name_hits, name_miss = Counter(), Counter()
    maps = _levelMaps(cur)          # BEFORE _rows: same cursor
    for school, yr, meet_name, div_title, state, feed in _rows(
            cur, sport, lo, hi):
        st_["rows"] += 1
        st = (state or "").upper()
        if state_filter and st != state_filter.upper():
            continue
        is_coll = _schoolIsCollege(school, st, maps)
        if is_coll is None:
            st_["name"] += 1
            is_coll = (feed == 'tfrrs' and _isCollegeName(meet_name))
        else:
            st_["lvl"] += 1
        if st in _FOREIGN_ST or (meet_name and (
                _NEVER_RX.search(meet_name)
                or (not is_coll and _NATIONALS_RX.search(meet_name)))):
            st_["excluded"] += 1
            continue
        facts = parseUnits(meet_name, div_title, college=is_coll, state=st)
        if not facts:
            name_miss[meet_name] += 1
            continue
        name_hits[meet_name] += 1
        key = (school, st)
        for kind, unit in facts:
            if kind == "state" and unit == "STATE":
                unit = st or "STATE"
            votes[key][kind][(unit, yr)] += 1
            unit_eg.setdefault((kind, unit), (meet_name or "", school, st))
        seasons[key].add(yr)
    st_["hits"] = sum(name_hits.values())
    return votes, seasons, unit_eg, (st_, name_hits, name_miss)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    ap.add_argument("--school", default=None,
                    help="print one school's full unit evidence")
    ap.add_argument("--state", default=None,
                    help="restrict the sample table to one state")
    ap.add_argument("--conflicts", action="store_true",
                    help="what the ! flags actually disagree about")
    ap.add_argument("--unparsed", action="store_true",
                    help="championship-gated names that yielded NO unit")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    lo, hi = _WINDOW[args.sport]

    with getConn() as conn, conn.cursor() as cur:
        votes, seasons, unit_eg, (st_, name_hits, name_miss) = buildVotes(
            cur, args.sport, args.state)

    print(f"\n  {args.sport} late-season window months {lo}-{hi}: "
          f"{st_['rows']:,} (school, championship-meet) attendance rows")
    print(f"  parsed into units: {st_['hits']:,} rows across "
          f"{len(name_hits):,} distinct meet names")
    print(f"  excluded (club/foreign/shoe postseason): "
          f"{st_['excluded']:,} rows")
    print(f"  championship-gated but NO unit extracted: "
          f"{sum(name_miss.values()):,} rows, {len(name_miss):,} names")
    print(f"  level from the school's own pool: {st_['lvl']:,} rows; "
          f"name heuristic fallback: {st_['name']:,} rows")

    if args.unparsed:
        print("\n  the unparsed names, biggest first (parser work lives "
              "here):")
        for name, n in name_miss.most_common(args.limit):
            print(f"    {n:>6,}  {name[:70]}")
        return

    def history(counter, skip_unit):
        """'earlier: BVAL (to 2015), ...' for --school provenance."""
        last = {}
        for (u, yr), _n in counter.items():
            if u != skip_unit:
                last[u] = max(last.get(u, ""), yr)
        return ", ".join(f"{u} (to {y})"
                         for u, y in sorted(last.items(),
                                            key=lambda kv: -int(kv[1]))[:4])


    if args.conflicts:
        # ★ WHY THE ! WALL. Every flagged school, grouped by what the
        #   latest season actually disagreed about -- the shape of the
        #   disagreement is the parser bug, not the individual school.
        shapes, examples = Counter(), {}
        for (school, st), kinds in votes.items():
            for kind, c in kinds.items():
                got = current(c)
                if not got or not got[2]:
                    continue
                top = rivals(c)
                shape = (kind, top[0][0], top[1][0])
                shapes[shape] += 1
                examples.setdefault(shape, f"{school} ({st}) {got[1]}")
        print(f"\n  {sum(shapes.values()):,} flagged (school, kind) pairs, "
              f"{len(shapes):,} distinct disagreements:\n")
        print(f"    {'n':>6}  {'kind':<10} {'winner':<18} {'rival':<18} "
              "example school / the two meets")
        print("    " + "-" * 100)
        for (kind, a, b), n in shapes.most_common(args.limit):
            print(f"    {n:>6,}  {kind:<10} {a[:18]:<18} {b[:18]:<18} "
                  f"{examples[(kind, a, b)][:30]}")
            for u in (a, b):
                eg = unit_eg.get((kind, u))
                if eg:
                    # ! the school is printed because the example is the
                    #   first meet ANYWHERE that produced this unit; it
                    #   is not necessarily the flagged school's own meet
                    print(f"{'':>12}  {u[:18]:<18} <- {eg[0][:52]}"
                          f"   [{eg[1][:18]} {eg[2]}]")
        return

    if args.school:
        pat = args.school.lower()
        for (school, st) in sorted(votes):
            if pat not in school.lower():
                continue
            key = (school, st)
            print(f"\n  {school} ({st or '??'})  seasons "
                  f"{min(seasons[key])}-{max(seasons[key])}")
            for kind in ("division", "conference", "league", "section",
                         "section_div", "district", "county", "region",
                         "state", "state_div", "class"):
                c = votes[key].get(kind)
                if not c:
                    continue
                cur_u, yr, conflict = current(c)
                n_total = sum(n for (u, _), n in c.items() if u == cur_u)
                line = f"    {kind:<9} {cur_u}  ({yr}, {n_total} votes)"
                if conflict:
                    line += ("   !! CONFLICT in " + str(yr) + ": "
                             + ", ".join(f"{u} x{n}"
                                         for u, n in rivals(c)[:4]))
                past = history(c, cur_u)
                if past:
                    line += f"   earlier: {past}"
                print(line)
        return

    # the sample table: current (most-recent-season) verdicts only
    print(f"\n    {'school':<28} {'st':<3} {'league':<18} {'section':<14} "
          f"{'class':<7} asof")
    print("    " + "-" * 82)
    ranked = sorted(votes.items(),
                    key=lambda kv: -sum(sum(c.values())
                                        for c in kv[1].values()))
    # college keys (tfrrs feed voted division/conference) print their own
    # table below with the owner's hierarchy: division -> region -> conf
    hs_ranked = [kv for kv in ranked
                 if not any(k in ("division", "conference")
                            for k in kv[1])]
    college_ranked = [kv for kv in ranked
                      if any(k in ("division", "conference")
                             for k in kv[1])]
    for (school, st), kinds in hs_ranked[:args.limit]:
        cells, asof, flag = {}, "", " "
        for kind in ("league", "section", "section_div", "state_div",
                     "class"):
            got = current(kinds.get(kind, Counter()))
            if got:
                cells[kind] = got[0]
                asof = max(asof, got[1])
                if got[2]:
                    flag = "!"
        div_cell = (cells.get("section_div") or cells.get("state_div")
                    or cells.get("class") or "")
        print(f"    {school[:28]:<28} {st:<3} "
              f"{cells.get('league', '')[:18]:<18} "
              f"{cells.get('section', '')[:14]:<14} "
              f"{div_cell[:6]:<6}{flag} {asof}")
    if college_ranked:
        print(f"\n    {'college':<28} {'conference':<18} {'region':<14} "
              f"{'division':<10} asof")
        print("    " + "-" * 78)
        for (school, st), kinds in college_ranked[:args.limit]:
            cells, asof, flag = {}, "", " "
            for kind in ("conference", "region", "division"):
                got = current(kinds.get(kind, Counter()))
                if got:
                    cells[kind] = got[0]
                    asof = max(asof, got[1])
                    if got[2]:
                        flag = "!"
            print(f"    {school[:28]:<28} "
                  f"{cells.get('conference', '')[:18]:<18} "
                  f"{cells.get('region', '')[:14]:<14} "
                  f"{cells.get('division', '')[:9]:<9}{flag} {asof}")
    print("\n  Current = the most recent season's verdict (owner's rule); "
          "! = that latest\n  season itself holds two corroborated units "
          "(true ambiguity -- eyes). Use\n  --unparsed for parser misses, "
          "--school NAME for full provenance.")


if __name__ == "__main__":
    main()
