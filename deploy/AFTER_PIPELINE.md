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
