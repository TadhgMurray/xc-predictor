# Project: xc-predictor
# File:    sources/hytek.py
# Purpose: Hy-Tek Meet Manager results reports, whatever they arrive as --
#          the most common format timing companies publish on their own sites
#          (docs/TIMING-COMPANIES.md, "Formats ranked"). One text parser; three
#          ways to get the text:
#            textFromPositionedHtml  Crystal Reports HTML (YenTiming and
#                                    others): every line an absolutely placed
#                                    <div>, rebuilt by its top/left position
#            textFromPre             a <pre> report page
#            textFromPdf             a PDF, through `pdftotext -layout`
#          parseReport(text) -> Meet(name, date, venue, licensee, events[])
#
# THE REPORT (what parseReport reads):
#     Yen Timing Services - Contractor License   Hy-Tek's MEET MANAGER ... Page 1
#                   Wayne Cross Country Invitational - 9/12/2026
#                                Ginegaw Park
#     Event 6  Boys 5k Run CC Varsity
#     ===========================================================
#         Name                    Year School            Finals  Points
#     ===========================================================
#       1 Lastname, First           11 Clarence           16:40    1
#
# ! COLUMNS FROM THE HEADER, TIME FROM THE RIGHT. The header line names where
#   Year/Grade/Age and School/Team start; the time (and team points) are read
#   from the end of the row, so a school with a space or a digit in its name
#   never shifts the time. A row that does not fit is skipped and counted,
#   never guessed.
# ! ONLY THE INDIVIDUAL RESULTS. "Team Scores" and relay blocks end an event's
#   rows; the engine rates individuals.

import html as _html
import re
import subprocess
from dataclasses import dataclass, field

# ------------------------------------------------------------------ #
# text from each container
# ------------------------------------------------------------------ #

_DIV = re.compile(r'<div[^>]*style="[^"]*?top:\s*(\d+)px;\s*left:\s*(\d+)px;[^"]*"[^>]*>(.*?)</div>',
                  re.S | re.I)


def textFromPositionedHtml(page):
    """Crystal Reports HTML -> the report's lines, in order."""
    rows = {}
    for m in _DIV.finditer(page):
        text = _html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).replace("\xa0", " ")
        if text.strip():
            rows.setdefault(int(m.group(1)), []).append((int(m.group(2)), text.rstrip()))
    return "\n".join(" ".join(t for _, t in sorted(rows[top])) for top in sorted(rows))


def textFromPre(page):
    """The text inside every <pre> block, unescaped."""
    blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", page, re.S | re.I)
    return "\n".join(_html.unescape(re.sub(r"<[^>]+>", "", b)) for b in blocks)


def textFromPdf(data):
    """pdftotext -layout keeps the report's columns. Needs poppler-utils."""
    out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                         capture_output=True, check=True, timeout=60)
    return out.stdout.decode("utf-8", "replace")


def textFromAny(body, content_type=""):
    """Pick the right extraction for a fetched document."""
    if body[:5] == b"%PDF-" or "pdf" in (content_type or "").lower():
        return textFromPdf(body)
    page = body.decode("utf-8", "replace")
    if re.search(r"<pre", page, re.I):
        return textFromPre(page)
    if _DIV.search(page):
        return textFromPositionedHtml(page)
    return re.sub(r"<[^>]+>", "", page)


def nextPageLinks(page):
    """Crystal Reports splits a report into page files: 'X.html', 'X2.html'..."""
    return re.findall(r'href="([^"]+)"[^>]*>\s*\[?\s*Next Page', page, re.I)


# ------------------------------------------------------------------ #
# the report
# ------------------------------------------------------------------ #

@dataclass
class Row:
    place: int            # None for DNF/DQ rows
    last: str
    first: str
    grade: str            # as printed: '11', 'SR', 'FR', '' when the report has none
    school: str
    seconds: float        # None for DNF/DQ/DNS
    mark: str             # the time as printed, or DNF/DQ/DNS
    points: int = None


@dataclass
class Event:
    number: int
    gender: str           # 'M' / 'F' / '' (mixed or unknown)
    title: str            # '5k Run CC Varsity'
    meters: float         # None when the title names no distance
    rows: list = field(default_factory=list)
    skipped: int = 0      # lines inside the event that did not parse


@dataclass
class Meet:
    name: str = ""
    date: str = ""        # ISO, from the title line
    venue: str = ""
    licensee: str = ""
    events: list = field(default_factory=list)


_HEADER = re.compile(r"hy-tek'?s\s+meet\s+manager", re.I)
_EVENT = re.compile(r"^\s*Event\s+(\d+)\s+(Girls|Boys|Women|Men|Mixed|Coed)\b\s*(.*)$", re.I)
_TITLE_DATE = re.compile(r"^(.*?)\s+-\s+(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+to\s+\S+)?\s*$")
_MARK = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{1,2})?|\d{2,3}\.\d{1,2}"
_ROW_TAIL = re.compile(rf"\s+(?P<mark>{_MARK}|DNF|DQ|DNS|NT|SCR)\s*(?:[*#xXqQ]\s*)?(?P<pts>\d+)?\s*$")
_PLACE = re.compile(r"^\s*(\d+|--|-)\s+")
_GRADE_WORD = r"\d{1,2}|FR|SO|JR|SR|R?FR|R?SO|R?JR|R?SR|GR|M?\d"
_GENDER = {"girls": "F", "women": "F", "boys": "M", "men": "M"}


def secondsOf(mark):
    if not mark or not re.match(rf"^({_MARK})$", mark):
        return None
    parts = [float(p) for p in mark.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return round(s, 3)


def metersOf(title):
    """'5k Run CC Varsity' -> 5000; '3200 Meter Run' -> 3200; '2 Mile' -> 3218.7."""
    t = title.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|km)\b", t)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*(meters?|metres?|m)\b", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*mile", t)
    if m:
        return round(float(m.group(1)) * 1609.344, 1)
    m = re.search(r"\b(one|two|three)\s+mile", t)
    if m:
        return round({"one": 1, "two": 2, "three": 3}[m.group(1)] * 1609.344, 1)
    return None


def _columns(header):
    """Start columns of the grade and school fields, from the header line."""
    cols = {}
    for name, keys in (("grade", ("Year", "Yr", "Grade", "Gr", "Age", "Cl")),
                       ("school", ("School", "Team", "Affiliation"))):
        for k in keys:
            m = re.search(rf"(?<!\S){k}(?!\S)", header)
            if m:
                cols[name] = m.start()
                break
    return cols


def _splitName(name):
    name = re.sub(r"\s+", " ", name).strip()
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    parts = name.split(" ")
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def _parseRow(line, cols):
    pm = _PLACE.match(line)
    if not pm:
        return None
    tail = _ROW_TAIL.search(line)
    if not tail:
        return None
    place = int(pm.group(1)) if pm.group(1).isdigit() else None
    body = line[:tail.start()]
    if "grade" in cols and "school" in cols and cols["grade"] < cols["school"] <= len(body):
        name = body[pm.end():cols["grade"]]
        grade = body[cols["grade"]:cols["school"]].strip()
        school = body[cols["school"]:].strip()
        # a long name runs into the grade column ("Maximilian12"): take the
        # grade back from the end of the two together
        if grade and not re.fullmatch(_GRADE_WORD, grade, re.I):
            g = re.match(r"^(.*?[^\d\s])\s*(\d{1,2}|FR|SO|JR|SR)$", (name + grade).rstrip())
            if g:
                name, grade = g.group(1), g.group(2)
    else:
        m = re.match(rf"^(?P<name>.+?)\s+(?P<grade>{_GRADE_WORD})\s+(?P<school>\S.*)$",
                     body[pm.end():], re.I)
        if not m:
            return None
        name, grade, school = m.group("name"), m.group("grade"), m.group("school")
    last, first = _splitName(name)
    if not last or not school.strip():
        return None
    mark = tail.group("mark")
    return Row(place=place, last=last, first=first, grade=grade.upper(),
               school=re.sub(r"\s+", " ", school).strip(), seconds=secondsOf(mark),
               mark=mark, points=int(tail.group("pts")) if tail.group("pts") else None)


def parseReport(text):
    """A Hy-Tek results report's text -> Meet."""
    meet = Meet()
    lines = text.splitlines()
    event, cols, in_rows = None, {}, False
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            continue
        if _HEADER.search(line):
            meet.licensee = meet.licensee or re.split(r"\s{2,}|hy-tek", line, flags=re.I)[0] \
                .replace(" - Contractor License", "").replace("Licensed to", "").strip()
            # the next non-empty lines are the title and the venue
            nxt = [l.strip() for l in lines[i + 1:i + 4] if l.strip()]
            if nxt and not meet.name:
                t = _TITLE_DATE.match(nxt[0])
                if t:
                    meet.name = t.group(1).strip()
                    meet.date = f"{t.group(4)}-{int(t.group(2)):02d}-{int(t.group(3)):02d}"
                else:
                    meet.name = nxt[0]
                if len(nxt) > 1 and not _EVENT.match(nxt[1]) and nxt[1].lower() not in (
                        "results", "rankings", "team rankings"):
                    meet.venue = nxt[1]
            continue
        ev = _EVENT.match(line)
        if ev:
            num, title = int(ev.group(1)), ev.group(3).strip()
            gender = _GENDER.get(ev.group(2).lower(), "")
            # a report page break repeats the event header: keep adding to it
            if not (event and event.number == num):
                event = Event(number=num, gender=gender, title=title, meters=metersOf(title))
                meet.events.append(event)
            cols, in_rows = {}, False
            continue
        if event is None:
            continue
        if re.match(r"^\s*(Team Scores|Team Rankings|Relay|Scoring)", line, re.I):
            in_rows = False
            continue
        if re.match(r"^\s*=+\s*$", line):
            continue
        if re.search(r"(?<!\S)Name(?!\S)", line) and re.search(r"Finals|Time|Results|Mark", line, re.I):
            cols, in_rows = _columns(line), True
            continue
        if not in_rows:
            continue
        row = _parseRow(line, cols)
        if row:
            event.rows.append(row)
        elif _PLACE.match(line):
            event.skipped += 1
    return meet
