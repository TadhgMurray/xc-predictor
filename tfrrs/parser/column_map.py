# Project: xc-predictor
# File:    tfrrs/parser/column_map.py
# Purpose: WHICH COLUMN IS WHICH, decided from what the cells CONTAIN rather
#          than from where they sit. Pure: no BeautifulSoup, no network, no
#          database -- the caller hands over (text, hrefs) tuples.
#
# ⚠⚠ THE FAILURE THIS EXISTS FOR (owner, 2026-09-17: "make sure tfrrs parses
#    well (no column shifts and such)"). Every parser in this directory reads
#    by fixed index -- cells[0] place, cells[2] year, cells[3] team, cells[5]
#    time. The only guard is `len(cells) < N`, which catches a row with too
#    FEW columns and passes a shifted one trivially, because a shifted row has
#    MORE. So if tfrrs ever inserts a column:
#
#      today   PL | NAME | YR | TEAM | AVG | TIME | SCORE
#      after   PL | BIB | NAME | YR | TEAM | AVG | TIME | SCORE
#
#    place, name and year still parse. The TIME comes from the AVG MILE cell,
#    and every race scraped after the change silently stores a per-mile pace
#    as a finish time. No error. We would find out weeks later, from the
#    ratings, with no cached HTML to re-parse (there is none -- I checked).
#
# ★ SO IT REPAIRS ITSELF (owner: "I'd prefer a self repairing solution").
#   The columns are found by what is in them, and the anchors are unambiguous:
#
#     athlete   the cell whose <a href> is in the /athletes/ namespace
#     team      the cell whose <a href> is in the /teams/ namespace
#     year      the cell matching the class vocabulary (SR-4, Jr., Freshman)
#     time      a cell that parses as a time
#     place     the last integer-only cell BEFORE the athlete
#     score     an integer-only cell AFTER the time
#
#   tfrrs may then insert, drop or reorder columns and the parse stays
#   correct -- and says the layout moved instead of going quiet.
#
# ⚠ THE ONE GENUINE AMBIGUITY IS THE TIME, and parse_xc's own header has
#   warned about it since the beginning: cell [4] is AVG MILE and cell [5] is
#   the finish, and BOTH parse as times. "Reading [4] would give a per-mile
#   pace and silently corrupt every XC time."
#
#   Two tiebreaks, in order:
#     1. the HEADER, when the page has one -- it literally says "Avg. Mile"
#        and "Time";
#     2. failing that, THE LARGEST time in the row. A finish time is longer
#        than the average mile inside it for every race over a mile, and at
#        exactly a mile the two are equal so either answer is the same number.
#
# ! VOTED ACROSS SAMPLE ROWS, NOT READ FROM ONE. A single row can be odd -- a
#   name-only finisher with no link, a blank year on an old meet, a DNF with
#   no time. The map is whatever most of the sampled rows agree on.
import re

from parse_time import parseTimeToSeconds

FIELDS = ("place", "athlete", "year", "team", "avg_mile", "time", "score")

# The layout every page has had so far. Returned when detection cannot
# improve on it, so the parsers behave exactly as they did before.
XC_DEFAULT = {"place": 0, "athlete": 1, "year": 2, "team": 3,
              "avg_mile": 4, "time": 5, "score": 6}

_ATHLETE_HREF = re.compile(r"/athletes?/", re.I)
_TEAM_HREF = re.compile(r"/teams?/", re.I)

# ! THE HEADER IS A HINT, NOT THE TRUTH. tfrrs has changed its wording before
#   ("PL" / "Place", "Avg. Mile" / "Avg Mile"), so a header that does not
#   match simply contributes nothing and the content decides.
_HEADER_PATTERNS = (
    ("place",    re.compile(r"^(pl|place)\.?$", re.I)),
    ("athlete",  re.compile(r"^(name|athlete)$", re.I)),
    ("year",     re.compile(r"^(yr|year|class|elig)\.?$", re.I)),
    ("team",     re.compile(r"^(team|school|affiliation)$", re.I)),
    ("avg_mile", re.compile(r"avg", re.I)),
    ("time",     re.compile(r"^(time|mark|result|finish)$", re.I)),
    ("score",    re.compile(r"^(score|pts|points)$", re.I)),
)

# SR-4, Jr., FR, RS, Freshman, So. -- the spellings grade_sanity.classGrade
# normalises, plus the bare high-school numbers a few meets carry.
_YEAR = re.compile(
    r"^(fr|so|jr|sr|rs|fy|freshman|freshmen|sophomore|junior|senior|"
    r"redshirt|soph)([\s.\-]*\d)?\.?$|^(9|10|11|12)$", re.I)

_INT_ONLY = re.compile(r"^\d{1,4}$")


def _text(cell):
    return (cell[0] if isinstance(cell, (tuple, list)) else str(cell or "")).strip()


def _hrefs(cell):
    if isinstance(cell, (tuple, list)) and len(cell) > 1:
        return [str(h or "") for h in (cell[1] or ())]
    return []


def _seconds(text):
    """The cell's value in seconds, or None. A bare integer is NOT a time --
    a score of "11" must never look like eleven seconds."""
    t = _text(text) if not isinstance(text, str) else text.strip()
    if not t or ":" not in t:
        return None
    try:
        return parseTimeToSeconds(t)
    except Exception:                                    # noqa: BLE001
        return None


def headerMap(header):
    """{field: index} from the header row's text, for whatever it names."""
    out = {}
    for i, cell in enumerate(header or ()):
        t = _text(cell)
        for field, pat in _HEADER_PATTERNS:
            if field not in out and pat.search(t):
                out[field] = i
                break
    return out


def _rowVote(cells, from_header):
    """One row's opinion about {field: index}. Pure."""
    seen = {}
    for i, cell in enumerate(cells):
        hrefs = " ".join(_hrefs(cell))
        if "athlete" not in seen and _ATHLETE_HREF.search(hrefs):
            seen["athlete"] = i
        elif "team" not in seen and _TEAM_HREF.search(hrefs):
            seen["team"] = i
    for i, cell in enumerate(cells):
        if "year" not in seen and _YEAR.match(_text(cell)):
            # ! NEVER THE PLACE. "11" matches the high-school year pattern and
            #   is also a perfectly good finishing position, so a bare number
            #   only counts as a year when it sits AFTER the athlete.
            if "athlete" in seen and i > seen["athlete"]:
                seen["year"] = i

    # every time-shaped cell, largest wins the finish (see the header note)
    times = [(i, _seconds(cell)) for i, cell in enumerate(cells)]
    times = [(i, s) for i, s in times if s]
    if times:
        if "time" in from_header and from_header["time"] in dict(times):
            seen["time"] = from_header["time"]
        else:
            seen["time"] = max(times, key=lambda kv: kv[1])[0]
        others = [i for i, _s in times if i != seen["time"]]
        if others:
            seen["avg_mile"] = (from_header.get("avg_mile")
                                if from_header.get("avg_mile") in others
                                else others[0])

    ints = [i for i, cell in enumerate(cells) if _INT_ONLY.match(_text(cell))]
    if "athlete" in seen:
        before = [i for i in ints if i < seen["athlete"]]
        if before:
            seen["place"] = before[-1]
    if "time" in seen:
        after = [i for i in ints if i > seen["time"]]
        if after:
            seen["score"] = after[0]
    return seen


def detectColumns(rows, header=None, default=None):
    """({field: index}, note) for a table.

    `rows` is a few sample data rows, each a list of cells; a cell is
    (text, [href, ...]) or plain text. `header` is the <th> texts if the page
    has them. `default` is the layout to fall back to per field.

    The note is None when the answer is the default layout, and a human
    sentence naming what moved when it is not -- so a shift is LOUD.
    """
    default = XC_DEFAULT if default is None else default
    from_header = headerMap(header)
    votes = {}
    for cells in rows or ():
        for field, idx in _rowVote(cells, from_header).items():
            votes.setdefault(field, {})
            votes[field][idx] = votes[field].get(idx, 0) + 1

    out = {}
    for field in FIELDS:
        if votes.get(field):
            # the index most rows agreed on; ties to the smaller index
            out[field] = min(votes[field].items(),
                             key=lambda kv: (-kv[1], kv[0]))[0]
        elif field in from_header:
            out[field] = from_header[field]
        elif field in default:
            out[field] = default[field]

    moved = {f: (default.get(f), out.get(f))
             for f in FIELDS
             if f in out and f in default and out[f] != default[f]}
    note = None
    if moved:
        note = ("tfrrs column layout moved: "
                + ", ".join(f"{f} {was}->{now}" for f, (was, now)
                            in sorted(moved.items())))
    return out, note


def trustworthy(colmap):
    """Is this map safe to parse with? The two anchors that cannot be guessed
    from position are the athlete and the finish time; without them a row is
    refused rather than read into the wrong fields."""
    return "athlete" in colmap and "time" in colmap
