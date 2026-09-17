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

# Words that say nothing about WHICH school a name means, so a spelling that
# adds one is the same school ("Williams" / "Williams College"). Deliberately
# short: anything that can distinguish two institutions stays in the key.
_SUFFIX_NOISE = {
    "hs", "h", "s", "high", "school", "schools", "ms", "middle", "jhs",
    "junior", "senior", "college", "university", "univ", "academy",
    "the", "team", "tc", "xc", "track",
}


def splitStateSuffix(school):
    """('La Jolla', 'CA') for a name carrying a delimited state code, else
    (name, None). Pure.

    ! THE CODE HAS TO BE A REAL STATE. "Mid-Pacific" and "Tri-Valley" end in
      a hyphen and letters too; only a postal code is a decoration.
    """
    name = (school or "").strip()
    m = _DECORATION.search(name)
    if not m:
        return name, None
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
    words = [w for w in re.split(r"[^a-z0-9]+", base.lower()) if w]
    while words and words[-1] in _SUFFIX_NOISE:
        words.pop()
    return tuple(words)


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
