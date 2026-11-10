# Project: xc-predictor
# File:    sources/land.py
# Purpose: The landing tables for timing-company results, and the step that
#          writes a parsed meet into them. A timer's meet arrives here first,
#          as the timer printed it, and only reaches the tables the pipeline
#          reads (results, meets ...) through sources/promote.py, after
#          sources/dedup.py has asked whether athletic.net or TFRRS already
#          has it.
#
#   meets_ext    one row per timer meet page: source 'timer:<slug>', the url,
#                name, date, venue, the timer as the report names itself,
#                and what dedup and promotion decided (status, twin_*,
#                live_meet_id)
#   races_ext    one row per race: title, gender, sport, and the TIMER'S OWN
#                DISTANCE (meters_stated); once dedup finds the same race in
#                a feed, that race's div and its distance ride beside it
#                (twin_div_id, twin_meters) -- a second witness for the
#                wrong-distance work
#   results_ext  one row per runner per race: name, grade, school, place,
#                time or status; result_id is the promoted row, NULL until then
#
# ★ WHY A LANDING TABLE AND NOT STRAIGHT INTO results (PLAN-MULTISOURCE.md,
#   "Landing table"). Most timer meets are also on athletic.net or TFRRS,
#   and the feed's copy wins (it carries athlete ids and the re-scrape).
#   Landing first lets dedup look at the whole meet before anything is
#   rated, keeps the timer's distance even for a meet we do not promote,
#   and leaves the timer's copy on record when we drop it.
#
# ! THE PROMOTED ROWS ARE UPLOAD-SHAPED (racecast/uploads.py applyUpload):
#   anet-shaped meets / results rows, negative ids of their own range, NO
#   person_id, source 'timer'. Everything the pipeline does for an approved
#   upload -- link_uploads (04a pass 4), twin_upload (04c), person_gender,
#   backfill_normalize -- it does for these. The landing table keeps the
#   per-timer source the plan names ('timer:<slug>'); the raw tables carry
#   source 'timer' and the slug in id_system, so one SQL test covers every
#   timer.

import datetime
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

SOURCE = "timer"                  # results.source / meets.source once promoted
EXT_PREFIX = "timer:"             # meets_ext.source = 'timer:<slug>'
MEET_BASE = 700_000_000           # timer meet_id = -(MEET_BASE + ext_meet_id);
                                  # uploads use -(900,000,000 + id)
_BIGINT_62 = (1 << 62) - 1
STATUS_MARKS = {"DNF": "DNF", "DQ": "DQ", "DNS": "DNS", "SCR": "SCR", "NT": "NT"}

DDL = """
CREATE TABLE IF NOT EXISTS meets_ext (
    ext_meet_id   BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,          -- 'timer:<slug>'
    url           TEXT NOT NULL UNIQUE,   -- the meet's own page
    sport         TEXT NOT NULL,          -- 'XC' / 'TF' (the meet's main sport)
    meet_name     TEXT,
    meet_date     TEXT,                   -- ISO, first day
    venue         TEXT,
    place         TEXT,                   -- 'Winchester, KS' as printed
    state         TEXT,
    licensee      TEXT,                   -- the timer as the report names itself
    content_sha   TEXT,                   -- of the parsed rows: a re-land that changes nothing is a no-op
    n_rows        INTEGER,
    n_skipped     INTEGER,
    landed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'landed',
                  -- landed | twin | partial | new | promoted | held
    twin_source   TEXT,                   -- 'anet' / 'tfrrs' / 'upload' / 'timer'
    twin_meet_id  BIGINT,
    twin_share    REAL,                   -- landed finishers matched in the feed
    judged_at     TIMESTAMPTZ,
    live_meet_id  BIGINT,                 -- the negative meet id once promoted
    promoted_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS meets_ext_date ON meets_ext (meet_date);
CREATE INDEX IF NOT EXISTS meets_ext_status ON meets_ext (status);
CREATE TABLE IF NOT EXISTS races_ext (
    ext_meet_id   BIGINT NOT NULL REFERENCES meets_ext ON DELETE CASCADE,
    race_n        INTEGER NOT NULL,       -- the report's event number
    sport         TEXT NOT NULL,
    title         TEXT,
    gender        TEXT,                   -- 'M' / 'F' / '' (mixed or unknown)
    division      TEXT,                   -- 'Girls 5k Run CC Varsity'
    meters_stated REAL,                   -- the timer's own distance, from the title
    n_rows        INTEGER,
    twin_source   TEXT,
    twin_meet_id  BIGINT,
    twin_div_id   BIGINT,
    twin_matched  INTEGER,
    twin_meters   REAL,                   -- the feed's distance for the same race
    PRIMARY KEY (ext_meet_id, race_n)
);
CREATE TABLE IF NOT EXISTS results_ext (
    ext_result_id BIGSERIAL PRIMARY KEY,
    ext_meet_id   BIGINT NOT NULL REFERENCES meets_ext ON DELETE CASCADE,
    race_n        INTEGER NOT NULL,
    n             INTEGER NOT NULL,       -- order within the race as printed
    source        TEXT NOT NULL,          -- 'timer:<slug>'
    athlete_name  TEXT,                   -- 'First Last'
    first_name    TEXT,
    last_name     TEXT,
    grade         TEXT,                   -- as printed: '11', 'SR', ''
    school        TEXT,
    place         INTEGER,
    time_seconds  REAL,
    mark          TEXT,                   -- the time as printed, or DNF/DQ/DNS
    status        TEXT,
    result_id     BIGINT,                 -- the promoted results / results_tf row
    UNIQUE (ext_meet_id, race_n, n)
);
CREATE INDEX IF NOT EXISTS results_ext_result ON results_ext (result_id);
-- ★ EVERY MEET PAGE A TIMER'S INDEX OFFERED, AND WHAT BECAME OF IT
--   (2026-11-10). Only landed pages were remembered, so a page that did not
--   parse (an empty template, a relays meet, no date) was "new" every night
--   and took one of the timer's few nightly slots for good; and nothing could
--   say how many meets a timer listed against how many we have
--   (scripts/coverage_report.py reads this as the timers' "expected").
CREATE TABLE IF NOT EXISTS ext_seen (
    url          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,          -- 'timer:<slug>'
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_tried   TIMESTAMPTZ,
    tries        INTEGER NOT NULL DEFAULT 0,
    outcome      TEXT,                   -- 'landed' | 'waits a day' | 'not parsed: ...' | 'no date' | 'outside the US'
    meet_name    TEXT,
    meet_date    TEXT,                   -- ISO, when the page gave one
    sport        TEXT,
    n_races      INTEGER,
    n_rows       INTEGER
);
CREATE INDEX IF NOT EXISTS ext_seen_source ON ext_seen (source);
"""


def initTables(cur):
    cur.execute(DDL)


# ------------------------------------------------------------------ #
#  the landed meet -- pure
# ------------------------------------------------------------------ #
@dataclass
class LandedRow:
    name: str
    first: str
    last: str
    grade: str
    school: str
    place: int
    seconds: float
    mark: str
    status: str = None


@dataclass
class LandedRace:
    n: int
    title: str
    gender: str
    sport: str
    meters: float
    rows: list = field(default_factory=list)

    @property
    def division(self):
        word = {"M": "Boys", "F": "Girls"}.get(self.gender, "")
        t = self.title or ""
        if word and re.search(r"\b(boys|girls|men|women)\b", t, re.I):
            return t
        return " ".join(x for x in (word, t) if x) or None


@dataclass
class LandedMeet:
    slug: str
    url: str
    name: str = ""
    date: str = ""
    venue: str = ""
    place: str = ""
    state: str = ""
    licensee: str = ""
    races: list = field(default_factory=list)
    skipped: int = 0

    @property
    def source(self):
        return EXT_PREFIX + self.slug

    @property
    def sport(self):
        """The meet's main sport: the one most of its rows are."""
        n = {"XC": 0, "TF": 0}
        for r in self.races:
            n[r.sport] += len(r.rows)
        return "XC" if n["XC"] >= n["TF"] and n["XC"] else ("TF" if n["TF"] else "XC")

    def rowCount(self):
        return sum(len(r.rows) for r in self.races)

    def contentSha(self):
        blob = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def toJson(self):
        return asdict(self)

    @classmethod
    def fromJson(cls, d):
        races = [LandedRace(**{**r, "rows": [LandedRow(**x) for x in r["rows"]]})
                 for r in d.get("races", [])]
        return cls(**{**d, "races": races})


_XC_WORDS = re.compile(r"\b(cc|xc|x-c|cross[\s-]*country)\b", re.I)
_RELAY = re.compile(r"\brelay\b|\d\s*x\s*\d|medley|\bdmr\b|\bsmr\b", re.I)
_FIELD = re.compile(r"jump|vault|throw|shot\s*put|discus|javelin|hammer|weight|pentathlon|"
                    r"heptathlon|decathlon", re.I)
_STATE = re.compile(r",\s*([A-Z]{2})\b")
# ★ AGE-GROUP RACES ARE A CLUB'S, NOT A SCHOOL'S (2026-11-09, Big River Race
#   Management's youth invitational: "Boys 8 and Under 2K", "Girls 9-10 Year
#   Old 3K"). No grade, a club for a team: they fit no pool we rate, and a
#   linker would only find namesakes. Left out and counted as skipped.
_AGE_GROUP = re.compile(r"\b\d{1,2}\s*(?:&|and)\s*(?:under|younger)\b|\b\d{1,2}\s*-\s*\d{1,2}\s*(?:year|yr)s?[\s-]*old\b"
                        r"|\b(?:sub[\s-]*)?bantam\b|\bmidget\b|\b\d{1,2}U\b|\bage[\s-]*group\b"
                        # club categories (Athletics Canada, USATF): U18, JD 9, Masters
                        r"|\bU\s?\d{2}\b|\bJD\s?\d{1,2}\b|\bmasters?\b", re.I)
# ★ OUTSIDE THE US (2026-11-09, a MeetPro account's "Trick or Trails",
#   Richmond, BC): every pool we rate is a US one
CANADA = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


# ★ A SPLIT IS NOT A RACE (2026-11-09, Hy-Tek real time at Live Running
#   Results: "#2 Women 1.93 Mile Run CC Split #1" is its own event, the
#   runners' times at the first split). Landed, it would rate a partial
#   distance as a finished race.
#   Nor is a timing system's test event (Mancos' "Zero Test").
_SPLIT = re.compile(r"\bsplits?\b(?!\s*(?:squad|team))|\btest\b", re.I)


def isSplit(title):
    return bool(_SPLIT.search(title or ""))


# an AAU or Junior Olympics meet is age groups throughout (Snake River's
# "AAU Region 21B": "Boys 2000m Steeple (15-16)", "Girls 3000 Meters (11)")
_AGE_MEET = re.compile(r"\bAAU\b|junior\s+olympic|\bJ\.?O\.?\s+(?:champ|qualif)", re.I)
_AGE_PAREN = re.compile(r"\((\d{1,2})(?:\s*-\s*(\d{1,2}))?\)\s*$")


def isAgeGroup(title, meet_name=""):
    """An age-group race: by its title ('8 and Under', 'U18', '(15-16)': an
    age past any grade), or any race of an AAU / Junior Olympics meet."""
    if _AGE_GROUP.search(title or "") or _AGE_MEET.search(meet_name or ""):
        return True
    m = _AGE_PAREN.search(title or "")
    return bool(m and int(m.group(2) or m.group(1)) >= 13)


# ★ A COMMUNITY RACE, NOT A SCHOOL RACE (2026-11-09, Finished Results'
#   "Santiago Sharks 5k": Place Name Age Hometown Time -- runners aged 9 to
#   57, "Corona, CA" where a school would be). The column read as the team is
#   hometowns: most rows "City, ST". Nothing in it fits a pool we rate.
_HOMETOWN = re.compile(r"^[A-Za-z][A-Za-z .'-]*,\s*[A-Z]{2}$")


def isCommunity(rows):
    """True when most of a race's teams are hometowns ('Corona, CA'). Pure."""
    teams = [(r.school or "").strip() for r in rows]
    teams = [t for t in teams if t]
    return len(teams) >= 3 and sum(bool(_HOMETOWN.match(t)) for t in teams) >= 0.6 * len(teams)


# ★ AN ELEMENTARY RACE (2026-11-09, Finish Line Results' "St. Matthew's
#   Elementary Mini Meet": grades K to 5, a 0.8 km race). No pool we rate
#   starts below grade 6; when most graded runners are in K-5 the race is
#   left out. A middle school race with a few fifth graders stays.
_ELEMENTARY = re.compile(r"^(K|[1-5])$", re.I)


def isElementary(rows):
    grades = [(r.grade or "").strip() for r in rows]
    grades = [g for g in grades if g]
    return len(grades) >= 3 and sum(bool(_ELEMENTARY.match(g)) for g in grades) > 0.5 * len(grades)


def sportByTimes(sport, meters, rows):
    """A race that states no distance and was run in cross country times
    (median past ten minutes) is cross country: a track race always names
    its distance (Rapid Results' 'Mens Open' on an XC championship day). Pure."""
    if sport != "TF" or meters:
        return sport
    secs = sorted(r.seconds for r in rows if r.seconds)
    return "XC" if secs and secs[len(secs) // 2] >= 600 else sport


def oneSport(races, track_meet):
    """A meet that is not a track meet and has a cross country race is a
    cross country meet: all its races are XC. (2026-11-09, Jacksonville AC's
    Parker Valby Invitational: the varsity races carried the team score that
    says XC, the "BOYS (MS) • 3 Kilometers" race scored no teams and read TF.)"""
    if not track_meet and any(r.sport == "XC" for r in races):
        for r in races:
            r.sport = "XC"
    return races


def withoutReprints(races):
    """Races, less any that only re-prints others of the same meet: a
    combined 'Boys and Girls 2K' whose every runner (name and time) is in
    the meet's gendered races, but in no single one of them -- the same run
    scored twice. A race printed twice keeps its first copy. Pure."""
    def key(r):
        return {(x.name.lower(), x.seconds) for x in r.rows if x.seconds is not None}
    keys = [key(r) for r in races]
    out = []
    for i, r in enumerate(races):
        mine = keys[i]
        others = [k for j, k in enumerate(keys) if j != i and k]
        if len(mine) >= 2 and others:
            single = any(mine <= k for k in others)
            twin = [j for j, k in enumerate(keys) if j != i and k == mine]
            if (not single and mine <= set().union(*others)) or (twin and twin[0] < i):
                continue
        out.append(r)
    return out


# ! not relays: cross country has relay festivals ('4x2 Mile XC Relay')
_TRACK_ONLY = re.compile(r"hurdle|steeple|dash|\b(?:55|60|100|110|200|300|400)\s*(?:m\b|meters?)", re.I)


def yearOfDate(name, date):
    """A meet name whose own year is not its date's -> the date's year
    (2026-11-10, Y Timing's state meet: "STATE XC 2025" on the 11/5/2026
    report, last year's template). Only a lone trailing or leading year
    one off; "2025-26 Indoor" and the like are left alone. Pure."""
    m = re.fullmatch(r"(.*?\S)\s+(20\d\d)|(20\d\d)\s+(\S.*)", name or "")
    if not m or not date or not re.match(r"20\d\d-(0[2-9]|1[0-2])", str(date)):   # January: a December meet's name
        return name
    yr, want = int(m.group(2) or m.group(3)), int(str(date)[:4])
    if abs(yr - want) != 1:
        return name
    return f"{m.group(1)} {want}" if m.group(2) else f"{want} {m.group(4)}"


def isTrackMeet(titles):
    """A meet holding a sprint, hurdles, a steeplechase or a field event is a
    track meet, whatever its longest race. Pure."""
    return any(_TRACK_ONLY.search(t or "") or _FIELD.search(t or "") for t in titles)


def sportOf(title, meet_name="", meters=None, track_meet=False):
    """'XC' for a cross country race, else 'TF'. The race title first ('5k
    Run CC Varsity'), then the meet's name ('... XC Invitational'); a track
    meet has no 5k-plus race on the infield, so a distance past 5,000 m with
    no track word in the meet's name reads cross country too.
    ! UNLESS THE MEET IS A TRACK MEET (2026-11-09, Trojan Timing's Scots
      Invitational: its "Men's 10000 Meters" read XC beside the steeplechase
      and the 1500): track_meet (isTrackMeet) keeps it TF."""
    if _XC_WORDS.search(title or ""):
        return "XC"
    if track_meet:
        return "TF"
    if _XC_WORDS.search(meet_name or ""):
        return "XC"
    if meters and meters > 5000 and not re.search(r"track|t\s*&\s*f|relays", meet_name or "", re.I):
        return "XC"
    return "TF"


def stateOf(*texts):
    for t in texts:
        m = _STATE.search(t or "")
        if m:
            return m.group(1)
    return ""


def _statusOf(mark, seconds):
    if seconds is not None:
        return None
    return STATUS_MARKS.get((mark or "").upper().strip(), None)


# ★ A CLASS YEAR IS NOT A GRADE (owner, 2026-11-10: "yes, but what about
#   college"). Some reports print the class of 2028 where the grade goes
#   (iResultsLIVE's "Year" column; the site audit's grade "2010"). It is
#   turned into the runner's grade on the meet's own day -- the only moment
#   both the column's meaning and the date are known: high school 12 minus
#   the years left (a middle schooler comes out 6-8), college the class
#   letters TFRRS uses (FR/SO/JR/SR). Out of range -> no grade, never a guess.
_CLASS_YEAR = re.compile(r"^(?:19|20)\d\d$")
_COLLEGE_TEAM = re.compile(r"\b(university|college|univ\.?|institute|academy of)\b|\bcc$|\bu$", re.I)


def isCollegeField(rows):
    """Most of a race's teams are colleges. Pure."""
    teams = [(r.school or "").strip() for r in rows if (r.school or "").strip()]
    return bool(teams) and sum(bool(_COLLEGE_TEAM.search(t)) for t in teams) > 0.5 * len(teams)


def gradeFromClassYear(grade, date, college=False):
    """'2028' at a 2026-10-17 meet -> '11' (high school) / 'SO' (college);
    any other grade comes back as it is. Pure."""
    g = (grade or "").strip()
    if not _CLASS_YEAR.match(g):
        return g
    try:
        import datetime as _dt
        d = _dt.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        return ""
    left = int(g) - (d.year + (1 if d.month >= 8 else 0))      # school years after this one
    if college:
        return {0: "SR", 1: "JR", 2: "SO", 3: "FR"}.get(left, "")
    hs = 12 - left
    return str(hs) if 6 <= hs <= 12 else ""


def _landedRows(rows, date=None):
    college = isCollegeField(rows)
    out = []
    for r in rows:
        name = " ".join(x for x in (r.first, r.last) if x).strip()
        if not name:
            continue
        out.append(LandedRow(name=name, first=r.first or "", last=r.last or "",
                             grade=gradeFromClassYear(r.grade, date, college), school=(r.school or "").strip(),
                             place=r.place, seconds=r.seconds, mark=r.mark or "",
                             status=_statusOf(r.mark, r.seconds)))
    return out


def fromHytek(meet, slug, url):
    """sources.hytek.Meet -> LandedMeet. Events with no rows, relays and
    field events are left out: the engine rates individual running races."""
    lm = LandedMeet(slug=slug, url=url, name=yearOfDate(meet.name, meet.date), date=meet.date, venue=meet.venue,
                    place="", state=stateOf(meet.venue), licensee=meet.licensee)
    track = isTrackMeet([e.title for e in meet.events])
    for e in meet.events:
        lm.skipped += e.skipped
        if not e.rows or _RELAY.search(e.title) or _FIELD.search(e.title):
            continue
        if isAgeGroup(e.title, meet.name) or isCommunity(e.rows) or isElementary(e.rows) or isSplit(e.title):
            lm.skipped += len(e.rows)
            continue
        sport = getattr(e, "sport", None) or sportOf(e.title, meet.name, e.meters, track)
        lm.races.append(LandedRace(n=e.number, title=e.title, gender=e.gender,
                                   sport=sportByTimes(sport, e.meters, e.rows),
                                   meters=e.meters, rows=_landedRows(e.rows, meet.date)))
    kept = withoutReprints(lm.races)
    lm.skipped += sum(len(r.rows) for r in lm.races if r not in kept)
    lm.races = oneSport(kept, track)
    return lm


PRELIM_OFFSET = 10000        # a prelim round's race number: event + 10000


def fromMeetPro(index, results, slug, url):
    """sources.meetpro.MeetIndex + {event number: [Row]} -> LandedMeet.
    A value may instead be [(round title, [Row])] (meetpro.parseRounds): a
    prelim round lands as its own race, n + PRELIM_OFFSET, title '... Prelims'
    (racecast/uploads.py titles an uploaded prelim the same way)."""
    lm = LandedMeet(slug=slug, url=url, name=index.name, date=index.date, venue="",
                    place=index.place, state=stateOf(index.place))
    lm.venue = index.place
    track = isTrackMeet([e.title for e in index.events])
    for e in index.events:
        got = results.get(e.number) or []
        if not got or e.relay or _FIELD.search(e.title) or isAgeGroup(e.title, index.name) or isSplit(e.title):
            continue
        rounds = got if isinstance(got[0], tuple) else [("", got)]
        for rnd, rows in rounds:
            if isCommunity(rows) or isElementary(rows):
                lm.skipped += len(rows)
                continue
            prelim = bool(re.search(r"prelim|semi|heat|quarter", rnd or "", re.I))
            if not rows:
                continue
            lm.races.append(LandedRace(n=e.number + (PRELIM_OFFSET if prelim else 0),
                                       title=e.title + (" Prelims" if prelim else ""), gender=e.gender,
                                       sport=("XC" if getattr(e, "xc", False)
                                              else sportByTimes(sportOf(e.title, index.name, e.meters, track),
                                                                e.meters, rows)),
                                       meters=e.meters, rows=_landedRows(rows, index.date)))
    lm.races = oneSport(lm.races, track)
    return lm


def summary(lm):
    """One line for a dry run."""
    by = {}
    for r in lm.races:
        by.setdefault(r.sport, [0, 0])
        by[r.sport][0] += 1
        by[r.sport][1] += len(r.rows)
    parts = ", ".join(f"{s} {a} races {b} rows" for s, (a, b) in sorted(by.items()))
    return f"{lm.date or '????-??-??'}  {lm.name[:60]:<60}  {parts or 'no races'}  (skipped {lm.skipped})"


# ------------------------------------------------------------------ #
#  ids -- pure
# ------------------------------------------------------------------ #
def meetIdFor(ext_meet_id):
    return -(MEET_BASE + int(ext_meet_id))


def divIdFor(ext_meet_id, race_n):
    """One meets row per race, negative, never an anet or upload div_id."""
    return meetIdFor(ext_meet_id) * 1000 - int(race_n)


def mintResultId(source, ext_meet_id, race_n, n, name, seconds, place):
    """A negative content hash (save_tfrrs._mintResultId's scheme, its own
    prefix): the same row of the same landed race is the same id every time."""
    key = "|".join(str(x) for x in ("timer", source, ext_meet_id, race_n, n,
                                    (name or "").lower(), seconds, place))
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return -(int.from_bytes(digest, "big") & _BIGINT_62) - 1


# ------------------------------------------------------------------ #
#  writing
# ------------------------------------------------------------------ #
def landMeet(cur, lm):
    """Write one LandedMeet. -> (ext_meet_id, what): 'new', 'same' (nothing
    changed), 'replaced' (rows re-read), or 'kept' (already promoted: the
    page changed after promotion, so it is reported and left as it was --
    the promoted rows may carry links)."""
    from psycopg2.extras import execute_values
    sha = lm.contentSha()
    cur.execute("SELECT ext_meet_id, content_sha, status FROM meets_ext WHERE url = %s", (lm.url,))
    got = cur.fetchone()
    if got and got[1] == sha:
        return got[0], "same"
    if got and got[2] == "promoted":
        return got[0], "kept"
    vals = (lm.source, lm.url, lm.sport, lm.name or None, lm.date or None, lm.venue or None,
            lm.place or None, lm.state or None, lm.licensee or None, sha, lm.rowCount(), lm.skipped)
    if got:
        ext = got[0]
        cur.execute("""UPDATE meets_ext SET source=%s, url=%s, sport=%s, meet_name=%s, meet_date=%s,
                              venue=%s, place=%s, state=%s, licensee=%s, content_sha=%s, n_rows=%s,
                              n_skipped=%s, landed_at=now(), status='landed', twin_source=NULL,
                              twin_meet_id=NULL, twin_share=NULL, judged_at=NULL
                       WHERE ext_meet_id = %s""", vals + (ext,))
        cur.execute("DELETE FROM races_ext WHERE ext_meet_id = %s", (ext,))
        cur.execute("DELETE FROM results_ext WHERE ext_meet_id = %s", (ext,))
        what = "replaced"
    else:
        cur.execute("""INSERT INTO meets_ext (source, url, sport, meet_name, meet_date, venue, place,
                                              state, licensee, content_sha, n_rows, n_skipped)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING ext_meet_id""", vals)
        ext = cur.fetchone()[0]
        what = "new"
    races, rows = [], []
    for race in lm.races:
        races.append((ext, race.n, race.sport, race.title, race.gender, race.division,
                      race.meters, len(race.rows)))
        for i, r in enumerate(race.rows, 1):
            rows.append((ext, race.n, i, lm.source, r.name, r.first, r.last, r.grade, r.school,
                         r.place, r.seconds, r.mark, r.status))
    if races:
        execute_values(cur, """INSERT INTO races_ext (ext_meet_id, race_n, sport, title, gender,
                                                      division, meters_stated, n_rows)
                               VALUES %s ON CONFLICT DO NOTHING""", races)
    if rows:
        execute_values(cur, """INSERT INTO results_ext (ext_meet_id, race_n, n, source, athlete_name,
                                   first_name, last_name, grade, school, place, time_seconds, mark,
                                   status)
                               VALUES %s ON CONFLICT DO NOTHING""", rows, page_size=2000)
    return ext, what


def today():
    return datetime.date.today().isoformat()
