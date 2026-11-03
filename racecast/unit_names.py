# Project: xc-predictor / racecast
# File:    unit_names.py
# Purpose: Is this stored "conference" really one? Pure, no database: read by
#          the meet-name parser (scripts/check_school_units._collegeUnits)
#          and by the Conferences board (unit_board.validKeys), so a name
#          that is a meet or a region never votes and never ranks.
#
# ★ THE OWNER'S BOARD (2026-11-03): "BORDER WAR", "JACK SAINT CLAIR
#   MEMORIAL", "XIV", "XVI", "XIX", "CCS XC", "COLLEGIATE", "HEART MEN'S AND
#   WOMEN'S" ranked as college conferences. Each is the part of a meet name
#   in front of "Championship(s)" -- a dual meet, a memorial, an NJCAA
#   region numeral, a high school section's race, a bare adjective, a
#   conference's name with the sexes still glued on.
import re

# a word that names a meet, an event or the sexes, never a conference
_MEET_WORDS = re.compile(
    r"\b(?:MEMORIAL|INVITATIONAL|INVITE|CLASSIC|WAR|BATTLE|DUAL|TRI|QUAD|SHOOTOUT|"
    r"CHALLENGE|RELAYS?|OPEN|CUP|SHOWDOWN|FESTIVAL|GAMES|MEET|RACE|RUN|"
    r"MEN'?S|WOMEN'?S|MENS|WOMENS|BOYS|GIRLS|XC|CROSS[- ]?COUNTRY|TRACK|FIELD|"
    r"INDOOR|OUTDOOR|T&F)\b", re.I)
# a whole name that is a numeral (an NJCAA region: XIV), a level or a
# kind word, not a conference's name
_ROMAN = re.compile(r"(?:REGION\s+)?[IVXL]{1,6}", re.I)
_BARE = {"COLLEGIATE", "ATHLETIC", "CONFERENCE", "LEAGUE", "ASSOCIATION", "NATIONAL",
         "NATIONALS", "REGIONAL", "REGIONALS", "SECTIONAL", "DISTRICT", "STATE",
         "COLLEGE", "UNIVERSITY", "JUNIOR COLLEGE", "JUCO", "INTERCOLLEGIATE", "ALL"}


def notAConference(value):
    """True when a stored conference name is a meet, a region numeral or a
    bare word. Pure."""
    v = " ".join(str(value or "").replace("’", "'").split()).upper()
    if not v:
        return True
    if v in _BARE or _ROMAN.fullmatch(v):
        return True
    return bool(_MEET_WORDS.search(v))
