"""
course_name_rules.py -- semantic vetoes for course-name matching.

Separate from course_identity.py on purpose. That module does MECHANICAL
normalization: case, accents, punctuation -- transformations that cannot
change which venue a name refers to. This module encodes SEMANTIC knowledge
about what distinguishes two venues, which is a different kind of claim and
deserves its own file so it can be audited on its own.

WHY THIS EXISTS -- measured from diag_course_clusters.py output
    Name similarity alone cannot separate correct from incorrect merges,
    because they occupy the same score band:

        sim 0.67 CORRECT   Del Valle HS      <-> Del Valle High School
        sim 0.67 WRONG     Delta Middle Sch  <-> Delta High School

    No threshold splits those. Raising it to 0.7 destroys every
    "HS <-> High School" merge, the most common correct case in the corpus.
    So these are hard vetoes applied INDEPENDENTLY of the score.

DESIGN RULE
    Every veto here BLOCKS merges -- none of them cause one. The worst case
    is fragmentation, which leaves the status quo intact. That is deliberate:
    course_id was rejected for making bad merges, and this must not
    reintroduce the same failure mode by a different route.
"""

from collections import Counter


# ---------------------------------------------------------------------------
# ABBREVIATION EXPANSION
#   Runs BEFORE scoring, so correct pairs score higher and the threshold can
#   be raised without losing them.
# ---------------------------------------------------------------------------

# Values may be multi-word; the expansion re-splits afterwards.
#
# DELIBERATELY EXCLUDED, do not add without evidence:
#   "st" -- Saint or Street. "St. Clair Park" vs "Grade School St".
#   "cc" -- Country Club or Community College. Both are common here.
#   "gc" -- Golf Club or Golf Course.
#   "u"  -- University, or a real word.
# An ambiguous expansion is a bad merge waiting to happen, and bad merges
# are the thing we cannot undo.
_ABBREVIATIONS = {
    "hs": "high school",
    "ms": "middle school",
    "jhs": "junior high school",
    "jh": "junior high",
    "elem": "elementary",
    "univ": "university",
    "mt": "mount",
    "ft": "fort",
    "rec": "recreation",
    "ctr": "center",
    "jr": "junior",
    "sr": "senior",
}


def expandAbbreviations(canonical):
    """
    Expand known abbreviations in an already-normalized name.

    Input MUST have been through normalizeCourseName first -- this matches on
    whole lowercase tokens and would miss "H.S." or "HS".

        "walsh jesuit hs"  ->  "walsh jesuit high school"
        "hulcy steam ms"   ->  "hulcy steam middle school"

    Effect on scoring, measured on real pairs:
        before:  "walsh jesuit hs" vs "walsh jesuit high school"  = 0.67
        after:   identical strings                                = 1.00

    That lift is what makes --min-sim 0.8 safe. Without expansion, 0.8 would
    reject every HS/High School pair in the corpus.

    Unknown tokens pass through untouched -- .get(word, word) returns the
    word itself when it is not a key.
    """
    if not canonical:
        return ""

    expanded = [_ABBREVIATIONS.get(word, word) for word in canonical.split()]

    # Re-join then re-split: an expansion like "high school" arrives as one
    # string and must become two tokens for word-level comparison to work.
    return " ".join(" ".join(expanded).split())


# ---------------------------------------------------------------------------
# VETO 1 -- DIRECTIONAL AND NUMERIC QUALIFIERS
# ---------------------------------------------------------------------------

# THE BUG THIS FIXES, exactly:
#   SequenceMatcher("east", "west").ratio() == 0.75
#   They share "est" -- 3 of 4 characters -- and _TOKEN_MATCH_THRESHOLD is
#   0.75, so "east" MATCHES "west". That is how West Bend East merged with
#   West Bend West. Character similarity is precisely the wrong tool for
#   words whose whole job is to distinguish.
_QUALIFIER_WORDS = frozenset({
    "north", "south", "east", "west",
    "northeast", "northwest", "southeast", "southwest",
    "central", "upper", "lower",
})


def _isDistinguishingNumber(word):
    """
    Digits that identify a SUB-VENUE vs digits that are incidental.

    Keep:  "Field 2" vs "Field 3"        -- genuinely different places
           "Tom Bass Regional Park Section 1"
    Drop:  "JFK 2021 XC Course"          -- the 2021 running of one meet
           "1400 cherokee blvd"          -- a street number on Cherokee Blvd

    4+ digits means a year or an address, neither of which distinguishes a
    venue from itself.
    """
    return word.isdigit() and len(word) < 4


def qualifierTokens(expanded):
    """
    Multiset (Counter) of directional/numeric qualifiers in a name.

    A COUNTER, not a set, and that distinction is load-bearing:

        "west bend high school"  ->  {west: 1}
        "west bend west"         ->  {west: 2}

    As sets both are {west} and the veto misses. As counters they differ and
    the merge is blocked -- which is correct, since West Bend West is a
    separate school from West Bend High.
    """
    tokens = Counter()

    for word in expanded.split():
        if word in _QUALIFIER_WORDS or _isDistinguishingNumber(word):
            tokens[word] += 1

    return tokens


# ---------------------------------------------------------------------------
# VETO 2 -- SCHOOL LEVEL
# ---------------------------------------------------------------------------

_ELEMENTARY_WORDS = frozenset({"elementary", "primary"})
_MIDDLE_WORDS = frozenset({"middle", "intermediate"})
_HIGH_WORDS = frozenset({"high"})


def schoolLevel(expanded):
    """
    'elementary' | 'middle' | 'high' | None

    ORDER MATTERS and is not alphabetical.

    "junior high" is checked first as a PHRASE, because in US usage it means
    middle school -- but it contains the word "high", so a word-level check
    would classify it as a high school and merge a junior high with the local
    high school. Substring search on the whole string catches the phrase
    before the word check can misfire.

    Elementary is checked before high for the same class of reason:
    "Greensburg Community High School" is high, but a name like
    "Elementary and High Campus" should fall to the more specific label.

    KNOWN IMPRECISION: combined schools like "Mayville Middle & High School"
    and "South Knox HS/MS" resolve to a single level and get vetoed against
    their own single-level spelling. That is fragmentation, which is the safe
    failure. Fixing it needs a multi-level return type; not worth it yet.
    """
    if "junior high" in expanded:
        return "middle"

    words = set(expanded.split())

    if words & _ELEMENTARY_WORDS:
        return "elementary"
    if words & _MIDDLE_WORDS:
        return "middle"
    if words & _HIGH_WORDS:
        return "high"

    return None


# ---------------------------------------------------------------------------
# VETO 3 -- FACILITY TYPE (head noun)
# ---------------------------------------------------------------------------

# In English place names the head noun lands last: "Van Wert County
# FAIRGROUNDS", "Battle Run CAMPGROUND". If both names end in a KNOWN
# facility word and those words differ, they are different kinds of place.
#
# The vocabulary gate is what makes this safe. Without it, "Walsh Jesuit HS"
# vs "Walsh Jesuit" compares "school" against "jesuit" and blocks a correct
# merge. "jesuit" is not a facility word, so no veto fires and the truncated
# name still merges. Same protection for "Watkins Memorial" and
# "Pickerington North".
_FACILITY_WORDS = frozenset({
    "park", "school", "academy", "college", "university", "campus",
    "complex", "stadium", "arena", "course", "club", "center", "centre",
    "campground", "fairgrounds", "hospital", "church", "field", "fields",
    "ranch", "farm", "reserve", "preserve", "trail", "trails",
    "road", "street", "lake", "beach", "gym", "track",
})


def facilityType(expanded):
    """
    The trailing facility word, or None if the name does not end in one.

    None means "cannot tell" and must never veto -- an unknown head noun is
    usually a truncated name, which is exactly the case we want to merge.
    """
    words = expanded.split()

    if not words:
        return None

    last = words[-1]
    return last if last in _FACILITY_WORDS else None


# ---------------------------------------------------------------------------
# THE COMBINED CHECK
# ---------------------------------------------------------------------------

def blocksMerge(expandedA, expandedB):
    """
    Should this pair be vetoed? -> (blocked: bool, reason: str)

    Both arguments must have gone through normalizeCourseName THEN
    expandAbbreviations.

    Returns a REASON, not just a boolean, so the diagnostic can print why each
    merge was refused. A veto you cannot inspect is a veto you cannot audit,
    and these rules encode judgement calls that deserve review.

    Every branch requires BOTH sides to carry the signal. If one name is
    silent on level or facility, that is missing information, not a conflict,
    and missing information never blocks. Same discipline as nameSimilarity
    returning 0.0 for empty input: absence is not evidence.

    Verified against real pairs from the diagnostic:
        BLOCKS   west bend high school  / west bend east high school  (qualifier)
        BLOCKS   delta middle school    / delta high school           (level)
        BLOCKS   battle run park        / battle run campground       (facility)
        BLOCKS   van wert county fairgrounds / van wert county hospital
        ALLOWS   walsh jesuit high school / walsh jesuit          (unknown head)
        ALLOWS   cove lake state park   / cove lake               (containment)
        ALLOWS   pau wa lu middle school / paw wu lu middle school (all agree)
    """
    qualifiersA = qualifierTokens(expandedA)
    qualifiersB = qualifierTokens(expandedB)
    if qualifiersA != qualifiersB:
        return (True, f"qualifier {dict(qualifiersA)} vs {dict(qualifiersB)}")

    levelA = schoolLevel(expandedA)
    levelB = schoolLevel(expandedB)
    if levelA is not None and levelB is not None and levelA != levelB:
        return (True, f"level {levelA} vs {levelB}")

    facilityA = facilityType(expandedA)
    facilityB = facilityType(expandedB)
    if facilityA is not None and facilityB is not None and facilityA != facilityB:
        wordsA = set(expandedA.split())
        wordsB = set(expandedB.split())

        # CONTAINMENT ESCAPE HATCH.
        # If either name already carries the OTHER's head noun, one name is an
        # extension of the other, not a different place:
        #     "cove lake state park" contains "lake"               -> same venue
        #     "mcfarland high school sports complex" has "school"  -> same venue
        # True positives never do this:
        #     "van wert county fairgrounds" has no "hospital"  -> veto stands
        #     "battle run park" has no "campground"            -> veto stands
        if facilityB not in wordsA and facilityA not in wordsB:
            return (True, f"facility {facilityA} vs {facilityB}")

    return (False, "")