# Project: xc-predictor / racecast
# File:    results_parse.py
# Purpose: Read a meet's results file that a coach or athlete uploads (owner,
#          2026-10-10, approved: "upload meet results for meets missing from
#          the site") into rows the staging tables hold. Three formats:
#
#            Hy-Tek MEET MANAGER / TEAM MANAGER printed results (.txt), the
#              layout every timer posts, cross country and track;
#            Hy-Tek's HTML results (.htm/.html) -- the same text inside <pre>;
#            CSV in the template uploads.CSV_TEMPLATE documents.
#
#          Pure: no database, no clock, no network. uploads.py stores what
#          this returns; nothing here decides what reaches the live tables.
#
# ★ A PARSER, NOT AN INTERPRETER (2026-10-10). The file is text read with
#   regular expressions and the standard library's csv and html.parser.
#   Nothing in it is evaluated, imported, executed or fetched: a link in an
#   HTML file is not followed, a script block is dropped unread, and a CSV
#   cell that starts with '=' is a name with an odd first character.
#
# ★ FIXED-WIDTH, READ FROM BOTH ENDS. Meet Manager pads its columns with
#   spaces, but a result copied out of a browser or retyped loses the
#   padding. So a line is read from the RIGHT for the numbers (points, heat,
#   wind, the result, the seed) and from the LEFT for the place, and what is
#   left in the middle is name, year and school -- split on runs of two
#   spaces where the padding survived, on the year token where it did not.
#
# ! EVERY ROW IT CANNOT READ IS A WARNING, NOT A GUESS. A line that starts
#   with a place but yields no time, an event with no distance, a relay, a
#   field event: the uploader and the owner see each one in the preview.
#   Rows are never invented to fill a gap.
#
# ⚠ RELAYS AND FIELD EVENTS ARE SKIPPED, SAID ONCE PER EVENT. The engine
#   rates individual running races; a relay's "name" is a team and a field
#   mark is a distance, and both would land as nonsense times.

import csv
import datetime
import html as _html
import io
import re
from html.parser import HTMLParser

MILE = 1609.344
YARD = 0.9144
MAX_ROWS = 20000                 # one meet; a file past this is not a meet

STATUSES = {"DNF": "DNF", "DNS": "DNS", "DQ": "DQ", "DSQ": "DQ", "SCR": "SCR",
            "SCRATCH": "SCR", "FS": "DQ", "NT": None, "NH": None, "NM": None,
            "FOUL": None, "DNP": "DNS"}

_BANNER = re.compile(r"hy-?tek'?s?\s+(meet|team)\s+manager", re.I)
_DATE_TAIL = re.compile(
    r"\s+-\s+(?P<d1>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})"
    r"(?:\s+to\s+(?P<d2>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}))?\s*$", re.I)
_EVENT = re.compile(r"^\s*\(?\s*Event\s+(?P<num>\d+[A-Za-z]?)\s+(?P<title>.+?)\s*\)?\s*$", re.I)
_SEP = re.compile(r"^\s*[=\-_]{8,}\s*$")
_RESULTS_WORD = re.compile(r"^\s*(Results|Final Results|Official Results)\b.*$", re.I)
_ROUND = re.compile(r"^\s*(Finals|Preliminaries|Prelims|Semi-?Finals|Quarterfinals|"
                    r"(Section|Heat|Flight)\s+\d+.*|Timed Finals|Seeded Section.*)\s*$", re.I)
_TEAM_SCORES = re.compile(r"^\s*(Team Scores|Team Rankings|Scores - )", re.I)
_RECORD = re.compile(r"^\s*[A-Za-z][\w .'/&-]{0,40}:\s*[!*#@$%^&+]?\s*\d", re.I)
_SPLITS = re.compile(r"^[\s\d:.()\[\]]+$")

_TIME_COLON = re.compile(r"^[xX]?(?:\d{1,2}:){1,2}\d{1,2}(?:\.\d{1,3})?[A-Za-z*#@$!&+%^]{0,2}$")
_TIME_DEC2 = re.compile(r"^[xX]?\d{1,3}\.\d{2,3}[A-Za-z*#@$!&+%^]{0,2}$")
_TIME_DEC1 = re.compile(r"^[xX]?\d{1,3}\.\d[hH]?[A-Za-z*#@$!&+%^]{0,1}$")
_EXTRA = re.compile(r"^(?:\d{1,3}|\d{1,2}\.\d|[+-]\d{1,2}\.\d|NWI|NW|[*#@$!+&%^]+|J\d*|q|Q)$")
_PLACE = re.compile(r"^\s{0,8}(?P<place>\d{1,4}|-{2,3}|\*{1,2})\s+(?P<rest>\S.*)$")
_YEAR = re.compile(r"^(?:\d{1,2}|FR|SO|JR|SR|GR|Fr|So|Jr|Sr|Gr|fr|so|jr|sr|"
                   r"(?:FR|SO|JR|SR|Fr|So|Jr|Sr)-?\d|[1-9]th|1[0-2]th|K)$")
_NUMERIC_YEAR = re.compile(r"^(?:\d{1,2}|(?:FR|SO|JR|SR)-?\d|[1-9]th|1[0-2]th)$", re.I)

_RELAY = re.compile(r"\brelay\b|\b\d\s*x\s*\d|\bmedley\b|\bshuttle\b", re.I)
_FIELD = re.compile(r"\b(jump|vault|put|throw|discus|javelin|hammer|weight|pentathlon|"
                    r"heptathlon|decathlon|triathlon)\b", re.I)
_XC_WORDS = re.compile(r"\b(CC|XC|cross\s*country|cross-country)\b", re.I)
_GENDER_WORDS = (("F", re.compile(r"\b(girls?|women'?s?|female|ladies)\b", re.I)),
                 ("M", re.compile(r"\b(boys?|men'?s?|male)\b", re.I)))
_DIST = re.compile(r"(?P<n>\d[\d,]*(?:\.\d+)?)\s*-?\s*(?P<u>meters?|metres?|meter|m\b|k\b|km\b|"
                   r"kilomet\w*|miles?\b|mi\b|yards?|yd\b|y\b)", re.I)
_WORD_N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "half": 0.5}
_WORD_MILE = re.compile(r"\b(one|two|three|four|five|six|half)[\s-]*miles?\b", re.I)
_BARE_MILE = re.compile(r"\bmile\b", re.I)


# ---- small readers ------------------------------------------------------

def parseTime(text):
    """'15:12.40' -> 912.4, '1:02:03.4' -> 3723.4, '58.31q' -> 58.31.
    None for anything that is not a clock reading."""
    s = (text or "").strip()
    if not s:
        return None
    s = s.lstrip("xX")
    s = re.sub(r"[A-Za-z*#@$!&+%^]+$", "", s)
    if not re.fullmatch(r"(?:\d{1,2}:){0,2}\d{1,3}(?:\.\d{1,3})?", s):
        return None
    parts = s.split(":")
    try:
        secs = float(parts[-1])
        mult = 60
        for p in reversed(parts[:-1]):
            secs += int(p) * mult
            mult *= 60
    except ValueError:
        return None
    return round(secs, 3) if secs > 0 else None


def statusOf(text):
    """The non-finish word ('DNF', 'DNS', 'DQ', 'SCR') or None."""
    t = (text or "").strip().upper().rstrip(".")
    return STATUSES.get(t) if t in STATUSES else None


def _isResultToken(tok):
    return bool(_TIME_COLON.match(tok) or _TIME_DEC2.match(tok) or _TIME_DEC1.match(tok)
                or tok.upper().rstrip(".") in STATUSES)


def parseDate(text):
    """'10/4/2025', '2025-10-04', 'October 4, 2025', 'Oct 4 2025' -> ISO, or None."""
    s = (text or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
        else:
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
                try:
                    return datetime.datetime.strptime(s.replace(".", ""), fmt).date().isoformat()
                except ValueError:
                    continue
            return None
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def distanceOf(title):
    """Metres named in an event title: '5000 Meter Run' 5000, '3 Mile' 4828,
    '5K' 5000, '1 Mile Run' / 'Mile' 1609, 'Two Mile' 3219. None if none."""
    t = " ".join((title or "").split())
    m = _WORD_MILE.search(t)
    if m:
        return round(_WORD_N[m.group(1).lower()] * MILE, 1)
    m = _DIST.search(t)
    if m:
        n = float(m.group("n").replace(",", ""))
        u = m.group("u").lower()
        if u.startswith("mi"):
            return round(n * MILE, 1)
        if u.startswith(("k",)):
            return n * 1000.0
        if u.startswith("y"):
            return round(n * YARD, 1)
        return n
    if _BARE_MILE.search(t):
        return MILE
    m = re.search(r"\b(\d{3,5})\b", t)          # 'Boys 3200' -- a bare number is metres
    if m and 100 <= int(m.group(1)) <= 42195:
        return float(m.group(1))
    return None


def genderOf(title):
    for g, rx in _GENDER_WORDS:
        if rx.search(title or ""):
            return g
    return None


def eventKind(title):
    if _RELAY.search(title or ""):
        return "relay"
    if _FIELD.search(title or ""):
        return "field"
    return "running"


def divisionOf(title):
    """What is left of an event title once gender, distance and the event's
    own words are gone: 'Varsity', 'JV', 'Frosh/Soph', 'Division II'."""
    t = title or ""
    for _g, rx in _GENDER_WORDS:
        t = rx.sub(" ", t)
    t = _WORD_MILE.sub(" ", t)
    t = _DIST.sub(" ", t)
    t = re.sub(r"\b(run|dash|race|cc|xc|cross\s*country|meters?|metres?|mile|steeplechase|"
               r"hurdles|event|\d+)\b", " ", t, flags=re.I)
    t = re.sub(r"[()\[\]]", " ", t)
    return " ".join(t.split()).strip(" -,") or None


def isXcTitle(text):
    return bool(_XC_WORDS.search(text or ""))


_NOT_A_NAME = re.compile(r"[=<>(){}\[\];|\\]|https?:|www\.")


def isName(s):
    """A person's name: letters, and no formula, markup or link in it."""
    return bool(re.search(r"[A-Za-z]", s or "")) and not _NOT_A_NAME.search(s or "")


def normName(raw):
    """'Castellano, Owen' -> 'Owen Castellano'; all-caps names title-cased;
    record markers and stray punctuation at the ends dropped."""
    s = " ".join((raw or "").replace("\t", " ").split()).strip(" *#@$!+&%^")
    if "," in s:
        last, _c, first = s.partition(",")
        s = f"{first.strip()} {last.strip()}".strip()
    if s and s.upper() == s and any(c.isalpha() for c in s):
        s = " ".join(w.capitalize() if not re.fullmatch(r"[IVX]+", w) else w for w in s.split())
    return s


def _gradeText(tok):
    t = (tok or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{1,2})(?:th)?", t, re.I)
    if m:
        return m.group(1)
    return t.upper()


# ---- the fixed-width text ------------------------------------------------

class _Cols:
    """What a 'Name  Year School  Seed  Finals  Points' header says."""

    def __init__(self, line):
        self.line = line
        low = line.lower()
        words = low.split()
        self.has_year = any(w in words for w in ("year", "yr", "gr", "grade", "age", "class", "cl"))
        after = []
        for w in words:
            if w in ("seed", "prelims", "prelim", "semis", "finals", "time", "mark",
                     "points", "pts", "h#", "wind", "score", "avg", "mile"):
                after.append(w)
        res = next((i for i, w in enumerate(after) if w in ("finals", "time", "mark")), None)
        if res is None:
            res = next((i for i, w in enumerate(after) if w in ("prelims", "prelim", "semis")), None)
        # a column printed BEFORE the result: the seed (or the prelim time
        # carried into a final)
        self.n_before = (sum(1 for w in after[:res] if w in ("seed", "prelims", "prelim", "semis"))
                         if res is not None else 0)
        self.round = "P" if (res is not None and after[res] in ("prelims", "prelim")) else "F"

    @staticmethod
    def isHeader(line):
        low = line.lower()
        return (re.match(r"^\s*(name|athlete)\b", low) is not None
                and any(w in low for w in ("school", "team", "affiliation", "club")))


def _peelRight(s):
    """(middle, result_token, extras) from the right end of a line, or None."""
    toks = s.split()
    if len(toks) < 2:
        return None
    idx = None
    for i in range(len(toks) - 1, max(-1, len(toks) - 7), -1):
        t = toks[i]
        if _TIME_COLON.match(t) or _TIME_DEC2.match(t) or t.upper().rstrip(".") in STATUSES:
            idx = i
            break
    if idx is None:
        for i in range(len(toks) - 1, max(-1, len(toks) - 4), -1):
            if _TIME_DEC1.match(toks[i]):
                idx = i
                break
    if idx is None or idx == 0:
        return None
    extras = toks[idx + 1:]
    if len(extras) > 4 or not all(_EXTRA.match(e) for e in extras):
        return None
    # cut the middle off the ORIGINAL string so its spacing survives
    pos = len(s)
    for t in reversed(toks[idx:]):
        pos = s.rfind(t, 0, pos)
    return s[:pos].rstrip(), toks[idx], extras


def _splitMiddle(mid, cols):
    """(name_raw, year, school) from what is left between place and times."""
    mid = mid.strip()
    segs = [x for x in re.split(r"\s{2,}", mid) if x.strip()]
    has_year = cols.has_year if cols else True
    if len(segs) >= 2:
        name = segs[0]
        rest = " ".join(segs[1:])
        if has_year:
            first, _sp, tail = rest.partition(" ")
            if _YEAR.match(first) and (tail or len(segs) > 2):
                return name, first, tail.strip()
            if _YEAR.match(first) and not tail:
                return name, first, ""
            # the name column ran into the year: 'Castellano, Owen 11'
            nt = name.split()
            if len(nt) >= 2 and _YEAR.match(nt[-1]):
                return " ".join(nt[:-1]), nt[-1], rest
        return name, None, rest
    # single-spaced: find the year token between a name and a school
    toks = mid.split()
    if has_year:
        cands = [i for i, t in enumerate(toks) if 2 <= i < len(toks) - 1 and _YEAR.match(t)]
        if not cands:
            cands = [i for i, t in enumerate(toks) if 1 <= i < len(toks) - 1 and _YEAR.match(t)]
        num = [i for i in cands if _NUMERIC_YEAR.match(toks[i])]
        pick = (num or cands or [None])[0]
        if pick is not None:
            return " ".join(toks[:pick]), toks[pick], " ".join(toks[pick + 1:])
    # no year: 'Last, First School Name' -- the comma ends the surname and
    # the first name is one word; without a comma we cannot split, say so
    m = re.match(r"^([^,]+,\s*\S+)\s+(.+)$", mid)
    if m:
        return m.group(1), None, m.group(2)
    return mid, None, ""


class _Event:
    def __init__(self, n, num, title, sport_hint):
        self.n = n
        self.num = num
        self.title = " ".join(title.split())
        self.gender = genderOf(self.title)
        self.distance_m = distanceOf(self.title)
        self.kind = eventKind(self.title)
        self.division = divisionOf(self.title)
        self.sport = "XC" if (isXcTitle(self.title) or sport_hint == "XC") else sport_hint
        self.warned = False

    def asDict(self):
        return {"n": self.n, "num": self.num, "title": self.title, "gender": self.gender,
                "distance_m": self.distance_m, "kind": self.kind, "division": self.division,
                "sport": self.sport}


def parseHytekText(text, fmt="hytek_text"):
    """Meet Manager / Team Manager printed results -> the parsed meet (see
    parse()). Works on the text of an HTML file too (parseHytekHtml)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = {"format": fmt, "software": None, "licensee": None,
           "meet": {"name": None, "date": None, "date_end": None, "location": None},
           "sport": None, "events": [], "rows": [], "warnings": []}
    warn = out["warnings"]
    banner_seen = 0
    in_banner = False
    header_lines = []
    ev = None
    cols = None
    skipping = False                  # team scores, relays, field events
    rnd = "F"
    events_by_key = {}
    whole = "\n".join(lines[:80])
    sport_hint = "XC" if isXcTitle(whole) else None

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if not line.strip():
            continue
        b = _BANNER.search(line)
        if b:
            banner_seen += 1
            in_banner = True
            if banner_seen == 1:
                out["software"] = "Team Manager" if b.group(1).lower() == "team" else "Meet Manager"
                lic = re.split(r"\s+-\s+Licensed", line, flags=re.I)[0].strip()
                out["licensee"] = lic if lic and not _BANNER.search(lic) else None
            continue
        if in_banner:
            # the meet title block, repeated at the top of every page
            if _EVENT.match(line) or _Cols.isHeader(line) or _SEP.match(line):
                in_banner = False
            elif _RESULTS_WORD.match(line):
                in_banner = False
                continue
            else:
                if banner_seen == 1:
                    header_lines.append(line.strip())
                continue
        m = _EVENT.match(line)
        if m and not _PLACE.match(line):
            title = m.group("title")
            key = (m.group("num"), " ".join(title.split()).lower())
            if key in events_by_key:            # '(Event 3 ...)' continued on a new page
                ev = events_by_key[key]
            else:
                ev = _Event(len(out["events"]) + 1, m.group("num"), title, sport_hint)
                events_by_key[key] = ev
                out["events"].append(ev)
                if ev.kind != "running":
                    warn.append({"line": i, "text": f"Event {ev.num} {ev.title}: "
                                 f"{'relays' if ev.kind == 'relay' else 'field events'} are not imported"})
            skipping = ev.kind != "running"
            cols = None
            rnd = "F"
            continue
        if _TEAM_SCORES.match(line):
            skipping = True
            continue
        if _SEP.match(line):
            continue
        if _Cols.isHeader(line):
            cols = _Cols(line)
            rnd = cols.round
            if ev is not None and ev.kind == "running":
                skipping = False
            continue
        r = _ROUND.match(line)
        if r:
            w = r.group(1).lower()
            if w.startswith("prelim"):
                rnd = "P"
            elif w.startswith(("final", "timed")):
                rnd = "F"
            continue
        if skipping or ev is None:
            continue
        pm = _PLACE.match(line)
        if _RECORD.match(line) and not pm:
            continue
        if _SPLITS.match(line):
            continue
        if not pm:
            # an unplaced row ('   Smith, Kim  9 Jesuit   DNF') still has a status
            got = _peelRight(line)
            if not got or not statusOf(got[1]):
                continue
            place, rest = None, line.strip()
        else:
            place_tok = pm.group("place")
            place = int(place_tok) if place_tok.isdigit() else None
            rest = pm.group("rest")
        got = _peelRight(rest)
        if not got:
            if pm and re.search(r"[A-Za-z]{2}", rest):
                warn.append({"line": i, "text": f"Could not read a time: {line.strip()[:90]}"})
            continue
        mid, res_tok, _extras = got
        if cols and cols.n_before:
            mt = mid.split()
            if mt and (_isResultToken(mt[-1]) or mt[-1].upper() in ("NT", "NH", "--")):
                mid = mid[:mid.rfind(mt[-1])].rstrip()
        name_raw, year, school = _splitMiddle(mid, cols)
        name = normName(name_raw)
        if not isName(name):
            warn.append({"line": i, "text": f"No name on this line: {line.strip()[:90]}"})
            continue
        st = statusOf(res_tok)
        secs = None if st or res_tok.upper().rstrip(".") in STATUSES else parseTime(res_tok)
        if not st and secs is None:
            if res_tok.upper().rstrip(".") in STATUSES:   # NT / NH: no mark, no status
                continue
            warn.append({"line": i, "text": f"Could not read the time '{res_tok}'"})
            continue
        out["rows"].append({
            "event": ev.n, "place": place, "name": name, "name_raw": name_raw.strip(),
            "grade": _gradeText(year), "school": (school or "").strip() or None,
            "time_text": res_tok, "time_seconds": secs, "status": st, "round": rnd, "line": i})
        if len(out["rows"]) > MAX_ROWS:
            raise ParseError(f"More than {MAX_ROWS:,} results: that is not one meet.")

    _meetFromHeader(out, header_lines)
    _finish(out)
    return out


def _meetFromHeader(out, header_lines):
    meet = out["meet"]
    for h in header_lines[:4]:
        if meet["name"] is None:
            d = _DATE_TAIL.search(h)
            if d:
                meet["name"] = h[:d.start()].strip() or None
                meet["date"] = parseDate(d.group("d1"))
                meet["date_end"] = parseDate(d.group("d2")) if d.group("d2") else None
                continue
            meet["name"] = h
            continue
        if meet["location"] is None and not _RESULTS_WORD.match(h):
            meet["location"] = h
    if meet["date"] is None and meet["name"]:
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", meet["name"])
        if m:
            meet["date"] = parseDate(m.group(1))


def _finish(out):
    """Events as dicts, the sport, and the warnings an uploader must see."""
    evs = out["events"]                 # _Event objects until here
    sports = {e.sport for e in evs if e.kind == "running"}
    for e in evs:
        if e.kind == "running" and e.distance_m is None:
            out["warnings"].append({"line": None, "text": f"Event {e.num} {e.title}: no distance in "
                                    "the title; set the race distance before sending"})
        if e.kind == "running" and e.gender is None:
            out["warnings"].append({"line": None, "text": f"Event {e.num} {e.title}: the title "
                                    "does not say boys or girls"})
    out["events"] = [e.asDict() for e in evs]
    # ! NO XC WORD ANYWHERE IS TRACK: Meet Manager titles a cross country
    #   race "5000 Meter Run CC" (or the meet says XC); a track 3200 says
    #   neither. The uploader can still change it before sending.
    if out["sport"] is None:
        out["sport"] = "XC" if "XC" in sports else ("TF" if evs else None)
    for e in out["events"]:
        e["sport"] = e["sport"] or out["sport"]
    counts = {}
    for r in out["rows"]:
        counts[r["event"]] = counts.get(r["event"], 0) + 1
    for e in out["events"]:
        e["n_rows"] = counts.get(e["n"], 0)
    if not out["rows"]:
        out["warnings"].append({"line": None, "text": "No individual results were found in this file."})


# ---- HTML ------------------------------------------------------------------

class _HtmlText(HTMLParser):
    """The text a browser would show, with <pre> blocks kept verbatim.
    Scripts, styles and comments are dropped unread; nothing is fetched."""

    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "table", "thead", "tbody", "section", "article", "header", "footer", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.pre = []
        self._in_pre = 0
        self._skip = 0
        self.title = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style", "noscript", "template", "iframe", "object", "embed"):
            self._skip += 1
        elif t == "pre":
            self._in_pre += 1
            self.pre.append([])
        elif t == "title":
            self._in_title = True
        elif t in self._BLOCK:
            self._emit("\n")
        elif t in ("td", "th"):
            self._emit("  ")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in ("br", "hr"):
            self._emit("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style", "noscript", "template", "iframe", "object", "embed"):
            self._skip = max(0, self._skip - 1)
        elif t == "pre":
            self._in_pre = max(0, self._in_pre - 1)
        elif t == "title":
            self._in_title = False
        elif t in self._BLOCK:
            self._emit("\n")

    def _emit(self, s):
        if self._skip:
            return
        if self._in_pre and self.pre:
            self.pre[-1].append(s)
        self.parts.append(s)

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
            return
        self._emit(data)


def htmlToText(raw):
    p = _HtmlText()
    p.feed(raw or "")
    p.close()
    pres = ["".join(x) for x in p.pre if "".join(x).strip()]
    body = "\n".join(pres) if pres else "".join(p.parts)
    return body.replace("\xa0", " "), " ".join("".join(p.title).split())


def parseHytekHtml(raw):
    text, title = htmlToText(raw)
    out = parseHytekText(text, fmt="hytek_html")
    if not out["meet"]["name"] and title:
        out["meet"]["name"] = title
    return out


# ---- CSV ---------------------------------------------------------------------

CSV_COLUMNS = ("meet_name", "meet_date", "location", "sport", "event", "gender", "division",
               "distance", "place", "name", "grade", "school", "time")
CSV_REQUIRED = ("name", "time")
_CSV_SYNONYMS = {
    "meet": "meet_name", "meet name": "meet_name", "date": "meet_date", "meet date": "meet_date",
    "venue": "location", "course": "location", "site": "location",
    "race": "event", "event name": "event", "sex": "gender",
    "distance_m": "distance", "distance (m)": "distance", "meters": "distance",
    "pl": "place", "pos": "place", "position": "place",
    "athlete": "name", "athlete name": "name", "runner": "name", "full name": "name",
    "first": "first_name", "first name": "first_name", "first_name": "first_name",
    "last": "last_name", "last name": "last_name", "last_name": "last_name",
    "yr": "grade", "year": "grade", "class": "grade", "gr": "grade",
    "team": "school", "affiliation": "school", "club": "school",
    "mark": "time", "result": "time", "finals": "time", "final time": "time",
}

CSV_TEMPLATE = (
    "meet_name,meet_date,location,sport,event,gender,division,distance,place,name,grade,school,time\n"
    "Lakeside Invitational,2025-10-04,Lakeside Park,XC,Boys 5000m Varsity,M,Varsity,5000,1,"
    "Owen Castellano,11,Jesuit,15:12.40\n"
    "Lakeside Invitational,2025-10-04,Lakeside Park,XC,Boys 5000m Varsity,M,Varsity,5000,2,"
    "Ezra Goldfarb,12,Lincoln,15:20.10\n"
    "Lakeside Invitational,2025-10-04,Lakeside Park,XC,Girls 5000m Varsity,F,Varsity,5000,,"
    "Ann Doe,10,Sunset,DNF\n")


def _gender(text):
    t = (text or "").strip().lower()
    if t in ("m", "male", "boys", "boy", "men", "man"):
        return "M"
    if t in ("f", "w", "female", "girls", "girl", "women", "woman"):
        return "F"
    return genderOf(t)


def parseCsv(text):
    out = {"format": "csv", "software": None, "licensee": None,
           "meet": {"name": None, "date": None, "date_end": None, "location": None},
           "sport": None, "events": [], "rows": [], "warnings": []}
    warn = out["warnings"]
    body = (text or "").lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(body[:4096], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rd = csv.reader(io.StringIO(body), dialect)
    try:
        header = next(rd)
    except StopIteration:
        raise ParseError("The CSV file is empty.")
    keys = []
    for h in header:
        k = " ".join((h or "").strip().lower().replace("_", " ").split())
        k = _CSV_SYNONYMS.get(k, k.replace(" ", "_"))
        keys.append(k)
    has_name = "name" in keys or ("first_name" in keys and "last_name" in keys)
    if not has_name or "time" not in keys:
        raise ParseError("The CSV needs a name column (or first_name and last_name) and a time "
                         "column. Download the template to see the layout.")
    events = {}
    sports = set()
    for i, rec in enumerate(rd, start=2):
        if not any((c or "").strip() for c in rec):
            continue
        row = {k: (rec[j].strip() if j < len(rec) and rec[j] is not None else "")
               for j, k in enumerate(keys)}
        for k in ("meet_name", "location"):
            if row.get(k) and not out["meet"]["name" if k == "meet_name" else k]:
                out["meet"]["name" if k == "meet_name" else k] = row[k][:200]
        if row.get("meet_date") and not out["meet"]["date"]:
            out["meet"]["date"] = parseDate(row["meet_date"])
            if not out["meet"]["date"]:
                warn.append({"line": i, "text": f"Could not read the date '{row['meet_date'][:30]}'"})
        name = row.get("name") or " ".join(x for x in (row.get("first_name"), row.get("last_name")) if x)
        name = normName(name)
        if not isName(name):
            warn.append({"line": i, "text": f"Not a name: {name[:40]}"})
            continue
        title = row.get("event") or ""
        g = _gender(row.get("gender")) or genderOf(title)
        dist = None
        if row.get("distance"):
            dtxt = row["distance"]
            dist = distanceOf(dtxt) if re.search(r"[A-Za-z]", dtxt) else None
            if dist is None:
                try:
                    dist = float(dtxt.replace(",", ""))
                except ValueError:
                    warn.append({"line": i, "text": f"Could not read the distance '{dtxt[:20]}'"})
        if dist is None and title:
            dist = distanceOf(title)
        if not title:
            gw = {"M": "Boys", "F": "Girls"}.get(g, "")
            title = " ".join(x for x in (gw, f"{dist:g}m" if dist else "", row.get("division") or "") if x)
        title = title or "Race"
        kind = eventKind(title)
        sp = (row.get("sport") or "").strip().upper()
        sp = "XC" if sp in ("XC", "CC", "CROSS COUNTRY") else ("TF" if sp in ("TF", "TRACK", "T&F") else None)
        key = (title.lower(), g, dist, (row.get("division") or "").lower())
        ev = events.get(key)
        if ev is None:
            ev = {"n": len(events) + 1, "num": str(len(events) + 1), "title": title, "gender": g,
                  "distance_m": dist, "kind": kind, "division": row.get("division") or divisionOf(title),
                  "sport": sp or ("XC" if isXcTitle(title) else None)}
            events[key] = ev
            if kind != "running":
                warn.append({"line": i, "text": f"{title}: "
                             f"{'relays' if kind == 'relay' else 'field events'} are not imported"})
        if ev["sport"]:
            sports.add(ev["sport"])
        if kind != "running":
            continue
        tt = row.get("time") or ""
        st = statusOf(tt)
        secs = None if st or tt.upper() in STATUSES else parseTime(tt)
        if not st and secs is None:
            warn.append({"line": i, "text": f"Could not read the time '{tt[:20]}'"})
            continue
        place = row.get("place") or ""
        out["rows"].append({
            "event": ev["n"], "place": int(place) if place.isdigit() else None, "name": name,
            "name_raw": name, "grade": _gradeText(row.get("grade")),
            "school": (row.get("school") or "")[:120] or None, "time_text": tt[:20],
            "time_seconds": secs, "status": st, "round": "F", "line": i})
        if len(out["rows"]) > MAX_ROWS:
            raise ParseError(f"More than {MAX_ROWS:,} results: that is not one meet.")
    out["events"] = list(events.values())
    for e in out["events"]:
        if e["kind"] == "running" and e["distance_m"] is None:
            warn.append({"line": None, "text": f"{e['title']}: no distance; set the race distance "
                         "before sending"})
    out["sport"] = "XC" if sports == {"XC"} else ("TF" if sports == {"TF"} else None)
    counts = {}
    for r in out["rows"]:
        counts[r["event"]] = counts.get(r["event"], 0) + 1
    for e in out["events"]:
        e["n_rows"] = counts.get(e["n"], 0)
    if not out["rows"]:
        warn.append({"line": None, "text": "No individual results were found in this file."})
    return out


# ---- the entry point ---------------------------------------------------------

class ParseError(Exception):
    """A message for the uploader: the file is not something we can read."""


EXTENSIONS = (".txt", ".htm", ".html", ".csv")
# the first bytes of things that are not results files, refused unread
_MAGIC = ((b"MZ", "a Windows program"), (b"\x7fELF", "a program"), (b"PK\x03\x04", "a zip archive"),
          (b"%PDF", "a PDF"), (b"\xd0\xcf\x11\xe0", "an old Office file"), (b"#!", "a script"),
          (b"\x89PNG", "an image"), (b"\xff\xd8\xff", "an image"), (b"GIF8", "an image"),
          (b"\x1f\x8b", "a compressed file"), (b"Rar!", "an archive"), (b"7z\xbc\xaf", "an archive"),
          (b"\xca\xfe\xba\xbe", "a program"), (b"\xcf\xfa\xed\xfe", "a program"))


def decode(data):
    """Bytes -> text, refusing anything that is not a text file."""
    for magic, what in _MAGIC:
        if data.startswith(magic):
            raise ParseError(f"That file is {what}, not a results file.")
    if b"\x00" in data[:65536]:
        raise ParseError("That is not a text file. Save the results as .txt, .htm or .csv.")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("Could not read the file's characters.")


def sniffFormat(filename, text):
    name = (filename or "").lower()
    head = text[:4000].lower()
    if name.endswith((".htm", ".html")) or "<html" in head or "<pre" in head or "<!doctype" in head:
        return "hytek_html"
    if name.endswith(".csv"):
        return "csv"
    return "hytek_text"


def plausible(distance_m, secs):
    """None, or why a time cannot be a run over this distance. The floor is
    a little under world-record pace (2:00 per km from 800 m, 9.4 s per
    100 m below it); the ceiling is a walk (12:00 per km)."""
    if not distance_m or not secs:
        return None
    km = distance_m / 1000.0
    lo = km * 120.0 if distance_m >= 800 else distance_m * 0.094
    if secs < lo:
        return "faster than world-record pace for this distance"
    if secs > km * 720.0 + 30:
        return "slower than walking pace for this distance"
    return None


def checkRows(out):
    """Mark every row a human should look at; never drop one here."""
    evs = {e["n"]: e for e in out["events"]}
    seen = {}
    for r in out["rows"]:
        e = evs.get(r["event"]) or {}
        why = plausible(e.get("distance_m"), r.get("time_seconds"))
        key = (r["event"], r.get("round"), (r["name"] or "").lower())
        if why is None and key in seen:
            why = f"the same name twice in this race (line {seen[key]})"
        seen.setdefault(key, r.get("line"))
        if why:
            r["warning"] = why
            out["warnings"].append({"line": r.get("line"), "text": f"{r['name']} {r['time_text']}: {why}"})
    return out


def parse(filename, data):
    """Bytes of an uploaded file -> {format, software, meet{name, date,
    date_end, location}, sport, events[...], rows[...], warnings[...]}.
    Raises ParseError with a message for the uploader."""
    name = (filename or "").strip()
    if not name.lower().endswith(EXTENSIONS):
        raise ParseError("Upload a .txt, .htm, .html or .csv file.")
    text = decode(data)
    fmt = sniffFormat(name, text)
    if fmt == "csv":
        return checkRows(parseCsv(text))
    if fmt == "hytek_html":
        return checkRows(parseHytekHtml(text))
    out = parseHytekText(text)
    if out["software"] is None and not out["events"]:
        raise ParseError("This does not look like Hy-Tek results: no 'Event' headings were found. "
                         "If it is a spreadsheet, save it as CSV in the template's layout.")
    return checkRows(out)


def escapeForDisplay(s):
    return _html.escape(s or "")
