# Project: xc-predictor
# File:    sources/raceroster.py
# Purpose: Race Roster results (results.raceroster.com): the school cross
#          country meets its timers post there -- Alan and Robin (NE/IA),
#          ERC Timing (IA/IL/NM), Start 2 Finish (MO/TN/NC), TimingSpot (OH),
#          Granite State (NH), Chuck Magera (SC), the northern Wisconsin
#          timer ... (docs/timing_passes/raceroster_adapter_notes.md: 70
#          school meets this season by 2026-10-23, about 45 a year before).
#
#   python -m sources.raceroster --dry [--limit 5] [--code CODE ...]
#
# THE API (raceroster_adapter_notes.md has it all)
#   search.raceroster.com/search?q=&l=100&p=&from=   events by name/place (the index)
#   results.raceroster.com/v2/api/events/redirect/<eventId>   302 -> /v3/events/<code>
#   results.raceroster.com/v2/api/events/<code>               the meet and its races
#   .../v2/api/result-events/<id>/sub-events/<sub>/results?start=0&limit=1000
#                                                     a STRUCTURED race's rows
#   .../v2/api/events/<code>/sub-events/<sub>         an UNSTRUCTURED race's text
# ! THE MEET'S OWN KEY IS ITS EVENT ID (raceroster.com/events/<yyyy>/<eid>),
#   known from the search before any results call.
# ! NEVER STORED: the organizer's e-mail and access code the event JSON carries.
# ! FETCHED LIKE EVERY TIMER: sources.fetch.Fetcher and its settings.
import asyncio
import datetime
import json
import re
import urllib.parse

from sources import hytek, land

SEARCH = "https://search.raceroster.com/search"
API = "https://results.raceroster.com/v2/api"
SLUG = "race-roster"                  # meets_ext.source 'timer:race-roster'

# the names a school meet goes by (notes, "Classify"); the index is searched
# with these, since the start of the season
TERMS = ("cross country", "xc", "invitational", "conference", "championship", "district",
         "sectional", "regional", "middle school", "high school")
_SCHOOL = re.compile(r"cross[\s-]*country|\bxc\b|\binvit|\bconference|\bchampionship|\bdistrict|"
                     r"\bsectional|\bregional|\bmiddle school|\bhigh school|\b(?:hs|ms|jv)\b|"
                     r"\b(?:nsaa|ihsa|ihsaa|wiaa|mshsl|mshsaa|khsaa|lhsaa|schsl|ighsau|iahsaa|"
                     r"ohsaa|tssaa|ghsa|uil|mhsaa|osaa|fhsaa)\b", re.I)
_ROAD = re.compile(r"\b(\d+(\.\d+)?\s*k\s*(run|walk|fun)|fun\s*run|walk|trot|turkey|marathon|"
                   r"half|triathlon|duathlon|mtb|mountain\s*bike|bike|camp|clinic|nordic|ski|"
                   r"swim|color|glow|virtual|relay)\b", re.I)
_TEAM_SUB = re.compile(r"\bteam\b|text\s*format", re.I)


def searchUrl(term, since, page=0):
    q = urllib.parse.urlencode({"q": term, "l": 100, "p": page, "from": since})
    return f"{SEARCH}?{q}"


def eventKey(event_id, year):
    """The meet's stable key (meets_ext.url / ext_seen)."""
    return f"https://raceroster.com/events/{year}/{event_id}"


def parseSearch(body):
    """A search page -> ([{event_id, name, date, state, country, subs}], more). Pure."""
    d = json.loads(body)
    out = []
    for e in d.get("data") or []:
        loc = e.get("location") or {}
        prov = loc.get("province") or {}
        out.append({"event_id": e.get("eventId"), "name": e.get("name") or "",
                    "date": (e.get("date") or "")[:10], "state": prov.get("abbreviation"),
                    "country": loc.get("countryISO"),
                    "subs": [s.get("name") or "" for s in e.get("subEvents") or []]})
    return out, bool((d.get("meta") or {}).get("nextPage"))


def isSchool(item):
    """A US school cross country meet by its name and races, not a road
    race. Pure."""
    if item.get("country") not in (None, "US"):
        return False
    name = item.get("name") or ""
    if _ROAD.search(name) and not re.search(r"cross[\s-]*country|\bxc\b", name, re.I):
        return False
    return bool(_SCHOOL.search(name) or any(re.search(r"\b(varsity|jv|middle|freshm|grade)\b", s, re.I)
                                            for s in item.get("subs") or []))


def localDate(event_date, tz):
    """'2026-09-19T21:00:00.000Z' in America/Chicago -> '2026-09-19'. Pure."""
    try:
        t = datetime.datetime.fromisoformat((event_date or "").replace("Z", "+00:00"))
    except ValueError:
        return (event_date or "")[:10] or None
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo(tz)) if tz else t
    except Exception:                                               # noqa: BLE001
        pass
    return t.date().isoformat()


def parseEvent(body):
    """The event JSON -> the meet and the races worth reading. Pure."""
    e = (json.loads(body).get("data") or {}).get("event") or {}
    subs = [s for s in e.get("subEvents") or []
            if s.get("hasResults") and s.get("isPublic", True) is not False
            and not _TEAM_SUB.search(s.get("name") or "")]
    return {"id": e.get("id"), "eid": e.get("eid"), "code": e.get("uniqueCode"),
            "name": e.get("name") or "", "date": localDate(e.get("eventDate"), e.get("timezone")),
            "place": ", ".join(x for x in (e.get("locality"), e.get("administrativeArea")) if x),
            "state": e.get("administrativeArea"), "country": e.get("countryCode"),
            "timer": ((e.get("provider") or {}).get("label") or ""),
            "subs": sorted(subs, key=lambda s: (s.get("sortOrder") or 0, s.get("id") or 0))}


# ---------------------------------------------------------------- rows

_LABELS = {
    "place": ("place", "race place", "overall place", "overall"),
    "name": ("name", "full name"),
    "first": ("first name",),
    "last": ("last name",),
    "school": ("school", "team", "team name", "school name"),
    "grade": ("grade", "year", "class", "yr", "cla", "gr"),
    "time": ("time", "gun elapsed time", "gun time", "guntime", "chip elapsed time", "finish time",
             "chip time"),
    "gender": ("gender", "sex"),
}


def _field(labels):
    """{our field: their column} from {their column: label}, the first label
    that names it. ! A "Team Place" column is a place, not the school (Start
    2 Finish puts it on teamName). Pure."""
    out = {}
    low = {col: (lab or "").strip().lower() for col, lab in labels.items()}
    for f, names in _LABELS.items():
        for n in names:
            hit = [c for c, lab in low.items() if lab == n]
            if hit:
                out[f] = hit[0]
                break
    return out


def seconds(mark):
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$", (mark or "").strip())
    if not m:
        return None
    s = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return round(s, 2) if s > 0 else None


def _row(place, name, first, last, grade, school, mark):
    if not last and name:
        parts = name.split()
        first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", name)
    secs = seconds(mark)
    status = None if secs else (mark.strip().upper() if re.match(r"^(DNF|DNS|DQ|DSQ)$", (mark or "").strip(), re.I)
                                else None)
    if not last or (secs is None and status is None):
        return None
    p = str(place or "").strip()
    return hytek.Row(place=int(p) if p.isdigit() and secs else None, last=last, first=first or "",
                     grade=(grade or "").strip(), school=(school or "").strip(), seconds=secs,
                     mark=status or mark.strip())


def structuredRows(body):
    """A STRUCTURED race's rows call -> [(Row, gender)]. Pure."""
    d = json.loads(body)
    cols = {c.get("col"): c.get("label") for c in (d.get("meta") or {}).get("flattenedColumns") or []}
    f = _field(cols)
    out = []
    for r in d.get("data") or []:
        g = lambda k: str(r.get(f[k]) if f.get(k) and r.get(f[k]) is not None else "")  # noqa: E731
        row = _row(g("place"), g("name"), g("first"), g("last"), g("grade"), g("school"), g("time"))
        if row:
            sex = g("gender").strip().upper()[:1]
            out.append((row, sex if sex in ("M", "F") else ""))
    return out


_HEADER = re.compile(r"^\s*(place|pl|pos)\b.*\bname\b", re.I)
_RULER = re.compile(r"^[\s=\-]+$")
_TIMEISH = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?$")


def _spans(head, ruler):
    """[(start, end, label)]: from the ruler under the header when there is
    one ("===== ==== ====="), each run of = or - is a column; else from where
    each heading word starts. Pure."""
    if ruler:
        runs = [(m.start(), m.end()) for m in re.finditer(r"[=\-]+", ruler)]
        out = []
        for k, (st, en) in enumerate(runs):
            nxt = runs[k + 1][0] if k + 1 < len(runs) else None
            out.append((st, nxt, " ".join(head[st:en + 1].split()).lower()))
        return out
    words = [(m.start(), m.group(0)) for m in re.finditer(r"\S+", head)]
    return [(st, words[k + 1][0] if k + 1 < len(words) else None, w.lower())
            for k, (st, w) in enumerate(words)]


def _textColumns(spans, sample):
    """{field: (start, end)} from the headings, and the time column from
    the values when its heading is no time word ("T4"). Pure."""
    cols = {}
    # ! BY THE LABEL'S RANK, NOT THE COLUMN'S PLACE (iResultsLive, Pelham XC
    #   Invitational 2026: "Sex Year School Grade" -- Year is the class of
    #   2028, Grade is 11): "grade" wins over "year" wherever each sits
    for f, names in _LABELS.items():
        for n in names:
            hit = [(st, en) for st, en, lab in spans if lab == n]
            if hit:
                cols[f] = hit[0]
                break
    if "name" not in cols and not ("first" in cols and "last" in cols):
        hit = [(st, en) for st, en, lab in spans if "name" in lab and "team" not in lab]
        if hit:
            cols["name"] = hit[0]
    if "place" not in cols and spans:
        cols["place"] = spans[0][:2]
    if "time" not in cols:
        after = cols.get("name", (0, 0))[0]
        for st, en, lab in spans:
            if st <= after or "pace" in lab:
                continue
            vals = [(ln[st:en] if en else ln[st:]).strip() for ln in sample]
            if vals and sum(bool(_TIMEISH.match(v)) for v in vals) >= 0.6 * len(vals):
                cols["time"] = (st, en)
                break
    return cols


def textRows(text):
    """An UNSTRUCTURED race's fixed-width text -> [(Row, '')]: the columns
    are read off the individual results' header line ("Place TmPl Bib Name
    School Class Time Pace"), or the ruler of = under it when there is one
    (ORR's Muscatine 2025: "Place TmPl No. First Name Gr School T4 Pace"),
    and the time column by its values when its heading is not a time word.
    The team block above it is skipped. Pure."""
    lines = (text or "").splitlines()
    out = []
    i = 0
    while i < len(lines):
        if not _HEADER.match(lines[i]):
            i += 1
            continue
        head = lines[i]
        i += 1
        ruler = lines[i] if i < len(lines) and _RULER.match(lines[i]) and "=" in lines[i] + "-" else None
        if ruler is not None and not re.search(r"[=\-]{2,}", ruler):
            ruler = None
        if ruler is not None:
            i += 1
        body = []
        while i < len(lines) and lines[i].strip() and not _HEADER.match(lines[i]):
            if not _RULER.match(lines[i]):
                body.append(lines[i])
            i += 1
        cols = _textColumns(_spans(head, ruler), body[:20])
        for ln in body:
            cut = lambda f: ((ln[cols[f][0]:cols[f][1]] if cols[f][1] else ln[cols[f][0]:])  # noqa: E731
                             if f in cols else "").strip()
            mark = cut("time")
            tm = re.search(r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?|\bDN[FS]\b|\bDQ\b",
                           mark or (ln[cols["time"][0]:] if "time" in cols else ""))
            row = _row(cut("place"), cut("name"), cut("first"), cut("last"), cut("grade"),
                       cut("school"), tm.group(0) if tm else "")
            if row:
                out.append((row, ""))
    return out


def _gender(title, rows):
    t = (title or "").lower()
    if re.search(r"\b(women'?s?|girls?|ladies|female)\b", t):
        return "F"
    if re.search(r"\b(men'?s?|boys?|male)\b", t):
        return "M"
    return ""


def events(title, rows, number, meters):
    """One race -> hytek.Events (one per gender when a Gender column splits
    a race its title does not name). Pure."""
    g = _gender(title, rows)
    sexes = {s for _r, s in rows if s}
    if g or not sexes:
        e = hytek.Event(number=number, gender=g, title=title, meters=meters, rows=[r for r, _s in rows])
        e.sport = "XC"
        return [e]
    out = []
    for k, sex in enumerate(("F", "M")):
        mine = [r for r, s in rows if s == sex]
        if mine:
            e = hytek.Event(number=number * 10 + k, gender=sex,
                            title=f"{'Girls' if sex == 'F' else 'Boys'} {title}", meters=meters, rows=mine)
            e.sport = "XC"
            out.append(e)
    return out


# ---------------------------------------------------------------- fetching

ORR_REDIRECT = ("https://results.raceroster.com/redirect/third-party"
                "?source=onlineraceresults&entity=event&id={}")


async def resolveCode(fetcher, event_id, url=None):
    """eventId -> results code: the redirect's Location, not followed.
    Kept in the page store like any page (a code never changes). url: a
    different redirect to resolve (the OnlineRaceResults archive's)."""
    url = url or f"{API}/events/redirect/{event_id}"
    meta = fetcher.store.meta(url)
    if meta and meta.get("status") == 200:
        body = fetcher.store.body(url) or b""
        return body.decode() or None
    allowed, _delay = fetcher.robots.get(url)
    if not allowed:
        return None
    import requests
    from sources.fetch import USER_AGENT, hostOf, TIMEOUT
    host = hostOf(url)
    lock = await fetcher.limiter.acquire(host)
    try:
        r = await asyncio.to_thread(requests.get, url, headers={"User-Agent": USER_AGENT},
                                    timeout=TIMEOUT, allow_redirects=False)
    except Exception:                                               # noqa: BLE001
        return None
    finally:
        fetcher.limiter.release(host, lock)
    m = re.search(r"/v3/events/([A-Za-z0-9]+)", r.headers.get("Location") or "")
    if not m:
        if r.status_code in (301, 302) and "not-found" in (r.headers.get("Location") or ""):
            fetcher.store.put(url, 200, b"")          # asked: nothing there
        return None
    fetcher.store.put(url, 200, m.group(1).encode())
    return m.group(1)


async def readMeet(fetcher, code, key, max_age):
    """One meet by its results code -> (LandedMeet | None, note)."""
    got = await fetcher.fetch(f"{API}/events/{code}", max_age)
    if not got.body:
        return None, got.note or f"HTTP {got.status}"
    ev = parseEvent(got.body)
    m = hytek.Meet(name=ev["name"], date=ev["date"], venue=ev["place"], licensee=ev["timer"])
    skipped = 0
    for k, sub in enumerate(ev["subs"], 1):
        if sub.get("resultsType") == "STRUCTURED":
            r = await fetcher.fetch(f"{API}/result-events/{ev['id']}/sub-events/{sub['id']}/results"
                                    f"?start=0&limit=1000", max_age)
            rows = structuredRows(r.body) if r.body else []
        else:
            r = await fetcher.fetch(f"{API}/events/{code}/sub-events/{sub['id']}", max_age)
            data = (json.loads(r.body).get("data") or {}) if r.body else {}
            u = data.get("unstructuredData") or {}
            text = u.get("text") or ""
            if not text and u.get("url"):
                p = await fetcher.fetch(u["url"], max_age)
                text = hytek.textFromAny(p.body, "application/pdf") if p.body else ""
            rows = textRows(text)
            if not rows and text:                  # a Hy-Tek report after all
                rep = hytek.parseReport(text)
                rows = [(x, "") for e in rep.events for x in e.rows]
        # ! AN OPEN RACE: no runner has a school or a grade (Big Timber XC
        #   Challenge 2026, "Boys 5 Mile Overall Results"): nothing to rate
        if rows and not any(r.school or r.grade for r, _s in rows):
            skipped += len(rows)
            continue
        if rows:
            m.events.extend(events(sub.get("name") or "", rows, k,
                                   raceMeters(sub.get("name"), sub.get("distanceMeters"))))
    lm = land.fromHytek(m, SLUG, key)
    lm.place, lm.state = ev["place"], ev["state"] or lm.state
    lm.skipped += skipped
    return lm, "race roster"


def raceMeters(title, stated):
    """The race's distance: the one its title names wins over the
    sub-event's distanceMeters, which is 1000 when nobody set it (Texas
    Youth State XC 2026: "BOYS 4K" at 1000; the team-score sub-events too).
    Pure."""
    named = hytek.metersOf(title or "")
    if named:
        return named
    return stated if stated and stated != 1000 else None


def seeds(since):
    """The research file's meets from `since` on: (key, code, name) with no
    search or redirect needed (docs/timing_passes/raceroster_meets.csv)."""
    import csv
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "timing_passes", "raceroster_meets.csv")
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if (r.get("date") or "") >= since and r.get("results_code") and r.get("event_id"):
                    out.append((eventKey(r["event_id"], r["date"][:4]), r["results_code"], r["meet_name"],
                                r["date"]))
    except OSError:
        pass
    return out


async def discover(fetcher, since, per_run, known, max_pages=5, index_max_age=20 * 3600):
    """([(key, code_or_None, event_id, name, date)], note): school meets
    since `since`, newest first, not yet landed."""
    found, pages = {}, 0
    for key, code, name, date in seeds(since):
        found[key] = (key, code, None, name, date)
    for term in TERMS:
        for p in range(max_pages):
            got = await fetcher.fetch(searchUrl(term, since, p), index_max_age)
            pages += 1
            if not got.body:
                break
            try:
                items, more = parseSearch(got.body)
            except ValueError:
                break
            for it in items:
                if it["event_id"] and it["date"] >= since and isSchool(it):
                    key = eventKey(it["event_id"], it["date"][:4])
                    if key not in found:
                        found[key] = (key, None, it["event_id"], it["name"], it["date"])
            if not more:
                break
    today = datetime.date.today().isoformat()
    todo = sorted((v for k, v in found.items() if k not in known and v[4] < today),
                  key=lambda v: v[4], reverse=True)
    return todo[:per_run], (f"{len(found)} school meets since {since} ({pages} index pages); "
                            f"{min(len(todo), per_run)} new")


# ---------------------------------------------------------------- the archive

ARCHIVE_DDL = """
CREATE TABLE IF NOT EXISTS rr_archive (
    orr_id    bigint PRIMARY KEY,      -- OnlineRaceResults' event id
    code      text,                    -- Race Roster's results code, when it has one
    status    text,                    -- 'none' | 'not school' | 'landed: <what>' | 'no races' | ...
    meet_key  text,
    at        timestamptz NOT NULL DEFAULT now()
)"""


async def archiveOne(fetcher, orr_id):
    """One OnlineRaceResults id -> (code, status, LandedMeet | None)."""
    code = await resolveCode(fetcher, None, ORR_REDIRECT.format(orr_id))
    if not code:
        return None, "none", None
    got = await fetcher.fetch(f"{API}/events/{code}", 365 * 86400)
    if not got.body:
        return code, f"event {got.status}", None
    ev = parseEvent(got.body)
    item = {"name": ev["name"], "country": ev["country"], "subs": [s.get("name") or "" for s in ev["subs"]]}
    if not ev["subs"]:
        return code, "no results", None
    if not isSchool(item):
        return code, "not school", None
    key = eventKey(ev["eid"] or f"orr{orr_id}", (ev["date"] or "0000")[:4])
    lm, why = await readMeet(fetcher, code, key, 365 * 86400)
    if lm is None or not lm.races:
        return code, f"no races ({why})", None
    return code, "school", lm


def cmdArchive(a):
    """★ THE ONLINERACERESULTS ARCHIVE (owner, 2026-11-10: "yes"). Its
    events moved to Race Roster under their old ids, reachable only by
    walking them (raceroster_adapter_notes.md section 7): ids 22,300-25,800
    are 2019-2025, about 600 school meets. Resumable (rr_archive remembers
    every id asked), bounded by --minutes, landed through land -> dedup ->
    promote like every timer meet. Run it through the lock:
    deploy/rr_archive.sh."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    if a.write:
        from database import getConn
        with getConn() as conn:
            return _archive(a, conn)
    return _archive(a, None)


def _archive(a, conn):
    import time as _t
    from sources.fetch import Fetcher, RawStore
    fetcher = Fetcher(RawStore())
    deadline = _t.monotonic() + a.minutes * 60 if a.minutes else None
    done = set()
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(ARCHIVE_DDL)
            land.initTables(cur)
            cur.execute("SELECT orr_id FROM rr_archive WHERE orr_id BETWEEN %s AND %s", (a.lo, a.hi))
            done = {r[0] for r in cur.fetchall()}
        conn.commit()
    ids = [i for i in range(a.hi, a.lo - 1, -1) if i not in done]       # newest first
    print(f"[rr archive] ids {a.lo:,}..{a.hi:,}: {len(ids):,} to ask ({len(done):,} asked before)", flush=True)
    tally, landed = {}, 0

    async def go():
        nonlocal landed
        for n, orr in enumerate(ids, 1):
            if deadline and _t.monotonic() > deadline:
                print(f"[rr archive] --minutes spent; {len(ids) - n + 1:,} ids wait for the next run")
                break
            try:
                code, status, lm = await archiveOne(fetcher, orr)
            except Exception as exc:                                # noqa: BLE001
                print(f"  {orr}: {type(exc).__name__}: {exc}")
                continue
            tally[status.split(" (")[0]] = tally.get(status.split(" (")[0], 0) + 1
            if lm is not None:
                print(f"  {orr}: {land.summary(lm)}", flush=True)
            if conn is not None:
                with conn.cursor() as cur:
                    if lm is not None:
                        what = land.landMeet(cur, lm)[1]
                        status = f"landed: {what}"
                        landed += 1
                    cur.execute("""INSERT INTO rr_archive (orr_id, code, status, meet_key)
                                   VALUES (%s, %s, %s, %s) ON CONFLICT (orr_id) DO UPDATE SET
                                   code = EXCLUDED.code, status = EXCLUDED.status,
                                   meet_key = EXCLUDED.meet_key, at = now()""",
                                (orr, code, status[:200], lm.url if lm is not None else None))
                conn.commit()
            if n % 100 == 0:
                print(f"[rr archive] {n:,}/{len(ids):,} asked: {tally}", flush=True)
    asyncio.run(go())
    print(f"[rr archive] {tally}; landed {landed}; fetcher {fetcher.stats}")
    if conn is not None and landed:
        from sources import dedup, promote
        dedup.run(conn, write=True, show=20)
        promote.run(conn, apply=True, show=20)
        print("[rr archive] link them with: python scripts/link_uploads.py --source timer --apply "
              "(the nightly 04a does it too)")
    return 0


def main(argv=None):
    import argparse
    from sources.fetch import Fetcher, RawStore
    ap = argparse.ArgumentParser(description="Race Roster: parse, print (dry run); archive: the ORR walk")
    ap.add_argument("cmd", nargs="?", choices=("archive",))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--write", action="store_true", help="archive: land what it finds")
    ap.add_argument("--lo", type=int, default=22300)
    ap.add_argument("--hi", type=int, default=25800)
    ap.add_argument("--minutes", type=float, help="archive: stop asking after this long")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--code", action="append", help="a results code to read")
    a = ap.parse_args(argv)
    if a.cmd == "archive":
        return cmdArchive(a)
    if not a.dry:
        ap.error("--dry (or the archive command)")
    fetcher = Fetcher(RawStore())

    async def go():
        if a.code:
            todo = [(f"code:{c}", c, None, c, "") for c in a.code]
        else:
            since = f"{datetime.date.today().year}-08-01"
            todo, note = await discover(fetcher, since, a.limit, set())
            print(f"[race roster] {note}")
        for key, code, eid, name, _d in todo:
            code = code or await resolveCode(fetcher, eid)
            if not code:
                print(f"  {name}: no results code")
                continue
            lm, why = await readMeet(fetcher, code, key, 30 * 86400)
            print(f"  {land.summary(lm) if lm else name} -- {why}")
    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
