# Project: xc-predictor / racecast
# File:    alert_email.py
# Purpose: The follow digest as a person would write it (owner, 2026-10-10:
#          the first cut "looks bad, needs to be a bit more personalized and
#          human friendly"). follow_alerts.py decides WHAT is new; this turns
#          those items into one email -- a subject, an HTML body and its
#          plain-text twin. Pure: no database, no clock it does not get.
#
# ★ SENTENCES, NOT ROWS. Each item is one sentence about one race:
#     "Ezra Goldfarb ran 15:12 at Nike Portland XC on Saturday -- a new PR by
#      13 seconds, and his best race of the season."
#     "Jesuit ran at Nike Portland XC on Saturday: 7 runners, led by Owen
#      Castellano (15:02), with 2 PRs."
#   The rating is there for whoever wants it, small and grey ("rating
#   118.9"); the sentence never needs it.
#
# ★ A BREAKOUT IN PLAIN WORDS. breakouts.py's breakout is a race rated well
#   above the median of the athlete's earlier races this season; the mail
#   says exactly that -- "well above his usual level" -- and never prints the
#   points it was by.
#
# ! LINKS ARE BUTTONS IN THE HTML, URLS ONLY IN THE TEXT PART. A raw URL in
#   an HTML body reads as a machine wrote it; a text-only client still needs
#   somewhere to go.
#
# ! THE SITE'S LOOK, IN WHAT MAIL CLIENTS RENDER: tables and inline styles,
#   black on white, the site's navy (--link) as the one accent. No images, so
#   nothing is blocked and nothing tracks an open.
import datetime
import html as _html

INK = "#111418"          # style.css --ink
MUTED = "#6b7280"        # --muted
LINE = "#e5e7eb"         # --line
ACCENT = "#14477d"       # --link, the one accent
# RFC 5322 2.1.1: a header line should stay within 78 characters; a subject
# that grows past it is cut at a label and ends "and more"
SUBJECT_MAX = 78
# "on Saturday" only inside the week the reader is in; older races get a date
WEEK_DAYS = 7


def _e(s):
    return _html.escape(str(s if s is not None else ""), quote=True)


def firstName(name):
    """The account's first name for the greeting, or None."""
    parts = (name or "").split()
    return parts[0] if parts else None


def _first(full):
    parts = (full or "").split()
    return parts[0] if parts else (full or "")


def pronoun(pool):
    """his / her / their from the result's pool (hs_m, college_f ...)."""
    g = (pool or "").rsplit("_", 1)[-1]
    return {"m": "his", "f": "her"}.get(g, "their")


def clock(sec, sport):
    """A race time as a runner says it: 15:12 for cross country, 4:21.5 on
    the track (recruiting.fmtTime's rule)."""
    from recruiting import fmtTime
    if not sec:
        return ""
    return fmtTime(float(sec), None if sport == "TF" else False)


def gap(sec):
    """'13 seconds', '1:05', '0.4 seconds' -- how much faster."""
    s = float(sec)
    if s < 10:
        return f"{s:.1f} seconds".replace(".0 seconds", " seconds")
    if s < 60:
        n = int(round(s))
        return f"{n} second{'s' if n != 1 else ''}"
    m, r = divmod(int(round(s)), 60)
    return f"{m}:{r:02d}"


def when(iso, today):
    """'on Saturday' within the reader's week, else 'on Oct 3'."""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return ""
    if today and 0 <= (today - d).days < WEEK_DAYS:
        return "on " + d.strftime("%A")
    return "on " + d.strftime("%b ") + str(d.day)


def _join(parts):
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def raceSentence(p, today):
    """One athlete's race, as a sentence."""
    name = p.get("name") or "Your athlete"
    his = pronoun(p.get("pool"))
    t = clock(p.get("time"), p.get("sport"))
    where = p.get("meet_name") or "a race"
    s = (f"{name} ran {t} at {where}" if t else f"{name} raced at {where}")
    w = when(p.get("date"), today)
    if w:
        s += " " + w
    fl = p.get("flags") or {}
    parts = []
    if "pr" in fl:
        prev = (fl.get("pr") or {}).get("prev")
        if prev and p.get("time") and float(prev) > float(p["time"]):
            parts.append(f"a new PR by {gap(float(prev) - float(p['time']))}")
        else:
            parts.append("a new PR")
    if fl.get("sr"):
        parts.append(f"{his} best race of the season")
    if "jump" in fl:
        parts.append(f"a breakout race, well above {his} usual level")
    return s + (" — " + _join(parts) if parts else "") + "."


def teamSentence(team, p, today):
    """A team's meet, as a sentence."""
    where = p.get("meet_name") or "a meet"
    s = f"{team} ran at {where}"
    w = when(p.get("date"), today)
    if w:
        s += " " + w
    n = int(p.get("n_rated") or 0)
    best = p.get("best") or {}
    s += f": {n} runner{'s' if n != 1 else ''}"
    if best.get("name"):
        t = clock(best.get("time"), p.get("sport"))
        s += f", led by {best['name']}" + (f" ({t})" if t else "")
    extra = []
    if p.get("n_pr"):
        extra.append(f"{p['n_pr']} PR{'s' if p['n_pr'] != 1 else ''}")
    if p.get("n_jump"):
        k = p["n_jump"]
        extra.append(f"{k} breakout race{'s' if k != 1 else ''}")
    if extra:
        s += ", with " + " and ".join(extra)
    return s + "."


def _small(p):
    """The grey line under a race: the rating, for those who want it."""
    bits = []
    if p.get("type") == "race" and p.get("rating") is not None:
        bits.append(f"rating {float(p['rating']):.1f}")
    elif p.get("type") == "team" and (p.get("best") or {}).get("rating") is not None:
        bits.append(f"best rating {float(p['best']['rating']):.1f}")
    return " · ".join(bits)


def _label(title, p):
    """A short subject label for one item."""
    fl = p.get("flags") or {}
    if p["type"] == "team":
        return f"{title} at {p.get('meet_name') or 'a meet'}"
    if "pr" in fl:
        return f"{title} PR"
    if fl.get("sr"):
        return f"{title} season best"
    if "jump" in fl:
        return f"{title} breakout"
    return f"{title} at {p.get('meet_name') or 'a race'}"


def subjectLine(groups):
    """'Ezra Goldfarb ran a PR at Nike Portland XC' for one item;
    '3 updates: Ezra Goldfarb PR, Jesuit at Nike Portland XC' for several
    (one label per followed name, the most notable item's)."""
    items = [(t, p) for t, _h, ps, *_ in groups for p in ps]
    if len(items) == 1:
        t, p = items[0]
        where = p.get("meet_name") or ("a meet" if p["type"] == "team" else "a race")
        fl = p.get("flags") or {}
        if p["type"] == "team":
            return f"{t} raced at {where}"
        if "pr" in fl:
            return f"{t} ran a PR at {where}"
        if fl.get("sr"):
            return f"{t} ran a season best at {where}"
        if "jump" in fl:
            return f"{t} had a breakout race at {where}"
        return f"{t} raced at {where}"

    def rank(p):
        fl = p.get("flags") or {}
        return ("pr" not in fl, not fl.get("sr"), "jump" not in fl, p.get("type") == "team")
    labels = []
    for t, _h, ps, *_ in groups:
        best = sorted(ps, key=rank)[0]
        labels.append(_label(t, best))
    head = f"{len(items)} updates: "
    out = head + labels[0]
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
            f'text-decoration:none;font-weight:600;font-size:14px;line-height:20px;padding:9px 16px;'
            f'border-radius:6px">{_e(text)}</a>')


def _link(href, text):
    return (f'<a href="{_e(href)}" style="color:{ACCENT};font-weight:600;text-decoration:none">'
            f'{_e(text)} &rarr;</a>')


def compose(groups, origin, unsub, account_name=None, today=None):
    """(subject, text, html) for one account's digest.

    groups: [(title, page_href, [payload], kind)] in the order the account
    followed them; kind is 'athlete' or 'team'."""
    first = firstName(account_name)
    hi = f"Hi {first}," if first else "Hi,"
    subject = subjectLine(groups)
    lead = "Here's the latest from the athletes and teams you follow."
    me = f"{origin}/account/me"
    alerts = f"{origin}/account/me#alerts"
    because = (f"You're getting this because you follow {_names([g[0] for g in groups])} "
               f"on Racecast.")

    text = [hi, "", lead, ""]
    blocks = []
    for title, href, items, kind in groups:
        page_text = f"See {_first(title) if kind == 'athlete' else title}'s page"
        text.append(title.upper())
        rows = []
        for p in sorted(items, key=lambda p: (p["date"], p.get("meet_name") or "")):
            sent = raceSentence(p, today) if p["type"] == "race" else teamSentence(title, p, today)
            small = _small(p)
            race_word = "See the race" if p["type"] == "race" else "See the meet"
            text.append(sent)
            if small:
                text.append(f"  ({small})")
            if p.get("href"):
                text.append(f"  {race_word}: {origin}{p['href']}")
            text.append("")
            rows.append(
                f'<p style="margin:0 0 4px;font-size:16px;line-height:24px;color:{INK}">{_e(sent)}</p>'
                + (f'<p style="margin:0 0 6px;font-size:13px;line-height:18px;color:{MUTED}">{_e(small)}</p>'
                   if small else "")
                + (f'<p style="margin:0 0 16px;font-size:14px;line-height:20px">'
                   f'{_link(origin + p["href"], race_word)}</p>' if p.get("href") else
                   '<p style="margin:0 0 16px"></p>'))
        text.append(f"{page_text}: {origin}{href}")
        text.append("")
        blocks.append(
            f'<tr><td style="padding:18px 0 0;border-top:1px solid {LINE}">'
            f'<p style="margin:0 0 10px;font-size:17px;line-height:22px;font-weight:700;color:{INK}">{_e(title)}</p>'
            + "".join(rows)
            + f'<p style="margin:0 0 20px">{_btn(origin + href, page_text)}</p></td></tr>')
    text += ["--", because, f"Change how often: {alerts}", f"Unsubscribe: {unsub}", f"Your page: {me}", ""]

    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><title>{_e(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid {LINE};border-radius:10px;font-family:{font}">
<tr><td style="padding:22px 28px 0">
<p style="margin:0;font-size:21px;line-height:24px;font-weight:900;font-style:italic;letter-spacing:-0.5px;color:{INK}">RACECAST</p>
</td></tr>
<tr><td style="padding:18px 28px 4px">
<p style="margin:0 0 6px;font-size:16px;line-height:24px;color:{INK}">{_e(hi)}</p>
<p style="margin:0 0 18px;font-size:16px;line-height:24px;color:{INK}">{_e(lead)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{''.join(blocks)}
</table>
</td></tr>
<tr><td style="padding:4px 28px 22px;border-top:1px solid {LINE}">
<p style="margin:14px 0 0;font-size:12px;line-height:18px;color:{MUTED}">{_e(because)}
<a href="{_e(alerts)}" style="color:{MUTED};text-decoration:underline">Change how often</a> &middot;
<a href="{_e(unsub)}" style="color:{MUTED};text-decoration:underline">Unsubscribe</a></p>
</td></tr>
</table>
</td></tr></table>
</body></html>
"""
    return subject, "\n".join(text), html
