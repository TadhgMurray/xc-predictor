# Project: xc-predictor / racecast
# File:    cards.py
# Purpose: The share card: one 1200x630 PNG per athlete, served as the
#          page's og:image so a link pasted into a group chat, a story or
#          a text shows the name, the team, the rating and the ranks
#          (owner, 2026-09-07, issue 281: "I like the share cards").
#
# ★ RENDERED FROM THE SAME ROWS THE HEADER READS. The card asks
#   athleteCardData for exactly what the athlete page's header shows:
#   the latest season with three races (any sport, majority school), its
#   rating and grade, the best race, the rank line. A card that said
#   something the page does not would be a lie in a screenshot.
#
# ! PILLOW, DEJAVU, CACHED. Pillow draws it; DejaVu Sans is on every
#   Debian box (fallback: Pillow's built-in font, ugly but never a 500);
#   the PNG is cached under static/cards/ and redrawn after CARD_TTL so a
#   new season shows within the day. The route is the only writer.
import io
import os
import time

CARD_W, CARD_H = 1200, 630
CARD_TTL = 6 * 3600
# ! NOT UNDER static/: the site runs as a user that cannot write the repo
#   (PermissionError on the first card, 2026-09-07). A world-writable
#   temp location, overridable; the route serves the file itself.
CARD_DIR = os.environ.get("XCP_CARD_DIR", "/var/tmp/racecast-cards")

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu",
              "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")

INK, MUTED, LINE, PAPER, GOLD = "#111111", "#6b7280", "#d9d9d4", "#fafaf7", "#a97a12"


def _font(bold, size):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in _FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                               # older Pillow
        return ImageFont.load_default()


def _fit(draw, text, bold, size, max_w, min_size=28):
    """The largest font at or under `size` that fits the width, and the
    text cut with no ellipsis (the owner's rule) if even min_size does not."""
    while size >= min_size:
        f = _font(bold, size)
        if draw.textlength(text, font=f) <= max_w:
            return f, text
        size -= 4
    f = _font(bold, min_size)
    while text and draw.textlength(text, font=f) > max_w:
        text = text[:-1].rstrip()
    return f, text


def athleteCardData(cur, person_id):
    """The card's fields from the same rows the page header reads, or
    None when the athlete does not exist."""
    from school_identity import schoolLabelFor, _collegeState
    from grade_label import gradeLabel
    from school_units import unitsForPerson, unitsFor, homeStateOf
    cur.execute("""SELECT NULLIF(TRIM(concat_ws(' ', first_name, last_name)), '') AS name,
                          school FROM athletes WHERE person_id = %s
                   ORDER BY (COALESCE(TRIM(first_name), '') <> '') DESC LIMIT 1""",
                (person_id,))
    a = cur.fetchone()
    if a is None:
        return None
    a = dict(a) if not isinstance(a, dict) else a
    cur.execute("""SELECT mean_rating, sport, pool, year, n_races, state, school, grade
                   FROM athlete_season WHERE person_id = %s
                   ORDER BY (n_races >= 3) DESC, last_race DESC NULLS LAST,
                            year DESC, n_races DESC LIMIT 1""", (person_id,))
    season = cur.fetchone()
    season = dict(season) if season is not None and not isinstance(season, dict) else season
    school = (season or {}).get("school") or a.get("school")
    if season and (season.get("pool") or "").startswith("college") \
            and not _collegeState(season.get("school")):
        cur.execute("""SELECT school, count(*) AS n FROM ranking_results
                       WHERE person_id = %s AND pool = %s AND sport = %s AND year = %s
                         AND division IS NOT NULL AND school IS NOT NULL
                       GROUP BY school ORDER BY n DESC LIMIT 1""",
                    (person_id, season["pool"], season["sport"], season["year"]))
        c = cur.fetchone()
        if c:
            school = c["school"] if isinstance(c, dict) else c[0]
    cur.execute("""SELECT max(best_rating) AS best, count(*) AS seasons, sum(n_races) AS races
                   FROM athlete_season WHERE person_id = %s""", (person_id,))
    agg = cur.fetchone()
    agg = dict(agg) if agg is not None and not isinstance(agg, dict) else (agg or {})
    cur.execute("""SELECT sport FROM athlete_season WHERE person_id = %s
                   ORDER BY best_rating DESC NULLS LAST LIMIT 1""", (person_id,))
    bs = cur.fetchone()
    best_sport = (bs["sport"] if isinstance(bs, dict) else bs[0]) if bs else ""
    pool = (season or {}).get("pool")
    label_year = None
    if season:
        label_year = season["year"] + 1 if season["sport"] == "TF" else season["year"]
    units = []
    try:
        home = homeStateOf(cur, person_id)
        fb = unitsFor(cur, school, home) if school else []
        br = unitsFor(cur, school, home, collapse=False) if school else []
        units = unitsForPerson(cur, person_id, fallback=fb, borrow=br)
    except Exception:                                 # noqa: BLE001
        cur.connection.rollback()
    ranks = []
    try:
        from app import buildRankLine
        for e in (buildRankLine(cur, person_id, season) or []) if season else []:
            if e.get("rank"):
                ranks.append(f"{e['label']} #{e['rank']:,}")
    except Exception:                                 # noqa: BLE001
        cur.connection.rollback()
    return {
        "name": a.get("name") or "Unknown",
        "school": schoolLabelFor(school, pool, (season or {}).get("state")) if school else "",
        "grade": gradeLabel((season or {}).get("grade"), pool) or "",
        "units": [u["label"] for u in units][:4],   # kept for a later use; not drawn
        "rating": (season or {}).get("mean_rating"),
        "season": (f"{label_year} {season['sport']} season" if season else ""),
        "best": agg.get("best"),
        "best_sport": best_sport,
        "races": agg.get("races") or 0,
        "seasons": agg.get("seasons") or 0,
        "ranks": ranks[:9],
    }


PHOTO = 280          # the photo slot, a rounded square, top left (owner: "space for a photo")


def _initials(name):
    parts = [p for p in (name or "").replace("-", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo-card.png")


DARK, DARK_SLOT, DARK_MUTED, DARK_LINE, DARK_PILL = "#111111", "#2a2a2a", "#c9ced4", "#444444", "#1c1c1c"
SILVER, BRONZE, DARK_RANK = "#dde1e6", "#c98a4b", "#80858c"


def _medal(i):
    """Gold, silver and bronze for the first three rows, dim after, so the
    medals read against the rest."""
    return (GOLD, SILVER, BRONZE)[i] if i < 3 else DARK_RANK


def _rank(dr, x, y, i, font):
    """The rank number at (x, y) in the row's font. The first three sit on
    a gold, silver or bronze disc, the rest are a dim digit; the disc is
    centred on where a plain digit's glyph sits so the column lines up."""
    txt = f"{i + 1}"
    if i < 3:
        # the glyph box of a plain digit at this spot: the disc goes there
        l, t, r_, b = dr.textbbox((x, y), "8", font=font)
        cx, cy = (l + r_) / 2, (t + b) / 2
        r = int((b - t) * 0.86)
        dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_medal(i))
        l, t, r_, b = dr.textbbox((0, 0), txt, font=font)
        dr.text((cx - (l + r_) / 2, cy - (t + b) / 2), txt, font=font, fill=DARK)
    else:
        dr.text((x, y), txt, font=font, fill=DARK_RANK)


BAND = 14               # the gold band across the top


def _logoLight(logo):
    """The wordmark in white, for the dark card."""
    from PIL import Image
    a = logo.split()[3]
    white = Image.new("L", logo.size, 255)
    return Image.merge("RGBA", (white, white, white, a))


def renderAthleteCard(d, photo_path=None):
    """The PNG bytes for one athlete's card. `photo_path`: a picture for
    the slot when accounts exist (283); until then the slot carries the
    initials, so the layout is the one the photo will land in.

    The look the owner chose (2026-09-07, after nine renders): dark, a
    gold band across the top, the photo top left, the name and team
    beside it, the rating in gold under, the best race and race count
    beside it, every rank as a pill on up to two rows with Nation filled
    gold, and the white wordmark with its tagline under it top right."""
    from PIL import Image, ImageDraw, ImageOps
    img = Image.new("RGB", (CARD_W, CARD_H), DARK)
    dr = ImageDraw.Draw(img)
    M = 64
    dr.rectangle((0, 0, CARD_W, BAND), fill=GOLD)

    # the photo slot
    px, py = M, M
    mask = Image.new("L", (PHOTO, PHOTO), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, PHOTO - 1, PHOTO - 1), radius=28, fill=255)
    placed = False
    if photo_path and os.path.exists(photo_path):
        try:
            ph = Image.open(photo_path).convert("RGB")
            ph = ImageOps.fit(ph, (PHOTO, PHOTO), method=Image.LANCZOS)
            img.paste(ph, (px, py), mask)
            placed = True
        except Exception:                          # noqa: BLE001
            placed = False
    if not placed:
        slot = Image.new("RGB", (PHOTO, PHOTO), DARK_SLOT)
        sd = ImageDraw.Draw(slot)
        ini = _initials(d["name"])
        fi = _font(True, 110)
        w = sd.textlength(ini, font=fi)
        sd.text(((PHOTO - w) / 2, PHOTO / 2 - 72), ini, font=fi, fill="#6b6b66")
        img.paste(slot, (px, py), mask)

    # the wordmark top right, the tagline under it
    tag = "Every result on one comparable scale"
    ft = _font(False, 20)
    try:
        logo = _logoLight(Image.open(LOGO).convert("RGBA"))
        lh = 40
        lw = int(logo.width * lh / logo.height)
        logo = logo.resize((lw, lh), Image.LANCZOS)
        img.paste(logo, (CARD_W - M - lw, M - 8), logo)
    except Exception:                              # noqa: BLE001
        fw = _font(True, 30)
        dr.text((CARD_W - M - dr.textlength("racecast.co", font=fw), M - 6), "racecast.co", font=fw, fill="#ffffff")
    dr.text((CARD_W - M - dr.textlength(tag, font=ft), M + 40), tag, font=ft, fill=DARK_MUTED)

    # name and team
    tx = M + PHOTO + 40
    TEXT_W = CARD_W - M - tx
    f, name = _fit(dr, d["name"], True, 76, TEXT_W - 40, 40)
    dr.text((tx, M + 76), name, font=f, fill="#ffffff")
    sub = " · ".join(x for x in [d["school"], d["grade"]] if x)
    f, sub = _fit(dr, sub, False, 32, TEXT_W, 22)
    dr.text((tx, M + 172), sub, font=f, fill=DARK_MUTED)

    # the three numbers, the rating in gold
    y = M + 224
    cols = [("RATING", f"{d['rating']:.1f}" if d["rating"] is not None else "-", d["season"]),
            ("BEST RACE", f"{d['best']:.1f}" if d["best"] is not None else "-", d["best_sport"]),
            ("RACES", f"{d['races']:,}", f"{d['seasons']} seasons")]
    x = tx
    for i, (lab, val, note) in enumerate(cols):
        dr.text((x, y), lab, font=_font(False, 20), fill=DARK_MUTED)
        big = _font(True, 80 if i == 0 else 54)
        dr.text((x, y + 24 if i == 0 else y + 48), val, font=big, fill=GOLD if i == 0 else "#ffffff")
        dr.text((x, y + 116), note, font=_font(False, 22), fill=DARK_MUTED)
        x += 300 if i == 0 else 210

    # every rank as a pill, Nation filled gold, up to two rows
    y = M + 388
    x = M
    fp = _font(True, 24)
    rows_used = 0
    for i, r in enumerate(d["ranks"]):
        w = dr.textlength(r, font=fp) + 32
        if x + w > CARD_W - M:
            x = M
            y += 54
            rows_used += 1
            if rows_used >= 2:
                break
        if i == 0:
            dr.rounded_rectangle((x, y, x + w, y + 44), radius=22, fill=GOLD)
            dr.text((x + 16, y + 9), r, font=fp, fill=DARK)
        else:
            dr.rounded_rectangle((x, y, x + w, y + 44), radius=22, outline=DARK_LINE, width=2, fill=DARK_PILL)
            dr.text((x + 16, y + 9), r, font=fp, fill="#f2f2ee")
        x += w + 12
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def cachedAthleteCard(cur, person_id):
    """The card's PNG path, drawn now if missing or older than CARD_TTL;
    None when the athlete does not exist."""
    os.makedirs(CARD_DIR, exist_ok=True)
    path = os.path.join(CARD_DIR, f"athlete-{int(person_id)}.png")
    try:
        if time.time() - os.path.getmtime(path) < CARD_TTL:
            return path
    except OSError:
        pass
    data = athleteCardData(cur, person_id)
    if data is None:
        return None
    png = renderAthleteCard(data)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)
    return path


# ===================================================================== #
#  RACE AND SCHOOL CARDS (281, "more share cards", 2026-09-07)
# ===================================================================== #

def _clock(seconds):
    if seconds is None:
        return "-"
    s = float(seconds)
    if s >= 3600:
        return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"
    m = int(s // 60)
    return f"{m}:{s - 60 * m:05.2f}" if s < 600 else f"{m}:{int(round(s - 60 * m)):02d}"


def _frame(title, sub, dr, img):
    """The dark card's shared frame: the gold band, the wordmark and tagline
    top right, a title and a sub line top left. Returns the y under them."""
    from PIL import Image
    M = 64
    dr.rectangle((0, 0, CARD_W, BAND), fill=GOLD)
    tag = "Every result on one comparable scale"
    ft = _font(False, 20)
    try:
        logo = _logoLight(Image.open(LOGO).convert("RGBA"))
        lh = 40
        lw = int(logo.width * lh / logo.height)
        logo = logo.resize((lw, lh), Image.LANCZOS)
        img.paste(logo, (CARD_W - M - lw, M - 8), logo)
    except Exception:                              # noqa: BLE001
        fw = _font(True, 30)
        dr.text((CARD_W - M - dr.textlength("racecast.co", font=fw), M - 6), "racecast.co", font=fw, fill="#ffffff")
    dr.text((CARD_W - M - dr.textlength(tag, font=ft), M + 40), tag, font=ft, fill=DARK_MUTED)
    # the title wraps onto a second line before it shrinks: a meet's name
    # is long, and cutting "Invitational" to "Invitati" is not allowed
    width = CARD_W - 2 * M - 420       # stops short of the wordmark and its tagline

    def wrap(size):
        f = _font(True, size)
        lines, cur = [], ""
        for w in title.split():
            t = (cur + " " + w).strip()
            if dr.textlength(t, font=f) <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return f, lines

    # two lines at the largest size that holds them; cut only as a last resort
    for size in (48, 42, 36, 30):
        f, lines = wrap(size)
        if len(lines) <= 2 and all(dr.textlength(ln, font=f) <= width for ln in lines):
            break
    else:
        f, one = _fit(dr, title, True, 30, width, 24)
        lines = [one]
    yy = M + 2
    for ln in lines:
        dr.text((M, yy), ln, font=f, fill="#ffffff")
        yy += int(f.size * 1.18)
    yy = max(yy, M + 60)
    f, sb = _fit(dr, sub, False, 26, CARD_W - 2 * M, 18)
    dr.text((M, yy + 8), sb, font=f, fill=DARK_MUTED)
    return yy + 66


def raceCardData(cur, sport, meet_id, div_id, event_id=None):
    """The race card's fields: the meet's name and where, and the top five
    with time and rating. None when the race has no rows."""
    from app import get_race_header, get_race_results, get_tf_race_header, get_tf_race_results
    from school_identity import schoolLabel
    if sport == "XC":
        header = get_race_header(cur, meet_id, div_id)
        rows = get_race_results(cur, meet_id, div_id) if header else []
    else:
        header = get_tf_race_header(cur, meet_id, div_id, event_id)
        rows = get_tf_race_results(cur, meet_id, div_id, event_id) if header else []
    if not header or not rows:
        return None
    header = dict(header)
    date = str(rows[0].get("date") or "")[:10]
    if sport == "XC":
        where = header.get("course_name") or ""
        dist = header.get("distance")
        what = f"{int(dist)}m" if dist else ""
    else:
        from tf_points import prettyEventName
        try:
            what = prettyEventName(header.get("event_short") or "") or (header.get("event_short") or "")
        except Exception:                          # noqa: BLE001
            what = header.get("event_short") or ""
        where = header.get("venue_name") or header.get("state") or ""
    sub = " · ".join(x for x in [what, header.get("division") or "", where, date] if x)
    top = []
    for r in rows[:6]:
        top.append({
            "name": (r.get("name") or r.get("athlete_name") or "Unknown").strip(),
            "school": schoolLabel(r.get("school")) if r.get("school") else "",
            "time": _clock(r.get("time_seconds")) if r.get("time_seconds") is not None else (str(r.get("mark") or "")),
            "rating": r.get("speed_rating"),
        })
    return {"title": header.get("meet_name") or "Race", "sub": sub, "top": top,
            "n": len(rows), "difficulty": header.get("difficulty")}


def renderRaceCard(d):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (CARD_W, CARD_H), DARK)
    dr = ImageDraw.Draw(img)
    M = 64
    y = _frame(d["title"], d["sub"], dr, img)
    # the top five as rows: place, name, school, time, rating in gold
    fp, ft = _font(True, 28), _font(True, 28)
    row_h = 50
    for i, r in enumerate(d["top"]):
        yy = y + i * row_h
        if i == 0:
            dr.rounded_rectangle((M - 16, yy - 7, CARD_W - M + 16, yy + row_h - 9), radius=12, fill=DARK_PILL)
        _rank(dr, M, yy, i, fp)
        f, name = _fit(dr, r["name"], True, 28, 360, 20)
        dr.text((M + 52, yy), name, font=f, fill="#ffffff")
        f, school = _fit(dr, r["school"], False, 22, 330, 16)
        dr.text((M + 430, yy + 4), school, font=f, fill=DARK_MUTED)
        tw = dr.textlength(r["time"], font=ft)
        dr.text((CARD_W - M - 150 - tw, yy), r["time"], font=ft, fill="#ffffff")
        rt = f"{r['rating']:.1f}" if r["rating"] is not None else "-"
        rw = dr.textlength(rt, font=ft)
        dr.text((CARD_W - M - rw, yy), rt, font=ft, fill=GOLD)
    dr.text((M, CARD_H - 50), f"{d['n']} finishers", font=_font(False, 22), fill=DARK_MUTED)
    # the course difficulty, labelled (owner: "not just 'course'")
    if d.get("difficulty") is not None:
        val = f"{float(d['difficulty']) * 100:+.1f}%"
        lab = "COURSE DIFFICULTY"
        fv, fl = _font(True, 30), _font(False, 18)
        vw, lw = dr.textlength(val, font=fv), dr.textlength(lab, font=fl)
        dr.text((CARD_W - M - lw, CARD_H - 84), lab, font=fl, fill=DARK_MUTED)
        dr.text((CARD_W - M - vw, CARD_H - 62), val, font=fv, fill=GOLD)
    lab = "RATING"
    dr.text((CARD_W - M - dr.textlength(lab, font=_font(False, 18)), y - 30), lab, font=_font(False, 18), fill=DARK_MUTED)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def schoolCardData(cur, school, state=None):
    """The school card: the newest season of either sport, its top seven
    by season rating, the team rating (the top five's mean)."""
    from school import schoolRoster, currentSeason
    from school_identity import schoolLabel
    best = None
    for sport in ("XC", "TF"):
        y = currentSeason(cur, school, sport)
        if y is None:
            continue
        # a track season is stored as its opening academic year: XC 2025 and
        # TF 2025 (spring 2026) both read 2025, and the track one is newer
        key = (y, 1 if sport == "TF" else 0)
        if best is None or key > best[0]:
            best = (key, sport, y)
    if best is None:
        return None
    _, sport, year = best
    rows = schoolRoster(cur, school, year, sport)
    rows = [dict(r) for r in rows if r.get("mean_rating") is not None]
    rows.sort(key=lambda r: -float(r["mean_rating"]))
    top = rows[:7]
    five = [float(r["mean_rating"]) for r in top[:5]]
    team = sum(five) / len(five) if len(five) == 5 else None
    label = year + 1 if sport == "TF" else year
    from grade_label import gradeLabel
    ranks = teamRanks(cur, school, state, sport, year, top[0]["pool"] if top else None)
    return {"ranks": ranks,
            "title": schoolLabel(school) if not state else f"{school} ({state})",
            "sub": f"{label} {'cross country' if sport == 'XC' else 'track'} · top seven by season rating",
            "team": team, "athletes": len(rows),
            "top": [{"name": (r.get("name") or "").strip() or "Unknown",
                     "grade": gradeLabel(r.get("grade"), r.get("pool")) or "",
                     "rating": float(r["mean_rating"]), "races": r.get("n_races") or 0,
                     "pool": r.get("pool")}
                    for r in top]}


def teamRanks(cur, school, state, sport, year, pool):
    """[("Nation", 12), ("CA", 3), ("NCS D2", 1), ...] for the team's season:
    nation and state from team_season's own boards, every unit of the
    school by racing the unit's stored squads against each other
    (team_rank.raceStored), the athlete rank line's shape for a team
    (owner, 2026-09-07)."""
    from team_rank import raceStored
    from school_units import unitsFor, schoolsInUnits
    out = []
    if not pool:
        return out
    try:
        cur.execute("SELECT to_regclass('public.team_season')")
        if cur.fetchone()[0] is None:
            return out
        cur.execute("""SELECT state FROM team_season WHERE span = 'season' AND scope = 'usa'
                       AND school = %s AND pool = %s AND sport = %s AND year = %s
                       ORDER BY rank LIMIT 1""", (school, pool, sport, year))
        r = cur.fetchone()
        home = (r["state"] if isinstance(r, dict) else r[0]) if r else state
        home = state or home
        for scope, lab in (("usa", "Nation"), (home, home)):
            if not scope:
                continue
            cur.execute("""SELECT rank FROM team_season WHERE span = 'season' AND scope = %s
                           AND school = %s AND pool = %s AND sport = %s AND year = %s
                           ORDER BY rank LIMIT 1""", (scope, school, pool, sport, year))
            r = cur.fetchone()
            if r:
                out.append((lab, int(r["rank"] if isinstance(r, dict) else r[0])))
        if not home:
            return out
        units = unitsFor(cur, school, home, sport=sport, collapse=False)
        for u in units:
            kind = u["kind"]
            if kind not in ("state_div", "section", "section_div", "area", "league",
                            "division", "conference", "region"):
                continue
            wanted = {kind: [u["raw"]]}
            # a division inside its section, a state division inside its state
            if kind == "section_div":
                sec = next((x["raw"] for x in units if x["kind"] == "section"), None)
                if sec:
                    wanted["section"] = [sec]
            hs = kind in ("state_div", "section", "section_div", "area", "league")
            schools = schoolsInUnits(cur, wanted, [home] if hs else None)
            if not schools or school not in schools:
                continue
            cur.execute("""SELECT school, state, ratings, n_athletes, rank, points
                           FROM team_season WHERE span = 'season' AND scope = %s
                           AND pool = %s AND sport = %s AND year = %s
                           AND school = ANY(%s)""",
                        (home if hs else "usa", pool, sport, year, schools))
            rows = [dict(x) for x in cur.fetchall()]
            raced = raceStored(rows) if rows else None
            if not raced:
                continue
            mine = next((t for t in raced if t.get("school") == school), None)
            if mine and mine.get("rank"):
                out.append((u["label"], int(mine["rank"])))
    except Exception as exc:                          # noqa: BLE001
        cur.connection.rollback()
        print(f"card: team ranks failed ({type(exc).__name__}: {exc})", flush=True)
    return out


def renderSchoolCard(d):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (CARD_W, CARD_H), DARK)
    dr = ImageDraw.Draw(img)
    M = 64
    y = _frame(d["title"], d["sub"], dr, img)
    # the team number, left; the seven, right
    dr.text((M, y), "TEAM RATING", font=_font(False, 20), fill=DARK_MUTED)
    big = f"{d['team']:.1f}" if d["team"] is not None else "-"
    dr.text((M, y + 26), big, font=_font(True, 96), fill=GOLD)
    dr.text((M, y + 150), f"mean of the top five · {d['athletes']} rated", font=_font(False, 22), fill=DARK_MUTED)
    # the team's ranks as pills, the athlete card's shape, under the number
    py_, px_ = y + 196, M
    fp = _font(True, 22)
    rows_used = 0
    for i, (lab, rk) in enumerate(d.get("ranks") or []):
        text = f"{lab} #{rk:,}"
        w = dr.textlength(text, font=fp) + 28
        if px_ + w > 440:
            px_ = M
            py_ += 48
            rows_used += 1
            if rows_used >= 4:
                break
        if i == 0:
            dr.rounded_rectangle((px_, py_, px_ + w, py_ + 40), radius=20, fill=GOLD)
            dr.text((px_ + 14, py_ + 8), text, font=fp, fill=DARK)
        else:
            dr.rounded_rectangle((px_, py_, px_ + w, py_ + 40), radius=20, outline=DARK_LINE, width=2, fill=DARK_PILL)
            dr.text((px_ + 14, py_ + 8), text, font=fp, fill="#f2f2ee")
        px_ += w + 10
    lx = 470
    fs, fr = _font(False, 20), _font(True, 26)
    row_h = 50
    for i, r in enumerate(d["top"]):
        yy = y + i * row_h - 4
        dr.text((lx, yy), f"{i + 1}", font=_font(True, 22), fill=DARK_MUTED)
        f, name = _fit(dr, r["name"], True, 26, 360, 18)
        dr.text((lx + 36, yy), name, font=f, fill="#ffffff")
        dr.text((lx + 420, yy + 4), r["grade"], font=fs, fill=DARK_MUTED)
        rt = f"{r['rating']:.1f}"
        dr.text((CARD_W - M - dr.textlength(rt, font=fr), yy), rt, font=fr, fill=GOLD if i < 5 else "#f2f2ee")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _cached(name, build):
    """Draw-or-reuse for any card: `build()` returns PNG bytes or None."""
    os.makedirs(CARD_DIR, exist_ok=True)
    path = os.path.join(CARD_DIR, name)
    try:
        if time.time() - os.path.getmtime(path) < CARD_TTL:
            return path
    except OSError:
        pass
    png = build()
    if png is None:
        return None
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)
    return path


def cachedRaceCard(cur, sport, meet_id, div_id, event_id=None):
    name = (f"race-xc-{int(meet_id)}-{int(div_id)}.png" if sport == "XC"
            else f"race-tf-{int(meet_id)}-{int(event_id)}-{int(div_id)}.png")
    def build():
        d = raceCardData(cur, sport, meet_id, div_id, event_id)
        return renderRaceCard(d) if d else None
    return _cached(name, build)


def cachedSchoolCard(cur, school, state=None):
    import hashlib
    key = hashlib.sha1(f"{school}|{state or ''}".encode("utf-8")).hexdigest()[:16]
    def build():
        d = schoolCardData(cur, school, state)
        return renderSchoolCard(d) if d else None
    return _cached(f"school-{key}.png", build)


def meetCardData(cur, meet_id):
    """The meet card: the meet's name, course and date, and the team scores
    of its biggest division (the varsity race, in practice), top ten."""
    from app import get_meet_header, get_meet_divisions, get_race_results
    from meet_compile import scoreRows
    from school_identity import schoolLabel
    header = get_meet_header(cur, meet_id)
    if not header:
        return None
    divs = [dict(d) for d in get_meet_divisions(cur, meet_id)]
    if not divs:
        return None
    divs.sort(key=lambda d: -(d.get("n_results") or 0))
    div = divs[0]
    rows = get_race_results(cur, meet_id, div["div_id"])
    if not rows:
        return None
    teams = scoreRows([dict(r) for r in rows])
    date = str(rows[0].get("date") or "")[:10]
    sub = " · ".join(x for x in [header.get("course_name") or "", date,
                                 f"{len(divs)} races" if len(divs) > 1 else ""] if x)
    winner = rows[0]
    return {"title": header.get("meet_name") or "Meet", "sub": sub,
            "division": div.get("division") or "",
            "teams": [{"school": schoolLabel(t["school"]) if t.get("school") else "",
                       "points": t.get("points")} for t in teams[:10]],
            "winner": {"name": (winner.get("name") or "Unknown").strip(),
                       "school": schoolLabel(winner.get("school")) if winner.get("school") else "",
                       "time": _clock(winner.get("time_seconds")),
                       "rating": winner.get("speed_rating")}}


def renderMeetCard(d):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (CARD_W, CARD_H), DARK)
    dr = ImageDraw.Draw(img)
    M = 64
    y = _frame(d["title"], d["sub"], dr, img)
    lab = f"TEAM SCORES · {d['division'].upper()}" if d["division"] else "TEAM SCORES"
    dr.text((M, y - 22), lab, font=_font(False, 18), fill=DARK_MUTED)
    # ten rows have to fit under a title that may take two lines: the row
    # height comes from the room left, and the type follows it
    top = y + 14
    n = max(1, len(d["teams"]))
    row_h = max(30, min(40, (CARD_H - 36 - top) // n))
    size = max(19, min(26, row_h - 12))
    fp, ft = _font(True, size), _font(True, size)
    for i, t in enumerate(d["teams"]):
        yy = top + i * row_h
        if i == 0:
            dr.rounded_rectangle((M - 16, yy - 5, 700, yy + row_h - 7), radius=10, fill=DARK_PILL)
        _rank(dr, M, yy, i, fp)
        f, sch = _fit(dr, t["school"], True, size, 440, 17)
        dr.text((M + 52, yy), sch, font=f, fill="#ffffff")
        pts = f"{t['points']}" if t.get("points") is not None else "-"
        dr.text((680 - 16 - dr.textlength(pts, font=ft), yy), pts, font=ft, fill=_medal(i) if i < 3 else "#f2f2ee")
    # the individual winner, right
    wx = 760
    dr.text((wx, y - 22), "WON BY", font=_font(False, 18), fill=DARK_MUTED)
    w = d["winner"]
    f, nm = _fit(dr, w["name"], True, 34, CARD_W - M - wx, 20)
    dr.text((wx, top), nm, font=f, fill="#ffffff")
    f, sc = _fit(dr, w["school"], False, 22, CARD_W - M - wx, 16)
    dr.text((wx, top + 46), sc, font=f, fill=DARK_MUTED)
    dr.text((wx, top + 88), w["time"], font=_font(True, 40), fill="#ffffff")
    if w.get("rating") is not None:
        dr.text((wx, top + 142), f"{float(w['rating']):.1f}", font=_font(True, 40), fill=GOLD)
        dr.text((wx, top + 192), "speed rating", font=_font(False, 20), fill=DARK_MUTED)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def cachedMeetCard(cur, meet_id):
    def build():
        d = meetCardData(cur, meet_id)
        return renderMeetCard(d) if d else None
    return _cached(f"meet-xc-{int(meet_id)}.png", build)
