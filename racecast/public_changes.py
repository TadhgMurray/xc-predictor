"""
public_changes.py -- what we changed in how ratings and predictions are
made, in a reader's words, for "What's new" (activity.publicEvent).

★ WHY (owner, 2026-10-22: "give the people actual insight"). The numbers a
  full update moves are measured (scripts/run_changes.py); WHY they moved is
  often a change to the method, which only we know. Each entry is shown on
  the first full update after its date.

! ADD AN ENTRY WITH EVERY CHANGE A READER WOULD NOTICE: how ratings, course
  numbers or predictions are computed. Plain words, what changed and what it
  does; a measured effect only if it was measured. No internal names.
"""

# (date it went live, text), oldest first
CHANGES = [
    ("2026-10-22",
     "Predictions keep each runner's place in the field from their rating, "
     "and take the field's overall pace from the prediction model. On 80 "
     "recent races the typical miss on a runner's time was 2.97%, against "
     "3.44% from ratings alone."),
    ("2026-10-22",
     "A course with no difficulty number yet is treated as a typical course, "
     "not the fastest one. Predictions on such courses, most of them "
     "college courses, had been coming out too fast."),
    ("2026-10-22",
     "College cross country courses are now matched to their locations, so "
     "they get their own difficulty numbers like high school courses. Until "
     "now every college course was treated as unknown."),
]


def between(after, upto):
    """The entries dated after `after` (a date, or None for all) up to and
    including `upto` (a date). Pure."""
    out = []
    for day, text in CHANGES:
        if (after is None or day > after.isoformat()) and day <= upto.isoformat():
            out.append(text)
    return out
