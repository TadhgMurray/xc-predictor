"""
division_group.py -- which standings a division label belongs to.

★ THE RULE (owner, sweep 2026-10-10, A11). A meet's division strings are
  whatever the host typed: "Varsity", "Invitational", "Open", "Seeded",
  "Heat 2", "Girls 5K Varsity A", or nothing at all. Track team scores were
  keyed on that raw string, so a meet whose varsity events were filed under
  "Varsity" and "Open" (or "Seeded" and "Unseeded", or "Section 1" and
  "Section 2") printed two or three half-standings, none of them the meet's
  score. The decided roll-up:

    varsity  Varsity, Invite/Invitational, Open, Elite, Seeded/Unseeded,
             Championship, a Heat/Flight/Section/Race label, a school-size
             word beside one of those ("Varsity Large School"), or blank
             -> ONE standings per gender.
    jv       JV, Junior Varsity
    fs       Frosh-Soph, F/S, Freshman, Frosh, Novice (and the 9th/10th
             grade spellings of the same thing)
    ms       Middle School, 7th, 8th, Junior High
    para     Para, Adaptive, Ambulatory, Wheelchair, Seated, Unified
             -> each of those four keeps its own standings.
    other    ANY label not recognised -> its own group, keyed on the label
             ("Masters", "Gold Race", "Large School" with no level word --
             see _SIZE_WORDS).
             Never merged by guess: two unknown labels are two standings.

! SPORT-AGNOSTIC. Nothing here knows about track: the same function is
  meant for cross-country compiled results (varsity-level races compiled
  together, JV and F/S apart). Gender and distance words are read past
  ("Girls 5K Varsity A" is varsity), because a label carries them and
  they do not say which standings.

! PRECEDENCE: para > ms > fs > jv > varsity. A label naming two levels is
  the narrower one: "Middle School Open" is a middle-school race, "JV
  Invitational" a JV race, and "Junior Varsity" contains "varsity" but is
  JV. Checked in that order so no label can fall into varsity by a
  substring.

! A RECOGNISED WORD WITH AN UNRECOGNISED ONE BESIDE IT IS UNRECOGNISED
  ("Varsity Masters", "Open Alumni"): the rule only merges what it can name
  completely.
"""
import re

VARSITY, JV, FS, MS, PARA = "varsity", "jv", "fs", "ms", "para"

# Display order: the varsity standings is the meet's PRIMARY score (owner,
# 2026-10-10) -- shown first, and the one a single "team score" means.
GROUP_ORDER = (VARSITY, JV, FS, MS, PARA)

GROUP_LABELS = {VARSITY: "Varsity", JV: "JV", FS: "Frosh-Soph",
                MS: "Middle school", PARA: "Para"}

# Multi-word spellings first collapse to one token so the word tests below
# see them whole. Order matters: "junior varsity" before anything reads
# "varsity", "junior high" before "high" is dropped as filler.
_PHRASES = (
    (r"\bjunior\s+varsity\b", " jv "),
    (r"\bj\.?\s*v\.?(?=\s|$|\d)", " jv "),
    (r"\bjunior\s+high\b|\bjr\.?\s+high\b", " ms "),
    (r"\bmiddle\s+school\b|\bmiddle\b", " ms "),
    (r"\bfrosh\s*[-/&]?\s*soph\w*\b|\bfresh\w*\s*[-/&]\s*soph\w*\b", " fs "),
    (r"\bf\s*/\s*s\b|\bf\s*-\s*s\b", " fs "),
    (r"\b7th\s*[-/&]\s*8th\b|\b7\s*/\s*8\b|\b7\s*-\s*8\b", " ms "),
    (r"\bspecial\s+olympics\b", " specialolympics "),
    # "Division 1", "Div II": a size class (see _SIZE_WORDS), not a heat
    # number -- without this the numeral would read past as a distance
    (r"\b(?:division|div)\.?\s*(?:[1-7]|iv|i{1,3})\b", " class "),
)

_CATEGORY_WORDS = {
    PARA: {"para", "parasport", "adaptive", "ambulatory", "wheelchair",
           "seated", "unified"},
    MS: {"ms", "7th", "8th", "7th/8th"},
    FS: {"fs", "frosh", "freshman", "freshmen", "fresh", "novice",
         "soph", "sophomore", "sophomores", "9th", "10th"},
    JV: {"jv", "jv1", "jv2"},
    VARSITY: {"varsity", "v", "invite", "invitational", "inv", "open",
              "elite", "seeded", "unseeded", "championship",
              "championships", "champ", "champs", "heat", "heats", "flight",
              "section", "sect", "sec", "race", "fast", "slow", "final",
              "finals", "prelim", "prelims"},
}

# Words that say nothing about WHICH standings: gender, distance, the
# section letter or number, a school-size class, and connective filler.
# They are read past, never counted as recognition on their own.
_FILLER = {
    "boys", "boy", "girls", "girl", "men", "mens", "women", "womens",
    "male", "female", "coed", "co-ed", "mixed",
    "mile", "miles", "meter", "meters", "metre", "metres", "km",
    "kilometer", "kilometers", "yard", "yards", "run",
    "school", "schools", "high", "hs",
    "division", "div", "level", "team", "teams", "grade", "grades",
    "event", "events", "group", "wave",
    "i", "ii", "iii", "iv",
    "a", "b", "c", "d", "and", "of", "the", "&", "-", "/", "+", "#",
}
_DISTANCE_TOKEN = re.compile(
    r"^\d+(?:\.\d+)?(?:k|km|m|mi|mile|miles|meters?|metres?|y|yd|yds|"
    r"mtr|mtrs)?$")
# ★ A SCHOOL-SIZE CLASS is read past BESIDE a level word ("Varsity Large
#   School", "Varsity Small", "Varsity 4A" are the varsity races of one
#   meet) but is not a level on its own: "Large School" and "Small School"
#   with no level word are two races the host scored apart, and nothing in
#   the label says they are one varsity field -- so they stay unrecognised.
_SIZE_WORDS = {"large", "small", "medium", "big", "class"}
# size classes: 1a..7a, aa/aaa/aaaa, d1..d3
_SIZE_TOKEN = re.compile(r"^(?:[1-7]a|a{2,5}|d[1-3]|class[1-7]a?)$")


def _tokens(label):
    s = (label or "").lower()
    # apostrophes out entirely: "Boys' Varsity", "Women's", "Women’s"
    s = s.replace("’", "").replace("'", "")
    for pat, rep in _PHRASES:
        s = re.sub(pat, rep, s)
    # split on whitespace and the punctuation hosts separate words with,
    # keeping '/' and '-' out of tokens (frosh/soph is already collapsed)
    return [t for t in re.split(r"[\s,;:()\[\]/\-_.|]+", s) if t]


def divisionGroup(label):
    """The standings group a division label belongs to: 'varsity', 'jv',
    'fs', 'ms', 'para', or 'other:<the label, normalised>' for anything not
    recognised. Blank (None, '', 'Heat 2', 'Girls 5K') is 'varsity'."""
    toks = _tokens(label)
    found, unknown, sized = set(), [], False
    for t in toks:
        cat = next((c for c in (PARA, MS, FS, JV, VARSITY)
                    if t in _CATEGORY_WORDS[c]), None)
        if cat:
            found.add(cat)
        elif t in _SIZE_WORDS or _SIZE_TOKEN.match(t):
            sized = True
        elif t in _FILLER or _DISTANCE_TOKEN.match(t):
            continue
        else:
            unknown.append(t)
    if unknown or (sized and not found):
        return "other:" + " ".join((label or "").lower().split())
    for cat in (PARA, MS, FS, JV, VARSITY):
        if cat in found:
            return cat
    # nothing but gender/distance/section filler, or nothing at all: the
    # meet's one undifferentiated field
    return VARSITY


def groupRank(group):
    """Sort key: the known groups in GROUP_ORDER, unrecognised after."""
    try:
        return GROUP_ORDER.index(group)
    except ValueError:
        return len(GROUP_ORDER)


def groupLabel(group):
    """'Varsity', 'JV', ...; an unrecognised group has no generic name."""
    return GROUP_LABELS.get(group)
