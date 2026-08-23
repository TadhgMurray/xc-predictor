"""
engine/season_year.py -- the ONE definition of which season a race belongs to.

★ ONE CLOCK. THE ACADEMIC YEAR. EVERYTHING.
--------------------------------------------------------------------------
A season is the school year: August through July, named for the calendar year
it opens in. Autumn 2025 XC, winter 2025-26 indoor and spring 2026 outdoor are
all season 2025.

Every consumer uses this and only this -- the packer's athlete grouping key,
grade_sanity's verdict key, athlete_season_level, panels, and
build_ranking_results. There is no second clock to disagree with.

WHY THIS REPLACED THE SPORT-SPECIFIC ROLLOVER RULES
---------------------------------------------------
This module used to hold two rules pointing in opposite directions: TF rolled
FORWARD from October (early indoor opens the season ahead) and XC rolled BACK
from February (a January race closes the autumn campaign). Both were right
about their sport, and together they were a third clock -- neither the calendar
year the pro tables use, nor the academic year grade_sanity reasons on.

⚠ THE COST WAS MEASURED, NOT THEORISED.

  Gideon Bosh ran 3000m races at middle-school meets in Feb-April 2025 as a
  grade 8, then 5000m high-school races in Sept-Nov 2025 as a grade 9. The XC
  rule rolled back only Jan-Feb, so March 12 and November 14 both landed in
  season 2025 -- one athlete-season spanning a middle schooler and a high
  schooler. His 9:38 3000 was displayed under the high-school season and rated
  on the high-school scale: 147.0.

  Person 23950279, corroborated grade 6 on every row, came out of the solve as
  TWO 2024 athlete-seasons -- hs_m at 174.1 and ms_m at 124.8, near-identical
  ability, 49 rating points apart, because ms anchors at 3200 and hs at 5000
  and each half was measured against the other's pool_mean.

★ AND THE ACADEMIC YEAR SUBSUMES BOTH OLD RULES. Checked against every case
  this module's self-check already carried:

    TF Dec 27 and TF Jan 3      -> both 2025. Paterna's split season, fixed,
                                   which is what the TF rollover was FOR.
    TF Oct opener, TF Jun close -> both 2025. One indoor-plus-outdoor season.
    XC Dec 6 (NXN)              -> 2025, with its own autumn. The December
                                   exception the old rule guarded so carefully
                                   needs no guard: August already passed.
    XC Jan 17, XC Feb 7         -> 2025. A January race closes the autumn
                                   campaign, which is what the XC rollback
                                   was FOR.

  Two sport-specific rules, four constants and a `sport ==` guard, replaced by
  one boundary that no longer has a sport to be wrong about.

! THE BOUNDARY IS AUGUST, NOT JULY, AND THE DIFFERENCE IS ONE MONTH THAT
  MATTERS. July holds the outdoor championships and the USATF Junior Olympic
  finals -- the CLIMAX of a season that began the previous autumn, not the
  opening of anything. A July boundary files those with the season ahead and
  cuts every summer club campaign in half. August is the only month in the
  year when essentially nothing is running, which is exactly what makes it the
  right place to put a seam.
"""

from datetime import date, datetime
from functools import lru_cache


# First month of a new season. 8 = August. Everything in this module is
# derived from this one constant, including the SQL form, so moving the seam
# is a single edit and no consumer can be left behind.
ACADEMIC_START_MONTH = 8


# ------------------------------------------------------------------ #
#  1. THE RULE                                                        #
# ------------------------------------------------------------------ #

def seasonShift(sport, month):
    """0 or -1: how many years to move a race in this month.

    ⚠ `sport` IS ACCEPTED AND IGNORED, DELIBERATELY. Every caller in the
      codebase passes it and the parameter is kept so none of them has to
      change -- but a season boundary that differs by sport is precisely what
      produced the two-clock bug. If you find yourself wanting to branch on
      sport here, that is the thing this rewrite removed. Don't.
    """
    return 0 if month >= ACADEMIC_START_MONTH else -1


def rollsOver(sport, month):
    """Kept for callers that only asked "does this move forward?".

    Nothing moves forward any more -- the season is named for the year it
    OPENS in, so a race either sits in its own calendar year or belongs to the
    one before. Always False.
    """
    return False


def seasonYearFor(sport, d, ledger=None):
    """Season year for a race on date `d`. For callers holding a date object.

    `d` must be a real date or datetime, not a string. packResults already
    parses via `_asDate`, and accepting a string here would let a caller pass
    an unparsed row and get an AttributeError deep in the pack rather than a
    clear failure at the boundary.

    ★ THIS IS NOW SAFE FOR poolOf's `season=` TOO -- with one exception. The
      old warning here said never to feed this to `season=`, because that
      argument keyed lookups into tables built on the calendar year. grade_fix
      and athlete_season_level have since moved onto this clock, so they want
      exactly this value. pro_athlete_season has NOT; see section 4.
    """
    assert isinstance(d, (date, datetime)), \
        f"seasonYearFor needs a date, got {type(d)}"
    return _resolve(sport, d.year, d.month, ledger)


# ! ONE ANSWER PER (sport, date), AND build_ranking_results ASKS IT
#   61.6M TIMES ACROSS ABOUT 13,000 DISTINCT DATES. Pure: a date string
#   in, a season year out.
@lru_cache(maxsize=1 << 16)
def seasonYearFromIso(sport, date_text, ledger=None):
    """Season year from a 'YYYY-MM-DD' string. For callers holding raw text.

    Sliced rather than parsed. build_ranking_results streams 61.6M rows and its
    SQL already guarantees the format with a regex, so constructing a date
    object per row would be pure overhead for a value we only need two integers
    from.
    """
    return _resolve(sport, int(date_text[:4]), int(date_text[5:7]), ledger)


def academicYear(d):
    """Season year for a date, with no sport argument. The plain form.

    Provided so callers that never had a sport to pass -- grade_sanity,
    season_level -- stop hand-rolling `month >= 7` and share this definition
    instead. Two copies of a boundary do not stay two copies of the same
    boundary.
    """
    return seasonYearFor(None, d)


def _resolve(sport, year, month, ledger):
    """Shared tail of every wrapper: apply the rule, record it, return."""
    shift = seasonShift(sport, month)
    if ledger is not None:
        ledger.record(sport, month, shift)
    return year + shift


# ------------------------------------------------------------------ #
#  2. THE LEDGER                                                      #
# ------------------------------------------------------------------ #

class RolloverLedger:
    """Counts what the rule moved, so the pack can print a watchable line.

    A change that only moves rows between season buckets is invisible in the
    rating output, so without a count the only way to know it fired is to go
    looking in the database afterwards.

    ⚠ A ZERO HERE NO LONGER MEANS THE GUARD FAILED. Under the old sport rules
      zero meant the `sport ==` test never matched. Under one academic
      boundary, zero means the pack held no races between January and July,
      which for a real corpus means something is wrong with the DATE FILTER
      rather than with this module.
    """

    def __init__(self):
        self.total = 0
        self.moved = 0
        self.by_month = {}

        # Kept under the old name: speed_ratings prints _rollover.moved and
        # the census reads tf_total. Renaming them here would break a caller
        # for no gain.
        self.tf_total = 0

    def record(self, sport, month, shift):
        """Called once per row by _resolve. Kept cheap: two int bumps."""
        self.total += 1
        self.tf_total += 1
        if shift:
            self.moved += 1
            self.by_month[month] = self.by_month.get(month, 0) + 1

    def line(self):
        """One log line, shaped like the other [pack] lines."""
        if not self.total:
            return "[pack] season year: no rows seen"
        months = ", ".join(f"{_MONTH_NAMES.get(m, m)} {n:,}"
                           for m, n in sorted(self.by_month.items()))
        return (f"[pack] season year: {self.moved:,} of {self.total:,} races "
                f"belong to the previous academic year ({months})")


_MONTH_NAMES = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}


# ------------------------------------------------------------------ #
#  3. THE SAME RULE, AS SQL                                           #
# ------------------------------------------------------------------ #

def seasonYearSql(sport, date_col="date"):
    """The rule as a Postgres expression over a TEXT 'YYYY-MM-DD' column.

    ★ GENERATED FROM ACADEMIC_START_MONTH, NEVER RETYPED. panels.py groups the
      athlete board in SQL, so Python cannot regroup after the AVG has already
      happened -- the rule needs a SQL form. Building it from the same constant
      is what stops the boards and the engine drifting apart the first time
      someone moves the seam.

    Returns TEXT, not int, because every caller compares it against
    substring(...) results that are already text. Returning an int here would
    make `yr == season_year` silently false everywhere.
    """
    year = f"substring({date_col}, 1, 4)"
    month = f"substring({date_col}, 6, 2)"
    return (f"(CASE WHEN {month} >= '{ACADEMIC_START_MONTH:02d}' "
            f"THEN {year} ELSE ({year}::int - 1)::text END)")


def seasonYearSqlInt(sport=None, date_col="date"):
    """The same expression typed as int, for joins against int season columns.

    grade_fix.season and athlete_season_level.ay are integers, so a caller
    joining on them needs this rather than casting the text form at every site
    and getting it subtly wrong at one of them.
    """
    return (f"(CASE WHEN substring({date_col}, 6, 2) >= "
            f"'{ACADEMIC_START_MONTH:02d}' "
            f"THEN substring({date_col}, 1, 4)::int "
            f"ELSE substring({date_col}, 1, 4)::int - 1 END)")


# ------------------------------------------------------------------ #
#  4. WHERE THIS RULE MUST *NOT* BE APPLIED                           #
# ------------------------------------------------------------------ #
#
# ! pro_athlete_season IS STILL BUILT ON THE CALENDAR YEAR.
#
#   poolOf keeps two keys for that reason:
#       pkey = (pid, int(season))   calendar -- pro_athlete_season
#       gkey = (pid, ay)            academic -- grade_fix
#
#   That is the LAST table on the old clock, and it is a wart, not a design.
#   pro_flag.py writes it; until that is rebuilt with this module's rule, a
#   spring race looks up the wrong pro season. When it is rebuilt, pkey and
#   gkey collapse into one and this section deletes itself.
#
#   Everything else is already here:
#       grade_fix              academic, written by grade_sanity
#       athlete_season_level   academic
#       pair_athlete_season    academic, via the packer's grouping key
#       panels                 academic, via seasonYearSql
#       build_ranking_results  academic, via seasonYearFromIso
#
# ! college_first_season and upperclass_first_season are GONE from pooling.
#   resolvePool no longer consults them -- they promoted on a per-person date
#   with no reference to the row's own grade, and both were measured putting
#   middle schoolers on the college board. They are not season-keyed and need
#   nothing from this module.


# ------------------------------------------------------------------ #
#  5. SELF-CHECK -- `python engine\season_year.py`                    #
# ------------------------------------------------------------------ #

def _selfCheck():
    """The cases that encode the decisions. Run before adopting."""
    cases = [
        ("TF", date(2025, 12, 27), 2025, "Paterna's Dec race"),
        ("TF", date(2026,  1,  3), 2025, "and her Jan race -- SAME season"),
        ("TF", date(2025, 10,  4), 2025, "October indoor opener"),
        ("TF", date(2026,  6, 15), 2025, "June outdoor -- same season as Oct"),
        ("TF", date(2025,  7, 20), 2024, "July champs CLOSE the prior season"),
        ("TF", date(2025,  5, 30), 2024, "May outdoor, same season as July"),
        ("XC", date(2025, 12,  6), 2025, "NXN stays with autumn 2025"),
        ("XC", date(2025,  9, 13), 2025, "ordinary autumn XC"),
        ("XC", date(2026,  1, 17), 2025, "January XC closes the campaign"),
        ("XC", date(2026,  2,  7), 2025, "February XC, same campaign"),
        ("XC", date(2025,  3, 12), 2024, "Bosh's spring MS 3000"),
        ("XC", date(2025, 11, 14), 2025, "Bosh's autumn HS 5000 -- DIFFERENT"),
        ("XC", date(2025,  8, 30), 2025, "late-August XC opener"),
    ]

    ledger = RolloverLedger()
    bad = 0

    # The wrappers must agree on every case: build_ranking_results uses the iso
    # one and the engine uses the date one, so a divergence would put the site
    # and the engine in different seasons.
    for sport, d, _want, _why in cases:
        assert seasonYearFromIso(sport, d.isoformat()) == seasonYearFor(sport, d), \
            f"wrappers disagree on {sport} {d}"
        assert academicYear(d) == seasonYearFor(sport, d), \
            f"academicYear disagrees on {d}"
    print("  (all three Python forms agree)")

    # The SQL form is a FOURTH implementation, so it gets the same treatment:
    # evaluate it in Python and demand identical answers.
    for sport, d, _want, _why in cases:
        iso = d.isoformat()
        sql = int(iso[:4]) if iso[5:7] >= f"{ACADEMIC_START_MONTH:02d}" \
            else int(iso[:4]) - 1
        assert sql == seasonYearFor(sport, d), f"SQL disagrees on {sport} {d}"
    print("  (the SQL expression agrees too)\n")
    print("  SQL text:", seasonYearSql(None, "r.date"))
    print("  SQL int: ", seasonYearSqlInt(None, "r.date"), "\n")

    for sport, d, want, why in cases:
        got = seasonYearFor(sport, d, ledger)
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {sport} {d} -> {got} "
              f"(want {want})  {why}")

    print("\n" + ledger.line())
    print("\nall cases pass" if not bad else f"\n{bad} FAILURES")
    return bad


if __name__ == "__main__":
    raise SystemExit(_selfCheck())