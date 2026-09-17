# Project: xc-predictor
# File:    tfrrs/parser/parse_tf.py
# Purpose: Parse one TF result row, reading the REAL time/mark out of TFRRS's
#          anti-scraper decoy columns. TF rows carry several TIME cells; all but
#          one are fake, and the fakes are hidden by injected CSS. We read that
#          CSS to know which column is real. This is the crux of the TF parser.
 
import re
from parse_time import parseTimeToSeconds
from parse_xc import (_cellText, _toIntOrNone, _firstIntInPath,
                      _teamSlugFromHref)
from column_map import TF_DEFAULT, trustworthy
 
# ------------------------------------------------------------------ #
# HOW TFRRS HIDES THE REAL TIME  (verified against raw HTML)
# ------------------------------------------------------------------ #
#
# A TF result table has MULTIPLE <td class="compiled_round_4_<event>_<col>">
# time cells per row — but only ONE is the real time. The others are decoys
# with random values (a 100m row showing 10.22 AND 10.84 AND 8.06...). TFRRS
# hides the decoys with injected CSS right before the table:
#
#     <style>
#         .compiled_round_4_6006712_97 { display: none !important; }
#     </style>
#
# So column "_97" is hidden -> a decoy. The cell whose class is NOT hidden is
# the real time. We collect every hidden class from the page's <style> blocks,
# then in each row pick the time cell whose class is NOT in that hidden set.
#
# Athlete-id namespaces vary PER ROW in TF: most rows are directathletics
# (/athletes/track/<id>), some are tfrrs (/athletes/<id>). We detect which per
# row and record it as id_system so a directathletics 8050334 and a tfrrs
# 8050334 can't collide.
#
# Name-only rows (no <a>) and DNF rows (time cell text "DNF") both occur and are
# handled: name-only -> native_id None + flag; DNF -> time None.
# ------------------------------------------------------------------ #
 
# The two id-namespaces a TF athlete link can use, with the path marker that
# precedes the numeric id and the id_system label we record.
_ATHLETE_NAMESPACES = (
    ("directathletics.com/athletes/track/", "directathletics"),
    ("tfrrs.org/athletes/",                 "tfrrs"),
)
 
# The class-name prefixes that mark a RESULT cell (the time/mark/points columns
# that carry decoys). TFRRS uses TWO parallel naming schemes:
#   - regular events:  compiled_round_* (overall) / compiled_heat_* (per section)
#   - combined events: round_*         (the parent decathlon/heptathlon total)
#                      sub_round_*      (a leg's overall, e.g. the decathlon 100m)
#                      sub_heat_*       (a leg's per-section table)
# We must recognise all of them, or a combined-event page yields zero result
# cells and every row silently returns None. Order doesn't matter; we test by
# startswith against this whole tuple.
_RESULT_CELL_PREFIXES = (
    "compiled_round_",
    "compiled_heat_",
    "sub_round_",
    "sub_heat_",
    "round_",          # bare round_ is the combined-event PARENT total — keep
)                      # this LAST conceptually; see _isResultCell for the catch.

# collectHiddenClasses
# Purpose: Scan a page (or table region) for the CSS that hides decoy columns,
#          returning the set of hidden column-class names. The row parser uses
#          this set to know which time cell is real.
# Arguments:
#           soup: a BeautifulSoup tree (the whole page, or an event's region).
# Output:   a set of class-name strings, e.g. {"compiled_round_4_6006712_97"}.
def collectHiddenClasses(soup):
    hidden = set()
    # Every decoy-hiding rule lives in a <style> tag as
    #   .<classname> { display: none !important; }
    # We pull each .<classname> that is paired with display:none.
    for style in soup.find_all("style"):

        # For all the style tags we pull each decoy column.
        css = style.get_text()
        # Find every ".name { ... display: none ... }" block.
        for match in re.finditer(r"\.([\w]+)\s*\{[^}]*display:\s*none", css):
            hidden.add(match.group(1))
    return hidden

# parseTFRow
# Purpose: Parse one TF result <tr> into a structured dict, choosing the real
#          time/mark/points via the hidden-class set.
# Arguments:
#           row:           a BeautifulSoup <tr> Tag from a TF result <tbody>.
#           hidden_classes: the set from collectHiddenClasses — which result
#                          columns are decoys.
#           result_kind:   "running" (read a TIME), "field" (read a MARK), or
#                          "combined" (read a POINTS total). Decided upstream
#                          from the table's <th> header (TIME/MARK/POINTS).
# Output:   a dict of fields with ok=True, or {"ok": False, "note": ...}. Never
#           raises. note carries a decoy-scheme-change flag when the decoder
#           found other than exactly one visible result column.
# ★ colmap: THE FOUR FIXED COLUMNS IN FRONT OF THE RESULT, worked out once
#   per table from what the cells contain (column_map). The result cell is NOT
#   in it and must not be -- _realResultCellText picks that by reading the
#   injected CSS, which is the crux of this parser and already content-driven.
#   What this adds is that PL / NAME / YEAR / TEAM were read by index exactly
#   like XC's, and would shift exactly like XC's.
#
# ! None means today's layout, which is byte-for-byte what this function has
#   always done.
def parseTFRow(row, hidden_classes, result_kind, colmap=None):

    # Finds all results for an event in the row.
    cells = row.find_all("td")
 
    # A real result row has at least PL, NAME, YEAR, TEAM, and one result cell.
    # Fewer than 5 is a sub-row (the field-series <ul> row) or a spacer — skip.
    if len(cells) < 5:
        return {"ok": False, "note": f"only {len(cells)} cells (sub-row/spacer)"}
 
    # ⚠ AND THE CELL COUNT IS NOT A SHAPE CHECK. It catches a row with too
    #   FEW cells; a row with an INSERTED column has more and sails through.
    cm = colmap or TF_DEFAULT
    # ! ("athlete",) ONLY. Requiring a `time` would refuse every well-formed
    #   track row, because the result cell is the decoder's job below.
    if not trustworthy(cm, ("athlete",)):
        return {"ok": False, "note": "column layout not recognised"}

    def _at(field):
        i = cm.get(field)
        return cells[i] if i is not None and i < len(cells) else None

    def _textAt(field):
        c = _at(field)
        return _cellText(c) if c is not None else ""

    # place — may be blank ("&nbsp;") for DNF/unplaced rows.
    place = _toIntOrNone(_textAt("place"))

    # athlete — link gives (native_id, id_system); name-only gives None id.
    _a = _at("athlete")
    athlete_native_id, id_system, name = (
        _extractTFAthlete(_a) if _a is not None else (None, None, ""))

    # year — e.g. "Senior"/"SR-4"/blank. Kept raw for the grade normaliser.
    year_raw = _textAt("year")

    # team — the raw team text (TF often has unattached/club names with no
    # link; we keep the text and the link/id when present).
    _t = _at("team")
    (team_name, team_native_id, team_id_system,
     team_slug) = (_extractTFTeam(_t) if _t is not None
                   else ("", None, None, None))
 
    # The ONE real result cell (decoder picks the non-hidden column). Its meaning
    # depends on result_kind: a TIME (running), a MARK (field), or a POINTS total
    # (combined). status flags a decoy-scheme change (0 or >1 visible columns).
    result_raw, status = _realResultCellText(cells, hidden_classes)
 
    # Interpret the one real value per kind. Unused fields stay None so every
    # row has the same shape regardless of event type.
    time_seconds  = None
    mark_metres   = None
    mark_raw      = None
    points_total  = None
    if result_kind == "field":
        mark_raw    = result_raw
        mark_metres = _parseMarkMetres(result_raw)
    elif result_kind == "combined":
        points_total = _toIntOrNone(result_raw)   # decathlon/heptathlon score
    else:  # "running" (default)
        time_seconds = parseTimeToSeconds(result_raw)

    # Wind (sprints/jumps only): the value sits in a trailing <nobr> cell, e.g.
    # <td><nobr>2.6</nobr></td>. Absent for distance/throw events -> None.
    wind = _extractWind(cells)

    # Individual runner's score.
    score = _extractTFScore(cells)
 
    return {
        "ok":                True,
        "note":              status if status != "ok" else None,
        "place":             place,
        "athlete_native_id": athlete_native_id,
        "id_system":         id_system,
        "name":              name,
        "year_raw":          year_raw,
        "team_name":         team_name,
        "team_native_id":    team_native_id,
        "team_id_system":    team_id_system,
        # The tfrrs team page's own filename, the same stable key the XC parser
        # reads. None for the unlinked club/unattached rows TF is full of.
        "team_slug":         team_slug,
        "result_kind":       result_kind,
        "time_seconds":      time_seconds,   # set for running; None otherwise
        "mark_metres":       mark_metres,    # set for field; None otherwise
        "mark_raw":          mark_raw,       # raw "8.28m" for field events
        "points_total":      points_total,   # set for combined; None otherwise
        "wind":              wind,           # float m/s, or None if no wind
        "score":             score,
    }


# ------------------------------------------------------------------ #
# HELPERS
# ------------------------------------------------------------------ #
 
 
# _realResultCellText
# Purpose: From a row's cells, return the text of the ONE real result cell —
#          the time/mark cell whose class is NOT in the hidden (decoy) set.
# Arguments:
#           cells:          the row's <td> list.
#           hidden_classes: the decoy class set from collectHiddenClasses.
# Output:   the real cell's text (e.g. "10.22" or "8.28m"), or None if no
#           visible result cell is found.
#
# DEFENSIVE: there should be EXACTLY ONE visible result cell per row. If TFRRS
# changes the decoy scheme (renames classes, stops hiding, hides all), we'd see
# zero or several visible cells. Rather than silently grab the wrong one, we
# return None AND a flag so the caller can shout. Catching a scheme change loudly
# is the whole point — a silent miss poisons the dataset (exactly the trap).
def _realResultCellText(cells, hidden_classes):

    visible = []

    # For each result in the event, finds the real time.
    for cell in cells:
        classes = cell.get("class", [])
        if not _isResultCell(classes):
            continue
        # A result cell is REAL (visible) when none of its classes are hidden.
        if not any(c in hidden_classes for c in classes):
            visible.append(_cellText(cell))
 
    # Exactly one visible result cell is the healthy case.
    if len(visible) == 1:
        return visible[0], "ok"
    if len(visible) == 0:
        return None, "no_visible_result_cell"   # scheme change, or non-result row
    
    return visible[0], f"multiple_visible_result_cells:{len(visible)}"  # scheme change

# _isResultCell
# Purpose: Decide whether a cell's classes mark it as a result (time/mark/points)
#          column — i.e. one of the decoy-bearing columns under any of TFRRS's
#          four naming schemes.
# Arguments:
#           classes: the cell's class list (from cell.get("class", [])).
# Output:   True if any class starts with a known result-cell prefix.
def _isResultCell(classes):
    
    # Looks at one table cell's CSS classes and return true if
    # any class has a time/mark/point result. any means
    # if any is true, return true.
    return any(
        c.startswith(prefix)          # the test
        for c in classes              # loop 1: each class on the cell
        for prefix in _RESULT_CELL_PREFIXES   # loop 2: each known prefix
    )

# _extractWind
# Purpose: Pull the wind value (m/s) from a row. Wind sits in a trailing cell
#          wrapped in <nobr>, e.g. <td><nobr>2.6</nobr></td> or <nobr>-0.1</nobr>.
#          Distance and throw events have no wind -> None. A "-" placeholder
#          (DNF/no-wind rows) also -> None.
# Arguments:
#           cells: the row's <td> list.
# Output:   wind as a float (may be negative), or None when absent.
def _extractWind(cells):

    # Look over each cell in a row, finding if it's wind cell <nobr> </nobr>,
    # then returns the float inbetween.
    for cell in cells:
        nobr = cell.find("nobr")
        if nobr is None:
            continue
        text = nobr.get_text().strip()
        # Accept a signed decimal like "2.6" or "-0.1"; reject "-" / blanks.
        match = re.match(r"-?\d+(?:\.\d+)?$", text)
        if match:
            return float(text)
    return None

# _extractTFAthlete
# Purpose: From the name cell, pull (native_id, id_system, name), detecting which
#          id-namespace the link uses (directathletics vs tfrrs). Name-only rows
#          return (None, None, name).
# Arguments:
#           cell: the athlete-name <td>.
# Output:   (native_id, id_system, name).
def _extractTFAthlete(cell):

    link = cell.find("a")
    if link is None:
        # Name-only row (e.g. "Ibrahim Fuseini"). Flagged downstream by the
        # None id; we still keep the displayed name.
        return None, None, _cellText(cell)
 
    href = link.get("href", "")
    name = " ".join(link.get_text().split())
 
    # Try each known namespace; the first whose marker appears wins, and tells
    # us BOTH the id and which system it belongs to.
    for marker, system in _ATHLETE_NAMESPACES:
        if marker in href:
            # Pulls the stable numerid ID out of the URL Path (href), 
            # which in this case is the athleteID.
            native_id = _firstIntInPath(href, marker="/athletes/track/"
                                        if system == "directathletics"
                                        else "/athletes/")
            return native_id, system, name
 
    # A link we don't recognise — keep the name, leave id unknown.
    return None, None, name

# _extractTFTeam
# Purpose: From the team cell, pull the team name plus (when linked) its native
#          id and namespace. TF teams are often clubs/unattached with no link.
# Arguments:
#           cell: the team <td>.
# Output:   (team_name, team_native_id, team_id_system, team_slug).
def _extractTFTeam(cell):

    # Finds the <a> section in the team cell with the url. 
    # This is the anchor tag that contains a hyperlink.
    link = cell.find("a")

    if link is None:
        # Unlinked team (e.g. "Unattached", "Athletics TX") — just the text.
        return _cellText(cell), None, None, None
    
    # Gets href from the url. href is the URL the link points to
    # It also contains the text between the tags that is presented to the user.
    href = link.get("href", "")
    team_name = " ".join(link.get_text().split())

    # directathletics teams: /teams/track/<id>. tfrrs teams: /teams/tf/<slug>
    # (slug, not numeric). We capture the numeric directathletics id when present.
    if "directathletics.com/teams/track/" in href:
        return (team_name, _firstIntInPath(href, marker="/teams/track/"),
                "directathletics", None)

    # A tfrrs team href has no numeric id — the slug IS the id, and it carries
    # state, level, gender and name. Keep it; the saver stores it.
    return team_name, None, "tfrrs", _teamSlugFromHref(href)

# _parseMarkMetres
# Purpose: Parse a field-event mark like "8.28m" into a float of metres.
# Arguments:
#           mark_raw: the raw mark text, e.g. "8.28m" (or "FOUL"/"PASS"/None).
# Output:   the metres as a float (8.28), or None for fouls/passes/blanks.
def _parseMarkMetres(mark_raw):
    if not mark_raw:
        return None
    # Pull the leading number off "8.28m". Non-marks (FOUL/PASS/ND) won't match.
    match = re.search(r"(\d+(?:\.\d+)?)", mark_raw)
    return float(match.group(1)) if match else None

# _extractTFScore
# Purpose: Pull the team-score (SC column) integer from a TF result row. The SC
#          cell is the trailing plain <td> after the result/wind columns. We scan
#          from the END, skipping result cells (compiled_round_ etc.) and the
#          wind <nobr> cell, and take the first plain integer cell we find.
# Arguments:
#           cells: the row's <td> list (same list parseTFRow already has).
# Output:   the score as an int, or None if there's no trailing numeric SC cell
#           (e.g. an exhibition/unscored row leaves it blank).
def _extractTFScore(cells):
    # Walk cells right-to-left: the SC column is at/near the end.
    for cell in reversed(cells):
        classes = cell.get("class", [])
 
        # Skip result cells (time/mark/points columns) — SC is never one of these.
        if _isResultCell(classes):
            # Once we've walked back INTO the result columns, the SC cell (which
            # sits AFTER them) is already behind us — stop, there's no score.
            return None
 
        # Skip the wind cell: wind lives in a <nobr> wrapper, score never does.
        if cell.find("nobr") is not None:
            continue
 
        text = _cellText(cell)
        if text == "":
            continue                      # blank trailing cell (unscored) — skip
 
        # A score is a plain integer. _toIntOrNone returns None for non-numerics
        # (e.g. a stray text cell), in which case we keep scanning leftward.
        value = _toIntOrNone(text)
        if value is not None:
            return value
 
    return None
 
