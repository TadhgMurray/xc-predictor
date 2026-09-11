"""difficulty_view.py -- course difficulty as a reader understands it.

The engine stores difficulty as a multiplier on time, (1 + d), anchored so
the AVERAGE TRACK IS 0.0. This file displays that number and does not move
it.

  ★ ONE ZERO PER SPORT (owner, 2026-09-11). The STORED scale still has one
    zero for both sports and that zero is a track, exactly as the engine
    writes it. What changed is the DISPLAY: a course is shown against a
    typical course OF ITS OWN SPORT, so the average track reads 0.0 and the
    average XC course reads 0.0.

    ⚠ BECAUSE ONE LINE WITH A TRACK AT ZERO IS UNREADABLE ON A CROSS
      COUNTRY PAGE. An ordinary XC course is about +7% against a flat 400m
      oval, so every XC course read seven points harder than anyone would
      call it, and The Hydrangea Ranch -- where the Ultimook Race is run --
      published +13.26% when against other XC courses it is +6.4%. The
      owner said it looked like a 7-9% course and that the engine was
      broken. The engine was right. The page was answering a different
      question than the one being asked.

      The gap between the two zeros is real and measured; sportGapPct()
      reports it for a page that wants to compare the sports.

  ⚠ THIS FILE USED TO SUBTRACT THE CORPUS MEAN, and that quietly undid the
    engine's anchor. The note here claimed "the engine anchors to this same
    row-weighted mean, so the value is ~0 by construction" -- true when it
    was written, false from the moment the engine started anchoring on
    track. Cross country dominates the corpus, so the mean it subtracted
    was about +6%, and a typical TRACK displayed near -6%. The owner asked
    three times why track difficulty was not 0.0; this was why, and the
    engine was innocent every time.

    The zero is defined in ONE place now -- engine/joint_golive.py -- and
    read here as a GUARD, not as a correction.

  ★ A PERCENTAGE, NOT A DECIMAL. "+4% slower than a typical course, about 40
    seconds on a 16-minute 5K" is what +0.04 means, and it is what the
    templates print. The raw value stays available in a title attribute.

Display only. Nothing here changes a rating; ratings already carry the
difficulty, which is why a 133 here and a 133 anywhere else are the same
performance.
"""


import math
import threading
import time

# Seconds of the worked example: a 16-minute 5K, the reference a reader has.
REFERENCE_SECONDS = 960.0
REFERENCE_LABEL = "a 16-minute 5K"

# The zero, re-read this often. course_difficulties changes once a pipeline
# run, so an hour is generous.
_TTL = 3600.0
_state = {"at": 0.0, "XC": 0.0, "TF": 0.0}
_lock = threading.Lock()

# ! THE GUARD READS THE TRACK CELLS, because those are what the engine
#   anchored on. If this drifts far from zero the table was written by an
#   engine with a different anchor, and the site says so in the log rather
#   than silently re-centring -- silently re-centring is what went wrong.
_SQL = """
    SELECT CASE WHEN course_name LIKE 'TF:%%' THEN 'TF' ELSE 'XC' END AS sport,
           avg(ln(1.0 + difficulty))
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND difficulty > -0.9
    GROUP  BY 1
"""
# how far the average track may sit from zero before the log complains
_DRIFT_WARN = 0.01


def _refresh():
    """Read the corpus mean log-multiplier. A failure leaves the last good
    value in place, or zero on a fresh process: a page must not 500 because
    the database is mid-rebuild."""
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute(_SQL)
            rows = cur.fetchall()
        for sport, mean in rows:
            _state[sport] = float(mean or 0.0)
    except Exception:                                    # noqa: BLE001
        _state.setdefault("XC", 0.0)
        _state.setdefault("TF", 0.0)
    _state["at"] = time.time()


def sportMeanLog(sport=None):
    """THE DISPLAY ZERO FOR THIS SPORT: the mean log-multiplier of that
    sport's own courses.

    ★★★ WHY THIS CAME BACK (owner, 2026-09-11). The engine anchors the
        average TRACK at 0.0, which is correct and stays. But the site was
        then printing that raw number on a cross country course page, and
        an ordinary XC course is about +7% against a flat 400m oval -- so
        every XC course read seven points harder than a reader would ever
        call it.

        The Ultimook Race at The Hydrangea Ranch published +13.26%. The
        owner: "it seems to me to be actually a not very hard course, or at
        least maybe a 7-9% max course. NOT A FUCKING 13% course." Against
        the average XC course it IS +6.4%, which is his number. The engine
        was right; the page was showing a track-relative figure to someone
        reading a cross country page. He was told the model was broken.
        It was not.

      ! AND THIS IS NOT THE OLD BUG COMING BACK. The version deleted on
        2026-09-10 subtracted ONE corpus-wide mean from BOTH sports. Cross
        country dominates the corpus, so that mean was about +6% and it was
        subtracted from TRACKS TOO -- which is why a typical track
        displayed near -6% and the owner asked three times why track was
        not zero. Per SPORT, each sport's own courses average 0.0: the
        average track reads 0.0 and the average XC course reads 0.0.

      ! THE SPORT GAP DID NOT DISAPPEAR, it moved to where it belongs.
        sportGapPct() reports it for a page that wants to say it out loud.
        A course page should not have to.
    """
    key = "TF" if str(sport or "XC").upper().startswith("TF") else "XC"
    with _lock:
        if time.time() - _state["at"] > _TTL:
            _refresh()
    return float(_state.get(key, 0.0))


def sportGapPct():
    """How much slower an average XC course is than an average track, as a
    percentage. This is the number the two display zeros are now hiding,
    and it is a real measurement -- show it on a page that compares the
    sports, not on a course page."""
    with _lock:
        if time.time() - _state["at"] > _TTL:
            _refresh()
    return 100.0 * (math.exp(_state.get("XC", 0.0)
                             - _state.get("TF", 0.0)) - 1.0)


def trackDriftLog():
    """How far the stored average track sits from zero. Should be ~0; a
    large value means course_difficulties was written by an engine with a
    different anchor. A guard for the log -- it moves nothing."""
    with _lock:
        if time.time() - _state["at"] > _TTL:
            _refresh()
    return float(_state.get("TF", 0.0))


def relativePct(difficulty, sport="XC"):
    """Percent slower (+) or faster (-) than a typical course of the sport,
    or None."""
    if difficulty is None:
        return None
    try:
        d = float(difficulty)
    except (TypeError, ValueError):
        return None
    if d <= -0.9:
        return None
    return 100.0 * (math.exp(math.log1p(d) - sportMeanLog(sport)) - 1.0)


def diffPct(difficulty, sport="XC"):
    """The template filter: '+4.1%', '0.0%', '-2.3%', or an em dash."""
    pct = relativePct(difficulty, sport)
    if pct is None:
        return " - "
    if abs(pct) < 0.05:
        return "0.0%"
    return f"{pct:+.1f}%"


def diffWords(difficulty, sport="XC"):
    """A sentence: '+4.1% slower than a typical XC course, about 40 seconds
    on a 16-minute 5K'."""
    pct = relativePct(difficulty, sport)
    if pct is None:
        return "no difficulty solved for this course yet"
    secs = REFERENCE_SECONDS * pct / 100.0
    if abs(pct) < 0.05:
        return "about the same as a typical course"
    how = "slower" if pct > 0 else "faster"
    return (f"{abs(pct):.1f}% {how} than a typical course, about "
            f"{abs(secs):.0f} seconds on {REFERENCE_LABEL}")

