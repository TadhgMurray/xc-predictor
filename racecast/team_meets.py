# Project: xc-predictor / racecast
# File:    team_meets.py
# Purpose: A team's LIKELY meets for the rest of the season, and the same list
#          as a calendar a phone subscribes to (owner, 2026-10-10, approved:
#          "Calendar (ICS) export of a team's likely meets").
#
#              /school/<school>/meets.ics[?state=OR&level=hs&sport=XC]
#
# ★ "LIKELY", BECAUSE NOBODY POSTS ENTRIES. The feeds post a meet before it
#   runs -- its date, its races, its venue (weekend.py reads them forward) --
#   but never who is coming. What IS known is who came last time: a posted
#   meet's previous edition (last_edition.py, the rule the predictions page
#   already borrows its field by). A team that ran the last edition of a meet
#   on this season's calendar is likely to run this one, and every event says
#   "likely" in its title, its description and its status (TENTATIVE).
#
# ★ THE SAME TWO RULES, NOT A THIRD. The posted calendar is weekend._SQL (the
#   Coming up list's own queries, over the rest of the season instead of a
#   week) and the previous edition is last_edition.findLastEdition, unchanged.
#   This module only joins them to a team: a posted meet whose normalised
#   name (last_edition.editionName) is one the team has raced is a CANDIDATE,
#   and it is LIKELY only when the edition findLastEdition picks -- the most
#   recent earlier running, same venue first -- is one the team ran. Matching
#   on the name alone would put every "Twilight Invitational" in forty states
#   on one team's calendar.
#
# ! THE REST OF THE SEASON, NOT A WINDOW. The calendar runs from today to the
#   last day of the academic year (season_year.ACADEMIC_START_MONTH): the
#   season the boards are showing. A meet posted for next season belongs to
#   next season's calendar, and its "last edition" would be this season's
#   running, which has not happened.
#
# ! ONE COMPUTE PER TEAM PER DAY PER WORKER (ttlcache): the posted calendar is
#   shared by every team, the per-team part is a name filter in Python plus
#   one findLastEdition per candidate. The ICS response itself is an ordinary
#   public page and caches at the edge like the school page it hangs off.
import datetime
import sys

import ttlcache

sys.path.insert(0, "engine")

# the calendar's own product id, and the domain every event uid is minted in
PRODID = "-//Racecast//Team meets//EN"
UID_DOMAIN = "racecast.co"
# RFC 5545 3.1: content lines are folded at 75 octets
_ICS_LINE = 75
_TTL = 6 * 3600.0


# ------------------------------------------------------------------ #
#  the season's end, and the posted calendar
# ------------------------------------------------------------------ #

def seasonEnd(today):
    """The last day of the academic year `today` is in: the day before the
    next season opens (season_year.ACADEMIC_START_MONTH, the month every
    stored season year turns over on)."""
    from season_year import ACADEMIC_START_MONTH, academicYear
    nxt = datetime.date(academicYear(today) + 1, ACADEMIC_START_MONTH, 1)
    return nxt - datetime.timedelta(days=1)


def _feedKeys(sport):
    """weekend._SQL's keys for one sport: anet's own table and tfrrs."""
    return ("anet_XC", "tfrrs") if sport == "XC" else ("anet_TF", "tfrrs")


def postedMeets(cur, sport, today):
    """Every meet posted for today .. seasonEnd(today) in one sport, as
    weekend.comingUp shapes them: {meet_id, sport, source, name, date,
    venue, state, n_races, level}. A table a box does not have is skipped,
    never raised (weekend's own rule)."""
    import psycopg2
    import weekend as W
    p = {"lo": today.isoformat(), "hi": seasonEnd(today).isoformat()}
    out = []
    for key in _feedKeys(sport):
        try:
            cur.execute("SAVEPOINT tm")
            cur.execute(W._SQL[key], p)
            rows = cur.fetchall()
            cur.execute("RELEASE SAVEPOINT tm")
        except psycopg2.Error:
            cur.execute("ROLLBACK TO SAVEPOINT tm")
            continue
        for r in rows:
            r = dict(r)
            source = "tfrrs" if key == "tfrrs" else "anet"
            sp = (r.get("sport") or key.split("_")[-1]).upper()
            if sp != sport:
                continue
            out.append({"meet_id": r["meet_id"], "sport": sp, "source": source,
                        "name": r["meet_name"], "date": str(r["date"])[:10],
                        "venue": r.get("venue"), "state": r.get("state"),
                        "n_races": int(r.get("n_races") or 0),
                        "level": W._level(r.get("level_mask"), source)})
    return out


# ------------------------------------------------------------------ #
#  the rule (pure: tests/test_team_meets.py)
# ------------------------------------------------------------------ #

def candidates(posted, team_meet_names):
    """The posted meets whose normalised name is one the team has raced.
    A prefilter only: likelyFrom decides."""
    from last_edition import editionName
    want = {editionName(n) for n in team_meet_names if n}
    want.discard("")
    return [m for m in posted if editionName(m.get("name")) in want]


def likelyFrom(cands, edition_of, team_ran):
    """The likely meets among `cands`, date order.

    edition_of(meet) -> the previous edition {meet_id, name, date} or None
    team_ran(meet, edition) -> True when the team has results there

    ★ THE TEAM MUST HAVE RUN THE EDITION findLastEdition PICKS, not any
      running of the name: a meet that moved from the team's state to
      another, or a namesake in another state, picks a running the team was
      not at, and the meet is not on their calendar."""
    out = []
    for m in cands:
        ed = edition_of(m)
        if ed is None or not team_ran(m, ed):
            continue
        out.append(dict(m, edition={"meet_id": ed["meet_id"], "name": ed.get("name"),
                                    "date": str(ed.get("date") or "")[:10] or None}))
    out.sort(key=lambda m: (m["date"], m["name"] or ""))
    return out


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _postedCached(cur, sport, today):
    """The posted calendar for one sport, shared by every team for a day."""
    return ttlcache.get(("team_meets_posted", sport, today.isoformat()),
                        lambda: postedMeets(cur, sport, today), ttl=_TTL)[0]


# weekend._level's words, by the school page's ?level= (the claim levels)
_LEVEL_WORDS = {"hs": ("HS",), "ms": ("HS",), "college": ("College",)}


def levelFits(meet_level, level):
    """A posted meet's level (weekend._level: "HS", "College", "HS & college"
    or "" when the host did not say) against the team's. An unsaid level
    fits everyone: the feed not knowing is not a reason to drop the meet."""
    if not level or not meet_level or level not in _LEVEL_WORDS:
        return True
    return meet_level == "HS & college" or meet_level in _LEVEL_WORDS[level]


def _ranThere(cur, school, edition, source, sport):
    table = "results" if sport == "XC" else "results_tf"
    cur.execute(f"SELECT 1 AS x FROM {table} WHERE meet_id = %s AND source = %s "
                f"AND school = %s LIMIT 1", (int(edition["meet_id"]), source, school))
    return cur.fetchone() is not None


def likelyMeets(cur, school, state, sport="XC", today=None, level=None):
    """The team's likely meets from today to the season's end, each with
    the edition it was inferred from and its predictions link."""
    import weekend as W
    from last_edition import findLastEdition
    from school import schoolMeets
    from school_identity import primaryState
    today = today or datetime.date.today()
    sport = (sport or "XC").upper()
    posted = [m for m in _postedCached(cur, sport, today)
              if levelFits(m.get("level"), level)]
    if not posted:
        return []
    primary = primaryState(school)
    raced = schoolMeets(cur, school, sport, state=state, primary=primary)
    ran_ids = {int(r["meet_id"]) for r in raced if r.get("meet_id") is not None}
    cands = candidates(posted, [r.get("meet_name") for r in raced])

    def edition_of(m):
        up = {"source": m["source"], "name": m["name"], "date": m["date"],
              "venue": m.get("venue"), "state": m.get("state")}
        try:
            return findLastEdition(cur, m["meet_id"], sport, up)
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            return None

    def team_ran(m, ed):
        # ! THE ID AND THE FEED: the state-scoped meet list carries no source
        #   (ranking_results has none), and anet and tfrrs ids collide
        return int(ed["meet_id"]) in ran_ids and _ranThere(cur, school, ed, m["source"], sport)

    out = likelyFrom(cands, edition_of, team_ran)
    for m in out:
        m["predict_href"] = W.predictHref(m)
    return out


def likelyMeetsCached(getConn, school, state, sport="XC", today=None, level=None):
    """likelyMeets behind ttlcache, one compute per team per day per worker.
    A failure is an empty calendar, never an error page."""
    today = today or datetime.date.today()

    def compute():
        import psycopg2.extras
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                out = likelyMeets(cur, school, state, sport, today, level)
                conn.rollback()
                return out
    try:
        return ttlcache.get(("team_meets", school, state or "", level or "", sport,
                             today.isoformat()),
                            compute, ttl=_TTL)[0]
    except Exception as exc:                                     # noqa: BLE001
        print(f"team meets: {type(exc).__name__}: {exc}", flush=True)
        return []


# ------------------------------------------------------------------ #
#  the calendar file (pure)
# ------------------------------------------------------------------ #

def _esc(text):
    """RFC 5545 3.3.11 TEXT: backslash, semicolon, comma and newline."""
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545 3.1: lines longer than 75 octets continue on the next line
    after CRLF + one space, never splitting a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= _ICS_LINE:
        return line
    parts, cur, size = [], "", 0
    limit = _ICS_LINE
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append(cur)
            cur, size = "", 0
            limit = _ICS_LINE - 1          # the leading space counts
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n ".join(parts)


def _ymd(iso):
    return str(iso)[:10].replace("-", "")


def eventUid(m):
    return f"{m['source']}-{m['sport'].lower()}-{m['meet_id']}@{UID_DOMAIN}"


def icsCalendar(team_label, meets, origin, now=None):
    """The VCALENDAR text (CRLF lines) for a team's likely meets: one all-day,
    TENTATIVE event per meet, titled "Likely: <meet>", saying which running
    it was inferred from and linking to the meet's prediction."""
    now = now or datetime.datetime.utcnow()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_esc(team_label + ' - likely meets (Racecast)')}",
             f"X-WR-CALDESC:{_esc('Meets ' + team_label + ' ran last time, posted again this season. Entries are not posted, so each is likely, not confirmed.')}"]
    for m in meets:
        d = datetime.date.fromisoformat(str(m["date"])[:10])
        ed = m.get("edition") or {}
        why = (f"Likely, not confirmed: entries are not posted. {team_label} ran the last "
               f"edition ({ed.get('name') or 'the previous running'}"
               f"{', ' + ed['date'] if ed.get('date') else ''}).")
        url = f"{origin}{m['predict_href']}" if m.get("predict_href") else origin
        where = ", ".join(x for x in (m.get("venue"), m.get("state")) if x)
        lines += ["BEGIN:VEVENT", f"UID:{eventUid(m)}", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{_ymd(d.isoformat())}",
                  f"DTEND;VALUE=DATE:{_ymd((d + datetime.timedelta(days=1)).isoformat())}",
                  f"SUMMARY:{_esc('Likely: ' + (m.get('name') or 'Meet'))}",
                  f"DESCRIPTION:{_esc(why + ' Predict it: ' + url)}",
                  f"URL:{url}", "STATUS:TENTATIVE", "TRANSP:TRANSPARENT"]
        if where:
            lines.append(f"LOCATION:{_esc(where)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


# ------------------------------------------------------------------ #
#  the route
# ------------------------------------------------------------------ #

from flask import Blueprint, Response, abort, request  # noqa: E402

bp = Blueprint("team_meets", __name__)


def calHref(school, state=None, level=None, sport="XC"):
    """The calendar's URL for a team, the school page's identifiers."""
    from urllib.parse import quote, urlencode
    q = {"sport": sport}
    if state:
        q["state"] = state
    if level:
        q["level"] = level
    return f"/school/{quote(school, safe='/')}/meets.ics?{urlencode(q)}"


# ! A TEMPLATE GLOBAL TOO, so a page holding only a season row (my_page's
#   athlete card) builds the calendar link through this one spelling rather
#   than hand-writing /school/... (tests/test_school_logos.py OneHref).
bp.add_app_template_global(calHref, "cal_href")


@bp.route("/school/<path:school_name>/meets.ics")
def school_meets_ics(school_name):
    """The team's likely meets as a calendar. Public and cacheable like the
    school page: nothing here reads a session."""
    from database import getConn
    from panels import isTeamName
    from school_identity import primaryState, schoolLabelIn
    if not isTeamName(school_name):
        abort(404)
    sport = (request.args.get("sport") or "XC").strip().upper()
    if sport not in ("XC", "TF"):
        sport = "XC"
    state = (request.args.get("state") or "").strip().upper()[:2] or None
    if state and not state.isalpha():
        state = None
    state = state or primaryState(school_name)
    level = (request.args.get("level") or "").strip().lower() or None
    meets = likelyMeetsCached(getConn, school_name, state, sport, level=level)
    label = schoolLabelIn(school_name, state) if state else school_name
    from accounts import siteOrigin
    body = icsCalendar(label, meets, siteOrigin())
    resp = Response(body, mimetype="text/calendar")
    resp.headers["Content-Disposition"] = 'inline; filename="meets.ics"'
    return resp
