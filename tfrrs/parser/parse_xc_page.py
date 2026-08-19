# Project: xc-predictor
# File:    tfrrs/parser/parse_xc_page.py
# Purpose: Parse a whole TFRRS XC meet page into a flat list of individual-result
#          rows, one per finisher per race. This is the XC analog of
#          parse_tf_page.py, but MUCH simpler: XC has no decoy columns, no
#          rolled-up-vs-heat split, and no combined events. The page parser's only
#          real jobs are (1) find the right tables and (2) label each with its
#          race context, then hand every <tr> to the existing parseXCRow.
#
#          The output rows are shaped to flow straight into the TFRRS glue layer
#          (save_tfrrs.buildTFRRSResultRows), which reads event_id/event_name off
#          each row — the same contract parse_tf_page satisfies.
 
import re
from bs4 import BeautifulSoup
from parse_xc import parseXCRow, _cellText, _toIntOrNone, _extractTeam
from parse_time import parseTimeToSeconds
 
# ------------------------------------------------------------------ #
# HOW A TFRRS XC MEET PAGE IS LAID OUT  (verified against real HTML)
# ------------------------------------------------------------------ #
#
# ONE page holds BOTH genders' races (this is the big difference from TF, where
# /m/ and /f/ are separate pages). The body is, per race:
#
#     <a name="event176680"></a>                         <- the race's id anchor
#     <h3>Men's Race - 8000 Meters Team Results (8k)</h3> <- TEAM table title
#     <table class="tablesaw tablesaw-xc"> ...team... </table>   <- SKIP (team)
#     <h3>Men's Race - 8000 Meters Individual Results (8k)</h3>  <- INDIV title
#     <table class="tablesaw tablesaw-xc"> ...indiv... </table>  <- KEEP (per-athlete)
#     <a name="event176681"></a>                          <- next race (Women's)
#     ...women's team table, women's individual table...
#
# THREE load-bearing facts, each read off the HTML you supplied:
#
#   * TEAM vs INDIVIDUAL tables share the SAME classes. We tell them apart by the
#     HEADER, not the class: an individual table's <thead> has a "NAME" column;
#     the team table's second column is "Team" and it has no "NAME". So
#     "NAME in the header" cleanly selects the per-athlete tables (and the team
#     rows, which have 12 cells, never reach the 7-cell parseXCRow at all).
#
#   * The CLEAN event title + id come from the page-top "LIST OF EVENTS" links:
#       <a href="#event176680">Men's Race - 8000 Meters</a>
#     We build a {event_id -> title} map from those once, then look each table's
#     title up by the id on its nearest preceding <a name="event..."> anchor.
#     This avoids the local <h3>, which has "Top^" and "(8k)" glued onto it.
#
#   * GENDER is in the title ("Men's"/"Women's"), and it is the WHOLE race's
#     gender — so we stamp it on every row, overriding parseXCRow's per-row guess
#     (which it derives from the team slug). A race is single-gender by definition.
#
# Name-only ancient rows (a finisher with no <a> link) and blank-score rows are
# handled inside parseXCRow already; the page parser just passes the <tr> down.
# ------------------------------------------------------------------ #


# parseXCPage
# Purpose: Parse one TFRRS XC meet page into a flat list of individual-result row
#          dicts (one per finisher per race), each carrying its race context.
# Arguments:
#           html:    the full meet-page HTML as one string.
#           meet_id: the TFRRS meet id, stamped onto every row for the DB.
# Output:   a list of row dicts. Each is the parseXCRow dict PLUS event context
#           (meet_id, event_id, event_name, gender, distance_meters). Rows that
#           fail parseXCRow are kept with ok=False (the glue drops them) so
#           nothing is silently discarded.
def parseXCPage(html, meet_id):
    # Parse the raw HTML into a navigable tree. "lxml" is the parser engine;
    # `soup` is then the whole page as objects we can search.
    soup = BeautifulSoup(html, "lxml")
 
    # STEP 1 - read the clean {event_id -> title} map ONCE from the top
    # "LIST OF EVENTS" links. Cheaper and cleaner than re-deriving a title from
    # each table's local <h3>.
    title_map = _buildEventTitleMap(soup)
 
    # STEP 2 - walk every result table, keep only the INDIVIDUAL ones, and parse
    # each finisher row with its race context attached.
    rows = []
    for table in _resultTables(soup):
        # Skip Team Results tables - they're a different shape (12 cols) and would
        # double-count athletes if we tried to read them as individuals.
        if not _isIndividualTable(table):
            continue
 
        event_id   = _eventIdForTable(table)
        # Prefer the clean title from the events-list map; fall back to the local
        # <h3> if this meet didn't render the list (older pages sometimes don't).
        event_name = title_map.get(event_id) or _titleForTable(table)
 
        rows.extend(_parseRaceTable(table, meet_id, event_id, event_name))
 
    return rows
 
 
# ------------------------------------------------------------------ #
# HELPERS - finding & classifying the tables
# ------------------------------------------------------------------ #


# _resultTables
# Purpose: Yield every result <table> on the page, in document order.
# Arguments:
#           soup: the parsed page tree.
# Output:   a list of <table> Tags (both team and individual; we filter next).
def _resultTables(soup):
    # All XC result tables carry the "tablesaw" class; the nav widgets are
    # <select>/<div>, so this is already clean.
    return soup.find_all("table", class_="tablesaw")
 
 
# _isIndividualTable
# Purpose: Decide whether a table is an INDIVIDUAL-results table (the per-athlete
#          one we keep) versus a TEAM-results table (skipped). Decided by the
#          presence of a "NAME" column header - the structural tell.
# Arguments:
#           table: a <table> Tag.
# Output:   True if the table's header row contains a "NAME" column.
def _isIndividualTable(table):
    head = table.find("thead")
    if head is None:
        return False
    # Uppercase every header cell so "Name"/"NAME"/"name" all compare equal.
    headers = [th.get_text().strip().upper() for th in head.find_all("th")]
    return "NAME" in headers
 
 
# _parseRaceTable
# Purpose: Parse one individual-results table into result rows, attaching the
#          race context (event id/name, gender, distance) to each.
# Arguments:
#           table:      an individual-results <table> Tag.
#           meet_id:    carried onto each row.
#           event_id:   this race's numeric id (from the anchor), or None.
#           event_name: this race's clean title (e.g. "Men's Race - 8000 Meters").
# Output:   a list of row dicts (one per <tbody> <tr>).
def _parseRaceTable(table, meet_id, event_id, event_name):
    # Race-level facts shared by every finisher in this table.
    gender   = _genderFromTitle(event_name)
    # Distance can live in the events-list title OR only in the <h3> "(5k)".
    # _resolveDistance tries the clean title first, then the <h3> fallback — so
    # class-named races ("Class I Boys") still get their distance from "(5k)".
    distance = _resolveDistance(event_name, table)
 
    body = table.find("tbody")
    if body is None:
        return []
 
    out = []
    # recursive=False: only this table's own rows, never rows of a nested table.
    for tr in body.find_all("tr", recursive=False):
        row = parseXCRow(tr)
        # Attach race context to EVERY row (parsed or not) so nothing floats free.
        # gender is overridden from the title because the race defines it - more
        # reliable than parseXCRow's per-row slug guess (and present even when a
        # row is name-only).
        row.update({
            "meet_id":         meet_id,
            "event_id":        event_id,
            "event_name":      event_name,
            "gender":          gender,
            "distance_meters": distance,
        })
        out.append(row)
    return out
 
 
# ------------------------------------------------------------------ #
# HELPERS - locating an event's id and title
# ------------------------------------------------------------------ #
 
 
# _buildEventTitleMap
# Purpose: Build {event_id -> clean title} from the page-top "LIST OF EVENTS"
#          links, the cleanest source of each race's name + distance.
# Arguments:
#           soup: the parsed page tree.
# Output:   a dict like {176680: "Men's Race - 8000 Meters", 176681: "Women's ..."}.
def _buildEventTitleMap(soup):
    title_map = {}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # The events-list links are in-page jumps: href="#event176680". The
        # per-table "Top^" links are href="#top", so they don't match.
        if not href.startswith("#event"):
            continue
        event_id = _eventIdFromAnchorName(href[1:])   # drop the leading '#'
        if event_id is None:
            continue
        title_map[event_id] = " ".join(link.get_text().split())
    return title_map
 
 
# _eventIdForTable
# Purpose: Find a table's event id - the nearest <a name="event..."> anchor ABOVE
#          it. Each race has one such anchor preceding both its tables.
# Arguments:
#           table: a <table> Tag.
# Output:   the integer event id, or None if no event anchor precedes it.
def _eventIdForTable(table):
    # attrs={"name": True} matches an <a> that HAS a name attribute (the event
    # anchors). Team/athlete links have href but no name, so they're ignored.
    anchor = table.find_previous("a", attrs={"name": True})
    if anchor is None:
        return None
    return _eventIdFromAnchorName(anchor.get("name", ""))
 
 
# _titleForTable
# Purpose: Fallback title for a table when the events-list map didn't have it -
#          the first text line of the nearest <h3> above the table.
# Arguments:
#           table: a <table> Tag.
# Output:   the title string (e.g. "Men's Race - 8000 Meters Individual Results
#           (8k)"), or None. The first stripped string skips the "Top^" anchor,
#           which is a later child of the same <h3>.
def _titleForTable(table):
    h3 = table.find_previous("h3")
    if h3 is None:
        return None
    for chunk in h3.stripped_strings:
        return chunk   # first non-empty line = the title text
    return None


# _fullTitleForTable
# Purpose: The FULL text of the nearest <h3> above a table, joining every text
#          chunk — used for DISTANCE extraction, where the "(5k)" may sit in the
#          <h3> even when the clean events-list title (e.g. "Class I Boys") omits
#          it. Unlike _titleForTable (first chunk only, for a clean NAME), this
#          keeps everything so the distance parser can see the "(5k)".
# Arguments:
#           table: a <table> Tag.
# Output:   the joined <h3> text (e.g. "Class I Boys Team Results (5k) Top↑"),
#           or None if there's no <h3>. The trailing "Top↑" anchor text is
#           harmless to distance parsing (it holds no digits+k/meters pattern).
def _fullTitleForTable(table):
    h3 = table.find_previous("h3")
    if h3 is None:
        return None
    # Join ALL stripped strings (not just the first), so a "(5k)" living in a
    # later chunk is included. " ".join over stripped_strings collapses the
    # <h3>'s internal whitespace/newlines into single spaces.
    chunks = list(h3.stripped_strings)
    return " ".join(chunks) if chunks else None


# _resolveDistance
# Purpose: Find a race's distance from the BEST available title. Distance and the
#          clean name can live in DIFFERENT places: some meets put "8000 Meters"
#          in the events-list title (so event_name carries it), others name the
#          race only by class/gender ("Class I Boys") and put the distance only in
#          the per-table <h3> as "(5k)". So we try the events-list title first,
#          then fall back to the full <h3> text.
# Arguments:
#           event_name: the clean events-list title (may or may not hold a distance).
#           table:      the race's <table> Tag (to reach its <h3> for the fallback).
# Output:   distance in metres (float), or None if neither title states one.
def _resolveDistance(event_name, table):
    # 1) Try the clean events-list title — where "8000 Meters" usually lives.
    distance = _distanceFromTitle(event_name)
    if distance is not None:
        return distance
    # 2) Fall back to the FULL <h3> text, which may carry the "(5k)" the clean
    #    title omitted. This is the case for class-named meets (109, 110).
    return _distanceFromTitle(_fullTitleForTable(table))
 
 
# _eventIdFromAnchorName
# Purpose: Pull the integer event id out of an anchor name / href fragment like
#          "event176680".
# Arguments:
#           name: e.g. "event176680".
# Output:   the integer id (176680), or None if the pattern isn't present.
def _eventIdFromAnchorName(name):
    match = re.search(r"event(\d+)", name)
    return int(match.group(1)) if match else None
 
 
# ------------------------------------------------------------------ #
# HELPERS - reading gender & distance off the title
# ------------------------------------------------------------------ #
 
 
# _genderFromTitle
# Purpose: Read gender from a race title. "Women"/"Girls" -> F, "Men"/"Boys" -> M.
#          Check WOMEN first, because the string "women" contains "men".
# Arguments:
#           title: the race title, or None.
# Output:   "M", "F", or None.
def _genderFromTitle(title):
    if not title:
        return None
    low = title.lower()
    if "women" in low or "girls" in low:
        return "F"
    if "men" in low or "boys" in low:
        return "M"
    return None
 
 
# ------------------------------------------------------------------ #
# DISTANCE PARSING - unit constants + converters + the ordered pattern list
# ------------------------------------------------------------------ #
#
# THE IDEA: every distance form a title can use is one (regex -> converter) pair.
# _distanceFromTitle walks the list IN ORDER and returns the first hit. Order is
# load-bearing; adding a unit is one more tuple, not a new branch.
#
# TWO REAL-DATA QUIRKS this handles:
#   * commas: "5,000 Meters" must read as 5000, not 5 — so we STRIP the thousands
#     comma from the title before matching (a comma otherwise walls the number).
#   * mangled "Mile": TFRRS writes some metric races as "5,000 Mile Run" meaning
#     5,000 METRES. A thousands-scale "mile" distance is physically absurd (nobody
#     races 5,000 miles), so a number >= 1000 followed by "mile" is really metres.
#     This rule is tried BEFORE the real-mile rule so "5000 Mile" -> 5000 m, while
#     a genuine "3 Mile" (small number) still falls through to real miles.

METRES_PER_MILE = 1609.344   # international mile (exact)
METRES_PER_KM   = 1000.0
METRES_PER_YARD = 0.9144     # exact

# The smallest "mile" number we treat as a mangled metre value. 3-8 mile races are
# real; a 4-digit "mile" is a mislabelled metre distance.
MANGLED_MILE_MIN = 1000


# _milesToMetres / _kmToMetres / _yardsToMetres / _identity
# Purpose : one-line converters, one per unit. Each takes the numeric string the
#           regex captured and returns metres (float). Named so the pattern list
#           reads "this regex -> miles" with no lambda logic hidden inside it.
# Argument: value - the captured number as a string, e.g. "3" or "2.5" or "5000".
# Output  : distance in metres (float).
def _milesToMetres(value):
    return float(value) * METRES_PER_MILE

def _kmToMetres(value):
    return float(value) * METRES_PER_KM

def _yardsToMetres(value):
    return float(value) * METRES_PER_YARD

def _identity(value):
    # Forms already in metres ("8000 Meters", "5000m", mangled "5,000 Mile").
    return float(value)


# _stripThousandsCommas
# Purpose : turn "5,000" into "5000" so a comma can't wall the number capture.
#           Only removes a comma sitting BETWEEN digits (a thousands separator),
#           never a comma used as punctuation elsewhere in the title.
# Argument: title - the raw title string.
# Output  : the title with intra-number commas removed.
def _stripThousandsCommas(title):
    # (?<=\d),(?=\d): a comma with a digit on each side — the thousands separator.
    # Lookarounds mean only the comma is removed, digits are kept.
    return re.sub(r"(?<=\d),(?=\d)", "", title)


# A number: digits with an optional decimal, captured in group(1).
_NUM = r"(\d+(?:\.\d+)?)"

# THE PATTERN LIST — first match wins. Ordering rules:
#   1. Meters/metres first: the fullest word, never mis-read by a shorter rule.
#   2. ★ thousands-mile (>=1000 + "mile") BEFORE real miles: claims the mangled
#      "5000 Mile" as metres before the mile rule can multiply it.
#   3. real miles (small "N mile"/"mi").
#   4. km ("k"/"km").
#   5. yards.
#   6. bare "m" suffix LAST ("5000m"); m NOT followed by a letter, so it can't
#      bite the "m" in "mile"/"meters".
_DISTANCE_PATTERNS = [
    # "8000 Meters" / "6000 metres"  ->  as-is
    (re.compile(_NUM + r"\s*met(?:er|re)s?", re.IGNORECASE), _identity),
    # ★ "5000 Mile" (number >= 1000) -> METRES. \d{4,} guarantees >= 1000, so a
    #   3/8-mile race never matches here and falls to the real-mile rule below.
    (re.compile(r"(\d{4,})\s*miles?\b", re.IGNORECASE), _identity),
    # ★ "8000k" (number >= 1000 + k) -> METRES. Same mangling one unit over: an
    #   "8000k" race is absurd (8,000 km); it means 8000 metres. Tried before the
    #   real-km rule so "8k"/"6k" (small number) still multiply to metres.
    (re.compile(r"(\d{4,})\s*k(?:m)?\b", re.IGNORECASE), _identity),
    # "3 Mile" / "2.5 Miles" / "2 mi"  ->  x1609.344
    (re.compile(_NUM + r"\s*(?:miles?|mi)\b", re.IGNORECASE), _milesToMetres),
    # "8k" / "8 km"  ->  x1000
    (re.compile(_NUM + r"\s*k(?:m)?\b", re.IGNORECASE), _kmToMetres),
    # "1000 Yards" / "880 yd"  ->  x0.9144
    (re.compile(_NUM + r"\s*(?:yards?|yd)\b", re.IGNORECASE), _yardsToMetres),
    # "5000m" bare-metre suffix, m not followed by another letter
    (re.compile(_NUM + r"\s*m(?![a-z])", re.IGNORECASE), _identity),
]


# _distanceFromTitle
# Purpose : read the race distance (metres) from a title, tolerant of commas and
#           the mangled-"mile" quirk. Walks _DISTANCE_PATTERNS; first unit wins.
# Argument: title - the race title (clean events-list title OR full <h3>), or None.
# Output  : distance in metres (float), or None if no known unit form is present.
def _distanceFromTitle(title):
    if not title:
        return None
    clean = _stripThousandsCommas(title)     # "5,000" -> "5000" first
    for pattern, convert in _DISTANCE_PATTERNS:
        match = pattern.search(clean)
        if match:
            return convert(match.group(1))   # group(1) = the captured number
    return None                              # no known unit -> genuinely unknown

# ================================================================== #
# TEAM RESULTS - the per-team scoring rows (PL / Team / Total Time / Avg. Time /
# Score / scorer places). These do NOT join the individuals in results_tf; they
# mirror anet's team scores and belong in meet_extras.team_scores_json. anet
# captures team scores for BOTH sports, so TFRRS does too - this is the XC half.
# (No TFRRS meet_extras glue exists yet; parseXCTeams produces the rows, the save
# path is a later, separate piece - exactly how the individual glue was staged.)
# ================================================================== #
 
 
# Fixed leading columns in a team row, before the scorer-place columns. Header
# order is: PL, Team, Total Time, Avg. Time, Score, then 1..7 (the scorers).
_TEAM_FIXED_COLS = 5
 
 
# parseXCTeams
# Purpose: Parse a TFRRS XC meet page's TEAM-results tables into a flat list of
#          team-score dicts, one per team per race. Sibling to parseXCPage (which
#          does the individuals); the two feed different DB destinations.
# Arguments:
#           html:    the full meet-page HTML.
#           meet_id: stamped onto every team row.
# Output:   a list of team-score dicts (see _parseTeamRow), each carrying its race
#           context (event_id, gender).
def parseXCTeams(html, meet_id):
    soup = BeautifulSoup(html, "lxml")
    title_map = _buildEventTitleMap(soup)
 
    teams = []
    for table in _resultTables(soup):
        # Keep only TEAM tables this time - the mirror of parseXCPage's filter.
        if not _isTeamTable(table):
            continue
        event_id   = _eventIdForTable(table)
        event_name = title_map.get(event_id) or _titleForTable(table)
        gender     = _genderFromTitle(event_name)
        teams.extend(_parseTeamTable(table, meet_id, event_id, gender))
    return teams
 
 
# _isTeamTable
# Purpose: Decide whether a table is a TEAM-results table. Keyed on the
#          "TOTAL TIME" header column, which only the team table has (the
#          individual table has "TIME" + "Avg. Mile" instead).
# Arguments:
#           table: a <table> Tag.
# Output:   True if the header carries a "TOTAL TIME" column.
def _isTeamTable(table):
    head = table.find("thead")
    if head is None:
        return False
    headers = [th.get_text().strip().upper() for th in head.find_all("th")]
    return "TOTAL TIME" in headers
 
 
# _parseTeamTable
# Purpose: Parse one team-results table into team-score dicts, attaching race
#          context (event id, gender) to each.
# Arguments:
#           table:    a team-results <table> Tag.
#           meet_id:  carried onto each row.
#           event_id: this race's numeric id, or None.
#           gender:   this race's gender ("M"/"F"/None), from the title.
# Output:   a list of team-score dicts.
def _parseTeamTable(table, meet_id, event_id, gender):
    body = table.find("tbody")
    if body is None:
        return []
    out = []
    for tr in body.find_all("tr", recursive=False):
        row = _parseTeamRow(tr)
        # Attach race context to every row (parsed or not) so nothing floats free.
        row.update({
            "meet_id":  meet_id,
            "event_id": event_id,
            "gender":   gender,
        })
        out.append(row)
    return out
 
 
# _parseTeamRow
# Purpose: Parse ONE team scoring <tr> into a structured dict - the team analog of
#          parseXCRow. Never raises; a malformed row returns ok=False so one bad
#          row can't kill the meet.
# Arguments:
#           tr: a <tr> from a team-results <tbody>.
# Output:   a dict with ok=True, or {"ok": False, "note": ...}. Fields map onto
#           anet's team_scores: place->Place, score->Points, team_name->Name,
#           team_slug->(SchoolID surrogate). total/avg time and scorer_places are
#           TFRRS extras anet doesn't carry.
def _parseTeamRow(tr):
    cells = tr.find_all("td")
 
    # Need at least the fixed columns (PL..Score) to read a team safely.
    if len(cells) < _TEAM_FIXED_COLS:
        return {"ok": False, "note": f"only {len(cells)} cells (not a team row)"}
 
    # [0] place; [1] team (link -> slug/name); [2] total time; [3] avg time;
    # [4] score; [5:] the scorers' individual places.
    place              = _toIntOrNone(_cellText(cells[0]))
    team_slug, team_name, _slug_gender = _extractTeam(cells[1])
    total_time_seconds = parseTimeToSeconds(_cellText(cells[2]))
    avg_time_seconds   = parseTimeToSeconds(_cellText(cells[3]))
    score              = _toIntOrNone(_cellText(cells[4]))
 
    # Trailing cells are the scorers' places; blank (&nbsp;) slots collapse to ""
    # and _toIntOrNone gives None, which we drop - so a team with <7 scorers just
    # yields a shorter list rather than a list padded with Nones.
    scorer_places = [
        p for p in (_toIntOrNone(_cellText(c)) for c in cells[_TEAM_FIXED_COLS:])
        if p is not None
    ]
 
    return {
        "ok":                 True,
        "note":               None,
        "place":              place,
        "team_name":          team_name,
        "team_slug":          team_slug,
        "total_time_seconds": total_time_seconds,
        "avg_time_seconds":   avg_time_seconds,
        "score":              score,
        "scorer_places":      scorer_places,
    }