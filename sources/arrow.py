# Project: xc-predictor
# File:    sources/arrow.py
# Purpose: Arrow Live Results (arrowliveresults.com): one adapter for every
#          timer that posts there (34 in docs/timing_companies.csv: Above and
#          Beyond, RCC Timing, 12-1 Running, Terkhorn ...).
#
#   python -m sources.arrow --dry [--states IN,MI] [--limit 5]   # parse, print, write nothing
#
# THE SITE (read 2026-11-10)
#   /Home/pvStates                                  the state ids (STATES below)
#   /Home/pvRecentEvents?pStateId=18&pCountryId=1&showAll=false
#                                                   a state's recent races: date,
#                                                   name, link, timer, address
#   /<RaceSlug>                                     one race's page: its EventId
#   /Results/pvResults?pEventId=25700&pDivision=Overall&pChipTime=false
#                                                   that race's results table:
#                                                   place, bib, name, team,
#                                                   gender, age, finish
# ! ONE RACE PER PAGE. A meet is the races one timer lists for one day at one
#   address ("2026 IHSAA Cross Country Sectional 22 - Boys" / "- Girls");
#   group() puts them back together, so a meet lands once.
# ! NO GRADE, AN AGE. A race whose runners are mostly 25+ is a community
#   race, mostly 10 and under an elementary one: neither is landed.
# ! FETCHED LIKE EVERY TIMER: sources.fetch.Fetcher -- its settings, one
#   request at a time to the host, robots.txt honoured, every body kept.
import datetime
import html as _html
import re
import urllib.parse
from collections import Counter

from sources import hytek, land

BASE = "https://www.arrowliveresults.com"
SLUG = "arrow-live-results"           # meets_ext.source 'timer:arrow-live-results'

# /Home/pvStates, 2026-11-10 (61 'Other' left out)
STATES = {2: "AL", 3: "AK", 6: "AR", 7: "CA", 8: "CO", 9: "CT", 12: "FL", 13: "GA", 17: "IL",
          18: "IN", 21: "KY", 22: "LA", 27: "MI", 29: "MN", 30: "MS", 31: "MO", 34: "NV",
          36: "NJ", 37: "NM", 38: "NY", 39: "NC", 42: "OH", 43: "OK", 44: "OR", 49: "SC",
          51: "TN", 52: "TX", 55: "VA", 57: "WA", 59: "WI"}

_XC = re.compile(r"cross\s*country|\bx\.?c\.?\b|\bcc\b", re.I)
_NOT_XC = re.compile(r"\b(5k\s*run\s*/?\s*walk|fun\s*run|walk|half\s*marathon|marathon|trot|"
                     r"color\s*run|triathlon|duathlon|road|relays?|hokum\s*karem)\b", re.I)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _text(s):
    return " ".join(_html.unescape(_TAG.sub(" ", s or "")).split())


def indexUrl(state_id, show_all=False):
    return (f"{BASE}/Home/pvRecentEvents?pStateId={state_id}&pCountryId=1"
            f"&showAll={'true' if show_all else 'false'}")


def resultsUrl(event_id):
    return f"{BASE}/Results/pvResults?pEventId={event_id}&pDivision=Overall&pChipTime=false"


def _iso(mdy):
    try:
        return datetime.datetime.strptime(mdy.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parseIndex(page):
    """A state's recent-events page -> [{date, name, url, timer, address,
    state}], as listed. Pure."""
    out = []
    for row in _ROW.findall(page or ""):
        tds = _TD.findall(row)
        link = re.search(r'href="([^"]+)"', row)
        if len(tds) < 5 or not link:
            continue
        date = _iso(_text(tds[0]))
        address = _text(tds[-1])
        st = re.search(r"\b([A-Z]{2})\s+United States\s*$", address)
        out.append({"date": date, "name": _text(tds[1]),
                    "url": link.group(1).replace("http://", "https://"),
                    "timer": _text(tds[-2]), "address": address,
                    "state": st.group(1) if st else None})
    return out


def isXC(name):
    """A cross country race by its listed name (not a road race). Pure."""
    return bool(_XC.search(name or "")) and not _NOT_XC.search(name or "")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


_GENDER_TAIL = re.compile(r"\s*[-:]\s*[^-:]*$")


def meetName(names):
    """The meet's name from its races' names: their common part, the race's
    own tail (' - Boys', ' - HS Girls 5K') taken off. Pure."""
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        n = names[0]
        return _GENDER_TAIL.sub("", n) if re.search(r"\s[-:]\s", n) else n
    words = [n.split() for n in names]
    common = []
    for ws in zip(*words):
        if len(set(w.lower() for w in ws)) != 1:
            break
        common.append(ws[0])
    name = " ".join(common).rstrip(" -:")
    if len([w for w in name.split() if not w.isdigit()]) >= 2:
        return name
    # little in common ("2026 Culver MS Boys XC" / "2026 HS Girls Culver XC
    # Invitational"): the longest name, less its own tail and gender words
    best = max(names, key=len)
    best = re.split(r"\s[-:]\s", best)[0]
    best = re.sub(r"\b(boys?|girls?|men'?s?|women'?s?|hs|ms|jh)\b", " ", best, flags=re.I)
    return " ".join(best.split())


_STEM_DROP = re.compile(r"\b(boys?|girls?|men'?s?|women'?s?|hs|ms|jh|jv|varsity|open|"
                        r"middle\s+school|high\s+school|\d+\s*k|\d+(\.\d+)?\s*miles?)\b", re.I)


def _stem(name):
    """A race name less its own tail and gender/level words: what the races
    of one meet share. Pure."""
    n = re.split(r"\s[-:]\s", name or "")[0]
    return _slug(_STEM_DROP.sub(" ", n))


def group(entries):
    """Index entries -> meets: the races one timer lists for one day. Two
    races are one meet when their address or their name's stem is the same
    (! not either alone: "Fishers, IN 10910 Elle" on one race of the 2026
    Midwest Championship; "Culver MS" / "HS Boys Culver XC Invitaitonal" at
    one address). [{url, date, name, timer, address, state, races}],
    newest first. Pure."""
    entries = [e for e in entries if e.get("date")]
    parent = list(range(len(entries)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, e in enumerate(entries):
        for j in range(i):
            f = entries[j]
            if (e["date"], _slug(e["timer"])) != (f["date"], _slug(f["timer"])):
                continue
            if _slug(e["address"]) == _slug(f["address"]) or _stem(e["name"]) == _stem(f["name"]):
                parent[find(i)] = find(j)
    meets = {}
    for i, e in enumerate(entries):
        m = meets.setdefault(find(i), {"date": e["date"], "timer": e["timer"], "address": e["address"],
                                       "state": e["state"], "races": []})
        if e["url"] not in [r["url"] for r in m["races"]]:
            m["races"].append(e)
    out = []
    for m in meets.values():
        m["races"].sort(key=lambda r: r["url"])
        m["name"] = meetName([r["name"] for r in m["races"]])
        # a stable key for meets_ext.url / ext_seen: the day, the timer and
        # the meet's first race page
        m["url"] = f"{BASE}/#meet/{m['date']}/{_slug(m['timer'])[:60]}/{m['races'][0]['url'].rsplit('/', 1)[-1][:80]}"
        out.append(m)
    return sorted(out, key=lambda m: (m["date"], m["url"]), reverse=True)


def eventId(page):
    m = re.search(r"var\s+EventId\s*=\s*(\d+)", page or "")
    return int(m.group(1)) if m else None


def _seconds(mark):
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$", (mark or "").strip())
    if not m:
        return None
    return round(int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2)


def parseResults(page):
    """A race's results table -> [(Row, gender, age)]. Pure."""
    body = (page or "").split("resultsTableBody", 1)
    if len(body) < 2:
        return []
    out = []
    for row in _ROW.findall(body[1]):
        tds = _TD.findall(row)
        if len(tds) < 7:
            continue
        place = _text(tds[0])
        a = re.search(r"<a[^>]*>(.*?)</a>", tds[2], re.S)
        name = _text(a.group(1)) if a else _text(re.sub(r"<text.*?</text>", "", tds[2], flags=re.S))
        team = _text(tds[3])
        gender = _text(tds[4]).upper()[:1]
        age = _text(tds[5])
        mark = _text(re.sub(r"<br.*", "", tds[6], flags=re.S))
        secs = _seconds(mark)
        status = None if secs else (mark.upper() if re.match(r"^(DNF|DNS|DQ|DSQ)$", mark, re.I) else None)
        if not name or (secs is None and status is None):
            continue
        parts = name.split()
        first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", name)
        out.append((hytek.Row(place=int(place) if place.isdigit() and secs else None,
                              last=last, first=first, grade="", school=team,
                              seconds=secs, mark=status or mark),
                    gender if gender in ("M", "F") else "",
                    int(age) if age.isdigit() else None))
    return out


def _gender(title, rows):
    t = (title or "").lower()
    if re.search(r"\b(women|girls?|ladies)\b", t):
        return "F"
    if re.search(r"\b(men|boys?)\b", t):
        return "M"
    g = Counter(x[1] for x in rows if x[1])
    return g.most_common(1)[0][0] if g and g.most_common(1)[0][1] >= 0.9 * sum(g.values()) else ""


def _ageKind(rows):
    """'community' when most runners are 25+, 'elementary' when most are 10
    and under, else ''. Pure."""
    ages = [x[2] for x in rows if x[2]]
    if len(ages) < 3:
        return ""
    if sum(a >= 25 for a in ages) > 0.5 * len(ages):
        return "community"
    if sum(a <= 10 for a in ages) > 0.5 * len(ages):
        return "elementary"
    return ""


def events(race_title, rows, number):
    """One Arrow race -> hytek.Events: one, or one per gender when its
    runners are of both (a combined start, each gender placed in its own
    order). Pure."""
    meters = hytek.metersOf(race_title)
    g = _gender(race_title, rows)
    if g:
        return [_event(number, g, race_title, meters, [x[0] for x in rows])]
    out = []
    for k, sex in enumerate(("F", "M")):
        mine = [x[0] for x in rows if x[1] == sex]
        if mine:
            word = "Girls" if sex == "F" else "Boys"
            out.append(_event(number * 10 + k, sex, f"{word} {race_title}", meters, mine))
    return out


def _event(number, gender, title, meters, rows):
    e = hytek.Event(number=number, gender=gender, title=title, meters=meters, rows=rows)
    e.sport = "XC"
    return e


_LONG_DATE = re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
                        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")


def meetFromPage(page, url):
    """One race page (an uploaded link) -> a meet of that race, as group()
    gives them. Pure."""
    title = _text((re.search(r"<title>(.*?)</title>", page or "", re.S) or [None, ""])[1])
    text = _text(re.sub(r"<script.*?</script>", " ", page or "", flags=re.S))
    d = _LONG_DATE.search(text)
    date = None
    if d:
        try:
            date = datetime.datetime.strptime(d.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            date = None
    st = re.search(r"\b([A-Z]{2})\s+\d{5}\b", text)
    addr = re.search(r"Presented By\s+(.*?)\s+United States", text)
    race = {"url": url, "name": title, "date": date, "timer": "", "address": addr.group(1) if addr else "",
            "state": st.group(1) if st else None}
    return {"url": url, "date": date, "timer": "", "address": race["address"], "state": race["state"],
            "name": meetName([title]), "races": [race]}


async def readMeet(fetcher, meet, max_age):
    """One grouped meet: each race's page (for its EventId) and results ->
    (LandedMeet | None, note)."""
    m = hytek.Meet(name=meet["name"], date=meet["date"], venue=meet["address"],
                   licensee=meet["timer"])
    skipped = Counter()
    for k, race in enumerate(meet["races"], 1):
        page = await fetcher.fetch(race["url"], max_age)
        if not page.body:
            return None, page.note or f"HTTP {page.status} on {race['url']}"
        eid = eventId(page.body.decode("utf-8", "replace"))
        if eid is None:
            skipped["no event id"] += 1
            continue
        got = await fetcher.fetch(resultsUrl(eid), max_age)
        if not got.body:
            return None, got.note or f"HTTP {got.status} on results {eid}"
        rows = parseResults(got.body.decode("utf-8", "replace"))
        kind = _ageKind(rows)
        if kind:
            skipped[kind] += 1
            continue
        title = (race["name"][len(meet["name"]):].strip(" -:")
                 if race["name"].startswith(meet["name"]) else race["name"]) or race["name"]
        m.events.extend(events(title, rows, k))
    lm = land.fromHytek(m, SLUG, meet["url"])
    lm.place, lm.state = meet["address"], meet["state"] or lm.state
    note = "arrow" + (f" (left out: {dict(skipped)})" if skipped else "")
    return lm, note


async def discover(fetcher, since_year, per_run, known, states=None, index_max_age=6 * 3600):
    """([meet], note): up to per_run cross country meets not yet landed,
    newest first, from every state's recent list."""
    entries, failed = [], []
    # ★ A THIRD OF THE STATES A NIGHT (sources.rotate): the rest from the store
    from sources import rotate
    want = [sid for sid, st in STATES.items() if not states or st in states]
    now = rotate.due(getattr(fetcher, "store", None), [indexUrl(sid) for sid in want])
    for sid, st in STATES.items():
        if states and st not in states:
            continue
        got = await fetcher.fetch(indexUrl(sid), index_max_age if indexUrl(sid) in now
                                  else rotate.FOREVER)
        if not got.body:
            failed.append(st)
            continue
        entries.extend(e for e in parseIndex(got.body.decode("utf-8", "replace"))
                       if e["date"] and e["date"][:4] >= str(since_year) and isXC(e["name"]))
    meets = [m for m in group(entries) if m["url"] not in known]
    note = (f"{len(entries)} cross country races in {len(group(entries))} meets"
            + (f"; index failed for {','.join(failed)}" if failed else "") + f"; {min(len(meets), per_run)} new")
    return meets[:per_run], note


def main(argv=None):
    import argparse
    import asyncio
    from sources.fetch import Fetcher, RawStore
    ap = argparse.ArgumentParser(description="Arrow Live Results: parse, print (dry run)")
    ap.add_argument("--dry", action="store_true", required=True)
    ap.add_argument("--states", default="")
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args(argv)
    fetcher = Fetcher(RawStore())
    states = {s.strip().upper() for s in a.states.split(",") if s.strip()} or None

    async def go():
        meets, note = await discover(fetcher, datetime.date.today().year, a.limit, set(), states)
        print(f"[arrow] {note}")
        for meet in meets:
            lm, why = await readMeet(fetcher, meet, 30 * 86400)
            print(f"  {land.summary(lm) if lm else meet['name']} -- {why}")
    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
