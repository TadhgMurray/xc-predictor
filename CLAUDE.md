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
