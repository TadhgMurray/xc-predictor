# Project: xc-predictor / racecast
# File:    decision_email.py
# Purpose: The mail a person gets when the owner decides on something they
#          sent: an uploaded meet (uploads.py) or a suggested fix (fixes.py).
#          Pure: (subject, text, html), no database, no clock.
#
# ★ THE ALERT MAILS' PLAIN STYLE (owner, 2026-10-10: reuse alert_email's
#   look): labelled facts, no adjectives, the site's ink and one accent,
#   links as buttons in the HTML and as URLs in the text. Built from
#   alert_email's own pieces so the two cannot drift apart.
#
# ! NO DATA FROM THE FILE BEYOND THE MEET'S NAME. A results file is
#   somebody else's text; the mail names the meet, its date and the counts.

import alert_email as AE

_e = AE._e
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

FIX_WORDS = {"grade": "Wrong grade or class year", "school": "Wrong school",
             "not_mine": "A result that is not mine", "missing": "A missing result",
             "same_person": "Two pages that are the same person"}


def _wrap(subject, hi, lead, facts, button, foot):
    """The alert mail's frame around one block of facts."""
    text = [hi, "", lead, ""]
    for k, v in facts:
        text.append(f"{k}: {v}")
    if button:
        text += ["", f"{button[1]}: {button[0]}"]
    text += ["", "--", foot, ""]
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><title>{_e(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid {AE.LINE};border-radius:10px;font-family:{FONT}">
<tr><td style="padding:22px 24px 0">
<p style="margin:0;font-size:21px;line-height:24px;font-weight:900;font-style:italic;letter-spacing:-0.5px;color:{AE.INK}">RACECAST</p>
</td></tr>
<tr><td style="padding:16px 24px 8px">
<p style="margin:0 0 4px;font-size:16px;line-height:24px;color:{AE.INK}">{_e(hi)}</p>
<p style="margin:0 0 8px;font-size:16px;line-height:24px;color:{AE.INK}">{_e(lead)}</p>
{AE._factsHtml(facts)}
{f'<p style="margin:4px 0 12px">{AE._btn(button[0], button[1])}</p>' if button else ''}
</td></tr>
<tr><td style="padding:4px 24px 20px;border-top:1px solid {AE.LINE}">
<p style="margin:14px 0 0;font-size:12px;line-height:18px;color:{AE.MUTED}">{_e(foot)}</p>
</td></tr>
</table>
</td></tr></table>
</body></html>
"""
    return subject, "\n".join(text), html


def uploadDecision(up, status, note, origin):
    """(subject, text, html) for an uploaded meet's decision."""
    first = AE.firstName(up.get("account_name"))
    hi = f"Hi {first}," if first else "Hi,"
    meet = up.get("meet_name") or "your meet"
    when = AE.day(up.get("meet_date")) or (up.get("meet_date") or "")
    if status == "approved":
        subject = f"Added to Racecast: {meet}"
        lead = ("The results you uploaded were checked and added. They show on the site "
                "after the next nightly update.")
    elif status == "flagged":
        subject = f"Held for a check: {meet}"
        lead = "The results you uploaded look like a meet Racecast already has, so they were held."
    else:
        subject = f"Not added: {meet}"
        lead = "The results you uploaded were checked and not added."
    facts = [("Meet", meet), ("Date", when), ("Results", f"{up.get('n_rows') or 0}")]
    if up.get("file_name"):
        facts.append(("File", up["file_name"]))
    if note:
        facts.append(("Note", note))
    button = (f"{origin}/account/uploads/{up['id']}", "See the upload")
    foot = ("You're getting this because you uploaded results to Racecast. "
            "Reply to this email if something is wrong.")
    return _wrap(subject, hi, lead, facts, button, foot)


def fixDecision(fx, status, outcome, note, origin, athlete_name=None, account_name=None):
    """(subject, text, html) for a suggested fix's decision. outcome is
    what approval did ('applied' / 'queued' / 'recorded') in one line."""
    first = AE.firstName(account_name)
    hi = f"Hi {first}," if first else "Hi,"
    what = FIX_WORDS.get(fx.get("kind"), "A fix")
    who = athlete_name or f"athlete {fx.get('person_id')}"
    if status == "approved":
        subject = f"Fix approved: {who}"
        lead = "The fix you suggested was checked and approved."
    else:
        subject = f"Fix not made: {who}"
        lead = "The fix you suggested was checked and not made."
    facts = [("Page", who), ("Fix", what)]
    if outcome and status == "approved":
        facts.append(("What happens", outcome))
    if note:
        facts.append(("Note", note))
    button = (f"{origin}/athlete/{fx.get('person_id')}", "Your page")
    foot = ("You're getting this because you suggested a fix on Racecast. "
            "Reply to this email if something is wrong.")
    return _wrap(subject, hi, lead, facts, button, foot)
