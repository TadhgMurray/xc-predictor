"""
marks.py -- the field-event mark parser.

results_tf.mark is TEXT, and the handoff's census of it says the commonest
values are NH, ND, DNS, SCR and FOUL rather than measurements. The real ones
are mostly feet-inches ('4-06.00'), where 5-00.00 outranks 4-10.00 and sorts
BELOW it as text. A marks board therefore needs exactly what this module is:
a parser, a unit decision, and a sanity floor per event -- each one a place
to put a wrong number at the top of a national list, so each one is explicit
and testable here rather than inlined into a query.

★ THE CONTRACT: parseMark NEVER GUESSES. Anything it cannot read with
  confidence comes back kind=None and stays off every board. A missing mark
  is visible to its athlete; a misread one distorts everybody's rank -- the
  same undercount-over-distortion call the US scope filter makes.

WHO USES IT
    audit_marks.py    the census: run it BEFORE building the board, so the
                      unit mix and the unparsed tail are measured, not
                      assumed.
    (the marks board) once the census says coverage is good.

Run `python racecast/marks.py` for the self-check.
"""

import re


# ------------------------------------------------------------------ #
#  1. SENTINELS -- the "no mark" vocabulary
# ------------------------------------------------------------------ #
#
# ★ A SENTINEL IS AN ANSWER, NOT A PARSE FAILURE. NH (no height) and ND (no
#   distance) are the meet saying the athlete recorded nothing; telling that
#   apart from a spelling we cannot read is what lets the census say "94%
#   parsed, 5% legitimately empty, 1% unknown" instead of one mushy number.
#
# Compared upper-cased and stripped of trailing periods, so 'nh', 'NH.' and
# 'Foul' all land. 'X'/'XXX' are attempt strings (three misses), not marks.
_NO_MARK = {
    "NH", "ND", "NM",              # no height / no distance / no mark
    "DNS", "DNF", "SCR", "WD",     # never competed
    "FOUL", "F", "X", "XXX",       # fouled out
    "DQ", "FS",                    # disqualified, false start
    "PASS", "P",                   # passed every height
    "-", "--", "—", "",       # dashes and blanks
}


# ------------------------------------------------------------------ #
#  2. THE MARK GRAMMARS
# ------------------------------------------------------------------ #
#
# Feet-inches: '55-11.25', '4-06', '17-3.5'. The inches part MUST be under
# twelve -- '17-15' is not a mark, it is two numbers -- and that guard is in
# the code, not the regex, so it can be tested by name.
_FEET_INCHES = re.compile(r"^(\d{1,3})-(\d{1,2}(?:\.\d{1,2})?)$")

# Metric with the unit stated: '16.76m', '1.83M', '16.76 m'.
_METRIC = re.compile(r"^(\d{1,3}(?:\.\d{1,3})?)\s*m$", re.IGNORECASE)

# ⚠ A BARE NUMBER ('12.19') IS PARSED BUT TAGGED metric_bare. It is almost
#   certainly metres -- feet never appear without inches in this corpus's
#   format -- but "almost certainly" is exactly what the census exists to
#   check, so the tag keeps the two populations separable until it has.
_BARE = re.compile(r"^(\d{1,3}(?:\.\d{1,3})?)$")

# tfrrs sometimes stores both: '16.76m (55-0)'. The metric half is the
# measurement; the parenthetical is a courtesy conversion.
_COMBINED = re.compile(r"^(\d{1,3}(?:\.\d{1,3})?)\s*m\s*\(.*\)$", re.IGNORECASE)

_FOOT = 0.3048
_INCH = 0.0254


def parseMark(text):
    """One mark string -> (metres, kind).

        ('55-11.25')      -> (17.05..., 'feet_inches')
        ('16.76m')        -> (16.76,    'metric')
        ('16.76m (55-0)') -> (16.76,    'metric')
        ('12.19')         -> (12.19,    'metric_bare')   see _BARE
        ('NH')            -> (None,     'no_mark')
        ('4-15')          -> (None,     None)            15 inches is no mark
        (garbage)         -> (None,     None)

    kind=None is the parser refusing, and the caller must treat it as
    "unknown", never as zero -- a refusal that becomes a 0.00 sorts to the
    bottom of an ascending board and the TOP of a descending one.
    """
    if text is None:
        return None, "no_mark"
    s = str(text).strip()
    if s.rstrip(".").upper() in _NO_MARK:
        return None, "no_mark"

    m = _FEET_INCHES.match(s)
    if m:
        feet, inches = int(m.group(1)), float(m.group(2))
        # ! INCHES < 12, OR IT IS NOT FEET-INCHES. '17-15' parses as numbers
        #   and means nothing; letting it through would coin 17ft 15in.
        if inches >= 12:
            return None, None
        return feet * _FOOT + inches * _INCH, "feet_inches"

    m = _METRIC.match(s) or _COMBINED.match(s)
    if m:
        return float(m.group(1)), "metric"

    m = _BARE.match(s)
    if m:
        return float(m.group(1)), "metric_bare"

    return None, None


# ------------------------------------------------------------------ #
#  3. EVENT NORMALISATION
# ------------------------------------------------------------------ #
#
# The corpus spells one event several ways -- 'shot' and "Women's Shot Put"
# are the same event, and event_type_id does NOT unify them (checked). The
# keyword map below folds spellings onto nine canonical keys; the exact-code
# map catches the two-letter shorthands a keyword search would miss or
# mis-hit.
#
# ⚠ ORDER MATTERS in _KEYWORDS: 'triple jump' contains 'jump', so the
#   specific names are tested before the generic ones. Tuples, not a dict,
#   because iteration order IS the precedence.

_EVENT_CODES = {
    "SP": "shot_put",   "DT": "discus",      "JT": "javelin",
    "HT": "hammer",     "WT": "weight_throw",
    "HJ": "high_jump",  "PV": "pole_vault",
    "LJ": "long_jump",  "TJ": "triple_jump",
}

_KEYWORDS = (
    ("shot",    "shot_put"),
    ("discus",  "discus"),
    ("disc ",   "discus"),
    ("javelin", "javelin"),
    ("jav ",    "javelin"),
    ("hammer",  "hammer"),
    ("weight",  "weight_throw"),
    ("pole",    "pole_vault"),
    ("triple",  "triple_jump"),
    ("high jump", "high_jump"),
    ("highjump",  "high_jump"),
    ("long jump", "long_jump"),
    ("longjump",  "long_jump"),
)


def normalizeFieldEvent(name):
    """Event spelling -> canonical key, or None when it is not recognisably a
    field event. None is a refusal, same contract as parseMark: an event we
    cannot place must not land on some other event's board."""
    if not name:
        return None
    s = str(name).strip()
    code = _EVENT_CODES.get(s.upper())
    if code:
        return code
    # Padded with spaces so 'jav ' can match at the end of a string too.
    low = " " + s.lower() + " "
    for needle, key in _KEYWORDS:
        if needle in low:
            return key
    return None


# ------------------------------------------------------------------ #
#  4. SANITY RANGES
# ------------------------------------------------------------------ #
#
# Per-event bounds in metres, wide enough for a small middle schooler and a
# world record with daylight, tight enough that a mis-parsed unit cannot
# top a board: 55 feet of shot put read as 55 metres fails its ceiling.
# Lower bounds exist because a 0.05 m 'long jump' is a data-entry artefact
# that would otherwise anchor every ascending sort.
_SANE = {
    "high_jump":    (0.50, 2.60),     # WR 2.45
    "pole_vault":   (0.90, 6.50),     # WR 6.30
    "long_jump":    (1.00, 9.20),     # WR 8.95
    "triple_jump":  (3.00, 18.80),    # WR 18.29
    "shot_put":     (1.00, 24.50),    # WR 23.56
    "discus":       (2.00, 78.00),    # WR 74.35 (m WR 74.08 pre-2024, 74.35 Alekna)
    "javelin":      (2.00, 100.00),   # WR 98.48
    "hammer":       (2.00, 88.00),    # WR 86.74
    "weight_throw": (2.00, 26.50),    # world best 25.86
}


def saneMark(event_key, metres):
    """Is this parsed mark physically plausible for its event?

    An unknown event has no range, so nothing is sane for it -- the board
    only serves events it can bound. metres=None is never sane."""
    if metres is None:
        return False
    lo, hi = _SANE.get(event_key, (None, None))
    return lo is not None and lo <= metres <= hi


# ------------------------------------------------------------------ #
#  5. SELF-CHECK -- `python racecast/marks.py`
# ------------------------------------------------------------------ #

def _selfCheck():
    cases = [
        # feet-inches
        ("4-06.00",  1.3716, "feet_inches"),
        ("55-11.25", 17.0498, "feet_inches"),
        ("5-00",     1.524,  "feet_inches"),
        ("17-3.5",   5.2705, "feet_inches"),
        # metric, stated and bare
        ("16.76m",       16.76, "metric"),
        ("1.83M",        1.83,  "metric"),
        ("16.76m (55-0)", 16.76, "metric"),
        ("12.19",        12.19, "metric_bare"),
        # sentinels
        ("NH", None, "no_mark"), ("nd", None, "no_mark"),
        ("FOUL", None, "no_mark"), ("SCR.", None, "no_mark"),
        ("--", None, "no_mark"), ("", None, "no_mark"), (None, None, "no_mark"),
        # refusals
        ("4-15", None, None),           # fifteen inches
        ("55-11.25 (x)", None, None),   # trailing junk on feet-inches
        ("4:06.00", None, None),        # a running time, not a mark
        ("abc", None, None),
        ("1e5", None, None),
    ]
    bad = 0
    for text, want_m, want_kind in cases:
        got_m, got_kind = parseMark(text)
        ok = (got_kind == want_kind and
              (want_m is None if got_m is None
               else got_m is not None and abs(got_m - want_m) < 0.001))
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} parseMark({text!r}) -> "
              f"({got_m}, {got_kind!r})")

    events = [
        ("shot", "shot_put"), ("Women's Shot Put", "shot_put"),
        ("SP", "shot_put"), ("Boys Discus Throw", "discus"),
        ("HJ", "high_jump"), ("High Jump", "high_jump"),
        ("Triple Jump", "triple_jump"), ("Long Jump", "long_jump"),
        ("longjump", "long_jump"), ("Pole Vault", "pole_vault"),
        ("Javelin Throw - 800g", "javelin"), ("Weight Throw", "weight_throw"),
        ("Hammer Throw", "hammer"),
        ("100 Meters", None), ("4x400 Relay", None), ("", None), (None, None),
        # 'jumping jehosaphat invitational' must not become a jump event
        ("jump ball", None),
    ]
    for name, want in events:
        got = normalizeFieldEvent(name)
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} normalizeFieldEvent({name!r}) -> {got!r}")

    sanity = [
        ("shot_put", 17.05, True),
        ("shot_put", 55.0, False),      # feet read as metres fails the ceiling
        ("high_jump", 1.37, True),
        ("high_jump", 0.05, False),     # data-entry artefact fails the floor
        ("long_jump", None, False),
        (None, 5.0, False),             # unknown event bounds nothing
    ]
    for key, metres, want in sanity:
        got = saneMark(key, metres)
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} saneMark({key!r}, {metres}) -> {got}")

    print("\nall cases pass" if not bad else f"\n{bad} FAILURES")
    return bad


if __name__ == "__main__":
    raise SystemExit(_selfCheck())
