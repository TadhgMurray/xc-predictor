"""social.py -- the weekly Instagram posts: images and captions written from
the boards (owner, 2026-10-13: "make insta posts for national/state
rankings and such"; the look was settled over a round of renders the same
day). Pipeline step 13g_social (racecast/build_social.py) builds them; the
owner picks them up at /account/admin/social.

    buildPosts(cur, sport, pool, state, year, weeks)  -> [post]
    nationalPost(cur, sport, pool, year, weeks)       -> post | None
    postHtml(post, assets)                            -> the 1080x1350 page
    caption(post)                                     -> the text to paste

FIVE KINDS, EACH ITS OWN LAYOUT AND COLOURS (owner: "vary things between
the different things"; "change the colors around"):

  rankings   a state's top 10: rank, name, school and class, rating, 5K
             equivalent, move against last week (owner: "the column I want
             with 5k equivalent and with moving up/down")
  top1       the state's #1: weeks at #1, rating, 5K, lead over #2
  risers     the three biggest climbs on the state board's first page
  breakout   the week's race furthest above the runner's level coming in
  national   the national top 25

★ EVERY NUMBER IS A BOARD'S. Ranks and moves are season_rank_weekly's
  (build_rank_snapshot.py, 10g2) -- the same two weeks /movers compares --
  risers and the breakout are movers.digest's lists, the 5K is
  conversions.fiveK. Nothing is computed here that a page does not show.

★ THE WEEK IS SAID, NOT A MEET (owner: "after Portage doesn't mean much,
  should say the week"): "WEEK OF OCT 10", the Friday the snapshot week
  starts on.

! COLOURS: RED AND GREEN ONLY MEAN DOWN AND UP (owner: "134.3 -> 138.2 in
  red doesn't make much sense"). Each kind's accent is a colour that never
  reads as a direction; the move columns keep green up, red down, gold NEW.
! UPRIGHT NAMES (owner: "I don't really like the italics on the athlete
  names"), no grain or scuffed letters (owner: "not a big fan of the font
  or effects"): the title's only effect is outlined copies behind it.
! PHOTOS ARE THE OWNER'S. Posts need pictures the owner may use, so the
  renderer takes them from XCP_SOCIAL_PHOTOS (a folder; a file named
  "MI_anything.jpg" is preferred for Michigan's posts) and draws a plain
  panel when there is none. No photo is fetched from anywhere.
! CAPTIONS ARE TEMPLATES THAT FIRE ON FACTS, like movers.sentences: a line
  is written only when the row it reads exists. They never tag anyone --
  the admin page lists the schools to tag, and the owner adds the
  photographer.
"""
import datetime
import hashlib
import os
import re

from markupsafe import escape

KINDS = ("rankings", "top1", "risers", "breakout", "national")
TOP_N = 10
NATIONAL_N = 25
RISERS_N = 3

POOL_WORDS = {"hs_m": "boys", "hs_f": "girls", "college_m": "men", "college_f": "women",
              "ms_m": "MS boys", "ms_f": "MS girls"}
SPORT_WORDS = {"XC": "XC", "TF": "track"}

# kind -> palette. Two light pages among five so the grid alternates.
THEMES = {
    "rankings": dict(bg="#0e1c36", fg="#ffffff", fg2="#c3cde0", fg3="#8494b3", line="#253659",
                     frame="#f4efe4", ghost="rgba(255,255,255,.14)", acc="#ff7a1a",
                     accg="rgba(255,122,26,.45)"),
    "top1": dict(bg="#efe9dc", fg="#15130f", fg2="#4a463d", fg3="#7c766a", line="#cfc6b3",
                 frame="#15130f", ghost="rgba(0,0,0,.12)", acc="#d4401f",
                 accg="rgba(212,64,31,.4)"),
    "risers": dict(bg="#0d2419", fg="#ffffff", fg2="#c4d8cb", fg3="#86a593", line="#1f3d2d",
                   frame="#eef5e6", ghost="rgba(255,255,255,.14)", acc="#c6f24e",
                   accg="rgba(198,242,78,.45)"),
    "breakout": dict(bg="#1b1030", fg="#ffffff", fg2="#cfc3e6", fg3="#9586b3", line="#35274f",
                     frame="#f1ecfa", ghost="rgba(255,255,255,.14)", acc="#3fe0c5",
                     accg="rgba(63,224,197,.45)"),
    "national": dict(bg="#f3f3f1", fg="#111214", fg2="#3d4048", fg3="#7a7e88", line="#dcdde0",
                     frame="#111214", ghost="rgba(0,0,0,.10)", acc="#1f4fd8",
                     accg="rgba(31,79,216,.35)"),
}
_LIGHT = {"top1", "national"}

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")


# ------------------------------------------------------------------ #
#  pure helpers (tests/test_social.py)
# ------------------------------------------------------------------ #

def classWord(grade, pool):
    """'12' in a high-school pool -> 'SR' (11 JR, 10 SO, 9 FR); a college
    'JR-3' -> 'JR'; anything else as gradeLabel spells it, or ''."""
    if grade is None:
        return ""
    from grade_label import gradeLabel
    g = gradeLabel(grade, pool) or ""
    if g in ("9", "10", "11", "12") and (pool or "").startswith("hs"):
        return {"9": "FR", "10": "SO", "11": "JR", "12": "SR"}[g]
    m = re.match(r"^(FR|SO|JR|SR)-\d$", g)
    if m:
        return m.group(1)
    return "" if g.startswith("Class of") else g


def weekLabel(week):
    """date -> 'WEEK OF OCT 10'."""
    if isinstance(week, str):
        week = datetime.date.fromisoformat(week[:10])
    return f"WEEK OF {week.strftime('%b').upper()} {week.day}"


def moveOf(now, prev):
    """Places gained since last week (positive = up), None for a runner who
    was not on last week's board at all."""
    if prev is None:
        return None
    return int(prev) - int(now)


def streakAtTop(week_leaders, person_id):
    """Consecutive weeks, newest first, that `person_id` led the board.
    week_leaders: [(week date, leader person_id)] newest first; a missing
    week (a gap of more than 7 days) ends the streak."""
    n, last = 0, None
    for week, pid in week_leaders:
        if pid != person_id:
            break
        if last is not None and (last - week).days > 7:
            break
        n += 1
        last = week
    return n


def pickPhoto(files, key, state=None):
    """One file name from `files` for the post `key`: a file whose name
    starts with the state's code ("MI_" / "MI-") first, else any; the same
    post always gets the same photo (a hash, not a random draw)."""
    files = sorted(f for f in files if f.lower().endswith(PHOTO_EXT))
    if not files:
        return None
    if state:
        mine = [f for f in files if re.match(rf"^{re.escape(state)}[_\-]", f, re.I)]
        files = mine or [f for f in files if not re.match(r"^[A-Z]{2}[_\-]", f)] or files
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
    return files[h % len(files)]


def _pick(options, key):
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def hashtags(post):
    sport = post["sport"]
    tail = "xc" if sport == "XC" else "track"
    generic = "#crosscountry" if sport == "XC" else "#trackandfield"
    level = "#hsxc" if sport == "XC" else "#hstrack"
    if not (post.get("pool") or "").startswith("hs"):
        level = "#ncaaxc" if sport == "XC" else "#ncaatrack"
    if post["scope"] == "US":
        return f"{level} {generic}"
    st = re.sub(r"[^a-z]", "", (post.get("scope_name") or "").lower())
    return f"#{st}{tail} {level} {generic}"


def caption(post):
    """The text to paste under the image: the fact first (Instagram cuts a
    caption at about 125 characters), two to four short lines, a question,
    the link, hashtags. Each line only when its fact is in the post."""
    k, key = post["kind"], f"{post['week']}|{post['scope']}|{post['pool']}|{post['kind']}"
    where = post.get("scope_name") or "the country"
    lines = []
    if k == "rankings":
        rows = post["rows"]
        up = sorted((r for r in rows if (r.get("move") or 0) > 0), key=lambda r: -r["move"])
        new = [r for r in rows if r.get("prev") is None or r["prev"] > TOP_N]
        if up and up[0]["move"] >= 2:
            r = up[0]
            lines.append(f"{r['name']} jumps {r['move']} spots to #{r['rank']}.")
        elif new:
            r = new[0]
            lines.append(f"{r['name']} enters the top {TOP_N} at #{r['rank']}.")
        top = rows[0] if rows else None
        if top:
            streak = post.get("streak") or 0
            if streak >= 2:
                s = f"{top['name']} holds #1 for a {_ordinalWord(streak)} straight week"
            elif rows[0].get("move") not in (0, None) or streak == 1:
                s = f"New #1: {top['name']}"
            else:
                s = f"{top['name']} stays #1"
            if len(rows) > 1 and rows[0].get("rating") and rows[1].get("rating"):
                gap = rows[0]["rating"] - rows[1]["rating"]
                if gap < 1.0:
                    s += f", but {rows[1]['name']} is within a point"
            lines.append(s + ".")
        lines.append("Top 50 for every state → link in bio")
        lines.append(_pick(["Who's too low?", "Who's missing?", "Who moves next week?"], key))
    elif k == "top1":
        r, streak = post["row"], post.get("streak") or 0
        if streak >= 2:
            lines.append(_pick([f"{_plural(streak, 'week')} at #1 in {where}.",
                                f"{_plural(streak, 'week')} and nobody's caught {r['name'].split()[0]}."],
                               key))
        else:
            lines.append(f"New #1 in {where}: {r['name']}.")
        detail = f"{r['name']}, {r['rating']:.1f}"
        if r.get("five_k"):
            detail += f", the equivalent of {r['five_k']} for a flat {r.get('five_k_dist') or '5K'}"
        lines.append(detail + ".")
        if post.get("lead") is not None and post.get("second"):
            lines.append(f"{post['second']} is {post['lead']:.1f} back.")
        lines.append(_pick(["Who takes it before state?", "Anyone catching that?"], key))
    elif k == "risers":
        rs = post["rows"]
        if rs:
            lines.append(f"Biggest climbs in {where} this week.")
            lines.append(", ".join(f"{r['name'].split()[-1]} +{r['change']}" for r in rs) + ".")
            r = rs[0]
            lines.append(f"{r['name']} goes from #{r['prev']} to #{r['now']}.")
        lines.append("Who's next?")
    elif k == "breakout":
        b = post["row"]
        at = f" at {b['meet_name']}" if b.get("meet_name") else ""
        lines.append(f"{b['name']} ran {b['vs_level']:.0f}% above their level{at}.")
        s = f"{b['level']:.1f} coming in, {b['rating']:.1f} in this race"
        if b.get("time"):
            s += f" ({b['time']}"
            s += f", {b['five_k']} for a flat 5K)" if b.get("five_k") else ")"
        lines.append(s + ".")
        if post.get("sigma"):
            lines.append(f"A normal day's swing is about {post['sigma']:.0f}%.")
        lines.append("Full race → link in bio")
    elif k == "national":
        rows = post["rows"]
        states = {r["state"] for r in rows if r.get("state")}
        if rows:
            lines.append(f"National top {NATIONAL_N}, {weekLabel(post['week']).title().replace(' Of ', ' of ')}.")
        if states:
            counts = {}
            for r in rows:
                if r.get("state"):
                    counts[r["state"]] = counts.get(r["state"], 0) + 1
            st, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            lines.append(f"{len(states)} states in the top {NATIONAL_N}; {st} has {n}.")
        new = [r for r in rows if r.get("move") is None]
        if new:
            names = ", ".join(r["name"].split()[-1] for r in new[:3])
            lines.append(f"New this week: {names}.")
        lines.append("Every state's list → link in bio")
        lines.append(_pick(["Who's missing?", "Who's too low?"], key))
    lines.append(hashtags(post))
    return "\n".join(lines)


def _ordinalWord(n):
    words = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
             8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth"}
    if n in words:
        return words[n]
    from race_story import ordinal
    return ordinal(n)


def tagsFor(post):
    """The schools on the post, for the owner to tag (their accounts are
    not in the database, so the page lists names, not handles)."""
    rows = post.get("rows") or ([post["row"]] if post.get("row") else [])
    out = []
    for r in rows:
        s = r.get("school")
        if s and s not in out:
            out.append(s)
    return out


# ------------------------------------------------------------------ #
#  the image
# ------------------------------------------------------------------ #

_BASE_CSS = """
@font-face{font-family:BS;font-weight:900;src:url('{font_bs}')}
@font-face{font-family:AN;font-weight:400 700;src:url('{font_an}')}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1350px}
body{overflow:hidden;background:var(--bg);color:var(--fg);font-family:AN;position:relative}
.d{font-family:BS;font-weight:900;text-transform:uppercase}
.ghost{position:absolute;font-family:BS;font-weight:900;color:transparent;-webkit-text-stroke:2px var(--ghost);white-space:nowrap;line-height:.9;text-transform:uppercase}
.side{position:absolute;top:0;bottom:0;width:44px;display:flex;align-items:center;justify-content:center}
.side span{transform:rotate(90deg);white-space:nowrap;font:700 15px AN;letter-spacing:.5em;color:var(--fg3)}
.frame{position:absolute;border:5px solid var(--frame);background-repeat:no-repeat;background-color:var(--line);overflow:visible}
.frame .ph{position:absolute;inset:0;background-size:cover;background-position:50% 40%}
.frame .none{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;overflow:hidden;
  font-family:BS;font-weight:900;color:var(--fg3);opacity:.5;font-size:420px;line-height:.8}
.lg{position:absolute;left:50%;bottom:-34px;transform:translateX(-50%);width:200px;filter:drop-shadow(0 4px 10px rgba(0,0,0,.5))}
.tag{position:absolute;background:var(--frame);color:var(--bg);font:700 18px AN;letter-spacing:.28em;padding:8px 14px;z-index:2}
i.mv{font-family:BS;font-weight:900;font-style:normal}
.up{color:var(--upc)}.dn{color:var(--dnc)}.fl{color:var(--fg3)}.nw{color:var(--nwc)}
"""


def _vars(kind):
    t = THEMES[kind]
    light = kind in _LIGHT
    extra = {"upc": "#118a3e" if light else "#4fd27f", "dnc": "#c42b1c" if light else "#ff6b5e",
             "nwc": "#a86b00" if light else "#f2c14e"}
    return ":root{" + "".join(f"--{k}:{v};" for k, v in {**t, **extra}.items()) + "}"


def _mv(move, size=None):
    st = f' style="font-size:{size}px"' if size else ""
    if move is None:
        return f'<i class="mv nw"{st}>NEW</i>'
    if move == 0:
        return f'<i class="mv fl"{st}>–</i>'
    return (f'<i class="mv {"up" if move > 0 else "dn"}"{st}>'
            f'{"+" if move > 0 else "−"}{abs(move)}</i>')


def _frame(post, assets, style, tag_html="", logo=True):
    photo = assets.get("photo")
    inner = (f'<div class=ph style="background-image:url(\'{escape(photo)}\')"></div>' if photo
             else f'<div class=none>{escape(post["scope"])}</div>')
    lg = f'<img class=lg src="{escape(assets["logo"])}">' if logo else ""
    return f'<div class=frame style="{style}">{inner}{tag_html}{lg}</div>'


def _titleBits(post):
    scope = "NATIONAL" if post["scope"] == "US" else (post.get("scope_name") or post["scope"]).upper()
    who = POOL_WORDS.get(post["pool"], post["pool"]).upper()
    return scope, who, SPORT_WORDS.get(post["sport"], post["sport"]).upper()


def postHtml(post, assets):
    """The page a 1080x1350 screenshot of is the post. `assets`: font_bs,
    font_an, logo (URLs) and photo (URL or None)."""
    k = post["kind"]
    scope, who, sport = _titleBits(post)
    wk = weekLabel(post["week"])
    css, body = _BODY[k](post, assets, scope, who, sport, wk)
    return ("<!doctype html><html><head><meta charset=utf-8><style>" + _vars(k)
            + _BASE_CSS.replace("{font_bs}", assets["font_bs"]).replace("{font_an}", assets["font_an"]) + css + "</style></head><body>" + body + "</body></html>")


def _rankingsBody(post, assets, scope, who, sport, wk):
    css = """
.t{position:absolute;left:0;right:0;top:40px;text-align:center;font-size:230px;line-height:1}
.e1{left:14px;right:-14px;text-align:center;top:54px;font-size:230px}
.e2{left:28px;right:-28px;text-align:center;top:68px;font-size:230px}
.slash{position:absolute;left:64px;top:268px;width:250px;height:14px;background:var(--acc);transform:skewX(-20deg)}
.bar{position:absolute;left:64px;right:64px;top:300px;background:var(--frame);color:var(--bg);text-align:center;font:700 22px AN;letter-spacing:.3em;padding:7px 0;white-space:nowrap;overflow:hidden}
.lst{position:absolute;left:570px;right:64px;top:380px}
.hd{display:flex;font:700 14px AN;letter-spacing:.2em;color:var(--fg3);padding-bottom:10px;border-bottom:2px solid var(--frame)}
.hd .a{flex:1;padding-left:44px}.hd .b{width:62px;text-align:right}.hd .c{width:68px;text-align:right}.hd .x{width:56px;text-align:right}
.r{display:flex;align-items:center;height:80px;border-bottom:1px solid var(--line)}
.r b{width:44px;font-size:32px}
.n{flex:1;min-width:0;font-weight:700;font-size:25px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.n em{display:block;font-style:normal;font-weight:400;font-size:16px;color:var(--fg2);overflow:hidden;text-overflow:ellipsis}
.v{width:62px;text-align:right;font-weight:700;font-size:22px}.k{width:68px;text-align:right;font-size:20px;color:var(--fg2)}
.r i.mv{width:56px;text-align:right;font-size:24px}
.foot{position:absolute;left:570px;right:64px;bottom:96px;font-size:15px;color:var(--fg3);letter-spacing:.06em}
"""
    rows = "".join(
        f'<div class=r><b class=d>{r["rank"]}</b><span class=n>{escape(r["name"])}'
        f'<em>{escape(r.get("school") or "")}{" · " + escape(r["cls"]) if r.get("cls") else ""}</em></span>'
        f'<span class=v>{r["rating"]:.1f}</span><span class=k>{escape(r.get("five_k") or "")}</span>'
        f'{_mv(r.get("move"))}</div>' for r in post["rows"])
    dist = post.get("five_k_dist") or "5K"
    tag = (f'<div class=tag style="right:-5px;top:24px;font:900 30px BS;letter-spacing:1px">'
           f'{escape(wk.replace("WEEK OF ", ""))}</div>')
    body = (f'<div class="ghost e2">RANKINGS</div><div class="ghost e1">RANKINGS</div>'
            f'<div class="t d">RANKINGS</div><div class=slash></div>'
            f'<div class=side style="left:0"><span>RACECAST · {escape(sport)} RANKINGS</span></div>'
            f'<div class=side style="right:0"><span>{escape(scope)} {escape(who)} · {escape(wk)}</span></div>'
            f'<div class=bar>RACECAST {escape(scope)} {escape(who)} {escape(sport)} · {escape(wk)}</div>'
            + _frame(post, assets, "left:64px;top:372px;width:470px;height:880px", tag)
            + f'<div class=lst><div class=hd><span class=a>ATHLETE</span><span class=b>RTG</span>'
              f'<span class=c>{escape(dist.upper())}</span><span class=x>MOVE</span></div>{rows}</div>'
              f'<div class=foot>{escape(dist)} = flat-course {escape(dist)} equivalent · move vs. last week</div>')
    return css, body


def _top1Body(post, assets, scope, who, sport, wk):
    r = post["row"]
    first, _, last = r["name"].partition(" ")
    css = """
.big{position:absolute;left:40px;top:120px;font-size:560px;line-height:.8;color:var(--acc);text-shadow:12px 12px 0 var(--bg);z-index:1}
.gh{left:56px;top:136px;font-size:560px;-webkit-text-stroke:3px var(--accg)}
.lab{position:absolute;left:64px;top:600px;width:440px;z-index:2}
.lab .k{font:700 18px AN;letter-spacing:.3em;color:var(--acc)}
.lab h2{font-size:96px;line-height:.92;margin-top:12px;overflow-wrap:anywhere}
.lab p{font:700 24px AN;color:var(--fg2);margin-top:14px}
.stats{position:absolute;left:64px;top:950px;display:grid;grid-template-columns:auto auto;gap:24px 40px}
.stats div b{display:block;font:900 64px BS}.stats div span{font:700 15px AN;letter-spacing:.24em;color:var(--fg3)}
.note{position:absolute;left:64px;bottom:96px;width:420px;font:400 21px/1.4 AN;color:var(--fg2)}
"""
    streak = post.get("streak") or 0
    kicker = "STILL ON TOP" if streak >= 2 else "NEW NO. 1" if streak == 1 else "NO. 1"
    stats = [(f'{r["rating"]:.1f}', "RATING")]
    if r.get("five_k"):
        stats.append((r["five_k"], f'{r.get("five_k_dist") or "5K"} EQUIV.'))
    if streak:
        stats.append((str(streak), "WEEKS AT #1" if streak != 1 else "WEEK AT #1"))
    if post.get("lead") is not None:
        stats.append((f'+{post["lead"]:.1f}', "LEAD OVER #2"))
    st = "".join(f"<div><b>{escape(v)}</b><span>{escape(lab)}</span></div>" for v, lab in stats)
    tag = f'<div class=tag style="right:-5px;bottom:70px">{escape(scope)} {escape(who)} · {escape(wk)}</div>'
    body = (_frame(post, assets, "right:64px;top:64px;width:520px;height:1100px", tag)
            + f'<div class="ghost gh">#1</div><div class="big d">#1</div>'
              f'<div class=lab><div class=k>{kicker}</div><h2 class=d>{escape(first)}<br>{escape(last)}</h2>'
              f'<p>{escape(r.get("school") or "")}{" · " + escape(r["cls"]) if r.get("cls") else ""}</p></div>'
              f'<div class=stats>{st}</div>'
              f'<div class=note>Full {escape(scope.title())} top 50 at racecast.co</div>')
    return css, body


def _risersBody(post, assets, scope, who, sport, wk):
    css = """
.t{position:absolute;left:58px;top:690px;font-size:230px;line-height:.85}
.gh{left:76px;top:708px;font-size:230px;-webkit-text-stroke-color:var(--accg)}
.arrow{position:absolute;right:64px;top:712px;font:900 170px BS;color:var(--acc);line-height:.9}
.row{position:absolute;left:64px;right:64px;top:940px;display:flex;gap:28px}
.c{flex:1;min-width:0;border-top:6px solid var(--acc);padding-top:14px}
.c .num{font-size:120px;line-height:.9;color:var(--acc)}
.c b{display:block;font-size:46px;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.c span{display:block;font:700 19px AN;color:var(--fg2);margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.c .s{color:var(--fg3)}.c strong{color:var(--fg)}
"""
    cols = "".join(
        f'<div class=c><div class="num d">+{r["change"]}</div><b class=d>{escape(r["name"].split()[-1])}</b>'
        f'<span>{escape(r["name"].split()[0])} · {escape(r.get("school") or "")}</span>'
        f'<span class=s>#{r["prev"]} → <strong>#{r["now"]}</strong></span>'
        f'<span class=s>{r["rating"]:.1f}{" · " + escape(r["five_k"]) + " " + escape(r.get("five_k_dist") or "5K") if r.get("five_k") else ""}</span></div>'
        for r in post["rows"])
    tag = f'<div class=tag style="left:-5px;top:24px">{escape(scope)} {escape(who)} · {escape(wk)}</div>'
    body = (_frame(post, assets, "left:64px;right:64px;top:64px;height:560px", tag)
            + f'<div class="ghost gh">RISERS</div><div class="t d">RISERS</div><div class=arrow>▲</div>'
              f'<div class=row>{cols}</div>')
    return css, body


def _breakoutBody(post, assets, scope, who, sport, wk):
    b = post["row"]
    css = """
.k{position:absolute;left:64px;top:840px;font:700 18px AN;letter-spacing:.3em;color:var(--acc)}
.t{position:absolute;left:58px;top:866px;font-size:150px;line-height:.9;white-space:nowrap}
.gh{left:70px;top:878px;font-size:150px;-webkit-text-stroke-color:var(--accg)}
.who{position:absolute;left:64px;right:64px;top:1015px;font:700 26px AN;color:var(--fg2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ba{position:absolute;left:64px;right:64px;top:1075px;display:flex;align-items:flex-end;gap:30px;border-top:2px solid var(--line);padding-top:22px}
.ba div b{display:block;font:900 84px BS;line-height:.9}.ba div span{font:700 15px AN;letter-spacing:.24em;color:var(--fg3)}
.ba .to{font:900 70px BS;color:var(--acc);padding-bottom:22px}.ba .hl b{color:var(--acc)}
"""
    title = f'+{b["vs_level"]:.0f}% OVER LEVEL'
    who_line = " · ".join(x for x in (b["name"], " ".join(x for x in (b.get("school"), b.get("cls")) if x),
                                      b.get("meet_name")) if x)
    right = ""
    if b.get("time"):
        sub = f'TIME · {b["five_k"]} {b.get("five_k_dist") or "5K"} EQUIV.' if b.get("five_k") else "TIME"
        right = (f'<div style="margin-left:auto;text-align:right"><b>{escape(b["time"])}</b>'
                 f'<span>{escape(sub)}</span></div>')
    tag = f'<div class=tag style="right:-5px;top:24px">BREAKOUT · {escape(wk)}</div>'
    body = (_frame(post, assets, "left:64px;top:64px;width:952px;height:700px", tag)
            + f'<div class=k>{escape(scope)} {escape(who)} · {escape(sport)}</div>'
              f'<div class="ghost gh">{escape(title)}</div><div class="t d">{escape(title)}</div>'
              f'<div class=who>{escape(who_line)}</div>'
              f'<div class=ba><div><b>{b["level"]:.1f}</b><span>LEVEL COMING IN</span></div>'
              f'<div class=to>→</div><div class=hl><b>{b["rating"]:.1f}</b><span>THIS RACE</span></div>{right}</div>')
    return css, body


def _nationalBody(post, assets, scope, who, sport, wk):
    css = """
.t{position:absolute;left:400px;top:52px;font-size:150px;line-height:.85}
.gh{left:410px;top:62px;font-size:150px;-webkit-text-stroke-color:var(--accg)}
.sub{position:absolute;left:404px;top:332px;font:700 19px AN;letter-spacing:.28em;color:var(--acc)}
.cols{position:absolute;left:400px;right:64px;top:372px}
.r{display:flex;align-items:center;height:35.5px;border-bottom:1px solid var(--line);font-size:20px}
.r b{width:40px;font-size:24px;color:var(--acc)}
.n{flex:1;min-width:0;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.n em{font-style:normal;font-weight:400;color:var(--fg3);font-size:16px;margin-left:6px}
.v{width:64px;text-align:right;font-weight:700}.k{width:68px;text-align:right;color:var(--fg2)}
.r i.mv{width:52px;text-align:right;font-size:20px}
.foot{position:absolute;left:400px;bottom:56px;font-size:15px;color:var(--fg3);letter-spacing:.06em}
"""
    rows = "".join(
        f'<div class=r><b class=d>{r["rank"]}</b><span class=n>{escape(r["name"])} <em>{escape(r.get("state") or "")}</em></span>'
        f'<span class=v>{r["rating"]:.1f}</span><span class=k>{escape(r.get("five_k") or "")}</span>{_mv(r.get("move"))}</div>'
        for r in post["rows"])
    title = f"NATIONAL<br>TOP {NATIONAL_N}"
    dist = post.get("five_k_dist") or "5K"
    body = (_frame(post, assets, "left:64px;top:64px;width:300px;height:1222px")
            + f'<div class="ghost gh">{title}</div><div class="t d">{title}</div>'
              f'<div class=sub>{escape(who)} {escape(sport)} · {escape(wk)}</div>'
              f'<div class=cols>{rows}</div>'
              f'<div class=foot>rating · {escape(dist)} equivalent · move vs. last week · racecast.co</div>')
    return css, body


_BODY = {"rankings": _rankingsBody, "top1": _top1Body, "risers": _risersBody,
         "breakout": _breakoutBody, "national": _nationalBody}


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _fiveK(rating, pool, sport):
    from conversions import fiveK
    c = fiveK(rating, pool, sport) if rating is not None else None
    return (c["time"], c["dist"]) if c else (None, None)


def _clock(seconds):
    if not seconds:
        return None
    from time_format import clock_fixed
    return clock_fixed(seconds, 1)


def _board(cur, sport, pool, year, weeks, col, n, state=None):
    """Top `n` of this week's board by `col` (state_rank / nation) with last
    week's rank beside it; names, class and 5K stamped."""
    now = weeks[0]
    prev = weeks[1] if len(weeks) > 1 else None
    cur.execute(f"""
        SELECT a.person_id, a.{col} AS rank, a.school, a.state, a.mean_rating AS rating,
               b.{col} AS prev, (b.person_id IS NOT NULL) AS was_ranked
        FROM   season_rank_weekly a
        LEFT   JOIN season_rank_weekly b
               ON  b.week = %(prev)s AND b.person_id = a.person_id AND b.pool = a.pool
               AND b.sport = a.sport AND b.year = a.year
        WHERE  a.week = %(now)s AND a.sport = %(sp)s AND a.pool = %(p)s AND a.year = %(y)s
          AND  a.{col} <= %(n)s
          AND  (%(st)s::text IS NULL OR a.state = %(st)s)
        ORDER  BY a.{col}
    """, {"now": now, "prev": prev, "sp": sport, "p": pool, "y": year, "n": n, "st": state})
    rows = [dict(r) for r in cur.fetchall()]
    _stamp(cur, rows, sport, pool, year)
    for r in rows:
        if prev is None:
            r["move"] = 0
        else:
            r["move"] = moveOf(r["rank"], r["prev"])
    return rows


def _stamp(cur, rows, sport, pool, year):
    from team_tracker import _names
    names = _names(cur, {r["person_id"] for r in rows})
    grades = {}
    if rows:
        try:
            cur.execute("SAVEPOINT sg")
            cur.execute("""SELECT person_id, grade FROM athlete_season
                           WHERE person_id = ANY(%(p)s) AND sport = %(sp)s AND pool = %(pl)s
                             AND year = %(y)s""",
                        {"p": sorted({r["person_id"] for r in rows}), "sp": sport, "pl": pool,
                         "y": year})
            grades = {r["person_id"]: r["grade"] for r in cur.fetchall()}
            cur.execute("RELEASE SAVEPOINT sg")
        except Exception:                               # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT sg")
    for r in rows:
        r["name"] = names.get(r["person_id"]) or r.get("name") or "Unknown"
        r["cls"] = classWord(grades.get(r["person_id"]), pool)
        if r.get("rating") is not None:
            r["rating"] = float(r["rating"])
            r["five_k"], r["five_k_dist"] = _fiveK(r["rating"], pool, sport)


def _leaders(cur, sport, pool, year, state):
    cur.execute("""SELECT week, person_id FROM season_rank_weekly
                   WHERE sport = %s AND pool = %s AND year = %s AND state = %s AND state_rank = 1
                   ORDER BY week DESC""", (sport, pool, year, state))
    return [(r["week"], r["person_id"]) for r in cur.fetchall()]


def _base(kind, sport, pool, state, week):
    from landing import STATE_NAMES
    return {"kind": kind, "sport": sport, "pool": pool, "scope": state or "US",
            "scope_name": STATE_NAMES.get(state, state) if state else "the country",
            "week": week.isoformat() if hasattr(week, "isoformat") else str(week)}


def buildPosts(cur, sport, pool, state, year, weeks):
    """The state's posts for this week: rankings and top1 always (when the
    board has a top 10), risers when two weeks are stored and someone
    climbed, breakout when movers found a race above its runner's level."""
    import movers as M
    out = []
    rows = _board(cur, sport, pool, year, weeks, "state_rank", TOP_N, state)
    if len(rows) < TOP_N:
        return out
    leaders = _leaders(cur, sport, pool, year, state)
    streak = streakAtTop(leaders, rows[0]["person_id"])
    dist = next((r.get("five_k_dist") for r in rows if r.get("five_k_dist")), None)
    p = _base("rankings", sport, pool, state, weeks[0])
    p.update(rows=rows, streak=streak, five_k_dist=dist)
    out.append(p)
    p = _base("top1", sport, pool, state, weeks[0])
    p.update(row=rows[0], streak=streak, rows=[rows[0]],
             lead=(rows[0]["rating"] - rows[1]["rating"]) if rows[1].get("rating") else None,
             second=rows[1]["name"])
    out.append(p)
    d = M.digest(cur, sport, pool, state)
    risers = [dict(r) for r in (d.get("risers") or [])[:RISERS_N]]
    if len(weeks) > 1 and risers:
        for r in risers:
            r["rating"] = float(r["rating"]) if r.get("rating") is not None else 0.0
            r["five_k"], r["five_k_dist"] = _fiveK(r["rating"], pool, sport)
        p = _base("risers", sport, pool, state, weeks[0])
        p.update(rows=risers)
        out.append(p)
    above = (d.get("above") or [])[:1]
    if above:
        b = dict(above[0])
        b["rating"], b["level"] = float(b["rating"]), float(b["level"])
        cur.execute("""SELECT time_seconds FROM ranking_results
                       WHERE result_id = %s AND person_id = %s AND sport = %s LIMIT 1""",
                    (b.get("result_id"), b["person_id"], sport))
        t = cur.fetchone()
        b["time"] = _clock(t["time_seconds"]) if t else None
        b["five_k"], b["five_k_dist"] = _fiveK(b["rating"], pool, sport)
        cls = {r["person_id"]: r["cls"] for r in rows}
        b["cls"] = cls.get(b["person_id"], "")
        p = _base("breakout", sport, pool, state, weeks[0])
        p.update(row=b, rows=[b], sigma=d.get("sigma"))
        out.append(p)
    return out


def nationalPost(cur, sport, pool, year, weeks):
    rows = _board(cur, sport, pool, year, weeks, "nation", NATIONAL_N)
    if len(rows) < NATIONAL_N:
        return None
    p = _base("national", sport, pool, None, weeks[0])
    p.update(rows=rows, five_k_dist=next((r["five_k_dist"] for r in rows if r.get("five_k_dist")),
                                         None))
    return p


def photoFiles(folder=None):
    folder = folder or os.environ.get("XCP_SOCIAL_PHOTOS") or ""
    if not folder or not os.path.isdir(folder):
        return folder, []
    return folder, sorted(f for f in os.listdir(folder) if f.lower().endswith(PHOTO_EXT))


def postKey(post):
    return f"{post['week']}|{post['sport']}|{post['pool']}|{post['scope']}|{post['kind']}"


# ------------------------------------------------------------------ #
#  the owner's page: /account/admin/social
# ------------------------------------------------------------------ #

from flask import Blueprint, Response, abort, render_template, request, send_file  # noqa: E402

bp = Blueprint("social", __name__)


def outDir():
    return os.environ.get("XCP_SOCIAL_DIR") or "/var/lib/xc-predictor/social"


def _assetsWeb(post, photo_name):
    return {"font_bs": "/static/social/BigShouldersDisplay-900.woff2",
            "font_an": "/static/social/ArchivoNarrow.woff2", "logo": "/static/logo.svg",
            "photo": f"/account/admin/social/photo/{photo_name}" if photo_name else None}


def _db():
    import psycopg2.extras
    from database import getConn

    class _Ctx:
        def __enter__(self):
            self.cm = getConn()
            self.conn = self.cm.__enter__()
            self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return self.conn, self.cur

        def __exit__(self, *exc):
            try:
                self.conn.rollback()
            finally:
                return self.cm.__exit__(*exc)
    return _Ctx()


def _one(pid):
    with _db() as (conn, cur):
        cur.execute("SELECT * FROM social_post WHERE id = %s", (pid,))
        r = cur.fetchone()
    if not r:
        abort(404)
    return dict(r)


@bp.route("/account/admin/social")
def admin_social():
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    pool = (request.args.get("pool") or "").strip() or None
    state = (request.args.get("state") or "").strip().upper() or None
    kind = (request.args.get("kind") or "").strip() or None
    with _db() as (conn, cur):
        cur.execute("SELECT to_regclass('public.social_post') AS t")
        if cur.fetchone()["t"] is None:
            return render_template("admin_social.html", posts=[], weeks=[], week=None, unready=True,
                                   pool=pool, state=state, kind=kind, kinds=KINDS, scopes=[]), 503
        cur.execute("SELECT DISTINCT week FROM social_post ORDER BY week DESC LIMIT 8")
        weeks = [r["week"].isoformat() for r in cur.fetchall()]
        week = request.args.get("week") or (weeks[0] if weeks else None)
        cur.execute("""SELECT DISTINCT scope FROM social_post WHERE week = %s ORDER BY scope""",
                    (week,))
        scopes = [r["scope"] for r in cur.fetchall()]
        cur.execute("""SELECT id, week, sport, pool, scope, kind, caption, png_path, data
                       FROM social_post
                       WHERE week = %(w)s AND (%(p)s::text IS NULL OR pool = %(p)s)
                         AND (%(s)s::text IS NULL OR scope = %(s)s)
                         AND (%(k)s::text IS NULL OR kind = %(k)s)
                       ORDER BY (scope = 'US') DESC, scope, pool,
                                array_position(%(order)s::text[], kind)
                       LIMIT 300""",
                    {"w": week, "p": pool, "s": state, "k": kind, "order": list(KINDS)})
        posts = []
        for r in cur.fetchall():
            r = dict(r)
            r["has_png"] = bool(r.get("png_path") and os.path.exists(r["png_path"]))
            r["tags"] = tagsFor(r["data"])
            r["title"] = f'{r["data"].get("scope_name", r["scope"])} · {POOL_WORDS.get(r["pool"], r["pool"])} · {r["kind"]}'
            posts.append(r)
    return render_template("admin_social.html", posts=posts, weeks=weeks, week=week, unready=False,
                           pool=pool, state=state, kind=kind, kinds=KINDS, scopes=scopes,
                           pools=POOL_WORDS)


@bp.route("/account/admin/social/<int:pid>.html")
def admin_social_html(pid):
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    r = _one(pid)
    return Response(postHtml(r["data"], _assetsWeb(r["data"], r["data"].get("photo"))),
                    mimetype="text/html")


@bp.route("/account/admin/social/<int:pid>.png")
def admin_social_png(pid):
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    r = _one(pid)
    path = r.get("png_path")
    if not path or not os.path.exists(path):
        abort(404)
    name = f'racecast_{r["scope"]}_{r["pool"]}_{r["kind"]}_{r["week"]}.png'
    return send_file(path, mimetype="image/png", download_name=name,
                     as_attachment=bool(request.args.get("dl")))


@bp.route("/account/admin/social/photo/<path:name>")
def admin_social_photo(name):
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    folder, files = photoFiles()
    if name not in files:
        abort(404)
    return send_file(os.path.join(folder, name))
