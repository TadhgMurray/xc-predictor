# Project: xc-predictor
# File:    tfrrs/parser/parse_tf_team.py
# Purpose: Parse a TFRRS TF meet's TEAM SCORES into team-score dicts. TF team
#          scoring is structurally different from XC's, so it gets its own parser:
#
#            - It lives on the meet LANDING page, the same page that lists the
#              events - NOT on the per-gender compiled page.
#            - It is MEET-WIDE: one standings table per gender PER SECTION,
#              aggregated across every event, so there is NO per-event id
#              (event_id stays None). Multi-section meets (e.g. middle school 7th
#              vs 8th grade, or JV vs Varsity) render MULTIPLE team tables per
#              gender, with ids team_scores_m_1 / team_scores_m_2 etc. and a
#              <p> label naming the section. We KEEP these distinct (they are
#              different competitions, NOT duplicates).
#            - The table is a clean 3 columns: RANK, TEAM, SCORE.
#            - Tables carry ids team_scores_<gender>[_<section>].
#
#          Output mirrors parse_xc_page's team dict field names so both sports'
#          team scores are interchangeable into meet_extras.teams_json.

import re
from bs4 import BeautifulSoup
from parse_xc import _cellText, _toIntOrNone, _extractTeam

# Team-scores tables are identified by id "team_scores_<gender>" with an OPTIONAL
# "_<section>" suffix. The gender letter (m/f) is group 1; the section digits
# (1/2/...) are group 2 (None when unsuffixed = a single-section meet).
# The section suffix is a real DIVISION marker (7th/8th grade, JV/Varsity) — the
# tables are distinct competitions, NOT duplicates, so we keep them apart.
_TEAM_SCORES_ID = re.compile(r"^team_scores_([mf])(?:_(\d+))?$")

# Fixed columns in a TF team row: RANK, TEAM, SCORE.
_TF_TEAM_COLS = 3


# parseTFTeams
# Purpose: Parse a TFRRS TF meet landing page's TEAM-SCORES tables into a flat
#          list of team-score dicts, one per team per gender per section.
# Arguments:
#           html:    the meet landing-page HTML.
#           meet_id: stamped onto every team row.
# Output:   a list of team-score dicts (see _parseTFTeamRow), each carrying
#           gender, section, and meet_id. event_id is always None (meet-wide).
def parseTFTeams(html, meet_id):
    soup = BeautifulSoup(html, "lxml")

    teams = []
    # find_all matches the id attribute against the compiled pattern, so this
    # picks up team_scores_m, team_scores_m_1, team_scores_f_2, etc.
    for table in soup.find_all("table", id=_TEAM_SCORES_ID):
        gender, suffix = _genderAndSectionFromId(table.get("id", ""))
        # Prefer the real division label ("7th grade") from the <p> above the
        # table; fall back to the id suffix so sections stay distinguishable
        # even if a meet omits the label.
        label = _sectionLabelForTable(table)
        section = label or suffix
        teams.extend(_parseTFTeamTable(table, meet_id, gender, section))
    # NO dedup — the _1/_2 tables are distinct divisions, not duplicates.
    return teams


# _genderAndSectionFromId
# Purpose: Read gender AND the section suffix from a team-scores table id.
#          "team_scores_m" -> ("M", None); "team_scores_m_2" -> ("M", "2").
# Arguments:
#           table_id: the table's id attribute string.
# Output:   (gender, section) — gender "M"/"F"/None, section the suffix digits
#           as a string, or None if unsuffixed / unrecognised.
def _genderAndSectionFromId(table_id):
    match = _TEAM_SCORES_ID.match(table_id)
    if match is None:
        return None, None
    gender = match.group(1).upper()     # 'm' -> 'M', 'f' -> 'F'
    section = match.group(2)            # the "_<n>" digits, or None
    return gender, section


# _sectionLabelForTable
# Purpose: Read the division label that sits just above a team-scores table — a
#          <p><b>7th grade</b></p> between the <h3> and the <table> at
#          multi-section meets. Returns None when absent (single-section meets).
# Arguments:
#           table: the team-scores <table> Tag.
# Output:   the label string (e.g. "7th grade"), or None.
def _sectionLabelForTable(table):
    # The nearest <p> ABOVE the table is its division label. find_previous walks
    # backwards in document order; the first <p> we hit is this table's label.
    p = table.find_previous("p")
    if p is None:
        return None
    text = " ".join(p.get_text().split())   # collapse whitespace/newlines
    return text or None


# _parseTFTeamTable
# Purpose: Parse one team-scores table into team-score dicts, attaching meet_id,
#          gender, section, and a None event_id (meet-wide) to each.
# Arguments:
#           table:   a team-scores <table> Tag.
#           meet_id: carried onto each row.
#           gender:  this table's gender ("M"/"F"/None).
#           section: this table's division marker (label or suffix), or None.
# Output:   a list of team-score dicts.
def _parseTFTeamTable(table, meet_id, gender, section):
    body = table.find("tbody")
    if body is None:
        return []
    out = []
    for tr in body.find_all("tr", recursive=False):
        row = _parseTFTeamRow(tr)
        # Attach context to every row (parsed or not) so nothing floats free.
        row.update({
            "meet_id":  meet_id,
            "event_id": None,      # TF team scores are meet-wide, not per-event
            "gender":   gender,
            "section":  section,   # division marker: "7th grade"/"2"/None
        })
        out.append(row)
    return out


# _parseTFTeamRow
# Purpose: Parse ONE TF team-scores <tr> into a structured dict. Never raises; a
#          malformed/empty row returns ok=False so one bad row can't kill a meet.
# Arguments:
#           tr: a <tr> from a team_scores_* <tbody>.
# Output:   a dict with ok=True, or {"ok": False, "note": ...}.
def _parseTFTeamRow(tr):
    cells = tr.find_all("td")

    # An empty trailing <tr> or a spacer has too few cells - skip it safely.
    if len(cells) < _TF_TEAM_COLS:
        return {"ok": False, "note": f"only {len(cells)} cells (not a team row)"}

    # [0] rank; [1] team (link -> slug/name); [2] score.
    place = _toIntOrNone(_cellText(cells[0]))
    team_slug, team_name, _slug_gender = _extractTeam(cells[1])
    score = _toIntOrNone(_cellText(cells[2]))

    return {
        "ok":        True,
        "note":      None,
        "place":     place,
        "team_name": team_name,
        "team_slug": team_slug,
        "score":     score,
    }