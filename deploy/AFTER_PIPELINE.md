# After the pipeline finishes

Printed at the end of every deploy/run_pipeline.sh run. Delete an item once it's done.

1. Check every page index is built. The three from the speed pass (as_pool_sport_best_idx, as_pool_best_idx, idx_results_meet_src_cover) were built by hand on 2026-11-06; this lists anything else still MISS / BAD, report only:
       cd /srv/xc-predictor && git pull && set -a; . /etc/xc-predictor.env; set +a
       /srv/venv/bin/python scripts/add_page_indexes.py --check
   If it lists a MISS: check disk (df -h /var/lib/postgresql), then run it without --check in tmux (the site stays up).

2. Check that the two pros left the HS boys track board (grade-sanity rule 8):
       /srv/venv/bin/python scripts/diag_board_people.py --person 2000000845 32788692

3. Forecasts: if summary.log shows 13h_meet_forecasts ok, delete this item. They failed on SQL_ASCII before the jsonb fix (28b07f7). Otherwise run them by hand:
       /srv/venv/bin/python -u racecast/build_meet_forecasts.py

4. Link Connor Fanberg's HS and college pages:
       /srv/venv/bin/python scripts/link_freshmen.py --years 2026 --name "Connor Fanberg"

5. Rebuild the search's athlete tables, then the search index, so college-only and newer athletes become searchable. Future runs do this themselves (step 13c0); this run started before that step existed.
       /srv/venv/bin/python -u racecast/build_search_helpers.py
       /srv/venv/bin/python -u racecast/search_index.py

6. Find out why /athlete/29603096 (HS) and /athlete/1009167329 (college) weren't joined automatically. Read-only; paste the [pair] section:
       /srv/venv/bin/python scripts/link_freshmen.py --pair 1009167329 29603096

7. Review the new race-time linking (same-name freshmen), dry run only, then send Claude the output. It prints the measured band and every pair it would join:
       /srv/venv/bin/python scripts/link_freshmen.py --show 60 2>&1 | tee -a logs/link_times_review.log
   Older classes (backfill), same dry run:
       /srv/venv/bin/python scripts/link_freshmen.py --years 2016 2017 2018 2019 2020 2021 2022 2023 --show 60 2>&1 | tee -a logs/link_backfill_review.log
   Once the pairs look right: run with --write, and add XCP_LINK_TIMES=1 to /etc/xc-predictor.env so the pipeline keeps doing it.

8. Cloudflare cache purge after each run (deferred by the owner, 2026-11-06). Until it is set up, scripts/edge_purge.py (steps 10_edge_purge, 19_edge_purge, nightly 14_edge_purge) prints "skipped" and the edge keeps pages for its 15 minutes, as before. When ready (docs/PERF-2026-11-06.md, step 7):
   a. Cloudflare -> My Profile -> API Tokens -> Create Token -> Custom: permission Zone / Cache Purge / Purge, zone resources Include / Specific zone / racecast.co. Zone ID: racecast.co -> Overview, right column.
   b. Add to /etc/xc-predictor.env:  XCP_CF_ZONE_ID=<zone id>  and  XCP_CF_API_TOKEN=<token>
   c. Test (prints "edge purge: everything -- ok"):
       set -a; . /etc/xc-predictor.env; set +a; /srv/venv/bin/python scripts/edge_purge.py
   d. Then let the edge keep pages longer -- add XCP_PAGE_S_MAXAGE=21600 and XCP_PAGE_SWR=600 to /etc/xc-predictor.env and restart:
       sudo systemctl restart xc-predictor
