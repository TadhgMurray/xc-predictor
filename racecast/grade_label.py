"""grade_label.py -- one spelling for a grade on the page (owner,
2026-09-06: "consistently displayed, not in the DB"): a school pool reads
the number, 5 to 12 (a class word in a high-school pool is its number:
Fr 9, So 10, Jr 11, Sr 12); a college pool reads the tfrrs eligibility
spelling, FR-1 .. SR-4 (a bare word or a 13-16 number becomes it). The
database keeps whatever the feed said.
"""
import re

_WORD_HS = {"fr": "9", "so": "10", "jr": "11", "sr": "12",
            "freshman": "9", "sophomore": "10", "junior": "11", "senior": "12"}
_WORD_COLLEGE = {"fr": "FR-1", "so": "SO-2", "jr": "JR-3", "sr": "SR-4",
                 "freshman": "FR-1", "sophomore": "SO-2", "junior": "JR-3",
                 "senior": "SR-4"}
_NUM_COLLEGE = {"13": "FR-1", "14": "SO-2", "15": "JR-3", "16": "SR-4"}
_ELIG = re.compile(r"^(FR|SO|JR|SR)-?([1-6])$", re.I)


def gradeLabel(grade, pool=None):
    """'Sr' in hs_m -> '12'; '11' -> '11'; 'Fr' in college_f -> 'FR-1';
    'sr-4' -> 'SR-4'; '14' in a college pool -> 'SO-2'. Unknown spellings
    come back as they are; None stays None."""
    if grade is None:
        return None
    g = str(grade).strip()
    if not g:
        return g
    level = (pool or "").split("|")[0].split("_")[0].lower()
    college = level in ("college", "pro")
    m = _ELIG.match(g)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}" if college or level == "" \
            else _WORD_HS.get(m.group(1).lower(), g)
    low = g.lower().rstrip(".")
    if college:
        if low in _WORD_COLLEGE:
            return _WORD_COLLEGE[low]
        if g.isdigit():
            return _NUM_COLLEGE.get(g, g)
        return g
    if low in _WORD_HS:
        return _WORD_HS[low]
    if g.isdigit():
        return str(int(g))
    return g
