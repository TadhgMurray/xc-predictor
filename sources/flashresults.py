# Project: xc-predictor
# File:    sources/flashresults.py
# Purpose: Flash Results' own meet pages (flashresults.com/<season>_Meets/
#          <xc|Indoor|Outdoor>/<meet>/index.htm): conference, regional and
#          state championships.
#
#   isMeetIndex(html)          -> bool   the meet's index.htm
#   raceLinks(html, url)       -> [(url, title)] its races' result pages, not
#                                 their split pages
#   parseIndex(html)           -> (name, ISO date, place)
#   parseRace(html)            -> [Row]  one race's individual table
#
# ★ (2026-11-09) Flash Results was enabled as a Hy-Tek timer but publishes
#   its own layout, so every meet read "no individual races":
#   - the index: a table row per race, "<a href=002-1_compiled.htm><b>Men
#     8k</b></a> ... <b>Result</b>"; a split's row is in italics, "Split"
#   - a race: <table id='xc_indiv'> (cross country) or a results table, a
#     row "1 | logo | <b>First LAST</b><br><small>Team [SO]</small> | 22:03.5"
#   - the header: "<meet><br><small>City, ST | October 30, 2026</small>"
import datetime
import html as _html
import re
import urllib.parse

from sources.hytek import Row, secondsOf

_RACE_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_LINK = re.compile(r"<a\s+href\s*=\s*['\"]?([^'\"\s>]+_compiled\.htm)['\"]?\s*>(.*?)</a>", re.S | re.I)
_HEADER = re.compile(r"navbar-text[^>]*>(.*?)<br>\s*<small>(.*?)</small>", re.S | re.I)
_MONTHS = "january february march april may june july august september october november december".split()


def _text(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def isMeetIndex(page):
    head = page[:200000] if page else ""
    return "Flash Results" in head and bool(_LINK.search(head))


def parseIndex(page):
    m = _HEADER.search(page or "")
    name = _text(m.group(1)) if m else ""
    sub = _text(m.group(2)) if m else ""
    place, date = "", ""
    if "|" in sub:
        place, when = [x.strip() for x in sub.split("|", 1)]
        d = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:\s*-\s*\d{1,2})?,\s*(\d{4})", when)
        if d and d.group(1).lower() in _MONTHS:
            date = datetime.date(int(d.group(3)), _MONTHS.index(d.group(1).lower()) + 1,
                                 int(d.group(2))).isoformat()
    return name, date, place


def raceLinks(page, url):
    """The races' result pages, in page order: a row whose link reads in
    bold and is not a split ('Men 1.4k Split' rows are italic)."""
    out, seen = [], set()
    for row in _RACE_ROW.findall(page or ""):
        links = _LINK.findall(row)
        if not links:
            continue
        href, inner = links[0]
        title = _text(inner)
        if re.search(r"\bsplit\b", _text(row), re.I) or "<i>" in inner.lower():
            continue
        u = urllib.parse.urljoin(url, href)
        if u not in seen:
            seen.add(u)
            out.append((u, title))
    return out


def _splitName(name):
    """'Elsingi KIPRUTO' -> ('Kipruto', 'Elsingi')."""
    parts = name.split()
    caps = [i for i, p in enumerate(parts) if len(p) > 1 and p.replace("-", "").replace("'", "").isupper()]
    if caps and caps[0] > 0:
        return " ".join(parts[caps[0]:]).title(), " ".join(parts[:caps[0]])
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def parseRace(page):
    """One race page's individual results -> [Row]. The first table with an
    Athlete and a Time column; team scores are left alone."""
    for t in re.finditer(r"<table\b[^>]*>(.*?)</table>", page or "", re.S | re.I):
        body = t.group(1)
        heads = [_text(h).lower() for h in re.findall(r"<th\b[^>]*>(.*?)</th>", body, re.S | re.I)]
        if "athlete" not in heads or "time" not in heads:
            continue
        ia, it = heads.index("athlete"), heads.index("time")
        ip = heads.index("pl") if "pl" in heads else 0
        rows = []
        for tr in _RACE_ROW.findall(body):
            cells = re.findall(r"<td\b[^>]*>(.*?)(?=<td\b|</tr>|$)", tr, re.S | re.I)
            if len(cells) <= max(ia, it):
                continue
            who = cells[ia]
            name = _text((re.search(r"<b>(.*?)</b>", who, re.S | re.I) or re.search(r"(.*)", who)).group(1))
            small = re.search(r"<small>(.*?)</small>", who, re.S | re.I)
            team, grade = _text(small.group(1)) if small else "", ""
            g = re.match(r"^(.*?)\s*\[([^\]]*)\]\s*$", team)
            if g:
                team, grade = g.group(1).strip(), g.group(2).strip().upper()
            mark = _text(re.split(r"<br", cells[it], maxsplit=1, flags=re.I)[0])
            if not name:
                continue
            secs = secondsOf(mark) if re.match(r"^(\d{1,2}:)?\d{1,2}:\d{2}(\.\d+)?$|^\d+\.\d+$", mark) else None
            place = _text(cells[ip])
            last, first = _splitName(name)
            rows.append(Row(place=int(place) if place.isdigit() and secs else None, last=last, first=first,
                            grade=grade, school=team, seconds=secs, mark=mark))
        return rows
    return []
