"""
tf_points.py -- computed team scores for a TF meet.

PURE: takes rows, returns structure. app.py owns the one query that feeds
it (get_tf_meet_scoring_rows), so this whole module tests without a
database -- the compare/teams convention.

★ NOT THE MEET'S OFFICIAL SCORE, BY DESIGN. No meet publishes its scoring
  rules into this data, so one honest computed score beats a thousand
  guessed official ones: the standard 8-place table (10-8-6-5-4-3-2-1)
  for every event, relays once per school, field events by mark, multis
  by points, ties split. The page says so.

★ ROUNDS. The raw event names carry the truth the distance parser strips:
  "100 Meters Prelims", "Boys 3200 Finals", "(Section 2)". Where a
  canonical event has rows marked FINAL, only those score; otherwise the
  whole event ranks as one field by each athlete's best mark -- which is
  also exactly right for sectioned timed finals.

★ EVENT IDENTITY. Prelims and finals arrive as different event_ids with
  different names; scoring needs them to be ONE event. The merge key is
  the name with the gender prefix and the round/section noise stripped --
  but NOT the level words (Varsity, JV, Frosh): those scope who was
  racing whom, and merging a varsity 100 with the JV 100 would score a
  meet that never happened. The division column joins the key for the
  same reason.
"""

import re

TABLE = (10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0)

_FINAL = re.compile(r"\bfinals?\b", re.IGNORECASE)
_PRELIM = re.compile(
    r"\b(prelims?|preliminar\w*|semis?|semifinals?|quarterfinals?|"
    r"trials?|heats?)\b", re.IGNORECASE)

# Mirrors event_parse's gender prefix, locally: this module must not pull
# the engine tree into the web app.
_GENDER = re.compile(
    r"^\s*(men|mens|men's|boys|boy's|male|"
    r"women|womens|women's|girls|girl's|female)\b[\s'’]*",
    re.IGNORECASE)
_MALE = frozenset({"men", "mens", "men's", "boys", "boy's", "male"})

# Round/section noise for the MERGE KEY. Deliberately narrower than
# event_parse._TRAILING_NOISE: no varsity/jv/frosh (level words scope the
# competition) and no invite/championship (part of some events' names).
_NOISE = re.compile(
    r"\s*[\(\[].*?[\)\]]|"
    r"\s*\b(section|sect|heat|flight|round|prelims?|preliminar\w*|"
    r"semis?|semifinals?|quarterfinals?|finals?|trials?|"
    r"unseeded|seeded|fast|slow|\#)\b.*$",
    re.IGNORECASE)


def roundOf(event_short):
    """'final' | 'prelim' | None, read from the RAW name."""
    s = event_short or ""
    if _FINAL.search(s):
        return "final"
    if _PRELIM.search(s):
        return "prelim"
    return None


def genderOf(event_short):
    """'M' | 'F' | None from the event name's gender prefix."""
    m = _GENDER.match(event_short or "")
    if not m:
        return None
    return "M" if m.group(1).lower() in _MALE else "F"


def canonicalEvent(event_short):
    """The merge key: gender prefix off, round noise off, case folded."""
    s = _GENDER.sub("", event_short or "")
    s = _NOISE.sub("", s)
    return " ".join(s.split()).lower()


def displayEvent(event_short):
    """The reader's name for an event: same strip, original case kept."""
    s = _GENDER.sub("", event_short or "")
    s = _NOISE.sub("", s)
    return " ".join(s.split()) or (event_short or "Event")


def _value(row):
    """The rankable number, or None. Running/relay: seconds ascending.
    Field and multis: mark descending -- a mark that will not parse as a
    number cannot rank and the row shows without scoring."""
    if row.get("is_field") or (row.get("result_kind") in ("field", "combined")):
        try:
            v = float(row.get("mark"))
        except (TypeError, ValueError):
            return None
        return -v                     # negate: one ascending sort ranks both
    t = row.get("time_seconds")
    return float(t) if t and float(t) > 0 else None


def fmtPoints(p):
    """10 -> '10', 5.5 -> '5.5'."""
    return f"{p:g}"


def _entries(rows, is_relay):
    """Scoring entries: best row per athlete (or per school for a relay).
    Returns [(value, key, best_row)] for rankable rows only."""
    best = {}
    for r in rows:
        v = _value(r)
        if v is None:
            continue
        if is_relay:
            key = ("school", (r.get("school") or "").strip().lower())
            if not key[1]:
                continue              # a relay with no school cannot score
        else:
            key = ("person", r.get("person_id") or
                   ((r.get("athlete_name") or "").strip().lower(),
                    (r.get("school") or "").strip().lower()))
        if key not in best or v < best[key][0]:
            best[key] = (v, r)
    return sorted(((v, k, r) for k, (v, r) in best.items()),
                  key=lambda e: e[0])


def _award(entries):
    """[(place_label, points, row)] with standard split-tie handling: tied
    entries share the mean of the points their places cover, and wear a
    T-prefixed place."""
    out = []
    i = 0
    while i < len(entries):
        j = i
        while j + 1 < len(entries) and entries[j + 1][0] == entries[i][0]:
            j += 1
        span = range(i, j + 1)
        pts = [TABLE[p] if p < len(TABLE) else 0.0 for p in span]
        share = sum(pts) / len(pts)
        label = f"T{i + 1}" if j > i else str(i + 1)
        for p in span:
            out.append((label, share, entries[p][2]))
        i = j + 1
    return out


def scoreMeet(rows):
    """All of one meet's scoring in one pass.

    rows: dicts carrying result_id, event_id, event_short, division,
    distance_meters, school, athlete_name, person_id, gender,
    time_seconds, mark, result_kind, is_field, is_relay, speed_rating,
    grade. Output:

      {"divisions": [{"name", "teams": {"M": [...], "F": [...]},
                      "events": [{"name", "gender", "is_relay",
                                  "is_field", "distance", "rows"}]}],
       "points_by_result": {result_id: "10"},
       "n_scored_events": int}
    """
    # ---- group into canonical events ---------------------------------- #
    groups = {}
    for r in rows:
        if not (r.get("school") or "").strip():
            continue                  # unattached rows cannot move a team
        div = (r.get("division") or "").strip()
        canon = canonicalEvent(r.get("event_short"))
        g = genderOf(r.get("event_short")) or r.get("gender") or "?"
        key = (div.lower(), canon, g)
        grp = groups.setdefault(key, {"division": div, "gender": g,
                                      "canon": canon, "rows": []})
        grp["rows"].append(r)

    divisions = {}
    points_by_result = {}
    n_scored = 0

    for key in sorted(groups):
        grp = groups[key]
        rows_g = grp["rows"]

        # rounds: finals beat everything else when any exist
        finals = [r for r in rows_g
                  if roundOf(r.get("event_short")) == "final"]
        scoring_rows = finals or rows_g

        is_relay = any(r.get("is_relay") for r in scoring_rows)
        is_field = any(_value(r) is not None and
                       (r.get("is_field") or
                        r.get("result_kind") in ("field", "combined"))
                       for r in scoring_rows)

        entries = _entries(scoring_rows, is_relay)
        awarded = _award(entries)
        n_scored += 1 if awarded else 0

        by_rid = {}
        for label, pts, row in awarded:
            if pts > 0:
                points_by_result[row["result_id"]] = fmtPoints(pts)
            by_rid[row["result_id"]] = (label, pts)

        # ---- the event's display rows: ranked scorers, then the rest -- #
        ev_rows = []
        seen = set()
        for label, pts, row in awarded:
            ev_rows.append({**row, "place_label": label,
                            "points": fmtPoints(pts) if pts > 0 else ""})
            seen.add(row["result_id"])
        for r in sorted(rows_g, key=lambda x: (_value(x) is None,
                                               _value(x) or 0)):
            if r["result_id"] not in seen:
                ev_rows.append({**r, "place_label": "", "points": ""})

        name_src = finals[0] if finals else rows_g[0]
        dist = name_src.get("distance_meters")
        div = divisions.setdefault(grp["division"].lower(), {
            "name": grp["division"] or "All divisions",
            "teams": {}, "events": []})
        div["events"].append({
            "name": displayEvent(name_src.get("event_short")),
            "gender": grp["gender"], "is_relay": is_relay,
            "is_field": is_field, "distance": dist, "rows": ev_rows,
            "scored_finals": bool(finals)})

        # ---- team sums ------------------------------------------------ #
        if grp["gender"] in ("M", "F"):
            teams = div["teams"].setdefault(grp["gender"], {})
            for label, pts, row in awarded:
                school = (row.get("school") or "").strip()
                if not school or pts <= 0:
                    continue
                cell = teams.setdefault(school.lower(),
                                        {"school": school, "points": 0.0,
                                         "wins": 0})
                cell["points"] += pts
                if label in ("1", "T1"):
                    cell["wins"] += 1

    out_divs = []
    for dkey in sorted(divisions):
        div = divisions[dkey]
        for g in ("M", "F"):
            teams = sorted(div["teams"].get(g, {}).values(),
                           key=lambda t: (-t["points"], t["school"]))
            # standings places, tie-aware: two schools on the same total
            # share a T-place, same convention as the event rows
            i = 0
            while i < len(teams):
                j = i
                while (j + 1 < len(teams) and
                       teams[j + 1]["points"] == teams[i]["points"]):
                    j += 1
                label = f"T{i + 1}" if j > i else str(i + 1)
                for p in range(i, j + 1):
                    teams[p]["place_label"] = label
                i = j + 1
            for t in teams:
                t["display"] = fmtPoints(t["points"])
            div["teams"][g] = teams
        # events in a stable reading order: running by distance, then
        # field/multis, relays last, name as the tiebreak
        div["events"].sort(key=lambda e: (
            bool(e["is_relay"]), bool(e["is_field"]),
            float(e["distance"] or 0), e["name"], e["gender"]))
        out_divs.append(div)

    return {"divisions": out_divs, "points_by_result": points_by_result,
            "n_scored_events": n_scored}
