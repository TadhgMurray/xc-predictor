# Project: xc-predictor
# File:    sources/meetreport.py
# Purpose: Results reports that are not Hy-Tek's -- the layouts timers'
#          PDFs came in when sources.hytek read no races (2026-11-01):
#
#   "OFFICIAL MEET REPORT" (MeetPro's desktop print, Hero's Timing):
#       Race #3 / Girls 5K, then INDIVIDUAL RESULTS with an "Athlete YR Team
#       Score Time" header; the page's right half carries the same race's
#       results on from the left ("TEAM SCORING SUMMARY (cont'd)").
#   Two-column "Individual Results" (Monroe County Coaches Invitational,
#       YenTiming's per-race PDFs): "Race 4 - Girls Varsity - 5K", then
#       "Place Name Grade Team Time Points" printed twice across the page,
#       a long name wrapping onto the next line.
#
#   parseReport(text, url='') -> sources.hytek.Meet (so land.fromHytek lands it)
#
# ★ COLUMNS FROM THE HEADER. Every "Name"/"Athlete" word on a header line
#   starts a column; a row in that column is a place number just before the
#   column with a name starting at it (+-3). A column stays open until the
#   next race, so a right-hand column that started above the left one keeps
#   being read.
# ! A ROW: place, name, grade (when printed), team, then the time; a team
#   score ("30", "(30)", "-") may sit before or after the time. A line that
#   does not fit is skipped and counted, never guessed.
import datetime
import re

from sources.hytek import Event, Meet, Row, metersOf, secondsOf, _splitName

_RACE = re.compile(r"^\s*Race\s*#?\s*(\d+)\b\s*[-:]?\s*(.*?)\s*$", re.I)
_HEADER_NAME = re.compile(r"\b(Name|Athlete)\b")
_MARK = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{1,2})?"
# after the time: team points (Monroe), and/or a gap behind the winner
# ('---', '11.4', '4:25.1': Hero's Dan Conway report) -- the time is the
# FIRST mark, so a gap that looks like a time is never read as one
_GAP = r"(?:-{1,3}|\+?(?:\d{1,2}:)?\d{1,2}\.\d{0,2})"         # '10:28.' when the page cut it
_ROW = re.compile(rf"^(?P<mid>.+?)\s+(?P<mark>{_MARK}|DNF|DNS|DQ)\b\s*(?P<pts>\(?\d+\)?|-)?"
                  rf"(?:\s+{_GAP})?\s*$")
_SCORE = re.compile(r"^(\(?\d+\)?|-)$")
_GRADE = re.compile(r"^(\d{1,2}|FR|SO|JR|SR|GR|R?FR|R?SO|R?JR|R?SR)$", re.I)
_GRADE_LEAD = re.compile(r"^(\d{1,2}|FR|SO|JR|SR|GR)\s+(\S.*)$", re.I)
_GRADE_TAIL = re.compile(r"^(.*\S)\s+(\d{1,2}|FR|SO|JR|SR|GR)$", re.I)
_MONTHS = "january february march april may june july august september october november december".split()
_DATE_WORDS = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
_DATE_SLASH = re.compile(r"(?<!printed: )\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_URL_DATE = re.compile(r"(?:^|/)(\d{1,2})-(\d{1,2})-(\d{2,4})(?=[-/_])")
_XC_SCORING = re.compile(r"Scoring Order|Finish Order|TEAM SCORING SUMMARY|\b\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}\b")
_TRAILER = re.compile(r"\s{2,}(Final Results|Results|Official)\s*$", re.I)


def isReport(text):
    """True when the text looks like one of these layouts."""
    return bool(re.search(r"^\s*Race\s*#?\s*\d+", text, re.M | re.I)
                and re.search(r"Individual Results", text, re.I))


def _gender(title):
    t = title.lower()
    if re.search(r"\b(girls?|women'?s?|female)\b", t):
        return "F"
    if re.search(r"\b(boys?|men'?s?|male)\b", t):
        return "M"
    return ""


def _meters(title):
    m = metersOf(title)
    if m:
        return m
    # 'JH Girls 3200': a bare distance in meters
    n = re.search(r"\b(\d{3,5})\s*(m|meters?)?\b", title, re.I)
    if n and 800 <= int(n.group(1)) <= 12000:
        return float(n.group(1))
    return None


def _date(lines, url):
    for line in lines[:12]:
        if "printed" in line.lower():
            continue
        m = _DATE_WORDS.search(line)
        if m:
            return datetime.date(int(m.group(3)), _MONTHS.index(m.group(1).lower()) + 1,
                                 int(m.group(2))).isoformat()
        m = _DATE_SLASH.search(line)
        if m:
            return datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    m = _URL_DATE.search(url or "")
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return datetime.date(y, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return ""
    return ""


def _headCols(line):
    return [m.start() for m in _HEADER_NAME.finditer(line)] if "Time" in line else []


def _placeAt(line, col, lo):
    """The place token of the row in the column at `col`: a number ending
    just before a name that starts within 3 of col. -> match start, or None."""
    for m in re.finditer(r"(\d{1,4})\s+(?=[A-Za-z'])", line):
        if m.start() >= lo and col - 3 <= m.end() <= col + 4:
            return m.start()
    return None


def parseRow(piece):
    """'36 MACHO, Isabella  JR Ely  30  24:00.3' -> Row, or None. Pure."""
    m = re.match(r"^\s*(\d{1,4})\s+(.*)$", piece)
    if not m:
        return None
    place, rest = int(m.group(1)), m.group(2)
    r = _ROW.match(rest)
    if not r:
        return None
    chunks = [c.strip() for c in re.split(r"\s{2,}", r.group("mid").strip()) if c.strip()]
    while len(chunks) > 1 and _SCORE.match(chunks[-1]):
        chunks.pop()
    if not chunks:
        return None
    name, grade, team = chunks[0], "", ""
    rest_chunks = chunks[1:]
    if not rest_chunks:
        g = re.match(r"^(.*?\S)\s+(\d{1,2}|FR|SO|JR|SR)\s+(\S.*)$", name, re.I)
        if not g:
            return None
        name, grade, team = g.group(1), g.group(2), g.group(3)
    else:
        t = _GRADE_TAIL.match(name)
        if t and len(rest_chunks) == 1 and not _GRADE.match(rest_chunks[0]):
            name, grade = t.group(1), t.group(2)
            team = rest_chunks[0]
        elif _GRADE.match(rest_chunks[0]):
            grade, team = rest_chunks[0], " ".join(rest_chunks[1:])
        else:
            lead = _GRADE_LEAD.match(rest_chunks[0])
            if lead:
                grade, team = lead.group(1), " ".join([lead.group(2)] + rest_chunks[1:])
            else:
                team = " ".join(rest_chunks)
    if not re.search(r"[A-Za-z]", name):
        return None
    last, first = _splitName(name)
    mark = r.group("mark")
    secs = secondsOf(mark) if re.match(_MARK, mark) else None
    return Row(place=place if secs else None, last=last, first=first, grade=grade.upper(),
               school=team, seconds=secs, mark=mark)


def parseReport(text, url=""):
    """The report's races. Pure."""
    lines = text.splitlines()
    head = [re.sub(r"\s{2,}.*$", "", ln.strip()) for ln in lines[:8] if ln.strip()]
    meet = Meet(name=head[0] if head else "", date=_date(lines, url))
    venue = []
    for ln in head[1:]:
        if _RACE.match(ln) or _DATE_WORDS.search(ln) or "REPORT" in ln.upper():
            break
        venue.append(ln)
    meet.venue = ", ".join(venue)
    events, cur, cols, last_row = {}, None, [], {}
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        rm = _RACE.match(line)
        if rm:
            num, title = int(rm.group(1)), _TRAILER.sub("", rm.group(2)).strip()
            if not title:
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    title = _TRAILER.sub("", lines[j].strip()).strip()
                    i = j
            if num not in events:
                events[num] = Event(number=num, gender=_gender(title), title=title,
                                    meters=_meters(title))
            cur, cols, last_row = events[num], [], {}
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        hc = _headCols(line)
        if hc:
            for c in hc:
                if all(abs(c - o) > 3 for o in cols):
                    cols.append(c)
            cols.sort()
            # ! a header line can carry a row of a column opened earlier
            #   ('Athlete YR Team Score Time Gap    46 TOCHTERMAN, Euge ...')
        starts = []
        lo = 0
        for c in cols:
            s = _placeAt(line, c, lo)
            starts.append(s)
            if s is not None:
                lo = s + 1

        def pieceAt(k):
            nxt = starts[k + 1] if k + 1 < len(cols) else None
            if nxt is None and k + 1 < len(cols):
                nxt = max(starts[k] + 1, cols[k + 1] - 3)
            return line[starts[k]:nxt]
        # a "place" whose piece holds no time is the left row's points
        # before a wrapped name ('8          Slovik-Rodgers'), not a row
        for k in range(len(cols)):
            if starts[k] is not None and not re.search(_MARK + r"|DNF|DNS|DQ", pieceAt(k)):
                starts[k] = None
        for k, c in enumerate(cols):
            s = starts[k]
            if s is None:
                # a name wrapped onto this line: one word sitting in the column
                frag = line[max(0, c - 3):(cols[k + 1] - 3) if k + 1 < len(cols) else None].strip()
                prev = last_row.get(c)
                if prev and re.fullmatch(r"[A-Za-z][A-Za-z'\-]+", frag or "") and not prev.first:
                    prev.first, prev.last = prev.last, frag
                continue
            piece = pieceAt(k)
            row = parseRow(piece)
            if row is None:
                if re.search(_MARK, piece):
                    cur.skipped += 1
                continue
            cur.rows.append(row)
            last_row[c] = row
        i += 1
    for e in events.values():
        seen, keep = set(), []
        for r in sorted(e.rows, key=lambda r: (r.place is None, r.place or 0)):
            key = (r.place, r.last, r.first)
            if key not in seen:
                seen.add(key)
                keep.append(r)
        e.rows = keep
    meet.events = [events[k] for k in sorted(events)]
    # ★ CROSS COUNTRY BY ITS TEAM SCORING: both layouts score teams by
    #   finish order ('3-4-19-29-40', 'Scoring Order'), which a track meet's
    #   report never prints -- the race titles ('Girls Varsity - 5K') do not
    #   say XC, and land.sportOf read them as track
    if _XC_SCORING.search(text):
        for e in meet.events:
            e.sport = "XC"
    return meet
