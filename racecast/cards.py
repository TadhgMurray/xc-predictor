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
CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "cards")

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
        "units": [u["label"] for u in units][:4],
        "rating": (season or {}).get("mean_rating"),
        "season": (f"{label_year} {season['sport']} season" if season else ""),
        "best": agg.get("best"),
        "best_sport": best_sport,
        "races": agg.get("races") or 0,
        "seasons": agg.get("seasons") or 0,
        "ranks": ranks[:5],
    }


def renderAthleteCard(d):
    """The PNG bytes for one athlete's card."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (CARD_W, CARD_H), PAPER)
    dr = ImageDraw.Draw(img)
    M = 72
    # a thin rule under a small header line, the site's own quiet idiom
    dr.text((M, 46), "racecast.co", font=_font(True, 26), fill=MUTED)
    lab = "SPEED RATING"
    f = _font(False, 22)
    dr.text((CARD_W - M - dr.textlength(lab, font=f), 50), lab, font=f, fill=MUTED)
    dr.line((M, 92, CARD_W - M, 92), fill=LINE, width=2)

    f, name = _fit(dr, d["name"], True, 78, CARD_W - 2 * M, 40)
    dr.text((M, 118), name, font=f, fill=INK)
    sub = " · ".join(x for x in [d["school"], d["grade"]] + d["units"] if x)
    f, sub = _fit(dr, sub, False, 34, CARD_W - 2 * M, 24)
    dr.text((M, 218), sub, font=f, fill=MUTED)

    # the three numbers
    y = 300
    cols = [("RATING", f"{d['rating']:.1f}" if d["rating"] is not None else "-", d["season"]),
            ("BEST RACE", f"{d['best']:.1f}" if d["best"] is not None else "-", d["best_sport"]),
            ("RACES", f"{d['races']:,}", f"{d['seasons']} seasons")]
    x = M
    for i, (lab, val, note) in enumerate(cols):
        dr.text((x, y), lab, font=_font(False, 22), fill=MUTED)
        big = _font(True, 112 if i == 0 else 72)
        dr.text((x, y + 30 if i == 0 else y + 58), val, font=big, fill=INK)
        dr.text((x, y + 160), note, font=_font(False, 24), fill=MUTED)
        x += 420 if i == 0 else 300

    # the rank line as pills
    y = 508
    x = M
    fp = _font(True, 26)
    for r in d["ranks"]:
        w = dr.textlength(r, font=fp) + 36
        if x + w > CARD_W - M:
            break
        dr.rounded_rectangle((x, y, x + w, y + 50), radius=25, outline=LINE, width=2, fill="#ffffff")
        dr.text((x + 18, y + 10), r, font=fp, fill=INK)
        x += w + 14

    tag = "Every result on one comparable scale"
    f = _font(False, 22)
    dr.text((CARD_W - M - dr.textlength(tag, font=f), CARD_H - 52), tag, font=f, fill=MUTED)
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
