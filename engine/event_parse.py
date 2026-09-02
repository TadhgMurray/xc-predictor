# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/event_parse.py
# Purpose: Turn a free-text `results_tf.event_short` into (distance_metres, gender).
#
# ============================================================================
# WHY THIS FILE EXISTS
# ============================================================================
# `EVENT_DISTANCES_TF` in normalize_distance.py is an exact-match dict of 12
# keys. results_tf contains 64,079 distinct (event_short, source) groups. It is
# free text, written by two scrapers with two conventions:
#
#     anet :  '800m'   '3200m'  '1mile'   '2miles'  '3ksteeple'  '10-km'  '880y'
#     tfrrs:  "Men's 800 Meters"   "Women's 3000 Steeplechase"   "Men's Mile"
#
# So every tfrrs distance race resolved to None and was skipped as `no_distance`
# -- silently, with no error, hiding inside a bucket legitimately full of shot
# puts and 100m dashes. Measured loss: 7,157,445 timed rows.
#
# A 64,000-entry lookup table is not the fix. A PARSER is: distance races name
# their distance, and the job is to read it out of the noise and REJECT
# everything that is not an individual distance race.
#
# ============================================================================
# THE METHOD THAT FOUND THE BUGS  (read this before changing anything)
# ============================================================================
# Six bugs were found during development. FIVE of them were caught not by
# reading input strings, but by grouping the parser's OUTPUT by distance and
# asking "is that a distance anyone races?"
#
#     'sprintmed2248'   -> 2248m   a medley relay's LEG SUM        (496k rows)
#     'midmed8,4,4,8'   -> 8448m   another medley encoding         (3.7k rows)
#     '4xmile'          -> 6437m   a relay; \d x \d missed the 'm' (3.0k rows)
#     '2007th Boys'     -> 2007m   an ORDINAL, not a distance
#     'Boys 5-8 1 Mile' -> 8047m   '5-8' is a GRADE RANGE, read as 5 miles
#     '880y'            ->  880m   YARDS. The race is 804.67m.     (12k rows)
#
# You cannot enumerate 64,000 ways to name an event. You CAN enumerate the ~20
# distances a race is run at. When the input space is unbounded and the output
# space is small, AUDIT THE OUTPUT. That is what scripts/fix_tf_events.py does,
# and any change to this file should be re-scored through it before shipping.
#
# The `880y` episode is the cautionary one. The first fix (convert all yards)
# repaired 12,413 rows and BROKE 10,146 -- because "Men's 1600 Yards" is not a
# yard race, it is a 1600m race with the wrong unit typed after it. Converting
# it shrank a real 1600m to 1463m. No exception, no warning, a plausible number.
# Only a `1463` bucket in the distance audit gave it away. See _YARD_DISTANCES.
#
# ============================================================================
# CONTRACT
# ============================================================================
#   distanceFromEventShort(event_short) -> (metres | None, gender | None)
#
#   metres : the race distance, or None if this is not an individual distance
#            race we price (sprint, hurdle, relay, field event, race walk),
#            or if the distance falls outside [800, 12000].
#   gender : 'M' | 'F' | None, read from the tfrrs name prefix. This is a FREE
#            BONUS: tfrrs TF rows carry no reliable gender, and 20,027,808 rows
#            state it in the event name. Returned even when metres is None --
#            a shot put still tells you the athlete's gender.
#
# Exact keys in EVENT_DISTANCES_TF are tried FIRST, so the 12 events the fitters
# already price return byte-identical values and nothing downstream shifts.

import re

from normalize_distance import EVENT_DISTANCES_TF   # one-way import; no cycle


# ================================================================== #
# CONSTANTS  —  the domain, stated once
# ================================================================== #

# Below 800m the event is anaerobic and outside the distance model's domain.
# Above 12,000m is corruption (the *1609.344 unit-conversion monsters). Both
# mirror fit_distance_exponent.py's MIN_DISTANCE / MAX_DISTANCE. Keep in sync.
# ★ 600, NOT 800 (issue 42, owner 2026-09-02: "we should try to rate 600m").
#   The distance potential is FITTED on 800 m and up (fit_distance_exponent
#   keeps its own floor) and evaluated at 600 by its last local exponent,
#   one short step of extrapolation. Below 600 the sprints stay unrated.
_MIN_DISTANCE = 600.0
_MAX_DISTANCE = 12_000.0

_MILE = 1609.344
_YARD = 0.9144

# The imperial track schedule was FIXED. These are the only yard distances a
# race was ever actually run at. Anything else labelled "Yards" -- 1500, 1600,
# 2400, 3000, 3200, 5000 -- is a ROUND METRIC NUMBER with the wrong unit typed
# after it, and the NUMBER is the truth while the unit is the typo.
# (100/220/440 are below the 800m floor and never survive the range gate.)
_YARD_DISTANCES = frozenset({440, 600, 660, 880, 1000, 1320, 1760})

# Words meaning "not an individual distance race whose time we can put on a
# distance scale". Tested BEFORE any number is read, because "110 Meter Hurdles"
# and "4x400 Meter Relay" both contain a plausible number AND a metre unit.
# That ordering is the whole correctness argument of this module.
#
#   sprintmed / distmed / midmed / med\d : anet encodes medley relays as a GLUED
#       leg-sum with the word 'relay' nowhere in sight -- 'sprintmed2248',
#       'distmed12,4,8,16', 'midmed8,4,4,8'. The number is the sum of the legs.
#   \brw\b        : '3218m-rw' is a race walk.
#   walk          : a walker's 5000m on a runner's curve is nonsense.
#   \d+(st|nd|rd|th) : '2007th Boys' parsed as 2007 metres and hid inside the
#       2000m bucket, because 2007 is within the audit's +/-12m tolerance.
_REJECT_WORDS = re.compile(
    r"hurdle|relay|medley|\bdmr\b|\bsmr\b|\bwalk\b|\bracewalk\b|"
    r"sprintmed|distmed|midmed|med\d|swedish|shuttle|\brw\b|"
    r"steeple|"                       # <-- ADD THIS LINE
    r"\d+(?:st|nd|rd|th)\b|"
    r"\bshot\b|\bdiscus\b|\bjavelin\b|\bhammer\b|\bweight\b|\bvault\b|"
    r"\bjump\b|\bthrow\b|\bput\b|\bpentathlon\b|\bheptathlon\b|\bdecathlon\b|"
    r"\bhept\b|\bpent\b|\bdec\b|\blj\b|\btj\b|\bhj\b|\bpv\b|\bsp\b"
)
# Relay shorthand: 4x400, 4 x 400, 4X800m, and -- the one a digit-only rule
# missed -- '4xmile'. `\w` after the x, not `\d`.
_RELAY_SHORTHAND = re.compile(r"\d\s*[x\u00d7]\s*\w")

# Hurdle shorthand anet uses: 100mh, 110mh, 300mh, 400mh, 55h, 60h.
_HURDLE_SHORTHAND = re.compile(r"\d+\s*m?h\b")

# The gender prefix tfrrs puts on every event name.
_GENDER_PREFIX = re.compile(
    r"^\s*(men|mens|men's|boys|boy's|male|"
    r"women|womens|women's|girls|girl's|female)\b[\s'\u2019]*", re.IGNORECASE)

_MALE_WORDS = frozenset({"men", "mens", "men's", "boys", "boy's", "male"})

# Trailing noise: parentheticals and section/heat/round/level markers.
_TRAILING_NOISE = re.compile(
    r"\s*[\(\[].*?[\)\]]|"                               # (Section 1), [Finals]
    r"\s*\b(section|sect|heat|flight|div|division|"
    r"round|prelim|prelims|semi|semis|final|finals|"
    r"trial|trials|invite|invitational|open|championship|"
    r"varsity|jv|frosh|freshman|sophomore|novice|"
    r"unseeded|seeded|fast|slow|small|large|\#)\b.*$",
    re.IGNORECASE)

# A grade range: 'Boys 5-8 1 Mile Run' means grades 5 through 8. Both sides must
# be 1-2 digits, so this can never eat '10-km' (digit-dash-LETTER).
_GRADE_RANGE = re.compile(r"\b\d{1,2}\s*-\s*\d{1,2}\b")

# Unit words anet GLUES to the number with no separator: '1mile', '2miles',
# '3ksteeple', '3200m'. Re-inserting a space turns them into ordinary tokens so
# the unit regexes below (which rely on \b word boundaries) can see them.
# '3ksteeple' is the motivating case: `\d\s*k\b` cannot match `3k` when
# `steeple` is fused to it, so it parsed as 3 metres and was dropped.
_GLUED_SUFFIX = re.compile(
    r"(?<=[a-z0-9])(steeple\w*|meters?|metres?|miles?|run|dash|relay|hurdles?|walk)")


# ================================================================== #
# HELPERS  —  one job each
# ================================================================== #

# _stripGender
# Purpose : remove a leading "Men's "/"Women's " and report which it was.
# Output  : (remainder, 'M' | 'F' | None)
# Syntax  : re.match anchors at the string start (re.search would not). `m.end()`
#           is the index just past the WHOLE match, so `s[m.end():]` drops the
#           prefix along with its trailing apostrophe and space.
def _stripGender(s):
    m = _GENDER_PREFIX.match(s)
    if not m:
        return s, None
    return s[m.end():], ("M" if m.group(1).lower() in _MALE_WORDS else "F")


# _isNotARace
# Purpose : the reject gate. Must run BEFORE any number is read.
def _isNotARace(s):
    return bool(_REJECT_WORDS.search(s)
                or _RELAY_SHORTHAND.search(s)
                or _HURDLE_SHORTHAND.search(s))


# _deglue
# Purpose : separate a number from a unit word fused to it ('1mile' -> '1 mile').
# Syntax  : `(?<=[a-z0-9])` is a LOOKBEHIND: it asserts the preceding character
#           is a letter or digit WITHOUT consuming it, so the substitution only
#           inserts a space and never eats the character before the match.
def _deglue(s):
    return _GLUED_SUFFIX.sub(r" \1", s)


# _readNumber
# Purpose : the first number in the string, comma-stripped, as a float.
# Syntax  : `(\d[\d,]*(?:\.\d+)?)` -- a leading digit, then digits and commas
#           ("10,000"), then an optional decimal tail. Commas are stripped before
#           float(), which does not accept them.
# Returns None when there is no number ("Mile", "Steeplechase").
def _readNumber(s):
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", s)
    return float(m.group(1).replace(",", "")) if m else None


# _toMeters
# Purpose : combine the number and the unit words into metres.
# ORDER IS LOAD-BEARING, most specific unit first:
#     mile / mi   -> n * 1609.344   ("1 Mile", "2 mile", "1mile")
#     yard / y    -> n * 0.9144, BUT ONLY for a genuine yard distance
#     k / km      -> n * 1000       ("5k", "10 km", "10-km")
#     bare / m    -> n              ("800 Meters", "3200m", "3200")
# A steeplechase names its distance normally ("3000 Meter Steeplechase"), so it
# needs no special case here -- only the reject gate must let it through.
def _toMeters(s, n):
    if re.search(r"\bmiles?\b|\bmi\b|mile", s):
        return n * _MILE
    if re.search(r"\byards?\b|\byds?\b|\d\s*y\b", s):
        # Trust the unit only where a yard race existed. See _YARD_DISTANCES.
        return n * _YARD if int(n) in _YARD_DISTANCES else n
    #  \dkm\b is the GLUED form: '10km' has no boundary either side of the
    #  unit ('k' is followed by 'm', 'km' is preceded by a digit), so both
    #  older alternatives missed it and the row fell through to bare metres
    #  -- 10m, under MIN_DISTANCE, dropped as no_distance. '10-km' always
    #  worked; the dash was doing the regex's job.
    if re.search(r"\d\s*k\b|\dkm\b|\bkm\b|kilomet", s):
        return n * 1000.0
    return n                                        # metres, stated or implied


# ================================================================== #
# THE ENTRY POINT
# ================================================================== #

# distanceFromEventShort
# Purpose : event_short -> (metres | None, gender | None).
# Arguments: ev -- the raw results_tf.event_short value. May be None.
# Output   : see CONTRACT at the top of this file.
#
# The pipeline, in order. Every step earned its place by breaking without it:
#   0. exact dict     -- the 12 keys the fitters already price, byte-identical
#   1. lowercase + collapse whitespace
#   2. strip the gender prefix          (and KEEP it -- 20M rows' worth)
#   3. strip trailing section/heat noise
#   4. strip grade ranges               ('boys 5-8 1 mile' -> 'boys 1 mile')
#   5. reject non-races                 BEFORE reading any number
#   6. deglue, then reject AGAIN        (degluing can EXPOSE 'relay'/'hurdles')
#   7. read the number; a bare "Mile" has none and means 1609.344
#   8. apply the unit
#   9. gate on [MIN_DISTANCE, MAX_DISTANCE]
def distanceFromEventShort(ev):
    if not ev:
        return None, None

    # 0) Exact-dict fast path. Keeps the 12 known events bit-for-bit identical
    #    to what fit_distance_exponent.py and fit_era_corrections.py already see,
    #    so this module cannot perturb a fitted curve. None of the 12 keys carry
    #    a gender, hence None.
    exact = EVENT_DISTANCES_TF.get(ev)
    if exact is not None:
        return exact, None

    s = " ".join(ev.lower().split())                # collapse runs of whitespace
    s, gender = _stripGender(s)
    s = _TRAILING_NOISE.sub("", s).strip()
    s = _GRADE_RANGE.sub(" ", s).strip()

    # Reject BEFORE degluing: the reject words we care about ('sprintmed2248')
    # are themselves glued, and degluing must not be allowed to hide them.
    if _isNotARace(s):
        return None, gender
    s = _deglue(s)
    if _isNotARace(s):                              # degluing can EXPOSE a reject
        return None, gender

    n = _readNumber(s)
    if n is None:
        # A bare "Mile" names its distance without a number. tfrrs writes
        # "Men's Mile" and "Women's Mile" -- 558,020 timed rows that a
        # number-first parser silently discards.
        if re.search(r"\bmiles?\b", s):
            return _MILE, gender
        return None, gender

    meters = _toMeters(s, n)
    if not (_MIN_DISTANCE <= meters <= _MAX_DISTANCE):
        return None, gender                         # sprints below, corruption above
    return meters, gender


# genderFromEventShort
# Purpose : the gender alone, for callers that already have a distance.
# Why separate: _resolveDistanceGender short-circuits on _DISTANCE_OVERRIDES and
#   _RESULT_OVERRIDE, returning gender=None even when the event name states it.
#   Exactly the failure _blobGender was written to fix on the XC side.
# Output  : 'M' | 'F' | None. Works on field events too -- "Men's Shot Put" has
#   no distance but still names a gender.
def genderFromEventShort(ev):
    if not ev:
        return None
    _s, gender = _stripGender(" ".join(ev.lower().split()))
    return gender