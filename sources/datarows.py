# Project: xc-predictor
# File:    sources/datarows.py
# Purpose: Results pages that carry each finisher in the row's own data
#          attributes -- EliteFeats' live results (elitefeats.com/xc-results
#          ?ID=<meet>):
#
#   <tr data-bib="141" data-name="First Last" data-team="North (Del.)"
#       data-grade="FR" data-div="Men 8K" data-sec="1566.8" data-place="1">
#
#   One page is one division; a <select id="xrDivSelect"> lists the others
#   ('./?ID=26305&Div=Women+5K'), each the same page with its own Div.
#
#   isDataPage(html)               -> bool
#   divisionUrls(html, url)        -> [url of each other division's page]
#   parsePage(html, url)           -> sources.hytek.Meet (one event per division)
#
# ★ (2026-11-09) EliteFeats' results index links these pages, but nothing
#   could read them -- Hy-Tek text they are not -- so the timer gave nothing.
import datetime
import html as _html
import re
import urllib.parse

from sources.hytek import Event, Meet, Row, metersOf

_ROW = re.compile(r"<tr\b([^>]*\bdata-sec\s*=[^>]*)>", re.I)
_ATTR = re.compile(r'\bdata-([a-z]+)\s*=\s*"([^"]*)"', re.I)
_SELECT = re.compile(r'<select[^>]*id="xrDivSelect"[^>]*>(.*?)</select>', re.S | re.I)
_DAY = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?!\d)")


def isDataPage(page):
    head = page[:400000] if page else ""
    return bool(_ROW.search(head) and re.search(r'\bdata-name\s*=', head))


def divisionUrls(page, url):
    """The page of every division the picker offers, the current one left out."""
    m = _SELECT.search(page or "")
    sp = urllib.parse.urlsplit(url)
    here = dict(urllib.parse.parse_qsl(sp.query))
    out = []
    for opt in re.finditer(r"<option\b[^>]*>", m.group(1) if m else "", re.I):
        if re.search(r"\bselected\b", opt.group(0), re.I):
            continue                                  # the page itself
        value = re.search(r'value="([^"]*)"', opt.group(0))
        value = value.group(1) if value else ""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_html.unescape(value)).query))
        if not q:
            continue
        q = {("div" if k.lower() == "div" else k): v for k, v in q.items()}
        if q.get("div") and q.get("div") == (here.get("div") or here.get("Div")):
            continue
        u = urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path, urllib.parse.urlencode(q), ""))
        if u != url and u not in out:
            out.append(u)
    return out


def _gender(div):
    d = div.lower()
    if re.search(r"\b(girls?|women'?s?|female)\b", d):
        return "F"
    if re.search(r"\b(boys?|men'?s?|male)\b", d):
        return "M"
    return ""


def _splitName(name):
    parts = name.split()
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def parsePage(page, url=""):
    """One data-rows page -> Meet: its divisions as events, each runner once."""
    text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", page or "")))
    title = re.search(r"<title>(.*?)</title>", page or "", re.S | re.I)
    name = re.sub(r"\s+", " ", _html.unescape(title.group(1))).strip() if title else ""
    name = re.split(r"\s+(?:elitefeats\s+)?results\b", name, flags=re.I)[0].strip()
    meet = Meet(name=name)
    day = re.search(r"(?:Cross Country|Track(?: & Field)?|Indoor|Outdoor)\s*·\s*(\d{1,2}/\d{1,2}/\d{2,4})"
                    r"\s*·\s*([^<·]+)", _html.unescape(page or ""))
    d = _DAY.search(day.group(1) if day else name)
    if d:
        y = int(d.group(3)) + (2000 if len(d.group(3)) == 2 else 0)
        try:
            meet.date = datetime.date(y, int(d.group(1)), int(d.group(2))).isoformat()
        except ValueError:
            pass
    if day:
        meet.venue = day.group(2).strip()
    events, seen = {}, set()
    for attrs in _ROW.findall(page or ""):
        a = {k.lower(): _html.unescape(v).strip() for k, v in _ATTR.findall(attrs)}
        div, nm = a.get("div", ""), a.get("name", "")
        if not nm:
            continue
        e = events.get(div)
        if e is None:
            e = events[div] = Event(number=len(events) + 1, gender=_gender(div), title=div,
                                    meters=metersOf(div))
        key = (div, nm.lower(), a.get("bib"))
        if key in seen:
            continue
        seen.add(key)
        try:
            secs = float(a.get("sec") or "") or None
        except ValueError:
            secs = None
        place = a.get("place", "")
        last, first = _splitName(nm)
        e.rows.append(Row(place=int(place) if place.isdigit() and secs else None, last=last, first=first,
                          grade=a.get("grade", "").upper(), school=a.get("team", ""), seconds=secs,
                          mark="" if secs else "DNF"))
    meet.events = list(events.values())
    if re.search(r"/xc-results\b", url or "", re.I) or re.search(r"\bCross Country\b", text[:20000]):
        for e in meet.events:
            e.sport = "XC"
    return meet
