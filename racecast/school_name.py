# Project: xc-predictor / racecast
# File:    school_name.py
# Purpose: two spellings, one team. The pure half -- no database, no network.
#
# ★ THE BUG THIS EXISTS FOR (owner, 2026-09-17). One athlete, twice on one
#   board, because her team is spelled two ways:
#
#     15  Chiara Dailey  La Jolla (CA)  152.2  9:53.38  San Diego HS vs La Jolla HS
#     16  Chiara Dailey  La Jolla-CA    152.1  9:49.57  Brooks PR Invitation
#
#   "please make it so team substrings like this are found and merged (where
#   some athletes are the same!)"
#
# ⚠ AND THE SUBSTRING ALONE IS EXACTLY THE WRONG RULE, which the same
#   conversation proves: "Oregon" is a prefix of "Oregon Episcopal", of
#   "Oregon Clay" and of "Oregon School for the Deaf", and those are four
#   different schools in three states. A containment test on its own would
#   merge the whole set and undo the split this session just built.
#
# ★ SO THE SUBSTRING ONLY PROPOSES; THE ATHLETES DECIDE -- which is the
#   owner's own parenthesis, and the same shape as every other join in this
#   project that works (person_id is merged across the feeds, so shared
#   athletes need no spelling). La Jolla (CA) and La Jolla-CA are the same
#   roster; Oregon and Oregon Episcopal share nobody.
#
# ! EVERYTHING HERE IS PURE so the judgement can be tested without a corpus:
#   tests/test_school_name_variants.py. The evidence-gathering half lives in
#   scripts/merge_school_names.py.
import re

# The postal codes, plus DC and the territories that appear in the feeds.
#
# ! A SET, NOT A REGEX, because "OR", "IN", "ME", "HI" and "OK" are ordinary
#   English words and the delimiter is what makes them a decoration.
#
# ⚠ THE SIXTH COPY OF THIS LIST IN THE REPO, and deliberately its own:
#   rankings.US_STATES is the site's (50 + DC, territories left out ON
#   PURPOSE -- see its comment), pool_resolve.US_STATES adds the APO codes,
#   region_lookup has another, build_college_directory.STATES is names ->
#   codes. None of them is the right question here. This one asks only
#   "could these two letters be a state DECORATION on a school name", and a
#   feed does write "(PR)" and "(GU)", so the territories belong in it while
#   the APO codes do not. Importing rankings would also drag the site's
#   module graph into a file whose whole point is that it is pure.
STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}

# ★ THE FOUR SHAPES THE FEEDS ACTUALLY USE, and only those. A bare
#   "La Jolla CA" is NOT one of them: without a delimiter there is no way to
#   tell a decoration from a name ("Washington", "Indiana", and every school
#   whose last word happens to be two letters).
_DECORATION = re.compile(
    r"""\s*(?:
            \(\s*([A-Za-z]{2})\s*\)      # La Jolla (CA)
          | \[\s*([A-Za-z]{2})\s*\]      # La Jolla [CA]
          | [-–—]\s*([A-Za-z]{2})        # La Jolla-CA,  La Jolla - CA
          | ,\s*([A-Za-z]{2})            # La Jolla, CA
        )\s*$""",
    re.X)

# ★ AND A STATE WRITTEN OUT IN BRACKETS (owner, 2026-11-01: "Trinity" and
#   "Trinity (Conn.)", "Wesleyan" and "Wesleyan (Conn.)" on the NESCAC list).
#   tfrrs names colleges AP-style -- (Conn.), (N.Y.), (Ohio), (Calif.) --
#   which the two-letter shapes above never matched. In brackets only: a
#   bare "-Ohio" can be a name ("Miami-Ohio" is how some feeds spell it,
#   and that one is fine as it stands).
_AP_STATE = {
    "ala": "AL", "alabama": "AL", "alaska": "AK", "ariz": "AZ", "arizona": "AZ",
    "ark": "AR", "arkansas": "AR", "calif": "CA", "cal": "CA", "california": "CA",
    "colo": "CO", "colorado": "CO", "conn": "CT", "connecticut": "CT",
    "del": "DE", "delaware": "DE", "dc": "DC", "fla": "FL", "florida": "FL",
    "ga": "GA", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "ill": "IL",
    "illinois": "IL", "ind": "IN", "indiana": "IN", "iowa": "IA", "kan": "KS",
    "kans": "KS", "kansas": "KS", "ky": "KY", "kentucky": "KY", "la": "LA",
    "louisiana": "LA", "maine": "ME", "me": "ME", "md": "MD", "maryland": "MD",
    "mass": "MA", "massachusetts": "MA", "mich": "MI", "michigan": "MI",
    "minn": "MN", "minnesota": "MN", "miss": "MS", "mississippi": "MS",
    "mo": "MO", "missouri": "MO", "mont": "MT", "montana": "MT", "neb": "NE",
    "nebr": "NE", "nebraska": "NE", "nev": "NV", "nevada": "NV", "nh": "NH",
    "newhampshire": "NH", "nj": "NJ", "newjersey": "NJ", "nm": "NM",
    "newmexico": "NM", "ny": "NY", "newyork": "NY", "nc": "NC",
    "northcarolina": "NC", "nd": "ND", "northdakota": "ND", "ohio": "OH",
    "okla": "OK", "oklahoma": "OK", "ore": "OR", "oreg": "OR", "oregon": "OR",
    "pa": "PA", "penn": "PA", "pennsylvania": "PA", "ri": "RI",
    "rhodeisland": "RI", "sc": "SC", "southcarolina": "SC", "sd": "SD",
    "southdakota": "SD", "tenn": "TN", "tennessee": "TN", "tex": "TX",
    "texas": "TX", "utah": "UT", "vt": "VT", "vermont": "VT", "va": "VA",
    "virginia": "VA", "wash": "WA", "washington": "WA", "wva": "WV",
    "westvirginia": "WV", "wis": "WI", "wisc": "WI", "wisconsin": "WI",
    "wyo": "WY", "wyoming": "WY",
    "nmex": "NM", "sdak": "SD", "ndak": "ND", "wvirginia": "WV",
}
_BRACKET_TAIL = re.compile(r"\s*[\(\[]\s*([A-Za-z. ]{2,20})\s*[\)\]]\s*$")


def _bracketState(name):
    """('Trinity', 'CT') for 'Trinity (Conn.)', else None. Pure."""
    m = _BRACKET_TAIL.search(name)
    if not m:
        return None
    key = re.sub(r"[^a-z]", "", m.group(1).lower())
    code = _AP_STATE.get(key)
    # ★ ANY STATE'S TWO LETTERS, DOTTED OR NOT (owner, 2026-11-02: tfrrs's
    #   "Charleston (W.V.)" was a second school beside anet's "Charleston
    #   (W.Va.)", and its runners second people -- one of them #5 in the
    #   country off one race). The table only had the few codes AP style
    #   writes as letters; "(W.V.)" is "wv".
    if code is None and len(key) == 2 and key.upper() in STATE_CODES:
        code = key.upper()
    base = name[:m.start()].strip(" \t,-–—")
    return (base, code) if code and base else None


# ★ ONE WORD, ONE SPELLING (owner, 2026-11-01: "try to be exhaustive with
#   variants"): "Conn College" / "Connecticut College", "St. Mark's" /
#   "Saint Marks", "Mt. Hebron" / "Mount Hebron", "HS" / "High School".
#   Expanding an abbreviation can only make two spellings of one name
#   agree; the athletes still decide every merge.
_ABBR = {
    "mt": "mount", "ft": "fort", "pt": "point", "hts": "heights",
    "spgs": "springs", "spg": "springs", "vly": "valley", "lk": "lake",
    "twp": "township", "cty": "county", "ctr": "center", "cntr": "center",
    "hs": "high school", "ms": "middle school", "jhs": "junior high school",
    "jr": "junior", "sr": "senior", "acad": "academy", "prep": "preparatory",
    "sch": "school", "elem": "elementary", "intl": "international",
    "inst": "institute", "univ": "university", "coll": "college",
    "conn": "connecticut", "mass": "massachusetts", "cath": "catholic",
    "chr": "christian", "reg": "regional", "voc": "vocational",
    "sci": "science", "tech": "tech", "assn": "association",
}
_DIRECTION = {"n": "north", "s": "south", "e": "east", "w": "west",
              "ne": "northeast", "nw": "northwest", "se": "southeast",
              "sw": "southwest"}
_LEVEL_WORDS = {"high", "school", "college", "university", "middle", "junior",
                "senior", "community"}


def _words(base):
    """A name's words, lowercased and spelled one way. Pure."""
    t = base.lower().replace("&amp;", "&").replace("&", " and ")
    t = re.sub(r"[\'’`]", "", t)                       # St. Mark's = St Marks
    raw = [w for w in re.split(r"[^a-z0-9]+", t) if w]
    out = []
    for i, w in enumerate(raw):
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        if w == "st":
            # "St. Olaf", "Mount St. Mary's" -> saint; "Penn St.", "Ohio St.
            # University" -> state: saint before a name, state at the end
            out.append("saint" if nxt and nxt not in _LEVEL_WORDS
                       and nxt not in ("univ", "coll", "hs") else "state")
        elif w in _DIRECTION and i == 0 and nxt and len(nxt) >= 3:
            out.append(_DIRECTION[w])                   # "N. Central"
        else:
            out.extend(_ABBR.get(w, w).split())
    return out


# ⚠⚠ LEVEL WORDS ARE NOT NOISE, AND THE FIRST DRY RUN PROVED IT (2026-09-17).
#    This set used to hold "middle", "ms", "high", "hs", "junior", "senior",
#    "college", "university", "academy" -- on the theory that "Williams" and
#    "Williams College" are one team. The corpus answered:
#
#      Adrian            <- Adrian College (38)  AND  Adrian Middle School (5)
#      Cumberland        <- Cumberland High School  AND  Cumberland Middle School
#      Norwell           <- Norwell Middle School (329 athletes)
#      Sanford           <- Sanford High School  AND  Sanford School
#
#    A middle school is not its high school and a college is not either. That
#    is the Amherst College / Amherst Regional split this whole branch is
#    about, and the shared athletes are REAL -- a kid runs the middle school
#    and then the high school -- which is exactly why the athlete evidence
#    cannot be trusted to overrule a level word. `school_level` exists to
#    keep these apart; this must not undo it.
#
# ★ SO ONLY DECORATION MERGES: a state suffix, case, and punctuation. That is
#   narrower than intended and it is the case that was actually reported --
#   "La Jolla (CA)" and "La Jolla-CA". A shortening like "Williams" /
#   "Williams College" is a `prefix` candidate, which needs --prefix and a
#   far higher bar.
_SUFFIX_NOISE = {"the"}

# ⚠⚠ A ROSTER STATUS IS NOT A TEAM, AND SHARED ATHLETES SAY NOTHING ABOUT IT.
#    The first dry run folded FORTY spellings of "Unattached" into one
#    "school" of several thousand athletes -- Unattached (IL), Unattached-IN,
#    UNATTACHED, 42-UNATTACHED, "Unattached - High School" ... every one of
#    them sharing athletes with every other, because they are all the same
#    sentinel and not the same roster. The whole premise of this module ("the
#    same people wear both spellings, so it is one team") is void for a string
#    that means "no team".
#
# ! THE REPO ALREADY KNEW. racecast/panels.isTeamName is this exact judgement
#   and is what the boards use; engine/normalize_distance._NON_SCHOOLS is the
#   same list again. Copied rather than imported because this module is pure
#   and panels is not -- tests/test_school_name_variants.py pins them equal so
#   they cannot drift.
_NOT_A_TEAM_EXACT = frozenset({
    "unat", "none", "n/a", "na", "n a", "no team", "no school", "independent",
    "individual", "individuals", "club", "open", "unattached",
    "unattached runner", "alumni", "guest",
})
_NOT_A_TEAM_FRAGMENTS = ("unattached", "individual", "independent",
                         "no team", "no school")

# A name has to have some name in it. "-mi" and "MI" both reduce to "mi" and
# were merged with each other; two letters is a state code or a typo, never a
# school.
MIN_KEY_CHARS = 3


def isRosterStatus(school):
    """True when the string means "no team" rather than naming one. Pure."""
    s = (school or "").strip().lower()
    if not s:
        return True
    if s in _NOT_A_TEAM_EXACT:
        return True
    return any(frag in s for frag in _NOT_A_TEAM_FRAGMENTS)


def mergeable(school):
    """May this string take part in a merge at all? Pure."""
    if isRosterStatus(school):
        return False
    return len(nameKey(school).replace(" ", "")) >= MIN_KEY_CHARS


def splitStateSuffix(school):
    """('La Jolla', 'CA') for a name carrying a delimited state code, else
    (name, None). Pure.

    ! THE CODE HAS TO BE A REAL STATE. "Mid-Pacific" and "Tri-Valley" end in
      a hyphen and letters too; only a postal code is a decoration.
    """
    name = (school or "").strip()
    m = _DECORATION.search(name)
    if not m:
        got = _bracketState(name)
        return got if got else (name, None)
    code = next(g for g in m.groups() if g).upper()
    if code not in STATE_CODES:
        return name, None
    base = name[:m.start()].strip(" \t,-–—")
    # ! A DECORATION CANNOT BE THE WHOLE NAME. "(CA)" on its own is not a
    #   school called "" in California.
    return (base, code) if base else (name, None)


def nameWords(school):
    """The identifying words of a name, lowercased, with the trailing noise
    dropped. Pure. ('Williams College' and 'Williams' -> ('williams',))"""
    base, _state = splitStateSuffix(school)
    words = _words(base)
    while words and words[-1] in _SUFFIX_NOISE:
        words.pop()
    if words and words[0] == "the" and len(words) > 1:
        words = words[1:]
    return tuple(words)


def levelWords(school):
    """nameWords without the level words: 'Williams College' -> ('williams',),
    'University of Michigan' -> ('michigan',), 'La Jolla High School' ->
    ('la', 'jolla'). Pure.

    ! A KEY FOR PROPOSING, NEVER FOR DECIDING. This is the key the first dry
      run of 2026-09-17 was burnt by (Adrian <- Adrian Middle School); a pair
      it proposes merges only when both spellings race at the SAME level
      (merge_school_names.levels), in one state, with the athletes to show
      it."""
    words = list(nameWords(school))
    if len(words) > 2 and words[1] == "of" and words[0] in ("university", "college"):
        words = words[2:]
    while len(words) > 1 and words[-1] in _LEVEL_WORDS:
        words.pop()
    return tuple(words)


def levelKey(school):
    return " ".join(levelWords(school))


def statedState(school):
    """The state a spelling writes for itself ('Trinity (Conn.)' -> CT,
    'La Jolla-CA' -> CA), or None. Pure."""
    return splitStateSuffix(school)[1]


def nameKey(school):
    """The comparison key for a school string: identifying words, joined.
    Two spellings of one team have the same key; two schools do not."""
    return " ".join(nameWords(school))


def sameKey(a, b):
    """Do these two strings differ only in decoration? Pure."""
    ka, kb = nameKey(a), nameKey(b)
    return bool(ka) and ka == kb


def isPrefixOf(a, b):
    """Is `a`'s key a strict WORD prefix of `b`'s? Pure.

    ⚠ WORDS, NOT CHARACTERS. "Oregon" is a character prefix of "Oregonian"
      and of "Oregon Episcopal"; only the second is a word prefix, and even
      that one is merely a CANDIDATE -- the athletes decide.
    """
    wa, wb = nameWords(a), nameWords(b)
    return bool(wa) and len(wa) < len(wb) and wb[:len(wa)] == wa


def relation(a, b):
    """'same' when two strings are one team's two spellings, 'prefix' when
    one might be a shortening of the other, else None. Pure -- and 'prefix'
    is a QUESTION, never an answer: see the header."""
    if not (nameWords(a) and nameWords(b)):
        return None
    if sameKey(a, b):
        return None if a == b else "same"
    if isPrefixOf(a, b) or isPrefixOf(b, a):
        return "prefix"
    return None


def canonicalOf(spellings):
    """Which spelling a group should be filed under, given
    [(school, n_athletes), ...]. The most-used one; ties to the shortest,
    then alphabetically, so the answer is stable across runs. Pure.

    ! THE MOST USED, NOT THE TIDIEST. Renaming a team to the spelling nobody
      wrote moves every board row and every link for a cosmetic gain.
    """
    if not spellings:
        return None
    return min(spellings, key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0]


# ⚠ NO CHAINS REACH A READER. merge_school_names writes one canonical per
#   GROUP, so a variant is never also somebody's canonical -- but a
#   hand-written row could be, and a reader that has to walk a chain is a
#   reader that can loop. Flattened once, where it is cheap, and a cycle
#   resolves to a fixed point rather than hanging.
def resolveChains(alias):
    """{variant: canonical} with every chain followed to its end. Pure;
    returns a new dict and does not mutate the argument."""
    out = {}
    for variant, target in alias.items():
        seen = {variant}
        while target in alias and target not in seen:
            seen.add(target)
            target = alias[target]
        out[variant] = target
    return out
