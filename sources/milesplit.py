# Project: xc-predictor
# File:    sources/milesplit.py
# Purpose: MileSplit's posted results (owner, 2026-11-10: "they both told me
#          it was fine, so you wanna do milesplit?"). 540 timers in the
#          research file post only here; NY, NJ, PA, GA, VA ... run through it.
#
#   python -m sources.milesplit --dry [--states ny,nj] [--limit 3]
#
# THE SITE (read 2026-11-10; robots.txt keeps /rankings, /api/ and a few
#   member pages off limits, not results)
#   https://<st>.milesplit.com/results           a state's newest results:
#                                                meet id, name, venue, day
#   .../meets/<id>-<slug>/results                the meet: its results files
#                                                (meetResultFiles, /raw links)
#   .../meets/<id>-<slug>/results/<file>/raw     one file as posted, in a
#                                                <pre>: tab-separated races
#                                                ("Boys Class C - 5K Run" then
#                                                Place Athlete Grade School
#                                                Time), Hy-Tek text, or a
#                                                fixed-width report
# ! ONLY THE FREE FILES: a page that asks for a login is not read.
# ! FETCHED LIKE EVERY SCRAPER: sources.fetch.Fetcher and its settings.
import datetime
import html as _html
import json
import re

from sources import hytek, land

SLUG = "milesplit"
STATES = ("al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv "
          "nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy").split()
_LOGIN = re.compile(r"(?i)<form[^>]+action=\"[^\"]*/login|you must be logged in|sign in to view")


# ! ONE SITE, MANY HOSTS: every state is its own subdomain, and the fetcher
#   paces per host, so 51 state lists would go out back to back. Every real
#   request to *.milesplit.com waits GAP seconds after the last one, as one
#   host's would (sources.fetch.DEFAULT_INTERVAL); a copy from the store
#   waits for nothing.
GAP = 10.0
_last = [0.0]


async def _get(fetcher, url, max_age):
    import asyncio
    import time
    store = getattr(fetcher, "store", None)
    m = store.meta(url) if store is not None else None
    fresh = bool(m and max_age is not None and m.get("status") == 200
                 and time.time() - (m.get("fetched_at") or 0) < max_age)
    if not fresh:
        wait = _last[0] + GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
    got = await fetcher.fetch(url, max_age)
    if not fresh:
        _last[0] = time.monotonic()
    return got


def indexUrl(state, page=1):
    return f"https://{state}.milesplit.com/results" + (f"?&page={page}" if page > 1 else "")


def _text(s):
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def _year(url, today):
    m = re.search(r"-(20\d\d)/results", url or "")
    return int(m.group(1)) if m else today.year


def parseIndex(page, today=None):
    """A state's results list -> [{meet_id, url, name, venue, state, date}]. Pure."""
    today = today or datetime.date.today()
    out = []
    for block in re.split(r'<li class="meet-row"', page or "")[1:]:
        mid = re.search(r'data-meet-id="(\d+)"', block)
        a = re.search(r'class="meet-row__name" href="([^"]+)">(.*?)</a>', block, re.S)
        if not (mid and a):
            continue
        day = _text((re.search(r'meet-row__day">(.*?)</span>', block, re.S) or [None, ""])[1])
        venue = _text((re.search(r'meet-row__venue">(.*?)</span>', block, re.S) or [None, ""])[1])
        url = a.group(1)
        date = None
        try:
            d = datetime.datetime.strptime(f"{day} {_year(url, today)}", "%b %d %Y").date()
            if d > today + datetime.timedelta(days=7):      # a December list in January
                d = d.replace(year=d.year - 1)
            date = d.isoformat()
        except ValueError:
            pass
        st = re.search(r",\s*([A-Z]{2})\s*$", venue)
        out.append({"meet_id": int(mid.group(1)), "url": url, "name": _text(a.group(2)),
                    "venue": venue, "state": st.group(1) if st else None, "date": date})
    return out


def rawUrls(page, meet_url):
    """The meet page's results files, as raw-text urls, each once. Pure."""
    base = meet_url.split("/results")[0] + "/results"
    ids = []
    m = re.search(r"meetResultFiles\s*=\s*(\[.*?\])\s*;", page or "", re.S)
    if m:
        try:
            ids = [f["id"] for f in json.loads(m.group(1)) if f.get("id")]
        except (ValueError, TypeError):
            ids = []
    ids += [int(x) for x in re.findall(r"/results/(\d+)/raw", page or "")]
    out = []
    for i in ids:
        u = f"{base}/{i}/raw"
        if u not in out:
            out.append(u)
    return out


def rawText(page):
    """A raw results page -> text: its <pre> as it is, or its table (a
    RunMeet export, NYSAIS XC Champs 2026) as tab-separated lines, one row a
    line. Pure."""
    m = re.search(r"<pre[^>]*>(.*?)</pre>", page or "", re.S | re.I)
    if m:
        return _html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
    t = re.search(r"<table[^>]*>(.*?)</table>", page or "", re.S | re.I)
    if not t:
        return ""
    lines = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t.group(1), re.S | re.I):
        cells = [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            lines.append("\t".join(cells))
    return "\n".join(lines)


_TAB_FIELDS = {"place": ("place", "pl", "pos"), "name": ("athlete", "name", "runner"),
               "first": ("first", "first name"), "last": ("last", "last name"),
               "grade": ("grade", "gr", "yr", "year", "class"), "school": ("school", "team", "affiliation"),
               "time": ("time", "finals", "mark", "result")}


def tabEvents(text):
    """Tab-separated races -> [hytek.Event]: a title line, a header line,
    then one runner a line, until the next title. Pure."""
    from sources.raceroster import _row
    events, title, cols = [], "", None
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        cells = [c.strip() for c in ln.split("\t")]
        if len(cells) == 1:
            title, cols = cells[0], None
            continue
        low = [c.lower() for c in cells]
        if cols is None or ("time" in low and ("athlete" in low or "name" in low or "place" in low)):
            if any(x in low for x in ("time", "athlete", "name")):
                cols = {}
                for f, names in _TAB_FIELDS.items():
                    for n in names:
                        if n in low and f not in cols:
                            cols[f] = low.index(n)
                events.append(hytek.Event(number=len(events) + 1, gender=_gender(title), title=title,
                                          meters=hytek.metersOf(title)))
                continue
        if not cols or not events:
            continue
        g = lambda f: cells[cols[f]] if f in cols and cols[f] < len(cells) else ""   # noqa: E731
        row = _row(g("place"), g("name"), g("first"), g("last"), g("grade"), g("school"), g("time"))
        if row:
            events[-1].rows.append(row)
    return [e for e in events if e.rows]


def _gender(title):
    t = (title or "").lower()
    if re.search(r"\b(girls?|women'?s?|female)\b", t):
        return "F"
    if re.search(r"\b(boys?|men'?s?|male)\b", t):
        return "M"
    return ""


def eventsOf(text):
    """A raw results file -> [hytek.Event], whichever layout it is in."""
    if "\t" in text:
        ev = tabEvents(text)
        if ev:
            return ev
    rep = hytek.parseReport(text)
    if any(e.rows for e in rep.events):
        return [e for e in rep.events if e.rows]
    from sources.raceroster import textRows
    rows = [r for r, _s in textRows(text)]
    return [hytek.Event(number=1, gender="", title="", meters=None, rows=rows)] if rows else []


async def readMeet(fetcher, meet, max_age):
    """One meet -> (LandedMeet | None, note)."""
    got = await _get(fetcher, meet["url"], max_age)
    if not got.body:
        return None, got.note or f"HTTP {got.status}"
    page = got.body.decode("utf-8", "replace")
    m = hytek.Meet(name=meet["name"], date=meet["date"], venue=meet["venue"])
    for u in rawUrls(page, meet["url"]):
        r = await _get(fetcher, u, max_age)
        if not r.body:
            continue
        body = r.body.decode("utf-8", "replace")
        if _LOGIN.search(body) and "<pre" not in body.lower() and "<table" not in body.lower():
            continue                                  # behind a login: not read
        for e in eventsOf(rawText(body)):
            e.number = len(m.events) + 1
            m.events.append(e)
    # ★ THE SEASON SAYS THE SPORT (2026-11-10, Section 5 Championships:
    #   "Boys Class C - 5K Run", no cross country word anywhere, read as
    #   track). MileSplit's lists mix both sports; a meet run from August to
    #   mid-December with no sprint, hurdle or field event is cross country.
    if isFallMeet(meet["date"]) and not land.isTrackMeet([e.title for e in m.events]):
        for e in m.events:
            e.sport = "XC"
    lm = land.fromHytek(m, SLUG, meet["url"])
    lm.place, lm.state = meet["venue"], meet["state"] or lm.state
    return lm, "milesplit"


def isFallMeet(date):
    """Aug 1 to Dec 15. Pure."""
    try:
        d = datetime.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        return False
    return (d.month, d.day) >= (8, 1) and (d.month, d.day) <= (12, 15)


async def discover(fetcher, since_year, per_run, known, states=None, pages=1, index_max_age=6 * 3600):
    found, failed = {}, []
    for st in states or STATES:
        for p in range(1, pages + 1):
            got = await _get(fetcher, indexUrl(st, p), index_max_age)
            if not got.body:
                failed.append(st)
                break
            for mt in parseIndex(got.body.decode("utf-8", "replace")):
                found.setdefault(mt["meet_id"], mt)
    today = datetime.date.today().isoformat()
    meets = sorted((m for m in found.values()
                    if m["date"] and m["date"][:4] >= str(since_year) and m["date"] < today
                    and m["url"] not in known), key=lambda m: m["date"], reverse=True)
    return meets[:per_run], (f"{len(found)} meets listed" + (f"; index failed for {','.join(failed)}"
                                                             if failed else "")
                             + f"; {min(len(meets), per_run)} new")


def main(argv=None):
    import argparse
    import asyncio
    from sources.fetch import Fetcher, RawStore
    ap = argparse.ArgumentParser(description="MileSplit: parse, print (dry run)")
    ap.add_argument("--dry", action="store_true", required=True)
    ap.add_argument("--states", default="")
    ap.add_argument("--limit", type=int, default=3)
    a = ap.parse_args(argv)
    fetcher = Fetcher(RawStore())
    states = [s.strip().lower() for s in a.states.split(",") if s.strip()] or None

    async def go():
        meets, note = await discover(fetcher, datetime.date.today().year, a.limit, set(), states)
        print(f"[milesplit] {note}")
        for mt in meets:
            lm, why = await readMeet(fetcher, mt, 30 * 86400)
            print(f"  {land.summary(lm) if lm else mt['name']} -- {why}")
    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
