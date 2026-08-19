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


def _is_better_time(race, current):
    """Faster wins. A race with no numeric time can never be a best."""
    if race.get("time_raw") is None:
        return False
    if current is None:
        return True
    return race["time_raw"] < current["time_raw"]


def _is_better_rating(race, current):
    """Higher wins. A race with no rating can never be a best."""
    if race.get("speed_rating") is None:
        return False
    if current is None:
        return True
    return race["speed_rating"] > current["speed_rating"]


def _empty_sport():
    # _rating_sum / _rating_n accumulate the mean; they are stripped before
    # returning so the template never sees them.
    return {"events": {}, "rating": None, "avg_rating": None,
            "_rating_sum": 0.0, "_rating_n": 0}


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

        if race.get("is_field"):
            continue

        event = race.get("event")
        if not event:
            continue
        if _is_better_time(race, bucket["events"].get(event)):
            bucket["events"][event] = race

    for sport in ("XC", "TF"):
        bucket = out[sport]
        events = bucket["events"]
        bucket["events"] = {
            k: events[k] for k in sorted(events, key=_event_sort_key)
        }

        # A flat mean over every rated race in the sport. Deliberately NOT the
        # engine's ability: that one is decay-weighted and outlier-trimmed
        # across a whole career and already shows at the top of the panel. This
        # is "what did they average", which is a different and simpler claim.
        if bucket["_rating_n"]:
            bucket["avg_rating"] = bucket["_rating_sum"] / bucket["_rating_n"]

        del bucket["_rating_sum"], bucket["_rating_n"]

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

        key = (date[:4], sport)
        bucket = buckets.setdefault(key, {"events": {}, "rating": None})

        if _is_better_rating(race, bucket["rating"]):
            bucket["rating"] = race

        if race.get("is_field"):
            continue

        event = race.get("event")
        if not event:
            continue
        if _is_better_time(race, bucket["events"].get(event)):
            bucket["events"][event] = race

    for key, bucket in buckets.items():
        events = bucket["events"]
        bucket["events"] = {
            k: events[k] for k in sorted(events, key=_event_sort_key)
        }

        # seasons is keyed the same way. .get() rather than [] because a season
        # can exist in `races` and not in `seasons` -- enrich_seasons drops
        # anything it cannot rate, and a missing rating should render as a dash
        # rather than raise.
        season = seasons.get(key)
        bucket["season_rating"] = (
            season.get("rating") if isinstance(season, dict) else
            getattr(season, "rating", None)
        )

    return sorted(buckets.items(), reverse=True)