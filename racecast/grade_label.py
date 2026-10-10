"""grade_label.py -- one spelling for a grade on the page (owner,
2026-09-06: "consistently displayed, not in the DB"): a school pool reads
the number, 5 to 12 (a class word in a high-school pool is its number:
Fr 9, So 10, Jr 11, Sr 12); a college pool reads the tfrrs eligibility
spelling, FR-1 .. SR-4 (a bare word or a 13-16 number becomes it). The
database keeps whatever the feed said.
"""
import re

# ★ EVERY SPELLING THE FEEDS WRITE (owner, 2026-09-29: "it'll say senior for
#   somebody rated in hs instead of 12"). The class words come in full, short
#   and plural forms, and a school grade as an ordinal ("12th"). One table,
#   mirrored by rankings.js's gradeLabel -- tests/test_grade_label.py holds
#   the two to the same answers.
_CLASS = {"fr": 0, "fresh": 0, "freshman": 0, "freshmen": 0,
          "so": 1, "soph": 1, "sophomore": 1, "sophomores": 1,
          "jr": 2, "junior": 2, "juniors": 2,
          "sr": 3, "senior": 3, "seniors": 3}
_WORD_HS = {w: str(9 + i) for w, i in _CLASS.items()}
_WORD_COLLEGE = {w: ("FR-1", "SO-2", "JR-3", "SR-4")[i] for w, i in _CLASS.items()}
_ORDINAL = re.compile(r"^0*(\d{1,2})(st|nd|rd|th)?\.?$", re.I)
# ★ A SCHOOL GRADE IN A COLLEGE POOL IS A COLLEGE YEAR (owner, 2026-10-07:
#   "if someone puts 9th grade, and we know they're a college freshman,
#   we'll print college freshman"). The pool is the engine's verdict on the
#   level; a feed that typed 9-12 for a college athlete meant the year.
_NUM_COLLEGE = {"13": "FR-1", "14": "SO-2", "15": "JR-3", "16": "SR-4",
                "9": "FR-1", "10": "SO-2", "11": "JR-3", "12": "SR-4"}
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
    # ★ A GRADUATION YEAR IS NOT A GRADE (owner, 2026-10-09: Dynasty
    #   Gammage's 2007-08 seasons read "Grade 2011"): some feeds write the
    #   class year in the grade column
    if re.fullmatch(r"(19|20)\d\d", g):
        return f"Class of {g}"
    low = g.lower().rstrip(".").strip()
    om = _ORDINAL.match(g)
    num = str(int(om.group(1))) if om else None
    if college:
        if low in _WORD_COLLEGE:
            return _WORD_COLLEGE[low]
        if num is not None:
            return _NUM_COLLEGE.get(num, num)
        return g
    if low in _WORD_HS:
        return _WORD_HS[low]
    if num is not None:
        return num
    return g


# ★ A GRADUATION YEAR, READ FOR ONE SEASON (sweep 2026-10-10, A12). The
#   class of G are seniors in the academic year that ENDS in G, so in the
#   season that opens in calendar year `season` (season_year.academicYear,
#   the stored athlete_season.year) they are in grade 12 - (G - (season+1))
#   = 13 + season - G. Mirrors rankings.gradeKeySql's class-year branch.
_CLASS_YEAR = re.compile(r"^(19|20)\d\d$")


def isClassYear(grade):
    return grade is not None and bool(_CLASS_YEAR.match(str(grade).strip()))


def classYearGrade(grade, season, pool=None):
    """A class-year grade ("2026") as the grade it is in `season` (an
    academic year's opening calendar year), spelled for the pool: '12' /
    'SR-4' in its senior season, '11' / 'JR-3' the year before. None when
    the season is past it (graduated -- the season opening in G is the first
    without them), when it is not a class year, or when no grade fits: a
    college class more than four years out (FR-1 is three years out) or a
    school grade below 1."""
    if not isClassYear(grade) or season is None:
        return None
    left = int(str(grade).strip()) - (int(season) + 1)   # years to graduation
    if left < 0:
        return None
    level = (pool or "").split("|")[0].split("_")[0].lower()
    if level in ("college", "pro"):
        return ("SR-4", "JR-3", "SO-2", "FR-1")[left] if left <= 3 else None
    n = 12 - left
    return str(n) if n >= 1 else None


def advanceGrade(grade, years, pool=None, season=None):
    """A stored grade `years` seasons on, spelled for the page (owner,
    2026-09-25: a runner who has not raced yet this season showed a blank
    grade; "it should just increment grade by whatever from their last
    year"). '10' +1 -> '11'; 'JR-3' +1 -> 'SR-4'; 'SR-4' +1 -> 'SR-5' (a
    fifth year); 'So' in a high-school pool +1 -> '11'. A school grade past
    12 has graduated and comes back None, as does anything unreadable.

    ★ A CLASS YEAR ("2026") DOES NOT ADVANCE, IT IS READ (sweep 2026-10-10,
      A12): the graduation year is the same every season, so `years` says
      nothing -- `season`, the academic year it is wanted for, says which
      grade it is (classYearGrade), and None once that season is past it.
      With no season it is unreadable here, as before."""
    if grade is None or years is None:
        return None
    if isClassYear(grade):
        return classYearGrade(grade, season, pool)
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
