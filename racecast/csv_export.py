"""csv_export.py -- "Download CSV" for the tables coaches copy by hand
(owner, 2026-10-05: CSV export on rankings, school PRs, meet results).

    ?format=csv on the SAME URL the table came from: /api/rankings (the
    board the rankings page shows), /school/<name>/prs, /race/xc/...,
    /race/tf/... The route builds its rows exactly as for the page, then
    hands them here instead of to the template.

★ THE TABLE AS SHOWN, NOT MORE. Same filters, same page of a board (its
  limit and offset), same HS-equivalent stamp. A CSV is a convenience for
  what a reader can already see; it is not a bulk feed, and it adds no
  row the page would not draw.

★ A LINK BACK IN EVERY ROW. The athlete's Racecast URL, so a spreadsheet
  pasted into a team doc still leads to the page.

! A SPREADSHEET OPENS IT. UTF-8 with a BOM (Excel otherwise reads
  "Zoë" as mojibake), and a cell that starts with = + - @ is prefixed
  with ' so a school named "=HYPERLINK(...)" is text, not a formula.
"""
import csv
import io
import re

from flask import Response


def fmtTime(sec):
    """m:ss.ss (h:mm:ss.s over an hour); None for no time or the no-time
    sentinel the result tables use."""
    if sec is None:
        return None
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return None
    if s <= 0 or s >= 999999:
        return None
    h, rem = divmod(s, 3600)
    m, rem = divmod(rem, 60)
    if h:
        return f"{int(h)}:{int(m):02d}:{rem:04.1f}"
    return f"{int(m)}:{rem:05.2f}"


def _num(v, nd=1):
    if v is None or v == "":
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _safe(v):
    if v is None:
        return ""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"[-+]?\d[\d.:]*", s) else s


def athleteUrl(origin, pid):
    return f"{origin}/athlete/{pid}" if pid else None


def toCsv(rows, columns):
    """columns: [(header, fn(row) -> value)]. A column every row leaves
    empty is dropped, so a board with no HS-equivalent stamp has no empty
    HS column."""
    cells = [[fn(r) for _h, fn in columns] for r in rows]
    keep = [i for i in range(len(columns))
            if any(c[i] not in (None, "") for c in cells)]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([columns[i][0] for i in keep])
    for c in cells:
        w.writerow([_safe(c[i]) for i in keep])
    return "﻿" + buf.getvalue()


def csvResponse(text, filename):
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-") or "racecast.csv"
    resp = Response(text, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def wantsCsv(args):
    return (args.get("format") or "").lower() == "csv"


# ---- the tables ---------------------------------------------------------- #

def rankingColumns(board, origin, offset=0):
    """/api/rankings rows (rankings.getAbility/Performance/PrRankings)."""
    rank = {"i": offset}

    def _rank(_r):
        rank["i"] += 1
        return rank["i"]
    head = [("Rank", _rank),
            ("Name", lambda r: r.get("name")),
            ("School", lambda r: r.get("school")),
            ("School state", lambda r: r.get("school_state")),
            ("Grade", lambda r: r.get("grade")),
            ("Season", lambda r: r.get("year"))]
    if board == "ability":
        body = [("Rating", lambda r: _num(r.get("rating"))),
                ("Rating (HS equivalent)", lambda r: _num(r.get("hs_rating"))),
                ("Best rating", lambda r: _num(r.get("best_rating"))),
                ("Races", lambda r: r.get("n_races")),
                ("First race", lambda r: r.get("first_race")),
                ("Last race", lambda r: r.get("last_race"))]
    else:
        body = [("Time", lambda r: r.get("mark") or fmtTime(r.get("time_seconds"))),
                ("Distance (m)", lambda r: _num(r.get("distance"), 0)),
                ("Date", lambda r: r.get("race_date")),
                ("Race state", lambda r: r.get("state")),
                ("Rating", lambda r: _num(r.get("rating"))),
                ("Rating (HS equivalent)", lambda r: _num(r.get("hs_rating")))]
    return head + body + [("Athlete URL", lambda r: athleteUrl(origin, r.get("person_id")))]


def raceXcColumns(origin):
    """race_xc's results, in finish order."""
    place = {"i": 0}

    def _place(_r):
        place["i"] += 1
        return place["i"]
    return [("Place", _place),
            ("Name", lambda r: r.get("name") or "Unknown"),
            ("Grade", lambda r: r.get("grade")),
            ("School", lambda r: r.get("school")),
            ("School state", lambda r: r.get("school_state")),
            ("Time", lambda r: r.get("display_time") or fmtTime(r.get("time_seconds"))),
            ("Rating", lambda r: _num(r.get("speed_rating"))),
            ("Rating (HS equivalent)", lambda r: _num(r.get("hs_rating"))),
            ("Vs season level (%)", lambda r: _num(r.get("vs_level"))),
            ("Team score place", lambda r: r.get("score_place")),
            ("Athlete URL", lambda r: athleteUrl(origin, r.get("person_id")))]


def raceTfColumns(origin):
    """race_tf's sections, flattened: one row per result with its heat."""
    def _name(r):
        if r.get("is_relay"):
            legs = [leg.get("name") for leg in (r.get("relay_legs") or []) if leg.get("name")]
            return "; ".join(legs) or r.get("athlete_name") or "Relay"
        return r.get("athlete_name") or "Unknown"
    return [("Section", lambda r: r.get("_section")),
            ("Place", lambda r: r.get("sec_place")),
            ("Name", _name),
            ("Grade", lambda r: r.get("grade")),
            ("School", lambda r: r.get("school")),
            ("Mark", lambda r: r.get("display_result")),
            ("Wind", lambda r: r.get("wind")),
            ("Rating", lambda r: _num(r.get("speed_rating"))),
            ("Rating (HS equivalent)", lambda r: _num(r.get("hs_rating"))),
            ("Vs season level (%)", lambda r: _num(r.get("vs_level"))),
            ("Points", lambda r: r.get("points") or None),
            ("Athlete URL", lambda r: None if r.get("is_relay")
             else athleteUrl(origin, r.get("person_id")))]


def flattenTfSections(sections):
    out = []
    for sec in sections or []:
        for r in sec.get("rows") or []:
            out.append({**r, "_section": sec.get("label")})
    return out


def schoolPrColumns(origin):
    """school_prs sections x genders, flattened by flattenSchoolPrs."""
    return [("Event", lambda r: r.get("_event")),
            ("Gender", lambda r: r.get("_gender")),
            ("Rank", lambda r: r.get("_rank")),
            ("Name", lambda r: r.get("name")),
            ("Grade", lambda r: r.get("grade")),
            ("Mark", lambda r: r.get("display_mark")),
            ("Rating", lambda r: _num(r.get("speed_rating"))),
            ("Rating (HS equivalent)", lambda r: _num(r.get("hs_speed_rating"))),
            ("Date", lambda r: r.get("race_date") or r.get("date")),
            ("Course", lambda r: r.get("course_name")),
            ("Athlete URL", lambda r: athleteUrl(origin, r.get("person_id")))]


def flattenSchoolPrs(sections):
    out = []
    for sec in sections or []:
        for g, word in (("M", "Boys/Men"), ("F", "Girls/Women")):
            for i, r in enumerate((sec.get("tables") or {}).get(g) or [], 1):
                out.append({**r, "_event": sec.get("label"), "_gender": word, "_rank": i})
    return out
