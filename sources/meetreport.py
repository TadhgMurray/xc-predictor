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
_MARK = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{1,3})?"
# after the time: team points (Monroe), and/or a gap behind the winner
# ('---', '11.4', '4:25.1': Hero's Dan Conway report) -- the time is the
# FIRST mark, so a gap that looks like a time is never read as one
_GAP = r"(?:-{1,3}|\+?(?:\d{1,2}:)?\d{1,2}\.\d{0,2}|\+?\.\d{1,2})"   # '10:28.' when the page cut it; '.3'
# ★ THE BIB, PACE AND SPLIT COLUMNS (2026-11-09: Big River Race Management,
#   CFPI -- the same MeetPro report in one column): "Athlete YR # Team Score
#   Time Gap Avg. Mile Avg. kM Split 1 Split 2". Up to eight marks may trail
#   the time; the time is still the FIRST mark (the name part is lazy).
_TRAIL = 8
# ★ AND A PACE (parser review, 2026-11-02): '24:00.3  7:43' -- a pace with no
#   decimal failed the gap, the row matched with the PACE as its time, and
#   24:00.3 went into the school. One or two trailing columns, gap or pace.
_PACE = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{0,3})?"
_ROW = re.compile(rf"^(?P<mid>.+?)\s+(?P<mark>{_MARK}|DNF|DNS|DQ)\b\s*(?P<pts>\(?\d+\)?|-)?"
                  rf"(?:\s+(?:{_GAP}|{_PACE})){{0,{_TRAIL}}}\s*$")
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
    """A title naming both genders is the one it names last (sources.meetpro._gender)."""
    words = re.findall(r"\b(girls?|women'?s?|female|boys?|men'?s?|male)\b", title.lower())
    if not words:
        return ""
    return "F" if words[-1].startswith(("girl", "women", "female")) else "M"


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
        # ! the print stamp, not the line: CFPI prints the meet's date and
        #   "printed: 10/30/2026 9:06 AM" on one line
        line = re.sub(r"printed:.*$", "", line, flags=re.I)
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
        # ! or the place sits UNDER the header word: CFPI and Big River print
        #   "Athlete" flush left, over the place, the name four columns on
        if m.start() >= lo and (col - 3 <= m.end() <= col + 4 or abs(m.start() - col) <= 3):
            return m.start()
    return None


def parseRow(piece, bib=False):
    """'36 MACHO, Isabella  JR Ely  30  24:00.3' -> Row, or None. Pure.
    bib: the header has a '#' column -- a number between the grade (when
    printed) and the team is the runner's bib, not part of the team."""
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
    if bib and len(rest_chunks) >= 2:
        if rest_chunks[0].isdigit() and not (_GRADE.match(rest_chunks[0]) and rest_chunks[1].isdigit()):
            rest_chunks = rest_chunks[1:]                  # no grade printed: '1197  St. Louis'
        elif _GRADE.match(rest_chunks[0]) and rest_chunks[1].isdigit() and len(rest_chunks) >= 3:
            rest_chunks = [rest_chunks[0]] + rest_chunks[2:]   # 'JR  92  TEXAS A&M-CC'
    elif bib and rest_chunks:
        b = re.match(r"^(\d{1,5})\s+(\S.*)$", rest_chunks[0])
        if b and not _GRADE.match(b.group(1)):
            rest_chunks = [b.group(2)]
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
    # 'TEXAS A&M-CC (1': the "(Team Place)" column cut by the page
    team = re.sub(r"\s*\(\d{0,3}\)?$", "", team).strip()
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
    events, cur, cols, last_row, bib = {}, None, [], {}, False
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
            cur, cols, last_row, bib = events[num], [], {}, False
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        hc = _headCols(line)
        if hc:
            bib = bib or bool(re.search(r"\s#\s", line))
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
            row = parseRow(piece, bib)
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


# ★ RUNMEET'S REPORT (2026-11-09, Big River Race Management's results PDFs):
#   no "Race #" lines. Each race is a heading ("Girls 5,000 meters Varsity")
#   followed by "Team Scores" and/or "Individual Results", a header
#   "Pl Pts Name Yr Team Time Gap Av Mile Av Km", and rows
#   "3   2   #1103   Last, First   10 TEAM   23:15.7   17.1 ...". The page
#   header repeats the meet's name and "Venue | Tue, Oct 21, 2025"; a race
#   carried onto the next page repeats its heading.
_RM_HEADER = re.compile(r"^\s*Pl\s+(?:Pts\s+)?(?:Bib\s+)?Name\s+.*\bTime\b", re.I)
_RM_ROW = re.compile(rf"^\s*(?P<pl>\d{{1,4}})\s+(?:(?P<pts>\(?\d{{1,3}}\)?|--?)\s+)?(?:#?\d{{1,6}}\s+)?"
                     rf"(?P<name>[A-Za-z'][^\d#]*?)\s{{2,}}(?:(?P<yr>\d{{1,2}}|FR|SO|JR|SR|GR)\s+)?"
                     rf"(?P<team>\S.*?)\s{{2,}}(?P<mark>{_MARK}|DNF|DNS|DQ)\b")
_RM_SECTION = re.compile(r"^\s*(Team Scores|Individual Results)\s*$", re.I)
_MON = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DATE_ABBR = re.compile(r"\b(" + "|".join(_MON) + r")[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I)


def isRunMeet(text):
    return bool(re.search(r"^\s*Individual Results\s*$", text, re.M | re.I)
                and any(_RM_HEADER.match(ln) for ln in text.splitlines()))


def parseRunMeet(text, url=""):
    """A RunMeet results report -> sources.hytek.Meet. Pure."""
    lines = text.splitlines()
    meet = Meet()
    for ln in lines[:12]:
        t = ln.strip()
        if not t or re.match(r"^(mon|tue|wed|thu|fri|sat|sun)\w*,", t, re.I) and "|" not in t:
            continue                            # the print time
        if not meet.name:
            meet.name = re.sub(r"\s{2,}.*$", "", t)
            continue
        d = _DATE_ABBR.search(t)
        if d and not meet.date:
            meet.date = datetime.date(int(d.group(3)), _MON.index(d.group(1).lower()[:3]) + 1,
                                      int(d.group(2))).isoformat()
            meet.venue = t.split("|")[0].strip() if "|" in t else ""
            break
    events, cur, reading = {}, None, False
    prev = ""
    for ln in lines:
        t = ln.strip()
        if not t:
            continue
        if _RM_SECTION.match(t):
            # the heading is the line before, unless that is a table's row
            if prev and not re.match(r"^\(?\d", prev) and not _RM_SECTION.match(prev) \
                    and prev.lower() not in ("results",) and prev != meet.name:
                title = re.sub(r"\s{2,}.*$", "", prev)
                if title not in events:
                    events[title] = Event(number=len(events) + 1, gender=_gender(title), title=title,
                                          meters=_meters(title.replace(",", "")))
                cur = events[title]
            reading = t.lower().startswith("individual")
            prev = t
            continue
        if _RM_HEADER.match(ln) or t == "Results":
            prev = t
            continue
        if cur is not None and reading:
            m = _RM_ROW.match(ln)
            if m:
                last, first = _splitName(m.group("name").strip())
                mark = m.group("mark")
                secs = secondsOf(mark) if re.match(_MARK, mark) else None
                cur.rows.append(Row(place=int(m.group("pl")) if secs else None, last=last, first=first,
                                    grade=(m.group("yr") or "").upper(), school=m.group("team").strip(),
                                    seconds=secs, mark=mark))
            elif re.search(_MARK, ln) and re.match(r"^\s*\d", ln):
                cur.skipped += 1
        prev = t
    meet.events = list(events.values())
    if _XC_SCORING.search(text):
        for e in meet.events:
            e.sport = "XC"
    return meet


# ★ A TAB-SEPARATED EXPORT (2026-11-09, YenTiming's Section V championships:
#   ".../11-07-26-SectionV/results-Section V Cross Country Championships.html"
#   is one <pre> of "Boys Class C - 5K Run", then "Place<TAB>Athlete<TAB>Grade
#   <TAB>School<TAB>Time" and a row a line). The columns come from the header;
#   the meet's name from the file's ("results-<name>"), its day from the
#   folder's ("11-07-26").
_TAB_COLS = {"place": "place", "pl": "place", "athlete": "name", "name": "name", "grade": "grade",
             "yr": "grade", "year": "grade", "school": "team", "team": "team", "time": "time",
             "finals": "time", "mark": "time"}


def _tabHeader(line):
    cols = [c.strip().lower() for c in line.split("\t")]
    if len(cols) < 3 or "\t" not in line:
        return None
    got = [_TAB_COLS.get(c) for c in cols]
    return got if "name" in got and "time" in got else None


def isTabbed(text):
    return any(_tabHeader(ln) for ln in text.splitlines()[:400])


def parseTabbed(text, url=""):
    """A tab-separated results export -> sources.hytek.Meet. Pure."""
    import urllib.parse
    lines = text.splitlines()
    leaf = urllib.parse.unquote((url or "").rstrip("/").rsplit("/", 1)[-1])
    leaf = re.sub(r"\.\w{2,4}$", "", leaf)
    meet = Meet(name=re.sub(r"^results?[-_ ]+", "", leaf, flags=re.I).replace("_", " ").strip(),
                date=_date([], url))
    events, cur, cols, prev = [], None, None, ""
    for ln in lines:
        t = ln.strip()
        if not t:
            continue
        head = _tabHeader(ln)
        if head:
            cols = head
            if prev and "\t" not in prev:
                cur = Event(number=len(events) + 1, gender=_gender(prev), title=prev,
                            meters=_meters(prev))
                events.append(cur)
            prev = t
            continue
        if cur is not None and cols and "\t" in ln:
            cells = dict(zip(cols, [c.strip() for c in ln.split("\t")]))
            mark = cells.get("time", "")
            if not cells.get("name"):
                continue
            if not re.fullmatch(rf"{_MARK}|DNF|DNS|DQ", mark):
                cur.skipped += 1
                continue
            last, first = _splitName(cells["name"])
            secs = secondsOf(mark) if re.match(_MARK, mark) else None
            place = cells.get("place", "")
            cur.rows.append(Row(place=int(place) if place.isdigit() and secs else None, last=last,
                                first=first, grade=cells.get("grade", "").upper(),
                                school=cells.get("team", ""), seconds=secs, mark=mark))
        prev = t
    meet.events = events
    return meet
