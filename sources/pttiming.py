# Project: xc-predictor
# File:    sources/pttiming.py
# Purpose: PrimeTime Timing (pttiming.com): Wisconsin's and Missouri's state
#          meets, WIAA / IHSA sectionals, Big Ten / SEC / Big 12. Off limits
#          until the owner's word (2026-11-10: they told him scraping is fine).
#
#   python -m sources.pttiming --dry [--limit 3]
#
# THE SITE (read 2026-11-10; no robots.txt)
#   /results                         the newest ~95 events: name, date
#                                    ("November 6th - 7th, 2026"), place
#   /event/<uuid>?pt=results         one event: each race's Hy-Tek report as
#                                    HTML on dash.pttiming.com (and a PDF)
#   the reports read like any timer's (sources.timers.mergeReports)
# ! THE LIST GIVES NO SPORT: cross country is picked by the name, and a fall
#   meet with no track event is cross country (as for MileSplit).
# ! Leone Timing (NY) is a white label of the same platform, but its own site
#   fails its TLS certificate check (2026-11-10): not read until it is fixed.
# ! FETCHED LIKE EVERY SCRAPER: sources.fetch.Fetcher and its settings.
import datetime
import html as _html
import re

from sources import land

BASE = "https://www.pttiming.com"
SLUG = "primetime-timing-pttiming"
_XC_NAME = re.compile(r"cross[\s-]*country|\bx\.?c\.?\b|sectional|regional|conference|invitational|\binvite\b|"
                      r"championships?|classic|\bstate\b|district|\bopen\b", re.I)
_ROAD = re.compile(r"\b(\d+(\.\d+)?\s*k\b|run\b|walk|trot|carrera|half|marathon|triathlon|duathlon|"
                   r"golf|rampage|nordic|ski|relay|indoor|outdoor|track|field)", re.I)
_TRACK_TITLE = re.compile(r"\b(dash|hurdles?|steeple|relay|jump|vault|put|discus|javelin|hammer|"
                          r"\d{2,3}\s*m(?:eter)?s?\b)", re.I)


def _text(s):
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parseDate(text):
    """'November 6th - 7th, 2026' -> '2026-11-06'. Pure."""
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*-\s*(?:[A-Z][a-z]+\s+)?\d{1,2}(?:st|nd|rd|th)?)?"
                  r",\s*(\d{4})", text or "")
    if not m:
        return None
    try:
        return datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def parseIndex(page):
    """The results list -> [{uuid, url, name, date, place, state}] (each once). Pure."""
    out, seen = [], set()
    for block in re.split(r'<td class="d-md-none">', page or "")[1:]:
        a = re.search(r'href="(/event/([0-9a-f-]{36})\?pt=results)"[^>]*>(.*?)</a>', block, re.S)
        if not a or a.group(2) in seen:
            continue
        seen.add(a.group(2))
        when = re.search(r"<span>(.*?)</span>", block, re.S)
        where = re.search(r'class="small text-muted mt-1">(.*?)</div>', block, re.S)
        place = _text(where.group(1)).split("|")[0].strip() if where else ""
        st = re.search(r",\s*([A-Z]{2})$", place)
        out.append({"uuid": a.group(2), "url": BASE + a.group(1), "name": _text(a.group(3)),
                    "date": parseDate(_text(when.group(1)) if when else ""), "place": place,
                    "state": st.group(1) if st else None})
    return out


def isSchoolXC(ev):
    """A cross country meet by its name, not a road race. Pure."""
    name = ev.get("name") or ""
    if re.search(r"cross[\s-]*country|\bx\.?c\.?\b", name, re.I):
        return True
    return bool(_XC_NAME.search(name)) and not _ROAD.search(name)


def reportUrls(page):
    """An event page -> its races' HTML reports, in order, each once. Pure."""
    out = []
    for u in re.findall(r'https://dash\.pttiming\.com/f/[^"\\\s<>]+?\.html?', page or ""):
        if u not in out:
            out.append(u)
    return out


async def readMeet(fetcher, ev, max_age):
    """One event -> (LandedMeet | None, note)."""
    from sources import timers
    from sources.milesplit import isFallMeet
    got = await fetcher.fetch(ev["url"], max_age)
    if not got.body:
        return None, got.note or f"HTTP {got.status}"
    reports = reportUrls(got.body.decode("utf-8", "replace"))
    if not reports:
        return None, "no HTML reports on the event page"
    lm, why = await timers.mergeReports(fetcher, SLUG, ev["url"], reports, max_age)
    if lm is None:
        return None, why
    lm.url, lm.name = ev["url"], ev["name"] or lm.name
    lm.date = ev["date"] or lm.date
    lm.place, lm.state = ev["place"] or lm.place, ev["state"] or lm.state
    # the season says the sport (the list gives none; see the header)
    if isFallMeet(lm.date) and not any(_TRACK_TITLE.search(r.title or "") for r in lm.races):
        for r in lm.races:
            r.sport = "XC"
    return lm, "pttiming"


async def discover(fetcher, since_year, per_run, known, index_max_age=6 * 3600):
    got = await fetcher.fetch(f"{BASE}/results", index_max_age)
    if not got.body:
        return [], got.note or f"index HTTP {got.status}"
    evs = parseIndex(got.body.decode("utf-8", "replace"))
    today = datetime.date.today().isoformat()
    xc = [e for e in evs if e["date"] and e["date"][:4] >= str(since_year) and e["date"] < today
          and isSchoolXC(e)]
    new = [e for e in xc if e["url"] not in known]
    return new[:per_run], f"{len(evs)} events listed, {len(xc)} cross country; {min(len(new), per_run)} new"


def main(argv=None):
    import argparse
    import asyncio
    from sources.fetch import Fetcher, RawStore
    ap = argparse.ArgumentParser(description="PrimeTime Timing: parse, print (dry run)")
    ap.add_argument("--dry", action="store_true", required=True)
    ap.add_argument("--limit", type=int, default=3)
    a = ap.parse_args(argv)
    fetcher = Fetcher(RawStore())

    async def go():
        evs, note = await discover(fetcher, datetime.date.today().year, a.limit, set())
        print(f"[pttiming] {note}")
        for ev in evs:
            lm, why = await readMeet(fetcher, ev, 30 * 86400)
            print(f"  {land.summary(lm) if lm else ev['name']} -- {why}")
    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
