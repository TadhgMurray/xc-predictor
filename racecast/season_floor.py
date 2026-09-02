"""The race-count floor a season average must clear to be ranked, and the
small phrases the athlete header builds from a rank.

★ THE FLOOR IS A FACT ABOUT THE SEASON'S YEAR (owner, 2026-09-02).
  Seasons up to 2026 TF keep the floor of three: those years are settled,
  and a two-race "season average" there is a performance wearing an
  ability's name. From 2026 XC on there is NO floor -- the season is live,
  a roster is a roster from its first meet, and a wild one-race season is
  cheaper to drop by hand than a whole roster is to hide until October.

  The year here is the STORED one: XC is stored under its own year and TF
  under the year it opens in (2026 TF is stored 2025). So "after 2026 TF"
  is exactly `year >= 2026` on the table, for both sports.

Used by rankings (the boards and the rank endpoint), the athlete rank line,
and the home-page season panels. One rule, three readers.
"""

DEFAULT_FLOOR = 3
OPEN_FROM = 2026        # stored year; see the note above


def isOpen(stored_year):
    """True for a season that carries no floor."""
    return stored_year is not None and int(stored_year) >= OPEN_FROM


def floorFor(stored_year):
    """The floor one season is held to: 1 (no floor) or DEFAULT_FLOOR."""
    return 1 if isOpen(stored_year) else DEFAULT_FLOOR


def floorSql(explicit, alias="s"):
    """The WHERE fragment for a board.

    explicit  the reader typed a minimum: it applies to every row, open
              seasons included -- a filter they set is theirs.
    default   the board's own floor, which open seasons are exempt from.

    Both read %(min_races)s; the caller binds it either way."""
    if explicit:
        return f"{alias}.n_races >= %(min_races)s"
    return (f"({alias}.n_races >= %(min_races)s "
            f"OR {alias}.year >= {int(OPEN_FROM)})")


def floorLabel(stored_year):
    """What the rank line says about its floor: '(3+ races)' or nothing."""
    return "" if isOpen(stored_year) else f"({DEFAULT_FLOOR}+ races)"


# ----------------------------------------------------------------------
#  header phrases
# ----------------------------------------------------------------------

POOL_WORDS = {
    "hs_m": "high-school boys", "hs_f": "high-school girls",
    "college_m": "college men",  "college_f": "college women",
    "ms_m": "middle-school boys", "ms_f": "middle-school girls",
}


def poolWords(pool):
    return POOL_WORDS.get((pool or "").split("|", 1)[0], pool or "")


def percentileWords(rank, total):
    """'top 2%' for rank 41 of 8123; None when there is no board to stand in.
    Rounded UP so nobody is called top 0%, and never better than top 1%."""
    if not rank or not total or total < 1:
        return None
    pct = -(-100 * int(rank) // int(total))       # ceiling
    return f"top {max(1, min(100, pct))}%"


def clockFor(seconds):
    """A 5K-equivalent in m:ss, whole seconds -- a headline, not a result."""
    if seconds is None or seconds <= 0:
        return None
    s = int(round(float(seconds)))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"
