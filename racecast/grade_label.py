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


def advanceGrade(grade, years, pool=None):
    """A stored grade `years` seasons on, spelled for the page (owner,
    2026-09-25: a runner who has not raced yet this season showed a blank
    grade; "it should just increment grade by whatever from their last
    year"). '10' +1 -> '11'; 'JR-3' +1 -> 'SR-4'; 'SR-4' +1 -> 'SR-5' (a
    fifth year); 'So' in a high-school pool +1 -> '11'. A school grade past
    12 has graduated and comes back None, as does anything unreadable."""
    if grade is None or years is None:
        return None
    label = gradeLabel(grade, pool)
    if not label:
        return None
    years = int(years)
    if years <= 0:
        return label
    if label.isdigit():
        n = int(label) + years
        return str(n) if n <= 12 else None
    m = _ELIG.match(label)
    if m:
        order = ("FR", "SO", "JR", "SR")
        cls = order.index(m.group(1).upper())
        n = int(m.group(2)) + years
        return f"{order[min(cls + years, 3)]}-{n}" if n <= 6 else None
    return None


def classGrade(grades, pool=None):
    """The class an athlete is in for ONE academic year, from the stored
    grades of that year's seasons (its cross country season and its track
    season), as the stored value the page then spells with gradeLabel.

    ★ A COLLEGE CLASS IS THE ACADEMIC YEAR'S HIGHEST ELIGIBILITY (owner,
      2026-09-07, Joey Sullivan: "he's actually a senior, but all his races
      say junior"). tfrrs counts eligibility PER SPORT, so a runner in his
      fourth cross country season and third track season is SR-4 in the
      fall and JR-3 in the spring of the same year; the class he is in is
      the higher of the two. The athlete header and the school roster both
      ask this function, so the two pages cannot name different classes
      for one season (outside review, 2026-09-29: Harrison Dow SR-4 on the
      Amherst page, JR-3 on his own).

    ! A SCHOOL POOL TAKES THE FIRST GRADE AS IT IS. A high-school grade is
      the same number in both sports, and the eligibility regex below would
      read "12" as a second-year. Unreadable spellings are skipped, and a
      year with no readable eligibility at all keeps its first grade rather
      than going blank.
    """
    grades = [g for g in (grades or []) if g is not None and str(g).strip()]
    if not grades:
        return None
    level = (pool or "").split("|")[0].split("_")[0].lower()
    if level not in ("college", "pro"):
        return grades[0]
    best = None
    for g in grades:
        m = _ELIG.match(gradeLabel(g, pool) or "")
        if m and (best is None or int(m.group(2)) > best[0]):
            best = (int(m.group(2)), g)
    return best[1] if best else grades[0]
