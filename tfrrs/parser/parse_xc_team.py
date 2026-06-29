# Project: xc-predictor
# File:    tfrrs/parser/parse_xc_team.py
# Purpose: Parse the TEAM RESULTS table from a TFRRS XC meet into a structured
#          list, ready to store as the team_scores_json blob in meet_extras
#          (matching the athletic.net team-scores shape). We keep EVERY column
#          the table has, plus the team href slug, plus raw-and-parsed times, so
#          structuring loses nothing recoverable versus storing the raw HTML.
 
from parse_time import parseTimeToSeconds
# Reuse the slug/gender helpers from the individual parser — one source of truth,
# no duplicated logic (the team href has the same shape as an athlete-row team).
from parse_xc import _teamSlugFromHref, _genderFromTeamSlug, _cellText, _toIntOrNone

# ------------------------------------------------------------------ #
# WHAT ONE TFRRS XC TEAM ROW LOOKS LIKE  (verified against real HTML)
# ------------------------------------------------------------------ #
#
# The team table header is:
#   PL | Team | Total Time | Avg. Time | Score | 1 | 2 | 3 | 4 | 5 | 6 | 7
#
# so a row has 5 fixed cells then SEVEN scorer-place cells = 12 cells:
#
#   [0] place        "1"
#   [1] team + link  <a href=".../WI_college_m_Wis_La_Crosse.html">Wis.-La Crosse</a>
#   [2] total time   "2:01:09"  (hh:mm:ss — the 7 scorers' times summed)
#   [3] avg time     "24:13"
#   [4] score        "82"       <td id="team_score_13">82</td>  <- mystery id, see below
#   [5..11] scorers  "3" "4" "5" "13" "57" "58" "76"  (the PLACES of the scoring
#                                                       runners — NOT athlete ids)
#
# Two facts worth stating plainly:
#
#   * The scorer cells are FINISH PLACES, not athlete identities. To learn WHO
#     scored you cross-reference the individual table (place 3 = whoever ran 3rd).
#     The team blob alone is place-based; that matches what athletic.net stores.
#
#   * The score cell carries id="team_score_NN" (e.g. 13). We have NO idea yet
#     whether NN is a globally-stable team id or a page-local counter, so we
#     CAPTURE it under "team_score_id" and FLAG it as uncertain rather than drop
#     it — cheap insurance against throwing away a possibly-useful key.
# ------------------------------------------------------------------ #

# 5 fixed columns (PL, Team, Total, Avg, Score) + 7 scorer columns. Used as a
# soft check: a row with fewer cells than the fixed 5 can't be read and is
# skipped + flagged, same stance as the individual parser.
MIN_TEAM_CELL_COUNT = 5

# parseXCTeamRow
# Purpose: Turn ONE team-results <tr> into a structured dict for the blob.
# Arguments:
#           row: a BeautifulSoup <tr> Tag from the team-results <tbody>.
# Output:   a dict of team fields with ok=True, OR {"ok": False, "note": ...}
#           when the row is too malformed to read. Never raises.
def parseXCTeamRow(row):

    cells = row.find_all("td")
 
    # Shape check first: we need at least the 5 fixed columns to read anything by
    # position. Fewer than that and we refuse to guess — skip + flag.
    if len(cells) < MIN_TEAM_CELL_COUNT:
        return {"ok": False, "note": f"team row only {len(cells)} cells"}
 
    # [0] place — the team's finish place.
    place = _toIntOrNone(_cellText(cells[0]))
 
    # [1] team — slug (the stable join key), display name, and gender from slug.
    team_slug, team_name, gender = _extractTeamCell(cells[1])
 
    # [2]/[3] times — store BOTH raw (lossless) and parsed (queryable), so a
    # parse bug never destroys the original value.
    total_raw     = _cellText(cells[2])
    total_seconds = parseTimeToSeconds(total_raw)
    avg_raw       = _cellText(cells[3])
    avg_seconds   = parseTimeToSeconds(avg_raw)
 
    # [4] score — the team's points. Also carries the mystery id="team_score_NN".
    score         = _toIntOrNone(_cellText(cells[4]))
    team_score_id = _teamScoreIdFromCell(cells[4])
 
    # [5..] scorer places — the remaining cells are the 7 scoring runners' PLACES
    # (sometimes fewer than 7 if a team didn't field a full scoring squad).
    scorer_places = _scorerPlaces(cells[5:])
 
    return {
        "ok":                 True,
        "note":               None,
        "place":              place,
        "team_slug":          team_slug,
        "team_name":          team_name,
        "gender":             gender,
        "score":              score,
        "total_time_seconds": total_seconds,
        "total_time_raw":     total_raw,
        "avg_time_seconds":   avg_seconds,
        "avg_time_raw":       avg_raw,
        "scorer_places":      scorer_places,
        # FLAGGED uncertain — we don't yet know if this is a real team id.
        "team_score_id":      team_score_id,
    }

# parseXCTeamTable
# Purpose: Parse a whole team-results <table> into a list of team dicts — the
#          thing handed to saveMeetExtras as team_scores_array.
# Arguments:
#           table: the BeautifulSoup <table> Tag for a Team Results table.
# Output:   a list of clean team dicts (malformed rows dropped after flagging).
def parseXCTeamTable(table):
    teams = []
    body = table.find("tbody")
    if body is None:
        return teams
    
    # For each teams section in the HTML, parses it and appends it to the teams
    # list.
    for tr in body.find_all("tr"):
        parsed = parseXCTeamRow(tr)
        if parsed["ok"]:
            teams.append(parsed)
    return teams


# ------------------------------------------------------------------ #
# SMALL HELPERS
# ------------------------------------------------------------------ #


# _extractTeamCell
# Purpose: From the team cell, pull (slug, name, gender) — reusing the same
#          href-slug + gender logic the individual parser uses for its team cell.
# Arguments:
#           cell: the <td> for the team (cell [1]).
# Output:   (team_slug, team_name, gender), with None fields if there's no link.
def _extractTeamCell(cell):
    # Tries to find the team's link, which starts with "<a>"
    link = cell.find("a")

    # If not link collect bare text, salvage the name.
    if link is None:
        return None, _cellText(cell), None
    
    # Gets href by reading the URL, Gets team slug (state_pool_school),
    # digs out team name and team gender.
    href      = link.get("href", "")
    team_slug = _teamSlugFromHref(href)
    team_name = " ".join(link.get_text().split())
    gender    = _genderFromTeamSlug(team_slug)
    return team_slug, team_name, gender

# _teamScoreIdFromCell
# Purpose: Pull the NN out of the score cell's id="team_score_NN" attribute.
#          We don't know what NN means yet, so we just capture it; downstream
#          treats it as uncertain.
# Arguments:
#           cell: the score <td>, e.g. <td id="team_score_13">82</td>.
# Output:   the integer NN (13), or None if the cell has no such id.
def _teamScoreIdFromCell(cell):

    # Gets the id attribute of team_score.
    raw_id = cell.get("id", "")
    marker = "team_score_"

    # If it starts with team_score we take the digits out
    # by removing the marker and converting to digits.
    if raw_id.startswith(marker):
        digits = raw_id[len(marker):]
        return int(digits) if digits.isdigit() else None
    return None
 
 
# _scorerPlaces
# Purpose: Turn the trailing scorer cells into a list of integer finish-PLACES,
#          dropping the empty "&nbsp;" cells a short-handed team leaves behind.
# Arguments:
#           cells: the list of <td>s AFTER the score column (the 7 counter cols).
# Output:   a list of ints, e.g. [3, 4, 5, 13, 57, 58, 76]. Blank/non-numeric
#           counter cells (a team with <7 scorers) are simply omitted.
def _scorerPlaces(cells):
    places = []

    # Gets all the places for a team. Gets the slice of all cells after
    # the score columns, grabs the seven counter problems, and stores
    # them.
    for cell in cells:
        value = _toIntOrNone(_cellText(cell))
        # Omit blanks (the "&nbsp;" filler for teams with fewer than 7 scorers).
        if value is not None:
            places.append(value)
    return places