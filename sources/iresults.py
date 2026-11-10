# Project: xc-predictor
# File:    sources/iresults.py
# Purpose: iResultsLIVE (iresultslive.com): the New England timers' results
#          site -- Yankee Timing, Granite State Race Services, Empire Timing,
#          Novus Mage. School cross country meets (Pelham XC Invitational,
#          MVC XC Championship ...) next to road races.
#
#   python -m sources.iresults --dry [--limit 3]
#
# THE SITE (read 2026-11-10; no robots.txt)
#   /index.php?rev2&eid=&op=latest      the latest events: id, title, place,
#                                       date, and each race's name and count
#   /results/?eid=<id>                  the event: its report links
#   /results/?op=reports&eid=<id>&racename=<Race>Printable&raw=1
#                                       one race as a fixed-width report in a
#                                       <pre>: "Place TmPl No. First Name Last
#                                       Name Sex Year School Grade Time" over a
#                                       ruler (sources.raceroster.textRows)
# ! THE DATE IS THE INDEX'S: a report's own header can be last year's
#   template (Pelham 2026's says "Saturday, October 11, 2025").
# ! FETCHED LIKE EVERY TIMER: sources.fetch.Fetcher and its settings.
import datetime
import html as _html
import re

from sources import hytek, land
from sources.raceroster import textRows

BASE = "https://www.iresultslive.com"
INDEX = f"{BASE}/index.php?rev2&eid=&op=latest"
SLUG = "iresultslive"
_XC = re.compile(r"cross[\s-]*country|\bx\.?c\.?\b|\bcc\b", re.I)
_SCHOOL_RACE = re.compile(r"\b(varsity|jv|junior\s*varsity|freshm|middle|ms|hs|frosh|soph)\b|"
                          r"(?:boys|girls)(?:varsity|jv|ms|frosh)", re.I)
_ROAD = re.compile(r"\b(\d+(\.\d+)?\s*k|5k|10k|half|marathon|trot|walk|fun\s*run|road|relay)\b", re.I)


def _text(s):
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parseIndex(page):
    """The latest-events page -> [{eid, name, place, state, date, races}]. Pure."""
    out = []
    for block in re.split(r"<div class='row ev", page or "")[1:]:
        m = re.search(r"href='results\?eid=(\d+)'>\s*<h3>(.*?)</h3>", block, re.S)
        if not m:
            continue
        txt = _text(block)
        d = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(\w{3})\w*\s+(\d{1,2}),\s+(\d{4})", txt)
        date = None
        if d:
            try:
                date = datetime.datetime.strptime(f"{d.group(1)} {d.group(2)} {d.group(3)}",
                                                  "%b %d %Y").date().isoformat()
            except ValueError:
                date = None
        loc = re.search(r"<div class='col col-xs-12 col-md-6[^>]*>([^<]*)</div>", block)
        place = re.search(r"(.+?,\s*([A-Z]{2}))\s*$", _text(loc.group(1)) if loc else "")
        races = re.findall(r"racename=([^&']+)&eid=\d+'[^>]*>[^<]*<span[^>]*>\((\d+)\)", block)
        out.append({"eid": int(m.group(1)), "name": _text(m.group(2)), "date": date,
                    "place": place.group(1) if place else "", "state": place.group(2) if place else None,
                    "races": [(r.strip(), int(n)) for r, n in races]})
    return out


def isSchoolXC(ev):
    """A school cross country meet: XC in its name, or school-race names
    (BoysVarsity, GirlsJV ...) and no road-race word in its name. Pure."""
    name = ev.get("name") or ""
    races = [r for r, _n in ev.get("races") or []]
    if _XC.search(name):
        return True
    if _ROAD.search(name):
        return False
    return bool(races) and sum(bool(_SCHOOL_RACE.search(r)) for r in races) >= max(1, len(races) // 2)


def reportNames(page):
    """An event page -> its individual race reports' names (the
    '...Printable' ones, not the '...Team' scores), in order. Pure."""
    out = []
    for n in re.findall(r"racename=([A-Za-z0-9_%.\-]+?)Printable", page or ""):
        if n not in out:
            out.append(n)
    return out


def withoutCombined(names):
    """Report names less a gender's 'Combined' one when that gender's
    varsity and JV reports are there too (Northfield Mount Hermon 2026:
    GirlsCombined = GirlsJV5k + GirlsVarsity5k, every runner twice). Pure."""
    def sex(n):
        return "F" if re.match(r"(?i)girls|women", n) else "M" if re.match(r"(?i)boys|men", n) else ""
    out = []
    for n in names:
        if re.search(r"(?i)combined", n):
            same = [x for x in names if x != n and sex(x) == sex(n)]
            if any(re.search(r"(?i)varsity", x) for x in same) and any(re.search(r"(?i)jv", x) for x in same):
                continue
        out.append(n)
    return out


def reportUrl(eid, race):
    return f"{BASE}/results/?op=reports&eid={eid}&racename={race}Printable&raw=1"


def reportText(page):
    m = re.search(r"<pre[^>]*>(.*?)</pre>", page or "", re.S | re.I)
    return _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""


def raceTitle(race, text):
    """'GirlsVarsity' and the report's banner ('=== Varsity Girls 5k RACE
    ===') -> 'Girls Varsity 5k'. Pure."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", race)
    m = re.search(r"={3,}\s*([^=\n]+?)\s*={3,}", text or "")
    banner = m.group(1).strip() if m else ""
    meters = re.search(r"\d+(?:\.\d+)?\s*(?:k|km|mi(?:les?)?|m)\b", banner, re.I)
    return f"{words} {meters.group(0)}".strip() if meters else words


def _gender(title):
    t = title.lower()
    if re.search(r"\b(girls?|women'?s?|female)\b", t):
        return "F"
    if re.search(r"\b(boys?|men'?s?|male)\b", t):
        return "M"
    return ""


def csvUrl(eid, race):
    """The site's own "Download Results" CSV of one race (basic format)."""
    return f"{BASE}/results_downloadcsv.php?eid={eid}&racename={race}&format=basic"


def csvRows(body):
    """A race's CSV ('Place,TmPl,First,Last,S,No.,Gr,School,Div,Time,Pace')
    -> [(Row, sex)]. Pure."""
    import csv
    import io
    from sources.raceroster import _row
    text = body.decode("utf-8-sig", "replace") if isinstance(body, bytes) else body
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        low = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        g = lambda *ks: next((low[k] for k in ks if low.get(k)), "")   # noqa: E731
        row = _row(g("place", "pl"), g("name", "full name"), g("first", "first name"),
                   g("last", "last name"), g("gr", "grade", "yr", "year"), g("school", "team", "team name"),
                   g("time", "gun time", "finish", "chip time"))
        if row:
            sx = g("s", "sex", "gender").upper()[:1]
            out.append((row, sx if sx in ("M", "F") else ""))
    return out


_TEAM_RACE = re.compile(r"(?i)team|virtual")


async def readMeet(fetcher, ev, max_age):
    """One event -> (LandedMeet | None, note): each race's CSV, the races
    being the index's (team scores and virtual results left out)."""
    m = hytek.Meet(name=ev["name"], date=ev["date"], venue=ev["place"])
    races = withoutCombined([r for r, n in ev.get("races") or [] if n and not _TEAM_RACE.search(r)])
    for k, race in enumerate(races, 1):
        got = await fetcher.fetch(csvUrl(ev["eid"], race), max_age)
        if not got.body:
            continue
        rows = csvRows(got.body)
        if not rows or not any(x.school or x.grade for x, _s in rows):
            continue
        title = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", race)
        sexes = {sx for _x, sx in rows if sx}
        g = _gender(title) or (sexes.pop() if len(sexes) == 1 else "")
        if g or not sexes:
            parts = [(g, title, [x for x, _s in rows])]
        else:                                  # one race, both genders: split
            parts = [(sx, f"{'Girls' if sx == 'F' else 'Boys'} {title}", [x for x, s in rows if s == sx])
                     for sx in ("F", "M")]
        for j, (sx, t, rs) in enumerate(parts):
            if rs:
                e = hytek.Event(number=k * 10 + j, gender=sx, title=t, meters=hytek.metersOf(t), rows=rs)
                e.sport = "XC"
                m.events.append(e)
    lm = land.fromHytek(m, SLUG, eventKey(ev["eid"]))
    lm.place, lm.state = ev["place"], ev["state"] or lm.state
    return lm, "iresultslive"


def eventFromPage(page, eid):
    """An event page (an uploaded link) -> the event as parseIndex gives it:
    its name is the text just before its date in the page's header. Pure."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page or "", flags=re.S | re.I)
    d = re.search(r">\s*([^<>]{3,160}?)\s*(?:<[^>]*>\s*)+((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+"
                  r"(\w{3})\w*\s+(\d{1,2}),\s+(\d{4}))", body)
    name, date = "", None
    if d:
        name = _text(d.group(1))
        try:
            date = datetime.datetime.strptime(f"{d.group(3)} {d.group(4)} {d.group(5)}",
                                              "%b %d %Y").date().isoformat()
        except ValueError:
            date = None
    races = []
    for r in re.findall(r"op=overall&(?:amp;)?racename=([^&'\"]+)", page or ""):
        if r not in races:
            races.append(r)
    return {"eid": int(eid), "name": name, "date": date, "place": "", "state": None,
            "races": [(r, 1) for r in races]}


def eventKey(eid):
    return f"{BASE}/results/?eid={eid}"


async def discover(fetcher, since_year, per_run, known, index_max_age=6 * 3600):
    got = await fetcher.fetch(INDEX, index_max_age)
    if not got.body:
        return [], got.note or f"index HTTP {got.status}"
    evs = parseIndex(got.body.decode("utf-8", "replace"))
    today = datetime.date.today().isoformat()
    xc = [e for e in evs if e["date"] and e["date"][:4] >= str(since_year) and e["date"] < today
          and isSchoolXC(e)]
    new = [e for e in xc if eventKey(e["eid"]) not in known]
    return new[:per_run], f"{len(evs)} events listed, {len(xc)} school XC; {min(len(new), per_run)} new"


def main(argv=None):
    import argparse
    import asyncio
    from sources.fetch import Fetcher, RawStore
    ap = argparse.ArgumentParser(description="iResultsLIVE: parse, print (dry run)")
    ap.add_argument("--dry", action="store_true", required=True)
    ap.add_argument("--limit", type=int, default=3)
    a = ap.parse_args(argv)
    fetcher = Fetcher(RawStore())

    async def go():
        evs, note = await discover(fetcher, datetime.date.today().year, a.limit, set())
        print(f"[iresults] {note}")
        for ev in evs:
            lm, why = await readMeet(fetcher, ev, 30 * 86400)
            print(f"  {land.summary(lm) if lm else ev['name']} -- {why}")
    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
