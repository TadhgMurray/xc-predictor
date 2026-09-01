"""A query cap that says whether it bit.

Issue #57, point 2 -- the one the entry calls out as worth doing on its own:
"it is the only one that lets the site show a partial answer as if it were a
complete one".

★ THE PROBLEM IS NOT THE CAP, IT IS THE SILENCE. schoolMeets and
  get_course_meets stop at 2000 rows, schoolBest and schoolTopAthletes at 100,
  and a page that hits one of those says nothing at all. Worse, the staged
  reveal (staged.js) then runs out AT THE CAP and removes its own "show more"
  button -- so the reader is shown the end of the button, which reads as the
  end of the data. A school with 2400 meets looks like a school with 2000.

★ FETCH ONE MORE THAN YOU MEAN TO SHOW. That is the whole trick, and it costs
  one row: if the query can produce limit+1 rows then it had more to give, and
  the extra row is discarded. No count(*) over the same predicate -- that is a
  second full scan to answer a question the first scan already knew.

! IT RETURNS A LIST, NOT A TUPLE, ON PURPOSE. `Capped` IS a list, so every
  existing caller -- len(), slicing, iteration, `if rows:`, Jinja's `|length`
  -- keeps working untouched, and the templates that want to say so read
  `rows.truncated` and `rows.shown`. Changing four return signatures to
  (rows, flag) would have meant editing every call site and every template
  that iterates them, for a flag most of them do not use.
"""


class Capped(list):
    """Rows, plus whether the query had more it was not asked for."""

    #: True when the cap bit -- there are more rows behind this list.
    truncated = False
    #: The cap that was applied, for a message that can name it.
    shown = 0

    def __repr__(self):                                  # pragma: no cover
        return (f"<Capped {len(self)} rows"
                f"{' (truncated)' if self.truncated else ''}>")


def fetchCapped(cur, limit):
    """Read a cursor that was given `limit + 1`, and report the overflow.

    ⚠ THE CALLER MUST HAVE ASKED FOR limit + 1. This cannot check that -- it
      is handed a cursor, not a query -- so the two live together at every
      call site: `LIMIT %(lim)s` with `lim = limit + 1`, then this. Passing
      the plain limit makes `truncated` permanently False, which is the old
      silent behaviour rather than a crash, so the pairing is asserted in
      tests/test_capped.py instead.
    """
    rows = cur.fetchall()
    out = Capped(rows[:limit])
    out.truncated = len(rows) > limit
    out.shown = limit
    return out
