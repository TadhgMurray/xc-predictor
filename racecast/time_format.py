# Project: xc-predictor / racecast
# File:    time_format.py
# Purpose: the one server-side seconds -> clock formatter (sweep 2026-10-10).
#
# ⚠ WHY ITS OWN MODULE. app.format_time was the formatter, but compare.py and
#   panels.py cannot import app (it builds a Flask app at import time), so each
#   grew a twin -- and every twin split off the minutes BEFORE rounding the
#   seconds: 959.96 printed "15:60.0" on compiled races and /compare, and
#   app's own _format_fraction printed a 0.996 tail as ".10". One pure module,
#   imported by all three, is the only way they stay in agreement.
#
# ★ ROUND FIRST, THEN SPLIT. The value is rounded to hundredths -- the finest
#   precision any feed stores -- and only then divided into h/m/s, so a carry
#   lands in the minutes instead of printing a sixtieth second.

# ! Nothing at or past this is a running time: anet's 999999 non-finish and
#   friends (one leaked as '277:46:39', 2026-08-27, Walters State Opener).
NOT_A_TIME = 100_000

# anet TF's non-finish, 20,000 s (result_status.TF_SENTINEL) and its window
_TF_SENTINEL = 20000
_TF_SENTINEL_TOL = 1.0


def format_time(seconds):
    """Format raw seconds for display, keeping whatever precision the data has.

       11.24 -> '11.24'    14:58.2 -> '14:58.2'    1:05:03 -> '1:05:03'

    Precision is NOT fixed at two places. Printing '19:57.60' on a value stored
    as 1197.6 would claim hundredth accuracy the scrape never captured. We show
    the decimals that exist and nothing more.

    ! ROUNDED TO 2dp FIRST because `real` is a 4-byte float: a mark entered as
      11.24 can be stored as 11.239999771, and testing that raw would report
      false precision on effectively every row -- and 959.996 must carry to
      16:00, not print 15:59.10.
    """
    seconds = float(seconds)
    if seconds >= NOT_A_TIME or abs(seconds - _TF_SENTINEL) < _TF_SENTINEL_TOL:
        return " - "
    centis = int(round(seconds * 100))
    whole, hundredths = divmod(centis, 100)
    tail = ("" if hundredths == 0                        # whole second
            else f".{hundredths // 10}" if hundredths % 10 == 0   # '.6'
            else f".{hundredths:02d}")                   # '.24'

    if whole < 60:
        return f"{whole}{tail}"                          # sprint: '11.24'

    hours   = whole // 3600
    minutes = (whole % 3600) // 60
    secs    = whole % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}{tail}"
    return f"{minutes}:{secs:02d}{tail}"
