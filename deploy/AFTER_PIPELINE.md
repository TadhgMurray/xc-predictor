# After the pipeline finishes

Printed at the end of every deploy/run_pipeline.sh run. Delete an item once it's done.

1. Build the athlete-page indexes. Check disk first: each is roughly 2-3x the size of the plain meet index. Run in tmux; the site stays up.
       cd /srv/xc-predictor && git pull && set -a; . /etc/xc-predictor.env; set +a
       sudo -u postgres psql -d xc_predictor -c "SELECT pg_size_pretty(pg_relation_size('idx_results_meet')) AS xc, pg_size_pretty(pg_relation_size('idx_results_tf_meet')) AS tf;"
       df -h /var/lib/postgresql
       /srv/venv/bin/python scripts/add_page_indexes.py

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
