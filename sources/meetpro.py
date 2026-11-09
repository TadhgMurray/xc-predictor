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
    xc: bool = False      # the page itself says cross country (a team score's spread)


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
    """'Girls 1600 Meters' -> 'F'. A title naming both ('MEN & WOMEN (High
    School) • 5 Kilometers Women's': a combined race scored by gender) is
    the gender it names LAST."""
    words = re.findall(r"\b(girls?|women'?s?|female|boys?|men'?s?|male)\b", title.lower())
    if not words:
        return ""
    return "F" if words[-1].startswith(("girl", "women", "female")) else "M"


def _isoDate(text):
    """'5/20/2022' -> '2022-05-20'. ! ALSO IN THE TIMER'S LOCALE (2026-11-09):
    '4/17/26' (Butler Track) and '2026-10-31' (a Canadian timer) both read
    as no date, and an undated meet cannot be rated."""
    m = re.search(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])", text)
    if m:
        y = m.group(3) if len(m.group(3)) == 4 else f"20{m.group(3)}"
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"(?<!\d)(20\d\d)-(\d{2})-(\d{2})(?!\d)", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


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
    seen = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        num = re.search(r'class="event-num"[^>]*>(.*?)</td>', row, re.S)
        ev = re.search(r'class="event"[^>]*>(.*?)</td>', row, re.S)
        res = re.search(r'class="results"[^>]*>.*?href=[\'"]([^\'"]+)[\'"]', row, re.S)
        if not ev:
            continue
        # ★ NO NUMBER CELL (MeetProTemplate_v1.3 / v1.6, 2026-11-09: Tiffin
        #   Timing, Goddard Schools): the event's number is the one in its
        #   page names, results_35.html / start_list_35.html -- and each
        #   round is its own row pointing at the same page (#round_1, _2),
        #   which parseRounds already splits, so a number is read once
        n = _text(num.group(1)) if num else ""
        if not n.isdigit():
            byHref = re.search(r"(?:results|start_list)_(\d+)\.html?", row, re.I)
            if num or not byHref:
                continue
            n = byHref.group(1)
        if int(n) in seen:
            continue
        seen.add(int(n))
        name = _text(ev.group(1))
        href = res.group(1).split("#")[0] if res else ""
        out.events.append(MeetEvent(int(n), name, _gender(name), metersOf(name), href,
                                    bool(re.search(r"\d\s*x\s*\d|relay|medley", name, re.I))))
    return out


# ! A MEETPRO PAGE SAYS SO IN ITS TEMPLATE, NOT IN ITS TEXT (2026-11-09: CAG's
#   Timing's homepage links to "MeetPro" and read as an empty MeetPro meet,
#   so the meets it links were never followed)
_MARK_PAGE = re.compile(rb"MeetProTemplate|\|\s*MeetPro by DirectAthletics|<!-- MeetPro \d")


def isMeetProPage(body):
    """True for a page MeetPro wrote (any template version)."""
    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    return bool(body and _MARK_PAGE.search(body[:20000]))


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


# ★ A ROUND IS ITS OWN RACE (2026-10-25, B&B Race KC, Winchester 2022): a
#   sprint's page carries "Prelims" and "Finals" (h4.round-results-hdr), each
#   with its own "place" table, so the same runner is on the page twice with
#   two real times. parseRounds keeps them apart; a qualifier letter on the
#   mark ("12.07Q", "4:43.59 q") is not part of the time.
_ROUND = re.compile(r'<h4[^>]*round-results-hdr[^>]*>(.*?)</h4>', re.S | re.I)
_TABLE = re.compile(r"<table[^>]*results-table[^>]*>(.*?)</table>", re.S | re.I)


# ! A TABLE INSIDE A ROW (2026-11-09, Trojan Timing's Scots Invitational,
#   template v1.3: each runner's splits are a <table class="splits-table"> in
#   the row). The results table's pattern stopped at the first inner
#   </table>, and every race read one runner. Inner tables go first.
_INNER_TABLE = re.compile(r"<table(?![^>]*results-table)[^>]*>(?:(?!<table\b).)*?</table>", re.S | re.I)


def withoutInnerTables(page):
    """A page less every table that holds no other table and is not a
    results table: split and relay-leg tables nested in a row. Pure."""
    prev = None
    while prev != page:
        prev, page = page, _INNER_TABLE.sub(" ", page)
    return page


def parseRounds(page):
    """A MeetPro results page -> [(round title, [Row])] in page order, the
    overall table of each round (heats skipped). One untitled round ('')
    when the page names none."""
    page = withoutInnerTables(page)
    marks = [(m.start(), _text(m.group(1))) for m in _ROUND.finditer(page)]
    out = []
    for t in _TABLE.finditer(page):
        rnd = ""
        for pos, title in marks:
            if pos < t.start():
                rnd = title
        rows = _tableRows(t.group(1))
        if rows is None:
            continue
        if out and out[-1][0] == rnd:
            out[-1][1].extend(rows)
        else:
            out.append((rnd, rows))
    return out


def parseResults(page):
    """A MeetPro results page -> the overall tables' rows, every round."""
    return [r for _rnd, rows in parseRounds(page) for r in rows]


def _tableRows(table):
    """One results table's rows, or None for a heat/section ('hplace') table."""
    rows = []
    header = re.search(r"<thead>(.*?)</thead>", table, re.S | re.I)
    classes = re.findall(r'<th[^>]*class="([^"]+)"', header.group(1) if header else "")
    if "place" not in [c.split()[0] for c in classes]:
        return None                    # an 'hplace' heat/section table
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S | re.I):
        c = _cells(tr)
        if not c.get("athlete"):
            continue
        mark = c.get("time") or c.get("mark") or ""
        mark = mark.split(" ")[0]
        # "12.07Q" / "4:43.59q": the qualifier is not the time
        mark = re.sub(r"(?<=\d)[A-Za-z*#]+$", "", mark)
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


# ★ THE ONE-PAGE MEETPRO (owner, 2026-11-08: "you ever scrape diablo
#   timing?"). MeetProTemplate_v2.0 -- Diablo Timing's mp/ folders -- puts a
#   whole meet on one page: a <section id="eventN" class="event"> per race,
#   its title in the header's bold cells ("Race #1", "6th Girls 1.25 Miles"),
#   a team scoring table, then the individual table (class "result-table",
#   no header cell for the place: 'athlete-hdr' spans it; the team cell is
#   'ath-team'). The index-and-event-pages parser above found no event rows
#   on it, so every such meet read as "no individual races".
_SECTION = re.compile(r'<section[^>]*id="event(\d+)"[^>]*>(.*?)</section>', re.S | re.I)
_ONE_TABLE = re.compile(r'<table[^>]*class="[^"]*\bresult-table\b[^"]*"[^>]*>(.*?)</table>', re.S | re.I)
# ★ TEMPLATE v1.0 (2026-11-09, Snake River Timing's meets on results.tfmeetpro
#   .com): the same sections, but the individual table is a plain "table"
#   whose cells are ath-place / ath-name / ath-team / ath-score
_ANY_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)


def _individualTable(body):
    m = _ONE_TABLE.search(body)
    if m:
        return m.group(1)
    for t in _ANY_TABLE.finditer(body):
        if re.search(r'class=["\']ath-name', t.group(1)):
            return t.group(1)
    return ""


def isSinglePage(page):
    return bool(_SECTION.search(page) and (_ONE_TABLE.search(page) or re.search(r'class=["\']ath-name', page)))


def parseSinglePage(page):
    """A one-page MeetPro meet -> (MeetIndex, {event number: [Row]})."""
    idx = MeetIndex()
    nav = re.search(r'nav-title[^>]*>\s*<h\d[^>]*>(.*?)</h\d>', page, re.S | re.I)
    title = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    idx.name = _text(nav.group(1)) if nav else (_text(title.group(1)) if title else "")
    info = re.search(r'class="[^"]*m-info[^"]*"[^>]*>(.*?)</div>', page, re.S | re.I)
    for t in (_text(x) for x in re.findall(r"<p[^>]*>(.*?)</p>", info.group(1) if info else "", re.S)):
        if not idx.date and _isoDate(t):
            idx.date = _isoDate(t)
        elif t and not idx.place and re.search(r",\s*[A-Z]{2}\b", t):
            idx.place = t
    results = {}
    # ! TWO SECTIONS CAN SHARE AN ID (Finish Line Results, 2026-11-09: a
    #   "MEN & WOMEN" race scored by gender is <section id="event5"> twice,
    #   boys then girls). Keyed by the id alone, the girls' rows replaced the
    #   boys' and landed under both titles: a repeat id gets its own number.
    used = set()
    for num, body in _SECTION.findall(page):
        cells = [_text(x) for x in re.findall(r'class="bold-cell"[^>]*>(.*?)</p>', body, re.S | re.I)]
        name = next((c for c in reversed(cells) if c and not re.match(r"race\s*#", c, re.I)), "")
        if not name:
            continue
        n = int(num)
        while n in used:
            n += 1000
        used.add(n)
        # a cross country team score: five scorers, displacers in brackets, a
        # 1-5 spread -- no track meet scores a race like that
        xc = bool(re.search(r'class="(?:spread|scoring-order)"', body))
        idx.events.append(MeetEvent(n, name, _gender(name), metersOf(name), f"#event{n}",
                                    bool(re.search(r"\d\s*x\s*\d|relay|medley", name, re.I)), xc))
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", _individualTable(body), re.S | re.I):
            c = _cells(tr)
            for short in ("name", "place", "score", "year", "grade", "yr"):
                if f"ath-{short}" in c and short not in c:
                    c[short] = c[f"ath-{short}"]
            c.setdefault("athlete", c.get("name", ""))
            if not c.get("athlete"):
                continue
            mark = (c.get("time") or c.get("mark") or "").split(" ")[0]
            mark = re.sub(r"(?<=\d)[A-Za-z*#]+$", "", mark)
            last, first = _splitName(c["athlete"])
            place = c.get("place", "")
            rows.append(Row(
                place=int(place) if place.isdigit() else None,
                last=last, first=first,
                grade=(c.get("year") or c.get("grade") or c.get("yr") or "").upper(),
                school=c.get("ath-team") or c.get("team", ""),
                seconds=secondsOf(mark), mark=mark or place,
                points=int(c["score"]) if c.get("score", "").isdigit() else None))
        results[n] = rows
    return idx, results
