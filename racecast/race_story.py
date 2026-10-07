"""
race_story.py -- the two sentences at the top of an XC race page.

    raceStory(results, teams, titles=0)   -> [Markup, ...]  (0-2 sentences)
    priorTitles(cur, header, results)     -> int  (straight earlier wins)

★ WHY RULES, NOT A WRITER (owner, 2026-10-07: "How are you generating
  stories btw?" / "formalize summary so I can see it working"). Every
  sentence is a template that fires only when its facts are on the page,
  and every number in it is read off the rows the page already shows. A
  sentence that cannot be checked against the table below it is not
  written.

! NO JUDGEMENT WORDS WITHOUT A TEST. The first mockup said Campolindo won
  "on depth, five scorers 46.2 s apart"; Oak Park (18.6 s) and Hart
  (19.7 s) were both tighter that day. So: no "dominant", no "on depth",
  no "upset" -- the margin, the leader, the places, as numbers.

The sentences:

  1. THE WINNER. "<W> (<school>) won in <time>, <gap> seconds clear of
     <second> (<school>)." With a repeat: "won a <third> straight title
     here in <time>" -- see priorTitles. A dead heat on the sheet says so
     instead of "0.0 seconds clear".
  2. THE TEAM TITLE. "<T> took the team title with <pts> points, <m>
     ahead of <T2>, led by <name> in <ord>." A tie on points is "on the
     sixth-runner tiebreak over <T2>". No second team: no margin.
"""
import re

from markupsafe import Markup, escape

from last_edition import editionName

# Results at or past this are DNF/DNS/DQ sentinels, not times (app.format_time
# draws the same line).
_SENTINEL_S = 100_000

_ORD_WORDS = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
              7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def ordinal(n):
    """1 -> '1st', 12 -> '12th', 23 -> '23rd'."""
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _t(row):
    t = row.get("time_seconds")
    if t is None:
        return None
    t = float(t)
    return t if 0 < t < _SENTINEL_S else None


def _clock(seconds):
    """14:43.7 from 883.7 -- the decimals the data has, like format_time."""
    s = float(seconds)
    m, sec = divmod(s, 60)
    whole = f"{int(m)}:{int(sec):02d}"
    frac = round(sec - int(sec), 2)
    if frac == 0:
        return whole
    return whole + f"{frac:.2f}"[1:].rstrip("0")


def _gap(seconds):
    """'8.2 seconds', '1 second', '0.4 seconds' -- tenths, as the sheet."""
    g = round(float(seconds), 1)
    if g == 1:
        return "1 second"
    return f"{g:g} seconds"


def _who(row):
    name = escape(row.get("name") or "Unknown")
    school = row.get("school_label") or row.get("school")
    return Markup(f"<b>{name}</b> ({escape(school)})") if school else Markup(f"<b>{name}</b>")


def _plain(row):
    name = escape(row.get("name") or "Unknown")
    school = row.get("school_label") or row.get("school")
    return Markup(f"{name} ({escape(school)})") if school else name


def surname(name):
    """'Evan Noonan' -> 'Noonan'; a one-word name stays whole."""
    parts = (name or "").split()
    return parts[-1] if len(parts) > 1 else (name or "Unknown")


# ★ THE HEADER ALREADY NAMES THE WINNER AND THE MARK (owner, 2026-10-07:
#   "try not to have duplicated things"): the champion line above the
#   summary shows "EVAN NOONAN 14:43.7", so the sentence carries what the
#   line cannot -- the margin, the runner-up, the streak -- and calls the
#   winner by surname. The team's points are in the sidebar's first row; the
#   sentence gives the margin and who led them.
def winnerSentence(results, titles=0):
    fin = [r for r in results if _t(r) is not None]
    if len(fin) < 2:
        return None
    w, s = fin[0], fin[1]
    who = Markup(f"<b>{escape(surname(w.get('name')))}</b>")
    won = "won"
    if titles >= 1:
        won = f"won a {_ORD_WORDS.get(titles + 1, ordinal(titles + 1))} straight title here"
    gap = _t(s) - _t(w)
    if round(gap, 1) <= 0:
        return Markup(f"{who} {won}, given the place over {_plain(s)} on the same time.")
    return Markup(f"{who} {won}, {_gap(gap)} clear of {_plain(s)}.")


def teamSentence(teams):
    scored = [t for t in (teams or []) if t.get("points") is not None]
    if not scored:
        return None
    scored = sorted(scored, key=lambda t: (t.get("place") or 10 ** 6))
    a = scored[0]
    name = escape(a.get("label") or a.get("school"))
    out = f"<b>{name}</b> won the team title"
    if len(scored) > 1:
        b = scored[1]
        bname = escape(b.get("label") or b.get("school"))
        m = int(b["points"]) - int(a["points"])
        if m == 0:
            out += f" on the sixth-runner tiebreak over {bname}"
        else:
            out += f" by {m} point{'' if m == 1 else 's'} over {bname}"
    lead = (a.get("runners") or [None])[0]
    if lead and lead.get("place"):
        out += f", led by {escape(lead.get('name') or 'Unknown')} in {ordinal(lead['place'])}"
    return Markup(out + ".")


def raceStory(results, teams, titles=0):
    return [s for s in (winnerSentence(results, titles), teamSentence(teams)) if s]


# ------------------------------------------------------------ repeat titles
_DIV_NOISE = re.compile(r"\b(\d+|i{1,3}|iv|v|vi{0,3}|d\d+|div|division)\b")


def divisionKey(div):
    """'Division 3 Boys' and 'Division II Boys' -> 'boys'; 'Frosh/Soph
    Boys' -> 'frosh soph boys'. A state meet's divisions renumber; a
    frosh race is not the varsity race."""
    s = re.sub(r"[^a-z0-9]+", " ", (div or "").lower())
    return " ".join(_DIV_NOISE.sub(" ", s).split())


def priorTitles(cur, person_id, race_date, meet_name, division):
    """How many seasons in a row, counting back from the one before this
    race, the winner also WON this meet (same edition name, same
    division-key) -- 0 when the chain breaks at once.

    ★ "WON" IS THE FASTEST TIME IN THAT DIVISION'S ROWS, not results.place:
      place is not a within-division position (race_xc's comment), so a
      division of nine carries places past 60.
    ! BOUNDED: one savepoint, 1.5 s, and any failure is 0 -- the page
      renders without the clause, never without the page.
    """
    if not person_id or not race_date or not meet_name:
        return 0
    key, dkey = editionName(meet_name), divisionKey(division)
    year = int(str(race_date)[:4])
    cur.execute("SHOW statement_timeout")
    got = cur.fetchone()
    was = got["statement_timeout"] if isinstance(got, dict) else got[0]
    cur.execute("SAVEPOINT race_story")
    try:
        cur.execute("SET LOCAL statement_timeout = 1500")
        cur.execute("""
            SELECT r.meet_id, r.div_id, r.source, substr(r.date::text, 1, 4)::int AS yr,
                   r.time_seconds,
                   COALESCE(m.meet_name, mt.meet_name) AS meet_name,
                   COALESCE(m.division,
                            mt.division_distances -> r.div_id::text ->> 'div_name') AS division
            FROM   results r
            LEFT JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                             AND m.source = r.source
            LEFT JOIN meets_tfrrs mt ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id
                                    AND mt.sport = 'XC'
            WHERE  r.person_id = %s AND r.date < %s
              AND  r.time_seconds > 0 AND r.time_seconds < %s
        """, (person_id, race_date, _SENTINEL_S))
        cands = [dict(x) if not isinstance(x, dict) else x for x in cur.fetchall()]
        cands = [c for c in cands if editionName(c["meet_name"]) == key
                 and (not dkey or not c["division"] or divisionKey(c["division"]) == dkey)]
        won = set()
        for c in cands:
            if c["yr"] in won:
                continue
            cur.execute("""SELECT min(time_seconds) AS t FROM results
                           WHERE meet_id = %s AND div_id = %s AND source = %s
                             AND time_seconds > 0 AND time_seconds < %s""",
                        (c["meet_id"], c["div_id"], c["source"], _SENTINEL_S))
            best = cur.fetchone()
            best = best["t"] if isinstance(best, dict) else best[0]
            if best is not None and float(c["time_seconds"]) <= float(best):
                won.add(c["yr"])
        # ! SET LOCAL OUTLIVES A RELEASE (only a rollback undoes it): put
        #   the caller's timeout back first, as _borrowTwins does
        cur.execute("SET LOCAL statement_timeout = %s", (was,))
        cur.execute("RELEASE SAVEPOINT race_story")
    except Exception:                                   # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT race_story")   # and the timeout
        return 0
    n = 0
    while (year - 1 - n) in won:
        n += 1
    return n


# ------------------------------------------------------------ track events
def _tfWho(row, bold=True):
    """A relay is its team; anyone else is "Name (School)"."""
    if row.get("is_relay"):
        name = escape(row.get("school") or "Relay")
        return Markup(f"<b>{name}</b>") if bold else name
    name = escape(row.get("athlete_name") or "Unknown")
    school = row.get("school")
    core = f"<b>{name}</b>" if bold else str(name)
    return Markup(f"{core} ({escape(school)})") if school else Markup(core)


def tfWinner(sections, is_field):
    """(winner row, runner-up row or None, how) for a track race page.

    ★ THE FINAL DECIDES WHEN THERE IS ONE: its first two places. Without a
      final but with several heats, the best mark across the heats is the
      fastest/longest, not a win -- `how` is "heats" and the sentence says
      so. One undivided field: its own order.
    Running events rank by time, field events by parseMark (both the
    route's own sort keys), so a DNS row never leads."""
    from tf_points import parseMark
    finals = [s for s in sections if (s.get("label") or "").lower().startswith("final")]
    pool = finals[0]["rows"] if finals else [r for s in sections for r in s["rows"]]
    how = "won" if finals or len(sections) <= 1 else "heats"
    if is_field:
        marked = [(parseMark(r.get("mark")), r) for r in pool]
        marked = [(m, r) for m, r in marked if m is not None and m > 0]
        marked.sort(key=lambda x: -x[0])
    else:
        marked = [(_t(r), r) for r in pool]
        marked = [(t, r) for t, r in marked if t is not None]
        marked.sort(key=lambda x: x[0])
    if not marked:
        return None, None, how
    return marked[0][1], (marked[1][1] if len(marked) > 1 else None), how


def tfStory(sections, is_field):
    """(winner row, [sentences]) for a track race page: the winner and the
    margin (running) or the runner-up's mark (field; marks can be in feet
    or metres, so no subtraction), then how many set PRs."""
    w, s, how = tfWinner(sections, is_field)
    out = []
    if w is not None:
        n = sum(1 for x in sections if x.get("rows"))
        who = (Markup(f"<b>{escape(w.get('school') or 'Relay')}</b>") if w.get("is_relay")
               else Markup(f"<b>{escape(surname(w.get('athlete_name')))}</b>"))
        if how == "won":
            lead = f"{who} won"
        elif is_field:
            lead = f"{who} had the best mark across {n} flights"
        else:
            lead = f"{who} ran the fastest time across {n} heats"
        sent = None
        if s is not None and is_field:
            sent = f"{lead}; {_tfWho(s, bold=False)} was next with {escape(s.get('display_result') or '')}"
        elif s is not None:
            gap = _t(s) - _t(w)
            sent = (f"{lead}, given the place over {_tfWho(s, bold=False)} on the same time"
                    if round(gap, 2) <= 0 else
                    f"{lead}, {_gapTf(gap)} clear of {_tfWho(s, bold=False)}")
        elif how != "won":
            sent = lead
        if sent:
            out.append(Markup(sent + "."))
    rows = [r for sec in sections for r in sec["rows"]]
    fin = [r for r in rows if (r.get("display_result") or " - ").strip() not in ("-", "")]
    prs = sum(1 for r in fin if r.get("is_pr"))
    if fin and prs:
        out.append(Markup(f"{prs} of {len(fin)} set personal records."))
    return w, out


def _gapTf(seconds):
    """Hundredths on the track, as the clock gives them: '0.05 seconds'."""
    g = round(float(seconds), 2)
    if g == 1:
        return "1 second"
    return f"{g:g} seconds"
