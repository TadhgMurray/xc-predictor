# Project: xc-predictor
# File:    sources/meetpro.py
# Purpose: MeetPro (DirectAthletics' meet software) results sites -- one
#          layout for every timer that hosts on it: results.tfmeetpro.com/
#          <Timer>/<Meet>/ (Mitchell Timing, B&B Race KC, MAC-FAT, Westside)
#          and copies on timers' own sites (CFPI, CAG's, rtspt.com .../mp/).
#
#   parseMeetIndex(html) -> MeetIndex(name, place, date, events[])
#       the meet's index.html: one row per event, its number, title and the
#       results page (results_<n>.html)
#   parseResults(html)   -> [Row]  (sources.hytek.Row, so both feed one lander)
#
# ★ ONLY THE "place" TABLE. A results page holds the event's overall table
#   (header class "place") and then every heat or section again (class
#   "hplace"): read both and every runner counts twice. (B&B Race KC, 2A
#   Region 20 Winchester 2022, results_29.html.)
# ! THE CELLS ARE CLASSED, NOT POSITIONAL: athlete, team, time/mark, place,
#   and a year/grade cell when the meet records one -- many do not (the
#   Winchester meet above has none), so grade is '' there and the linkers
#   go on name and team.
# ! "Daniel LITTLE": MeetPro writes the last name in capitals after the first.

import html as _html
import re
from dataclasses import dataclass, field

from sources.hytek import Row, metersOf, secondsOf


@dataclass
class MeetEvent:
    number: int
    title: str            # 'Boys 1600 Meters'
    gender: str           # 'M' / 'F' / ''
    meters: float         # None when the title names no distance
    results_href: str     # relative, e.g. 'results_29.html'; '' before results
    relay: bool = False   # '4 x 800m Relay' -- its meters are a leg's, not a race's


@dataclass
class MeetIndex:
    name: str = ""
    place: str = ""       # 'Winchester, KS'
    date: str = ""        # ISO
    events: list = field(default_factory=list)


def _text(fragment):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))
                  .replace("\xa0", " ")).strip()


def _gender(title):
    t = title.lower()
    if re.search(r"\b(girls|women|female)\b", t):
        return "F"
    if re.search(r"\b(boys|men|male)\b", t):
        return "M"
    return ""


def _isoDate(text):
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""


def parseMeetIndex(page):
    """A MeetPro meet's index.html -> MeetIndex."""
    out = MeetIndex()
    title = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    if title:
        out.name = _text(title.group(1)).split(" | ")[0]
    # <h3><a class="home-link-hdr">Meet</a><span>City, ST<br></span><span>5/20/2022</span></h3>
    hdr = re.search(r'home-link-hdr[^>]*>.*?</a>(.*?)</h\d>', page, re.S | re.I)
    for t in (_text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", hdr.group(1) if hdr else "", re.S)):
        if not out.date and _isoDate(t):
            out.date = _isoDate(t)
        elif t and not out.place:
            out.place = t
    if not out.date:
        out.date = _isoDate(_text(page[:4000]))
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        num = re.search(r'class="event-num"[^>]*>(.*?)</td>', row, re.S)
        ev = re.search(r'class="event"[^>]*>(.*?)</td>', row, re.S)
        if not (num and ev and _text(num.group(1)).isdigit()):
            continue
        res = re.search(r'class="results"[^>]*>.*?href=[\'"]([^\'"]+)[\'"]', row, re.S)
        name = _text(ev.group(1))
        out.events.append(MeetEvent(int(_text(num.group(1))), name, _gender(name),
                                    metersOf(name), res.group(1) if res else "",
                                    bool(re.search(r"\d\s*x\s*\d|relay|medley", name, re.I))))
    return out


def _splitName(name):
    """'Daniel LITTLE' -> ('Little', 'Daniel'); 'Little, Daniel' -> ('Little', 'Daniel')."""
    name = re.sub(r"\s+", " ", name).strip()
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    parts = name.split(" ")
    caps = [i for i, p in enumerate(parts) if len(p) > 1 and p.isupper()]
    if caps and caps[0] > 0:
        return " ".join(parts[caps[0]:]).title(), " ".join(parts[:caps[0]])
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def _cells(row):
    return {m.group(1).split()[0]: _text(m.group(2))
            for m in re.finditer(r'<td[^>]*class="([^"]+)"[^>]*>(.*?)</td>', row, re.S | re.I)}


def parseResults(page):
    """A MeetPro results page -> the overall table's rows (heats skipped)."""
    rows = []
    for table in re.findall(r"<table[^>]*results-table[^>]*>(.*?)</table>", page, re.S | re.I):
        header = re.search(r"<thead>(.*?)</thead>", table, re.S | re.I)
        classes = re.findall(r'<th[^>]*class="([^"]+)"', header.group(1) if header else "")
        if "place" not in [c.split()[0] for c in classes]:
            continue                       # an 'hplace' heat/section table
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S | re.I):
            c = _cells(tr)
            if not c.get("athlete"):
                continue
            mark = c.get("time") or c.get("mark") or ""
            mark = mark.split(" ")[0]
            last, first = _splitName(c["athlete"])
            place = c.get("place", "")
            rows.append(Row(
                place=int(place) if place.isdigit() else None,
                last=last, first=first,
                grade=(c.get("year") or c.get("grade") or c.get("yr") or "").upper(),
                school=c.get("team", ""),
                seconds=secondsOf(mark), mark=mark or (place or ""),
                points=int(c["points"]) if c.get("points", "").isdigit() else None))
    return rows
