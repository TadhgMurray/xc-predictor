# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 6/21/2026
# File Title: scrape_tuning.py
# Purpose: One place for the scraper's pacing knobs. All sessions share a
#          single Mullvad exit IP at a time, so Cloudflare's per-IP rate
#          limiter (Error 1015) counts the COMBINED request rate of every
#          session. These constants hold that combined rate at a chosen,
#          sustainable level — and re-derive the per-request delay
#          automatically whenever the session count changes.

# Parallel sessions to run. Was 200 (burst-and-ban); 10-25 runs clean 24/7.
NUM_SESSIONS = 25

# The ONE number you normally tune. Aggregate GetResultsData3 requests/sec
# allowed against the single shared IP. Start low, raise it while watching
# your 1015 rate, then back off ~30% from where bans start.
TARGET_REQUESTS_PER_SEC_PER_IP = 3.0

# Rough time one GetResultsData3 fetch takes (network + parse). Subtracted
# from the budget so the sleep we add doesn't double-count the fetch time.
AVG_FETCH_SECONDS = 0.3

# A perfectly regular delay is both bot-like and causes synchronized bursts;
# ±25% jitter breaks both.
JITTER_FRACTION = 0.25

# _PER_MEET_DELAY_RANGE
# Purpose: (low, high) seconds for the between-meets sleep, per session. THE knob
#          — edit these two numbers to tune; nothing else changes. Starting wide
#          (3-6s) because the current 1-2s is over the per-session limit. Each of
#          ~200 sessions waits a random value in this range between meets, so
#          widening also lowers the COMBINED IP rate (watch both 429 types).
_PER_MEET_DELAY_RANGE = (1.5, 2.5)


# perRequestDelayRange
# Purpose: (low, high) seconds to feed into random.uniform() before each
#          rate-limited request, so all NUM_SESSIONS sessions COMBINED sit
#          at TARGET_REQUESTS_PER_SEC_PER_IP against the shared IP.
# Arguments: None — reads the module constants above.
# Output: (low, high) float tuple.
def perRequestDelayRange() -> tuple:

    # If all sessions together may do TARGET req/sec, each session may do
    # one request every (NUM_SESSIONS / TARGET) seconds — that's the budget.
    seconds_per_request = NUM_SESSIONS / TARGET_REQUESTS_PER_SEC_PER_IP

    # The fetch already eats AVG_FETCH_SECONDS of that; the rest is sleep.
    # max(..., 0.0) guards the case where so few sessions run that even
    # zero sleep can't slow them to the target.
    base = max(seconds_per_request - AVG_FETCH_SECONDS, 0.0)

    return base * (1 - JITTER_FRACTION), base * (1 + JITTER_FRACTION)

# perMeetDelayRange
# Purpose: Return the (low, high) between-meets delay range. A function (not a
#          bare constant import) so callers always read the current value and the
#          knob lives in exactly one place — mirrors perRequestDelayRange.
# Output:  (low, high) tuple of seconds.
def perMeetDelayRange():
    return _PER_MEET_DELAY_RANGE