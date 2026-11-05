# Project: xc-predictor / racecast
# File:    unit_board.py
# Purpose: conferences, leagues, sections and divisions RANKED AGAINST EACH
#          OTHER (owner, 2026-11-02: "I'd like to be able to rank conferences
#          and divisions and such"; the measure: "maybe 1 and 3?").
#
#   The team board already ranks the teams INSIDE a unit (its unit filter).
#   This ranks the units themselves, from the season team board
#   (team_season, span 'season', scope 'usa'), two ways side by side:
#
#     top_avg   the mean top-5 average of the unit's best N teams (N on the
#               page, default 5). A big conference is not dragged down by
#               its weakest members, a small one not padded: a unit with
#               fewer than N rated teams is averaged over what it has, and
#               says so.
#     raced     the unit's seven best runners -- from its teams' stored top
#               sevens, which hold every runner who could be in it -- entered
#               as one team in a meet against every other unit of the kind,
#               scored by the team boards' own rules (team_rank.raceStored).
#               Points, low wins: the top end of a unit, raced.
#
# ★ A HIGH SCHOOL UNIT IS A NAME INSIDE A STATE. "Central" is a section in
#   several states and "D2" a division in all of them; the key is (unit,
#   state) for the high school kinds. A college conference, division or
#   region spans states and is keyed by its name alone.
# ! A STATE DIVISION ("D2") RANKED ACROSS STATES is fifty different things
#   with one name; it is only offered with a state picked (KINDS[...][2]).

KINDS = {
    # kind: (label, college?, needs a state)
    "conference":  ("Conferences", True, False),
    "division":    ("Divisions", True, False),
    "region":      ("Regions", True, False),
    "league":      ("Leagues", False, False),
    "section":     ("Sections", False, False),
    "section_div": ("Section divisions", False, True),
    "state_div":   ("State divisions", False, True),
    "area":        ("Areas", False, True),
    # ★ THE NIKE REGIONS (owner, 2026-11-04: "there should be rankings for
    #   NXR regions somewhere in conferences"): high school teams by their
    #   state's NXR region (NXR_STATES), California as its own (it
    #   qualifies through CIF State). No state needed: a region spans them.
    "nxr":         ("NXR regions", False, False),
}

# The 2026 Nike Cross Regionals and their states (NikeCrossNationals.com /
# RunnerSpace, checked 2026-11-04); California qualifies through CIF State.
NXR_STATES = {
    "NXR Northwest": ("AK", "HI", "ID", "MT", "OR", "WA", "WY"),
    "NXR Southwest": ("AZ", "CO", "NM", "NV", "UT"),
    "NXR Heartland": ("IA", "KS", "MN", "ND", "NE", "SD", "WI"),
    "NXR Midwest": ("IL", "IN", "MI", "MO", "OH"),
    "NXR South": ("AR", "LA", "MS", "OK", "TX"),
    "NXR Southeast": ("AL", "DC", "DE", "FL", "GA", "KY", "MD", "NC", "SC", "TN", "VA", "WV"),
    "NXR Northeast": ("CT", "MA", "ME", "NH", "NJ", "NY", "PA", "RI", "VT"),
    "California (CIF State)": ("CA",),
}
STATE_NXR = {st: region for region, sts in NXR_STATES.items() for st in sts}
# a kind whose unit spans states (keyed by name alone, like a college one)
_STATELESS = {"nxr"}
# ★ RANKED INSIDE A PARENT (owner, 2026-11-02: "you cannot find like the
#   best conference in just d3"): the units a board can be narrowed by, per
#   level. A conference board inside NCAA DIII; a league board inside a
#   section.
WITHIN = {True: ("division", "region"), False: ("section", "state_div")}
DEFAULT_N = 5
MIN_TEAMS = 2          # a unit of one team is that team, not a unit
SQUAD = 7


def unitKey(kind, unit, state):
    """The identity of a unit: its name, and its state for a high school
    kind. Pure."""
    return (unit, None) if KINDS[kind][1] or kind in _STATELESS else (unit, state)


def rankUnits(rows, kind, n=DEFAULT_N):
    """team_season rows (dicts: school, state, top5_mean, ratings, and the
    unit column `kind`) -> [unit dict], ranked by top_avg, with the raced
    rank and points of each. Pure."""
    from team_rank import raceStored
    units = {}
    for r in rows:
        u = (r.get(kind) or "").strip()
        if not u:
            continue
        units.setdefault(unitKey(kind, u, r.get("state")), []).append(r)
    out = []
    for (name, state), teams in units.items():
        if len(teams) < MIN_TEAMS:
            continue
        rated = sorted((t for t in teams if t.get("top5_mean") is not None),
                       key=lambda t: -float(t["top5_mean"]))
        top = rated[:n]
        pool = sorted((float(x) for t in teams for x in (t.get("ratings") or [])), reverse=True)
        out.append({
            "unit": name, "state": state, "n_teams": len(teams), "n_rated": len(rated),
            "top_avg": round(sum(float(t["top5_mean"]) for t in top) / len(top), 2) if top else None,
            "top_n": len(top),
            "best_team": top[0]["school"] if top else None,
            "best_state": top[0].get("state") if top else None,
            "best_avg": round(float(top[0]["top5_mean"]), 2) if top else None,
            "ratings": pool[:SQUAD],
            "teams": [{"school": t["school"], "state": t.get("state"),
                       "top5_mean": round(float(t["top5_mean"]), 2)} for t in top],
        })
    # race the units: each one entrant, its seven best runners
    entrants = [{"school": f"{u['unit']}|{u['state'] or ''}", "ratings": u["ratings"], "_u": u}
                for u in out if len(u["ratings"]) >= 5]
    raced = raceStored(entrants) or []
    for r in raced:
        r["_u"]["raced_rank"] = r["rank"]
        r["_u"]["raced_points"] = r["points"]
    out.sort(key=lambda u: (u["top_avg"] is None, -(u["top_avg"] or 0), u["unit"]))
    for i, u in enumerate(out, 1):
        u["rank"] = i
        u.pop("ratings", None)
    return out


def sortUnits(units, by):
    """By the page's sort: 'top' (top_avg, the default) or 'raced' (points,
    low first; units too small to race last). Pure."""
    if by == "raced":
        return sorted(units, key=lambda u: (u.get("raced_rank") is None,
                                            u.get("raced_rank") or 0, u["unit"]))
    return sorted(units, key=lambda u: u["rank"])


_SQL = """
    SELECT school, state, rank, points, n_athletes, top5_mean, best_rating,
           best_person_id, ratings, {cols}
    FROM   team_season
    WHERE  span = 'season' AND scope = 'usa'
      AND  pool = %(pool)s AND sport = %(sport)s AND year = %(year)s
      AND  NULLIF(btrim({col}), '') IS NOT NULL
      {where}
"""

_ALL_COLS = ("division", "region", "conference", "league", "section", "section_div",
             "state_div", "area")


def _where(state, within, args):
    out = ""
    if state:
        out += " AND state = %(state)s"
        args["state"] = state
    for i, (col, val) in enumerate(sorted((within or {}).items())):
        if col not in _ALL_COLS or not val:
            continue
        out += f" AND upper({col}) = upper(%(w{i})s)"
        args[f"w{i}"] = val
    return out


def load(cur, kind, pool, sport, year, state=None, within=None, unit=None):
    """The season board's teams that carry this unit kind -- inside the
    `within` units ({col: value}), and only `unit` when given."""
    if kind not in KINDS:
        raise ValueError(kind)
    if kind == "nxr":
        return _loadRegions(cur, pool, sport, year, state)
    args = {"pool": pool, "sport": sport, "year": int(year)}
    w = dict(within or {})
    if unit:
        w[kind] = unit
    cur.execute(_SQL.format(col=kind, cols=", ".join(_ALL_COLS),
                            where=_where(state, w, args)), args)
    return [dict(r) for r in cur.fetchall()]


def _loadRegions(cur, pool, sport, year, state=None):
    """The season board's teams, each with its NXR region (STATE_NXR) in
    the "nxr" column; a team outside every region (abroad, unknown state)
    is left out."""
    args = {"pool": pool, "sport": sport, "year": int(year)}
    where = ""
    if state:
        where = " AND state = %(state)s"
        args["state"] = state
    cur.execute(f"""
        SELECT school, state, rank, points, n_athletes, top5_mean, best_rating,
               best_person_id, ratings, {", ".join(_ALL_COLS)}
        FROM   team_season
        WHERE  span = 'season' AND scope = 'usa'
          AND  pool = %(pool)s AND sport = %(sport)s AND year = %(year)s
          {where}""", args)
    out = []
    for r in cur.fetchall():
        r = dict(r)
        r["nxr"] = STATE_NXR.get((r.get("state") or "").upper())
        if r["nxr"]:
            out.append(r)
    return out


def regionHref(region, pool=None, sport=None):
    """An NXR region's teams: the Teams board narrowed to its states."""
    from urllib.parse import urlencode
    q = {"board": "teams", "pool": pool or "hs_m", "sport": sport or "XC",
         "state": ",".join(NXR_STATES.get(region, ()))}
    return "/rankings?" + urlencode(q)


def options(cur, col, pool, sport, year, state=None):
    """The values of `col` on this season's board, most teams first: the
    pickers' choices."""
    if col not in _ALL_COLS:
        return []
    args = {"pool": pool, "sport": sport, "year": int(year)}
    cur.execute(f"""SELECT {col} AS v, count(*) AS n FROM team_season
                    WHERE span = 'season' AND scope = 'usa' AND pool = %(pool)s
                      AND sport = %(sport)s AND year = %(year)s
                      AND NULLIF(btrim({col}), '') IS NOT NULL{_where(state, None, args)}
                    GROUP BY 1 ORDER BY 2 DESC, 1""", args)
    return [r["v"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]


# ---- the generated badge -------------------------------------------------- #
# ★ OUR OWN MARK, NOT THEIRS (owner, 2026-11-02: "can you generate some
#   logos?"). A conference's real logo is its trademark; this is a badge
#   drawn from its name -- initials on a colour fixed by the name -- so every
#   unit has a mark and none of them pretends to be the official one.
_PALETTE = ("#1f4e79", "#7a1f2b", "#1d5e3a", "#5b2a86", "#8a4b08", "#0f5f66",
            "#5a5a14", "#2c3e8f", "#7d2e63", "#334155")
_SKIP = {"of", "the", "and", "&", "at", "in", "for", "conference", "league", "athletic",
         "association", "section", "division"}


def initials(name, most=4):
    """'Middlesex League' -> 'ML'; 'NESCAC' -> 'NESC'; 'NCAA DIII' -> 'DIII'
    (a word with a digit names the unit); 'Big Ten' -> 'BT'. Pure."""
    words = [w for w in str(name or "").replace("-", " ").split() if w]
    if not words:
        return "?"
    digit = [w for w in words if any(c.isdigit() for c in w) or w.upper() in ("DI", "DII", "DIII")]
    if digit:
        return digit[-1][:most].upper()
    if len(words) == 1:
        w = words[0]
        return (w if w.isupper() else w[:3])[:most].upper()
    picked = [w for w in words if w.lower() not in _SKIP]
    if len(picked) <= 1:
        picked = words
    return "".join(w[0] for w in picked)[:most].upper()


def badgeSvg(name, size=28):
    """An inline SVG shield with the unit's initials. Markup-safe: the only
    text in it is letters and digits from initials()."""
    import hashlib
    import re as _re
    text = _re.sub(r"[^A-Z0-9]", "", initials(name)) or "?"
    colour = _PALETTE[int(hashlib.sha1(str(name).encode("utf-8")).hexdigest(), 16) % len(_PALETTE)]
    fs = {1: 15, 2: 13, 3: 10.5, 4: 8.5}.get(len(text), 8.5)
    return (f'<svg class="unit-badge" viewBox="0 0 32 32" width="{int(size)}" height="{int(size)}" '
            f'aria-hidden="true"><path d="M16 1.5 L29 6 V15.5 C29 23 23.5 28.5 16 30.5 '
            f'C8.5 28.5 3 23 3 15.5 V6 Z" fill="{colour}"/><path d="M16 4 L26.5 7.7 V15.5 '
            f'C26.5 21.6 22 26.2 16 27.9 C10 26.2 5.5 21.6 5.5 15.5 V7.7 Z" fill="none" '
            f'stroke="#fff" stroke-opacity=".55" stroke-width="1"/><text x="16" y="19.5" '
            f'text-anchor="middle" font-family="system-ui,sans-serif" font-weight="700" '
            f'font-size="{fs}" fill="#fff">{text}</text></svg>')


def unitHref(kind, unit, state=None, pool=None, sport=None):
    """/unit/<kind>/<name>[?state=..&pool=..&sport=..]: a unit's own page."""
    from urllib.parse import quote, urlencode
    hs = (kind in KINDS and not KINDS[kind][1]) or str(pool or "").startswith("hs")
    q = {k: v for k, v in (("state", state if hs else None),
                           ("pool", pool), ("sport", (sport or "").lower() or None)) if v}
    return f"/unit/{kind}/{quote(str(unit), safe='')}" + (f"?{urlencode(q)}" if q else "")


def chipHref(kind, raw, state=None, college=None):
    """The template global `unit_href`: a unit chip's link, or None for a
    kind with no page (county, district, class) or a high school unit with
    no state."""
    if kind not in KINDS or not raw:
        return None
    # ! a division (NCAA DIII, NAIA ...) is a college unit whatever the row
    #   says; a conference or region may be a high school one (Delaware's)
    is_college = (KINDS[kind][1] if college is None or kind == "division"
                  else bool(college))
    if not is_college and not state:
        return None
    pool = "college_m" if is_college else "hs_m"
    return unitHref(kind, raw, state if not is_college else None, pool)


def label(kind, value, state=None):
    """How a unit is NAMED on the page: the chips' own labeller
    (school_units._label, short form) -- 'Middlesex League' for the stored
    'MIDDLESEX LEAGUE', 'NESCAC' left alone, 'NCS D2' for a section division."""
    try:
        from school_units import _label
        return _label(kind, value, False, {"state": state})
    except Exception:                                     # noqa: BLE001
        return value


def logoKey(kind, name):
    """The school_logo name a unit's own logo is stored under
    (scripts/unit_logos.py). Pure."""
    return f"unit:{kind}:{str(name).strip().upper()}"


_ALIAS = {"of": None, "map": {}}


def _aliases():
    """{(kind, normKey): stored name} for every unit logo the crest cache
    holds, rebuilt when the cache is. A logo is stored under the spelling
    unit_logos.py looked up ('BIG TEN'); a school row may carry another
    ('BIG TEN CONFERENCE'), and both are the one unit."""
    try:
        from school_logo import _CRESTS
        m = _CRESTS.get("map") or {}
    except Exception:                                     # noqa: BLE001
        return {}
    if _ALIAS["of"] is not m:
        out = {}
        for key in m:
            if str(key).startswith("unit:"):
                _, kind, name = str(key).split(":", 2)
                out.setdefault((kind, normKey(name)), name)
        _ALIAS["of"], _ALIAS["map"] = m, out
    return _ALIAS["map"]


def storedLogoUrl(kind, name, state=None, px=64):
    """The unit's own stored logo's URL, or None (no badge)."""
    try:
        from school_logo import crestUrl
    except Exception:                                     # noqa: BLE001
        return None
    st = state if not KINDS.get(kind, ("", True))[1] else None
    try:
        url = crestUrl(logoKey(kind, name), st, px=px)
        if not url:
            alias = _aliases().get((kind, normKey(name)))
            if alias and alias.upper() != str(name).strip().upper():
                url = crestUrl(logoKey(kind, alias), st, px=px)
        return url
    except Exception:                                     # noqa: BLE001
        return None


def chipLogo(kind, raw, state=None, size=16):
    """The template global `unit_logo`: a unit chip's small logo -- only a
    stored one. A drawn badge at chip size is three unreadable letters, so a
    unit with no logo shows its name alone. Markup."""
    import html
    from markupsafe import Markup
    if (kind not in KINDS and kind != "state") or not raw:
        return Markup("")
    # a state wears its flag (unit_logos --flags: unit:state:MA)
    url = storedLogoUrl(kind, str(raw).upper() if kind == "state" else raw, state, px=64)
    if not url:
        return Markup("")
    return Markup(f'<img class="unit-chip-logo" src="{html.escape(url)}" alt="" '
                  f'width="{int(size)}" height="{int(size)}" loading="lazy" decoding="async">')


def logoForLink(link, px=64):
    """A unit's stored logo for a "/unit/<kind>/<name>[?state=..]" link, or
    None. For the search index, which keeps a link rather than the unit
    (school_logo.crestUrlForLink is the schools' twin)."""
    from urllib.parse import unquote, urlsplit, parse_qs
    if not link or not str(link).startswith("/unit/"):
        return None
    parts = urlsplit(str(link))
    bits = parts.path[len("/unit/"):].split("/", 1)
    if len(bits) != 2 or not bits[1]:
        return None
    state = (parse_qs(parts.query).get("state") or [None])[0]
    return storedLogoUrl(bits[0], unquote(bits[1]), state, px=px)


def stampUnitMarks(rows, kinds=("division", "conference")):
    """Each row's units' logos and pages, for the boards the browser draws
    (recruiting.js): row[kind + "_logo"] and row[kind + "_href"] where the
    row names one -- a logo only where one is stored, so no broken image.
    College units (the recruiting boards' rows are colleges)."""
    memo = {}
    for r in rows or ():
        for k in kinds:
            v = r.get(k)
            if not v:
                continue
            if (k, v) not in memo:
                memo[(k, v)] = (storedLogoUrl(k, v, None, px=64), chipHref(k, v, None, True))
            logo, href = memo[(k, v)]
            if logo:
                r[k + "_logo"] = logo
            if href:
                r[k + "_href"] = href


def unitMark(kind, name, state=None, size=28, shown=None):
    """The unit's mark for a page: its own logo when scripts/unit_logos.py
    stored one (served like a school crest), else the generated badge.
    Markup."""
    import html
    url = storedLogoUrl(kind, name, state, px=128 if size > 32 else 64)
    # ★ A NIKE REGION WEARS ITS REGIONAL'S MARK (owner, 2026-11-04: "why do
    #   NXR regions not have logos"): the one the meet links already use,
    #   NXR <REGION> then NXR's then NXN's (seriesLogoUrl)
    if not url and kind == "nxr":
        px = 128 if size > 32 else 64
        if str(name).lower().startswith("california"):
            # ★ CALIFORNIA WEARS ITS FLAG (owner, 2026-11-04: "CIF should be
            #   california flag"): the state's own mark, unit_logos --flags
            url = storedLogoUrl("state", "CA", px=px)
        else:
            url = seriesLogoUrl(str(name).strip().upper(), px=px)
    if url:
        return (f'<img class="unit-badge unit-logo" src="{html.escape(url)}" alt="" '
                f'width="{int(size)}" height="{int(size)}" loading="lazy" decoding="async" '
                f'style="object-fit:contain">')
    return badgeSvg(shown or name, size=size)



# ====================================================================== #
#  THE CONFERENCES TAB (owner, 2026-11-02: "the entire thing is pretty bad,
#  should probably rework" -- wrong units in the lists, the look, the
#  numbers, too few units). A board inside /rankings, served by
#  /api/rankings/units (pages3), drawn by rankings.js like the Teams tab.
# ====================================================================== #

_GENERIC_WORDS = {"conference", "conferences", "athletic", "athletics", "league", "association",
                  "intercollegiate", "the", "of", "and", "girls", "boys", "men", "women",
                  # a meet's event label filed as the conference (owner's board,
                  # 2026-11-02: "Gnac Combined Events" beside GNAC)
                  "combined", "events", "indoor", "outdoor", "championship", "championships",
                  "xc"}


def normKey(value):
    """One unit under its spellings: 'HENLOPEN ATHLETIC' and 'HENLOPEN' are
    'henlopen'; 'NIC-10 GIRLS' and 'NIC-10' are 'nic 10'. Pure."""
    import re
    toks = [_ABBR_WORDS.get(t, t) for t in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if t not in _GENERIC_WORDS]
    key = " ".join(toks) or str(value or "").strip().lower()
    return _KEY_ALIAS.get(key, key)


# ★ ONE CONFERENCE, TWO SPELLINGS ON THE BOARD (owner, 2026-11-03: "two MTN
#   wests", and Conference USA as both "C-USA" and "USA"). A shortened word
#   is the word; a code the feeds write two ways is one key.
_ABBR_WORDS = {"mtn": "mountain", "mt": "mountain", "intl": "international",
               "assn": "association", "conf": "conference"}
_KEY_ALIAS = {"c usa": "usa", "cusa": "usa", "mw": "mountain west",
              "ne10": "northeast 10", "a10": "a 10", "atlantic 10": "a 10",
              "big 10": "big ten", "socon": "southern", "metro atlantic": "maac",
              # the feeds file half the Big East as "EAST" (Villanova, DePaul,
              # Creighton, Seton Hall, UConn; owner checked, 2026-11-03)
              "east": "big east"}


_CODE_WORDS = {"ncaa", "naia", "njcaa", "nccaa", "uscaa", "di", "dii", "diii", "sec", "acc",
               "aac", "mac", "wac", "cia", "cif", "ucs", "ihsa", "osaa", "uil", "khsaa"}


def prettyName(value):
    """How a stored (shouting) unit name is shown. Codes stay as stored --
    NESCAC, PSAC, NCAA DIII, A-10 -- and words read as words: 'MOUNTAIN EAST'
    -> 'Mountain East', 'IVY LEAGUE' -> 'Ivy League', 'CENTENNIAL' ->
    'Centennial'. A word of six letters or fewer standing alone is taken
    for a code; with other words beside it, a word with a vowel is a word.
    Pure."""
    import re
    raw = str(value or "").strip()
    if not raw or raw != raw.upper():
        return raw                                   # already mixed case: as written
    words = raw.split()

    def word(w, alone):
        core = re.sub(r"[^A-Za-z]", "", w)
        if not core or w.lower() in _CODE_WORDS or any(c.isdigit() for c in w):
            return w if not core or w.lower() in _CODE_WORDS else \
                re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize()
                       if len(m.group(0)) > 3 else m.group(0), w)
        if alone and len(core) <= 6:
            return w
        if not alone and (len(core) <= 2 or not re.search(r"[AEIOUY]", core)):
            return w
        return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), w)
    return " ".join(word(w, len(words) == 1) for w in words)


def validKeys(cur, kind, college):
    """{normKey: True} for the units whose schools are mostly of this level
    (school_unit.is_college): a college board lists no high school league
    filed as a conference, and the reverse. A unit needs two schools of the
    level and no more than a quarter of its schools of the other."""
    if kind not in KINDS:
        return {}
    if kind in _STATELESS:
        return None               # every region is real: nothing to test
    cur.execute(f"""SELECT {kind} AS v,
                           count(DISTINCT (school, state)) FILTER (WHERE is_college) AS c,
                           count(DISTINCT (school, state)) FILTER (WHERE NOT is_college) AS h
                    FROM   school_unit
                    WHERE  NULLIF(btrim({kind}), '') IS NOT NULL
                    GROUP  BY 1""")
    by = {}
    from unit_names import notAConference
    for r in cur.fetchall():
        v, c, h = (r["v"], r["c"], r["h"]) if isinstance(r, dict) else r
        # a meet or a region numeral filed as a conference never ranks
        if kind == "conference" and notAConference(v):
            continue
        k = normKey(v)
        cc, hh = by.get(k, (0, 0))
        by[k] = (cc + int(c or 0), hh + int(h or 0))
    out = {}
    for k, (c, h) in by.items():
        mine, other = (c, h) if college else (h, c)
        if mine >= 2 and other <= 0.25 * (c + h):
            out[k] = True
    return out


TOP_TEAMS = 25


# ★ A CONFERENCE IS ONE DIVISION (owner's Big 12 board, 2026-11-03: "Andrew
#   (GA) 90.0" fourteenth, below Texas Tech). A team filed under a conference
#   whose other teams are another division is a name collision or a stray
#   vote, not a member: when ODD_SHARE of a conference's teams that carry a
#   division agree on it, a team carrying a different one is left out of
#   its board and its page. A team with no division stays.
ODD_SHARE = 0.75


def dropOddDivision(teams, kind="conference"):
    """The teams of one unit less those in a division most of it is not. Pure."""
    from collections import Counter
    if kind != "conference":
        return list(teams)
    divs = Counter((t.get("division") or "").strip().upper() for t in teams
                   if (t.get("division") or "").strip())
    if not divs:
        return list(teams)
    top, n = divs.most_common(1)[0]
    if n < ODD_SHARE * sum(divs.values()) or n < 3:
        return list(teams)
    return [t for t in teams
            if not (t.get("division") or "").strip()
            or (t.get("division") or "").strip().upper() == top]


def buildBoard(rows, kind, n=DEFAULT_N, valid=None):
    """team_season rows -> one row per unit, ranked by the average of its
    best n teams' top-5 averages. Spellings merge (normKey); units the level
    test rejects (valid) are left out; a unit needs MIN_TEAMS teams. Pure.

    Each unit row: unit (the stored spelling most of its teams carry), name
    (prettyName), key, state, n_teams, avg, top_n, top25 (teams in the
    national top TOP_TEAMS), best team (school, state, avg), top runner
    (person_id, rating, school) and members (every team, best first)."""
    from collections import Counter
    groups = {}
    for r in rows:
        raw = (r.get(kind) or "").strip()
        if not raw:
            continue
        k = normKey(raw)
        if valid is not None and k not in valid:
            continue
        st = None if KINDS[kind][1] or kind in _STATELESS else r.get("state")
        groups.setdefault((k, st), []).append(r)
    out = []
    for (k, st), teams in groups.items():
        teams = dropOddDivision(teams, kind)
        if len(teams) < MIN_TEAMS:
            continue
        spelled = Counter((t.get(kind) or "").strip() for t in teams).most_common(1)[0][0]
        rated = sorted((t for t in teams if t.get("top5_mean") is not None),
                       key=lambda t: -float(t["top5_mean"]))
        top = rated[:n]
        runner = max((t for t in teams if t.get("best_rating") is not None),
                     key=lambda t: float(t["best_rating"]), default=None)
        out.append({
            "unit": spelled, "name": prettyName(spelled), "key": k, "state": st,
            "n_teams": len(teams), "top_n": len(top),
            "avg": round(sum(float(t["top5_mean"]) for t in top) / len(top), 2) if top else None,
            "top25": sum(1 for t in teams if t.get("rank") and int(t["rank"]) <= TOP_TEAMS),
            "best_team": top[0]["school"] if top else None,
            "best_state": top[0].get("state") if top else None,
            "best_avg": round(float(top[0]["top5_mean"]), 2) if top else None,
            "runner_id": runner.get("best_person_id") if runner else None,
            "runner_rating": round(float(runner["best_rating"]), 2) if runner else None,
            "runner_school": runner.get("school") if runner else None,
            "runner_state": runner.get("state") if runner else None,
            "members": [{"school": t["school"], "state": t.get("state"),
                         "top5_mean": round(float(t["top5_mean"]), 2)
                                      if t.get("top5_mean") is not None else None,
                         "rank": t.get("rank")} for t in rated],
        })
    return out


SORTS = {"avg": ("avg", True), "top25": ("top25", True), "teams": ("n_teams", True),
         "name": ("name", False), "best": ("best_avg", True), "runner": ("runner_rating", True)}


def sortBoard(units, sort="avg", direction=None):
    """In place, by one of SORTS; `direction` 'asc'/'desc' overrides the
    column's natural one. Ranks follow the order. Pure."""
    key, desc = SORTS.get(sort, SORTS["avg"])
    if direction in ("asc", "desc"):
        desc = direction == "desc"
    present = [u for u in units if u.get(key) is not None]
    missing = [u for u in units if u.get(key) is None]
    present.sort(key=lambda u: (u[key].lower() if isinstance(u[key], str) else u[key]),
                 reverse=desc)
    units[:] = present + sorted(missing, key=lambda u: u["name"])
    for i, u in enumerate(units, 1):
        u["rank"] = i
    return units


# ====================================================================== #
#  A CONFERENCE CHAMPIONSHIP'S OWN MARK ON ITS RACE PAGE (owner, 2026-11-02:
#  "Should we put the conference logos ... maybe race pages?" -- yes, on the
#  header of a race that is the conference's own meet). The meet's name has
#  to name the unit AND several of the race's schools have to be in it.
# ====================================================================== #
MEET_UNIT_KINDS = ("conference", "league")
MEET_UNIT_MIN = 3
_MEET_WORDS = ("champ", "final", "conference", "league", "tournament")


def meetUnitPick(meet_name, counts, n_schools):
    """The (kind, value, state, college) the meet is the championship of, or
    None. `counts` is {(kind, value, state, college): schools of the race in
    it}. Pure.

    The unit's words (normKey: 'SUNKIST' -> 'sunkist', 'BIG TEN CONFERENCE'
    -> 'big ten') must all be words of the meet's name, at least
    MEET_UNIT_MIN of the race's schools must be in it, and either the name
    reads like a championship or the unit is most of the field."""
    import re
    words = set(re.findall(r"[a-z0-9]+", str(meet_name or "").lower()))
    if not words:
        return None
    champ = any(w.startswith(_MEET_WORDS) for w in words)
    best = None
    for key, n in counts.items():
        kind, value = key[0], key[1]
        if kind not in MEET_UNIT_KINDS or n < MEET_UNIT_MIN:
            continue
        nk = normKey(value).split()
        if not nk or not all(w in words for w in nk):
            continue
        if not champ and n < 0.6 * max(n_schools, 1):
            continue
        rank = (n, len(nk))
        if best is None or rank > best[0]:
            best = (rank, key)
    return best[1] if best else None


def meetUnit(cur, meet_name, schools, sport="XC"):
    """{kind, unit, name, href, mark} for a race page's header, or None."""
    schools = sorted({s for s in schools if s})
    if not schools or not meet_name:
        return None
    cur.execute("""SELECT school, state, conference, league, is_college
                   FROM   school_unit
                   WHERE  school = ANY(%s) AND sport = %s""", (schools, sport))
    seen, counts = set(), {}
    for r in cur.fetchall():
        school, state, conf, league, college = (
            (r["school"], r["state"], r["conference"], r["league"], r["is_college"])
            if isinstance(r, dict) else r)
        for kind, v in (("conference", conf), ("league", league)):
            v = (v or "").strip()
            if not v:
                continue
            k = (kind, v, (state or "").upper() if not college else "", bool(college))
            if (k, school) in seen:
                continue
            seen.add((k, school))
            counts[k] = counts.get(k, 0) + 1
    pick = meetUnitPick(meet_name, counts, len(schools))
    if not pick:
        return None
    kind, value, state, college = pick
    href = chipHref(kind, value, state or None, college)
    if not href:
        return None
    shown = label(kind, value, state or None)
    return {"kind": kind, "unit": value, "name": prettyName(shown), "href": href,
            "mark": unitMark(kind, value, state or None, size=22, shown=shown)}


# ====================================================================== #
#  THE SAME MARK WHEREVER THE MEET IS LINKED (owner, 2026-11-03: "for races
#  like secs, it doesn't always show the logo in all places the race is
#  linked"). A list row has only the meet's NAME -- no field to count -- so
#  this reads the name alone, against the units that HAVE a stored logo,
#  and is stricter about the words than meetUnitPick.
# ====================================================================== #
_MEET_LOGO = {"of": None, "cache": {}}
MEET_LOGO_CACHE = 20000


# a meet name that says it is a high school meet: a CIF section, a state
# high school association, a school level or a sub-varsity race
_HS_MEET = __import__("re").compile(
    r"\bcif\b|\bsection(al|s)?\b|\bhigh school\b|\bhs\b|\bmiddle school\b|\bms\b|"
    r"\bjunior high\b|\bjv\b|\bfrosh\b|\bfreshm[ae]n\b|\bsophomore\b|"
    r"\b(uil|ihsa|osaa|fhsaa|khsaa|mshsaa|mshsl|nysphsaa|piaa|vhsl|ghsa|wiaa|njsiaa|mpssaa|"
    r"chsaa|ohsaa|mhsaa|ahsaa|tssaa|ncsaa|scisa|scshsl|nchsaa|ihsaa|iahsaa|kshsaa|nsaa|"
    r"sdhsaa|ndhsaa|whsaa|wiaa|aia|nmaa|uhsaa|niaa|ciac|miaa|nhiaa|vpa|riil|mpa|diaa|wvssac|"
    r"lhsaa|mhsaa|aaa|osaa|asaa|hhsaa|chsaa|fhsaa)\b", __import__("re").I)


def meetLogoPick(meet_name, keys):
    """The (kind, stored name) of the unit `meet_name` is the championship
    of, among `keys` = {(kind, normKey): stored name}, or None. Pure.

    The name must read like a championship, hold every word of the unit's
    key, and a ONE-word key ('sec', 'summit', 'southern') must either be
    written as a code in the name ('SEC', 'GNAC') or be followed by
    'conference' / 'league' ('Southern Conference'), so 'Southern Indiana
    Championships' is not SoCon's. Two different units equally good: none."""
    import re
    raw = str(meet_name or "")
    toks = re.findall(r"[A-Za-z0-9]+", raw)
    low = [t.lower() for t in toks]
    words = set(low)
    if not words or not any(w.startswith(_MEET_WORDS) for w in words):
        return None
    # ★ A HIGH SCHOOL MEET IS NOT A COLLEGE CONFERENCE'S (owner, 2026-11-05:
    #   "CIF North Coast Section ..." wore the North Coast Athletic
    #   Conference's logo -- every word of "north coast" is in its name)
    hs_meet = bool(_HS_MEET.search(raw))
    best, tie = None, False
    for (kind, key), name in keys.items():
        if kind not in MEET_UNIT_KINDS:
            continue
        if hs_meet and kind == "conference":
            continue
        kw = key.split()
        if not kw or not all(w in words for w in kw):
            continue
        if len(kw) == 1:
            w = kw[0]
            idx = [i for i, t in enumerate(low) if t == w]
            coded = any(toks[i].isupper() and len(toks[i]) <= 6 for i in idx)
            named = any(i + 1 < len(low) and low[i + 1] in ("conference", "league")
                        for i in idx)
            if not (coded or named):
                continue
        rank = len(kw)
        if best is None or rank > best[0]:
            best, tie = (rank, (kind, name)), False
        elif rank == best[0] and best[1] != (kind, name):
            tie = True
    return None if best is None or tie else best[1]


# ★ THE NATIONAL SERIES WEAR THEIR OWN MARK (owner, 2026-11-03: "can we get
#   logos for NXN/NXR and footlocker?"). Not a conference: a meet that
#   belongs to one of these series by its name. Stored under
#   unit:series:<KEY> by scripts/unit_logos.py --series.
SERIES = (
    ("NXN", r"\bNXN\b|nike\s+cross\s+nationals"),
    ("NXR", r"\bNXR\b|nike\s+cross\s+regional"),
    # ★ THE HIGH SCHOOL NATIONALS UNDER EACH NAME (owner, 2026-11-03: "not
    #   seeing any logos for footlocker/brooks"): Kinney (1979), Foot Locker
    #   (1993), Eastbay (2021), Champs Sports (2022), Foot Locker again
    #   (2023), and since August 2025 Brooks XC, presented by Fleet Feet --
    #   its nationals and its regionals ("Brooks XC South Championship",
    #   "Brooks Midwest XC Championships"). Each era wears its own mark.
    ("BROOKS XC", r"\bbrooks\s+xc\b|\bbrooks\b[\w\s]{0,20}?\b(?:xc|cross\s*country)\s+"
                  r"(?:championships?|regionals?|nationals?|qualifiers?)"),
    ("CHAMPS SPORTS", r"champs\s+sports\s+(?:xc|cross)"),
    ("FOOT LOCKER", r"foot\s*locker|eastbay\s+cross\s+country|kinney\s+cross\s+country"),
)


# NXR's nine regions since 2023 (New York split from the Northeast): a
# regional's own mark is "NXR <REGION>", falling back to NXR's, then NXN's
NXR_REGIONS = ("SOUTHEAST", "SOUTHWEST", "NORTHEAST", "NORTHWEST", "NEW YORK",
               "CALIFORNIA", "HEARTLAND", "MIDWEST", "SOUTH")


def seriesOf(meet_name):
    """The SERIES key a meet's name belongs to, or None; an NXR meet that
    names its region is 'NXR <REGION>'. Pure."""
    import re
    name = str(meet_name or "")
    for key, rx in SERIES:
        if re.search(rx, name, re.I):
            if key == "NXR":
                for reg in NXR_REGIONS:
                    if re.search(r"\b" + reg.replace(" ", r"\s+") + r"\b", name, re.I):
                        return f"NXR {reg}"
            return key
    return None


def seriesLogoUrl(key, px=64):
    """The stored mark for a series key, walking NXR <REGION> -> NXR -> NXN."""
    chain = [key]
    if key.startswith("NXR"):
        chain += ["NXR", "NXN"]
    for k in chain:
        url = storedLogoUrl("series", k, None, px=px)
        if url:
            return url
    return None


def meetLogo(meet_name, college=None, size=16):
    """The template global `meet_logo`: a meet link's small conference logo
    -- only a stored one, else nothing. `college` narrows to conferences
    (True) or leagues (False). Markup."""
    import html
    from markupsafe import Markup
    if not meet_name:
        return Markup("")
    keys = _aliases()
    if _MEET_LOGO["of"] is not keys:
        _MEET_LOGO["of"], _MEET_LOGO["cache"] = keys, {}
    ck = (str(meet_name), college)
    cache = _MEET_LOGO["cache"]
    if ck not in cache and seriesOf(meet_name):
        sk = seriesOf(meet_name)
        cache[ck] = (seriesLogoUrl(sk), ("series", sk))
    if ck not in cache:
        sub = keys
        if college is not None:
            want = "conference" if college else "league"
            sub = {k: v for k, v in keys.items() if k[0] == want}
        pick = meetLogoPick(meet_name, sub)
        url = storedLogoUrl(pick[0], pick[1], None, px=64) if pick else None
        if len(cache) > MEET_LOGO_CACHE:
            cache.clear()
        cache[ck] = (url, pick)
    url, pick = cache[ck]
    if not url:
        return Markup("")
    title = html.escape(prettyName(pick[1]))
    # ★ AT ITS OWN WIDTH (2026-11-03): a series' mark is a wordmark (Foot
    #   Locker, Champs Sports), squeezed into a square it is a smudge; the
    #   height is the chip's and the width follows, capped (style.css is-meet)
    return Markup(f'<img class="unit-chip-logo is-meet" src="{html.escape(url)}" alt="" '
                  f'title="{title}" height="{int(size)}" loading="lazy" decoding="async">')
