"""
tf_points.py -- computed team scores for a TF meet.

PURE: takes rows, returns structure. app.py owns the one query that feeds
it (get_tf_meet_scoring_rows), so this whole module tests without a
database -- the compare/teams convention.

★ NOT THE MEET'S OFFICIAL SCORE, BY DESIGN. No meet publishes its scoring
  rules into this data, so one honest computed score beats a thousand
  guessed official ones: the standard 8-place table (10-8-6-5-4-3-2-1)
  for every event, relays once per school, field events by mark, multis
  by points, ties split. The page says so.

★ ROUNDS. The raw event names carry the truth the distance parser strips:
  "100 Meters Prelims", "Boys 3200 Finals", "(Section 2)". Where a
  canonical event has rows marked FINAL, only those score; otherwise the
  whole event ranks as one field by each athlete's best mark -- which is
  also exactly right for sectioned timed finals.

★ EVENT IDENTITY. Prelims and finals arrive as different event_ids with
  different names; scoring needs them to be ONE event. The merge key is
  the name with the gender prefix and the round/section noise stripped --
  but NOT the level words (Varsity, JV, Frosh): those scope who was
  racing whom, and merging a varsity 100 with the JV 100 would score a
  meet that never happened. The division column joins the key for the
  same reason. An event with NO name keys on its ids instead: an empty
  canonical name once merged six different unnamed events (470+ results)
  into one fake mega-event at a real meet.

★ UNATTACHED ATHLETES RANK BUT NEVER SCORE -- the real-meet rule. Some
  feeds write "Unattached" as a literal school name, and at open invites
  it promptly won both team titles. Non-team rows keep their place in
  the event table (they really did win); the points skip over them to
  the first actual team, and the event win goes with the points.

★ GENDER, THREE SOURCES DEEP. Some feeds name events "800m" with no
  Boys/Girls prefix. Order: the name's prefix, then the athlete's own
  gender, then the event's majority gender (so an athlete we cannot sex
  does not fork into a phantom copy of the event). An event with no
  gender anywhere -- relays at prefix-less meets, mostly -- still
  displays, but awards no points: points that cannot land in a Boys or
  Girls standings are points invented for nobody.
"""

import math
import re

TABLE = (10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0)

_FINAL = re.compile(r"\bfinals?\b", re.IGNORECASE)
_PRELIM = re.compile(
    r"\b(prelims?|preliminar\w*|semis?|semifinals?|quarterfinals?|"
    r"trials?|heats?)\b", re.IGNORECASE)

# Mirrors event_parse's gender prefix, locally: this module must not pull
# the engine tree into the web app.
_GENDER = re.compile(
    r"^\s*(men|mens|men's|boys|boy's|male|"
    r"women|womens|women's|girls|girl's|female)\b[\s'’]*",
    re.IGNORECASE)
_MALE = frozenset({"men", "mens", "men's", "boys", "boy's", "male"})

# Gender word ANYWHERE in the name, for classification (not stripping):
# "Varsity Boys 100" leads with a level word, not the gender. \b keeps
# "women" from matching its inner "men".
_GENDER_ANY = re.compile(
    r"\b(boys?|men|mens|male|girls?|women|womens|female)\b", re.IGNORECASE)
_MALE_WORDS = frozenset({"boy", "boys", "men", "mens", "male"})

# Round/section noise for the MERGE KEY. Deliberately narrower than
# event_parse._TRAILING_NOISE: no varsity/jv/frosh (level words scope the
# competition) and no invite/championship (part of some events' names).
_NOISE = re.compile(
    r"\s*[\(\[].*?[\)\]]|"
    r"\s*\b(section|sect|heat|flight|round|prelims?|preliminar\w*|"
    r"semis?|semifinals?|quarterfinals?|finals?|trials?|"
    r"unseeded|seeded|fast|slow|\#)\b.*$",
    re.IGNORECASE)

# School strings that mean "no team". Matched on the whole normalized
# name -- "Independence HS" is a school, "Independent" is a shrug.
_NON_TEAMS = frozenset({
    "unattached", "unaffiliated", "independent", "n/a", "na", "none",
    "no team", "no school", "no affiliation", "-", "--"})

# Bare feed codes -> reader names. Display only: the merge key keeps the
# raw string, so two different codes never collapse into one event.
_CODE_NAMES = {
    "hj": "High Jump", "lj": "Long Jump", "pv": "Pole Vault",
    "tj": "Triple Jump", "shot": "Shot Put", "sp": "Shot Put",
    "disc": "Discus", "discus": "Discus", "jav": "Javelin",
    "wt": "Weight Throw", "weight": "Weight Throw",
    "ham": "Hammer", "hammer": "Hammer",
    "pent": "Pentathlon", "hept": "Heptathlon", "dec": "Decathlon",
    "1mile": "1 Mile", "2mile": "2 Mile", "3mile": "3 Mile",
}
# Medley codes carry a leg suffix: a plain number reads as a distance
# ("sprintmed2248" -> "Sprint Medley 2248"), a leg list does not
# ("distmed12,4,8,16" is a DMR, and nobody calls it that).
_MEDLEY = re.compile(r"^(sprint|dist(?:ance)?)\s*med(?:ley)?\s*([\d,.x]+)?$",
                     re.IGNORECASE)

# Leading distance in an event name ("200m", "1600 Meters", "110h").
# Two digits minimum so "4x200m" stays a relay, not a 4; a lookahead,
# not \b, because "200m" has no word boundary before the m.
_LEAD_NUM = re.compile(r"^(\d{2,5})(?=\D|$)")

# Bare hurdle/steeple codes, display only ("110h" -> "110m Hurdles",
# "3000sc" -> "3000m Steeplechase"). Digit-first keeps "hj" a high
# jump; the optional letter admits the hh/lh/ih spellings.
_HURDLE_CODE = re.compile(r"^(\d{2,4})\s*m?\s*[hli]?h$", re.IGNORECASE)
_STEEPLE_CODE = re.compile(r"^(\d{3,4})\s*m?\s*sc$", re.IGNORECASE)

# Shuttle hurdle relays, code or word form ("100shuttleh", "4x110 Shuttle
# Hurdles"). The hurdle SPEC carries the gender: the 100m shuttle is a
# girls' event and the 110m a boys' one by definition of the hurdles
# themselves -- the one place gender is knowable from the event alone.
_SHUTTLE = re.compile(
    r"^(?:\d\s*x\s*)?(\d{2,3})\s*m?\s*(?:shuttle\s*h\w*|sh)\b",
    re.IGNORECASE)

# An "EnRoute" division holds split reads taken INSIDE other races (the
# 1600 en route to a mile). They are not races: scoring them invents
# points and double-counts the athletes' real events.
_ENROUTE = re.compile(r"\ben\s*-?\s*route\b|enroute", re.IGNORECASE)


def scorableSchool(school):
    """The school name if it names an actual team, else None.

    unattached/unaffiliated match ANYWHERE, meet_compile's rule (kept
    in step in spirit, per its header): no school is named with them,
    and the corpus writes both "Unattached - Nike" and "Nike
    Unattached". The other placeholders stay whole-string --
    "Independence HS" is a school, "Independent" is a shrug."""
    s = (school or "").strip()
    low = s.lower()
    if (not s or low in _NON_TEAMS or "unattached" in low
            or "unaffiliated" in low):
        return None
    return s


def prettyEventName(event_short):
    """A bare feed code ('hj', 'sprintmed2248') becomes its reader name;
    anything already name-shaped comes back untouched."""
    s = " ".join((event_short or "").split())
    low = s.lower()
    if low in _CODE_NAMES:
        return _CODE_NAMES[low]
    m = _SHUTTLE.match(low)
    if m and low.endswith(("h", "hurdle", "hurdles")):
        return f"{m.group(1)}m Shuttle Hurdles"
    m = _HURDLE_CODE.match(low)
    if m:
        return f"{m.group(1)}m Hurdles"
    m = _STEEPLE_CODE.match(low)
    if m:
        return f"{m.group(1)}m Steeplechase"
    m = _MEDLEY.match(low)
    if m:
        kind = "Sprint Medley" if m.group(1).lower() == "sprint" \
            else "Distance Medley"
        suffix = m.group(2)
        return f"{kind} {suffix}" if suffix and suffix.isdigit() else kind
    # ! A LOWERCASE NAME IS STILL A NAME (owner, 2026-09-02: "javelin"
    #   printed in lowercase). Title-case a wordy event with no digits in
    #   it; a distance ("1600m") keeps its unit letter lower.
    if s and s.islower() and not any(ch.isdigit() for ch in s):
        return s.title()
    return s or event_short



def eventDistance(event_short, stored=None):
    """Sort distance in metres: the stored column, else the number the
    name leads with. None when neither answers (fields, relays, codes)."""
    if stored:
        return float(stored)
    m = _LEAD_NUM.match(canonicalEvent(event_short))
    return float(m.group(1)) if m else None


_RELAY_NXM = re.compile(r"(\d)\s*x\s*(\d{2,5})", re.IGNORECASE)


def relayLegsSpec(event_short):
    """(n_legs, total_metres) for a relay name, or None.

    '4x200m' -> (4, 800). Medley codes carry legs in hundreds:
    'distmed12,4,8,16' -> 1200+400+800+1600 -> (4, 4000);
    'sprintmed2248' -> 200+200+400+800 -> (4, 1600). Bare medley words
    take their standard totals."""
    s = canonicalEvent(event_short)
    m = _RELAY_NXM.search(s)
    if m:
        return int(m.group(1)), int(m.group(1)) * int(m.group(2))
    m = _MEDLEY.match(s)
    if m:
        suffix = m.group(2) or ""
        if "," in suffix:
            try:
                legs = [int(x) * 100 for x in suffix.split(",") if x]
                if legs:
                    return len(legs), sum(legs)
            except ValueError:
                pass
        elif suffix.isdigit() and 2 <= len(suffix) <= 4:
            legs = [int(c) * 100 for c in suffix]
            return len(legs), sum(legs)
        return (4, 1600) if m.group(1).lower() == "sprint" else (4, 4000)
    return None


def _median(values):
    v = sorted(values)
    return v[len(v) // 2] if v else None


def _riegelK(leg_metres):
    """Pace-decay exponent by leg distance: sprints slow down harder
    per doubling than distance legs do."""
    if leg_metres < 300:
        return 1.16
    if leg_metres < 800:
        return 1.10
    return 1.06


def roundOf(event_short):
    """'final' | 'prelim' | None, read from the RAW name."""
    s = event_short or ""
    if _FINAL.search(s):
        return "final"
    if _PRELIM.search(s):
        return "prelim"
    return None


# The feed's own per-result round codes. 'F' final; everything else that
# appears ('P' prelim, and defensively S/Q/H for semis/quarters/heats).
_ROUND_CODES = {"F": "final", "P": "prelim", "S": "prelim",
                "Q": "prelim", "H": "prelim"}


def rowRound(row):
    """'final' | 'prelim' | None for one RESULT: the round column the
    feed stamped on it when present (the truth -- prelims and finals can
    share one event_id), else the event name's word."""
    code = (row.get("round") or "").strip().upper()[:1]
    if code in _ROUND_CODES:
        return _ROUND_CODES[code]
    return roundOf(row.get("event_short"))


def genderOf(event_short):
    """'M' | 'F' | None from the gender word in the event name, wherever
    it sits ("Boys 100", "Varsity Boys 100")."""
    m = _GENDER_ANY.search(event_short or "")
    if not m:
        return None
    return "M" if m.group(1).lower() in _MALE_WORDS else "F"


def shuttleGender(event_short):
    """'F' | 'M' | None from a shuttle hurdle relay's hurdle spec -- the
    one event whose gender is knowable from its name without a gender
    word in it. 55/60 shuttles stay None: both genders run those."""
    m = _SHUTTLE.match(canonicalEvent(event_short))
    if not m:
        return None
    return {"100": "F", "110": "M"}.get(m.group(1))


def canonicalEvent(event_short):
    """The merge key: gender prefix off, round noise off, case folded."""
    s = _GENDER.sub("", event_short or "")
    s = _NOISE.sub("", s)
    return " ".join(s.split()).lower()


def displayEvent(event_short):
    """The reader's name for an event: same strip, original case kept,
    bare feed codes translated ('hj' -> 'High Jump')."""
    s = _GENDER.sub("", event_short or "")
    s = _NOISE.sub("", s)
    s = " ".join(s.split()) or (event_short or "Event")
    return prettyEventName(s)


# Imperial field marks: "50-0.25", "22' 10.5\"", "5-10". Feet 1-3
# digits, inches under 12 (with fraction).
_IMPERIAL = re.compile(
    r"^(\d{1,3})\s*[-'’\s]\s*(\d{1,2}(?:\.\d+)?)\s*\"?$")


def parseMark(mark):
    """A field mark or multi score as a float, or None.

    The feed ships marks as STRINGS in whatever unit it had: "15.24m",
    "50-0.25" (feet-inches), "4,321" multi points. A bare float() call
    silently zeroed every field event at real meets -- six events per
    gender scoring nothing, 234 of each 780 points just gone."""
    if mark is None:
        return None
    if isinstance(mark, (int, float)):
        return float(mark)
    s = str(mark).strip().lower()
    s = s.replace("pts", "").replace("points", "").replace(",", "").strip()
    if s.endswith("m"):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        pass
    m = _IMPERIAL.match(s)
    if m and float(m.group(2)) < 12:
        return int(m.group(1)) * 0.3048 + float(m.group(2)) * 0.0254
    return None


def _scoreVal(row):
    """The feed's own published points for a result, or None. Zero is
    'did not score', which for coverage purposes is the same as absent."""
    try:
        v = float(row.get("score"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _value(row):
    """The rankable number, or None. Running/relay: seconds ascending.
    Field and multis: mark descending -- a mark that will not parse as a
    number cannot rank and the row shows without scoring."""
    if row.get("is_field") or (row.get("result_kind") in ("field", "combined")):
        v = parseMark(row.get("mark"))
        if v is None:
            return None
        return -v                     # negate: one ascending sort ranks both
    t = row.get("time_seconds")
    return float(t) if t and float(t) > 0 else None


def fmtPoints(p):
    """10 -> '10', 5.5 -> '5.5'. Two decimals at most: a genuine 7-way
    split does not deserve '92.7857' in a standings table."""
    return f"{round(p + 1e-9, 2):g}"


def _entryKey(r, is_relay):
    """Who a row belongs to: the school for a relay, the person (or the
    name+school stand-in) otherwise. None for an unattributable relay."""
    if is_relay:
        s = (r.get("school") or "").strip().lower()
        return ("school", s) if s else None
    return ("person", r.get("person_id") or
            ((r.get("athlete_name") or "").strip().lower(),
             (r.get("school") or "").strip().lower()))


def _entries(rows, is_relay):
    """Scoring entries: best row per athlete (or per school for a relay).
    Returns [(value, key, best_row)] for rankable rows only.

    ★ FIELD TIES BREAK ON THE FEED'S PLACE COLUMN. Two vertical jumpers
      on the same height are usually NOT tied -- the meet ordered them
      by countback misses, which our data carries only as `place`. So a
      field entry's value is (mark, place): equal marks with different
      places rank in place order, and only marks the meet itself left
      tied (same place, or no place at all) split points. Running ties
      on time stay real ties -- place there is section-local and must
      not order a dead heat."""
    best = {}
    for r in rows:
        v = _value(r)
        if v is None:
            continue
        if r.get("is_field") or r.get("result_kind") in ("field", "combined"):
            place = r.get("place")
            sv = ((v, 0, int(place)) if place
                  else (v, 1, 0))
        else:
            sv = (v, 0, 0)
        key = _entryKey(r, is_relay)
        if key is None:
            continue                  # a relay with no school cannot rank
        if key not in best or sv < best[key][0]:
            best[key] = (sv, r)
    return sorted(((v, k, r) for k, (v, r) in best.items()),
                  key=lambda e: e[0])


def _award(entries):
    """[(place_label, points, is_win, row)] with standard split-tie
    handling: tied entries share the mean of the points their places
    cover and wear a T-prefixed place.

    Places count EVERY ranked entry; points count only rows with a real
    team -- the unattached winner shows Place 1, and the 10 points (and
    the event win) go to the first school behind them, exactly as meets
    score open fields."""
    out = []
    i = 0
    slot = 0                          # next unclaimed scoring place
    while i < len(entries):
        j = i
        while j + 1 < len(entries) and entries[j + 1][0] == entries[i][0]:
            j += 1
        group = [entries[p][2] for p in range(i, j + 1)]
        label = f"T{i + 1}" if j > i else str(i + 1)
        n_team = sum(1 for r in group
                     if scorableSchool(r.get("school")) is not None)
        share = 0.0
        if n_team:
            pts = [TABLE[slot + k] if slot + k < len(TABLE) else 0.0
                   for k in range(n_team)]
            share = sum(pts) / n_team
        win = n_team > 0 and slot == 0
        for r in group:
            team = scorableSchool(r.get("school")) is not None
            out.append((label, share if team else 0.0,
                        win and team, r))
        slot += n_team
        i = j + 1
    return out


def _splitMap(rows):
    """{school_lower: (real_states, primary_state)} for school strings
    that arrive at THIS meet with athletes from two or more states --
    two real schools wearing one name (plain 'Highland', UT and CA,
    both at a national invite).

    The gate is two-plus-two: a state counts only with >= 2 distinct
    athletes here, and a split needs >= 2 such states -- one traveling
    transfer must not mint a phantom school. Rows carry home_state
    stamped by the route from person_home_state (the athlete's modal
    racing state); meets without the stamp split nothing."""
    by_school = {}
    for r in rows:
        school = scorableSchool(r.get("school"))
        st = r.get("home_state")
        if not school or not st:
            continue
        d = by_school.setdefault(school.lower(), {})
        d.setdefault(st, set()).add(r.get("person_id") or id(r))
    split = {}
    for key, by_st in by_school.items():
        real = {st for st, ppl in by_st.items() if len(ppl) >= 2}
        if len(real) >= 2:
            split[key] = (real, max(by_st, key=lambda s: len(by_st[s])))
    return split


def _teamIdentity(r, split):
    """(display_school, link_state) for a result's team row. Split
    names qualify as 'Name (ST)'; an athlete whose state is unknown or
    below the gate stands with the school's biggest cluster here."""
    school = scorableSchool(r.get("school"))
    if not school:
        return None, None
    info = split.get(school.lower())
    if not info:
        return school, None
    real, primary = info
    st = r.get("home_state")
    st = st if st in real else primary
    return f"{school} ({st})", st


def _standings(teams_dict):
    """Sorted standings rows from {school_lower: {school, points, wins}},
    tie-aware T-places, formatted points -- one rule for the computed
    table and the official one."""
    teams = sorted(teams_dict.values(),
                   key=lambda t: (-t["points"], t["school"]))
    i = 0
    while i < len(teams):
        j = i
        while (j + 1 < len(teams) and
               teams[j + 1]["points"] == teams[i]["points"]):
            j += 1
        label = f"T{i + 1}" if j > i else str(i + 1)
        for p in range(i, j + 1):
            teams[p]["place_label"] = label
        i = j + 1
    for t in teams:
        t["display"] = fmtPoints(t["points"])
    return teams


def scoreMeet(rows):
    """All of one meet's scoring in one pass.

    rows: dicts carrying result_id, event_id, event_short, division,
    distance_meters, school, athlete_name, person_id, gender,
    time_seconds, mark, result_kind, is_field, is_relay, speed_rating,
    grade, and (when the feed shipped one) the official per-result
    score. Output:

      {"divisions": [{"name", "teams": {"M": [...], "F": [...]},
                      "official": {"M": [...] | None, "F": ... },
                      "events": [{"name", "gender", "is_relay",
                                  "is_field", "distance", "rows"}]}],
       "points_by_result": {result_id: "10"},
       "n_scored_events": int}

    "official" holds standings summed from the feed's own published
    per-result points -- the meet's actual rules, multis included where
    the feed carries their results. It is None unless coverage is 100%:
    see the gate in the finalize loop.
    """
    # same name, two schools: the home-state split (see _splitMap)
    split = _splitMap(rows)

    # ---- majority gender per raw event -------------------------------- #
    # For rows whose name and athlete both stay silent: strict majority
    # of the KNOWN genders in the same raw event (div_id, event_id), so
    # one unsexed athlete doesn't fork a phantom copy of the 800.
    tallies = {}
    for r in rows:
        if r.get("gender") in ("M", "F"):
            k = (r.get("div_id"), r.get("event_id"))
            t = tallies.setdefault(k, {"M": 0, "F": 0})
            t[r["gender"]] += 1
    majority = {}
    for k, t in tallies.items():
        if t["M"] != t["F"]:
            majority[k] = "M" if t["M"] > t["F"] else "F"

    # ---- relay genders by pairing -------------------------------------- #
    # Relays have no athletes to take a majority from, so at prefix-less
    # meets they arrive genderless in PAIRS: the same canonical event
    # twice, one per gender. When one canonical relay has exactly two
    # genderless raw events, the faster-by-median one is the men's -- the
    # M/F relay gap runs ~15%+, while two heats of ONE gender sit within
    # a few percent, so the 10% gate leaves genuine sections merged and
    # unscored rather than guessing. A singleton genderless relay stays
    # unscored: there is nothing to compare it against.
    relay_pairs = {}
    for r in rows:
        canon = canonicalEvent(r.get("event_short"))
        if not canon or not r.get("is_relay"):
            continue
        ekey = (r.get("div_id"), r.get("event_id"))
        g = (genderOf(r.get("event_short")) or r.get("gender") or
             majority.get(ekey))
        d = relay_pairs.setdefault(
            ((r.get("division") or "").strip().lower(), canon), {})
        e = d.setdefault(ekey, {"g": None, "times": [], "rounds": set()})
        e["g"] = e["g"] or g
        e["rounds"].add(rowRound(r))
        t = r.get("time_seconds")
        if t and float(t) > 0:
            e["times"].append(float(t))
    relay_gender = {}
    for evs in relay_pairs.values():
        if len(evs) != 2:
            continue
        # An all-prelims event beside an all-finals one is ROUNDS of one
        # race, not a gender pair -- the canonical merge handles those.
        a, b = evs.values()
        if ({"prelim"} in (a["rounds"], b["rounds"]) and
                {"final"} in (a["rounds"], b["rounds"])):
            continue
        unknown = [k for k, v in evs.items() if v["g"] is None and v["times"]]
        known = sorted({v["g"] for v in evs.values() if v["g"]})
        if len(unknown) == 1 and len(known) == 1 and known[0] in ("M", "F"):
            relay_gender[unknown[0]] = "F" if known[0] == "M" else "M"
        elif len(unknown) == 2:
            med = {k: _median(evs[k]["times"]) for k in unknown}
            fast, slow = sorted(unknown, key=lambda k: med[k])
            if med[fast] / med[slow] < 0.90:
                relay_gender[fast], relay_gender[slow] = "M", "F"

    # ---- singleton relays: paced against the meet's own fields --------- #
    # A lone genderless relay (this meet ran only the women's 4x200) has
    # no sibling to pair with, but the meet itself is full of gendered
    # running events. Per-leg pace, Riegel-adjusted for leg distance,
    # against each gendered reference: whichever gender's fields predict
    # this relay better claims it. Self-calibrating -- a JV meet's slow
    # references pull the boundary down with them -- and it must win by a
    # clear margin or the relay stays unscored.
    refs = {}                      # (div, canon) -> {gender: (t_leg, d_leg)}
    agg = {}
    for r in rows:
        t = r.get("time_seconds")
        if r.get("is_field") or not t or float(t) <= 0:
            continue
        ekey = (r.get("div_id"), r.get("event_id"))
        g = (genderOf(r.get("event_short")) or r.get("gender") or
             majority.get(ekey) or relay_gender.get(ekey))
        if g not in ("M", "F"):
            continue
        canon = canonicalEvent(r.get("event_short"))
        if r.get("is_relay"):
            spec = relayLegsSpec(r.get("event_short"))
            if not spec:
                continue
            n, total = spec
        else:
            dm = eventDistance(r.get("event_short"),
                               r.get("distance_meters"))
            if not dm:
                continue
            n, total = 1, dm
        div = (r.get("division") or "").strip().lower()
        agg.setdefault((div, canon, g), {"n": n, "d": total, "ts": []})
        agg[(div, canon, g)]["ts"].append(float(t))
    for (div, canon, g), a in agg.items():
        refs.setdefault((div, canon), {})[g] = (
            _median(a["ts"]) / a["n"], a["d"] / a["n"])

    for (div, canon), evs in relay_pairs.items():
        for ekey, e in evs.items():
            if (e["g"] or ekey in relay_gender or not e["times"]):
                continue
            spec = relayLegsSpec(canon)
            if not spec:
                continue
            n, total = spec
            t_leg, d_leg = _median(e["times"]) / n, total / n
            err = {"M": [0.0, 0.0], "F": [0.0, 0.0]}   # [sum w*err, sum w]
            for (rdiv, rcanon), by_g in refs.items():
                if rdiv != div or rcanon == canon or len(by_g) != 2:
                    continue
                for g, (rt, rd) in by_g.items():
                    k = _riegelK(math.sqrt(d_leg * rd))
                    pred = rt * (d_leg / rd) ** k
                    w = 1.0 / (0.1 + abs(math.log(d_leg / rd)))
                    err[g][0] += w * abs(math.log(t_leg / pred))
                    err[g][1] += w
            if not (err["M"][1] and err["F"][1]):
                continue
            em = err["M"][0] / err["M"][1]
            ef = err["F"][0] / err["F"][1]
            if abs(em - ef) > 0.05 and min(em, ef) < 0.12:
                relay_gender[ekey] = "M" if em < ef else "F"

    # ---- group into canonical events ---------------------------------- #
    groups = {}
    for r in rows:
        div = (r.get("division") or "").strip()
        # Nameless events must NOT merge on their empty canonical name;
        # each keeps its own identity by id.
        canon = (canonicalEvent(r.get("event_short")) or
                 f"#{r.get('div_id')}:{r.get('event_id')}")
        ekey = (r.get("div_id"), r.get("event_id"))
        g = (genderOf(r.get("event_short")) or r.get("gender") or
             majority.get(ekey) or relay_gender.get(ekey) or
             shuttleGender(r.get("event_short")) or "?")
        key = (div.lower(), canon, g)
        grp = groups.setdefault(key, {"division": div, "gender": g,
                                      "canon": canon, "rows": []})
        grp["rows"].append(r)

    divisions = {}
    points_by_result = {}
    n_scored = 0

    for key in sorted(groups):
        grp = groups[key]
        rows_g = grp["rows"]

        # rounds: finals beat everything else when any exist. Per RESULT,
        # not per event: the feed stamps a round code on each row, and
        # prelims and finals often share one event_id.
        finals = [r for r in rows_g if rowRound(r) == "final"]
        # An event where EVERY row is a final (the normal case) needs no
        # cut; the cut is for genuine mixed-round events.
        scoring_rows = finals if (finals and len(finals) < len(rows_g)) \
            else rows_g

        is_relay = any(r.get("is_relay") for r in scoring_rows)
        is_field = any(_value(r) is not None and
                       (r.get("is_field") or
                        r.get("result_kind") in ("field", "combined"))
                       for r in scoring_rows)

        entries = _entries(scoring_rows, is_relay)
        awarded = _award(entries)

        # No gender means no standings to put points in: the event still
        # displays with its places, but awards nothing.
        gendered = grp["gender"] in ("M", "F")
        # An EnRoute division's rows are split reads inside other races:
        # display them, score nothing -- points here would double-count
        # the athlete's real event in the parent division.
        enroute = bool(_ENROUTE.search(grp["division"] or ""))
        scoreable = gendered and not enroute
        if not scoreable:
            awarded = [(label, 0.0, False, row)
                       for label, _pts, _win, row in awarded]
        n_scored += 1 if any(pts > 0 for _l, pts, _w, _r in awarded) else 0

        for label, pts, win, row in awarded:
            if pts > 0:
                points_by_result[row["result_id"]] = fmtPoints(pts)

        # ---- the event's display rows ---------------------------------- #
        # One row per athlete, their BEST mark from ANY round, ranked by
        # that mark -- the compiled page compares marks, same as the XC
        # compiled disclaimer. Points ride the athlete from the
        # finals-based scoring above, so a blazing prelim ranks where
        # the time deserves while its points still tell the finals
        # story. Race pages keep the full round-by-round detail.
        pts_by_key = {}
        for label, pts, win, row in awarded:
            k = _entryKey(row, is_relay)
            if k and pts > 0:
                pts_by_key[k] = fmtPoints(pts)

        disp = _entries(rows_g, is_relay)
        ev_rows = []
        i = 0
        while i < len(disp):
            j = i
            while j + 1 < len(disp) and disp[j + 1][0] == disp[i][0]:
                j += 1
            label = f"T{i + 1}" if j > i else str(i + 1)
            for p in range(i, j + 1):
                r = disp[p][2]
                ev_rows.append({**r, "place_label": label,
                                "points": pts_by_key.get(
                                    _entryKey(r, is_relay), "")})
            i = j + 1
        # athletes with no rankable mark anywhere (NH, FOUL, DNF) still
        # show, once, unranked
        ranked_keys = {_entryKey(e[2], is_relay) for e in disp}
        for r in rows_g:
            k = _entryKey(r, is_relay)
            if k in ranked_keys:
                continue
            ranked_keys.add(k)
            ev_rows.append({**r, "place_label": "", "points": ""})

        name_src = finals[0] if finals else rows_g[0]
        dist = eventDistance(name_src.get("event_short"),
                             name_src.get("distance_meters"))
        div = divisions.setdefault(grp["division"].lower(), {
            "name": grp["division"] or "All divisions",
            "teams": {}, "events": []})
        # A nameless running event with a stored distance can at least be
        # called "60m"; only truly unknowable ones stay "Event N".
        if name_src.get("event_short"):
            ev_name = displayEvent(name_src.get("event_short"))
        elif dist and not is_field and not is_relay:
            ev_name = f"{int(dist)}m"
        else:
            ev_name = f"Event {name_src.get('event_id')}"
        div["events"].append({
            "name": ev_name,
            "gender": grp["gender"], "is_relay": is_relay,
            "is_field": is_field, "distance": dist, "rows": ev_rows,
            "scored": scoreable,
            # The Finals tag marks an actual cut -- an event whose rows
            # are ALL finals is just an event, not news.
            "scored_finals": bool(finals) and len(finals) < len(rows_g)})

        # ---- team sums ------------------------------------------------ #
        if scoreable:
            teams = div["teams"].setdefault(grp["gender"], {})
            # every school that showed up gets a standings row, scoring
            # or not -- a zero is information too
            for r in rows_g:
                school, link_st = _teamIdentity(r, split)
                if school:
                    teams.setdefault(school.lower(), {
                        "school": school, "points": 0.0, "wins": 0,
                        "link_school": scorableSchool(r.get("school")),
                        "link_state": link_st})
            for label, pts, win, row in awarded:
                school, _st = _teamIdentity(row, split)
                if not school or pts <= 0:
                    continue
                cell = teams[school.lower()]
                cell["points"] += pts
                if win:
                    cell["wins"] += 1

        # ---- official sums --------------------------------------------- #
        # The feed's own per-result `score`, summed as published -- the
        # meet's actual rules (its own point table, its multis, its
        # tie resolutions) instead of our standard one. Tracked per
        # gender so the coverage gate can judge each standings table.
        off = div.setdefault("_official", {
            "M": {"teams": {}, "events": 0, "gaps": 0},
            "F": {"teams": {}, "events": 0, "gaps": 0}, "bad": False})
        # an EnRoute division never scores, so published points inside
        # one neither pay nor poison nor count as coverage
        o_rows = [] if enroute else [
            (r, s) for r in rows_g if (s := _scoreVal(r)) is not None]
        if o_rows:
            if not gendered:
                # published points with no standings to put them in --
                # official totals for this division would be missing
                # them, so the whole division's official view is off
                off["bad"] = True
            else:
                o = off[grp["gender"]]
                o["events"] += 1
                # a win, officially: holding the event's top score
                # (once per school -- a same-school tie is one win)
                top = max(s for _r, s in o_rows)
                won = set()
                for r, s in o_rows:
                    school, link_st = _teamIdentity(r, split)
                    if not school:
                        continue
                    cell = o["teams"].setdefault(school.lower(), {
                        "school": school, "points": 0.0, "wins": 0,
                        "link_school": scorableSchool(r.get("school")),
                        "link_state": link_st})
                    cell["points"] += s
                    if s == top and school.lower() not in won:
                        cell["wins"] += 1
                        won.add(school.lower())
        elif scoreable and any(pts > 0 for _l, pts, _w, _r in awarded):
            # we scored it, the meet's data did not: a coverage gap
            off[grp["gender"]]["gaps"] += 1

    out_divs = []
    for dkey in sorted(divisions):
        div = divisions[dkey]
        off = div.pop("_official", None)
        div["official"] = {"M": None, "F": None}
        for g in ("M", "F"):
            # standings places, tie-aware: two schools on the same total
            # share a T-place, same convention as the event rows
            div["teams"][g] = _standings(div["teams"].get(g, {}))

            # ★ THE 100% GATE, per standings table. Official totals show
            #   only when every event WE could score also carries the
            #   meet's published points (gaps == 0) and no published
            #   points sit in an event without a gender to stand in
            #   (bad). A partial sum is worse than none: it reads as a
            #   final score while silently missing events.
            o = off and off[g]
            if (o and o["events"] > 0 and o["gaps"] == 0
                    and not off["bad"]):
                # same rule as computed: every school that showed up
                # gets a row -- a zero is information too
                for t in div["teams"][g]:
                    o["teams"].setdefault(t["school"].lower(), {
                        "school": t["school"], "points": 0.0, "wins": 0,
                        "link_school": t.get("link_school"),
                        "link_state": t.get("link_state")})
                div["official"][g] = _standings(o["teams"])
        # events in a stable reading order: running by distance (parsed
        # from the name when the column is empty, so the 200 stops
        # sorting after the 3200), then field/multis, relays last,
        # unknown-distance running events after the known ones
        div["events"].sort(key=lambda e: (
            bool(e["is_relay"]), bool(e["is_field"]),
            float(e["distance"]) if e["distance"] else 1e9,
            e["name"], e["gender"]))
        out_divs.append(div)

    return {"divisions": out_divs, "points_by_result": points_by_result,
            "n_scored_events": n_scored}
