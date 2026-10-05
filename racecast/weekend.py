"""weekend.py -- "Coming up": the meets on the calendar for the next seven
days, each one a link into the predictions page (owner, 2026-10-05: "this
weekend", a front door to predicting the meets about to run).

★ THE FEEDS POST MEETS BEFORE THEY RUN. The forward scrape walks new meet
  ids until it hits nothing, and a meet the host has set up is a page with
  its date and its races and no results yet: saveMeet keeps the date in
  meets.meet_date (XC), meets_tf_meta.meet_date (track), and tfrrs keeps
  meets_tfrrs.date. So the calendar is already in the database; it was only
  never read forward.

! NO ENTRIES, SO NO FIELD. A posted meet does not say who is running. The
  link opens the predictions page's "This year" mode, which builds the
  field from the teams that ran the course and today's rosters -- the same
  page anyone reaches by searching the meet.

! ORDERED BY HOW MANY RACES ARE POSTED. With no field, the number of
  divisions a host has set up is the size signal there is: an invitational
  posts a dozen, a dual meet one or two.

! DATES ARE TEXT. Compared as ISO strings behind a regex guard, the same
  way panels.py reads them (the corpus holds junk years).
"""
import datetime

import ttlcache

DAYS = 7

_ISO = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$"

_SQL = {
    # anet cross country: one meets row per division
    "anet_XC": f"""
        SELECT meet_id, min(meet_name) AS meet_name, min(meet_date) AS date,
               min(course_name) AS venue, min(state) AS state,
               count(*) AS n_races, bit_or(COALESCE(level_mask, 0)) AS level_mask
        FROM   meets
        WHERE  meet_date ~ '{_ISO}' AND meet_date BETWEEN %(lo)s AND %(hi)s
          AND  meet_name IS NOT NULL
        GROUP  BY meet_id""",
    # anet track: one meta row per meet; races from meets_tf
    "anet_TF": f"""
        SELECT m.meet_id, m.meet_name, m.meet_date AS date, m.venue_name AS venue,
               m.state, COALESCE(d.n, 0) AS n_races, COALESCE(m.level_mask, 0) AS level_mask
        FROM   meets_tf_meta m
        LEFT JOIN (SELECT meet_id, count(*) AS n FROM meets_tf GROUP BY meet_id) d
               ON d.meet_id = m.meet_id
        WHERE  m.meet_date ~ '{_ISO}' AND m.meet_date BETWEEN %(lo)s AND %(hi)s
          AND  m.meet_name IS NOT NULL""",
    # tfrrs (college), both sports in one table
    "tfrrs": f"""
        SELECT meet_id, sport, min(meet_name) AS meet_name, min(date) AS date,
               min(venue_name) AS venue, min(state) AS state, count(*) AS n_races
        FROM   meets_tfrrs
        WHERE  date ~ '{_ISO}' AND date BETWEEN %(lo)s AND %(hi)s
          AND  meet_name IS NOT NULL
        GROUP  BY meet_id, sport""",
}


def _level(mask, source):
    if source == "tfrrs":
        return "College"
    mask = mask or 0
    hs, col = bool(mask & 4), bool(mask & 8)
    return "HS & college" if hs and col else "College" if col else "HS" if hs else ""


def predictHref(m):
    """The predictions page, opened on this meet in "This year" mode. The
    source rides along: anet and tfrrs ids collide."""
    href = f"/predictions?meet_id={m['meet_id']}&sport={m['sport']}"
    if m["source"] == "tfrrs":
        href += "&src=tfrrs"
    return href


def comingUp(cur, today=None):
    """[{date, label, meets: [...]}] for today and the next DAYS-1 days,
    biggest meets first within a day. A table a box does not have is
    skipped, never raised."""
    import psycopg2
    today = today or datetime.date.today()
    p = {"lo": today.isoformat(),
         "hi": (today + datetime.timedelta(days=DAYS - 1)).isoformat()}
    meets = []
    for key, sql in _SQL.items():
        try:
            cur.execute("SAVEPOINT wk")
            cur.execute(sql, p)
            rows = cur.fetchall()
            cur.execute("RELEASE SAVEPOINT wk")
        except psycopg2.Error:
            cur.execute("ROLLBACK TO SAVEPOINT wk")
            continue
        for r in rows:
            r = dict(r)
            source = "tfrrs" if key == "tfrrs" else "anet"
            sport = (r.get("sport") or key.split("_")[1]).upper()
            meets.append({"meet_id": r["meet_id"], "sport": sport, "source": source,
                          "name": r["meet_name"], "date": r["date"],
                          "venue": r.get("venue"), "state": r.get("state"),
                          "n_races": int(r.get("n_races") or 0),
                          "level": _level(r.get("level_mask"), source)})
    for m in meets:
        m["predict_href"] = predictHref(m)
    days = []
    for i in range(DAYS):
        d = today + datetime.timedelta(days=i)
        on = sorted((m for m in meets if m["date"] == d.isoformat()),
                    key=lambda m: (-m["n_races"], m["name"] or ""))
        if on:
            label = ("Today" if i == 0 else "Tomorrow" if i == 1
                     else d.strftime("%A, %b ") + str(d.day))
            days.append({"date": d.isoformat(), "label": label, "meets": on})
    return days


def comingUpCached(getConn, today=None):
    """comingUp behind the page cache, one compute per day per worker.
    A failure renders as no block, not an error page."""
    today = today or datetime.date.today()

    def compute():
        import psycopg2.extras
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                out = comingUp(cur, today)
                conn.rollback()
                return out
    try:
        return ttlcache.get(f"coming_up:{today.isoformat()}", compute)[0]
    except Exception as exc:                                     # noqa: BLE001
        print(f"coming up: {type(exc).__name__}: {exc}", flush=True)
        return []
