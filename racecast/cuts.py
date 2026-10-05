# Project: xc-predictor / racecast
# File:    cuts.py
# Purpose: "What it takes" (owner, 2026-10-05, approved): for each state,
#          division and gender, the season rating it took in past seasons to
#          get out of the section, to make the state meet, to finish top 25
#          there and to finish top 8 -- each mark also as a time on a typical
#          course and on the state meet's own course. And one line on an HS
#          athlete's page: their season rating against last season's mark.
#
#              /what-it-takes                     pick a state
#              /what-it-takes/<st>                the state's divisions
#              /what-it-takes/<st>/<div>[?g=girls]   the marks, section to state
#
# ★ THE MARKS ARE READ OFF WHO WAS ACTUALLY THERE, NOT OFF A RULE BOOK.
#   Everyone who ran a state championship race IS a state qualifier, however
#   their state qualifies people (places, teams, at-large picks). So the
#   qualifying mark is a fact about that race's finishers: their SEASON
#   ratings (athlete_season.mean_rating, the number the boards publish and an
#   athlete compares themself against), summarised by a percentile. No cut
#   number is typed in anywhere; a state that changed how many it takes moves
#   its own mark next season.
#
# ★ THE TENTH PERCENTILE, NOT THE SLOWEST QUALIFIER. The slowest qualifier is
#   one person -- the runner who had a bad season and rode a team in, the
#   mis-linked result, the at-large pick. "Nine in ten qualifiers had at least
#   this" is the sentence a reader can plan around, and one odd runner cannot
#   move it far. The slowest and the median are shown beside it, named.
#
# ★ THE SECTION IS PART OF THE PATH (owner, 2026-10-05: "qualifying depends
#   on SECTIONS too"). California runs league -> CIF section -> state, other
#   states regionals; the round before state sets its own mark. A section
#   (or regional) championship race's ADVANCERS are its finishers who ran
#   that season's state meet -- the same "who was actually there" rule one
#   round down. Where no section round is on record, the page says so and
#   shows the state meet alone.
#
# ⚠ THE DATA'S ASSIGNMENTS ARE NOT TRUSTED BLINDLY (owner, 2026-10-05: some
#   schools' section/state assignments are wrong right now). Which division a
#   race IS comes first from the race's own title ("Boys Division 2"), and
#   only when the title says nothing from its finishers' schools -- and then
#   only by a majority. Every cell carries its sample size, and a cell whose
#   evidence is thin (see THIN_N), whose finishers mostly carry no rating, or
#   whose schools mostly disagree with the race it ran, is flagged on the page
#   rather than dropped -- a bad cell should LOOK thin, not authoritative.
#
# ! anet AND tfrrs MEET IDS COLLIDE. Every join from meet_unit to meets to
#   results is on (meet_id, source), never meet_id alone.
import re
import threading

import ttlcache
import projections as P

GENDERS = P.GENDERS                 # boys -> (hs_m, "Boys"), girls -> ...
_POOL_GENDER = {pool: g for g, (pool, _w) in GENDERS.items()}

# ★ THE MARK IS THIS QUANTILE OF THE GROUP'S SEASON RATINGS (see the header).
MARK_Q = 0.10
# ★ THIN = FEWER RUNNERS THAN THE MARK NEEDS TO BE A PERCENTILE AT ALL.
#   Below 1/MARK_Q = 10 rated runners the tenth percentile sits at or below
#   the second-slowest runner: it IS the slowest one or two people, and the
#   robustness the percentile was chosen for is gone. Derived from MARK_Q,
#   so changing the quantile moves the threshold with it.
THIN_N = int(round(1.0 / MARK_Q))

# The place lines. Not rating cuts -- places, which is what a reader means by
# "top 25" or "the podium" -- and the page names them as places. 25 is the
# all-state line in many states and 8 the medal stand in many; states differ,
# and the page says that rather than pretending these are each state's rule.
PLACE_LINES = (("top", 25, "Top 25"), ("podium", 8, "Top 8"))

# Seasons shown, newest first; the trend is the slope over these.
SEASONS_SHOWN = 3
# How far back the query reads to FIND those seasons: a state whose meet is
# missing from one season still shows three. A fetch window, not a cut.
_FETCH_SEASONS = SEASONS_SHOWN + 3

# ★ A MAJORITY, AND NOT A TUNED SHARE. "Most finishers' schools say D2" is the
#   weakest claim that names one division; anything less is a plurality of a
#   mixed field, and the race is left unplaced rather than guessed.
_MAJORITY = 0.5

# The kinds of round before state whose advancers are measured. A district
# (TX: district -> region -> state) is two rounds out, so its finishers who
# later ran state did not advance FROM it to state; it is left out.
_ROUND_KINDS = ("section", "region")
# A meet that also names one of these is a smaller unit's meet that happens
# to carry a section or state word (an NCS area meet, a league final).
_SUBUNIT_KINDS = ("area", "league", "district", "county", "conference")
_QUALIFIER_RX = re.compile(r"qualif", re.I)

_TTL = 6 * 3600.0


# ------------------------------------------------------------------ #
#  small pure pieces
# ------------------------------------------------------------------ #

def quantile(values, q):
    """The q-quantile of `values`, linear between order statistics (the
    usual 'type 7' -- numpy's default). None for no values."""
    xs = sorted(float(v) for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def markOf(values):
    """{mark, slowest, median, n} for a group's season ratings."""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return None
    return {"mark": round(quantile(xs, MARK_Q), 1),
            "slowest": round(min(xs), 1),
            "median": round(quantile(xs, 0.5), 1),
            "n": len(xs)}


def trendOf(points):
    """Least-squares slope, rating points per season, over [(year, mark)].
    None with fewer than two seasons -- one point has no direction."""
    pts = [(float(y), float(m)) for y, m in points if m is not None]
    if len(pts) < 2:
        return None
    n = len(pts)
    my = sum(p[0] for p in pts) / n
    mm = sum(p[1] for p in pts) / n
    den = sum((p[0] - my) ** 2 for p in pts)
    if not den:
        return None
    return round(sum((p[0] - my) * (p[1] - mm) for p in pts) / den, 1)


def majority(values):
    """(value, share, n_known) for the most common non-empty value, where
    share is over ALL entries -- an unknown is a vote for nothing, not an
    abstention, so a field of mostly unknown schools names no division."""
    n = len(values)
    counts = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None, 0.0, 0
    v, c = max(counts.items(), key=lambda kv: (kv[1], str(kv[0])))
    return v, (c / n if n else 0.0), sum(counts.values())


def _slug(v):
    return P.divisionSlug(v) if v else None


def meetLevel(meet_name, facts):
    """('state', None) for a state final, (kind, unit) for a section or
    regional final, None for anything else.

    facts: [(kind, unit)] as meet_unit stores them for the meet."""
    kinds = {k for k, _u in facts}
    if kinds & set(_SUBUNIT_KINDS):
        return None
    for kind in _ROUND_KINDS:
        units = sorted({u for k, u in facts if k == kind and u})
        if units:
            # ! TWO DIFFERENT SECTIONS NAMED ON ONE MEET is a joint or a
            #   mis-parse; the finishers decide (see raceUnit), so the unit
            #   is left open here rather than picking one name.
            return (kind, units[0] if len(units) == 1 else None)
    if "state" in kinds and not _QUALIFIER_RX.search(meet_name or ""):
        return ("state", None)
    return None


def titleDivision(title_facts, level_kind):
    """The division token the race's OWN title names, or None.

    At a state final that is the state division / class; at a section
    final, the section's division (parseUnits files the token under the
    unit that carried it); at a regional, the class it names."""
    want = (("state_div", "class") if level_kind == "state" else
            ("section_div", "class", "state_div"))
    for kind in want:
        for k, u in title_facts:
            if k == kind and u:
                return str(u).strip().upper()
    return None


# ------------------------------------------------------------------ #
#  the core: races + finishers + season ratings -> marks (no database)
# ------------------------------------------------------------------ #

def _raceGender(rows):
    g, share, _n = majority([_POOL_GENDER.get(r["pool"]) for r in rows])
    return g if share > _MAJORITY else None


def _placeRace(race, rows):
    """Resolve one race: gender, level, unit, division. Mutates `race`."""
    race["gender"] = _raceGender(rows)
    kind, unit = race["level"]
    race["kind"] = kind
    title = race.get("title_div")
    if kind == "state":
        own = [(r.get("state_div") or r.get("class") or "").upper() or None
               for r in rows]
    elif kind == "section":
        own = [(r.get("section_div") or "").upper() or None for r in rows]
    else:
        own = [(r.get("state_div") or r.get("class") or "").upper() or None
               for r in rows]
    if title:
        div = title
        race["div_from"] = "title"
    else:
        v, share, n_known = majority(own)
        if n_known == 0:
            # nobody's school names a division: a one-race state (or round)
            div = "ALL"
            race["div_from"] = "none"
        elif share > _MAJORITY:
            div = v
            race["div_from"] = "schools"
        else:
            div = None
            race["div_from"] = "mixed"
    race["div"] = div
    # how many finishers' own schools AGREE with the division the race is
    # filed under -- the visible symptom of a wrong school assignment
    known = [o for o in own if o]
    race["agree"] = (sum(1 for o in known if _slug(o) == _slug(div)) / len(known)
                     if known and div and div != "ALL" else None)
    if kind != "state" and unit is None:
        u, share, _n = majority([(r.get(kind) or "").upper() or None
                                 for r in rows])
        unit = u if share > _MAJORITY else None
    race["unit"] = unit
    return race


def _dedupe(rows):
    """One row per person per race, the best place kept (a duplicated feed
    row must not count a runner twice)."""
    best = {}
    for r in rows:
        k = r["person_id"]
        if k not in best or (r.get("place") or 1e9) < (best[k].get("place") or 1e9):
            best[k] = r
    return sorted(best.values(), key=lambda r: (r.get("place") or 1e9))


def computeMarks(state, races, rows, ratings):
    """Everything the page shows for one state, both genders.

    races:   [{meet_id, source, div_id, meet_name, title, title_div, level,
               course_name, distance, meet_date}] -- level from meetLevel
    rows:    [{meet_id, source, div_id, person_id, place, time_seconds, pool,
               year, race_rating, school, state_div, class, section,
               section_div}] -- every rated finisher
    ratings: {(person_id, pool, year): season mean_rating}

    Pure: the tests drive it with hand-built rows."""
    by_race = {}
    for r in rows:
        by_race.setdefault((r["meet_id"], r.get("source"), r["div_id"]),
                           []).append(r)
    placed, unplaced = [], 0
    for race in races:
        key = (race["meet_id"], race.get("source"), race["div_id"])
        rs = _dedupe(by_race.get(key, []))
        if not rs:
            continue
        _placeRace(race, rs)
        years = [r["year"] for r in rs if r.get("year") is not None]
        race["year"] = max(set(years), key=years.count) if years else None
        race["rows"] = rs
        race["n_schools"] = len({r.get("school") for r in rs if r.get("school")})
        if (race["gender"] is None or race["div"] is None
                or race["year"] is None
                or (race["kind"] != "state" and not race["unit"])):
            unplaced += 1
            continue
        placed.append(race)

    out = {"state": state, "unplaced": unplaced, "genders": {}}
    for gender, (pool, words) in GENDERS.items():
        st_races = [r for r in placed if r["kind"] == "state"
                    and r["gender"] == gender]
        # ★ ONE RACE PER (season, division): the biggest. Two races landing
        #   on one cell is a duplicate feed or a mis-filed race, and merging
        #   them would mix two fields' marks into one.
        cells = {}
        for r in st_races:
            k = (r["year"], _slug(r["div"]))
            cells.setdefault(k, []).append(r)
        state_cells, ran_state = {}, {}
        for (year, slug), rs in cells.items():
            rs.sort(key=lambda r: -len(r["rows"]))
            race, extra = rs[0], len(rs) - 1
            for r in rs:                        # everyone who ran state counts
                for x in r["rows"]:
                    ran_state.setdefault(year, {})[x["person_id"]] = slug
            state_cells.setdefault(slug, {"slug": slug, "value": race["div"],
                                          "seasons": []})
            state_cells[slug]["seasons"].append(
                _stateSeason(race, extra, pool, ratings))
        divisions = []
        for slug, cell in state_cells.items():
            _finishCell(cell, ("qualify",) + tuple(k for k, _n, _l in PLACE_LINES))
            cell["label"] = (P.divisionLabel(state, cell["value"], "state_div")
                             if slug != "all" else f"{state} (one race)")
            cell["short"] = (cell["label"].replace(state + " ", "")
                             if slug != "all" else "All")
            divisions.append(cell)
        divisions.sort(key=lambda c: P.divisionSortKey(c["value"]))

        sections = _sectionCells(state, placed, gender, pool, ratings,
                                 ran_state)
        labels = {c["slug"]: c["label"] for c in divisions}
        for sc in sections:
            sc["feeds_label"] = labels.get(sc.get("feeds"))
        out["genders"][gender] = {"pool": pool, "words": words,
                                  "divisions": divisions,
                                  "sections": sections,
                                  "distance": _modeDistance(st_races)}
    yrs = sorted({s["year"] for g in out["genders"].values()
                  for c in g["divisions"] for s in c["seasons"]}, reverse=True)
    out["years"] = yrs
    return out


def _modeDistance(races):
    counts = {}
    for r in races:
        d = r.get("distance")
        if d and 1000 <= float(d) <= 12000:
            k = int(round(float(d)))
            counts[k] = counts.get(k, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0] \
        if counts else None


def _seasonRating(r, pool, ratings):
    return ratings.get((r["person_id"], pool, r["year"]))


def _stateSeason(race, extra, pool, ratings):
    rows = race["rows"]
    rated = [(r, _seasonRating(r, pool, ratings)) for r in rows]
    have = [v for _r, v in rated if v is not None]
    season = {"year": race["year"], "n_finishers": len(rows),
              "n_rated": len(have), "n_schools": race["n_schools"],
              "extra_races": extra, "agree": race["agree"],
              "div_from": race["div_from"],
              "meet_id": race["meet_id"], "div_id": race["div_id"],
              "source": race.get("source"), "meet_name": race.get("meet_name"),
              "course_name": race.get("course_name"),
              "distance": race.get("distance"),
              "lines": {"qualify": markOf(have)}}
    for key, n, _label in PLACE_LINES:
        # ! A PLACE LINE THAT IS THE WHOLE FIELD SAYS NOTHING: in a 20-runner
        #   race "top 25" is everyone, which is the qualifying line again.
        if len(rows) <= n:
            season["lines"][key] = None
            continue
        grp = [v for r, v in rated if r.get("place") and r["place"] <= n
               and v is not None]
        line = markOf(grp)
        if line:
            at = [r for r in rows if r.get("place") and r["place"] <= n]
            last = at[-1] if at else None
            # ★ ON THE DAY: the Nth finisher's own clock and race rating. A
            #   real time on the real course, beside the season-rating mark.
            line["on_day"] = ({"place": last["place"],
                               "time": last.get("time_seconds"),
                               "rating": last.get("race_rating")}
                              if last else None)
        season["lines"][key] = line
    season["flags"] = _flags(season["n_rated"], len(rows), race["agree"],
                             race["div_from"])
    return season


def _flags(n_rated, n_rows, agree, div_from, who="finishers"):
    """The reasons a season's mark should be read as thin, in words."""
    out = []
    if n_rated < THIN_N:
        out.append(f"only {n_rated} rated runner{'s' if n_rated != 1 else ''}")
    if n_rows and n_rated / n_rows <= _MAJORITY:
        out.append(f"{n_rows - n_rated} of {n_rows} {who} have no season rating")
    if agree is not None and agree <= _MAJORITY:
        out.append("most finishers' schools are on record in another division")
    if div_from == "schools":
        out.append("division read from the schools, not the race title")
    return out


def _finishCell(cell, keys):
    cell["seasons"].sort(key=lambda s: -s["year"])
    cell["n_seasons_all"] = len(cell["seasons"])
    cell["seasons"] = cell["seasons"][:SEASONS_SHOWN]
    cell["trend"] = {k: trendOf([(s["year"], (s["lines"].get(k) or {}).get("mark"))
                                 for s in cell["seasons"]]) for k in keys}
    latest = cell["seasons"][0] if cell["seasons"] else None
    cell["latest"] = latest
    reasons = list(latest["flags"]) if latest else []
    if len(cell["seasons"]) < 2:
        reasons.insert(0, "one season on record")
    cell["thin_reasons"] = reasons
    # ★ ONLY THE SAMPLE MAKES A CELL THIN; the provenance note (division
    #   read from the schools) is said, not held against it.
    cell["thin"] = any(not r.startswith(_PROVENANCE) for r in reasons)


_PROVENANCE = "division read"


def _hardFlags(season):
    """A season's flags that are about the SAMPLE, not about where a label
    came from."""
    return [f for f in season.get("flags", []) if not f.startswith(_PROVENANCE)]


def _sectionCells(state, placed, gender, pool, ratings, ran_state):
    """The rounds before state: per (kind, unit, division), per season, the
    finishers who went on to run that season's state meet."""
    cells = {}
    for race in placed:
        if race["kind"] == "state" or race["gender"] != gender:
            continue
        year = race["year"]
        went = ran_state.get(year)
        if not went:
            continue           # no state meet on record that season to reach
        k = (race["kind"], race["unit"], _slug(race["div"]), year)
        cells.setdefault(k, []).append(race)
    out = {}
    for (kind, unit, slug, year), rs in cells.items():
        rs.sort(key=lambda r: -len(r["rows"]))
        race = rs[0]
        rows = race["rows"]
        went = ran_state[year]
        adv = [r for r in rows if r["person_id"] in went]
        if not adv:
            # ⚠ NOBODY FROM THIS RACE RAN STATE: an early round, or a meet
            #   that is not on the state path at all. Not a mark of zero.
            continue
        adv_r = [_seasonRating(r, pool, ratings) for r in adv]
        have = [v for v in adv_r if v is not None]
        feeds, feed_share, _n = majority([went[r["person_id"]] for r in adv])
        season = {"year": year, "n_finishers": len(rows),
                  "n_advanced": len(adv), "n_rated": len(have),
                  "n_schools": race["n_schools"], "agree": race["agree"],
                  "div_from": race["div_from"],
                  "deepest_place": max((r.get("place") or 0) for r in adv),
                  "feeds": feeds, "feeds_share": feed_share,
                  "meet_id": race["meet_id"], "div_id": race["div_id"],
                  "source": race.get("source"),
                  "meet_name": race.get("meet_name"),
                  "distance": race.get("distance"),
                  "lines": {"advance": markOf(have)}}
        season["flags"] = _flags(len(have), len(adv), race["agree"],
                                 race["div_from"], who="advancers")
        ck = (kind, unit, slug)
        cell = out.setdefault(ck, {"kind": kind, "unit": unit, "slug": slug,
                                   "value": race["div"], "seasons": []})
        cell["seasons"].append(season)
    cells_out = []
    for (kind, unit, slug), cell in out.items():
        _finishCell(cell, ("advance",))
        # the state division this round feeds: the majority over its seasons'
        # advancers, weighted by how many advanced
        votes = {}
        for s in cell["seasons"]:
            if s["feeds"]:
                votes[s["feeds"]] = votes.get(s["feeds"], 0) + s["n_advanced"]
        cell["feeds"] = max(votes.items(), key=lambda kv: kv[1])[0] if votes else None
        word = {"section": "", "region": "Region "}[kind]
        div = "" if slug == "all" else f" D{cell['value']}" \
            if str(cell["value"]).isdigit() else f" {cell['value']}"
        cell["label"] = f"{word}{_pretty(unit)}{div}".strip()
        cells_out.append(cell)
    cells_out.sort(key=lambda c: (c["unit"] or "",
                                  P.divisionSortKey(c["value"])))
    return cells_out


def _pretty(unit):
    u = str(unit or "")
    return u if len(u) <= 4 or not u.isupper() else u.title()


# ------------------------------------------------------------------ #
#  the database
# ------------------------------------------------------------------ #

def _row(r, *keys):
    return P._row(r, *keys)


def _stateMeets(cur, state):
    """{(meet_id, source): [(kind, unit)]} for every championship meet that
    is this state's final or one of its section / regional finals."""
    cur.execute("""
        WITH ids AS (
            SELECT DISTINCT meet_id, source FROM meet_unit
            WHERE  sport = 'XC'
              AND  ((kind = 'state' AND unit = %(st)s)
                    OR (kind IN ('section', 'region')
                        AND upper(state) = %(st)s))
        )
        SELECT u.meet_id, u.source, u.kind, u.unit
        FROM   meet_unit u
        JOIN   ids i ON i.meet_id = u.meet_id
                    AND i.source IS NOT DISTINCT FROM u.source
        WHERE  u.sport = 'XC'
    """, {"st": state})
    out = {}
    for r in cur.fetchall():
        mid, src, kind, unit = _row(r, "meet_id", "source", "kind", "unit")
        out.setdefault((mid, src), []).append((kind, unit))
    return out


def _titleFacts(meet_name, title, source, state):
    """The race title's own (kind, unit) facts -- build_meet_units'
    parser, the one that filed the meet in the first place."""
    try:
        from build_meet_units import unitsForMeet
        _champ, facts = unitsForMeet(meet_name, title, source, state)
        return facts
    except Exception:                                   # noqa: BLE001
        return []


def _races(cur, state, meets):
    if not meets:
        return []
    keys = list(meets)
    cur.execute("""
        SELECT m.meet_id, m.source, m.div_id, m.meet_name, m.division,
               m.course_name, m.distance, m.meet_date
        FROM   meets m
        JOIN   unnest(%(mids)s::bigint[], %(srcs)s::text[]) AS k(meet_id, source)
               ON m.meet_id = k.meet_id
              AND m.source IS NOT DISTINCT FROM k.source
    """, {"mids": [k[0] for k in keys], "srcs": [k[1] for k in keys]})
    out = []
    for r in cur.fetchall():
        mid, src, did, name, title, course, dist, date = _row(
            r, "meet_id", "source", "div_id", "meet_name", "division",
            "course_name", "distance", "meet_date")
        if P._NOT_HS_MEET.search(name or "") or P._NOT_HS_MEET.search(title or ""):
            continue
        level = meetLevel(name, meets[(mid, src)])
        if level is None:
            continue
        tf = _titleFacts(name, title, src, state)
        out.append({"meet_id": mid, "source": src, "div_id": did,
                    "meet_name": name, "title": title,
                    "title_div": titleDivision(tf, level[0]),
                    "level": level, "course_name": course,
                    "distance": dist, "meet_date": date})
    return out


_HAS_SU = {}


def _hasSchoolUnit(cur):
    if "v" not in _HAS_SU:
        cur.execute("SELECT to_regclass('school_unit') IS NOT NULL AS ok")
        _HAS_SU["v"] = bool(_row(cur.fetchone(), "ok")[0])
    return _HAS_SU["v"]


def _finishers(cur, state, races, years):
    if not races:
        return []
    su = _hasSchoolUnit(cur)
    # ★ PLACE OVER EVERY FINISHER WITH A TIME, before the rated join: an
    #   unrated runner still took a place, and "top 25" means the 25 who
    #   crossed first, not the first 25 the boards rate.
    cur.execute(f"""
        WITH k AS (
            SELECT * FROM unnest(%(m)s::bigint[], %(s)s::text[],
                                 %(d)s::bigint[]) AS k(meet_id, source, div_id)
        ), fin AS (
            SELECT r.result_id, r.meet_id, r.source, r.div_id, r.person_id,
                   r.time_seconds,
                   row_number() OVER (PARTITION BY r.meet_id, r.source, r.div_id
                                      ORDER BY r.time_seconds, r.result_id) AS place
            FROM   results r
            JOIN   k ON r.meet_id = k.meet_id AND r.div_id = k.div_id
                    AND r.source IS NOT DISTINCT FROM k.source
            WHERE  r.time_seconds > 0 AND r.time_seconds < 99999
        )
        SELECT f.meet_id, f.source, f.div_id, f.person_id, f.place,
               f.time_seconds, rr.pool, rr.year,
               rr.speed_rating AS race_rating, rr.school,
               {"su.state_div, su.class, su.section, su.section_div"
                if su else "NULL AS state_div, NULL AS class, NULL AS section, NULL AS section_div"}
        FROM   fin f
        JOIN   ranking_results rr ON rr.result_id = f.result_id
                                 AND rr.sport = 'XC'
        {"LEFT JOIN school_unit su ON su.school = rr.school AND su.state = %(st)s AND su.sport = 'XC' AND NOT su.is_college"
         if su else ""}
        WHERE  rr.pool IN ('hs_m', 'hs_f') AND rr.year = ANY(%(years)s)
          AND  f.person_id IS NOT NULL
    """, {"m": [r["meet_id"] for r in races], "s": [r["source"] for r in races],
          "d": [r["div_id"] for r in races], "st": state, "years": years})
    cols = ("meet_id", "source", "div_id", "person_id", "place", "time_seconds",
            "pool", "year", "race_rating", "school", "state_div", "class",
            "section", "section_div")
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, _row(r, *cols)))
        for c in ("time_seconds", "race_rating"):
            if d[c] is not None:
                d[c] = float(d[c])
        out.append(d)
    return out


def _seasonRatings(cur, rows, years):
    pids = sorted({r["person_id"] for r in rows})
    if not pids:
        return {}
    cur.execute("""
        SELECT person_id, pool, year, mean_rating
        FROM   athlete_season
        WHERE  sport = 'XC' AND pool IN ('hs_m', 'hs_f')
          AND  person_id = ANY(%(p)s) AND year = ANY(%(y)s)
          AND  mean_rating IS NOT NULL
    """, {"p": pids, "y": years})
    out = {}
    for r in cur.fetchall():
        pid, pool, year, v = _row(r, "person_id", "pool", "year", "mean_rating")
        out[(pid, pool, int(year))] = float(v)
    return out


# ------------------------------------------------------------------ #
#  marks -> times, through the conversions page's own inverse
# ------------------------------------------------------------------ #

def toTime(rating, pool, distance, difficulty=None, canonical_id=None,
           course=None):
    """A season rating as a clock at `distance`: conversions'
    _norm_from_rating then normalized_to_time, exactly the pair the
    conversions page and fiveKForRating use. No `difficulty` = a typical
    cross country course. None when there is no conversion."""
    if rating is None or not distance:
        return None
    try:
        import conversions as C
        norm = C._norm_from_rating(float(rating), pool, 0.0, "XC")
        if not norm:
            return None
        ctx = {"distance": float(distance), "pool": pool, "sport": "XC"}
        if difficulty is not None:
            ctx.update(difficulty=float(difficulty), canonical_id=canonical_id,
                       course=course)
        t = C.normalized_to_time(norm, ctx)
        return float(t) if t and t > 0 else None
    except Exception:                                   # noqa: BLE001
        return None


def _stateVenue(cur, state, gender, distance):
    """{course_name, distance, editions, difficulty, canonical_id} for the
    state meet's habit venue (projections.stateCourse), the difficulty
    resolved ONCE so a page's dozens of conversions do not each look it up.
    None when no venue holds MIN_EDITIONS editions."""
    course = P.stateCourse(cur, state, gender)
    if not course:
        return None
    out = dict(course)
    out["distance"] = course.get("distance") or distance
    out["difficulty"] = out["canonical_id"] = None
    try:
        spec = P._courseSpec(cur, out, P.currentYear(cur))
        if spec and spec.get("canonical_id"):
            import conversions as C
            out["canonical_id"] = spec["canonical_id"]
            out["difficulty"] = C.venue_difficulty(
                "XC", canonical_id=spec["canonical_id"],
                distance_meters=float(out["distance"]))
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
    return out


def stampTimes(data, venues, convert=toTime):
    """Put est. times on every mark: at a typical course over the state
    meet's distance, and at the state course when its difficulty is known.
    Section marks get the typical-course time over their own race's
    distance -- they are run on the section's course, not the state's."""
    for gender, g in data["genders"].items():
        pool = g["pool"]
        venue = venues.get(gender)
        dist = (venue or {}).get("distance") or g.get("distance") or 5000
        g["venue"] = venue
        g["time_distance"] = dist
        for cell in g["divisions"]:
            for s in cell["seasons"]:
                for line in s["lines"].values():
                    if not line:
                        continue
                    line["t_typical"] = convert(line["mark"], pool, dist)
                    line["t_state"] = (convert(line["mark"], pool, dist,
                                               venue["difficulty"],
                                               venue["canonical_id"],
                                               venue["course_name"])
                                       if venue and venue.get("difficulty")
                                       is not None else None)
        for cell in g["sections"]:
            for s in cell["seasons"]:
                line = s["lines"].get("advance")
                if line:
                    d = s.get("distance") or dist
                    line["t_typical"] = convert(line["mark"], pool, d)
                    line["t_distance"] = d
        # ! THE PAGE SAYS WHEN NOTHING CONVERTED, rather than promising
        #   times in its header over cards that have none.
        g["has_times"] = any(
            (ln or {}).get("t_typical") for c in g["divisions"]
            for s in c["seasons"] for ln in s["lines"].values())
    return data


# ------------------------------------------------------------------ #
#  the one call, cached per state
# ------------------------------------------------------------------ #

def _computeUncached(cur, state):
    year = P.currentYear(cur)
    years = list(range(year - _FETCH_SEASONS, year + 1))
    meets = _stateMeets(cur, state)
    races = _races(cur, state, meets)
    rows = _finishers(cur, state, races, years)
    ratings = _seasonRatings(cur, rows, years)
    data = computeMarks(state, races, rows, ratings)
    venues = {}
    for gender, g in data["genders"].items():
        if g["divisions"]:
            venues[gender] = _stateVenue(cur, state, gender, g.get("distance"))
    stampTimes(data, venues)
    data["current_year"] = year
    data["n_meets"] = len(meets)
    return data


def _empty(state, why):
    return {"state": state, "genders": {}, "years": [], "unplaced": 0,
            "error": why}


def marksFor(cur, state):
    """The cached marks for one state (both genders); stamp is when."""
    def compute():
        try:
            return _computeUncached(cur, state)
        except Exception as exc:                        # noqa: BLE001
            # ! NO meet_unit YET (step 10e not run) or a mid-swap table: the
            #   page says "nothing on record" and tries again in minutes.
            cur.connection.rollback()
            print(f"cuts.marksFor {state}: {type(exc).__name__}: {exc}",
                  flush=True)
            return _empty(state, f"{type(exc).__name__}")
    val, stamp = ttlcache.get(
        ("wit", state), compute, ttl=_TTL,
        ttl_of=lambda v: 300.0 if v.get("error") else _TTL)
    return dict(val, computed_at=stamp)


def findDivision(data, gender, slug):
    g = data.get("genders", {}).get(gender) or {}
    return next((c for c in g.get("divisions", []) if c["slug"] == slug), None)


def sectionsFeeding(data, gender, slug):
    """The section / regional cells whose advancers mostly ran `slug`."""
    g = data.get("genders", {}).get(gender) or {}
    return [c for c in g.get("sections", []) if c.get("feeds") == slug]


# ------------------------------------------------------------------ #
#  the athlete page's one line
# ------------------------------------------------------------------ #

_WARMING = set()
_WARM_LOCK = threading.Lock()


def _warm(state):
    """Fill the state's cache in the background. An athlete page must never
    wait on a state's whole history; the first visitor gets no line, the
    next one gets it."""
    with _WARM_LOCK:
        if state in _WARMING:
            return
        _WARMING.add(state)

    def run():
        try:
            import psycopg2.extras
            from database import getConn
            with getConn() as conn:
                with conn.cursor(
                        cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    marksFor(cur, state)
        except Exception:                               # noqa: BLE001
            pass
        finally:
            with _WARM_LOCK:
                _WARMING.discard(state)
    threading.Thread(target=run, daemon=True, name=f"wit-{state}").start()


_SEASON_UNIT_COLS = {}


def _athleteSeason(cur, person_id, year):
    if "cols" not in _SEASON_UNIT_COLS:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name = 'athlete_season'""")
        have = {_row(r, "column_name")[0] for r in cur.fetchall()}
        _SEASON_UNIT_COLS["cols"] = [c for c in ("state_div", "class", "section",
                                                 "section_div") if c in have]
    cols = _SEASON_UNIT_COLS["cols"]
    sel = "".join(f', "{c}"' for c in cols)
    cur.execute(f"""
        SELECT person_id, pool, year, mean_rating, n_races, state, school{sel}
        FROM   athlete_season
        WHERE  person_id = %s AND sport = 'XC' AND year = %s
          AND  pool IN ('hs_m', 'hs_f') AND mean_rating IS NOT NULL
        ORDER  BY n_races DESC NULLS LAST LIMIT 1
    """, (person_id, year))
    row = cur.fetchone()
    if not row:
        return None
    keys = ("person_id", "pool", "year", "mean_rating", "n_races", "state",
            "school") + tuple(cols)
    d = dict(zip(keys, _row(row, *keys)))
    # ! THE ROW'S OWN UNITS FIRST (the boards' membership); the school's
    #   school_unit verdict only when the row carries none.
    if not any(d.get(c) for c in cols) and d.get("school") and d.get("state"):
        try:
            cur.execute("""
                SELECT state_div, class, section, section_div FROM school_unit
                WHERE  school = %s AND state = %s AND sport = 'XC'
                  AND  NOT is_college LIMIT 1
            """, (d["school"], d["state"]))
            su = cur.fetchone()
            if su:
                uc = ("state_div", "class", "section", "section_div")
                d.update(zip(uc, _row(su, *uc)))
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
    return d


def _whose(year, current):
    if year == current - 1:
        return "last year's"
    if year == current:
        return "this season's"
    return f"the {year}"


def lineFor(season, data, current_year):
    """The athlete line, from the athlete's season row and the state's
    marks: the first rung of the ladder (section advance, state qualify, top
    25, top 8) their rating has not reached, and the one below it they have.
    None when nothing reliable applies -- no line beats a wrong one."""
    state = (season.get("state") or "").upper()
    gender = _POOL_GENDER.get(season.get("pool"))
    rating = season.get("mean_rating")
    if not state or not gender or rating is None:
        return None
    div_raw = season.get("state_div") or season.get("class")
    slug = _slug(str(div_raw).upper()) if div_raw else None
    g = data.get("genders", {}).get(gender) or {}
    divs = g.get("divisions", [])
    cell = (findDivision(data, gender, slug) if slug else None)
    if cell is None and len(divs) == 1 and divs[0]["slug"] == "all":
        cell = divs[0]
    if cell is None or not cell.get("latest"):
        return None
    rungs = []
    sec_unit = (season.get("section") or "").upper()
    if sec_unit:
        sdiv = _slug(str(season.get("section_div")).upper()) \
            if season.get("section_div") else "all"
        sc = next((c for c in g.get("sections", [])
                   if c["kind"] == "section" and (c["unit"] or "").upper() == sec_unit
                   and c["slug"] == sdiv), None)
        if sc and sc.get("latest") and not _hardFlags(sc["latest"]):
            ln = sc["latest"]["lines"].get("advance")
            if ln:
                s = sc["latest"]
                rungs.append({"what": f"mark to advance from {sc['label']}",
                              "short": f"{sc['label']} advancing",
                              "mark": ln["mark"], "year": s["year"],
                              "where": "section", "where_label": sc["label"],
                              "race": (s.get("meet_id"), s.get("source"),
                                       s.get("div_id"))})
    latest = cell["latest"]
    # ! A THIN STATE SEASON GIVES NO LINE AT ALL: the athlete page is not
    #   the place to explain a sample size, and the page it links to is.
    if _hardFlags(latest):
        return None
    label = cell["label"]
    words = {"qualify": "state qualifying mark", "top": "top-25 mark",
             "podium": "top-8 mark"}
    for key in ("qualify", "top", "podium"):
        ln = latest["lines"].get(key)
        if ln:
            rungs.append({"what": f"{words[key]}", "short": words[key],
                          "mark": ln["mark"], "year": latest["year"],
                          "where": "state"})
    if not rungs:
        return None
    rating = float(rating)
    unmet = next((r for r in rungs if rating < r["mark"]), None)
    met = [r for r in rungs if rating >= r["mark"]]
    out = {"state": state, "gender": gender, "slug": cell["slug"],
           "label": label, "rating": round(rating, 1),
           "pool": season.get("pool"),
           "href": f"/what-it-takes/{state.lower()}/{cell['slug']}"
                   + ("?g=girls" if gender == "girls" else "")}
    if unmet:
        out.update(kind="off", gap=round(unmet["mark"] - rating, 1),
                   mark=unmet["mark"], rung=unmet,
                   text=f"{_whose(unmet['year'], current_year)} {unmet['what']}",
                   cleared=(f"{_whose(met[-1]['year'], current_year)} "
                            f"{met[-1]['short']}") if met else None)
    else:
        top = rungs[-1]
        out.update(kind="above", gap=round(rating - top["mark"], 1),
                   mark=top["mark"], rung=top,
                   text=f"{_whose(top['year'], current_year)} {top['what']}",
                   cleared=None)
    # ★ THE GAP AS A SHARE TOO (owner, 2026-10-05, "goal times"): points mean
    #   little to a parent; a rating is inverse to time, so 1.6% of the
    #   rating is about 1.6% of the race -- fifteen seconds over 5K at 16:00.
    out["gap_pct"] = round(100.0 * out["gap"] / rating, 1) if rating else None
    return out


# ------------------------------------------------------------------ #
#  goal times: the mark as a clock at the courses the athlete races
# ------------------------------------------------------------------ #

# ★ THE COURSES THEY RACE MOST, NOT EVERY COURSE: one line on the athlete page
#   holds two clocks before it wraps on a phone. Every other course is one
#   click away on /conversions?athlete=, which already takes any venue.
GOAL_COURSES = 2


def _racedCourses(cur, person_id, year):
    """This season's XC courses for one athlete, most-raced first (newest
    breaking a tie): [{course_name, distance, canonical_id, n}].

    ! AGGREGATED BEFORE THE course_canonical JOIN: that join matches on
      rounded GPS, which no index serves, so it runs over a handful of
      grouped rows rather than over every race (pool_view's note)."""
    cur.execute("""
        WITH mine AS (
            SELECT m.course_name, round(m.distance)::int AS distance,
                   round(m.gps_lat::numeric, 5) AS lat,
                   round(m.gps_long::numeric, 5) AS lon,
                   count(*) AS n, max(rr.race_date) AS last
            FROM   ranking_results rr
            JOIN   results r ON r.result_id = rr.result_id
            JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                          AND m.source IS NOT DISTINCT FROM r.source
            WHERE  rr.person_id = %(p)s AND rr.sport = 'XC' AND rr.year = %(y)s
              AND  m.course_name IS NOT NULL AND m.course_name <> ''
              AND  m.distance BETWEEN 1000 AND 12000
            GROUP  BY 1, 2, 3, 4
        )
        SELECT mine.course_name, mine.distance, mine.n,
               (SELECT cc.canonical_id FROM course_canonical cc
                WHERE  cc.course_name = mine.course_name
                  AND  round(cc.gps_lat::numeric, 5) = mine.lat
                  AND  round(cc.gps_long::numeric, 5) = mine.lon
                LIMIT 1) AS canonical_id
        FROM   mine
        ORDER  BY mine.n DESC, mine.last DESC NULLS LAST
    """, {"p": person_id, "y": year})
    keys = ("course_name", "distance", "n", "canonical_id")
    return [dict(zip(keys, _row(r, *keys))) for r in cur.fetchall()]


def _raceCourse(cur, meet_id, source, div_id):
    """{course_name, distance, canonical_id} of one championship race."""
    cur.execute("""
        SELECT m.course_name, m.distance,
               (SELECT cc.canonical_id FROM course_canonical cc
                WHERE  cc.course_name = m.course_name
                  AND  round(cc.gps_lat::numeric, 5) = round(m.gps_lat::numeric, 5)
                  AND  round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
                LIMIT 1) AS canonical_id
        FROM   meets m
        WHERE  m.meet_id = %s AND m.div_id = %s
          AND  m.source IS NOT DISTINCT FROM %s
        LIMIT  1
    """, (meet_id, div_id, source))
    r = cur.fetchone()
    if not r:
        return None
    keys = ("course_name", "distance", "canonical_id")
    return dict(zip(keys, _row(r, *keys)))


def _difficulty(canonical_id, distance):
    if canonical_id is None or not distance:
        return None
    try:
        import conversions as Cv
        return Cv.venue_difficulty("XC", canonical_id=canonical_id,
                                   distance_meters=float(distance))
    except Exception:                                   # noqa: BLE001
        return None


def _cellOn(cur, canonical_id, distance):
    """conversions.venue_difficulty's XC lookup, on the REQUEST's cursor.

    ! NOT A SECOND getConn INSIDE THE ATHLETE REQUEST. venue_difficulty opens
      its own pooled connection; nested inside the athlete route's, it grows
      the pool past its minimum, and the pool then CLOSES the route's
      connection on return -- which the route still uses after its block
      (the rating_outlier read), so the page became a 503 in testing. Same
      key, same table, same rounding as venue_difficulty."""
    if canonical_id is None or not distance:
        return None
    cur.execute("""SELECT difficulty FROM course_difficulties
                   WHERE canonical_id = %s AND distance_m = %s""",
                (canonical_id, int(round(float(distance) / 100.0) * 100)))
    r = cur.fetchone()
    v = _row(r, "difficulty")[0] if r else None
    return float(v) if v is not None else None


def goalTimes(line, raced, champ, convert=toTime, difficulty=_difficulty):
    """[{course_name, distance, seconds, champ}] -- the line's mark as a clock
    at the athlete's most-raced course(s) this season and at the round's
    championship course.

    ! ONLY A COURSE WITH A RATED DIFFICULTY GETS A CLOCK. An unrated venue
      would silently be the typical course wearing its name; when none of
      their courses is rated, one typical-course time at their usual
      distance stands in, and says so."""
    pool, mark = line.get("pool"), line.get("mark")
    if not pool or mark is None:
        return []
    out, seen = [], set()

    def add(c, is_champ):
        if not c or not c.get("course_name") or not c.get("distance"):
            return False
        key = (c["course_name"], int(round(float(c["distance"]))))
        if key in seen:
            for o in out:                       # their course IS the final's
                if (o["course_name"], int(round(o["distance"]))) == key:
                    o["champ"] = o["champ"] or is_champ
            return True
        d = c.get("difficulty")
        if d is None:
            d = difficulty(c.get("canonical_id"), c["distance"])
        if d is None:
            return False
        t = convert(mark, pool, float(c["distance"]), d,
                    c.get("canonical_id"), c["course_name"])
        if not t:
            return False
        seen.add(key)
        out.append({"course_name": c["course_name"],
                    "distance": float(c["distance"]), "seconds": t,
                    "champ": is_champ})
        return True

    top_n = max((c.get("n") or 0) for c in raced) if raced else 0
    got = 0
    for c in raced:
        if got >= GOAL_COURSES or (c.get("n") or 0) < top_n and got:
            break
        if add(c, False):
            got += 1
    add(champ, True)
    if not out and raced:
        d = raced[0]["distance"]
        t = convert(mark, pool, float(d))
        if t:
            out.append({"course_name": None, "distance": float(d),
                        "seconds": t, "champ": False})
    return out


def _lineTimes(cur, person_id, year, line, data):
    """goalTimes for the line, its own failures contained: the line without
    clocks is still the line."""
    try:
        rung = line.get("rung") or {}
        champ = None
        if rung.get("where") == "section" and rung.get("race"):
            champ = _raceCourse(cur, *rung["race"])
        elif rung.get("where") == "state":
            v = (data["genders"].get(line["gender"]) or {}).get("venue")
            if v and v.get("difficulty") is not None:
                champ = v
        return goalTimes(line, _racedCourses(cur, person_id, year), champ,
                         difficulty=lambda cid, d: _cellOn(cur, cid, d))
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
        return []


def athleteLine(cur, person_id):
    """The line for the athlete page, or None. Never raises, never waits on
    a cold state: see _warm."""
    try:
        year = P.currentYear(cur)
        season = _athleteSeason(cur, person_id, year)
        if not season or not season.get("state"):
            return None
        state = season["state"].upper()
        data = ttlcache.peek(("wit", state))
        if data is None:
            _warm(state)
            return None
        line = lineFor(season, data, year)
        if line:
            line["times"] = _lineTimes(cur, person_id, year, line, data)
            line["conv_href"] = f"/conversions?athlete={int(person_id)}"
        return line
    except Exception:                                   # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:                               # noqa: BLE001
            pass
        return None


# ------------------------------------------------------------------ #
#  the routes
# ------------------------------------------------------------------ #

from flask import Blueprint, abort, redirect, render_template, request  # noqa: E402

bp = Blueprint("cuts", __name__)


def _states():
    from landing import STATE_NAMES, US_STATES
    return [(c, STATE_NAMES[c]) for c in US_STATES if c in STATE_NAMES], \
        STATE_NAMES


def _gender():
    g = (request.args.get("g") or "boys").strip().lower()
    return g if g in GENDERS else "boys"


def _cursor():
    import psycopg2.extras
    from database import getConn
    return getConn, psycopg2.extras.RealDictCursor


@bp.route("/what-it-takes")
def wit_index():
    st = (request.args.get("state") or "").strip().upper()
    states, names = _states()
    if st in names:
        # the state picker's no-script submit
        return redirect(f"/what-it-takes/{st.lower()}", code=302)
    return render_template("what_it_takes.html", mode="index", states=states,
                           gender=_gender())


@bp.route("/what-it-takes/<state>")
def wit_state(state):
    states, names = _states()
    st = (state or "").upper()
    if st not in names:
        abort(404)
    if state != st.lower():
        return redirect(f"/what-it-takes/{st.lower()}", code=301)
    getConn, factory = _cursor()
    with getConn() as conn:
        with conn.cursor(cursor_factory=factory) as cur:
            data = marksFor(cur, st)
    return render_template("what_it_takes.html", mode="state", state=st,
                           state_name=names[st], states=states, data=data,
                           gender=_gender(), mark_q=MARK_Q, thin_n=THIN_N)


@bp.route("/what-it-takes/<state>/<division>")
def wit_page(state, division):
    states, names = _states()
    st = (state or "").upper()
    if st not in names:
        abort(404)
    want = P.divisionSlug(division)
    if state != st.lower() or division != want:
        q = ("?" + request.query_string.decode("utf-8", "replace")
             if request.query_string else "")
        return redirect(f"/what-it-takes/{st.lower()}/{want}{q}", code=301)
    gender = _gender()
    getConn, factory = _cursor()
    with getConn() as conn:
        with conn.cursor(cursor_factory=factory) as cur:
            data = marksFor(cur, st)
    cell = findDivision(data, gender, want)
    other = "girls" if gender == "boys" else "boys"
    if cell is None and findDivision(data, other, want) is None:
        # ! A 404 FOR A SLUG NEITHER GENDER HAS: an invented division is not
        #   a page. One gender missing is a real, thin answer and renders.
        abort(404)
    g = data["genders"].get(gender) or {}
    return render_template(
        "what_it_takes.html", mode="division", state=st,
        state_name=names[st], states=states, data=data, gender=gender,
        gender_words=GENDERS[gender][1], division_slug=want, cell=cell,
        divisions=g.get("divisions", []),
        other_divisions=(data["genders"].get(other) or {}).get("divisions", []),
        sections=sectionsFeeding(data, gender, want),
        all_sections=g.get("sections", []),
        venue=g.get("venue"), time_distance=g.get("time_distance"),
        place_lines=PLACE_LINES, mark_q=MARK_Q, thin_n=THIN_N,
        seasons_shown=SEASONS_SHOWN)


@bp.app_template_filter("wit_time")
def _witTime(seconds):
    return P.fmtTime(seconds)


@bp.app_template_filter("wit_ord")
def _witOrd(n):
    """1st, 2nd, 3rd, 11th, 22nd."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


@bp.app_template_filter("wit_signed")
def _witSigned(v):
    if v is None:
        return ""
    return f"{'+' if v > 0 else ''}{v:.1f}"
