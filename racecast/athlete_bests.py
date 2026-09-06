"""
athlete_bests.py -- bests panels for the athlete page.

Drop into racecast/ and import, or paste both public functions into app.py.

In app.py:

    from athlete_bests import all_time_bests, season_bests_flat

In the athlete() route, AFTER the format_time loop and AFTER `seasons` is
built (it needs season.rating):

    alltime = all_time_bests(races)
    season_best_list = season_bests_flat(races, seasons)

and pass both to render_template:

    return render_template("athlete.html",
                           ...,
                           alltime=alltime,
                           season_bests=season_best_list)

`yearly` / `yearly_ordered` is no longer used by the template and can be
dropped from the route once this is in.

★ MUST RUN AFTER THE format_time LOOP. That loop rewrites race["result"] from
  raw seconds into "15:20.1", and these panels display that value. Built
  before it, every best reads "920.1".
"""

import re

# The engine's TF event parser -- prices '1mile', "Men's Mile", '10-km' and
# "Men's 3000 Meters" onto one number, which is exactly the dedupe this page
# needs. app.py puts engine/ on sys.path before importing us; standalone use
# falls back to the textual cleanup below.
try:
    from event_parse import distanceFromEventShort
except Exception:
    distanceFromEventShort = None

# The field-event mark parser and event normaliser (racecast/marks.py). A
# field best is the LONGEST/HIGHEST mark, in metres, and only a mark the
# parser reads with confidence can be one; a refused mark is not a best.
try:
    from marks import parseMark, normalizeFieldEvent, saneMark
except Exception:                                    # standalone use
    parseMark = normalizeFieldEvent = saneMark = None

# Display labels for the field keys marks.normalizeFieldEvent returns, in
# the order a meet programme lists them: jumps, then throws.
_FIELD_LABELS = {
    "high_jump": "High Jump", "pole_vault": "Pole Vault",
    "long_jump": "Long Jump", "triple_jump": "Triple Jump",
    "shot_put": "Shot Put", "discus": "Discus", "javelin": "Javelin",
    "hammer": "Hammer", "weight_throw": "Weight Throw",
}
_FIELD_ORDER = {k: i for i, k in enumerate(_FIELD_LABELS)}


def fieldMark(race):
    """(metres, label) for a field-event row, or (None, None).

    The row's `result` is the mark text (app.py selects r.mark for field
    rows). Bests need a number: a mark the parser refuses, a sentinel (NH,
    FOUL), or a value outside the event's physical range never becomes a
    best -- the same refusal contract the marks board will use."""
    if parseMark is None or not race.get("is_field"):
        return None, None
    key = normalizeFieldEvent(race.get("event"))
    if key is None:
        return None, None
    metres, kind = parseMark(race.get("result"))
    if metres is None or not saneMark(key, metres):
        return None, None
    return float(metres), _FIELD_LABELS.get(key, key)


def _is_better_mark(race, current):
    """Longer or higher wins. Compares the metres stamped by fieldMark."""
    if race.get("_mark_m") is None:
        return False
    if current is None:
        return True
    return race["_mark_m"] > current["_mark_m"]



def _event_sort_key(event):
    """
    Order events by distance, shortest first.

    Event labels are inconsistent -- XC carries a bare number ("4828"), TF
    carries things like "1600m", "3200M", "2MILES", "4X400". Pulling the
    leading digits covers almost all of them; anything with no leading number
    sorts last, alphabetically, rather than crashing or landing arbitrarily.

    "2MILES" sorts as 2, which puts it before the 800m. Wrong in principle,
    harmless in practice: an athlete rarely has both, and the alternative is a
    unit-parsing table that would be wrong in new ways as soon as a scraper
    invents another label.
    """
    if not event:
        return (2, "")
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(event))
    if match:
        return (0, float(match.group(1)))
    return (1, str(event).lower())


# Round/heat qualifiers that split one event into several "events" -- an
# athlete's 1600m prelim and final are two races but ONE event, and keying
# the bests panels on the raw label gave them two PR rows.
# ⚠ DISPLAY GROUPING ONLY. The race rows below the panels keep their raw
#   label, where "Prelims" is information. Mirrors the ROUND words of
#   engine/event_parse's _TRAILING_NOISE; the parser's full list also eats
#   "Invitational" and "Open", which here are usually meet names, not rounds.
_ROUND_NOISE = re.compile(
    r"\s*[\(\[].*?[\)\]]|"
    r"\s*\b(section|sect|heat|flight|round|prelims?|semis?|finals?|"
    r"trials?)\b.*$",
    re.IGNORECASE)

# The tfrrs spellings the anet forms collide with: a gender prefix on every
# event, and units written out ("3000 Meters" vs "3000m").
# ! LONGEST ALTERNATIVE FIRST. Regex alternation takes the first branch that
#   matches, so "men|men's" eats "Men" out of "Men's" and leaves "'s 200m".
_GENDER_LEAD = re.compile(
    r"^\s*(women's|womens|women|girl's|girls|female|"
    r"men's|mens|men|boy's|boys|male)\b[\s'’]*", re.IGNORECASE)
_METERS_WORD = re.compile(r"(\d+(?:,\d{3})?)\s*met(?:er|re)s?\b", re.IGNORECASE)


def _canonEvent(event):
    """'1600m Prelims 2', "Men's 1600 Meters" and '1600m' -> one bests row."""
    canon = _GENDER_LEAD.sub("", str(event))
    canon = _ROUND_NOISE.sub("", canon)
    canon = _METERS_WORD.sub(lambda m: m.group(1).replace(",", "") + "m", canon)
    canon = canon.strip(" -– - ·:")
    return canon or str(event).strip()


# Display names for parser-priced distances that are not round metres.
_MILE_LABELS = {805: "880y", 1609: "Mile", 3219: "2 Mile",
                4828: "3 Mile", 8047: "5 Mile"}


def _tfEvent(event):
    """(sort_metres | None, display_label) for one TF event string.

    Distance races go through the engine's parser, so both feeds' spellings
    land on one key with a real sort position. Sprints, hurdles and relays
    come back None there and keep a cleaned form of their own label. The
    steeple is rejected by the parser on purpose (it is not priced), but the
    feeds still spell it two ways ('3ksteeple', "Women's 3000 Steeplechase"),
    so it is keyed here by its number -- offset +0.5 so it sorts just after
    the flat race of the same distance instead of merging with it.
    """
    raw = str(event)
    if re.search(r"steeple", raw, re.IGNORECASE):
        num = re.search(r"(\d+(?:\.\d+)?)", raw)
        metres = 3000.0
        if num:
            metres = float(num.group(1))
            if metres < 10:
                metres *= 1000
        return metres + 0.5, f"{int(round(metres))}m Steeple"
    if distanceFromEventShort is not None:
        metres, _gender = distanceFromEventShort(raw)
        if metres is not None:
            label = (_MILE_LABELS.get(int(round(metres)))
                     or f"{int(round(metres))}m")
            return float(metres), label
    return None, _canonEvent(raw)


def _orderEvents(bucket):
    """The bests dict in display order: parser-priced running events by
    their real metres (a Mile between the 1600 and the 3000, the steeple
    beside its flat race), then the leading-digits heuristic for the rest,
    then the field events in programme order (jumps, then throws).
    Consumes the bucket's _ev_m and _field scratch keys."""
    events = bucket["events"]
    ev_m = bucket.pop("_ev_m", {})
    field = bucket.pop("_field", set())
    inv_label = {v: k for k, v in _FIELD_LABELS.items()}

    def key(k):
        if k in field:
            return (1, _FIELD_ORDER.get(inv_label.get(k), 99), k)
        if k in ev_m:
            return (0, ev_m[k], k)
        return (0,) + _event_sort_key(k)

    return {k: events[k] for k in sorted(events, key=key)}


def _is_better_time(race, current):

    """Faster wins. A race with no numeric time can never be a best."""
    if race.get("time_raw") is None:
        return False
    if current is None:
        return True
    return race["time_raw"] < current["time_raw"]


def _shownRating(race):
    """The number the page SHOWS for this race: the HS-equivalent where it
    was stamped, else the own-pool rating. None when unrated.

    ★ THE BEST RACE IS PICKED ON THE SHOWN SCALE (issue 142). A career that
      spans pools (HS then college) compared own-pool ratings, so a 128 HS
      race beat a college 121 that reads 132 on the HS-equivalent view the
      page opens on. Inside one pool the factor is a constant and this
      changes nothing; across pools it agrees with pool_view.sortByShown.
    """
    v = race.get("hs_rating")
    if v is None:
        v = race.get("speed_rating")
    return None if v is None else float(v)


def _is_better_rating(race, current):
    """Higher wins, on the shown scale. A race with no rating can never be
    a best."""
    v = _shownRating(race)
    if v is None:
        return False
    if current is None:
        return True
    return v > _shownRating(current)


def _empty_sport():
    # _rating_sum / _rating_n accumulate the mean; they are stripped before
    # returning so the template never sees them. The _hs twins accumulate the
    # HS-equivalent mean for the pool-view toggle -- counted separately
    # because a race can be rated on its own scale yet have no HS factor.
    return {"events": {}, "rating": None, "avg_rating": None,
            "avg_rating_hs": None,
            "_rating_sum": 0.0, "_rating_n": 0,
            "_hs_sum": 0.0, "_hs_n": 0}


def all_time_bests(races):
    """
    Career bests, split by sport.

        {
          "rating": race or None,          best rated race, EITHER sport
          "XC": {"events": {ev: race}, "rating": race or None},
          "TF": {"events": {ev: race}, "rating": race or None},
        }

    Arguments: races -- the deduped, time-formatted list from the route.
    Output:    dict of races (not values), so the template can link each best
               to #race-<result_id>.

    ⚠ FIELD EVENTS ARE EXCLUDED from the per-event bests. `time_raw` is NULL
      for them and `result` holds a mark in metres, so "fastest" is meaningless
      and a min() over mixed units would be nonsense. They still count toward
      the rating bests, which are comparable across everything.
    """
    out = {"rating": None, "XC": _empty_sport(), "TF": _empty_sport()}

    for race in races:
        sport = race.get("sport")
        if sport not in ("XC", "TF"):
            continue

        bucket = out[sport]

        if _is_better_rating(race, bucket["rating"]):
            bucket["rating"] = race
        if _is_better_rating(race, out["rating"]):
            out["rating"] = race

        if race.get("speed_rating") is not None:
            bucket["_rating_sum"] += float(race["speed_rating"])
            bucket["_rating_n"] += 1
        if race.get("hs_rating") is not None:
            bucket["_hs_sum"] += float(race["hs_rating"])
            bucket["_hs_n"] += 1

        if race.get("is_field"):
            # ★ FIELD EVENTS ARE BESTS TOO (owner, 2026-09-02): the longest
            #   or highest mark per event, listed after the running events.
            #   Only a parsed, plausible mark can be one -- see fieldMark.
            metres, label = fieldMark(race)
            if metres is None:
                continue
            race["_mark_m"] = metres
            bucket.setdefault("_field", set()).add(label)
            if _is_better_mark(race, bucket["events"].get(label)):
                bucket["events"][label] = race
            continue

        event = race.get("event")
        if not event:
            continue
        if sport == "TF":
            sort_m, event = _tfEvent(event)
            if sort_m is not None:
                bucket.setdefault("_ev_m", {})[event] = sort_m
        else:
            event = _canonEvent(event)
        if _is_better_time(race, bucket["events"].get(event)):
            bucket["events"][event] = race

    for sport in ("XC", "TF"):
        bucket = out[sport]
        bucket["events"] = _orderEvents(bucket)


        # A flat mean over every rated race in the sport. Deliberately NOT the
        # engine's ability: that one is decay-weighted and outlier-trimmed
        # across a whole career and already shows at the top of the panel. This
        # is "what did they average", which is a different and simpler claim.
        if bucket["_rating_n"]:
            bucket["avg_rating"] = bucket["_rating_sum"] / bucket["_rating_n"]
        if bucket["_hs_n"]:
            bucket["avg_rating_hs"] = bucket["_hs_sum"] / bucket["_hs_n"]

        del bucket["_rating_sum"], bucket["_rating_n"]
        del bucket["_hs_sum"], bucket["_hs_n"]

    return out


def season_bests_flat(races, seasons):
    """
    One entry per (year, sport), newest first.

        [ (("2026", "TF"), {"events": {...}, "rating": race, "season_rating": float}), ... ]

    Arguments: races   -- the deduped, formatted race list.
               seasons -- the enriched dict from the route, keyed (year, sport),
                          whose values carry .rating (the engine's season rating).
    Output:    list of ((year, sport), bests) tuples.

    WHY FLAT INSTEAD OF NESTED BY YEAR: the old shape was
    {year: {sport: bests}}, which the template rendered as a year heading with
    sports inside it. Reading "2026" then "TF" as separate headings is one more
    level than the content needs -- "2026 TF" is a single thing, and flattening
    lets it be a single clickable heading pointing at #2026-TF.

    `season_rating` is looked up rather than recomputed. The route already
    derives it in enrich_seasons, and recomputing it here would be a second
    definition of the same number that could drift from the first.
    """
    buckets = {}

    for race in races:
        sport = race.get("sport")
        date = race.get("date")
        if sport not in ("XC", "TF") or not date:
            continue

        # ! THE SEASON LABEL THE ROUTE STAMPED, not date[:4] -- the academic
        #   year, shown as year+1 for TF. It must be the same key
        #   group_into_seasons used or the seasons.get() below finds nothing
        #   and every season rating renders as a dash. The date slice remains
        #   only as the fallback for a caller that never stamped labels.
        key = (race.get("season_label") or date[:4], sport)
        bucket = buckets.setdefault(key, {"events": {}, "rating": None})

        if _is_better_rating(race, bucket["rating"]):
            bucket["rating"] = race

        if race.get("is_field"):
            metres, label = fieldMark(race)
            if metres is None:
                continue
            race["_mark_m"] = metres
            bucket.setdefault("_field", set()).add(label)
            if _is_better_mark(race, bucket["events"].get(label)):
                bucket["events"][label] = race
            continue

        event = race.get("event")
        if not event:
            continue
        if sport == "TF":
            sort_m, event = _tfEvent(event)
            if sort_m is not None:
                bucket.setdefault("_ev_m", {})[event] = sort_m
        else:
            event = _canonEvent(event)
        if _is_better_time(race, bucket["events"].get(event)):
            bucket["events"][event] = race

    for key, bucket in buckets.items():
        bucket["events"] = _orderEvents(bucket)


        # seasons is keyed the same way. .get() rather than [] because a season
        # can exist in `races` and not in `seasons` -- enrich_seasons drops
        # anything it cannot rate, and a missing rating should render as a dash
        # rather than raise.
        season = seasons.get(key)
        bucket["season_rating"] = (
            season.get("rating") if isinstance(season, dict) else
            getattr(season, "rating", None)
        )
        bucket["season_rating_hs"] = (
            season.get("rating_hs") if isinstance(season, dict) else
            getattr(season, "rating_hs", None)
        )

    return sorted(buckets.items(), reverse=True)