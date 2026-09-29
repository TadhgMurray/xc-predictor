"""
champ_course.py -- a championship that runs its OWN course at a shared venue
gets its own course key, by meet name.

★ WHY (owner, 2026-09-28: "Foot Locker Nationals (Morley Field) reads far
  too easy: +4.7%"). Trey Caldwell rated 131.5 / 132.1 / 131.4 at the
  national final (2022-2024) against 136-140 in every other race that
  month, a week after 137.6-139.9 at the West regional. The XC course key is
  the venue's canonical id (course_canonical: name + coordinates) and the
  distance, and Morley Field Sports Complex, San Diego, 5000 m, is ALSO the
  key of the local San Diego meets run there on other loops. So the final's
  one race day a year is outvoted in its own cell by a dozen ordinary days,
  and its difficulty is the park's, not the course's. course_canonical cannot
  split them -- it has no meet name, only a place -- and the place is the
  same. The meet name is the one thing that tells them apart.

  The open item since 2026-09-13 (docs/HANDOFF-2026-09-13.md §5 item 5,
  §8.2, §8.11: "Merging the ids (scripts/meet_cells.py --meet "Foot
  Locker") is still the fix") was the OTHER half of the same problem: the
  final keyed in pieces under several canonical ids, one to three race days
  each. A key from the meet name fixes both at once: every year of the final,
  under any spelling of the park and any sponsor, is one history.

★ WHAT. CHAMP_COURSES below: an ordered table of (slug, POSIX regex, display
  name). The first pattern a meet name matches gives the row the course key
  'XC:champ:<slug>:d<distance>' in place of 'XC:<canonical_id>:d<distance>'
  (speed_ratings_db._xcQuery). The distance suffix, the era split, every
  rule that drops a row's venue (a corrected distance, a placeholder venue)
  are exactly as before; only the venue part of the key changes.

  One history across SPONSORS: Foot Locker, Champs Sports and Eastbay are
  the same championship under the names it has carried, so each region's
  pattern takes all three.

⚠ THE KEY IS THE CHAMPIONSHIP, NOT THE PARK. That is the point (the final's
  years under three canonical ids become one cell), and it is also the
  assumption: the national final has been at Balboa Park / Morley Field and
  each regional at its own park throughout the corpus. If one of them ever
  moves, give the new site its own slug (a pattern with the year in it, above
  the old one) -- one key for two courses is the mistake this file exists to
  undo.

⚠ A CHAMP KEY CARRIES NO COORDINATES (speed_ratings.loadCourseCoords reads
  only a numeric id), so the place prior does not pull the final back toward
  the park's local loops -- deliberately. It publishes with canonical_id
  NULL under its display name (speed_ratings_db._splitVenueKey).

! THE NAME MUST SAY WHICH RACE. A pattern needs the sponsor AND a
  championship word (champ, national, final, regional): "East Bay Athletic
  League" is not Eastbay (the space, and no sponsor word), and a "Foot Locker
  Regional" that names no region matches no row rather than the final.
  Regions go before the final, and "Midwest" before "West".

The SQL and the Python are the same rule in the same order;
tests/test_champ_course.py holds them together. No `%` and no braces in the
regexes: the pack query is an f-string and psycopg2 scans for `%`.
"""
import re

SPONSOR = "(foot ?locker|champs ?sports|eastbay)"
CHAMP_WORD = "(champ|national|regional|final)"

# (slug, the region's regex -- tested on top of SPONSOR and CHAMP_WORD --
#  and the name a course page shows). Order matters: first match wins.
CHAMP_COURSES = (
    ("footlocker-northeast", "north ?-?east", "Foot Locker Northeast Regional"),
    ("footlocker-south", "(^|[^a-z])south([^a-z]|$)", "Foot Locker South Regional"),
    ("footlocker-midwest", "mid ?-?west", "Foot Locker Midwest Regional"),
    ("footlocker-west", "(^|[^a-z])west([^a-z]|$)", "Foot Locker West Regional"),
    # ! the final LAST, and never a name that says regional: a regional
    #   whose name lost its region is left at its venue, not folded in here
    ("footlocker-final", None, "Foot Locker Nationals"),
)
NOT_FINAL = "regional"

_sponsor = re.compile(SPONSOR, re.I)
_champ = re.compile(CHAMP_WORD, re.I)
_not_final = re.compile(NOT_FINAL, re.I)
_region = [(slug, re.compile(rx, re.I) if rx else None, name)
           for slug, rx, name in CHAMP_COURSES]
DISPLAY = {slug: name for slug, _rx, name in CHAMP_COURSES}
PREFIX = "champ:"


def courseKey(meet_name):
    """'champ:<slug>' for a championship with its own course, else None.
    The Python twin of sql()."""
    s = meet_name or ""
    if not (_sponsor.search(s) and _champ.search(s)):
        return None
    for slug, rx, _name in _region:
        if rx is None:
            return None if _not_final.search(s) else PREFIX + slug
        if rx.search(s):
            return PREFIX + slug
    return None


def sql(name_expr):
    """A SELECT expression: 'champ:<slug>' or NULL for the meet-name
    expression, in courseKey()'s order."""
    whens = []
    for slug, rx, _name in CHAMP_COURSES:
        if rx is None:
            whens.append(f"WHEN {name_expr} !~* '{NOT_FINAL}' THEN '{PREFIX}{slug}'")
        else:
            whens.append(f"WHEN {name_expr} ~* '{rx}' THEN '{PREFIX}{slug}'")
    return (f"CASE WHEN {name_expr} ~* '{SPONSOR}' AND {name_expr} ~* '{CHAMP_WORD}' "
            f"THEN CASE {' '.join(whens)} END END")


def displayName(venue_part):
    """'champ:footlocker-final' -> 'Foot Locker Nationals'; None when the
    venue part of a key is not a champ key."""
    v = str(venue_part or "")
    if not v.startswith(PREFIX):
        return None
    slug = v[len(PREFIX):]
    return DISPLAY.get(slug, slug)


def displaySql(name_expr):
    """A SELECT expression: the published course name's championship part
    ('Foot Locker Nationals', ...) for the meet-name expression, or NULL --
    the site's key into course_difficulties ('XC:' || this), in courseKey()'s
    order. For the pages, which find a row's difficulty by the meet, not by
    the engine's key (2026-09-29)."""
    whens = []
    for slug, rx, name in CHAMP_COURSES:
        if rx is None:
            whens.append(f"WHEN {name_expr} !~* '{NOT_FINAL}' THEN '{name}'")
        else:
            whens.append(f"WHEN {name_expr} ~* '{rx}' THEN '{name}'")
    return (f"CASE WHEN {name_expr} ~* '{SPONSOR}' AND {name_expr} ~* '{CHAMP_WORD}' "
            f"THEN CASE {' '.join(whens)} END END")
