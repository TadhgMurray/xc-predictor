"""
course_identity.py -- course identity primitives.

Pure functions: no database, no I/O, no printing, no writes. Import from a
report or a pipeline step. Safe to poke at in a REPL with no connection.

WHY THIS EXISTS
    course_difficulties is keyed on course_name alone. That splits one venue
    across its spellings ("Crystal Springs" vs "Crystal Springs XC Course"),
    so each fragment gets its own difficulty fitted on a fraction of the data.

WHY NOT course_id
    Measured: course_id merges DISTINCT venues. id 363 covers three different
    golf courses; id 0 covers 18,387 names. It behaves like a meet-series id
    that survived a venue change, not a venue id. Never use it for identity.

THE APPROACH -- two weak signals that fail in opposite directions
    GPS alone   over-merges where the NAME was vague and the geocoder
                returned a region centroid (Columbus: 4 unrelated venues).
    Name alone  over-merges where the name is GENERIC
                (18 different "Central Park"s, spread over 271 deg of lng).

    So: BLOCK by GPS proximity, then DECIDE by name similarity within the
    block, then VETO on semantic rules. Fuzzy name matching is only safe at
    short range -- globally, "Central Park" vs "Central Park" is a perfect
    match and a catastrophe.
"""

import difflib
import math
import re
import unicodedata
from collections import defaultdict


# ---------------------------------------------------------------------------
# 1. NAME NORMALIZATION
#    Reduce a name to a canonical form. We never ask what a name MEANS
#    (that is dead-end #2 -- never parse a name for a property). We only ask
#    whether two strings are the same name written differently. Name as an
#    opaque identity key is matching, not parsing, and that is allowed.
# ---------------------------------------------------------------------------

# Decorative words that appear in some spellings of a venue and not others.
#
# KEEP THIS SET SMALL. Every word added is a merge you cannot undo, and bad
# merges are exactly why course_id was rejected. When unsure, leave the word
# IN -- that fragments, and fragmentation is safe.
#
# DO NOT ADD "park". Measured: it is the only distinguishing token in
# "Riverside Park", "Central Park", "Memorial Park", "Lincoln Park" -- the top
# of the name-collision list. Stripping it detonates all of them.
_DECORATIVE_WORDS = frozenset({
    "xc", "cross", "country", "course", "official", "the",
})


def _foldAccents(text):
    """
    'Beau Chene' with a circumflex -> plain ASCII 'Beau Chene'.

    NFKD is a Unicode DECOMPOSITION: it rewrites an accented character as a
    base letter plus a separate combining mark. Once split, the marks can be
    dropped and the base letters kept.

    unicodedata.category(ch) returns a two-letter class. 'Mn' means
    "Mark, nonspacing" -- exactly the combining accents.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _stripPunctuation(text):
    """
    "Fort St. Clair's Park" -> "Fort St Clair s Park"

    The class [^a-z0-9]+ means "one or more characters that are NOT a
    lowercase letter and NOT a digit" -- the leading ^ inside [] negates it.

    Replaced with a SPACE, not deleted. "Hedges-Boyer" must become
    "Hedges Boyer" (two words) so it matches "Hedges Boyer". Deleting would
    give "HedgesBoyer" and the variants would never agree.

    Assumes text is already lowercased -- the class only knows lowercase.
    """
    return re.sub(r"[^a-z0-9]+", " ", text)


def _dropDecorativeWords(text):
    """
    "crystal springs xc course" -> "crystal springs"

    .split() with no argument splits on any whitespace run AND discards empty
    strings, so it collapses runs and trims in one step.
    """
    kept = [word for word in text.split() if word not in _DECORATIVE_WORDS]
    return " ".join(kept)


def normalizeCourseName(raw):
    """
    Reduce a raw course_name to a canonical comparison key.

    Returns '' for NULL or blank. An empty key must NEVER match another empty
    key -- that is the gender='' bug's exact shape, where a missing value was
    treated as a real one and routed rows into the wrong pool. Callers check
    for '' explicitly.

    Order matters: lowercase BEFORE _stripPunctuation.

    Observed behaviour:
        "Hedges-Boyer Park "       -> "hedges boyer park"
        "Hedges Boyer Park"        -> "hedges boyer park"    (merges)
        "Albuquerque Acadamey"     -> "albuquerque acadamey"
        "Albuquerque Academy"      -> "albuquerque academy"  (still split)

    The typo staying split is CORRECT here. This function only handles
    mechanical variants. Typos and abbreviations are caught downstream by
    nameSimilarity, at short range where it is safe to be fuzzy.
    """
    if raw is None:
        return ""

    lowered = raw.lower()
    folded = _foldAccents(lowered)
    depunctuated = _stripPunctuation(folded)
    return _dropDecorativeWords(depunctuated)


# ---------------------------------------------------------------------------
# 2. GEOGRAPHY -- blocking
#    Answers "which venues are worth comparing", nothing more. Deliberately
#    generous: precision comes from the name check and the vetoes afterwards.
# ---------------------------------------------------------------------------

def haversineMeters(lat1, lng1, lat2, lng2):
    """
    Great-circle distance in METRES.

    Real distance, not degree difference: 1 deg of longitude is ~111 km at the
    equator but ~85 km at latitude 40. This corpus spans Guam (13N) to Alaska,
    so a fixed degree threshold would mean wildly different real distances in
    different places.

    6_371_000 is Earth's mean radius in metres; the underscores are digit
    separators, ignored by Python.
    """
    earthRadiusMeters = 6_371_000

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    deltaPhi = math.radians(lat2 - lat1)
    deltaLambda = math.radians(lng2 - lng1)

    # Square of half the chord length between the two points.
    a = (math.sin(deltaPhi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(deltaLambda / 2) ** 2)

    # atan2(y, x) is a two-argument arctangent, numerically stable at very
    # small distances where a plain atan loses precision.
    return earthRadiusMeters * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _gridKey(lat, lng, cellDegrees):
    """
    Snap a coordinate to a grid cell, as an (row, col) integer tuple.

    math.floor rounds toward NEGATIVE INFINITY, not toward zero.
    int(-0.5) == 0 but math.floor(-0.5) == -1. Half these longitudes are
    negative (the Americas), so int() would fold cells on opposite sides of
    the prime meridian and the equator into each other.

    Tuple because tuples are hashable and can be dict keys; a list cannot.
    """
    return (math.floor(lat / cellDegrees), math.floor(lng / cellDegrees))


def _neighborKeys(key):
    """
    The 9 cells of the 3x3 square centred on `key`.

    EXISTS BECAUSE OF A REAL BUG CLASS. Grid cells have hard edges, so two
    points 5 m apart land in different cells if they straddle a boundary, and
    would never be compared. Checking the 8 neighbours guarantees anything
    within one cell-width is always considered.

    Costs ~9x more candidate pairs, which is nothing -- the exact haversine
    check discards the far ones immediately. Blocking must be GENEROUS and
    CHEAP; precision comes later.
    """
    row, col = key
    return [(row + dRow, col + dCol)
            for dRow in (-1, 0, 1)
            for dCol in (-1, 0, 1)]


def buildGridIndex(meets, cellDegrees=0.01):
    """
    Bucket meets by grid cell -> {gridKey: [meet, ...]}

    `meets` is an iterable of dicts with 'gps_lat' and 'gps_long'.

    cellDegrees=0.01 is ~1.1 km of latitude. With the 3x3 neighbour search,
    any point within ~1.1 km is guaranteed to be considered. If you ever raise
    the search radius above ~1000 m, RAISE cellDegrees TOO or true matches
    start falling outside the 3x3 and get silently missed.

    Rows with missing or zero coordinates are SKIPPED, never bucketed under a
    default key. Bucketing them together would build one giant fake cluster --
    the same shape as course_id's 18,387-name zero bucket. A row we cannot
    place is a row we leave alone.
    """
    index = {}

    for meet in meets:
        lat = meet.get("gps_lat")
        lng = meet.get("gps_long")

        if lat is None or lng is None or lat == 0 or lng == 0:
            continue

        key = _gridKey(lat, lng, cellDegrees)
        # setdefault returns the existing list, or inserts a fresh [] and
        # returns that -- one lookup instead of an 'if key not in index'.
        index.setdefault(key, []).append(meet)

    return index


# ---------------------------------------------------------------------------
# 3. NAME SIMILARITY -- deciding
#    ONLY SAFE INSIDE A BLOCK. Globally, "Central Park" vs "Central Park"
#    scores 1.00 and merging them would be a disaster. The grid earns the
#    right to be fuzzy.
# ---------------------------------------------------------------------------

# How close two single words must be to count as the same word.
# 0.75 admits "acadamey"/"academy" (~0.80) and rejects "hs"/"high" (~0.33).
# Raise to merge less. Every merge is unrecoverable, so when unsure, raise.
#
# KNOWN LIMITATION: SequenceMatcher("east","west").ratio() == 0.75 exactly,
# so this treats east and west as the same word. That is handled by the
# qualifier veto in course_name_rules.py, NOT by tuning this number -- raising
# it to 0.8 would also break "acadamey"/"academy".
_TOKEN_MATCH_THRESHOLD = 0.75


def _tokenSimilarity(wordA, wordB):
    """
    Character-level similarity of two single words, 0.0 to 1.0.

    difflib is Python stdlib -- nothing to install. SequenceMatcher finds
    matching subsequences; .ratio() is 2 * matched / total, so identical
    strings give 1.0 and strings sharing nothing give 0.0.

    SequenceMatcher's first argument is `isjunk`, a predicate for characters
    to ignore. None means ignore nothing; every character counts.
    """
    if wordA == wordB:
        return 1.0

    return difflib.SequenceMatcher(None, wordA, wordB).ratio()


def _hasCloseMatch(word, candidateWords):
    """
    True if `word` resembles ANY word in `candidateWords`.

    any() over a generator stops at the first True -- it does not score every
    candidate before deciding.
    """
    return any(
        _tokenSimilarity(word, candidate) >= _TOKEN_MATCH_THRESHOLD
        for candidate in candidateWords
    )


def nameSimilarity(canonicalA, canonicalB):
    """
    Fraction of the SHORTER name present in the longer one, 0.0 to 1.0.

    Both arguments must ALREADY have gone through normalizeCourseName (and,
    in the current pipeline, expandAbbreviations). Passing raw names silently
    produces bad scores, because case and punctuation would register as
    character differences.

    Asymmetric on purpose: a short name fully contained in a long one should
    score high, because that is what abbreviation looks like.

    Empty input returns 0.0, never 1.0. Two unknown names are not the same
    venue -- see the gender='' note in normalizeCourseName.

    Worked examples (post-expansion):
        "walsh jesuit high school" vs "walsh jesuit high school"  -> 1.00
        "albuquerque acadamey" vs "albuquerque academy"           -> 1.00
        "central park" vs "central park"                          -> 1.00

    That last case is NOT a defect. Two of the 18 Central Parks score a
    perfect name match; the only thing keeping them apart is that they are
    3,000 km apart and never land in the same block.
    """
    if not canonicalA or not canonicalB:
        return 0.0

    wordsA = canonicalA.split()
    wordsB = canonicalB.split()

    if not wordsA or not wordsB:
        return 0.0

    # Iterate the SHORTER list so the score means "fraction of the shorter
    # name accounted for". Iterating the longer one would penalise legitimate
    # abbreviations for the words they omit.
    if len(wordsA) <= len(wordsB):
        shorter, longer = wordsA, wordsB
    else:
        shorter, longer = wordsB, wordsA

    matched = sum(1 for word in shorter if _hasCloseMatch(word, longer))
    return matched / len(shorter)


# ---------------------------------------------------------------------------
# 4. PAIRING AND CLUSTERING
# ---------------------------------------------------------------------------

def findMergePairs(venues, radiusMeters, minNameSimilarity,
                   cellDegrees=0.01, blockFn=None):
    """
    Every pair of venues close enough, similarly named enough, and not vetoed.

    `venues` is a list of dicts, each needing:
        'idx'        int, its position in the list
        'gps_lat'    float
        'gps_long'   float
        'canonical'  str, already through normalizeCourseName (+ expansion)

    `blockFn` is an optional (canonicalA, canonicalB) -> (blocked, reason)
    predicate. Passed IN rather than imported so this module stays pure
    geometry and strings, and can be tested with no rules loaded.

    Returns [(idxA, idxB, distanceMeters, similarity), ...] with idxA < idxB.

    THE THREE FILTERS RUN CHEAPEST-FIRST and the order is deliberate:
        1. distance    -- pure arithmetic, discards the vast majority
        2. similarity  -- runs difflib, ~100x slower than haversine
        3. blockFn     -- only ever sees pairs that already passed both
    """
    index = buildGridIndex(venues, cellDegrees)
    pairs = []

    for cellKey, bucket in index.items():
        # Everything worth comparing against this cell: itself + 8 neighbours.
        candidates = []
        for neighborKey in _neighborKeys(cellKey):
            candidates.extend(index.get(neighborKey, []))

        for venueA in bucket:
            for venueB in candidates:
                idxA = venueA["idx"]
                idxB = venueB["idx"]

                # Emit each pair once; never compare a venue to itself.
                #
                # This one line does BOTH jobs. A pair in neighbouring cells
                # is visited twice, once from each cell -- but the second time
                # the roles are swapped, so idxA > idxB and we skip. No 'seen'
                # set needed.
                if idxA >= idxB:
                    continue

                distance = haversineMeters(
                    venueA["gps_lat"], venueA["gps_long"],
                    venueB["gps_lat"], venueB["gps_long"],
                )
                if distance > radiusMeters:
                    continue

                similarity = nameSimilarity(venueA["canonical"],
                                            venueB["canonical"])
                if similarity < minNameSimilarity:
                    continue

                # SEMANTIC VETO. Applied AFTER the score, because correct and
                # incorrect merges occupy the SAME score band and no threshold
                # separates them:
                #     sim 0.67 CORRECT  Del Valle HS      <-> Del Valle High School
                #     sim 0.67 WRONG    Delta Middle Sch  <-> Delta High School
                # These are hard rules, independent of similarity.
                if blockFn is not None:
                    blocked, _reason = blockFn(venueA["canonical"],
                                               venueB["canonical"])
                    if blocked:
                        continue

                pairs.append((idxA, idxB, distance, similarity))

    return pairs


class _DisjointSet:
    """
    Union-Find: tracks which items belong to the same group.

    Needed because merging is TRANSITIVE and pairs arrive in arbitrary order.
    Given (A,B) then (B,C), A and C are the same venue -- but nothing ever
    compared them directly. This works that out.

    Each item points at a 'parent'. Follow parents up to reach a root; two
    items in the same group share a root.
    """

    def __init__(self):
        self._parent = {}

    def find(self, item):
        """
        The root of item's group, registering item if new.

        Two passes. The first walks up to the root. The second is PATH
        COMPRESSION: re-point every node visited directly at the root, so the
        next lookup is one hop. Without it, long chains make repeated finds
        quadratic.

        Iterative, not recursive: 22K venues could in principle chain deeper
        than Python's default recursion limit of 1000, and that crash would
        only ever show up on unusual data.
        """
        if item not in self._parent:
            self._parent[item] = item
            return item

        root = item
        while self._parent[root] != root:
            root = self._parent[root]

        # Re-point the whole path at root. The tuple assignment evaluates the
        # right side FIRST, so self._parent[item] is read before it is
        # overwritten.
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]

        return root

    def union(self, itemA, itemB):
        """Merge two groups. No-op if already together."""
        rootA = self.find(itemA)
        rootB = self.find(itemB)

        if rootA != rootB:
            self._parent[rootB] = rootA


def buildClusters(venueCount, pairs):
    """
    Turn merge pairs into groups -> {rootIdx: [idx, ...]}

    EVERY venue appears, including unmerged ones, as groups of size 1.
    Callers filter for len > 1 when they want only proposed merges.

    Singletons are returned on purpose: "merged with nothing" is a real
    answer, not a missing one. If venues could silently vanish here, a bug
    that dropped rows would look identical to a venue that simply did not
    merge -- and the totals in the report would not tie back to the input.

    ⚠ TRANSITIVITY IS NOT VETO-AWARE. Union-Find will still chain A-B-C even
    if A and C are individually vetoed, as long as A-B and B-C both passed.
    The West Bend case survives this only because EVERY cross-group pair is
    vetoed, leaving no bridge. A cluster that looks wrong after the vetoes are
    live is most likely a chain -- check whether some middle name bridges two
    groups that should stay apart.
    """
    disjointSet = _DisjointSet()

    for idx in range(venueCount):
        disjointSet.find(idx)

    for idxA, idxB, _distance, _similarity in pairs:
        disjointSet.union(idxA, idxB)

    clusters = defaultdict(list)
    for idx in range(venueCount):
        clusters[disjointSet.find(idx)].append(idx)

    return dict(clusters)