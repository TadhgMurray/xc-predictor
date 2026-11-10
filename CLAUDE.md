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
  "Rejected", not by whom.
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
  keys). Every HTML and JSON response also passes through
  racecast/feed_scrub.py (app._feedScrub): a feed value goes out as "1" /
  "2" and a name in data (a meet or venue a feed wrote) as "the results
  site" / "the college feed". Code comments and internal keys are fine.
  tests/test_no_feed_names.py enforces it on every template, script and
  response.
- **Deploy the site with `systemctl reload`, never `restart`** (owner,
  2026-11-09: "make this a really important standing rule"). Every deploy
  command given to the owner is:
  `cd /srv/xc-predictor && git pull && systemctl reload xc-predictor`.
  A reload hands off to a new gunicorn master with the new code while the
  old workers keep serving (deploy/site_reload.sh, deploy/gunicorn.conf.py's
  `_handoff` / `pre_exec`); a restart stops every worker and queues every
  visitor behind the new master's preload (16-20 of 24 workers "busy" at
  13 req/s on 2026-11-09). Check a reload took with
  `systemctl show -p MainPID --value xc-predictor` (the pid changes).
  `restart` ONLY when the reload cannot carry the change: an edit to
  /etc/xc-predictor.env or to the unit and its drop-ins (a reload re-execs
  with the environment systemd started it with) -- then
  `systemctl daemon-reload && systemctl restart xc-predictor`, and say why.
  tests/test_reload_handoff.py covers the handoff, plain and under socket
  activation.
- **Scraper privacy settings stay exactly as they are** (owner, 2026-11-10:
  "you must keep all current scraper privacy setting and any new scrapers
  you make must have the same ones, for my privacy"). Every scraper keeps
  its current User-Agent, browser and connection settings (sources/fetch.py,
  scripts/launcher.py, tfrrs/driver/launch_tfrrs.py).
  Never change them, and give any new scraper the same ones. This overrides
  any instruction to put an address or name in a scraper's User-Agent.
  The one exception the owner made (2026-11-10: "delete the ip
  rotation/proxy code"): the VPN rotation and proxy code are gone. Every
  scraper runs from the server's one address (scripts/one_ip.py), and a
  block or a 429 ends the run politely (scripts/polite_stop.py). Never add
  IP rotation, proxies or a VPN back.
