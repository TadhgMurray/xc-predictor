"""movers.py -- "This week in <state>": the weekly digest of who moved
(owner, 2026-10-10, approved): /movers?state=CA&pool=hs_m.

    digest(cur, sport, pool, state)  -> {weeks, risers, fallers, entered,
                                         breakouts, above, sentences}

Three lists and the sentences that sum them up:

  RANK CHANGES   this week's state board against last week's, from
                 season_rank_weekly (build_rank_snapshot.py, step 10g2 --
                 the history did not exist before it; see that file).
  BREAKOUTS      breakouts.py's rating jumps in the state, this week.
  ABOVE LEVEL    the races run this week furthest above the runner's own
                 level coming in -- above_level.py's rule, for the whole
                 state's week instead of one race.

★ THE BOARD'S FIRST PAGE IS THE WINDOW. A rank change is listed when the
  runner is on the first page of the state board (rankings.DEFAULT_LIMIT
  rows, the page a reader actually sees) this week or was last week. Rank
  movement past that is the noise of a long tail -- 400th to 310th moves a
  runner ninety places on a rating change of a point.

★ SENTENCES ARE race_story.py's KIND: each is a template that fires only
  when its facts are in the lists below it, every number read off a row
  the page shows. No "surged", no "dominant": places, points, percent.

! NO HISTORY, NO RANK LISTS. Until two weeks of snapshots exist the page
  shows breakouts and above-level, and says when the rank lists start.
"""
import datetime
import math

from markupsafe import Markup, escape

import ttlcache
from above_level import LOOKBACK_DAYS, SEASON_Q, _quantile, _seasonRows

TTL = 6 * 3600.0
WINDOW_DAYS = 7

# pool -> (breakouts level slug, words): the levels breakouts.py offers
LEVEL_OF = {"hs_m": "hs-boys", "hs_f": "hs-girls", "college_m": "college-men",
            "college_f": "college-women", "ms_m": "ms-boys", "ms_f": "ms-girls"}


def boardWindow():
    from rankings import DEFAULT_LIMIT
    return DEFAULT_LIMIT


# ------------------------------------------------------------------ #
#  pure rules (tests/test_movers.py)
# ------------------------------------------------------------------ #

def rankChanges(rows, window):
    """(risers, fallers, entered) from [{person_id, now, prev, ...}].

    risers   on the window now, and higher than last week (prev None is a
             new entry to the ranked board: listed under `entered`)
    fallers  on the window last week, and lower now (or off the board)
    entered  on the window now, and not on it last week"""
    risers, fallers, entered = [], [], []
    for r in rows:
        now, prev = r.get("now"), r.get("prev")
        on_now = now is not None and now <= window
        on_prev = prev is not None and prev <= window
        if on_now and not on_prev:
            entered.append(r)
        if on_now and prev is not None and prev > now:
            risers.append(dict(r, change=prev - now))
        if on_prev and (now is None or now > prev):
            fallers.append(dict(r, change=(now - prev) if now is not None else None))
    risers.sort(key=lambda r: (-r["change"], r["now"]))
    fallers.sort(key=lambda r: (r["change"] is None, -(r["change"] or 0), r["prev"]))
    entered.sort(key=lambda r: r["now"])
    return risers, fallers, entered


def aboveLevel(window_rows, history):
    """(sigma, rows above their level, biggest first, one per runner).

    window_rows: [{person_id, day, rating, ...}] the week's rated races
    history:     {person_id: [(day, rating)]} their races in the year before

    The rule is above_level.stampAboveLevel's: the level is the boards'
    season statistic over the runner's races in the LOOKBACK_DAYS before
    THIS race; sigma is the pooled spread of 100*ln(rating) over every
    runner's year; a race above level by more than sigma is listed."""
    ss, dof = 0.0, 0
    for pid in {r["person_id"] for r in window_rows}:
        logs = [100.0 * math.log(x) for x in
                _seasonRows([float(v) for _d, v in history.get(pid, [])]) if x > 0]
        if len(logs) >= 2:
            m = sum(logs) / len(logs)
            ss += sum((x - m) ** 2 for x in logs)
            dof += len(logs) - 1
    sigma = math.sqrt(ss / dof) if dof else None
    out = {}
    for r in window_rows:
        day = datetime.date.fromisoformat(str(r["day"])[:10])
        lo = (day - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat()
        prior = [float(v) for d, v in history.get(r["person_id"], [])
                 if lo <= str(d)[:10] < day.isoformat()]
        prior = _seasonRows(prior)
        if not prior:
            continue
        level = _quantile(prior, SEASON_Q)
        if not level:
            continue
        vs = 100.0 * (float(r["rating"]) / level - 1.0)
        if sigma and vs > sigma:
            best = out.get(r["person_id"])
            if best is None or vs > best["vs_level"]:
                out[r["person_id"]] = dict(r, level=level, vs_level=vs)
    return sigma, sorted(out.values(), key=lambda r: -r["vs_level"])


def _who(row):
    name = escape(row.get("name") or "Unknown")
    school = row.get("school")
    return Markup(f"<b>{name}</b> ({escape(school)})") if school else Markup(f"<b>{name}</b>")


def _list(rows):
    names = [_who(r) for r in rows]
    if len(names) == 1:
        return names[0]
    return Markup(", ").join(names[:-1]) + Markup(" and ") + names[-1]


_NUM_WORDS = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
              8: "Eight", 9: "Nine", 10: "Ten"}


def sentences(d, state_name, window):
    """The digest's lede: 0-4 sentences, each only when its row exists."""
    from race_story import ordinal
    out = []
    if d.get("risers"):
        r = d["risers"][0]
        # ! A TIE IS SAID: three runners up nineteen are not one "biggest"
        tied = sum(1 for x in d["risers"] if x["change"] == r["change"]) - 1
        out.append(Markup("{} rose {} place{} to {} in {}, {} biggest climb in the top {}.").format(
            _who(r), r["change"], "" if r["change"] == 1 else "s",
            ordinal(r["now"]), state_name,
            Markup(f"level with {tied} other{'s' if tied > 1 else ''} for the") if tied else "the",
            window))
    ent = [r for r in d.get("entered") or [] if r.get("prev") is not None]
    if ent:
        shown = ent[:3]
        lead = (_NUM_WORDS.get(len(ent), str(len(ent))) + " runners" if len(ent) > 1
                else "One runner")
        more = len(ent) - len(shown)
        out.append(Markup("{} moved into the top {}: {}{}.").format(
            lead, window, _list(shown),
            Markup(f" and {more} more") if more > 0 else ""))
    if d.get("breakouts"):
        b = d["breakouts"][0]
        out.append(Markup("The week's biggest breakout: {} ran {:.1f} at {}, {:.1f} points above "
                          "the median of their earlier races this season.").format(
            _who(b), float(b["speed_rating"]), b.get("meet_name") or "a meet",
            float(b["jump"])))
    if d.get("above"):
        a = d["above"][0]
        out.append(Markup("Furthest above their level: {} at {}, {:.1f}% over the level they "
                          "brought in (a normal day's swing is {:.1f}%).").format(
            _who(a), a.get("meet_name") or "a meet", a["vs_level"], d["sigma"]))
    return out


# ------------------------------------------------------------------ #
#  the database half
# ------------------------------------------------------------------ #

def _weeks(cur, sport, pool, year):
    cur.execute("""SELECT DISTINCT week FROM season_rank_weekly
                   WHERE sport = %s AND pool = %s AND year = %s
                   ORDER BY week DESC LIMIT 2""", (sport, pool, year))
    return [r["week"] for r in cur.fetchall()]


def _rankRows(cur, sport, pool, year, state, now, prev, window):
    cur.execute("""
        SELECT COALESCE(a.person_id, b.person_id) AS person_id,
               a.state_rank AS now, b.state_rank AS prev,
               COALESCE(a.school, b.school) AS school,
               a.mean_rating AS rating, b.mean_rating AS prev_rating
        FROM  (SELECT * FROM season_rank_weekly
               WHERE week = %(now)s AND sport = %(sp)s AND pool = %(p)s
                 AND year = %(y)s AND state = %(st)s) a
        FULL JOIN
              (SELECT * FROM season_rank_weekly
               WHERE week = %(prev)s AND sport = %(sp)s AND pool = %(p)s
                 AND year = %(y)s AND state = %(st)s) b
               ON a.person_id = b.person_id
        WHERE  a.state_rank <= %(w)s OR b.state_rank <= %(w)s
    """, {"now": now, "prev": prev, "sp": sport, "p": pool, "y": year,
          "st": state, "w": window})
    return [dict(r) for r in cur.fetchall()]


def _aboveRows(cur, sport, pool, year, state, lo, hi):
    """The week's rated races in the state and each runner's year before."""
    cur.execute("""
        SELECT DISTINCT ON (rr.person_id, rr.race_date, round(rr.time_seconds::numeric, 1))
               rr.result_id, rr.person_id, rr.race_date::text AS day,
               rr.speed_rating AS rating, rr.school, rr.meet_id, rr.div_id,
               rr.event_id
        FROM   ranking_results rr
        WHERE  rr.pool = %(p)s AND rr.sport = %(sp)s AND rr.year = %(y)s
          AND  rr.state = %(st)s AND rr.speed_rating IS NOT NULL
          AND  rr.race_date BETWEEN %(lo)s AND %(hi)s
        ORDER  BY rr.person_id, rr.race_date, round(rr.time_seconds::numeric, 1), rr.result_id
    """, {"p": pool, "sp": sport, "y": year, "st": state, "lo": lo, "hi": hi})
    window = [dict(r) for r in cur.fetchall()]
    if not window:
        return [], {}
    # ! ONE INDEX PROBE PER RUNNER, as above_level.py does it: the lateral
    #   pins the person index instead of a season-wide scan
    cur.execute("""
        SELECT h.person_id, h.race_date::text AS day, h.speed_rating AS rating
        FROM   unnest(%(pids)s::bigint[]) AS p(pid)
        CROSS  JOIN LATERAL (
               SELECT person_id, race_date, speed_rating
               FROM   ranking_results
               WHERE  person_id = p.pid AND sport = %(sp)s AND pool = %(p)s
                 AND  race_date <  %(hi)s::date
                 AND  race_date >= %(lo)s::date - %(back)s
                 AND  speed_rating IS NOT NULL) h
    """, {"pids": sorted({r["person_id"] for r in window}), "sp": sport, "p": pool,
          "lo": lo, "hi": hi, "back": LOOKBACK_DAYS})
    hist = {}
    for r in cur.fetchall():
        hist.setdefault(r["person_id"], []).append((r["day"], float(r["rating"])))
    return window, hist


def _fill(cur, rows, sport):
    """Names, and meet names for rows that carry a meet."""
    from team_tracker import _meetNames, _names
    names = _names(cur, {r["person_id"] for r in rows})
    meets = _meetNames(cur, {r["meet_id"] for r in rows if r.get("meet_id")})
    import breakouts as B
    for r in rows:
        r["name"] = names.get(r["person_id"], "Unknown")
        if r.get("meet_id"):
            r["meet_name"] = meets.get(r["meet_id"])
            r["race_href"] = B.raceHref(sport, r)


def digest(cur, sport, pool, state):
    """Everything the page renders, cached TTL per (sport, pool, state)."""
    def compute():
        import breakouts as B
        from predict import _currentSeason
        year = int(_currentSeason(cur, sport))
        window = boardWindow()
        out = {"year": year, "window": window, "weeks": [], "risers": [], "fallers": [],
               "entered": [], "breakouts": [], "above": [], "sigma": None,
               "history": False, "error": None}
        # rank changes, when two weeks are stored
        try:
            cur.execute("SAVEPOINT mv")
            cur.execute("SELECT to_regclass('public.season_rank_weekly') AS t")
            if cur.fetchone()["t"] is not None:
                weeks = _weeks(cur, sport, pool, year)
                out["weeks"] = [w.isoformat() for w in weeks]
                if len(weeks) == 2:
                    out["history"] = True
                    rows = _rankRows(cur, sport, pool, year, state, weeks[0], weeks[1], window)
                    _fill(cur, rows, sport)
                    out["risers"], out["fallers"], out["entered"] = rankChanges(rows, window)
            cur.execute("RELEASE SAVEPOINT mv")
        except Exception as exc:                        # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT mv")
            print(f"movers ranks {state}: {type(exc).__name__}: {exc}", flush=True)
            out["error"] = type(exc).__name__
        # breakouts: the precomputed table, else the live compute sliced
        level = LEVEL_OF.get(pool)
        if level:
            data = B.fromTable(cur, sport, level, WINDOW_DAYS, state)
            if data is None:
                data = B.compute(cur, sport, level, WINDOW_DAYS)
                data = dict(data, breakouts=B.pickRows(data["breakouts"], "jump", state))
            out["breakouts"] = data.get("breakouts") or []
            out["anchor"] = data.get("anchor")
        # above their level, the week up to the same anchor
        anchor = out.get("anchor")
        if anchor:
            hi = datetime.date.fromisoformat(str(anchor)[:10])
            lo = hi - datetime.timedelta(days=WINDOW_DAYS - 1)
            out["window_lo"] = lo.isoformat()
            try:
                cur.execute("SAVEPOINT mv2")
                win, hist = _aboveRows(cur, sport, pool, year, state, lo.isoformat(),
                                       hi.isoformat())
                out["sigma"], above = aboveLevel(win, hist)
                above = above[:boardWindow()]
                _fill(cur, above, sport)
                out["above"] = above
                cur.execute("RELEASE SAVEPOINT mv2")
            except Exception as exc:                    # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT mv2")
                print(f"movers above {state}: {type(exc).__name__}: {exc}", flush=True)
        return out
    val, stamp = ttlcache.get(("movers", sport, pool, state), compute, ttl=TTL,
                              ttl_of=lambda v: 300.0 if v.get("error") else TTL)
    return dict(val, computed_at=stamp)
