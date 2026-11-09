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

KINDS = ("rankings", "top1", "risers", "breakout", "national", "teams", "alltime",
         "picks", "teampicks", "recap", "preview", "teampreview")
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
    if scopeState(post) is None:
        return f"{level} {generic}"
    if "~" in (post.get("scope") or ""):
        from landing import STATE_NAMES
        st = re.sub(r"[^a-z]", "", STATE_NAMES.get(scopeState(post), "").lower())
        return f"#{st}{tail} {level} {generic}" if st else f"{level} {generic}"
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
    elif k == "teams":
        rows = post.get("rows") or []
        if rows:
            lines.append(f"{rows[0]['school']} leads {where}’s team board with {rows[0].get('points')} points.")
            if len(rows) > 1:
                lines.append(f"{rows[1]['school']} next at {rows[1].get('points')}.")
        lines.append("Points from 1,000 simulated meets → link in bio")
        lines.append(_pick(["Who’s too low?", "Who takes state?"], key))
    elif k == "picks":
        rows = post.get("rows") or []
        if rows:
            r = rows[0]
            lines.append(f"{r['name']} is the pick in {where} {post.get('division_label') or ''}, "
                         f"{round(100 * r['p_win'])}% to win.".replace("  ", " "))
            if len(rows) > 1:
                lines.append(f"{rows[1]['name']} next at {round(100 * rows[1]['p_win'])}%.")
        lines.append("Every runner's odds → link in bio")
        lines.append(_pick(["Who are you taking?", "Who's the upset pick?"], key))
    elif k == "teampicks":
        rows = post.get("rows") or []
        if rows:
            lines.append(f"{rows[0]['school']} is the team to beat in {where}, "
                         f"{round(100 * rows[0]['p_win'])}% to win the title.")
        lines.append("Simulated from every runner's season → link in bio")
        lines.append("Who takes it?")
    elif k == "recap":
        rows = post.get("rows") or []
        teams = post.get("teams") or []
        if rows:
            lines.append(f"{rows[0]['name']} wins the {post.get('meet_name') or 'race'} in {rows[0].get('time')}.")
        if teams:
            lines.append(f"{teams[0]['school']} takes the team title with {teams[0]['points']} points.")
        lines.append("Every finisher rated → link in bio")
    elif k in ("preview", "teampreview"):
        rows = post.get("rows") or []
        meet = post.get("meet_short") or post.get("meet_name") or "the race"
        lab = post.get("race_label")
        r0 = rows[0] if rows else None
        if k == "preview" and r0:
            s = f"Our pick for {meet}" + (f" ({lab})" if lab else "") + f": {r0['name']}"
            if r0.get("p_win") is not None:
                s += f", {round(100 * r0['p_win'])}% to win"
            lines.append(s + ".")
            if len(rows) > 1 and rows[1].get("p_win") is not None:
                lines.append(f"{rows[1]['name']} next at {round(100 * rows[1]['p_win'])}%.")
            t = (post.get("teams") or [None])[0]
            if t:
                lines.append(f"Team favourite: {t['school']}" +
                             (f", {round(100 * t['p_win'])}%." if t.get("p_win") is not None else "."))
        elif r0:
            s = f"{r0['school']} is the team to beat at {meet}"
            if r0.get("p_win") is not None:
                s += f", {round(100 * r0['p_win'])}% to win"
            lines.append(s + ".")
            if len(rows) > 1 and rows[1].get("p_win") is not None:
                lines.append(f"{rows[1]['school']} next at {round(100 * rows[1]['p_win'])}%.")
        lines.append("The whole field predicted → link in bio")
        lines.append(_pick(["Who are you taking?", "Who's the upset pick?", "Too high? Too low?"], key))
    elif k == "alltime":
        rows = post.get("rows") or []
        if rows:
            r = rows[0]
            lines.append(f"The best race ever run in {where}: {r['name']}, {r.get('year')}.")
        lines.append("Every era on one scale → link in bio")
        lines.append("Who’s missing?")
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


def teamsPost(cur, sport, pool, state, year, week):
    """The state's top 10 teams this season (the team board, teams.py):
    points from the simulated meets, the top five's average, the best
    runner. None when the board has fewer than ten."""
    from werkzeug.datastructures import MultiDict
    import teams as T
    from pool_view import stampBoardRows
    f, err = T.parseFilters(MultiDict({k: v for k, v in {"sport": sport, "pool": pool, "state": state,
                                                         "year": str(year), "limit": "10"}.items() if v}))
    if err:
        return None
    rows = [dict(r) for r in T.getTeamRankings(cur, f)]
    if len(rows) < TOP_N:
        return None
    T.fillBestRunner(cur, rows)
    stampBoardRows(rows, rating_keys=("top5_mean",), sport=sport)
    out = []
    for i, r in enumerate(rows[:TOP_N]):
        out.append({"rank": i + 1, "name": None, "school": r["school"], "state": r.get("state") or state,
                    "points": r.get("points"), "top5": r.get("hs_top5_mean") or r.get("top5_mean"),
                    "hs_top5": r.get("hs_top5_mean"),
                    "sub": f'No. 1 {r["best_name"]}' if r.get("best_name") else "", "move": 0})
    try:
        cur.execute("SAVEPOINT sp_tw")
        odds = teamWinOdds(cur, pool, state)
        cur.execute("RELEASE SAVEPOINT sp_tw")
    except Exception:                                   # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT sp_tw")
        odds = {}
    for r in out:
        if r["school"] in odds:
            r["p_win"] = odds[r["school"]]
    p = _base("teams", sport, pool, state, week)
    p.update(rows=out, has_odds=bool(odds))
    return p


def alltimePost(cur, sport, pool, state, week):
    """The state's ten best performances ever, one per runner (the
    performance board, rankings.py)."""
    from werkzeug.datastructures import MultiDict
    import rankings as RK
    from pool_view import stampBoardRows
    f, err = RK.parseFilters(MultiDict({k: v for k, v in {"board": "performance", "sport": sport, "pool": pool,
                                                          "state": state, "limit": "80"}.items() if v}))
    if err:
        return None
    rows = [dict(r) for r in RK.getPerformanceRankings(cur, f)]
    stampBoardRows(rows, rating_keys=("rating",))
    seen, out = set(), []
    for r in rows:
        if r.get("person_id") in seen:
            continue
        seen.add(r.get("person_id"))
        out.append({"rank": len(out) + 1, "name": r.get("name") or "Unknown", "school": r.get("school"),
                    "state": state or r.get("state"), "rating": r.get("rating"), "hs": r.get("hs_rating"),
                    "year": str(r.get("race_date") or "")[:4], "time": _clock(r.get("time_seconds")),
                    "person_id": r.get("person_id")})
        if len(out) == TOP_N:
            break
    if len(out) < TOP_N:
        return None
    p = _base("alltime", sport, pool, state, week)
    p.update(rows=out)
    return p


GENDER_OF = {"hs_m": "boys", "hs_f": "girls"}


def _oddsYear(cur):
    cur.execute("SELECT to_regclass('public.state_odds_meta') AS t")
    if cur.fetchone()["t"] is None:
        return None
    cur.execute("SELECT max(year) AS y FROM state_odds_meta")
    y = cur.fetchone()["y"]
    return int(y) if y is not None else None


def teamWinOdds(cur, pool, state):
    """{school: p_win} from the state-meet simulation (build_state_odds.py)
    for the state's latest season, every division; {} when not built."""
    g = GENDER_OF.get(pool)
    y = _oddsYear(cur) if g else None
    if y is None:
        return {}
    cur.execute("""SELECT school, max(p_win) AS p FROM state_odds_team
                   WHERE year = %s AND state = %s AND gender = %s GROUP BY school""", (y, state, g))
    return {r["school"]: float(r["p"]) for r in cur.fetchall() if r["p"] is not None}


def picksPosts(cur, sport, pool, state, week, division=None):
    """The state meet, simulated (state_odds, the 'Who wins state?' page):
    [individual picks, team picks] for one division -- the biggest field
    unless `division` is given. [] when the odds are not built."""
    g = GENDER_OF.get(pool)
    y = _oddsYear(cur) if g else None
    if y is None:
        return []
    cur.execute("""SELECT division, division_label, draws, n_field FROM state_odds_meta
                   WHERE year = %s AND state = %s AND gender = %s
                     AND (%s::text IS NULL OR division = %s)
                   ORDER BY n_field DESC LIMIT 1""", (y, state, g, division, division))
    meta = cur.fetchone()
    if not meta:
        return []
    import state_odds as SO
    athletes, teams = SO.divisionOdds(cur, state, meta["division"], g, y)
    athletes = sorted((a for a in athletes if a.get("p_win") is not None),
                      key=lambda a: (-float(a["p_win"]), -(a.get("rating") or 0)))[:8]
    teams = sorted((t for t in teams if t.get("p_win") is not None),
                   key=lambda t: (-float(t["p_win"]), t.get("score_mean") or 9e9))[:6]
    out = []
    label = meta["division_label"] or ""
    if len(athletes) >= 5:
        rows = [{"rank": i + 1, "name": a.get("name") or "Unknown", "school": a.get("school"),
                 "state": a.get("school_state") or state, "rating": a.get("rating"), "hs": a.get("rating"),
                 "cls": classWord(a.get("grade"), pool), "p_win": float(a["p_win"]),
                 "place_mean": a.get("place_mean")} for i, a in enumerate(athletes)]
        for r in rows:
            r["five_k"], r["five_k_dist"] = _fiveK(r["rating"], pool, sport) if r["rating"] else (None, None)
        p = _base("picks", sport, pool, state, week)
        p.update(rows=rows, division=meta["division"], division_label=label, draws=meta["draws"])
        out.append(p)
    if len(teams) >= 3:
        rows = [{"rank": i + 1, "name": None, "school": t["school"], "state": t.get("school_state") or state,
                 "p_win": float(t["p_win"]), "score": t.get("score_mean")} for i, t in enumerate(teams)]
        p = _base("teampicks", sport, pool, state, week)
        p.update(rows=rows, division=meta["division"], division_label=label, draws=meta["draws"])
        out.append(p)
    return out


def recapRows(cur, source, meet_id, div_id, pool, sport):
    """(finishers top 8, teams top 5) for one race, ratings HS-equivalent."""
    import meet_recap as MR
    fin = MR.actualByDiv(cur, meet_id, source).get(int(div_id)) or []
    if len(fin) < 10:
        return [], []
    cur.execute("""SELECT result_id, person_id, time_seconds, speed_rating,
                          split_part(COALESCE(rating_pool, ''), '|', 1) AS pool, grade
                   FROM results WHERE meet_id = %s AND div_id = %s AND source = %s""",
                (meet_id, div_id, source))
    by = {}
    for r in cur.fetchall():
        by[(r["person_id"], round(float(r["time_seconds"] or 0), 1))] = r
    import pool_view as PV
    rows = []
    for i, f in enumerate(fin[:8], 1):
        x = by.get((f.get("person_id"), round(float(f["time_seconds"]), 1))) or {}
        rt = x.get("speed_rating")
        hs = PV.toHs(float(rt), x.get("pool") or pool, sport) if rt is not None else None
        rows.append({"rank": i, "name": f.get("name") or "Unknown", "school": f.get("school"),
                     "time": _clock(f["time_seconds"]), "rating": float(rt) if rt is not None else None,
                     "hs": float(hs) if hs is not None else None,
                     "cls": classWord(x.get("grade"), pool)})
    try:
        cur.execute("SAVEPOINT sp_rt")
        teams = MR.actualTeams(cur, fin, None, source)[:5]
        cur.execute("RELEASE SAVEPOINT sp_rt")
    except Exception:                                   # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT sp_rt")
        teams = []
    trows = [{"rank": i + 1, "name": None, "school": t["team"], "state": t.get("state"), "points": t["points"]}
             for i, t in enumerate(teams)]
    return rows, trows


def recapPost(cur, sport, pool, state, week, meet=None):
    """The state's biggest race of the week for this pool, recapped: a
    championship ('state' in the meet's name, not a pre-state) before any
    other. `meet` = (source, meet_id, div_id) picks one race instead."""
    import datetime as _dt
    if meet is None:
        hi = _dt.date.fromisoformat(str(week)[:10]) + _dt.timedelta(days=7)
        lo = hi - _dt.timedelta(days=10)
        cur.execute("""
            SELECT m.meet_id, m.div_id, max(m.meet_name) AS meet_name, count(*) AS n,
                   count(*) FILTER (WHERE split_part(COALESCE(r.rating_pool, ''), '|', 1) = %(p)s) AS np
            FROM   meets m JOIN results r ON r.div_id = m.div_id AND r.source = 'anet'
            WHERE  m.state = %(st)s AND m.meet_date >= %(lo)s AND m.meet_date < %(hi)s
              AND  r.time_seconds > 0 AND r.time_seconds < 90000
            GROUP  BY 1, 2 HAVING count(*) >= 40
            ORDER  BY (lower(max(m.meet_name)) ~ 'state' AND lower(max(m.meet_name)) !~ 'pre[- ]?state') DESC,
                      count(*) DESC
            LIMIT 12""", {"p": pool, "st": state, "lo": lo.isoformat(), "hi": hi.isoformat()})
        cands = [r for r in cur.fetchall() if r["np"] * 2 > r["n"]]
        if not cands:
            return None
        c = cands[0]
        meet = ("anet", c["meet_id"], c["div_id"])
    source, meet_id, div_id = meet
    from app import get_race_header
    m = dict(get_race_header(cur, meet_id, div_id, source=source) or {})
    cur.execute("SELECT max(date) AS d FROM results WHERE meet_id = %s AND div_id = %s AND source = %s",
                (meet_id, div_id, source))
    m["meet_date"] = (cur.fetchone() or {}).get("d")
    rows, teams = recapRows(cur, source, meet_id, div_id, pool, sport)
    if len(rows) < 8:
        return None
    label = m.get("division")
    p = _base("recap", sport, pool, state or m.get("state"), week)
    p.update(rows=rows, teams=teams, meet_name=m.get("meet_name"), meet_date=m.get("meet_date"),
             course=m.get("course_name"), distance=m.get("distance"), race_label=label,
             meet_id=meet_id, div_id=div_id, source=source)
    return p


# ★ ANY PREDICTED RACE (owner, 2026-11-09: "What about other predicted
#   races like NCAAs?"): a championship (NCAA, NXN, NAIA, a state final) or
#   any posted meet the site predicts, as a "who wins" post -- the runners
#   and the teams, with the win chances drawn from the model's own spread
#   (race_sim, the predictions page's simulation). The prediction is the
#   site's own: the stored forecast first (meet_forecast, what the recap
#   will score), else a championship's stored qualifier field run through
#   the model on its day (the predictions page's "this year"), else the
#   posted meet's live prediction.
PREVIEW_DAYS = 8
PREVIEW_RUNNERS = 8
PREVIEW_TEAMS = 6
PREVIEW_MIN_FIELD = 25


def scopeState(post):
    """The post's state (a two-letter code), or None for a national one. A
    race preview's scope is "<state or US>~<meet>~<race>" (one post per race)."""
    st = post.get("state")
    if st:
        return st
    sc = (post.get("scope") or "").split("~")[0]
    return sc if sc and sc != "US" else None


def shortMeetName(name):
    """A meet's name for a title: no year, the long words shortened. Pure."""
    n = re.sub(r"\b(19|20)\d{2}(-\d{2,4})?\b", " ", name or "")
    for a, b in ((r"\bcross[- ]country\b", "XC"), (r"\bchampionships\b", "Championship"),
                 (r"\bchamps\b", "Championship"), (r"\bdivision iii\b", "DIII"),
                 (r"\bdivision ii\b", "DII"), (r"\bdivision i\b", "DI"),
                 (r"\binvitational\b", "Invite"), (r"\bhigh school\b", "HS")):
        n = re.sub(a, b, n, flags=re.I)
    n = re.sub(r"\(.*?\)", " ", n)
    return re.sub(r"\s+", " ", n).strip(" -–·,")


def winChances(runners, draws=4000):
    """({person_id: P(wins the race)}, {school: P(team title)}) from each
    runner's predicted time and spread (race_sim.sigmaFromPred: the model's
    own sigma). Empty dicts when nothing can be drawn."""
    import numpy as np
    import race_sim as RS
    rows = [r for r in runners if r.get("seconds") and RS.sigmaFromPred(r) is not None]
    ind, teams = {}, {}
    if len(rows) >= 2:
        mu = np.log(np.array([float(r["seconds"]) for r in rows]))
        sig = np.array([RS.sigmaFromPred(r) for r in rows])
        rng = np.random.default_rng(0)
        won = np.zeros(len(rows))
        left = draws
        while left > 0:                     # in slices, so a 400-runner field stays small
            n = min(left, 1000)
            t = mu + sig * rng.standard_normal((n, len(rows)))
            won += np.bincount(t.argmin(axis=1), minlength=len(rows))
            left -= n
        ind = {rows[i].get("person_id"): float(won[i] / draws) for i in range(len(rows))}
    try:
        field = [{"school": r.get("school"), "person_id": r.get("person_id")} for r in runners]
        res = RS.simulate(field, runners, draws=2000) if runners else None
        teams = {n: float(v["p_win"]) for n, v in ((res or {}).get("teams") or {}).items()}
    except Exception as exc:                            # noqa: BLE001
        print(f"[social] team chances: {type(exc).__name__}: {exc}", flush=True)
    return ind, teams


def _poolOf(runners):
    import collections
    c = collections.Counter((r.get("pool") or "").split("|")[0] for r in runners if r.get("pool"))
    return c.most_common(1)[0][0] if c else None


def racePredictionFor(cur, source, meet_id, div_id, day=None):
    """(prediction, meta) for one race not yet run, or (None, None). meta:
    meet_name, meet_date, state, venue, race_label, distance."""
    import meet_forecast as MF
    import upcoming_preview as U
    pred = MF.loadRace(cur, meet_id, div_id, source)
    meta = {}
    if pred:
        row = next((r for r in MF.loadMeet(cur, meet_id, source) if int(r["div_id"]) == int(div_id)), {})
        meta = {k: row.get(k) for k in ("meet_name", "meet_date", "state", "venue", "race_label", "distance")}
        return pred, meta
    plan = U.pastPlan(cur, meet_id, source, day) if day else None
    if plan:
        # a championship a year on: its qualifiers (the nightly's stored
        # field), raced on the day by the model, as the predictions page does
        race = next((r for r in plan["races"] if int(r["div_id"]) == int(div_id)), None)
        field, _info = U.qualifiedFieldInfo(cur, plan, div_id)
        if race is None or not field:
            return None, None
        from app import _withSource
        import predict as P
        target = _withSource(cur, U._target(meet_id, div_id, source, on=day))
        target["field_ids"] = field
        with P.raceDate(day):
            out = P.predictTeam(cur, [], target)
        if not out.get("available"):
            return None, None
        state = None
        if source != "tfrrs":
            cur.execute("SELECT max(state) AS s FROM meets WHERE meet_id = %s", (int(meet_id),))
            state = (cur.fetchone() or {}).get("s")
        return U._trim(out, target), {"meet_name": plan.get("name"), "meet_date": str(day)[:10],
                                      "state": state, "venue": None, "race_label": race.get("label"),
                                      "distance": race.get("distance")}
    plan = U.meetPlan(cur, meet_id, source)
    race = next((r for r in (plan or {}).get("races") or [] if int(r["div_id"]) == int(div_id)), None)
    if race is None:
        return None, None
    pred = U.racePrediction(cur, meet_id, div_id, source)
    if not pred or not pred.get("available"):
        return None, None
    return pred, {"meet_name": plan.get("name"), "meet_date": str(plan.get("date") or "")[:10] or None,
                  "state": plan.get("state"), "venue": plan.get("venue"), "race_label": race.get("label"),
                  "distance": race.get("distance")}


def previewPosts(cur, sport, week, source, meet_id, div_id, day=None, national=None):
    """[the runners' post, the teams' post] for one predicted race ("who
    wins"), or []. `day`: a championship from last year, raced on that day.
    `national`: scope the post to the country (None: when the meet has no
    state or is a national meet by name)."""
    pred, meta = racePredictionFor(cur, source, meet_id, div_id, day)
    if not pred:
        return []
    runners = sorted((r for r in pred.get("runners") or [] if r.get("seconds")),
                     key=lambda r: (r.get("place") or 10 ** 6))
    if len(runners) < PREVIEW_MIN_FIELD:
        return []
    pool = _poolOf(runners) or "hs_m"
    import pool_view as PV
    ind, tw = winChances(runners)
    name = meta.get("meet_name") or "the meet"
    state = (meta.get("state") or "").upper() or None
    if national is None:
        national = state is None or bool(re.search(r"\bnational|\bncaa\b|\bnaia\b|\bnjcaa\b|\bnxn\b|nike cross",
                                                   name, re.I))
    if national:
        state = None
    rows = []
    for i, r in enumerate(runners[:PREVIEW_RUNNERS]):
        rt = r.get("rating")
        hs = PV.toHs(float(rt), (r.get("pool") or pool).split("|")[0], sport) if rt is not None else None
        rows.append({"rank": i + 1, "name": r.get("name") or "Unknown", "school": r.get("school"),
                     "state": r.get("school_state") or state, "cls": classWord(r.get("grade"), r.get("pool") or pool),
                     "rating": float(rt) if rt is not None else None, "hs": float(hs) if hs is not None else None,
                     "time": _clock(r["seconds"]), "p_win": ind.get(r.get("person_id"))})
    teams = [t for t in pred.get("teams") or [] if t.get("score") is not None]
    teams.sort(key=lambda t: t["score"])
    trows = [{"rank": i + 1, "name": None, "school": t.get("team"), "state": t.get("state") or state,
              "score": t["score"], "p_win": tw.get(t.get("team"))} for i, t in enumerate(teams[:PREVIEW_TEAMS])]
    base = _base("preview", sport, pool, state, week)
    base.update(scope=f"{state or 'US'}~{source}{int(meet_id)}~{int(div_id)}", state=state,
                scope_name=shortMeetName(name), meet_name=name, meet_short=shortMeetName(name),
                meet_date=meta.get("meet_date"), course=meta.get("venue"), distance=meta.get("distance"),
                race_label=meta.get("race_label"), meet_id=int(meet_id), div_id=int(div_id), source=source,
                day=day, n_field=len(runners), frozen=bool(pred.get("frozen")))
    out = []
    p = dict(base, rows=rows, teams=trows[:5])
    out.append(p)
    if len(trows) >= 3 and any(t.get("p_win") is not None for t in trows):
        out.append(dict(base, kind="teampreview", rows=trows, teams=[]))
    return out


def previewTargets(cur, today=None, days=PREVIEW_DAYS):
    """[(source, meet_id, div_id, day or None)] of the races worth a "who
    wins" post in the next `days` days: every championship on the
    predictions page (NCAA, NXN, NAIA, state finals), each race, and the
    biggest stored forecast in each state."""
    import upcoming_preview as U
    import meet_forecast as MF
    today = today or datetime.date.today()
    hi = (today + datetime.timedelta(days=days)).isoformat()
    out, seen = [], set()
    champs = U.storedChampionships(today)
    if champs is None:
        # ! no savepoint: the championship code may end the transaction
        #   itself (upcoming_preview._endTransaction)
        try:
            champs = U.upcomingChampionships(cur, today)
        except Exception as exc:                        # noqa: BLE001
            cur.connection.rollback()
            print(f"[social] championships: {type(exc).__name__}: {exc}", flush=True)
            champs = []
    for m in champs:
        if not (today.isoformat() <= (m.get("date") or "") <= hi):
            continue
        plan = U.pastPlan(cur, m["meet_id"], m["source"], m["date"])
        for r in (plan or {}).get("races") or []:
            if r.get("gender") in ("M", "F") and (m["source"], m["meet_id"], r["div_id"]) not in seen:
                seen.add((m["source"], m["meet_id"], r["div_id"]))
                out.append((m["source"], int(m["meet_id"]), int(r["div_id"]), m["date"]))
    best = {}
    for r in MF.weekRows(cur, today.isoformat(), hi):
        if (r.get("n_field") or 0) < PREVIEW_MIN_FIELD:
            continue
        champ = bool(re.search(r"state|champ|national|region|section|nxn|ncaa|naia",
                               r.get("meet_name") or "", re.I)) and not re.search(
            r"pre[- ]?(state|national)", r.get("meet_name") or "", re.I)
        key = (r.get("state") or "US", r.get("gender"))
        rank = (champ, r.get("n_field") or 0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, (r["source"], int(r["meet_id"]), int(r["div_id"]), None))
    for _rank, t in best.values():
        if (t[0], t[1], t[2]) not in seen:
            seen.add((t[0], t[1], t[2]))
            out.append(t)
    return out


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
        # a race preview's scope is "<state>~<meet>~<race>": listed by its state
        cur.execute("""SELECT DISTINCT split_part(scope, '~', 1) AS scope FROM social_post
                       WHERE week = %s ORDER BY 1""", (week,))
        scopes = [r["scope"] for r in cur.fetchall()]
        cur.execute("""SELECT id, week, sport, pool, scope, kind, caption, png_path, data
                       FROM social_post
                       WHERE week = %(w)s AND (%(p)s::text IS NULL OR pool = %(p)s)
                         AND (%(s)s::text IS NULL OR split_part(scope, '~', 1) = %(s)s)
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
    import social_compose as C
    r = _one(pid)
    spec = C.cleanSpec(r["data"], r["data"].get("spec") or C.defaultSpec(r["data"]))
    _path, url = _photoOf(spec)
    resp = Response(C.pages(r["data"], spec, C.webAssets(url))[0], mimetype="text/html")
    # ! FRAMED BY THE POSTS PAGE: the site's DENY (app._headers, setdefault)
    #   blocked the preview; our own pages may frame it, nobody else
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


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
    name = f'racecast_{r["scope"].replace("~", "_")}_{r["pool"]}_{r["kind"]}_{r["week"]}.png'
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


# ------------------------------------------------------------------ #
#  the composer: /account/admin/social/compose (owner, 2026-11-09:
#  "choose a post type, then a body type, then a picture if needed")
# ------------------------------------------------------------------ #

def _specFrom(args, data):
    import social_compose as C
    saved = dict(data.get("spec") or C.defaultSpec(data))
    for k in ("body", "photo", "file", "fmt", "color", "theme", "title"):
        v = args.get(k)
        if v is not None:
            saved[k] = v or None
    return C.cleanSpec(data, saved)


def _photoOf(spec):
    """(folder path, web url) of the spec's photo, when it is one of the
    owner's files; else (None, None)."""
    folder, files = photoFiles()
    name = spec.get("file")
    if spec.get("photo") == "none" or not name or name not in files:
        return None, None
    return os.path.join(folder, name), f"/account/admin/social/photo/{name}"


def _specQuery(spec):
    import urllib.parse
    return urllib.parse.urlencode({k: (spec.get(k) or "") for k in
                                   ("body", "photo", "file", "fmt", "color", "theme", "title")})


@bp.route("/account/admin/social/compose")
def admin_social_compose():
    import social_compose as C
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    with _db() as (conn, cur):
        cur.execute("SELECT to_regclass('public.social_post') AS t")
        if cur.fetchone()["t"] is None:
            abort(404)
        cur.execute("SELECT max(week) AS w FROM social_post")
        week = cur.fetchone()["w"]
        cur.execute("""SELECT id, kind, scope, pool, data->>'scope_name' AS scope_name,
                              data->>'race_label' AS race_label
                       FROM social_post WHERE week = %s
                       ORDER BY (scope = 'US') DESC, scope, pool, kind""", (week,))
        posts = [dict(r) for r in cur.fetchall()]
    if not posts:
        abort(404)
    kinds = [k for k in C.KIND_WORDS if any(p["kind"] == k for p in posts)]
    pid = request.args.get("pid", type=int)
    kind = request.args.get("kind")
    if pid is None or not any(p["id"] == pid for p in posts):
        kind = kind if kind in kinds else kinds[0]
        pid = next(p["id"] for p in posts if p["kind"] == kind)
    row = _one(pid)
    data = row["data"]
    kind = row["kind"]
    spec = _specFrom(request.args, data)
    folder, files = photoFiles()
    _path, url = _photoOf(spec)
    n = len(C.pages(data, spec, C.webAssets(url)))
    with _db() as (conn, cur):
        try:
            divisions = oddsDivisions(cur)
        except Exception:                                # noqa: BLE001
            divisions = []
    champs = upcomingChoices()
    of_kind = [p for p in posts if p["kind"] == kind]
    for p in of_kind:
        p["label"] = f'{"National" if p["scope"] == "US" else (p["scope_name"] or p["scope"])} · {POOL_WORDS.get(p["pool"], p["pool"])}'
        if p.get("race_label"):
            p["label"] += f' · {p["race_label"]}'
    return render_template(
        "admin_social_compose.html", pid=pid, kind=kind, kinds=kinds, kind_words=C.KIND_WORDS,
        of_kind=of_kind, spec=spec, bodies=C.bodiesFor(kind), body_words=C.BODY_WORDS,
        photos=C.PHOTOS, photo_words=C.PHOTO_WORDS, files=files, folder=folder, n_pages=n,
        q=_specQuery(spec), fmt_size=C.FORMATS[spec["fmt"]], csrf=sess["csrf"],
        themes=C.THEME_WORDS, presets=C.COLOR_PRESETS, title_words=C.TITLE_WORDS,
        saved=bool(data.get("spec")), caption=row.get("caption") or "", week=week,
        divisions=divisions, champs=champs, err=request.args.get("err"),
        make_kinds=[(k, C.KIND_WORDS[k]) for k in MAKE_KINDS], pool_words=POOL_WORDS,
        state_words=_stateChoices())


@bp.route("/account/admin/social/compose/<int:pid>/<int:page>.html")
def admin_social_compose_html(pid, page):
    import social_compose as C
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    data = _one(pid)["data"]
    spec = _specFrom(request.args, data)
    _path, url = _photoOf(spec)
    pages_ = C.pages(data, spec, C.webAssets(url))
    if not 0 <= page < len(pages_):
        abort(404)
    resp = Response(pages_[page], mimetype="text/html")
    # ! THE COMPOSER FRAMES THIS (owner, 2026-11-09: the preview was a broken
    #   page icon): app._headers sends X-Frame-Options: DENY with setdefault,
    #   so this wins; SAMEORIGIN lets only the site's own pages frame it
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@bp.route("/account/admin/social/compose/<int:pid>/<int:page>.png")
def admin_social_compose_png(pid, page):
    import tempfile
    import social_compose as C
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    row = _one(pid)
    data = row["data"]
    spec = _specFrom(request.args, data)
    path, _url = _photoOf(spec)
    pages_ = C.pages(data, spec, C.fileAssets(path))
    if not 0 <= page < len(pages_):
        abort(404)
    out = os.path.join(tempfile.mkdtemp(prefix="compose_"), "post.png")
    if not C.renderPng(pages_[page], out, spec["fmt"]):
        return Response("The image could not be rendered on the server (no browser?). "
                        "Open the preview and screenshot it instead.", status=503, mimetype="text/plain")
    name = (f'racecast_{row["scope"].replace("~", "_")}_{row["pool"]}_{row["kind"]}_{spec["body"]}'
            f'{"_story" if spec["fmt"] == "story" else ""}{"_" + str(page + 1) if len(pages_) > 1 else ""}.png')
    return send_file(out, mimetype="image/png", download_name=name, as_attachment=True)


@bp.route("/account/admin/social/compose/<int:pid>", methods=["POST"])
def admin_social_compose_save(pid):
    """Keep this design for the post: its spec goes into the post's data and
    its image is rendered again for the posts page."""
    import json as _json
    from flask import redirect
    import accounts as AC
    import social_compose as C
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    if not AC.csrfOk(sess):
        abort(400)
    row = _one(pid)
    data = dict(row["data"])
    spec = _specFrom(request.form, data)
    data["spec"] = spec
    path, _url = _photoOf(spec)
    png = row.get("png_path")
    try:
        os.makedirs(outDir(), exist_ok=True)
        target = png or os.path.join(outDir(), f'{row["week"]}_{row["sport"]}_{row["pool"]}_{row["scope"].replace("~", "_")}_{row["kind"]}.png')
        if C.renderPng(C.pages(data, spec, C.fileAssets(path))[0], target, spec["fmt"]):
            png = target
    except Exception as exc:                             # noqa: BLE001
        print(f"[compose] {pid}: {type(exc).__name__}: {exc}", flush=True)
    with _db() as (conn, cur):
        cur.execute("UPDATE social_post SET data = %s, png_path = %s WHERE id = %s",
                    (_json.dumps(data, default=str), png, pid))
        conn.commit()
    return redirect(f"/account/admin/social/compose?pid={pid}&saved=1")


def _savePost(cur, p):
    """Store a post the composer made (a recap of a race the owner named,
    picks for another division) as this week's post of its kind and scope."""
    import json as _json
    p["caption"] = caption(p)
    cur.execute("""INSERT INTO social_post (week, sport, pool, scope, kind, data, caption, png_path)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)
                   ON CONFLICT (week, sport, pool, scope, kind) DO UPDATE
                   SET data = EXCLUDED.data, caption = EXCLUDED.caption, png_path = NULL, built_at = now()
                   RETURNING id""",
                (p["week"], p["sport"], p["pool"], p["scope"], p["kind"], _json.dumps(p, default=str), p["caption"]))
    return cur.fetchone()["id"]


@bp.route("/account/admin/social/compose/new", methods=["POST"])
def admin_social_compose_new():
    """A post the pipeline did not make: a recap of any race (its link), or
    the state-meet picks for any division."""
    import re as _re
    import urllib.parse
    from flask import redirect
    import accounts as AC
    import public_ids as PI
    from uploads import requireAdmin
    sess, go = requireAdmin()
    if go:
        return go
    if not AC.csrfOk(sess):
        abort(400)
    what = request.form.get("what")
    with _db() as (conn, cur):
        cur.execute("SELECT max(week) AS w FROM social_post")
        week = cur.fetchone()["w"]
        week = week.isoformat() if week else None
        made = []
        if what == "recap":
            link = urllib.parse.urlparse((request.form.get("link") or "").strip())
            m = _re.search(r"/race/xc/([^/?#]+)/([^/?#]+)", link.path or "")
            if not m or not week:
                return redirect("/account/admin/social/compose?err=" + urllib.parse.quote("Paste a cross country race link (…/race/xc/…/…)."))

            def num(tok):
                got = PI.decode(tok)
                return got[1] if got else int(tok) if tok.lstrip("-").isdigit() else None
            meet_id, div_id = num(m.group(1)), num(m.group(2))
            src = {"1": "anet", "2": "tfrrs"}.get((urllib.parse.parse_qs(link.query).get("src") or [""])[0], "anet")
            if meet_id is None or div_id is None:
                abort(400)
            cur.execute("""SELECT split_part(COALESCE(rating_pool, ''), '|', 1) AS pool, count(*) AS n
                           FROM results WHERE meet_id = %s AND div_id = %s AND source = %s
                           GROUP BY 1 ORDER BY 2 DESC LIMIT 1""", (meet_id, div_id, src))
            pr = cur.fetchone()
            pool = (pr or {}).get("pool") or "hs_m"
            from app import get_race_header
            hdr = get_race_header(cur, meet_id, div_id, source=src) or {}
            p = recapPost(cur, "XC", pool, hdr.get("state") or "US", week, meet=(src, meet_id, div_id))
            if p:
                made.append(p)
        elif what == "preview":
            # a predictions link (?meet_id=&div_id=&date=), a meet preview
            # (/meet/preview/xc/<meet>) or a race (/race/xc/<meet>/<race>),
            # or one of the upcoming championships ("champ": src|meet|day)
            def num(tok):
                tok = (tok or "").strip()
                got = PI.decode(tok)
                return got[1] if got else int(tok) if tok.lstrip("-").isdigit() else None
            src, meet_id, div_id, day = "anet", None, None, None
            champ = (request.form.get("champ") or "").split("|")
            if len(champ) == 3:
                src, meet_id, day = ("tfrrs" if champ[0] == "2" else "anet"), num(champ[1]), champ[2]
            else:
                link = urllib.parse.urlparse((request.form.get("link") or "").strip())
                q = urllib.parse.parse_qs(link.query)
                src = {"1": "anet", "2": "tfrrs", "tfrrs": "tfrrs"}.get((q.get("src") or [""])[0], "anet")
                m = (_re.search(r"/race/xc/([^/?#]+)/([^/?#]+)", link.path or "")
                     or _re.search(r"/meet/(?:preview/)?xc/([^/?#]+)", link.path or ""))
                if m:
                    meet_id = num(m.group(1))
                    div_id = num(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
                elif q.get("meet_id"):
                    meet_id = num(q["meet_id"][0])
                    div_id = num((q.get("div_id") or [""])[0])
                    day = (q.get("date") or [None])[0]
            if meet_id is None or not week:
                return redirect("/account/admin/social/compose?err=" + urllib.parse.quote(
                    "Paste a predictions, meet preview or race link, or pick a championship."))
            import upcoming_preview as U
            if day and not _re.match(r"^\d{4}-\d{2}-\d{2}$", day):
                day = None
            plan = (U.pastPlan(cur, meet_id, src, day) if day else None) or U.meetPlan(cur, meet_id, src)
            races = [r for r in (plan or {}).get("races") or []
                     if div_id is None or int(r["div_id"]) == int(div_id)]
            if div_id is not None and not races:
                races = [{"div_id": div_id}]
            for r in races[:6]:
                # ! no savepoint: the prediction code may end the transaction
                #   itself (build_social, 2026-11-09); nothing is written yet
                try:
                    conn.rollback()
                    cur.execute("SET statement_timeout = '180s'")
                    made += previewPosts(cur, "XC", week, src, meet_id, r["div_id"], day=day)
                except Exception as exc:                 # noqa: BLE001
                    conn.rollback()
                    print(f"[compose] preview {src} {meet_id}/{r['div_id']}: {type(exc).__name__}: {exc}", flush=True)
            conn.rollback()
            cur.execute("RESET statement_timeout")     # a pooled connection: as it came
        elif what == "any":
            from landing import STATE_NAMES
            kind, pool = request.form.get("kind"), request.form.get("pool")
            st = (request.form.get("state") or "US").upper()
            if kind not in MAKE_KINDS or pool not in POOL_WORDS or (st != "US" and st not in STATE_NAMES):
                abort(400)
            try:
                cur.execute("SET statement_timeout = '120s'")
                made = makePost(cur, kind, pool, st, week)
            except Exception as exc:                     # noqa: BLE001
                conn.rollback()
                print(f"[compose] {kind} {pool} {st}: {type(exc).__name__}: {exc}", flush=True)
                made = []
            conn.rollback()
            cur.execute("RESET statement_timeout")
            if not made:
                why = {"top1": "a state", "risers": "a state", "breakout": "a state", "picks": "a state",
                       "teampicks": "a state", "recap": "a state"}.get(kind)
                return redirect("/account/admin/social/compose?err=" + urllib.parse.quote(
                    f"Nothing to post for {KIND_WORDS_OF(kind)} · {POOL_WORDS[pool]} · {st}: "
                    + (f"this type needs {why}, or " if why and st == "US" else "")
                    + "the board has too few rows (or no odds / no big race this week)."))
        elif what == "picks":
            st, div, g = (request.form.get("state") or "").upper(), request.form.get("division"), request.form.get("gender")
            pool = {"boys": "hs_m", "girls": "hs_f"}.get(g)
            if pool and st and week:
                made = picksPosts(cur, "XC", pool, st, week, division=div)
        if not made:
            return redirect("/account/admin/social/compose?err=" + urllib.parse.quote(
                "Nothing to post for that: too few runners, no prediction yet, or no odds built."))
        ids = [_savePost(cur, p) for p in made]
        conn.commit()
    return redirect(f"/account/admin/social/compose?pid={ids[0]}")


def oddsDivisions(cur):
    """[(state, division, label, gender)] the state-meet odds cover, latest season."""
    y = _oddsYear(cur)
    if y is None:
        return []
    cur.execute("""SELECT state, division, division_label, gender FROM state_odds_meta
                   WHERE year = %s AND n_field > 0 ORDER BY state, division, gender""", (y,))
    return [(r["state"], r["division"], r["division_label"] or r["division"], r["gender"]) for r in cur.fetchall()]


def upcomingChoices():
    """[(value, label)] of the upcoming championships the predictions page
    lists (the nightly's stored list), for the composer's preview form."""
    import public_ids as PI
    import upcoming_preview as U
    out = []
    for m in U.storedChampionships() or []:
        tok = PI.encode("m", int(m["meet_id"])) if PI._key() is not None else str(m["meet_id"])
        title = re.sub(r"\s+", " ", re.sub(r"\b(19|20)\d{2}(-\d{2,4})?\b", " ", m.get("name") or "")).strip(" -")
        # the feed as "1" / "2", as every link names it (CLAUDE.md: no feed names)
        out.append((f'{"2" if m["source"] == "tfrrs" else "1"}|{tok}|{m["date"]}', f'{m["date"]} · {title}'))
    return out


# ★ ANY POST, ANY LEVEL, ANY PLACE (owner, 2026-11-09: "why can I only make
#   hs ones? I should be able to compose anything"): the composer builds a
#   post of any type for any pool -- high school, college, middle school --
#   and any state or the whole country, on the spot, from the same builders
#   the pipeline uses.
MAKE_KINDS = ("rankings", "national", "top1", "risers", "breakout", "teams", "alltime",
              "picks", "teampicks", "recap")


def latestWeeks(cur, sport, pool, year):
    cur.execute("""SELECT DISTINCT week FROM season_rank_weekly
                   WHERE sport = %s AND pool = %s AND year = %s
                   ORDER BY week DESC LIMIT 2""", (sport, pool, year))
    return [r["week"] for r in cur.fetchall()]


def makePost(cur, kind, pool, state, week, sport="XC"):
    """[posts] of one type for one pool and one state (None: the country),
    or [] with nothing to show. Each is filed under `week` (the composer's
    and the run's week), whatever its own board's latest week is."""
    out = _makePost(cur, kind, pool, state, week, sport)
    wk = week.isoformat() if hasattr(week, "isoformat") else (str(week) if week else None)
    for p in out:
        if wk:
            p["week"] = wk
    return out


def _makePost(cur, kind, pool, state, week, sport):
    from predict import _currentSeason
    year = int(_currentSeason(cur, sport))
    weeks = latestWeeks(cur, sport, pool, year)
    wk = weeks[0] if weeks else week
    state = (state or "").upper() or None
    if state == "US":
        state = None
    if kind in ("national",) or (kind == "rankings" and state is None):
        p = nationalPost(cur, sport, pool, year, weeks) if weeks else None
        return [p] if p else []
    if kind in ("rankings", "top1", "risers", "breakout"):
        if not weeks or state is None:
            return []
        return [p for p in buildPosts(cur, sport, pool, state, year, weeks) if p["kind"] == kind]
    if kind == "teams":
        p = teamsPost(cur, sport, pool, state, year, wk)
        return [p] if p else []
    if kind == "alltime":
        p = alltimePost(cur, sport, pool, state, wk)
        return [p] if p else []
    if kind in ("picks", "teampicks"):
        return [p for p in picksPosts(cur, sport, pool, state, wk) if p["kind"] == kind] if state else []
    if kind == "recap":
        p = recapPost(cur, sport, pool, state, wk) if state else None
        return [p] if p else []
    return []


def KIND_WORDS_OF(kind):
    import social_compose as C
    return C.KIND_WORDS.get(kind, kind)


def _stateChoices():
    from landing import STATE_NAMES
    return sorted(((k, v) for k, v in STATE_NAMES.items() if len(k) == 2), key=lambda kv: kv[1])
