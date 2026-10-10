"""
record_pages.py -- the routes' half of the record books: request
arguments to a page context, the links between the pages, the HS-scale
stamp, and the template helpers. app.py's routes are thin wrappers over
these. ★ 2026-10-10 (owner: history and record books).

! NO QUERY HERE BUT record_books.loadBook (one primary key) and, for the
  school page, the identity chips the school page already reads.
"""

import record_books as RB

SPORTS = {"xc": "XC", "tf": "TF"}
SPORT_WORDS = {"xc": "Cross Country", "tf": "Track & Field"}


def recordsPath(state, sport="xc", level="hs", gender="m"):
    """The one spelling of a state record page: lower-case state, and only
    the arguments that differ from the defaults."""
    q = [f"sport={sport}"]
    if level != "hs":
        q.append(f"level={level}")
    if gender != "m":
        q.append("gender=" + RB.GENDERS[gender][0])
    return f"/records/{state.lower()}?" + "&".join(q)


def parseArgs(args):
    """request.args -> (sport slug, level, gender) with defaults; never an
    error -- an unknown value is the default, as on the landing pages."""
    sport = (args.get("sport") or "xc").strip().lower()
    if sport not in SPORTS:
        sport = "xc"
    level = (args.get("level") or "hs").strip().lower()
    if level not in RB.LEVELS:
        level = "hs"
    gender = RB.parseGender(args.get("gender"))
    return sport, level, gender


def stampBook(ctx, stamp):
    """HS-equivalent twins on every rating a record page shows, through the
    site's stampBoardRows (`stamp`). Single races carry their distance and
    take the exact factor; seasons and teams the representative one.
    Handles both shapes -- a state book (grades as (grade, words, rows))
    and a school book (grades / improved / teams grouped per pool).
    Returns True when the scale toggle has something to do."""
    if not ctx:
        return False
    races = list(ctx.get("races") or [])
    seasons = list(ctx.get("seasons") or [])
    teams = []
    for b in ctx.get("grades") or []:
        groups = b.get("grades") if isinstance(b, dict) else [b]
        for _g, _w, rows in groups or []:
            seasons += rows
    for b in ctx.get("improved") or []:
        seasons += b.get("rows") or []
    for t in ctx.get("teams") or []:
        teams += t.get("rows") if "rows" in t else [t]
    moved = False
    for sec in ctx.get("events") or []:
        if sec.get("kind") != "running":
            continue
        for g in ("M", "F"):
            moved = stamp(sec["tables"].get(g) or [],
                          rating_keys=("speed_rating",),
                          sport=ctx.get("sport")) or moved
    if races:
        moved = stamp(races, rating_keys=("rating",)) or moved
    if seasons:
        moved = stamp(seasons, rating_keys=("rating",)) or moved
    if teams:
        moved = stamp(teams, rating_keys=("top5_mean",)) or moved
    return moved


def statePage(cur, state, args, stamp, landing):
    """Everything records.html renders. `landing` is the landing module
    (STATE_NAMES, landingPath, POOLS); `stamp` is stampBoardRows."""
    import datetime
    sport, level, gender = parseArgs(args)
    pool = RB.poolFor(level, gender)
    key = RB.stateKey(SPORTS[sport], pool, state)
    book = RB.loadBook(cur, "state", key)
    has_hs = stampBook(book, stamp)
    otd = RB.onThisDay(cur, datetime.date.today(), state)
    state_name = landing.STATE_NAMES[state]
    level_words = RB.LEVELS[level][1]
    gword = RB.genderWord(level, gender)
    title = (f"{state_name} {level_words} {gword.capitalize()} "
             f"{SPORT_WORDS[sport]} All-Time Records")
    slug = {"hs": "hs", "ms": "ms", "college": "college"}[level]
    lp_pool = f"{slug}-{RB.GENDERS[gender][1 if level == 'college' else 0]}"
    landing_path = (landing.landingPath(sport, lp_pool, state)
                    if lp_pool in landing.POOLS else landing.landingPath(sport, "hs-boys", state))
    return dict(
        book=book, has_hs_view=has_hs, otd=otd,
        title=title,
        description=(f"The all-time {state_name} {level_words.lower()} "
                     f"{gword} {SPORT_WORDS[sport].lower()} lists: the 100 best "
                     f"races and seasons ever, the best freshman to senior "
                     f"seasons and the best team seasons, era-adjusted."),
        path=recordsPath(state, sport, level, gender),
        era_note=RB.ERA_NOTE,
        sport=sport, sport_words=SPORT_WORDS[sport], level=level,
        level_words=level_words, gender=gender, gender_word=gword,
        pool=pool, level_pool=pool, state=state, state_name=state_name,
        landing_path=landing_path,
        sport_links=[(s, recordsPath(state, s, level, gender)) for s in SPORTS],
        level_links=[(lv, words, recordsPath(state, sport, lv, gender))
                     for lv, (_p, words, _g) in RB.LEVELS.items()],
        gender_links=[(g, RB.genderWord(level, g), recordsPath(state, sport, level, g))
                      for g in RB.GENDERS],
        state_links=[(c, landing.STATE_NAMES[c],
                      recordsPath(c, sport, level, gender))
                     for c in landing.US_STATES if c in landing.STATE_NAMES],
    )


def poolWords(pool):
    """'hs_f' -> 'High school girls' (the school page's own group label)."""
    level, _, g = (pool or "").split("|")[0].partition("_")
    lv = {"hs": "High school", "ms": "Middle school", "college": "College",
          "elem": "Elementary", "pro": "Pro"}.get(level, level)
    word = RB.genderWord("college" if level in ("college", "pro") else "hs", g)
    return f"{lv} {word}".strip() if word else lv


def genderWordFor(rows, g):
    """'Boys' / 'Girls', or 'Men' / 'Women' when the rows are college."""
    pool = next((r.get("pool") for r in rows or () if r.get("pool")), "") or ""
    adult = pool.startswith(("college", "pro"))
    return RB.genderWord("college" if adult else "hs", g.lower()).capitalize()


def registerJinja(app):
    """The template helpers the record pages use."""
    from school import seasonLabel
    app.jinja_env.globals["season_label"] = seasonLabel
    app.jinja_env.globals["rb_pool_words"] = poolWords
    app.jinja_env.globals["rb_gender_word"] = genderWordFor
    app.jinja_env.globals["rb_era_note"] = RB.ERA_NOTE
    app.jinja_env.filters["rb_dist"] = RB.distanceLabel
