"""
social_compose.py -- the Instagram posts, composed from four choices (owner,
2026-11-09: "choose a post type, then a body type, then a picture if needed
... a bunch of ways to create templates"):

    TYPE    what the data is: a state's top 10, the No. 1, the climbers,
            the breakout, the team board, the all-time list, the national
            top 25 -- the post the pipeline stored (social_post.data)
    BODY    how it is drawn: table, lines, podium, leader, bars, timeline,
            card, climbs, breakout (each type lists the bodies that fit it)
    PHOTO   none, or a photo across the top, down the right or the left
    FORMAT  feed (1080x1350) or story (1080x1920)

    pages(post, spec, assets) -> [html, ...]   one page, or a carousel
    defaultSpec(post)                          what the pipeline renders
    bodiesFor(kind)

★ THE SITE'S OWN LOOK (owner-approved renders, 2026-11-09): white, Inter, the
  link blue, the medal circles, the rating-tier colours, the athlete page's
  outlined table. Titles in capitals (owner: "uppercase all titles").
★ NO TWO STATES ALIKE (owner: "make multiple templates so we don't post the
  same template for every state"): the default body of a board post turns
  over by state and week (defaultSpec), and the owner can change any of it
  on /account/admin/social/compose.
★ EVERY RATING HS-EQUIVALENT (CLAUDE.md): rows are drawn from `hs` where the
  pipeline stamped it, else pool_view.toHs of the row's own rating.
"""
import hashlib
import html as _html
import math
import os
import sys

E = _html.escape

FORMATS = {"feed": (1080, 1350), "story": (1080, 1920)}
PHOTOS = ("none", "top", "right", "left")
PHOTO_WORDS = {"none": "No photo", "top": "Photo across the top", "right": "Photo on the right",
               "left": "Photo on the left"}
BODY_WORDS = {"table": "Table", "lines": "Lines", "podium": "Podium", "leader": "Leader card",
              "bars": "Bars", "timeline": "Timeline", "card": "Athlete card",
              "spotlight": "Spotlight", "climbs": "Climbs", "breakout": "Breakout card",
              "odds": "Win chances", "recap": "Results and teams", "both": "Runners and teams"}
KIND_BODIES = {
    "rankings": ("table", "lines", "podium", "leader", "bars", "timeline"),
    "national": ("table", "lines", "podium", "leader", "bars"),
    "alltime": ("table", "lines", "podium", "bars"),
    "teams": ("table", "lines", "podium", "bars"),
    "top1": ("card", "spotlight"),
    "risers": ("climbs", "table"),
    "breakout": ("breakout",),
    "picks": ("odds", "table"),
    "teampicks": ("odds", "table"),
    "recap": ("recap", "table", "podium"),
    "preview": ("both", "odds", "table", "podium"),
    "teampreview": ("odds", "table"),
}
KIND_WORDS = {"rankings": "State top 10", "national": "National top 25", "alltime": "All-time",
              "teams": "Team rankings", "top1": "No. 1", "risers": "Moving up",
              "breakout": "Breakout", "picks": "Who wins state", "teampicks": "Team title odds",
              "recap": "Meet recap", "preview": "Who wins (any race)", "teampreview": "Team race (any race)"}
WHO = {"hs_m": "Boys", "hs_f": "Girls", "college_m": "Men’s", "college_f": "Women’s",
       "ms_m": "Middle school boys", "ms_f": "Middle school girls"}
CLS = {"9": "FR", "10": "SO", "11": "JR", "12": "SR"}
MEDAL = {1: "#c99a2e", 2: "#9aa3ad", 3: "#b0703e"}
# the site's rating tiers (style.css --rt1..5), the bars' shades darkest first
TIERS = ((140, "#86360d"), (130, "#b04a14"), (120, "#d2621f"), (110, "#e47f43"), (0, "#eda06f"))
BAR_SHADES = ("#86360d", "#9a3f10", "#b04a14", "#bf5518", "#d2621f", "#da6d2e", "#e47f43",
              "#e88c56", "#eb9862", "#eda06f")


# ★ COLOURS (owner, 2026-11-09: "add colour customization"): a page theme,
#   an accent (names, the tag, carousel dots, and the bars when it is not the
#   site's blue) and whether the title takes the accent.
THEMES = {
    "white": dict(bg="#ffffff", fg="#111418", mut="#6b7280", line="#d5d9df", line2="#e1e4e8",
                  panel="#f4f5f7", zebra="#f8f9fb", faint="#f3ead2"),
    "paper": dict(bg="#f6f2ea", fg="#1b1915", mut="#6f685c", line="#d8cfbe", line2="#e4dccd",
                  panel="#ece5d7", zebra="#f1ebe0", faint="#e9dcbc"),
    "dark": dict(bg="#111418", fg="#f3f4f6", mut="#9aa3ad", line="#2f353d", line2="#262b32",
                 panel="#1b1f25", zebra="#161a1f", faint="#2a2416"),
}
THEME_WORDS = {"white": "White", "paper": "Paper", "dark": "Dark"}
SITE_BLUE = "#14477d"
COLOR_PRESETS = (("Site blue", SITE_BLUE), ("Navy", "#1d3557"), ("Maroon", "#7a1f2b"),
                 ("Forest", "#2f5d3a"), ("Orange", "#c8561c"), ("Purple", "#5b2a86"),
                 ("Gold", "#a97a12"), ("Black", "#111418"))
TITLE_WORDS = {"ink": "Title in black (white on dark)", "accent": "Title in the accent colour"}


def _hex(v):
    v = (v or "").strip()
    return v.lower() if len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:]) else None


def _mix(a, b, t):
    """Hex a mixed toward hex b by t (0..1)."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def _st(post):
    """The post's state for crests: a two-letter scope, or the state a race
    preview carries; None for a national post."""
    if post.get("state"):
        return post["state"]
    sc = (post.get("scope") or "").split("~")[0]
    return sc if sc and sc != "US" else None


def bodiesFor(kind):
    return KIND_BODIES.get(kind, ("table",))


def _h(*parts):
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest(), 16)


def defaultSpec(post):
    """The pipeline's choice: a board post's body turns over by state and
    week, so neighbours and consecutive weeks differ; the rest take their
    type's first body. No photo until the owner picks one."""
    kind = post.get("kind")
    bodies = bodiesFor(kind)
    body = bodies[0]
    if kind in ("rankings", "national", "alltime", "teams") and len(bodies) > 1:
        body = bodies[_h(post.get("scope"), post.get("pool"), post.get("week"), kind) % len(bodies)]
    return {"body": body, "photo": "none", "file": None, "fmt": "feed",
            "color": SITE_BLUE, "theme": "white", "title": "ink"}


def cleanSpec(post, spec):
    """A spec with every value one this post can take."""
    d = defaultSpec(post)
    spec = dict(spec or {})
    body = spec.get("body") if spec.get("body") in bodiesFor(post.get("kind")) else d["body"]
    photo = spec.get("photo") if spec.get("photo") in PHOTOS else "none"
    fmt = spec.get("fmt") if spec.get("fmt") in FORMATS else "feed"
    return {"body": body, "photo": photo, "file": spec.get("file") or None, "fmt": fmt,
            "color": _hex(spec.get("color")) or SITE_BLUE,
            "theme": spec.get("theme") if spec.get("theme") in THEMES else "white",
            "title": spec.get("title") if spec.get("title") in TITLE_WORDS else "ink"}


def _vars(spec):
    """The page's CSS variables for the spec's colours."""
    t = dict(THEMES[spec["theme"]])
    acc = spec["color"]
    if spec["theme"] == "dark" and acc == SITE_BLUE:
        acc = "#8db8ee"                       # the site's blue, lifted to read on dark
    t["acc"] = acc
    t["ttl"] = acc if spec["title"] == "accent" else t["fg"]
    return ":root{" + "".join(f"--{k}:{v};" for k, v in t.items()) + "}"


# ------------------------------------------------------------------ #
#  words
# ------------------------------------------------------------------ #

def _poss(name):
    name = (name or "").upper()
    return name + ("’" if name.endswith("S") else "’S")


def weekWords(week):
    import datetime
    try:
        d = datetime.date.fromisoformat(str(week)[:10])
    except ValueError:
        return ""
    return f"{d.strftime('%b')} {d.day}"


def title(post):
    k = post.get("kind")
    name = post.get("scope_name") or post.get("scope") or ""
    n = len(post.get("rows") or [])
    if k == "national":
        return f"NATIONAL TOP {n or 25}"
    if k == "rankings":
        return f"{_poss(name)} TOP {n or 10}"
    if k == "top1":
        return f"NO. 1 IN {name.upper()}"
    if k == "risers":
        return "MOVING UP"
    if k == "breakout":
        return "BREAKOUT OF THE WEEK"
    national = _st(post) is None
    if k == "teams":
        return f"NATIONAL TOP {n or 10} TEAMS" if national else f"{_poss(name)} TOP {n or 10} TEAMS"
    if k == "alltime":
        return "THE BEST EVER, NATIONWIDE" if national else f"{_poss(name)} BEST EVER"
    if k == "picks":
        return "WHO WINS STATE?"
    if k == "teampicks":
        return "THE TEAM TITLE RACE"
    if k == "recap":
        return (post.get("meet_name") or "RESULTS").upper()
    if k in ("preview", "teampreview"):
        m = (post.get("meet_short") or post.get("meet_name") or "the race").upper()
        if k == "teampreview":
            return f"{m}: TEAMS" if len(m) <= 24 else m
        return f"WHO WINS {m}?" if len(m) <= 24 else m
    return (k or "").upper()


def subtitle(post, body=None):
    sport = "cross country" if post.get("sport") == "XC" else "track"
    who = f'{WHO.get(post.get("pool"), "")} {sport}'.strip()
    wk = weekWords(post.get("week"))
    k = post.get("kind")
    tail = {"rankings": "one scale for every course", "national": "one scale for every course",
            "top1": (f'{post["streak"]} weeks on top' if (post.get("streak") or 0) >= 2 else "new No. 1"),
            "risers": "the biggest climbs on the state board",
            "breakout": "the race furthest above a runner’s level",
            "teams": "points from 1,000 simulated meets",
            "alltime": "each runner’s best race, every era on one scale"}.get(k, "")
    if body == "timeline":
        tail = "the top 10 on one flat 5K"
    if k == "alltime":
        return f"{who} · <b>{E(tail)}</b>"
    if k in ("picks", "teampicks"):
        g = "boys" if post.get("pool") == "hs_m" else "girls"
        div = post.get("division_label") or ""
        sims = f'{int(post["draws"]):,} simulated state meets' if post.get("draws") else "simulated state meets"
        what = "our picks" if k == "picks" else "lowest score wins"
        return f'{E(post.get("scope_name") or "")} {E(div)} {g} · {sims} · <b>{what}</b>'.replace("  ", " ")
    if k in ("preview", "teampreview"):
        bits = [post.get("race_label") or who, weekWords(post.get("meet_date") or post.get("week")),
                post.get("course")]
        d = _distWords(post.get("distance"))
        if d and d.lower() not in (post.get("race_label") or "").lower():
            bits.append(d)
        what = "our picks" if k == "preview" else "lowest score wins"
        return " · ".join(E(str(b)) for b in bits if b) + f" · <b>{what}</b>"
    if k == "recap":
        bits = [post.get("race_label") or who, weekWords(post.get("meet_date") or post.get("week")),
                post.get("course")]
        dist = post.get("distance")
        tail2 = f"{int(round(float(dist)))} m" if dist else ""
        return " · ".join(E(b) for b in bits if b) + (f" · <b>{E(tail2)}</b>" if tail2 else "")
    return f"{who} · week of {wk}" + (f" · <b>{E(tail)}</b>" if tail else "")


def foot(post, body=None):
    k = post.get("kind")
    left = {"teams": "racecast.co/teams", "risers": "racecast.co/breakouts", "picks": "racecast.co/projections",
            "teampicks": "racecast.co/projections", "preview": "racecast.co/predictions",
            "teampreview": "racecast.co/predictions"}.get(k, "racecast.co")
    right = {"rankings": "Wk: places moved since last week", "national": "Wk: places moved since last week",
             "teams": "Fewer points is better, as at a meet", "risers": "Places gained since last week",
             "top1": "Every state’s top 50, link in bio", "breakout": "Full race, link in bio",
             "alltime": "All-time lists for every state", "picks": "Win chance at the state meet",
             "teampicks": "Win chance and average score", "recap": "Every finisher rated, link in bio",
             "preview": "Win chance: the race run 4,000 times",
             "teampreview": "Predicted score; win chance from 2,000 simulated races"}.get(k, "")
    return (f'<div class=foot><span><b>{E(left)}</b></span><span>{E(right)}</span></div>')


# ------------------------------------------------------------------ #
#  numbers
# ------------------------------------------------------------------ #

def tier(v):
    v = v or 0
    return next(c for lo, c in TIERS if v >= lo)


def hsOf(r, pool, sport="XC", key="rating"):
    """The row's HS-equivalent number (CLAUDE.md: every rating a page shows)."""
    if r.get("hs") is not None:
        return float(r["hs"])
    v = r.get(key)
    if v is None:
        return None
    try:
        import pool_view as PV
        h = PV.toHs(float(v), pool, sport)
        return float(h) if h is not None else float(v)
    except Exception:                                   # noqa: BLE001
        return float(v)


def _cls(r):
    c = r.get("cls")
    if c:
        return c
    return CLS.get(str(r.get("grade") or ""), "")


def _sec(t):
    try:
        parts = str(t).split(":")
        return int(parts[0]) * 60 + float(parts[1]) if len(parts) == 2 else float(parts[0])
    except (ValueError, IndexError):
        return None


def _clock(s):
    if s is None:
        return ""
    return f"{int(s // 60)}:{s % 60:04.1f}"


# ------------------------------------------------------------------ #
#  the page
# ------------------------------------------------------------------ #

CSS = """
@font-face{font-family:IN;font-weight:100 900;src:url('__FONT__')}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:__W__px;height:__H__px}
body{overflow:hidden;position:relative;background:var(--bg);color:var(--fg);font-family:IN;
     -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.hdr{position:absolute;left:56px;right:56px;top:0}
.hdr img.lg{position:absolute;left:0;top:56px;height:46px}
.hdr .tag{position:absolute;right:0;top:62px;display:inline-flex;align-items:center;gap:8px;font:650 20px IN;
          color:var(--acc);background:var(--panel);padding:6px 14px;border-radius:999px}
.hdr h1{position:absolute;left:0;right:0;top:__H1__px;font:900 72px/1 IN;letter-spacing:-.01em;
        text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ttl)}
.hdr p{position:absolute;left:0;right:0;top:__P__px;font:400 27px/1.3 IN;color:var(--mut);white-space:nowrap;
       overflow:hidden;text-overflow:ellipsis}
.hdr p b{color:var(--fg);font-weight:650}
.foot{position:absolute;left:56px;right:56px;bottom:46px;display:flex;justify-content:space-between;
      font:500 20px IN;color:var(--mut)}
.foot b{color:var(--fg)}
.area{position:absolute;overflow:hidden}
.photo{position:absolute;background-size:cover;background-position:50% 40%;border-radius:12px;background-color:#e5e7eb}
.photo.empty{border:2px dashed #cbd0d6;background:#f8f9fa;display:flex;align-items:center;justify-content:center;
             font:500 24px IN;color:#9aa3ad}
/* the outlined table (the athlete page's) */
.box{border:2px solid var(--line);background:var(--bg)}
.bh{display:flex;align-items:center;height:60px;background:var(--panel);border-bottom:2px solid var(--line);padding:0 20px;
    font:600 17px IN;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}
.br{display:flex;align-items:flex-start;padding:0 20px;border-bottom:1.5px solid var(--line2);position:relative}
.br:nth-child(odd){background:var(--zebra)}.br:last-child{border-bottom:none}
.br .stripe{position:absolute;left:0;top:0;bottom:0;width:6px}
.c{flex:none}.nmc{flex:1;min-width:0}
.nmc .w{display:flex;align-items:center;min-width:0}
.nmc b{display:block;font:500 27px IN;color:var(--acc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nmc s{display:block;text-decoration:none;font:400 20px IN;color:var(--mut);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nmc s:empty{display:none}
.box:not(.ln) .br .c{padding-top:3px}
.crx{flex:none;width:46px;height:46px;margin-right:18px;display:flex;align-items:center;justify-content:center}
.crx img{max-width:46px;max-height:46px}
.num{font:400 26px IN}
.rtg{font:700 29px IN;border-bottom:2px dotted #9aa3ad;padding-bottom:1px}
.sec{font:400 23px IN;color:var(--mut)}
.chip{display:inline-block;font:700 17px IN;color:#fff;padding:5px 10px;border-radius:5px}
.chip.up{background:#4b7a34}.chip.dn{background:#b42318}.chip.nw{background:#8a6508}.chip.fl{background:none;color:#9aa3ad}
.medal{display:inline-flex;width:42px;height:42px;border-radius:50%;align-items:center;justify-content:center;font:700 21px IN;color:#fff}
.plain{display:inline-flex;width:42px;height:42px;align-items:center;font:600 22px IN}
/* lines */
.ln .bh{background:none;border-bottom:2px solid var(--fg);padding:0;height:46px;font-size:16px;color:var(--mut)}
.ln{border:none}.ln .br{padding:0;border-bottom:1px solid var(--line)}.ln .br:nth-child(odd){background:none}
.ln .br:nth-child(even){background:var(--zebra)}
/* podium */
.steps{display:flex;align-items:flex-end;gap:14px}
.stp{flex:1;text-align:center;min-width:0}
.stp img{width:88px;height:88px;object-fit:contain}
.stp b{display:block;font:650 24px/1.15 IN;color:var(--acc);margin-top:8px;overflow:hidden;text-overflow:ellipsis}
.stp s{display:block;text-decoration:none;font:400 18px IN;color:var(--mut);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stp .rv{display:block;font:800 32px IN;margin-top:6px}
.stp .blk{margin-top:12px;color:#fff;font:900 54px IN;display:flex;justify-content:center;padding-top:10px}
/* leader */
.lead{position:relative;border:2px solid var(--line);overflow:hidden;background:var(--bg)}
.lead .big1{position:absolute;right:-20px;top:-80px;font:900 330px/1 IN;color:var(--faint)}
.lead .in{position:relative;padding:22px 30px;display:flex;align-items:center;gap:26px}
.lead img{width:110px;height:110px;object-fit:contain}
.lead .k{font:700 18px IN;letter-spacing:.1em;color:#a97a12;text-transform:uppercase}
.lead b.n{display:block;font:800 48px/1.05 IN;color:var(--acc);margin-top:6px}
.lead s{display:block;text-decoration:none;font:400 23px IN;color:var(--mut);margin-top:6px}
.lead .st3{display:flex;gap:40px;margin-top:16px}
.lead .st3 div{font:400 18px IN;color:var(--mut)}.lead .st3 em{display:block;font:800 34px IN;color:var(--fg);font-style:normal}
/* bars */
.bl{display:flex;align-items:center;border-bottom:1.5px solid var(--line2)}
.bl .no{width:52px;font:700 25px IN}
.bl .track{flex:1;position:relative;height:72%}
.bl .fill{position:absolute;left:0;top:0;bottom:0;display:flex;align-items:center;padding:0 14px;gap:12px;color:#fff;border-radius:4px;overflow:hidden}
.bl .fill img{width:36px;height:36px;object-fit:contain;background:#fff;border-radius:50%;padding:3px;flex:none}
.bl .fill b{font:650 23px IN;white-space:nowrap}.bl .fill s{font:400 17px IN;text-decoration:none;opacity:.85;white-space:nowrap}
.bl .val{position:absolute;top:0;bottom:0;display:flex;align-items:center;padding-left:12px;font:800 27px IN;white-space:nowrap}
.bl .val i{font:400 19px IN;color:var(--mut);font-style:normal;margin-left:8px}
/* timeline */
.tl .line{position:absolute;left:0;right:0;height:4px;background:var(--fg)}
.tl .tick{position:absolute;width:2px;height:18px;background:#9aa3ad}
.tl .tt{position:absolute;font:500 17px IN;color:#9aa3ad;transform:translateX(-50%)}
.tl .dot{position:absolute;width:30px;height:30px;border-radius:50%;border:3px solid var(--bg);box-shadow:0 0 0 2px var(--fg);
         transform:translate(-50%,-50%);display:flex;align-items:center;justify-content:center;font:800 14px IN;color:#fff}
.tl .lab{position:absolute;transform:translateX(-50%);text-align:center;width:170px;background:var(--bg)}
.tl .lab b{display:block;font:650 19px/1.15 IN;color:var(--acc)}.tl .lab s{display:block;text-decoration:none;font:400 15px IN;color:var(--mut)}
.tl .stem{position:absolute;width:1.5px;background:#cbd0d6}
/* cards */
.card{border:2px solid var(--line);border-radius:14px;padding:34px 38px;background:var(--bg)}
.card .big{font:800 60px/1.05 IN;letter-spacing:-.02em;color:var(--acc)}
.card .sub2{font:400 26px IN;color:var(--mut);margin-top:6px}
.stats{display:flex;margin-top:28px;border-top:1.5px solid var(--line)}
.stats div{flex:1;padding-top:22px}.stats b{display:block;font:800 58px IN;letter-spacing:-.02em}.stats span{font:500 19px IN;color:var(--mut)}
.note{font:400 22px/1.5 IN;color:var(--mut);margin-top:24px}
.dots{position:absolute;bottom:100px;left:0;right:0;display:flex;justify-content:center;gap:10px}
.dots i{width:11px;height:11px;border-radius:50%;background:#cbd0d6}.dots i.on{background:var(--acc)}
"""


def _crestImg(assets, school, state, pool, size=None):
    fn = assets.get("crest")
    u = fn(school, state, pool) if fn and school else None
    if not u:
        return ""
    st = f' style="width:{size}px;height:{size}px;object-fit:contain"' if size else ""
    return f'<img src="{E(u)}"{st}>'


def _chip(m):
    if m is None:
        return '<span class="chip nw">NEW</span>'
    if m == 0:
        return '<span class="chip fl">–</span>'
    return f'<span class="chip {"up" if m > 0 else "dn"}">{"▲" if m > 0 else "▼"} {abs(m)}</span>'


def _hasMoves(post):
    return post.get("kind") in ("rankings", "national", "teams")


def _cols(post, narrow):
    """[(label, width or None, align, cell(row, i) -> html)] for the post's
    rows. The name column is None (it takes the rest)."""
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    k = post.get("kind")
    st = _st(post)
    national = k == "national"

    def rank(r, i):
        return f'<span class=num>{r.get("rank") or i + 1}</span>'

    def who(r, i):
        sub = r.get("sub")
        if sub is None:
            sub = " · ".join(x for x in (
                # a team row's name is its school: not twice
                (r.get("school") or "") if r.get("name") else "",
                (f'({r.get("state")})' if national and r.get("state") else ""),
                str(r.get("year") or "") if k == "alltime" else _cls(r)) if x)
            sub = sub.replace(" · (", " (")
        if not sub:                          # one line: level with the row's numbers
            return _teamName(post, dict(r, state=r.get("state") or st), r.get("name") or r.get("school"))
        return (f'<span class=w><span class=crx>{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool)}</span>'
                f'<span style="min-width:0"><b>{E(r.get("name") or r.get("school") or "")}</b>'
                f'<s>{E(sub)}</s></span></span>')

    def rating(r, i):
        v = hsOf(r, pool, sport)
        return f'<span class=rtg>{v:.1f}</span>' if v is not None else ""

    def fk(r, i):
        return f'<span class=sec>{E(r.get("five_k") or "")}</span>'

    def wk(r, i):
        return _chip(r.get("move"))

    def win(r, i):
        p = r.get("p_win")
        if p is None:
            return '<span class=sec>–</span>'
        return f'<span class=rtg style="color:var(--acc);border:none">{_pct(p)}</span>'

    if k == "picks":
        cols = [("Pick", 80, "left", rank), ("Athlete", None, "left", who), ("Rating", 120, "right", rating)]
        return cols + [("Win", 110, "right", win)]
    if k == "preview":
        cols = [("Pick", 80, "left", rank), ("Athlete", None, "left", who),
                ("Time", 130, "right", lambda r, i: f'<span class=rtg style="border:none">{E(r.get("time") or "")}</span>')]
        if not narrow:
            cols.append(("Rating", 110, "right", lambda r, i: (f'<span class=sec>{hsOf(r, pool, sport):.1f}</span>'
                                                              if hsOf(r, pool, sport) is not None else "")))
        return cols + [("Win", 110, "right", win)]
    if k == "teampreview":
        return [("Pick", 80, "left", rank), ("Team", None, "left", who),
                ("Score", 120, "right", lambda r, i: f'<span class=sec>{round(r["score"]) if r.get("score") else "–"}</span>'),
                ("Win", 110, "right", win)]
    if k == "teampicks":
        return [("Pick", 80, "left", rank), ("Team", None, "left", who),
                ("Avg score", 140, "right", lambda r, i: f'<span class=sec>{round(r["score"]) if r.get("score") else "–"}</span>'),
                ("Win", 110, "right", win)]
    if k == "recap":
        return [("Pl", 70, "left", rank), ("Athlete", None, "left", who),
                ("Time", 130, "right", lambda r, i: f'<span class=rtg style="border:none">{E(r.get("time") or "")}</span>'),
                ("Rating", 120, "right", lambda r, i: (f'<span class=sec>{hsOf(r, pool, sport):.1f}</span>'
                                                      if hsOf(r, pool, sport) is not None else ""))]
    if k == "teams":
        cols = [("Rank", 80, "left", rank), ("Team", None, "left", who),
                ("Points", 120, "right", lambda r, i: f'<span class=rtg>{r.get("points") or ""}</span>'),
                ("Top-5", 110, "right", lambda r, i: f'<span class=sec>{(hsOf(r, pool, sport, "top5") or 0):.1f}</span>')]
        if post.get("has_odds") or any(r.get("p_win") is not None for r in post.get("rows") or []):
            cols.append(("Win", 100, "right", win))
        if not narrow:
            cols.append(("Wk", 96, "right", wk))
        return cols
    if k == "alltime":
        return [("Rank", 80, "left", rank), ("Athlete", None, "left", who), ("Rating", 120, "right", rating)] + (
            [] if narrow else [("Time", 130, "right", lambda r, i: f'<span class=sec>{E(r.get("time") or "")}</span>')])
    cols = [("Rank", 80, "left", rank), ("Athlete", None, "left", who), ("Rating", 120, "right", rating)]
    if not narrow:
        cols.append(("5K equiv", 130, "right", fk))
    if _hasMoves(post):
        cols.append(("Wk", 96, "right", wk))
    return cols


def _pct(p):
    p = float(p) * 100
    return "&lt;1%" if p < 0.5 else (f"{p:.0f}%" if p >= 9.5 else f"{p:.1f}%".replace(".0%", "%"))


def _table(post, rows, area, lines=False, start=0, stripe=False, cols=None):
    cols = cols or _cols(post, area["narrow"])
    head = 46 if lines else 60
    n = max(len(rows), 1)
    rh = int(max(54, min(96, (area["h"] - head - 4) / n)))
    pad = max(6, int((rh - 52) / 2))

    def cell(i, h, lab=False):
        lab_, w, al, _ = cols[i]
        st = f"width:{w}px;" if w else ""
        extra = ";padding-left:64px" if (lab and w is None) else ""
        return f'<span class="{"nmc" if w is None else "c"}" style="{st}text-align:{al}{extra}">{h}</span>'
    out = '<div class="box' + (' ln' if lines else '') + '">'
    out += '<div class=bh>' + "".join(cell(i, c[0], True) for i, c in enumerate(cols)) + '</div>'
    for j, r in enumerate(rows):
        i = start + j
        cells = []
        for ci, (lab, w, al, fn) in enumerate(cols):
            h = fn(r, i)
            if lines and ci == 0:
                rk = r.get("rank") or i + 1
                h = (f'<span class=medal style="background:{MEDAL[rk]}">{rk}</span>' if rk in MEDAL
                     else f'<span class=plain>{rk}</span>')
            cells.append(cell(ci, h))
        sp = ""
        if stripe:
            sp = f'<i class=stripe style="background:{tier(hsOf(r, post.get("pool"), post.get("sport") or "XC"))}"></i>'
        out += f'<div class=br style="height:{rh}px;padding-top:{pad}px">{sp}' + "".join(cells) + '</div>'
    return out + '</div>'


def bTable(post, rows, area, start=0):
    return _table(post, rows, area, start=start, stripe=True)


def bLines(post, rows, area, start=0):
    return _table(post, rows, area, lines=True, start=start)


def bPodium(post, rows, area, start=0):
    if len(rows) < 3:
        return bTable(post, rows, area, start)
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    top = min(400, int(area["h"] * 0.42))
    steps = ""
    for i, hgt in ((1, 0.27), (0, 0.40), (2, 0.20)):
        r = rows[i]
        rk = r.get("rank") or i + 1
        v = hsOf(r, pool, sport, "top5" if post.get("kind") == "teams" else "rating")
        if post.get("kind") == "preview":
            # a pick: the predicted time, and the chance it wins
            val = (f'<span class=rv>{E(r.get("time") or "")}</span>'
                   + (f'<s>{_pct(r["p_win"])} to win</s>' if r.get("p_win") is not None else ""))
        else:
            val = f'<span class=rv>{v:.1f}</span>' if v is not None else ""
        steps += (f'<div class=stp>{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool, 88) or "<div style=height:88px></div>"}'
                  f'<b>{E(r.get("name") or r.get("school") or "")}</b><s>{E(r.get("school") or "") if r.get("name") else ""}</s>'
                  f'{val}'
                  f'<div class=blk style="height:{int(top * hgt)}px;background:{MEDAL[rk] if rk in MEDAL else "#9aa3ad"}">{rk}</div></div>')
    sub = dict(area, h=area["h"] - top - 20)
    return (f'<div class=steps style="height:{top}px">{steps}</div>'
            f'<div style="margin-top:20px">{_table(post, rows[3:], sub, start=3, stripe=True)}</div>')


def bLeader(post, rows, area, start=0):
    if not rows:
        return ""
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    r = rows[0]
    v = hsOf(r, pool, sport)
    v2 = hsOf(rows[1], pool, sport) if len(rows) > 1 else None
    where = post.get("scope_name") or "the country"
    stats = f'<div><em>{v:.1f}</em>Rating</div>' if v is not None else ""
    if r.get("five_k"):
        stats += f'<div><em>{E(r["five_k"])}</em>5K equiv</div>'
    if v is not None and v2 is not None:
        stats += f'<div><em>+{v - v2:.1f}</em>Ahead of No. 2</div>'
    card = (f'<div class=lead><span class=big1>1</span><div class=in>{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool)}'
            f'<div><div class=k>No. 1 in {E(where)}</div><b class=n>{E(r.get("name") or "")}</b>'
            f'<s>{E(r.get("school") or "")}{" · " + _cls(r) if _cls(r) else ""}</s><div class=st3>{stats}</div></div></div></div>')
    sub = dict(area, h=area["h"] - 262)
    return card + f'<div style="margin-top:22px">{_table(post, rows[1:], sub, start=1, stripe=True)}</div>'


def bBars(post, rows, area, start=0):
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    key = "top5" if post.get("kind") == "teams" else "rating"
    vals = [hsOf(r, pool, sport, key) or 0 for r in rows]
    if not vals:
        return ""
    lo, hi = min(vals) - 4, max(vals)
    rh = int(min(96, area["h"] / max(len(rows), 1)))
    out = ""
    for j, (r, v) in enumerate(zip(rows, vals)):
        w = 50 + (28 if not area["narrow"] else 18) * ((v - lo) / (hi - lo) if hi > lo else 1)
        tail = r.get("five_k") or (f'{r.get("points")} pts' if key == "top5" else "")
        out += (f'<div class=bl style="height:{rh}px"><span class=no>{r.get("rank") or start + j + 1}</span><span class=track>'
                f'<span class=fill style="width:{w:.1f}%;background:{_barShade(start + j)}">'
                f'{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool)}'
                f'<span><b>{E(r.get("name") or r.get("school") or "")}</b><br><s>{E(r.get("school") or "") if r.get("name") else ""}</s></span></span>'
                f'<span class=val style="left:{w:.1f}%">{v:.1f}<i>{E(tail)}</i></span></span></div>')
    return out


def bTimeline(post, rows, area, start=0):
    ts = [_sec(r.get("five_k")) for r in rows]
    if not rows or any(t is None for t in ts):
        return bTable(post, rows, area, start)
    W = area["w"]
    t0, t1 = min(ts) - 2, max(ts) + 2
    x = lambda t: (t - t0) / (t1 - t0) * W
    mid = int(area["h"] * 0.48)
    out = (f'<div class=tl style="position:relative;height:{area["h"]}px">'
           f'<div style="position:absolute;left:0;top:0;font:700 18px IN;letter-spacing:.1em;color:var(--mut)">← FASTER · · · SLOWER →</div>'
           f'<div class=line style="top:{mid - 2}px"></div>')
    for s in range(int(t0) + 1, int(t1) + 1):
        if s % 5 == 0:
            out += (f'<span class=tick style="left:{x(s):.0f}px;top:{mid - 9}px"></span>'
                    f'<span class=tt style="left:{x(s):.0f}px;top:{mid + 12}px">{s // 60}:{s % 60:02d}</span>')
    step = max(110, int((area["h"] / 2 - 80) / 3))
    slots = [(1, 70), (-1, 70), (1, 70 + step), (-1, 70 + step), (1, 70 + 2 * step), (-1, 70 + 2 * step), (1, 70 + 3 * step)]
    last = {}
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    for j, (r, t) in enumerate(zip(rows, ts)):
        xx = x(t)
        lx = min(max(xx, 85), W - 85)
        side, lv = next(((sd, l) for sd, l in slots if abs(last.get((sd, l), -999) - lx) > 175), slots[-1])
        last[(side, lv)] = lx
        lab = (f'<b>{E(r.get("name") or "")}</b><s>{E(r.get("five_k") or "")} · {E(r.get("school") or "")}</s>')
        if side > 0:
            out += (f'<span class=stem style="left:{xx:.0f}px;top:{mid}px;height:{lv}px"></span>'
                    f'<div class=lab style="left:{lx:.0f}px;top:{mid + lv + 4}px">{lab}</div>')
        else:
            out += (f'<span class=stem style="left:{xx:.0f}px;top:{mid - lv}px;height:{lv}px"></span>'
                    f'<div class=lab style="left:{lx:.0f}px;top:{mid - lv - 62}px">{lab}</div>')
        out += (f'<span class=dot style="left:{xx:.0f}px;top:{mid}px;background:{tier(hsOf(r, pool, sport))}">'
                f'{r.get("rank") or start + j + 1}</span>')
    return out + '</div>'


def _statCard(post, r, lead_words=None, extra_stats=(), note=None, name_size=60):
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    v = hsOf(r, pool, sport)
    stats = ""
    if v is not None:
        stats += (f'<div><b><i style="display:inline-block;width:22px;height:22px;border-radius:50%;'
                  f'background:{tier(v)};margin-right:12px;vertical-align:6px"></i>{v:.1f}</b><span>Rating</span></div>')
    if r.get("five_k"):
        stats += f'<div><b>{E(r["five_k"])}</b><span>5K on a track</span></div>'
    for val, lab, *col in extra_stats:
        c = f' style="color:{col[0]}"' if col else ""
        stats += f'<div><b{c}>{E(val)}</b><span>{E(lab)}</span></div>'
    sub = " · ".join(x for x in (r.get("school"), _cls(r), lead_words) if x)
    return (f'<div class=card><div style="display:flex;align-items:center;gap:26px">'
            f'{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool, 110)}'
            f'<div style="min-width:0"><div class=big style="font-size:{name_size}px">{E(r.get("name") or "")}</div>'
            f'<div class=sub2>{E(sub)}</div></div></div><div class=stats>{stats}</div>'
            + (f'<div class=note>{E(note)}</div>' if note else "") + '</div>')


def bCard(post, rows, area, start=0):
    r = post.get("row") or (rows[0] if rows else {})
    extra = []
    if post.get("lead") is not None:
        extra.append((f'+{float(post["lead"]):.1f}', "Ahead of No. 2"))
    if (post.get("streak") or 0) >= 2:
        extra.append((str(post["streak"]), "Weeks at No. 1"))
    note = (f'No. 2 is {post["second"]}.' if post.get("second") else None)
    return _statCard(post, r, extra_stats=extra[:1], note=note)


def bSpotlight(post, rows, area, start=0):
    r = post.get("row") or (rows[0] if rows else {})
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    v = hsOf(r, pool, sport)
    first, _, last = (r.get("name") or "").partition(" ")
    return (f'<div style="position:relative;height:{area["h"]}px">'
            f'<div style="position:absolute;right:0;top:0;opacity:.95">{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool, 220)}</div>'
            f'<div style="font:900 200px/0.85 IN;color:#c99a2e;letter-spacing:-.04em">1</div>'
            f'<div style="font:800 92px/1 IN;color:var(--acc);margin-top:26px;text-transform:uppercase">{E(first)}<br>{E(last)}</div>'
            f'<div style="font:400 30px IN;color:var(--mut);margin-top:16px">{E(r.get("school") or "")}{" · " + _cls(r) if _cls(r) else ""}</div>'
            f'<div class=stats style="position:absolute;left:0;right:0;bottom:0">'
            + (f'<div><b>{v:.1f}</b><span>Rating</span></div>' if v is not None else "")
            + (f'<div><b>{E(r.get("five_k") or "")}</b><span>5K on a track</span></div>' if r.get("five_k") else "")
            + (f'<div><b>+{float(post["lead"]):.1f}</b><span>Ahead of No. 2</span></div>' if post.get("lead") is not None else "")
            + '</div></div>')


def bClimbs(post, rows, area, start=0):
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    rh = int(min(230, area["h"] / max(len(rows), 1)))
    out = '<div class="box ln">'
    for r in rows:
        v = hsOf(r, pool, sport)
        up = r.get("change") or 0
        out += (f'<div class=br style="height:{rh}px;align-items:center">'
                f'<span style="width:150px;font:800 54px IN;color:#2f7a4a">▲ {up}</span>'
                f'<span class=crx style="width:64px;height:64px">{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool, 64)}</span>'
                f'<span class=nmc><b style="font-size:30px">{E(r.get("name") or "")}</b><s>{E(r.get("school") or "")}{" · " + _cls(r) if _cls(r) else ""}</s></span>'
                f'<span style="width:300px;text-align:right"><b style="display:block;font:700 28px IN;white-space:nowrap">'
                f'No. {r.get("prev")} → No. {r.get("now")}</b>'
                f'<s style="display:block;text-decoration:none;font:400 21px IN;color:var(--mut)">'
                + (f'{v:.1f}' if v is not None else "") + (f' · {E(r.get("five_k") or "")}' if r.get("five_k") else "")
                + '</s></span></div>')
    return out + '</div>'


def bBreakout(post, rows, area, start=0):
    b = post.get("row") or (rows[0] if rows else {})
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    lvl = hsOf({"rating": b.get("level")}, pool, sport) if b.get("level") is not None else None
    v = hsOf(b, pool, sport)
    pct = (v / lvl - 1) * 100 if (v and lvl) else None
    extra = []
    if lvl is not None:
        extra.append((f'{lvl:.1f}', "Coming in", "#6b7280"))
    if pct is not None:
        extra.append((f'+{pct:.1f}%', "Above their level", "#2f7a4a"))
    where = " · ".join(x for x in (b.get("meet_name"), b.get("time")) if x)
    note = (f'A normal day swings about {float(post["sigma"]):.0f}%.' if post.get("sigma") else None)
    card = _statCard(post, dict(b, five_k=None), lead_words=where, extra_stats=extra, note=note, name_size=54)
    return card.replace("<span>Rating</span>", "<span>This race</span>")


def _barShade(i):
    acc = SPEC.get("color") or SITE_BLUE
    if acc == SITE_BLUE:
        return BAR_SHADES[min(i, 9)]
    return _mix(acc, "#ffffff", min(i, 9) * 0.055)


def bOdds(post, rows, area, start=0):
    """Win chances as bars (the state meet, simulated)."""
    pool, sport = post.get("pool"), post.get("sport") or "XC"
    st = _st(post)
    team = post.get("kind") in ("teampicks", "teampreview")
    rh = int(min(150, (area["h"] - 50) / max(len(rows), 1)))
    top = max((r.get("p_win") or 0) for r in rows) or 1
    out = ('<div class="box ln"><div class=bh><span class=c style="width:80px">Pick</span>'
           f'<span class=nmc style="padding-left:64px">{"Team" if team else "Athlete"}</span>'
           '<span class=c style="width:220px;text-align:right">Chance to win</span></div>')
    for j, r in enumerate(rows):
        p = r.get("p_win") or 0
        if team:
            sub = (f'{"predicted" if post.get("kind") == "teampreview" else "average"} score {round(r["score"])}'
                   if r.get("score") else "")
        else:
            v = hsOf(r, pool, sport)
            sub = " · ".join(x for x in (r.get("school"), _cls(r), f"{v:.1f}" if v is not None else "") if x)
        out += (f'<div class=br style="height:{rh}px;display:block;padding-top:14px">'
                f'<div style="display:flex;align-items:center"><span class=c style="width:80px"><span class=plain>{r.get("rank") or start + j + 1}</span></span>'
                f'<span class=nmc><span class=w><span class=crx>{_crestImg(ASSETS, r.get("school"), r.get("state") or st, pool)}</span>'
                f'<span style="min-width:0"><b>{E(r.get("name") or r.get("school") or "")}</b><s>{E(sub)}</s></span></span></span>'
                f'<span class=c style="width:220px;text-align:right;font:800 40px IN;color:var(--acc)">{_pct(p)}</span></div>'
                f'<div style="margin:10px 0 0 144px;height:12px;background:var(--line2);border-radius:6px;overflow:hidden">'
                f'<i style="display:block;height:100%;width:{max(1.5, 100 * p / top):.1f}%;background:var(--acc);border-radius:6px"></i></div></div>')
    return out + '</div>'


def bRecap(post, rows, area, start=0):
    """The race: the top finishers, then the team scores."""
    teams = post.get("teams") or []
    th = 0
    if teams:
        th = 60 + 62 * len(teams) + 24
    ind = _table(post, rows, dict(area, h=area["h"] - th), start=start)
    if not teams:
        return ind
    tp = dict(post, kind="teamscore")
    cols = [("Pl", 70, "left", lambda r, i: f'<span class=num>{r.get("rank")}</span>'),
            ("Team", None, "left", lambda r, i: _teamName(post, r)),
            ("Points", 130, "right", lambda r, i: f'<span class=rtg style="border:none">{r.get("points")}</span>')]
    return ind + f'<div style="margin-top:24px">{_table(tp, teams, dict(area, h=60 + 62 * len(teams) + 4), cols=cols)}</div>'


def _distWords(dist):
    """10000 -> "10K", 4800 -> "4,800 m"; "" for none. Pure."""
    try:
        d = int(round(float(dist)))
    except (TypeError, ValueError):
        return ""
    if d <= 0:
        return ""
    return f"{d // 1000}K" if d % 1000 == 0 else f"{d:,} m"


def _teamName(post, r, label=None):
    """A crest and one line (a team's name), the line level with the row's
    numbers (it has no second line to centre against)."""
    return (f'<span class=w style="align-items:flex-start"><span class=crx>{_crestImg(ASSETS, r.get("school"), r.get("state") or _st(post), post.get("pool"))}</span>'
            f'<span style="min-width:0"><b>{E(label or r.get("school") or "")}</b></span></span>')


def bBoth(post, rows, area, start=0):
    """A predicted race: the picks with their win chances, then the teams'
    predicted scores and theirs."""
    teams = (post.get("teams") or [])[:4]
    th = (60 + 58 * len(teams) + 24) if teams else 0
    ind = _table(post, rows, dict(area, h=area["h"] - th), start=start, stripe=True)
    if not teams:
        return ind
    tp = dict(post, kind="teampreview")
    cols = [("Pick", 80, "left", lambda r, i: f'<span class=num>{r.get("rank")}</span>'),
            ("Team", None, "left", lambda r, i: _teamName(post, r)),
            ("Score", 120, "right", lambda r, i: f'<span class=sec>{round(r["score"]) if r.get("score") else "–"}</span>'),
            ("Win", 110, "right", lambda r, i: (f'<span class=rtg style="color:var(--acc);border:none">{_pct(r["p_win"])}</span>'
                                                if r.get("p_win") is not None else '<span class=sec>–</span>'))]
    return ind + f'<div style="margin-top:24px">{_table(tp, teams, dict(area, h=60 + 58 * len(teams) + 4), cols=cols)}</div>'


BODIES = {"table": bTable, "lines": bLines, "podium": bPodium, "leader": bLeader, "bars": bBars,
          "timeline": bTimeline, "card": bCard, "spotlight": bSpotlight, "climbs": bClimbs,
          "breakout": bBreakout, "odds": bOdds, "recap": bRecap, "both": bBoth}

ASSETS = {}
SPEC = {}


def _chunks(post, body):
    rows = list(post.get("rows") or [])
    if post.get("kind") == "national" and len(rows) > 10 and body in ("table", "lines", "bars"):
        return [rows[:10], rows[10:18], rows[18:25]]
    if post.get("kind") == "national" and len(rows) > 10:
        return [rows[:10], rows[10:18], rows[18:25]]
    return [rows]


def _h1Size(t, W):
    """72 px, smaller for a long title so it fits the width in capitals."""
    room = W - 112
    est = len(t) * 0.68                       # Inter Black capitals, ems per character
    return int(max(44, min(72, room / est)))


def pages(post, spec, assets):
    """The post as one page or a carousel: [html, ...]."""
    global ASSETS, SPEC
    ASSETS = assets
    spec = cleanSpec(post, spec)
    SPEC = spec
    W, H = FORMATS[spec["fmt"]]
    story = spec["fmt"] == "story"
    head_h = 330 if story else 270
    h1top, ptop = (170, 260) if story else (130, 216)
    bottom = H - (150 if story else 100)
    chunks = _chunks(post, spec["body"])
    out = []
    for pi, rows in enumerate(chunks):
        area = {"x": 56, "y": head_h + 20, "w": W - 112, "h": bottom - head_h - 20, "narrow": False}
        photo = ""
        if spec["photo"] != "none":
            ph = assets.get("photo")
            cls_ = "photo" if ph else "photo empty"
            bg = f' style="%s;background-image:url(\'{E(ph)}\')"' if ph else ' style="%s"'
            label = "" if ph else "Photo"
            if spec["photo"] == "top":
                hh = 560 if story else 290
                photo = f'<div class="{cls_}"' + bg % f"left:56px;right:56px;top:{area['y']}px;height:{hh}px" + f'>{label}</div>'
                area["y"] += hh + 20
                area["h"] -= hh + 20
            else:
                side = "right:56px" if spec["photo"] == "right" else "left:56px"
                photo = (f'<div class="{cls_}"' + bg % f"{side};width:360px;top:{area['y']}px;height:{area['h']}px"
                         + f'>{label}</div>')
                if spec["photo"] == "left":
                    area["x"] += 384
                area["w"] -= 384
                area["narrow"] = True
        start = sum(len(c) for c in chunks[:pi])
        bname = spec["body"]
        if pi and bname in ("podium", "leader", "timeline"):
            bname = "table"                     # a carousel's later pages are the list
        body = BODIES[bname](post, rows, area, start)
        if bname in ("card", "breakout", "climbs"):
            body = f'<div style="height:100%;display:flex;flex-direction:column;justify-content:center">{body}</div>'
        t = title(post) + (f" · {start + 1}–{start + len(rows)}" if pi else "")
        tag = "XC" if post.get("sport") == "XC" else "Track"
        dots = ""
        if len(chunks) > 1:
            dots = '<div class=dots>' + "".join(f'<i class="{"on" if i == pi else ""}"></i>' for i in range(len(chunks))) + '</div>'
        css = (CSS.replace("__FONT__", assets.get("font", "")).replace("__W__", str(W)).replace("__H__", str(H))
               .replace("__H1__", str(h1top)).replace("__P__", str(ptop)))
        out.append(
            "<!doctype html><html><head><meta charset=utf-8><style>" + _vars(spec) + css + "</style></head><body>"
            f'<div class=hdr><img class=lg src="{E(assets.get("logo", ""))}"><span class=tag>{E(tag)}</span>'
            f'<h1 style="font-size:{_h1Size(t, W)}px">{E(t)}</h1><p>{subtitle(post, spec["body"])}</p></div>'
            + photo
            + f'<div class=area style="left:{area["x"]}px;top:{area["y"]}px;width:{area["w"]}px;height:{area["h"]}px">{body}</div>'
            + dots + foot(post, spec["body"]) + "</body></html>")
    return out


# ------------------------------------------------------------------ #
#  assets and rendering
# ------------------------------------------------------------------ #

_ROOT = os.path.dirname(os.path.abspath(__file__))


def fileAssets(photo_path=None):
    """file:// assets for the pipeline's headless browser: nothing fetched."""
    st = os.path.join(_ROOT, "static")

    def crest(school, state, pool):
        try:
            import school_logo as SL
            from school_identity import levelOf
            p = SL.crestPath(school, state, levelOf(pool) if pool else None)
            return ("file://" + p) if p and os.path.exists(p) else None
        except Exception:                               # noqa: BLE001
            return None
    return {"font": "file://" + os.path.join(st, "social", "Inter-var.woff2"),
            "logo": "file://" + os.path.join(st, "logo.svg"),
            "photo": ("file://" + photo_path) if photo_path else None, "crest": crest}


def webAssets(photo_url=None):
    """The same page served to the owner's browser (the composer's preview)."""
    def crest(school, state, pool):
        try:
            import school_logo as SL
            from school_identity import levelOf
            return SL.crestUrl(school, state, 256, level=levelOf(pool) if pool else None)
        except Exception:                               # noqa: BLE001
            return None
    return {"font": "/static/social/Inter-var.woff2", "logo": "/static/logo.svg",
            "photo": photo_url, "crest": crest}


def renderPng(html, out_path, fmt="feed", timeout=90):
    """Screenshot one page in a child process (a browser never runs inside a
    web worker). Returns True on success."""
    import subprocess
    import tempfile
    W, H = FORMATS.get(fmt, FORMATS["feed"])
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        src = f.name
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--png", src, out_path, str(W), str(H)],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            print(f"[compose] render failed: {r.stderr[-400:]}", flush=True)
        return r.returncode == 0 and os.path.exists(out_path)
    except subprocess.TimeoutExpired:
        print("[compose] render timed out", flush=True)
        return False
    finally:
        try:
            os.remove(src)
        except OSError:
            pass


def _childPng(src, out, w, h):
    from playwright.sync_api import sync_playwright
    sys.path.insert(0, _ROOT)
    try:
        from build_social import browserPath
        exe = browserPath()
    except Exception:                                   # noqa: BLE001
        exe = None
    with sync_playwright() as p:
        kw = {"headless": True}
        if exe:
            kw["executable_path"] = exe
        b = p.chromium.launch(**kw)
        pg = b.new_page(viewport={"width": int(w), "height": int(h)})
        pg.goto("file://" + src)
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(120)
        pg.screenshot(path=out)
        b.close()


if __name__ == "__main__" and len(sys.argv) == 6 and sys.argv[1] == "--png":
    _childPng(*sys.argv[2:6])
