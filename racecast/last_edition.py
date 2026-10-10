"""last_edition.py -- the field for a meet that has not run yet: the teams
from its previous edition (owner, 2026-10-06: "Make predicting an UPCOMING
meet work").

    from last_edition import upcomingMeet, lastEditionField

★ AN UPCOMING MEET HAS NO RESULTS, AND EVERYTHING WAS KEYED ON RESULTS. The
  Coming up list (weekend.py) links each posted meet to /predictions, and the
  page then asked for the meet's races (grouped FROM results), its date
  (min(date) FROM results) and its field (_exactField: the people who RAN
  it). A meet not yet run has none of those, so the link opened a page with
  nothing to predict -- and the Upcoming copy promised a field "from the teams
  that race the course now".

★ THE CALENDAR IS IN THE META TABLES, THE TEAMS ARE IN LAST YEAR. A posted
  meet has its date and its races (meets / meets_tf / meets_tfrrs, the rows
  weekend.py reads). It does not say who is coming -- there is no entries
  scrape -- so the field is borrowed: the teams that ran this meet's PREVIOUS
  EDITION, each with this season's squad (meetField's own machinery, fed
  those runners as its `originals`).

! "PREVIOUS EDITION" IS A RULE, NOT A WINDOW. The most recent earlier meet of
  the same feed whose name matches once years, ordinals and punctuation are
  gone, that HAS results. No "within 400 days" -- a meet skipped for a year
  (2020) still has a last edition, and an unrelated meet of the same name a
  week ago is excluded by being the wrong venue or state, not by a count.

! EVERY QUERY IS ONE FEED'S (2026-10-05): the anet and tfrrs ids collide, so
  the upcoming meet, its candidates and their results are all read under the
  source the route resolved (app._predictSource / the link's ?src=).
"""
import re

# The dates are text with junk years in the corpus; weekend.py and panels.py
# compare them as ISO strings behind the same guard.
_ISO = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$"

# "2026", and a season span "2025-26" / "2025-2026": the year a running is
# named for is what changes between editions, never what the meet is.
_YEAR = re.compile(r"\b(19|20)\d{2}(\s*[-/]\s*(\d{4}|\d{2}))?\b")
# "38th", "1st", "22nd", "3rd": the edition count, the other thing that moves
_ORDINAL = re.compile(r"\b\d+\s*(st|nd|rd|th)\b")
_NONWORD = re.compile(r"[^a-z0-9]+")


def editionName(name):
    """The meet's name with what changes between editions removed: lower
    case, no years, no ordinals, punctuation to single spaces.

    "2026 38th Annual Nike Portland XC!" -> "annual nike portland xc"

    ★ THE FEEDS NAME A MEET BY ITS RUNNING. anet stores "2025 Colorado State
      Championships"; a host writes "38th Annual ..."; tfrrs appends the
      year. Two editions of one meet differ in exactly those tokens.
    ! ORDER MATTERS: ordinals and years before punctuation, so "Twilight
      2025-26" loses the span whole rather than leaving a "26"."""
    s = (name or "").lower()
    s = _ORDINAL.sub(" ", s)
    s = _YEAR.sub(" ", s)
    s = _NONWORD.sub(" ", s)
    return " ".join(s.split())


def _venueKey(v):
    return editionName(v) if v else ""


def pickEdition(target, candidates):
    """The previous edition of `target` among `candidates`, or None.

    target:     {meet_id, name, date, venue, state}
    candidates: [{meet_id, name, date, venue, state}] -- `date` is the
                candidate's FIRST RESULT's date, None when it has no results.

    ★ THE RULE (owner, 2026-10-06): the same normalised name, earlier than
      the upcoming running, with results; of those the same venue first,
      then the same state, then the most recent.
    ! VENUE BEFORE RECENCY ON PURPOSE. A generic name -- "Twilight
      Invitational", "Conference Championship" -- is a different meet in forty
      states, and borrowing the wrong one's teams is a wrong field, not an old
      one. A meet that moved venue still finds itself through the state, and
      only loses to an older running at its old venue, which is the same
      meet's teams.
    ! NO DATE ON THE TARGET = NOTHING IS "LATER": every running with results
      is earlier than one that has not happened."""
    want = editionName(target.get("name"))
    if not want:
        return None
    tdate = target.get("date") or None
    tvenue = _venueKey(target.get("venue"))
    tstate = (target.get("state") or "").strip().upper()
    pool = []
    for c in candidates:
        if c.get("meet_id") == target.get("meet_id"):
            continue
        if not c.get("date") or editionName(c.get("name")) != want:
            continue
        if tdate and str(c["date"])[:10] >= str(tdate)[:10]:
            continue
        pool.append(c)
    if not pool:
        return None

    def key(c):
        same_venue = bool(tvenue) and _venueKey(c.get("venue")) == tvenue
        same_state = (bool(tstate)
                      and (c.get("state") or "").strip().upper() == tstate)
        return (same_venue, same_state, str(c["date"])[:10], c["meet_id"])
    return max(pool, key=key)


def genderOfLabel(label):
    """'M' | 'F' | None from the gender word in a race's label ("Men's 8k",
    "Varsity Girls"). anet's XC divisions are usually just "Varsity", whose
    gender is on the RESULTS -- which an upcoming meet does not have."""
    from tf_points import genderOf
    return genderOf(label)


def _snap(distance):
    """A race distance at 100 m, the snap course_difficulties keys on
    (app._champ_join), so 4999.9 and 5000 are one race length."""
    try:
        return int(round(float(distance) / 100.0) * 100) if distance else None
    except (TypeError, ValueError):
        return None


def _compatible(u, e):
    if u.get("gender") and e.get("gender") and u["gender"] != e["gender"]:
        return False
    a, b = _snap(u.get("distance")), _snap(e.get("distance"))
    return not (a and b and a != b)


def _sameRace(a, b):
    return (editionName(a.get("label")) == editionName(b.get("label"))
            and _snap(a.get("distance")) == _snap(b.get("distance")))


def mapRace(race, upcoming, edition):
    """(div_ids, how): which of the last edition's races `race` is.

    race:     the upcoming race {div_id, label, distance, gender}, or None for
              the whole meet ("All races")
    upcoming: every race of the upcoming meet (for siblings)
    edition:  the last edition's races, same shape, gender from its runners

    how is "race" (matched), "gender" (not matched: every edition race of the
    race's gender), or "whole" (div_ids None: the whole edition).

    ★ BY LABEL, THEN BY GENDER AND DISTANCE (owner, 2026-10-06). The label
      after editionName ("Varsity", "men s 8k") with a compatible gender and
      distance; failing that, the same gender at the same distance -- a host
      that renamed "Men's 8K" to "Men's 8000m".
    ⚠ anet's "Varsity" IS TWO RACES. Its XC divisions carry no gender word,
      so the boys' and girls' Varsity match both of last year's Varsity
      races. They are paired IN ORDER: the upcoming siblings by div_id
      against last year's by gender, each gender's races in their div_id
      order. anet numbers a meet's divisions as the host creates them, and a
      host sets up its meet the same way each year; when the counts differ
      the pairing is not attempted and the race falls to "gender"/"whole".
    ! UNMATCHED IS NOT EMPTY. The whole edition, narrowed to the race's
      gender when the label says it, is still the teams that come to this
      meet -- a better field than nothing to edit."""
    if race is None:
        return None, "whole"
    cands = []
    if editionName(race.get("label")):
        cands = [e for e in edition if _compatible(race, e)
                 and editionName(e.get("label")) == editionName(race.get("label"))]
    if not cands and race.get("gender") and _snap(race.get("distance")):
        cands = [e for e in edition if e.get("gender") == race["gender"]
                 and _snap(e.get("distance")) == _snap(race.get("distance"))]
    if cands:
        genders = sorted({e["gender"] for e in cands if e.get("gender")})
        if race.get("gender") or len(genders) <= 1:
            return [e["div_id"] for e in cands], "race"
        sibs = sorted((u for u in upcoming if _sameRace(u, race)
                       and not u.get("gender")),
                      key=lambda u: u["div_id"])
        groups = sorted(([e for e in cands if e.get("gender") == g]
                         for g in genders),
                        key=lambda grp: min(e["div_id"] for e in grp))
        if len(sibs) == len(groups):
            i = [u["div_id"] for u in sibs].index(race["div_id"])
            return [e["div_id"] for e in groups[i]], "race"
    if race.get("gender"):
        return [e["div_id"] for e in edition
                if e.get("gender") == race["gender"]], "gender"
    return None, "whole"


def raceGender(race, upcoming, edition):
    """The gender `race` maps to in the last edition, when its label did not
    say: so the race chip reads "Varsity · Boys" and agrees with the field
    under it."""
    if race.get("gender"):
        return race["gender"]
    ids, how = mapRace(race, upcoming, edition)
    if how != "race" or not ids:
        return None
    gs = {e.get("gender") for e in edition if e["div_id"] in ids}
    return gs.pop() if len(gs) == 1 else None


# ------------------------------------------------------------------ #
#  THE DATABASE HALF
# ------------------------------------------------------------------ #

def upcomingMeet(cur, meet_id, sport, source=None):
    """The posted meet from its meta rows -- {source, name, date, venue,
    state, races: [{div_id, label, distance, gender}]} -- or None.

    ★ THE TABLES weekend.py READS. anet XC: `meets`, one row per division
      (division, distance, meet_date); anet track: meets_tf_meta for the
      meet, meets_tf for its divisions; tfrrs: meets_tfrrs, XC divisions in
      its division_distances blob (keys are TEXT, see app._blob).
    ! `source` NAMES THE FEED; None reads anet first and then tfrrs, the
      unnarrowed reading _meetDate keeps, and the answer says which it was so
      every later query is that feed's."""
    sport = (sport or "XC").upper()
    out = None
    if source != "tfrrs":
        out = (_anetXC(cur, meet_id, source) if sport == "XC"
               else _anetTF(cur, meet_id, source))
    if out is None and source in (None, "tfrrs"):
        out = _tfrrs(cur, meet_id, sport)
    if out is not None:
        for r in out["races"]:
            r["gender"] = genderOfLabel(r.get("label"))
    return out


def _anetXC(cur, meet_id, source):
    # ! THE CORRECTED DISTANCE FIRST, as app._xc_distance_sql reads it
    cur.execute("""
        SELECT m.div_id, m.division, m.meet_name, m.course_name, m.state,
               substr(m.meet_date::text, 1, 10) AS meet_date,
               COALESCE(dov.distance::real, m.distance) AS distance,
               m.source
        FROM   meets m
        LEFT JOIN dist_override dov
               ON dov.meet_id = m.meet_id AND dov.div_id = m.div_id
        WHERE  m.meet_id = %(m)s
          AND  (%(src)s::text IS NULL OR m.source = %(src)s)
        ORDER  BY m.div_id
    """, {"m": int(meet_id), "src": source})
    rows = cur.fetchall()
    if not rows:
        return None
    feed = rows[0].get("source") or source or "anet"
    # ! ONE FEED'S ROWS even when none was named: the id can be two meets
    rows = [r for r in rows if (r.get("source") or feed) == feed]
    return {"source": feed,
            "name": next((r["meet_name"] for r in rows if r.get("meet_name")), None),
            "date": min((r["meet_date"] for r in rows if r.get("meet_date")),
                        default=None),
            "venue": next((r["course_name"] for r in rows if r.get("course_name")), None),
            "state": next((r["state"] for r in rows if r.get("state")), None),
            "races": [{"div_id": r["div_id"],
                       "label": r.get("division") or f"Race {r['div_id']}",
                       "distance": r.get("distance")} for r in rows]}


def _anetTF(cur, meet_id, source):
    src = source or "anet"
    cur.execute("""
        SELECT meet_name, substr(meet_date::text, 1, 10) AS meet_date,
               venue_name, state
        FROM   meets_tf_meta
        WHERE  meet_id = %(m)s AND source = %(src)s
        LIMIT  1
    """, {"m": int(meet_id), "src": src})
    meta = cur.fetchone()
    cur.execute("""
        SELECT div_id, min(division) AS division, min(meet_name) AS meet_name,
               min(venue_name) AS venue_name, min(state) AS state
        FROM   meets_tf
        WHERE  meet_id = %(m)s AND source = %(src)s
        GROUP  BY div_id
        ORDER  BY min(division) NULLS LAST, div_id
    """, {"m": int(meet_id), "src": src})
    divs = cur.fetchall()
    if not meta and not divs:
        return None
    meta = meta or {}
    d0 = divs[0] if divs else {}
    return {"source": src,
            "name": meta.get("meet_name") or d0.get("meet_name"),
            "date": meta.get("meet_date"),
            "venue": meta.get("venue_name") or d0.get("venue_name"),
            "state": meta.get("state") or d0.get("state"),
            "races": [{"div_id": r["div_id"],
                       "label": r.get("division") or f"Division {r['div_id']}",
                       "distance": None} for r in divs]}


def _tfrrs(cur, meet_id, sport):
    # ⚠ venue_name IS NOT A VENUE KEY on tfrrs (app.py: sparse, and on some
    #   meets the meet's own name). The city and state are what a meet's
    #   location is there.
    cur.execute("""
        SELECT meet_name, substr(date::text, 1, 10) AS meet_date, state,
               NULLIF(concat_ws(' ', city, state), '') AS venue,
               division_distances
        FROM   meets_tfrrs
        WHERE  meet_id = %(m)s AND sport = %(sp)s
        LIMIT  1
    """, {"m": int(meet_id), "sp": sport})
    row = cur.fetchone()
    if not row:
        return None
    races = []
    blob = row.get("division_distances") or {}
    if sport == "XC" and isinstance(blob, dict):
        for k, v in blob.items():
            if str(k).lstrip("-").isdigit():
                v = v or {}
                races.append({"div_id": int(k),
                              "label": v.get("div_name") or f"Race {k}",
                              "distance": v.get("distance")})
        races.sort(key=lambda r: r["div_id"])
    elif sport == "TF":
        cur.execute("""
            SELECT div_id, min(division) AS division
            FROM   meets_tf
            WHERE  meet_id = %(m)s AND source = 'tfrrs'
            GROUP  BY div_id ORDER BY min(division) NULLS LAST, div_id
        """, {"m": int(meet_id)})
        races = [{"div_id": r["div_id"],
                  "label": r.get("division") or f"Division {r['div_id']}",
                  "distance": None} for r in cur.fetchall()]
    return {"source": "tfrrs", "name": row.get("meet_name"),
            "date": row.get("meet_date"), "venue": row.get("venue"),
            "state": row.get("state"), "races": races}


def _candidates(cur, sport, source, name):
    """Every meet of this feed whose name could be an edition of `name`.

    ! A SUBSTRING PREFILTER, THE NAME TEST IS editionName's. Each word of the
      normalised name must appear (ILIKE, as meets_filter's name search);
      pickEdition then compares the normalised names exactly. The words are
      [a-z0-9] only, so none of them is a LIKE wildcard."""
    words = editionName(name).split()
    if not words:
        return []
    likes = " AND ".join(f"meet_name ILIKE %(w{i})s" for i in range(len(words)))
    params = {f"w{i}": f"%{w}%" for i, w in enumerate(words)}
    params["src"] = source
    if source == "tfrrs":
        params["sp"] = sport
        sql = f"""
            SELECT meet_id, meet_name, state,
                   NULLIF(concat_ws(' ', city, state), '') AS venue
            FROM   meets_tfrrs
            WHERE  sport = %(sp)s AND {likes}"""
    elif sport == "XC":
        sql = f"""
            SELECT meet_id, min(meet_name) AS meet_name, min(state) AS state,
                   min(course_name) AS venue
            FROM   meets
            WHERE  source = %(src)s AND {likes}
            GROUP  BY meet_id"""
    else:
        sql = f"""
            SELECT meet_id, meet_name, state, venue_name AS venue
            FROM   meets_tf_meta
            WHERE  source = %(src)s AND {likes}"""
    cur.execute(sql, params)
    return [{"meet_id": r["meet_id"], "name": r["meet_name"],
             "state": r.get("state"), "venue": r.get("venue")}
            for r in cur.fetchall()]


def findLastEdition(cur, meet_id, sport, up):
    """The previous edition of the upcoming meet `up` (upcomingMeet's
    answer): {meet_id, name, date, venue, state} or None."""
    sport = (sport or "XC").upper()
    source = up["source"]
    cands = [c for c in _candidates(cur, sport, source, up.get("name"))
             if editionName(c["name"]) == editionName(up.get("name"))
             and c["meet_id"] != int(meet_id)]
    if not cands:
        return None
    # ★ "THAT HAS RESULTS" AND ITS DATE, in one query: the first day a
    #   running's results carry, of this feed's rows only.
    table = "results" if sport == "XC" else "results_tf"
    cur.execute(f"""
        SELECT meet_id, min(date) AS d
        FROM   {table}
        WHERE  meet_id = ANY(%(ids)s) AND source = %(src)s
          AND  date ~ '{_ISO}'
        GROUP  BY meet_id
    """, {"ids": sorted({c["meet_id"] for c in cands}), "src": source})
    dated = {r["meet_id"]: r["d"] for r in cur.fetchall()}
    for c in cands:
        c["date"] = dated.get(c["meet_id"])
    return pickEdition({"meet_id": int(meet_id), "name": up.get("name"),
                        "date": up.get("date"), "venue": up.get("venue"),
                        "state": up.get("state")}, cands)


def editionRaces(cur, meet_id, sport, source):
    """The last edition's races, each with its runners' gender -- the shape
    mapRace matches against. The race page's own reading (app.
    get_meet_divisions): the division and distance from `meets` or the
    tfrrs blob, the corrected distance first, the gender off the runners."""
    if (sport or "XC").upper() == "XC":
        cur.execute("""
            SELECT r.div_id,
                   COALESCE(m.division,
                            mt.division_distances -> r.div_id::text ->> 'div_name')
                                                          AS division,
                   COALESCE(dov.distance::real, m.distance,
                            (mt.division_distances -> r.div_id::text
                                                   ->> 'distance')::real)
                                                          AS distance,
                   mode() WITHIN GROUP (ORDER BY a.gender)
                       FILTER (WHERE a.gender IN ('M', 'F')) AS gender
            FROM   results r
            LEFT JOIN meets m
                   ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                  AND m.source = r.source
            LEFT JOIN meets_tfrrs mt
                   ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id
                  AND mt.sport = 'XC'
            LEFT JOIN dist_override dov
                   ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
            LEFT JOIN LATERAL (
                SELECT x.gender FROM athletes x
                WHERE  x.athlete_id = COALESCE(r.person_id, r.athlete_id)
                  AND  x.gender IN ('M', 'F')
                LIMIT  1
            ) a ON TRUE
            WHERE  r.meet_id = %(m)s AND r.source = %(src)s
            GROUP  BY r.div_id, m.division, mt.division_distances,
                      dov.distance, m.distance
            ORDER  BY r.div_id
        """, {"m": int(meet_id), "src": source})
        return [{"div_id": r["div_id"],
                 "label": r.get("division") or f"Race {r['div_id']}",
                 "distance": r.get("distance"),
                 "gender": r.get("gender")} for r in cur.fetchall()]
    cur.execute("""
        SELECT r.div_id, min(m.division) AS division
        FROM   results_tf r
        LEFT JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                            AND m.source = r.source
        WHERE  r.meet_id = %(m)s AND r.source = %(src)s
        GROUP  BY r.div_id ORDER BY r.div_id
    """, {"m": int(meet_id), "src": source})
    return [{"div_id": r["div_id"],
             "label": r.get("division") or f"Division {r['div_id']}",
             "distance": None, "gender": genderOfLabel(r.get("division"))}
            for r in cur.fetchall()]


def lastEditionField(cur, meet_id, div_id, sport, source=None,
                     event_id=None):
    """(originals, basis) for a meet with no results: the last edition's
    runners of the race `div_id` maps to, in _exactField's shape, and what
    was borrowed --

        {"kind": "last_edition", "meet_id", "date", "meet_name", "matched"}
        {"kind": "none"}       no posted meet, or no earlier edition
        None                   the meet HAS results (an empty division of
                               a meet that ran): nothing borrowed

    ★ THE RUNNERS ARE _exactField's OF THE EDITION, so meetField's current
      squads, caps, gender, level and state all work on them unchanged --
      they are "who came to this meet", one running back.

    ★ AND FOR A TRACK EVENT, LAST YEAR'S SAME EVENT (2026-10-10). Event ids
      are per meet, so the upcoming 1600 is matched to the edition's events
      of the same distance (and gender, where both say one) inside the
      divisions mapRace pairs it with; who ran THOSE is the field. No such
      event last year is an empty field, never the whole division."""
    from predict import _exactField
    none = {"kind": "none"}
    # ! ONLY A MEET THAT HAS NOT RUN. A meet that ran and one empty division
    #   of it (a JV race nobody entered) is an empty race, not an unposted
    #   field -- borrowing last year's teams for it would be inventing.
    table = "results" if (sport or "XC").upper() == "XC" else "results_tf"
    cur.execute(f"SELECT 1 FROM {table} WHERE meet_id = %(m)s "
                f"AND (%(src)s::text IS NULL OR source = %(src)s) LIMIT 1",
                {"m": int(meet_id), "src": source})
    if cur.fetchone():
        return [], None
    up = upcomingMeet(cur, meet_id, sport, source)
    if up is None:
        return [], none
    ed = findLastEdition(cur, meet_id, sport, up)
    if ed is None:
        return [], none
    src = up["source"]
    race = None
    if div_id is not None:
        race = next((r for r in up["races"]
                     if str(r["div_id"]) == str(div_id)), None)
    divs, how = (None, "whole")
    if race is not None:
        divs, how = mapRace(race, up["races"],
                            editionRaces(cur, ed["meet_id"], sport, src))
    if event_id is not None and (sport or "XC").upper() == "TF":
        from predict import _tfEventRow, tfEventInfo
        ev = _tfEventRow(cur, meet_id, div_id, event_id, src) or {}
        metres, gender = tfEventInfo(ev.get("event_short"), ev.get("division"))
        originals, seen = [], set()
        if metres:
            for d, evs in _editionEvents(cur, ed["meet_id"], src, divs,
                                         metres, gender).items():
                for r in _exactField(cur, ed["meet_id"], d, sport,
                                     source=src, event_id=evs):
                    if r["person_id"] not in seen:
                        seen.add(r["person_id"])
                        originals.append(r)
        return originals, {"kind": "last_edition", "meet_id": ed["meet_id"],
                           "date": str(ed["date"])[:10],
                           "meet_name": ed["name"], "matched": "event"}
    if divs is None:
        originals = _exactField(cur, ed["meet_id"], None, sport, source=src)
    else:
        originals, seen = [], set()
        for d in divs:
            for r in _exactField(cur, ed["meet_id"], d, sport, source=src):
                if r["person_id"] not in seen:
                    seen.add(r["person_id"])
                    originals.append(r)
    return originals, {"kind": "last_edition", "meet_id": ed["meet_id"],
                       "date": str(ed["date"])[:10], "meet_name": ed["name"],
                       "matched": how}


# Purpose:   {div_id: [event_id, ...]} of an edition's track events at the
#            same distance as `metres` (and of `gender` where both name one),
#            within `divs` (None: every division).
# ! THE SAME DISTANCE, TO THE METRE: a 1600 is not the mile, and the mile is
#   not the 1500 -- each is its own race with its own entrants.
def _editionEvents(cur, ed_meet, source, divs, metres, gender):
    from predict import tfEventInfo
    cur.execute("""
        SELECT r.div_id, r.event_id, min(r.event_short) AS event_short,
               min(m.division) AS division
        FROM   results_tf r
        LEFT JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                            AND m.event_id = r.event_id
                            AND m.source = r.source
        WHERE  r.meet_id = %(m)s AND r.source = %(src)s
          AND  (%(divs)s::bigint[] IS NULL OR r.div_id = ANY(%(divs)s))
          AND  COALESCE(r.is_field, 0) = 0 AND COALESCE(r.is_relay, 0) = 0
        GROUP  BY r.div_id, r.event_id
    """, {"m": int(ed_meet), "src": source,
          "divs": [int(d) for d in divs] if divs else None})
    out = {}
    for r in cur.fetchall():
        m2, g2 = tfEventInfo(r.get("event_short"), r.get("division"))
        if not m2 or round(m2) != round(metres):
            continue
        if gender and g2 and g2 != gender:
            continue
        out.setdefault(r["div_id"], []).append(r["event_id"])
    return out


def upcomingRaces(cur, meet_id, sport, source=None):
    """(races, meta) for /api/predict/races when the meet has no results:
    its posted races -- n_results 0, the gender the last edition gives a
    label that does not say -- and {date, name, source}; ([], None)
    when nothing is posted under the id."""
    up = upcomingMeet(cur, meet_id, sport, source)
    if up is None:
        return [], None
    races = up["races"]
    if any(not r.get("gender") for r in races):
        ed = findLastEdition(cur, meet_id, sport, up)
        if ed is not None:
            edition = editionRaces(cur, ed["meet_id"], sport, up["source"])
            # ! ALL READ BEFORE ANY IS WRITTEN: the siblings mapRace pairs
            #   are the races whose label gave no gender
            got = [raceGender(r, races, edition) for r in races]
            for r, g in zip(races, got):
                r["gender"] = g
    out = [{"div_id": r["div_id"], "label": r["label"],
            "distance": r.get("distance"), "gender": r.get("gender"),
            "n_results": 0} for r in races]
    return out, {"date": up.get("date"), "name": up.get("name"),
                 "source": up["source"]}
