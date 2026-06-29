# Project: xc-predictor
# File:    tfrrs/parser/parse_xc.py
# Purpose: Parse individual-result rows from a TFRRS XC meet page into structured
#          dicts. This is the per-row workhorse; a page-level parser (later) calls
#          parseXCRow once per finisher. We read the RAW table HTML, not the
#          rendered text, because the truth lives in the <a href> namespaces and
#          the cell ORDER — both of which a markdown rendering destroys.
 
import re
from parse_time import parseTimeToSeconds

# ------------------------------------------------------------------ #
# WHAT ONE TFRRS XC INDIVIDUAL ROW LOOKS LIKE  (verified against real HTML)
# ------------------------------------------------------------------ #
#
# Each finisher is one <tr> of SEVEN <td> cells, in this FIXED order:
#
#   [0] place        "13"
#   [1] name + link  <a href=".../athletes/8270167/...">Grace McDonough</a>
#   [2] year         "SR-4"   (eligibility; often blank on old meets)
#   [3] team + link  <a href=".../teams/xc/CT_college_f_Conn_College.html">..</a>
#   [4] avg mile     "5:44.9" (a derived pace — NOT the finish time)
#   [5] finish TIME  "21:26.1" (THE time we want; increases with place)
#   [6] score        "11"     (blank for non-scoring runners)
#
# Three facts, each of which cost a round of investigation, so they are
# load-bearing:
#
#   * The XC <tr> carries NO id / class / data-result-id. There is no
#     source-provided result identifier, so de-duplication downstream MUST be
#     content-based (athlete + meet + event + time). This parser never looks
#     for a result id, because there isn't one.
#
#   * The finish time is cell [5] — the SECOND time cell — NOT cell [4], which
#     is "avg mile". Reading [4] would give a per-mile pace and silently corrupt
#     every XC time. We deliberately skip [4].
#
#   * The athlete link is the tfrrs namespace (/athletes/<id>/). Old TF pages
#     use a different namespace (directathletics). We record WHICH namespace via
#     id_system so two different people sharing a number can't collide.
#
# And one quality case: old meets (e.g. the 2000 race) list name-only finishers
# with NO <a> link at all. Those parse to athlete_native_id=None and must be
# FLAGGED downstream, never dropped — the project's "ingest but flag" rule.
# ------------------------------------------------------------------ #
 
# Number of cells a well-formed XC individual row has. A row with a different
# count is malformed (or a header/spacer) and gets skipped + flagged rather than
# read by index — see the shape-check at the top of parseXCRow for why. A cell
# is one box in a table, a single <td> tag, each table contains all athletes' info
# and results, row is one athlete, each cell is info about that specific athlete.
# <table>                          <- the whole table
#   <tr>                           <- one ROW (table row)
#     <td>13</td>                  <- a CELL (the place)
#     <td>Grace McDonough</td>     <- a CELL (the name)
#     <td>SR-4</td>                <- a CELL (the year)
#     <td>21:26.1</td>             <- a CELL (the time)
#   </tr>
#   <tr>                           <- the NEXT row (next runner)
#     <td>14</td>
#     <td>Mia Kotler</td>
#     ...
#   </tr>
# </table>
#
# PL   NAME              YEAR   TEAM              AVG MILE   TIME      SCORE
# 13   Grace McDonough   SR-4   Conn. College    5:44.9     21:26.1   11      <- one ROW
# └┬┘  └──────┬───────┘  └─┬─┘  └──────┬──────┘  └──┬───┘   └──┬──┘   └┬┘
# cell      cell         cell        cell         cell       cell    cell
# Fixed leading columns every XC individual row has, in order:
#   [0]=PL [1]=NAME [2]=YEAR [3]=TEAM [4]=Avg Mile [5]=TIME [6]=SCORE
# Anything at index 7+ is a split column (2k, 4k, 6k, ...), and the COUNT of
# those varies by race distance — so we require AT LEAST this many cells, not
# exactly this many.
MIN_XC_CELL_COUNT = 7


# parseXCRow
# Purpose: Turn ONE <tr> from an XC individual-results table into a structured
#          dict. Reads the seven fixed leading columns by index, then collects
#          any trailing split columns (2k/4k/6k...) into a list.
# Arguments:
#           row: a BeautifulSoup Tag for a single <tr> from an individual table.
# Output:   a dict of parsed fields with ok=True, OR {ok=False, note} when the row
#           has too few cells to read safely. Never raises — one bad row must not
#           kill a meet's parse.
def parseXCRow(row):
    # Direct cells of this row.
    cells = row.find_all("td")

    # Shape check FIRST. We need the 7 fixed columns to read fields by index; a
    # shorter row would misalign (a place could land in the time slot). Splits
    # (index 7+) are optional, so the bar is ">= 7", not "== 7".
    if len(cells) < MIN_XC_CELL_COUNT:
        return {"ok": False, "note": f"too few cells {len(cells)}"}

    # [0] place — plain integer; _toIntOrNone tolerates a blank/tie marker.
    place = _toIntOrNone(_cellText(cells[0]))

    # [1] athlete — <a href> holds the native id, link text the name; native_id
    # is None for name-only ancient rows (no <a>).
    athlete_native_id, name = _extractAthlete(cells[1])

    # [2] year/eligibility — e.g. "SR-4"; often blank on old meets. Keep raw.
    year_raw = _cellText(cells[2])

    # [3] team — href encodes state/level/gender/name; _extractTeam returns slug,
    # display name, and gender decoded from the slug.
    team_slug, team_name, gender = _extractTeam(cells[3])

    # [5] finish time — the SECOND time cell. [4] (avg mile) is deliberately skipped.
    time_seconds = parseTimeToSeconds(_cellText(cells[5]))

    # [6] score — blank for non-scoring (displaced) runners.
    score = _toIntOrNone(_cellText(cells[6]))

    # [7:] splits — zero or more segment columns; count varies by race distance.
    splits = _extractSplits(cells[7:])

    return {
        "ok":                True,
        "note":              None,
        "place":             place,
        "athlete_native_id": athlete_native_id,
        "name":              name,
        "year_raw":          year_raw,
        "team_slug":         team_slug,
        "team_name":         team_name,
        "gender":            gender,
        "time_seconds":      time_seconds,
        "score":             score,
        "splits":            splits,
        "has_splits":        len(splits) > 0,
    }

# A split cell is either a bare interval "7:07.2" or "interval (cumulative)" like
# "7:12.0 (14:19.2)". This pulls both numbers out when present.
_SPLIT_RE = re.compile(r"([\d:.]+)\s*(?:\(([\d:.]+)\))?")


# _extractSplits
# Purpose: Turn the trailing split <td> cells of an XC row into a list of split
#          dicts, each with the segment interval and (when shown) the cumulative
#          time, both in seconds.
# Arguments:
#           split_cells: the row's cells from index 7 onward (may be empty).
# Output:   a list of {interval_seconds, cumulative_seconds} dicts, one per split
#           column that held a parseable time. Blank cells are skipped.
def _extractSplits(split_cells):
    splits = []
    for cell in split_cells:
        text = _cellText(cell)          # _cellText flattens the inner <div> to text
        if not text:
            continue                    # blank split column — skip it
        match = _SPLIT_RE.search(text)
        if match is None:
            continue                    # unparseable — skip rather than guess
        interval_raw, cumulative_raw = match.group(1), match.group(2)
        splits.append({
            "interval_seconds":   parseTimeToSeconds(interval_raw),
            # cumulative is None when the cell was a bare interval (e.g. the 2k col)
            "cumulative_seconds": parseTimeToSeconds(cumulative_raw) if cumulative_raw else None,
        })
    return splits

# ------------------------------------------------------------------ #
# SMALL HELPERS — each does exactly one extraction job, so parseXCRow reads as a
# list of "get this field, get that field" rather than a wall of soup code.
# ------------------------------------------------------------------ #


# _cellText
# Purpose: Get the trimmed visible text of a <td>, collapsing the messy internal
#          whitespace/newlines TFRRS wraps its cells in.
# Arguments:
#           cell: a BeautifulSoup <td> Tag.
# Output:   the cell's text with leading/trailing whitespace removed and internal
#           runs of whitespace collapsed to single spaces; "" if the cell is empty.
def _cellText(cell):
    # get_text() pulls all descendant text; " ".join(split()) collapses any run
    # of spaces/newlines/tabs to single spaces and trims the ends. So a cell of
    # "\n   21:26.1\n" becomes "21:26.1".
    return " ".join(cell.get_text().split())

# _toIntOrNone
# Purpose: Parse an int from cell text, returning None instead of crashing when
#          the text is blank or non-numeric (e.g. a non-scoring runner's empty
#          score, or a tie marker in the place column).
# Arguments:
#           text: already-trimmed cell text.
# Output:   int(text) when it is a clean integer, else None.
def _toIntOrNone(text):
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        # Non-numeric place/score. None keeps it from crashing the row; the value
        # simply isn't recorded — the same "don't guess" stance as the time parser.
        return None
     
# _extractAthlete
# Purpose: From the name cell, pull the athlete's TFRRS native id (out of the <a>
#          href) and display name (the <a> text). Handle the name-only ancient
#          case where there is no <a> at all.
# Arguments:
#           cell: the <td> for the athlete name (cell [1]).
# Output:   (native_id, name) where native_id is the integer in
#           /athletes/<native_id>/... or None when the cell has no link (old
#           name-only rows), and name is the display name.
def _extractAthlete(cell):
    link = cell.find("a")
 
    # No link -> ancient name-only finisher. native_id is None; the name is
    # whatever bare text the cell holds. Downstream flags this row.
    if link is None:
        return None, _cellText(cell)
 
    # Pull the numeric id out of the href: /athletes/<digits>/...
    href = link.get("href", "")
    native_id = _firstIntInPath(href, marker="/athletes/")
 
    # Link text is the display name (collapse its internal whitespace too).
    name = " ".join(link.get_text().split())
    return native_id, name

# _extractTeam
# Purpose: From the team cell, pull the team slug (the stable key inside the
#          href), the display name, and the gender — which TFRRS encodes INSIDE
#          the team slug ("_f_") rather than giving as its own column.
# Arguments:
#           cell: the <td> for the team (cell [3]).
# Output:   (team_slug, team_name, gender):
#             team_slug -- "CT_college_f_Conn_College" (None if no link)
#             team_name -- "Connecticut College"
#             gender    -- "M" / "F" / None, decoded from the slug
def _extractTeam(cell):
    link = cell.find("a")
 
    # No team link (rare): keep the bare text as the name, nothing else.
    if link is None:
        return None, _cellText(cell), None
 
    href = link.get("href", "")
    # The slug is the filename without ".html":
    #   .../teams/xc/CT_college_f_Conn_College.html -> CT_college_f_Conn_College
    team_slug = _teamSlugFromHref(href)
    team_name = " ".join(link.get_text().split())
    gender    = _genderFromTeamSlug(team_slug)
    return team_slug, team_name, gender
 
 
# _firstIntInPath
# Purpose: Extract the integer id that follows a known path marker in a URL —
#          e.g. the 8270167 in ".../athletes/8270167/Conn_College/Grace.html".
# Arguments:
#           href:   the full href string, this is the url in html form to
#                   an athlete's page. It has identifying info we need that
#                   we can get from the href, w/o navigating.
#           marker: the path segment the id follows, e.g. "/athletes/".
# Output:   the integer id, or None if the marker/id isn't present.
def _firstIntInPath(href, marker):
    # Find the marker, then read the first run of digits right after it.
    # The marker is the last path segment before the ID.
    idx = href.find(marker)
    if idx == -1:
        return None
    match = re.search(r"(\d+)", href[idx + len(marker):])
    return int(match.group(1)) if match else None
 
 
# _teamSlugFromHref
# Purpose: Reduce a team href to its stable slug key (the basename minus ".html").
# Arguments:
#           href: e.g. "https://www.tfrrs.org/teams/xc/CT_college_f_Conn_College.html"
# Output:   "CT_college_f_Conn_College", or None if the href has no basename.
def _teamSlugFromHref(href):
    if not href:
        return None
    # Everything after the last "/", then drop a trailing ".html".
    # So state_level_gender_team.
    basename = href.rstrip("/").split("/")[-1]
    # Drops trailing .html
    if basename.endswith(".html"):
        basename = basename[: -len(".html")]
    return basename or None
 
 
# _genderFromTeamSlug
# Purpose: Decode gender from a team slug, since XC rows have no gender column and
#          instead bury it in the team key as "_m_" or "_f_".
# Arguments:
#           slug: e.g. "CT_college_f_Conn_College".
# Output:   "M" if the slug carries the _m_ token, "F" if _f_, else None.
def _genderFromTeamSlug(slug):
    if not slug:
        return None
    # Pad with underscores so we match the _m_/_f_ TOKEN, not a stray 'm' inside a
    # school name (e.g. don't let the 'm' in "Conn" or "Williams" trigger it).
    padded = f"_{slug}_"
    if "_m_" in padded:
        return "M"
    if "_f_" in padded:
        return "F"
    return None