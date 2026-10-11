# Project: xc-predictor / racecast
# File:    alert_email.py
# Purpose: The follow digest, written like a results email from a coach
#          (owner, 2026-10-10, second pass: "should have more info, and avoid
#          AI-isms"). follow_alerts.py decides WHAT is new and looks up the
#          race around it; this lays it out as one email -- a subject, an HTML
#          body and its plain-text twin. Pure: no database, no clock.
#
# ★ FACTS IN LABELLED LINES, NOT PROSE. Per race: the meet, date, course and
#   race; Place "12th of 184"; Time; PR and Season best with the previous
#   best and how much faster; Rating with the change from the previous race;
#   the team's finish when the athlete scored for one; the next likely meet.
#   Per team meet: the team's place and score in each race, its scoring squad
#   with places and times, the PRs by name, the next likely meet. No
#   adjectives, no em dashes, nothing a results sheet would not say.
#
# ★ A BREAKOUT IS SAID AS WHAT IT IS: breakouts.py's breakout is a rating
#   above the median of the athlete's earlier races this season, so the line
#   reads "4.1 above the median of earlier races this season".
#
# ! LINKS ARE BUTTONS IN THE HTML, URLS ONLY IN THE TEXT PART.
#
# ! THE SITE'S LOOK, IN WHAT MAIL CLIENTS RENDER: tables and inline styles,
#   black on white, the site's navy (--link) as the one accent.
#
# ★ PICTURES (owner, 2026-10-11): the real logo, the athlete's photo and the
#   school's crest. Plain <img> tags on the site's own static URLs, the same
#   for every reader -- nothing in them identifies who opened the mail. Each
#   has alt text and the layout stands without it, for a client that blocks
#   images.
import datetime
import html as _html

INK = "#111418"          # style.css --ink
MUTED = "#6b7280"        # --muted
LINE = "#e5e7eb"         # --line
ACCENT = "#14477d"       # --link, the one accent
# RFC 5322 2.1.1: a header line should stay within 78 characters; a subject
# that grows past it is cut at a label and ends "and more"
SUBJECT_MAX = 78


def _e(s):
    return _html.escape(str(s if s is not None else ""), quote=True)


def firstName(name):
    """The account's first name for the greeting, or None."""
    parts = (name or "").split()
    return parts[0] if parts else None


def _first(full):
    parts = (full or "").split()
    return parts[0] if parts else (full or "")


def ordinal(n):
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def hsScale(pool):
    """The factor from `pool`'s own scale onto the HS-equivalent one -- the
    one public scale every page shows (owner, 2026-10-10: a middle schooler's
    alert read their own-pool number). pool_view.hsFactor, one number per
    pool; 1.0 when the pool has no HS twin or the factor cannot be had, so
    the mail still goes, on the number the page would show in that case."""
    if not pool:
        return 1.0
    try:
        from pool_view import hsFactor
        f = hsFactor(pool, None, None)
    except Exception:                                   # noqa: BLE001
        f = None
    return float(f) if f else 1.0


def clock(sec):
    """A result time as a results sheet prints it: 15:12.4, 4:21.5."""
    from recruiting import fmtTime
    return fmtTime(float(sec), True) if sec else ""


def faster(prev, now):
    """'12.6 seconds faster' (or 1:05 faster), or '' when it was not."""
    if not prev or not now or float(prev) <= float(now):
        return ""
    s = float(prev) - float(now)
    if s < 60:
        return f"{s:.1f} seconds faster"
    m, r = divmod(s, 60)
    return f"{int(m)}:{r:04.1f} faster"


def day(iso):
    """'Sat, Oct 3'."""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return d.strftime("%a, %b ") + str(d.day)


def _dist(m):
    from school import distLabel
    return distLabel(m) if m else ""


def raceHead(p):
    """'Nike Portland XC, Sat, Oct 3. Blue Lake Park, Varsity Boys, 5000m.'"""
    head = f"{p.get('meet_name') or 'Race'}, {day(p.get('date'))}."
    course = p.get("course") or next((r.get("course") for r in p.get("races") or [] if r.get("course")), None)
    where = [x for x in (course, p.get("division") or p.get("event"),
                         _dist(p.get("distance")) if p.get("type") == "race" else None) if x]
    return head + (" " + ", ".join(where) + "." if where else "")


def raceFacts(p):
    """[(label, value)] for one athlete's race."""
    out = []
    if p.get("place"):
        out.append(("Place", f"{ordinal(p['place'])} of {p['field']}" if p.get("field") else ordinal(p["place"])))
    if p.get("time"):
        out.append(("Time", clock(p["time"])))
    fl = p.get("flags") or {}
    for key, label in (("pr", "PR"), ("sr", "Season best")):
        if key in fl:
            prev = (fl.get(key) or {}).get("prev")
            bits = []
            if prev:
                bits.append(f"previous {clock(prev)}")
                f = faster(prev, p.get("time"))
                if f:
                    bits.append(f)
            out.append((label, ", ".join(bits) if bits else "yes"))
    k = hsScale(p.get("pool"))
    r = f"{float(p['rating']) * k:.1f}"
    if p.get("prev_rating") is not None:
        d = (float(p["rating"]) - float(p["prev_rating"])) * k
        if abs(d) >= 0.05:
            r += f", {'up' if d > 0 else 'down'} {abs(d):.1f} from the previous race"
        else:
            r += ", same as the previous race"
    out.append(("Rating", r))
    if "jump" in fl:
        by = (fl.get("jump") or {}).get("by")
        if by is not None:
            out.append(("Breakout", f"{float(by) * k:.1f} above the median of earlier races this season"))
    t = p.get("team")
    if t and not t.get("runners"):
        out.append(("Team", f"{t['school']} {ordinal(t['place'])} of {t['n_teams']} teams, {t['points']} points"))
    return out


def teamBlock(p):
    """The athlete's team in that race, as a squad block: its finish and
    score as the title, its scoring runners (five score, two displace) with
    places and times. None when the team did not score or the item predates
    the squad."""
    t = p.get("team")
    if not t or not t.get("runners"):
        return None
    return {"title": f"{t['school']}: {ordinal(t['place'])} of {t['n_teams']} teams, {t['points']} points",
            "rows": [(ordinal(x["place"]) if x.get("place") else "", x.get("name") or "",
                      clock(x.get("time"))) for x in t["runners"]],
            "me": next((k for k, x in enumerate(t["runners"])
                        if x.get("person_id") is not None and x.get("person_id") == p.get("person_id")), None)}


def teamFacts(p):
    """([(label, value)], [race blocks]) for a team's meet."""
    facts, blocks = [], []
    for rc in p.get("races") or []:
        if rc.get("place"):
            title = (f"{rc.get('division') or 'Race'}: {ordinal(rc['place'])} of {rc['n_teams']} teams, "
                     f"{rc['points']} points")
        else:
            title = f"{rc.get('division') or 'Race'}: no complete team"
        blocks.append({"title": title,
                       "rows": [(ordinal(x["place"]) if x.get("place") else "", x.get("name") or "",
                                 clock(x.get("time"))) for x in rc.get("runners") or []]})
    if not blocks:
        best = p.get("best") or {}
        facts.append(("Runners", str(p.get("n_rated") or 0)))
        if best.get("name"):
            facts.append(("Fastest", f"{best['name']}, {clock(best.get('time'))}".rstrip(", ")))
    if p.get("pr_names"):
        facts.append((f"PRs ({len(p['pr_names'])})", ", ".join(p["pr_names"])))
    elif p.get("n_pr"):
        facts.append(("PRs", str(p["n_pr"])))
    if p.get("jump_names"):
        facts.append((f"Above season median ({len(p['jump_names'])})", ", ".join(p["jump_names"])))
    return facts, blocks


def nextLine(nx):
    if not nx or not nx.get("name"):
        return None
    where = ", ".join(x for x in (nx.get("venue"), nx.get("state")) if x)
    return f"{nx['name']}, {day(nx.get('date'))}" + (f" ({where})" if where else "")


def _label(title, p, with_meet=True):
    """A short subject label for one item; the meet left off when the
    subject names it once for all of them."""
    fl = p.get("flags") or {}
    at = f" at {p.get('meet_name') or 'a meet'}" if with_meet else ""
    if p["type"] == "team":
        rc = next((r for r in p.get("races") or [] if r.get("place")), None)
        return f"{title} {ordinal(rc['place'])}{at}" if rc else f"{title}{at}"
    if "pr" in fl:
        return f"{title} PR{at}"
    if p.get("place"):
        return f"{title} {ordinal(p['place'])}{at}"
    return f"{title}{at}" if with_meet else title


def subjectLine(groups):
    """'Ezra Goldfarb: PR at Nike Portland XC (15:12.4, 12th of 184)' for one
    item; '3 results: Ezra Goldfarb PR at Nike Portland XC, Jesuit 2nd at
    Nike Portland XC' for several (one label per followed name)."""
    items = [(g[0], p) for g in groups for p in g[2]]
    if len(items) == 1:
        t, p = items[0]
        meet = p.get("meet_name") or "a meet"
        if p["type"] == "team":
            rc = next((r for r in p.get("races") or [] if r.get("place")), None)
            return (f"{t}: {ordinal(rc['place'])} of {rc['n_teams']} teams at {meet}" if rc
                    else f"{t}: results from {meet}")
        bits = [clock(p.get("time"))] if p.get("time") else []
        if p.get("place") and p.get("field"):
            bits.append(f"{ordinal(p['place'])} of {p['field']}")
        what = "PR" if "pr" in (p.get("flags") or {}) else "result"
        return f"{t}: {what} at {meet}" + (f" ({', '.join(bits)})" if bits else "")

    def rank(p):
        fl = p.get("flags") or {}
        return ("pr" not in fl, p.get("type") == "team")
    meets = {p.get("meet_name") for _t, p in items}
    one = len(meets) == 1 and None not in meets
    labels = [_label(g[0], sorted(g[2], key=rank)[0], not one) for g in groups]
    out = (f"{len(items)} results at {meets.pop()}: " if one else f"{len(items)} results: ") + labels[0]
    for lab in labels[1:]:
        if len(out) + 2 + len(lab) > SUBJECT_MAX:
            return out + " and more"
        out += ", " + lab
    return out


def _names(titles):
    if len(titles) == 1:
        return titles[0]
    return ", ".join(titles[:-1]) + " and " + titles[-1]


def _btn(href, text):
    return (f'<a href="{_e(href)}" style="display:inline-block;background:{INK};color:#ffffff;'
            f'text-decoration:none;font-weight:600;font-size:14px;line-height:20px;padding:8px 14px;'
            f'border-radius:6px">{_e(text)}</a>')


def _link(href, text):
    return (f'<a href="{_e(href)}" style="display:inline-block;color:{ACCENT};font-weight:600;'
            f'text-decoration:none;font-size:14px;line-height:20px">{_e(text)} &rarr;</a>')


def _factsHtml(facts):
    if not facts:
        return ""
    rows = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;font-size:13px;line-height:20px;color:{MUTED};'
        f'white-space:nowrap;vertical-align:top">{_e(k)}</td>'
        f'<td style="padding:3px 0;font-size:15px;line-height:20px;color:{INK}">{_e(v)}</td></tr>'
        for k, v in facts)
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 10px">{rows}</table>'


def _squadHtml(block):
    me = block.get("me")
    rows = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;font-size:14px;line-height:20px;color:{MUTED};'
        f'text-align:right;white-space:nowrap">{_e(pl)}</td>'
        f'<td style="padding:2px 12px 2px 0;font-size:14px;line-height:20px;color:{INK}'
        f'{";font-weight:700" if k == me else ""}">{_e(n)}</td>'
        f'<td style="padding:2px 0;font-size:14px;line-height:20px;color:{INK};white-space:nowrap;'
        f'font-variant-numeric:tabular-nums">{_e(t)}</td></tr>'
        for k, (pl, n, t) in enumerate(block["rows"]))
    title = (f'<p style="margin:8px 0 4px;font-size:15px;line-height:20px;font-weight:600;color:{INK}">'
             f'{_e(block["title"])}</p>')
    if block.get("img"):
        # the team's crest beside its finish (owner, 2026-10-11)
        title = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 4px"><tr>'
                 f'<td style="padding:0 8px 0 0;vertical-align:middle">{_img(block["img"], "", 24)}</td>'
                 f'<td style="vertical-align:middle;font-size:15px;line-height:20px;font-weight:600;color:{INK}">'
                 f'{_e(block["title"])}</td></tr></table>')
    return (title
            + f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 8px">{rows}</table>')


def _img(src, alt, size, round_=False):
    return (f'<img src="{_e(src)}" width="{size}" height="{size}" alt="{_e(alt)}" '
            f'style="display:block;width:{size}px;height:{size}px;border:0;object-fit:'
            f'{"cover" if round_ else "contain"};{"border-radius:50%;" if round_ else ""}">')


def _groupHead(title, items, kind, origin):
    """The group's heading: the athlete's photo (or the team's crest) beside
    the name, the school with its crest under an athlete's name."""
    photo = next((p.get("photo") for p in items if p.get("photo")), None)
    logo = next((p.get("crest") for p in items if p.get("crest")), None)
    school = next((p.get("school") or (p.get("team") or {}).get("school")
                   for p in items if p.get("school") or p.get("team")), None)
    name = f'<p style="margin:0;font-size:17px;line-height:22px;font-weight:700;color:{INK}">{_e(title)}</p>'
    if kind == "athlete" and school:
        crest = (f'<td style="padding:0 6px 0 0;vertical-align:middle">{_img(origin + logo, school, 18)}</td>'
                 if logo else "")
        name += (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:2px 0 0"><tr>{crest}'
                 f'<td style="vertical-align:middle;font-size:14px;line-height:18px;color:{MUTED}">{_e(school)}</td>'
                 f'</tr></table>')
    pic = (_img(origin + photo, title, 48, round_=True) if kind == "athlete" and photo
           else _img(origin + logo, title, 44) if kind == "team" and logo else None)
    if not pic:
        return name
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="padding:0 12px 0 0;vertical-align:middle">{pic}</td>'
            f'<td style="vertical-align:middle">{name}</td></tr></table>')


def compose(groups, origin, unsub, account_name=None, today=None):
    """(subject, text, html) for one account's digest.

    groups: [(title, page_href, [payload], kind, next_meet)] in the order the
    account followed them; kind is 'athlete' or 'team'; next_meet is
    {name, date, venue, state} or None."""
    first = firstName(account_name)
    hi = f"Hi {first}," if first else "Hi,"
    subject = subjectLine(groups)
    lead = "New results for the athletes and teams you follow."
    me = f"{origin}/account/me"
    alerts = f"{origin}/account/me#alerts"
    because = (f"You're getting this because you follow {_names([g[0] for g in groups])} "
               f"on Racecast.")

    text = [hi, "", lead, ""]
    blocks = []
    for g in groups:
        title, href, items, kind = g[:4]
        nx = g[4] if len(g) > 4 else None
        page_text = f"{_first(title)}'s page" if kind == "athlete" else "Team page"
        text.append(title.upper())
        parts = []
        for p in sorted(items, key=lambda p: (p["date"], p.get("meet_name") or "")):
            head = raceHead(p)
            text.append(head)
            after = []
            if p["type"] == "race":
                facts, squads = raceFacts(p), []
                tb = teamBlock(p)
                if tb:
                    if p.get("crest"):
                        tb["img"] = origin + p["crest"]
                    after = [tb]
            else:
                facts, squads = teamFacts(p)

            def sqText(sq):
                text.append(sq["title"])
                for k, (pl, n, t) in enumerate(sq["rows"]):
                    text.append(f"  {pl:>5}  {n}  {t}")
            for sq in squads:
                sqText(sq)
            for k, v in facts:
                text.append(f"{k}: {v}")
            for sq in after:
                sqText(sq)
            race_word = "See the race" if p["type"] == "race" else "See the results"
            if p.get("href"):
                text.append(f"{race_word}: {origin}{p['href']}")
            text.append("")
            parts.append(
                f'<p style="margin:10px 0 0;font-size:14px;line-height:20px;color:{MUTED}">{_e(head)}</p>'
                + "".join(_squadHtml(sq) for sq in squads) + _factsHtml(facts)
                + "".join(_squadHtml(sq) for sq in after)
                + (f'<p style="margin:0 0 6px">{_btn(origin + p["href"], race_word)}</p>' if p.get("href") else ""))
        nl = nextLine(nx)
        if nl:
            text.append(f"Next likely meet: {nl}")
        text.append(f"{page_text}: {origin}{href}")
        text.append("")
        blocks.append(
            f'<tr><td style="padding:16px 0 14px;border-top:1px solid {LINE}">'
            + _groupHead(title, items, kind, origin)
            + "".join(parts)
            + (f'<p style="margin:10px 0 0;font-size:14px;line-height:20px;color:{INK}">'
               f'<span style="color:{MUTED}">Next likely meet:</span> {_e(nl)}</p>' if nl else "")
            + f'<p style="margin:6px 0 0">{_link(origin + href, page_text)}</p></td></tr>')
    text += ["--", because, f"Change how often: {alerts}", f"Unsubscribe: {unsub}", f"Your page: {me}", ""]

    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><title>{_e(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid {LINE};border-radius:10px;font-family:{font}">
<tr><td style="padding:22px 24px 0">
<a href="{_e(origin)}/" style="text-decoration:none"><img src="{_e(origin)}/static/logo-card.png" width="152" height="36" alt="RACECAST" style="display:block;width:152px;height:36px;border:0;font-size:21px;line-height:24px;font-weight:900;font-style:italic;color:{INK}"></a>
</td></tr>
<tr><td style="padding:16px 24px 4px">
<p style="margin:0 0 4px;font-size:16px;line-height:24px;color:{INK}">{_e(hi)}</p>
<p style="margin:0 0 14px;font-size:16px;line-height:24px;color:{INK}">{_e(lead)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{''.join(blocks)}
</table>
</td></tr>
<tr><td style="padding:4px 24px 20px;border-top:1px solid {LINE}">
<p style="margin:14px 0 0;font-size:12px;line-height:18px;color:{MUTED}">{_e(because)}
<a href="{_e(alerts)}" style="color:{MUTED};text-decoration:underline">Change how often</a> &middot;
<a href="{_e(unsub)}" style="color:{MUTED};text-decoration:underline">Unsubscribe</a></p>
</td></tr>
</table>
</td></tr></table>
</body></html>
"""
    return subject, "\n".join(text), html
