"""
athlete_chart_data.py -- the chart-data builder for the athlete page.

Drop into racecast/ and import, or paste build_chart_data into app.py beside
the other athlete helpers.

In app.py:

    from athlete_chart_data import build_chart_data

Then in the athlete() route, AFTER step 1 (the format_time loop):

    chart_data = build_chart_data(races)

and add one keyword to render_template:

    return render_template("athlete.html",
                           athlete=athlete,
                           xc_seasons=xc_seasons,
                           tf_seasons=tf_seasons,
                           tf_dists=tf_dists,
                           yearly=yearly_ordered,
                           chart_data=chart_data)      # <-- new

★ IF YOU FORGET THAT LAST LINE the page dies with
  "Object of type Undefined is not JSON serializable" -- Jinja resolves the
  missing name to Undefined and |tojson cannot encode it.

★ IT MUST RUN AFTER THE format_time LOOP. That loop rewrites race["result"]
  from raw seconds into "15:20.1", and the tooltip shows that value. Built
  before it, every tooltip reads "920.1".
"""

METERS_PER_MILE = 1609.34


def _pace_per_mile(time_seconds, distance_meters):
    """
    Seconds per mile, or None when it cannot be computed.

    Returns None rather than guessing, because the caller then drops the point
    entirely -- a missing distance should leave a gap, not an invented value.

    The 400m floor rejects the sentinel and typo distances that reach `meets`
    (0, 1, 5). Dividing by those yields paces in the hundreds of thousands,
    which blows out the y-axis and flattens every real point into a line.
    """
    if not time_seconds or not distance_meters:
        return None
    try:
        distance = float(distance_meters)
        seconds = float(time_seconds)
    except (TypeError, ValueError):
        return None

    if distance < 400 or seconds <= 0:
        return None

    return seconds / (distance / METERS_PER_MILE)


def _year_of(date_text):
    """Season year from a YYYY-MM-DD string, or None.

    Sliced from the string rather than parsed into a date: a Date object would
    introduce a timezone that could shift a race to the previous year, and the
    only thing needed here is four characters.
    """
    if not date_text or len(date_text) < 4:
        return None
    try:
        return int(date_text[:4])
    except ValueError:
        return None


def _point(race, value):
    """
    One chart point.

    Short keys because this is serialized for every race an athlete ever ran --
    "speed_rating" repeated 200 times is bytes on the wire for nothing.

    rid  the season tables anchor each row as id="race-<result_id>", so a dot
         can scroll to the race it represents.
    y    the season year. athlete-charts.js draws a divider wherever this
         changes and labels the span with it -- that is the whole x-axis.
    sp   the sport, so the combined chart can colour XC and TF differently.
    """
    return {
        "d": race["date"],
        "v": round(float(value), 4),
        "meet": race.get("meet"),
        "result": race.get("result"),
        "rid": race.get("result_id"),
        "y": _year_of(race["date"]),
        "sp": race.get("sport"),
    }


def build_chart_data(races):
    """
    Races -> the JSON blob athlete.html embeds.

        {
          "all_rating": [point, ...],       XC + TF together, by date
          "xc_rating":  [point, ...],
          "xc_pace":    [point, ...],
          "tf_rating":  [point, ...],
          "tf_dist":    { "1600m": [point, ...], "3200m": [...] }
        }

    Arguments: races -- the deduped, time-formatted list from the athlete route.
    Output:    dict, JSON-serializable, safe for |tojson.

    WHY IT REUSES `races` INSTEAD OF QUERYING: the route already loaded every
    race to render the tables. A second query would be a round trip for rows
    already in memory.

    ALL SERIES ARE SORTED BY DATE HERE. The route hands races over newest-first,
    and the charts space points evenly along the x-axis by INDEX -- so the order
    of the list IS the order on the chart. Sorting client-side would work too,
    but doing it once here means every consumer gets it right.

    ⚠ FIELD EVENTS ARE EXCLUDED from the per-distance charts. A shot put mark is
      metres sharing a column with running times in seconds; one axis cannot
      hold both. They keep their speed_rating line, which is comparable.
    """
    data = {
        "all_rating": [],
        "xc_rating": [],
        "xc_pace": [],
        "tf_rating": [],
        "tf_dist": {},
    }

    for race in races:
        if not race.get("date"):
            continue

        sport = race.get("sport")
        rating = race.get("speed_rating")

        if rating is not None:
            point = _point(race, rating)
            data["all_rating"].append(point)
            if sport == "XC":
                data["xc_rating"].append(point)
            elif sport == "TF":
                data["tf_rating"].append(point)

        if sport == "XC":
            # get_races selects m.distance::text AS event for XC -- the "event"
            # IS the race distance in metres, which is why pace needs no extra
            # column.
            pace = _pace_per_mile(race.get("time_raw"), race.get("event"))
            if pace is not None:
                data["xc_pace"].append(_point(race, pace))

        elif sport == "TF" and not race.get("is_field"):
            # For TF, "event" is event_short ("1600m") -- exactly the key
            # tf_distances() produces for the sidebar slots, so chart keys and
            # markup keys line up without a second mapping.
            event = race.get("event")
            time_raw = race.get("time_raw")
            if event and time_raw:
                data["tf_dist"].setdefault(event, []).append(
                    _point(race, time_raw))

    by_date = lambda p: p["d"]
    data["all_rating"].sort(key=by_date)
    data["xc_rating"].sort(key=by_date)
    data["xc_pace"].sort(key=by_date)
    data["tf_rating"].sort(key=by_date)
    for series in data["tf_dist"].values():
        series.sort(key=by_date)

    return data