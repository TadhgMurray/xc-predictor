# Racecast: standing rules

- **Every rating a page shows is HS-equivalent** (owner, 2026-11-02: "make
  everything everywhere hs equivalent and make it a standing rule"). Print a
  rating through `rv(value, hs)` (templates/_scale.html), a stamped `hs_*` key
  (pool_view.stampBoardRows), or the `hs` filter / `hs_delta` global (app.py)
  with the rating's pool. JS boards read `rval(r, key)`. A value that is
  already HS gets a `{# hs-ok: why #}` note. tests/test_hs_everywhere.py
  enforces this on every template.
- **Emails never name who approved or declined something.** Decision mails
  (racecast/decision_email.py) come from the site's sender address only.
- **No page, email or outbound request carries the owner's own address.**
  Decision mails come from the site's sender; admin pages say "Approved" /
  "Rejected", not by whom; scrapers' User-Agents give racecast@racecast.co.
  tests/test_no_owner_address.py enforces it.
- **After every pipeline run, audit the all-time boards** (owner, 2026-11-06:
  "after every pipeline you should go to the all time boards to look at the
  top results, and if you find anything fake try to fix"). When the owner
  says a run finished, or a session starts after one: read
  `/api/rankings?board=performance&sport=XC&pool=<pool>&limit=25` (and
  `board=ability`) for hs_m, hs_f, college_m, college_f, ms_m, ms_f, with
  the X-Racecast-Agent header. Open every row that looks off -- a rating far
  above the athlete's other races, a one-race season, an open/community or
  splits meet, a lone copy of a race, a pool that does not fit the person --
  and find the CAUSE. Fix the class in code (a rule, a guard, a check that
  runs every night), never a hand pin for one row, then tell the owner what
  was fake, why, and what now catches it.
- **Nothing on the site says athletic.net or TFRRS** (owner, 2026-11-06).
  No page text, tooltip, placeholder or message; links name a meet's feed as
  `?src=1` / `?src=2` (app._PublicSrc turns them back into the internal
  keys). Code comments and internal keys are fine.
  tests/test_no_feed_names.py enforces it on every template and script.
