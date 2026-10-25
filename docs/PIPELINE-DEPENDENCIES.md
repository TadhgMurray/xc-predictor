# Pipeline dependency map (deploy/run_pipeline.sh)

This maps every step of the nightly server pipeline to the tables and files it writes and reads. It was built by reading the code (written 2026-10-25), with no step timings and no database. Use it with the timings from the server to choose which steps to run side by side.

How to read the table:

- `results*` means `results` and `results_tf`. `meets*` means the meet tables the step names (`meets`, `meets_tf`, `meets_tf_meta`, `meets_tfrrs`).
- "(prev)" marks a table this step reads that a LATER step rebuilds. The step reads the previous run's copy, on purpose or not. Running it beside or after that later step changes its input.
- "private:" lists temp, unlogged or scratch tables that only that step uses.
- "swap" means the step builds `<t>_new` and renames it in (`dbfast.swapTable` / `database.swapTable`).
- "Depends on" names the nearest earlier step the step must wait for (it reads what that step writes, or a comment in the script requires the order). "none" means no earlier step in this run.
- "unclear" means the table set could not be fixed with confidence from the code; the reason is given.
- Steps marked BG run in the background today. Steps marked "gated" run only when the named setting allows it.

| Step | Command | Writes | Reads | Depends on | Notes |
|---|---|---|---|---|---|
| 00_integrity | `scripts/integrity_check.py` | nothing | every page of every table (amcheck) | none | A failure stops the run. Reads the whole database once. |
| 00b_run_snapshot | `scripts/run_changes.py --snapshot` | run_prev_athlete, run_prev_course, run_prev_meta | athlete_season (prev), course_difficulties (prev) | none | Must stay before 08 and 10_rankings_finish. Read by 17c0. |
| 00_meet_dates | `engine/meet_date_fix.py --write --show 20` | meet_date_fix; results*.date, meets.meet_date, meets_tf_meta.meet_date (anet rows) | results*, meets, meets_tf_meta, meet_date_fix; private: md_pm, md_top | 00_integrity | Comment: dates fixed before anything reads a season. Not on the --from always list. |
| 01_season_year | `engine/season_year.py` | nothing | nothing (self-check, no database) | none | Pure code check. |
| 04e_weather_grid | `backfill/atmost_era5_zarr.py` (WXPY venv); BG | weather_grid (INSERT) | results (anet dates), meets, meets_tf_meta, meets_tfrrs (GPS), weather_grid | 00_meet_dates | Gated: skipped if WXPY is missing. Collected by `bgjoin` before 04f. Reads tfrrs XC GPS that 06a fills later, so meets that 06a locates get weather next run. |
| 01a_person_probe | `scripts/person_redirects.py --snapshot` | person_probe (swap) | results*.person_id | none | Comment: must run before any step moves person ids (04a). Read by 13c0_person_redirects. |
| 02_drop_old | `engine/drop_old.py` | drops results_old, results_tf_old | those two tables | none | The backfill and go-live refuse a stale `_old`. Must precede 05 and 08. |
| 02b_tf_venues | `scripts/backfill_tf_venues.py --write` | meets_tf.venue_name; private: vn_loc, vn_gps | meets_tf, meets_tf_meta | none | Comment: before anything labels a venue. |
| 03_pro_flag | `engine/pro_flag.py --skip-dist --write` | pro_athlete_season, ncaa_first_season; private: tmp_* | results*, meets, meets_tf, grade_fix (prev), athlete_season (prev), athlete_named (prev), pair_athlete_season, pro_athlete_season | 00_meet_dates | Reads person ids before the 04a linkers move them. Readers: 05c, 07, 10, 13 panels. |
| 03b_age_bands | `engine/age_band_grades.py --write` | age_band_div, age_band_result | results*, meets, meets_tfrrs | 00_meet_dates | Comment: must precede 04_grade_sanity. Also read by 05 and 07. |
| 04a_link_tfrrs | `scripts/link_tfrrs_rows.py --apply` | results*.person_id, person_link_log, upload_link_report; private: tl_nat, tl_new, lf_*, lu_* | results*, athletes, athlete_season (prev), person_gender (prev), result_detach, meets | 01a_person_probe | Always runs. Start of the id-moving chain. |
| 04a2_link_teamless | `scripts/link_teamless.py --apply` | results*.person_id, athletes, person_redirect, person_link_log, teamless_merge, teamless_veto; private: tm_*, pm_move | results*, athletes, anet_team, meets, meets_tf, person_redirect | 04a_link_tfrrs | Comment: after 04a, before grade_sanity, twins and the pack. |
| 04a2b_feed_twins | `scripts/link_feed_twins.py --write` | results*.person_id, athletes, person_redirect, person_link_log, feed_twin_merge, feed_twin_veto; private: ft_*, fn_* | results*, athletes, person_gender (prev) | 04a2_link_teamless | Gated: XCP_LINK_FEED_TWINS=0 skips. Comment: after 04a/04a2. |
| 04a4_unsplit | `engine/unlink.py --rejoin --write` | results*.person_id, person_split, person_link_log; private: rj_*, tmp_split_moves | results*, person_split, person_link_log | 04a2b_feed_twins | Comment: before the pins. |
| 04a3_pins | `scripts/person_pins.py --apply` | results*.person_id, result_detach, person_link_log (creates grade_pin, school_pin, person_pin_log if absent) | results*, result_detach, athletes | 04a4_unsplit | Comment: after every linker. |
| 04a3b_merges | `scripts/link_profile_school.py --reapply` | results*.person_id, athletes, person_redirect, person_link_log, profile_school_*; private: ps_* | results*, athletes, profile_school_* | 04a3_pins | Last step that moves person ids. |
| 04_grade_sanity | `engine/grade_sanity.py --write` | grade_fix, grade_untrusted, ntschool; private: allraces, gradekind, tmp_* and others | results*, meets, meets_tfrrs, age_band_result, result_twin (prev, by design), dist_override (prev), grade_pin, race_top_level, anet_team, college_directory, athlete_season (prev) | 04a3b_merges, 03b_age_bands | Comment: result_twin is read from the previous run on purpose. A failure aborts before 05. |
| 04b_wheelchair | `engine/wheelchair_flag.py --write` | wheelchair_person, wheelchair_person_prev, wheelchair_race; private: wc_* (unlogged) | results*, meets, meets_tf, meets_tfrrs, athletes | 04a3b_merges | Always runs. Comment: before the pack. A failure aborts before 05. |
| 04c_twins | `engine/twin_flag.py --write` | result_twin (swap from result_twin_new); private: tw_*, lc_* | results*, meets, meets_tf, person_link_log, college_directory | 04a3b_merges | Comment: after 04a, before the pack. Keep after 04_grade_sanity (see above). A failure aborts before 05. |
| 04d_gender | `engine/person_gender.py --write` | person_gender (swap), person_gender_changed | results*, meets, meets_tfrrs, athletes | 04a3b_merges | A failure aborts before 05. person_gender_changed drives `XCP_BACKFILL_XC=changed`. |
| 04f_weather_fit_xc / _tf | `engine/fit_weather_correction.py --sport XC\|TF --refresh` | engine/data/weather_correction_{XC,TF}.pkl and a corpus cache .npz; private: wx_* | weather_grid, results*, meets, meets_tf_meta, meets_tfrrs, tfrrs_meet_geometry | 04e_weather_grid | Gated: only with XCP_WEATHER_FIT=1. Already a steps2 pair. Read by 05. |
| 04g0_gender_pin | `engine/gender_pace_pin.py --write` | engine/corrections.py (its block) | results, meets, athletes | none (needs 00_meet_dates) | Gated: XCP_AUDIT_OVERRIDES!=0 and corrections.py present. |
| 04g1_data_checks | `engine/data_checks.py --write` | data_issue, engine/corrections.py (its block); private: dc_* | results*, meets, meets_tfrrs, athletes, course_canonical (prev) | 04g0_gender_pin | Same file as 04g0, so in order. |
| 04g_audit_overrides | `engine/audit_overrides.py --rebuild --write` | engine/corrections.py (rewritten, .bak kept), override_proposals.csv; private: div_distance, ovr_ratings (unlogged) | dist_override (prev), results*.speed_rating (prev), meets, meets_tfrrs | 04g1_data_checks | Same file again. |
| 04g2_dump_overrides | `engine/dump_overrides.py` | dist_override, dist_drop (DROP then CREATE, not a swap) | engine/corrections.py | 04g_audit_overrides | Runs only if 04g succeeded. dist_override is read by 04_grade_sanity (prev), 06c, 07, 09b, 10 and others. |
| 05_backfill_xc / _tf | `backfill/backfill_normalize.py --sport XC\|TF --apply [--only-changed]` | results / results_tf (new table and rename; normalized_time), engine/data/distance_applied_{sport}.json, weather_applied_{sport}.pkl, backfill_wx_{sport}.pkl, suspects_{xc,tf}.txt (repo root); private: _wcp, tmp_drop_keys, bf_* | engine/corrections.py, grade_fix, age_band_result, person_gender, person_gender_changed, athlete_season_level, college_first_season, weather_grid, weather_correction_{sport}.pkl, distance_ability.pkl, meets*, tfrrs_meet_geometry, athletes | 04g2_dump_overrides (and 04_grade_sanity, 04d, 04e/04f, 03b) | Already a steps2 pair (one table each). Comment: after grade_sanity. Skipped with --skip-backfill. A failure aborts the run. athlete_season_level and college_first_season are not built by any step. |
| 05b_anchor_repair_xc / _tf | `engine/anchor_repair.py --sport XC\|TF --apply` | results*.normalized_time (UPDATE); private: ar_group | results*, results*.rating_pool (prev), ranking_results (prev), meets, dist_override | 05_backfill | Already a steps2 pair. Comment: after 05, before the pack. Always runs. |
| 05c_pro_ability | `engine/build_pro_ability.py --write` | pro_ability_season | the solve's loader (speed_ratings_db.streamResults): results*, meets*, athletes, person_gender, wheelchair_person, result_twin, impossible_result (prev), dist_override, course_canonical (prev), age_band_result, pro_athlete_season, grade_fix | 05b_anchor_repair | Comment: after 05 and before 07. Read by 07 (pool_resolve) and 13 panels. |
| 06_clear_cache | inline `rm` (not a helper step) | deletes engine/data/packed_XC_TF.npz, pair_solve_cache.npz | nothing | none | Only when `--from` <= 7. |
| 06_tfrrs_meets | `scripts/land_tfrrs_meet_names.py --apply` | meets_tf (INSERT tfrrs rows), results_tf.div_id (once, only NULL rows) | results_tf, meets_tf, meets_tfrrs, tfrrs_meet_geometry | none (needs 00_meet_dates) | Comment: before the pack. |
| 06a_tfrrs_location | `scripts/backfill_tfrrs_location.py --apply --out $LOGDIR` | meets_tfrrs.location_id / gps_* (XC rows only); a leftovers list in $LOGDIR | meets, meets_tfrrs, results | none | Comment: "first", before 06b. |
| 06b_course_canonical | `scripts/build_course_canonical.py --incremental --apply` | course_canonical (DROP then CREATE in one transaction) | meets, meets_tfrrs (incl. 06a's GPS), course_canonical | 06a_tfrrs_location | Comment: before the pack. |
| 06c_impossible | `engine/impossible_race.py --write` | impossible_race, impossible_result (swap) | results*, meets, meets_tf, meets_tfrrs, dist_override, athletes | 06_tfrrs_meets (and 04g2) | Read by 07, 09d, 10, 13j, 17d. |
| 07_pack | `engine/speed_ratings.py --sport merged --cache --pack-only` | engine/data/packed_XC_TF.npz | the loader tables (as 05c) plus grade_fix, grade_untrusted, pro_athlete_season, pro_ability_season, athlete_season_level, college_first_season, upperclass_first_season, race_top_level, course_canonical, wheelchair_person, impossible_result | 06c_impossible (and 06b, 06_tfrrs_meets, 05c) | Writes no table in --pack-only mode. |
| 08_golive (joint) | `engine/run_joint.py --golive --probes 0 --outer 5 ...` (about 40 XCP_* flags) | results* (speed_rating, rating_pool; heap rebuild), course_difficulties, athlete_ratings, results speed-rating backup tables, distance_offset, engine_scale, race_day_suspect, race_day_suspect_division, sport_gain, race_day_effect (swap); engine/data/joint_difficulty.npz, joint_difficulty_state.npz, pair_difficulty.npz | packed_XC_TF.npz, venue_elevation, results/meets (distance pins) | 07_pack | Default branch (XCP_JOINT_LIVE=1). A failure aborts the run. |
| 08a_holdout | `engine/run_joint.py --holdout-only --holdout-kind race ...` | nothing (log only) | packed_XC_TF.npz, venue_elevation | 07_pack | BG evidence chain at nice 19, started after 08. Gated: XCP_EVIDENCE=0 skips the chain. Log read by 17c. |
| 08a_sport_holdout | `engine/run_joint.py --holdout-only --holdout-kind sport ...` | nothing (log only) | as 08a | 07_pack | Evidence chain. Log read by 17c. |
| 08a_distance_holdout | `engine/run_joint.py --holdout-only --holdout-kind distance ...` | nothing (log only) | as 08a | 07_pack | Evidence chain. Gated: XCP_DIST_HOLDOUT=0 skips. |
| 08b_ladder | `scripts/ablation_ladder.py --pct 15 --outer 5 --rung-timeout 7200` | engine/data/ladder_logs/*.log, *_holdout.npz, ablation_ladder.json; the shell writes engine/data/ladder_stamp | packed_XC_TF.npz; DB reads unclear (each rung is a run_joint subprocess) | 07_pack | Evidence chain. Gated: runs only when the model stamp changed and at most weekly (XCP_LADDER). |
| 08d_diagnose | `scripts/diagnose.py --only indoor,tracks,holdout --out-dir $LOGDIR ...` | $LOGDIR/{bracket_holdout,indoor,tracks}.txt | packed_XC_TF.npz, joint_difficulty.npz, course_canonical (names) | 08_golive | Evidence chain. Gated: XCP_BRACKET=0 skips. |
| 08c_anchor_check | `engine/anchor_check.py --sport TF --pct 1` | nothing | results_tf (1% sample), ranking_results (live), meets_tf, dist_override | 08_golive | Evidence chain. Its read of ranking_results can hold up the 10_rankings_finish swap. |
| 08_golive (legacy) | `engine/linkage_check.py --golive --split` | results*.speed_rating, pair_* tables, pair_difficulty.npz; full set unclear (pair_golive / pair_write modules) | packed_XC_TF.npz | 07_pack | Only when XCP_JOINT_LIVE=0. |
| 08b_joint_shadow | `engine/run_joint.py --holdout --probes 16` | engine/data/joint_difficulty.npz (+ state npz) | packed_XC_TF.npz | 08_golive (legacy) | Only when XCP_JOINT_LIVE=0 and XCP_JOINT=1. |
| 09_tilt | `engine/apply_tilt.py --refresh --write` | results.speed_rating (via tilt_new, merge_column heap rebuild), results_rating_pretilt | results, course_difficulties, meets | 08_golive (legacy) | Only when XCP_JOINT_LIVE=0. |
| 09b_fill | `engine/fill_ratings.py` | results*.speed_rating for unrated rows (UPDATE) | results*, ranking_results (prev), course_difficulties, course_canonical, dist_override, meets*, plus the board query's tables (rating_outlier (prev), school_name_alias (prev), result_twin, wheelchair_person, impossible_result) | 08_golive | Comment: everything below republishes speed_rating. |
| 09c_school_names | `scripts/merge_school_names.py --write` | school_name_alias | results*, anet_team | 04a3b_merges | Comment: before the boards (10). Reads no rating. |
| 09d_outliers | `engine/rating_outliers.py --write --show 10 --streams $XCP_STREAMS` | rating_outlier, rating_outlier_calib; private: ro_*, scratch tables | results*.speed_rating, result_twin, impossible_result, rating_outlier (prev) | 09b_fill | Comment: after the last rating (09b), before the boards. Uses XCP_STREAMS parallel scans itself. |
| 10_rankings_prepare | `racecast/build_ranking_results.py --stage prepare` | ranking_results_new, athlete_season_new (shadows) | none of note | 09d_outliers, 09c_school_names | |
| 10_rankings_xc_a / xc_b / tf_a / tf_b | `racecast/build_ranking_results.py --stage stream --sport XC\|TF --until\|--since $XCP_RANK_SEAM` | ranking_results_new, athlete_season_new (COPY) | results*, meets*, athletes, grade_fix, pro_athlete_season, athlete_season_level, college_first_season, dist_override, result_twin, wheelchair_person, impossible_result, rating_outlier, school_name_alias, person_gender, school_identity (prev), school_unit (prev) | 10_rankings_prepare | Already parallel (stepsN, XCP_STREAMS at a time). The four big row walks. |
| 10_rankings_finish | `racecast/build_ranking_results.py --stage finish` | ranking_results, athlete_season (swap), board_size; private: tmp_campus* | the shadows, school_identity (prev) | 10_rankings_* streams | Readers start here: almost every later step. |
| 10f2_projection | `racecast/build_recruit_projection.py`; BG | recruit_projection (swap) | athlete_season, model/data files; other predict.py reads unclear | 10_rankings_finish | Leaf: only site pages read it. Commits per slice so 11b is not blocked. Collected by the final bgwait. |
| 10a_board_sanity | `scripts/board_sanity.py --top 60` | nothing | athlete_season, ranking_results, athletes, engine/data/joint_difficulty.npz | 10_rankings_finish | Pure reader. A hard finding fails the step but does not stop the run. Log read by 17c and 17d. |
| 10b0_tfrrs_link | `scripts/link_tfrrs_to_anet.py --write` | school_team_link; private: ltl_* | results*, anet_team, school_team_link | 04a3b_merges | Comment: before 10b. Reads no rating or board. |
| 10b_school_ids | `racecast/build_school_identity.py` | school_identity, school_state_alias, school_level, school_athlete_state, person_home_state (all via `_new` and rename); private: si_* | school_team_link, athlete_season, ranking_results, meets, meets_tfrrs, anet_team, college_directory | 10b0_tfrrs_link, 10_rankings_finish | Read by 10f, 11, 13c, 13d, 13j and by next run's 10. |
| 10c_gap | `scripts/measure_sport_gap.py` (with `--emit` when XCP_JOINT_LIVE=0) | nothing under the joint solve (temp sg_* only); with --emit, engine/data/sport_gap_bbar.json | results*, meets_tf, ranking_results | 10_rankings_finish | Telemetry only under the default branch. |
| 10d_school_units | `racecast/build_school_units.py` | school_unit (swap) | results*, meets*, ranking_results, college_directory | 10_rankings_finish | Does not read school_identity (traced through check_school_units). Read by 10f, 13c, 13i and next run's 10. |
| 10e_meet_units | `racecast/build_meet_units.py` | meet_unit (swap) | meets, meets_tf, meets_tfrrs | 06_tfrrs_meets | Nothing later in the run reads meet_unit (site pages, and 13i's cuts.py only). |
| 10f_recruits | `racecast/build_recruiting.py` | college_recruit, college_recruit_meta (swap) | athlete_season, school_identity, school_unit | 10b_school_ids, 10d_school_units | Comment: reads 10b and 10d. |
| 10f3_comps | `racecast/build_comps.py` | season_comps, season_comps_meta (swap); private: cs_seasons | athlete_season, college_recruit, ranking_results | 10f_recruits | Comment: in line, not BG, because its transaction would stall 11b. |
| 10g_season_ranks | `racecast/build_season_ranks.py` | season_rank (swap) | athlete_season (and a rankings spot check) | 10_rankings_finish | |
| 10g2_rank_snapshot | `racecast/build_rank_snapshot.py` | season_rank_weekly | season_rank, athlete_season | 10g_season_ranks | Comment: reads only season_rank. |
| 11_teams | `racecast/build_team_season.py` | team_season (swap) | athlete_season, ranking_results, school_identity, school_level, school_athlete_state, person_home_state | 10b_school_ids | |
| 11c_ncaa | `racecast/build_ncaa_projection.py` | ncaa_projection | athlete_season, results, meets_tfrrs, dist_override; rest unclear (ncaa_rules, race_sim, predict) | 10_rankings_finish | Gated: off unless XCP_NCAA=1. |
| 11b_indexes | `scripts/add_page_indexes.py` | indexes on results*, ranking_results, athlete_season, meets*, athletes (CREATE INDEX CONCURRENTLY) | catalog | 10_rankings_finish | Comment: before the course pages. CONCURRENTLY waits for every older open transaction, so nothing long may run beside it. |
| 12_courses | `racecast/build_course_rank.py` | course_rank (swap) | course_difficulties, meets, results | 09b_fill (ratings), 11b_indexes (placement) | Reads no board table. |
| 12b_prepare | `racecast/build_course_boards.py --prepare` | course_boards_new | nothing | 11b_indexes | |
| 12b_course_pages | `racecast/build_course_boards.py --limit 1200 --shard k/n` | course_boards_new (INSERT) | meets, results (course rows), course_difficulties, course_canonical, ranking_results, pool constants (see 13b) | 12b_prepare | Already sharded (XCP_COURSE_SHARDS, default 3). Comment: after 11b. |
| 12b_finish | `racecast/build_course_boards.py --finish` | course_boards (swap) | course_boards_new | 12b_course_pages | |
| 13j_record_books | `racecast/build_record_books.py --max-schools 4000`; BG | record_books, record_books_meta | athlete_season, ranking_results, school_identity, meets; rest unclear (school_prs, teams modules) | 12b_finish (comment) | Gated: off unless XCP_RECORD_BOOKS=1. Comment: start after 11b and 12b. |
| 13_panels_xc / _tf | `racecast/panels.py --sport XC\|TF` | homepage_panels, homepage_meta, homepage_recent (per-sport rows); private: ath | athlete_season, ranking_results, results*, meets*, course_difficulties, grade_fix, pro_athlete_season, athlete_season_level, athletes | 10_rankings_finish | Already a steps2 pair. Both write the same three tables, scoped by sport. |
| 13f_breakouts | `racecast/build_breakouts.py` | breakout_rows, breakout_meta (swap); private: bo_season | ranking_results, homepage_recent, meets* | 13_panels | Comment: reads the panels' newest dates. |
| 13g_social | `racecast/build_social.py` | social_post; PNG/HTML/TXT under XCP_SOCIAL_DIR | season_rank_weekly, breakout_rows, social_post; predict reads unclear | 13f_breakouts, 10g2_rank_snapshot | Gated: on unless XCP_SOCIAL=0. Report step. |
| 13i_state_odds | `racecast/build_state_odds.py` | state_odds_athlete, state_odds_team, state_odds_meta | school_unit, state_odds_meta; rest unclear (projections, cuts, landing, predict) | 10d_school_units | Gated: off unless XCP_STATE_ODDS=1. |
| 13b_pool_consts | `scripts/warm_pool_constants.py` | racecast/pool_constants_cache.json | ranking_results, results* (samples), engine_scale | 10_rankings_finish | pool_view readers (12b, 13 panels) use engine_scale in one-scale mode, otherwise the previous sidecar. Which applies on the server is unclear. |
| 13c0_person_redirects | `scripts/person_redirects.py --resolve` | person_redirect | person_probe, results*, athletes, person_redirect | 04a3b_merges (and 01a) | Needs every id move done. Read by 13d. |
| 13c0_search_helpers | `racecast/build_search_helpers.py` | athlete_named, athlete_agg, athlete_school (swap); private: tmp_person_scan, tmp_profile_name | results* (person, date, school, name), athletes | 04a3b_merges | Comment: before 13c. Reads no rating. |
| 13c_search_index | `racecast/search_index.py` | search_index (swap), meet_agg_xc, meet_agg_tf | athlete_named, athlete_agg, athlete_school, school_identity, school_unit, person_home_state, person_optout, result_twin, results*, meets*, course_difficulties | 13c0_search_helpers, 10b, 10d | Builds its indexes on its own shadow, not CONCURRENTLY; the script comment about "13c's concurrent index build" looks stale. |
| 13d_sitemap | `racecast/build_sitemap.py` | racecast/static/sitemaps/* | meet_agg_xc/tf, school_identity, course_difficulties, person_redirect, person_optout, results*, athlete_season | 13c_search_index, 13c0_person_redirects | |
| 13e_indexnow | `scripts/indexnow_submit.py` | its state file; network calls | racecast/static/sitemaps/* | 13d_sitemap | Comment: after the sitemap. |
| 13g_follow_alerts | `racecast/follow_alerts.py --send --prewarm` | alert_item, alert_pref, account_follow, follow_snapshot; sends email | breakout_rows, ranking_results, season_rank, team_season, account tables; rest unclear | 13f_breakouts (comment) | Gated: off unless XCP_ALERTS=1. |
| 13h_meet_forecasts | `racecast/build_meet_forecasts.py` | meet_forecast | results, athlete_season, ranking_results, meets*; predict reads unclear | 10_rankings_finish | Gated: off unless XCP_FORECASTS=1. Comment: nothing after it reads the table. |
| 15_rowguard_diag_xc / _tf | `scripts/diag_suspects.py --sport XC\|TF` | scripts/suspects_{div,row}_{xc,tf}.tsv, suspects_{xc,tf}.txt, result_drop_{xc,tf}.py; private: _base, _ranked, _resid | results / results_tf (normalized_time, speed_rating), meets (XC), engine/corrections.py | 09b_fill | Already a steps2 pair. |
| 15_rowguard_triage_xc | `scripts/triage_suspects.py --sport XC` | scripts/*_xc.py, scripts/triage_*_xc.tsv; private: _flagged | scripts/suspects_*_xc.tsv, results | 15_rowguard_diag_xc | Files are per sport. |
| 15_rowguard_triage_tf | `scripts/triage_suspects.py --sport TF` | scripts/*_tf.py, scripts/triage_*_tf.tsv; private: _flagged | scripts/suspects_*_tf.tsv, results_tf | 15_rowguard_diag_tf | Runs after triage_xc today, but shares nothing with it. |
| 16_rowguard_apply | `scripts/apply_triage.py` | engine/corrections.py (append, .bak kept) | scripts/*_{xc,tf}.py | both triage steps | This run's later readers of corrections.py: 16c, 16d (predict parses it lazily), 17_checklist. Next run's 04g/05 read it. |
| 17d_watchdog | `racecast/watchdog.py --log-dir $LOGDIR --kind full ... --send --ensure-indexes`; BG | watchdog_run, watchdog_finding, watchdog_board_top, $LOGDIR/WATCHDOG.txt; BRIN indexes (CONCURRENTLY) | athlete_season, results*, breakout_rows, impossible_result, result_twin, dist_override, person_link_log, profile_school_veto, $LOGDIR logs (10a, summary) | 16_rowguard_apply | Gated: XCP_WATCHDOG=0 skips. Comment: every writer of reader-facing tables is done by here. |
| 17_checklist | `scripts/run_checklist.py` | nothing (temp _chk_*, _ck_*) | results*, meets*, dist_override, dist_drop, wheelchair_person, ranking_results, engine/corrections.py, scripts/canaries.json | 16_rowguard_apply | The evidence chain is waited for just before this step. The script comment says 17_checklist reads the evidence logs, but run_checklist.py opens no log file, so that wait seems to matter only for 17c. |
| 17b_status | `scripts/print_status.py` | site_status_snapshot | athletes, meet_queue, results* counts, watchdog_* tables, logs/*/summary.log | 17_checklist (placement only) | Reads watchdog tables that the BG 17d may be writing. |
| 16b_lacctic | `scripts/lacctic_compare.py --race ... --runners 150` | its HTTP cache json; log | results (and conversions module reads, unclear); public LACCTiC API | 08_golive (ratings) | Gated: XCP_LACCTIC=0 skips. Log read by 17c. Network-bound. |
| 16c_kailo_record | `scripts/kailo_compare.py --record` | engine/data/kailo/<race>.json | athletes, meets_tfrrs, results; predict reads unclear; Kailo API | 10_rankings_finish | Gated: XCP_KAILO=0 skips. Parses corrections.py after 16 has changed it. |
| 16d_backtest | `scripts/backtest_predictions.py --from ... --to ... --divs 40 --no-breakdown` | log only | results*, result_twin; predict reads unclear | 09b_fill | Gated: XCP_BACKTEST=0 skips. Log read by 17c. |
| 17a_page_timing | `scripts/page_timing.py --write --n 6` | page_timing | unclear: renders every page kind through racecast/app.py, so most site tables | everything that builds pages | Gated: XCP_PAGE_TIMING=0 skips. It measures page speed, so anything running beside it skews its numbers. |
| 17c0_run_changes | `scripts/run_changes.py --report --log-dir $LOGDIR` | engine/data/run_changes/<run>.json | run_prev_*, athlete_season, course_difficulties, results* (scraped_at counts) | 10_rankings_finish, 00b_run_snapshot | |
| 17c_report | `scripts/run_report.py --log-dir $LOGDIR` | $LOGDIR/REPORT.txt, engine/data/run_history.jsonl | logs of 04f, 08_golive, 08a, 08a_sport, 10a, 16b, 16d; course_difficulties, results_tf | 16d_backtest, 16b_lacctic, evidence chain | |
| 17e_scoreboard | `scripts/scoreboard.py --log-dir $LOGDIR` | engine/data/scoreboard.jsonl, $LOGDIR/SCOREBOARD.txt | ranking_results, venue_elevation, diag_* module queries | 10_rankings_finish | Gated: XCP_SCOREBOARD=0 skips. |
| 18_vacuum | `scripts/gentle_vacuum.py` | VACUUM (ANALYZE) ranking_results, athlete_season | those two | all board writers | Comment: last, after everything the site waits for. |
| 18b_integrity_boards | `scripts/integrity_check.py --tables ranking_results,athlete_season` | nothing | every page of the two tables | 18_vacuum | Comment: the vacuum is the last writer of those pages. |

## Candidate parallel groups

### Already run in parallel today

- steps2: 04f_weather_fit_xc + _tf, 05_backfill_xc + _tf, 05b_anchor_repair_xc + _tf, 13_panels_xc + _tf, 15_rowguard_diag_xc + _tf.
- stepsN: 10_rankings_xc_a, xc_b, tf_a, tf_b (XCP_STREAMS at a time, default 4).
- shards: 12b_course_pages (XCP_COURSE_SHARDS, default 3).
- bgstep: 04e_weather_grid (joined before 04f), 10f2_projection, 13j_record_books (gated), 17d_watchdog.
- Evidence chain in one background subshell, in order: 08a_holdout, 08a_sport_holdout, 08a_distance_holdout, 08b_ladder, 08d_diagnose, 08c_anchor_check.
- 09d_outliers runs XCP_STREAMS parallel scans inside itself.

### Risks that apply to every group

- Row walks. Most steps scan `results` and `results_tf`. The pipeline limits these walks to XCP_STREAMS at a time because they slow the site. A new group should count its scanners against that limit.
- Shared maintenance flag. Every swap that falls back to "flagged" mode creates and then deletes one file (`/var/tmp/racecast-maintenance`). It is not reference-counted. If two swaps fall back at once, the first to finish removes the flag while the other still holds its lock. This only happens after 90 quiet tries fail, so it is rare.
- Swaps wait for readers. A swap needs an exclusive lock on the live table. A long read of that table from a parallel step delays the swap.
- 11b_indexes uses CREATE INDEX CONCURRENTLY. It waits for every older open transaction in the database. Nothing long may run beside it.
- Failure gates. The script aborts after the people steps (03-04d), after 05_backfill and after 08_golive. A group that crosses a gate changes what runs before the abort.

### Groups, most promising first

1. **After 10_rankings_finish: 10a_board_sanity, 10b0_tfrrs_link then 10b_school_ids, 10c_gap, 10d_school_units, 10e_meet_units, 10g_season_ranks then 10g2_rank_snapshot.**
   Safe because their writes are disjoint (nothing, school_team_link, school_identity and friends, temp only, school_unit, meet_unit, season_rank, season_rank_weekly). Their shared reads (ranking_results, athlete_season) are finished and stable. 10d does not read school_identity, so it need not wait for 10b. 10e and 10b0 do not need the boards at all.
   Then run 11_teams (needs 10b) beside 10f_recruits then 10f3_comps (needs 10b and 10d).
   Risk: 10b0, 10b, 10c and 10d each scan results* or ranking_results, so cap them by XCP_STREAMS. Every one of these must finish before 11b starts, because 11b waits on open transactions. 10f2 already runs beside them in the background.

2. **After 11b_indexes: 12_courses, the 12b chain (prepare, shards, finish), 13_panels (xc + tf) then 13f_breakouts then 13g_social, 13b_pool_consts, 13c0_person_redirects, and 13c0_search_helpers then 13c_search_index then 13d_sitemap then 13e_indexnow.**
   Safe because each writes its own tables or files (course_rank, course_boards, homepage_*, breakout_*, social_post, the constants file, person_redirect, athlete_* helpers, search_index and meet_agg_*, sitemap files). None of them writes ranking_results, athlete_season, results* or course_difficulties, which they share as readers. 13d needs both 13c and 13c0_person_redirects. 13g_social also needs 10g2.
   Risk: this is the biggest pile of row walks in the run (12b already uses three workers). Several steps swap at the end, so the maintenance-flag risk applies. 13b refreshes a constants file that 12b and the panels may read through pool_view; today they run before 13b. Check `pool_view._oneScale()` on the server before moving 13b.

3. **The people verdicts: 04_grade_sanity, 04b_wheelchair and 04d_gender together, then 04c_twins.**
   Safe because all three read the person ids that 04a3b leaves and write disjoint tables (grade_fix and grade_untrusted, wheelchair_*, person_gender). 04c can join 04b and 04d but not 04_grade_sanity: grade_sanity reads result_twin from the previous run by design, and 04c swaps that table.
   Risk: all four scan results* fully, so they compete on the same scans. Wait for all of them before the people-failure gate, so the gate still sees every failure. 04b is on the always-run list and 04_grade_sanity is not; keep both rules when grouping.

4. **The rowguard, moved up beside the board steps; and 15_rowguard_triage_xc beside 15_rowguard_triage_tf.**
   The two triage steps read and write per-sport files and per-sport tables only, so they can always be a steps2 pair. The diag steps need only the final ratings (09b) and normalized_time (05b), and write only files under scripts/, so the whole 15 chain could run beside groups 1 and 2.
   Risk: the diag steps are full row walks. Keep 16_rowguard_apply where it is: it rewrites engine/corrections.py, and steps that use racecast/predict.py parse that file lazily, so a parallel reader could read it half-written.

5. **The report steps: 16b_lacctic, 16c_kailo_record, 16d_backtest, 17_checklist, 17b_status, 17c0_run_changes and 17e_scoreboard together, then 17c_report.**
   Safe because none writes a table another reads; they write logs, json files, site_status_snapshot or temp tables. 16b and 16c are mostly network waits. 17c reads the logs of 16b, 16d and the evidence chain, so it goes last.
   Risk: 17b_status and 16d do heavy reads. Keep 17a_page_timing alone: it measures page speed, and parallel load skews it.

6. **The pre-pack block: 06_tfrrs_meets beside 06a_tfrrs_location, then 06b_course_canonical beside 06c_impossible.**
   Safe because 06 writes meets_tf and 06a writes only the XC location columns of meets_tfrrs; 06b and 06c write different tables. 06b needs 06a's GPS; 06c needs 06's meets_tf rows.
   Risk: 06a adds a column with ALTER TABLE on a database that lacks it (once ever), which locks meets_tfrrs briefly. Do not move 05c into this group: its loader reads course_canonical and impossible_result, which 06b drops and recreates and 06c swaps.

7. **09b_fill beside 09c_school_names (and 09c beside 09d_outliers).**
   Safe because 09c reads only school, person and team columns and writes only school_name_alias. 09b updates speed_rating rows, which 09c does not read. 09c needs nothing after 04a3b.
   Risk: both scan results*. 09d must still follow 09b.

8. **The start of the run: 00b_run_snapshot, 00_meet_dates, 01_season_year, 01a_person_probe and 02_drop_old together; then 02b_tf_venues, 03_pro_flag and 03b_age_bands together.**
   Safe because the writes are disjoint (run_prev_*, dates, nothing, person_probe, dropping the _old copies; then meets_tf.venue_name, pro tables, age band tables). 01a reads only person ids, which 00_meet_dates does not touch. 03 and 03b read dates, so they wait for 00_meet_dates.
   Risk: 00_integrity must stay first and alone, because it is the gate. 04e must still start after 00_meet_dates. 03 and 03b both scan results*.

9. **04f_weather_fit (opt-in) beside 04g0, 04g1 and 04g_audit_overrides.**
   Safe because 04f writes only its pickle files and the 04g steps write engine/corrections.py, data_issue and scratch tables.
   Risk: the 04g steps must stay in order (one file). 04g2_dump_overrides must not overlap 04_grade_sanity, which reads dist_override while 04g2 drops and recreates it. Starting the 04g steps before the people gate means corrections.py may change on a run that then aborts.

## Other findings from the mapping

- 04e_weather_grid reads tfrrs XC GPS from meets_tfrrs, but 06a_tfrrs_location fills that GPS later. Meets that 06a locates get no weather in this run's backfill (05); they get it next run.
- 10_rankings reads school_identity and school_unit from the previous run (10b and 10d rebuild them after). The code says so for school_identity.
- 09b_fill and 05b_anchor_repair read the previous run's ranking_results.
- athlete_season_level, college_first_season, upperclass_first_season and race_top_level are read by the backfill, the pack and the boards, but no pipeline step builds them.

## Done, 2026-10-25

What changed after the step times of 20261023_171317. Every change keeps
each step's output the same; the parallel paths were checked against the
serial ones for identical rows. Each has a switch back to the old way.

| step | before (s) | what changed | switch back |
|---|---:|---|---|
| 04e_weather_grid (bg, waited on before 04f) | 9,797 | ERA5 tiles read 8 at a time; rows written in tile order. Same rows on the live store, 2.6x on a 4-core box | `XCP_WX_THREADS=1` |
| 04e2_weather_recent (bg, new on master) | new | CDS days 4 at a time; no new day after 90 min, the rest resume next run | `XCP_CDS_THREADS=1`, `XCP_CDS_BUDGET_MIN` |
| 04_grade_sanity, 04b_wheelchair, 04d_gender | 4,292 + two | together (group 3), then 04c_twins | — |
| 09b_fill, 09c_school_names | 1,085 + 09c | together (group 7) | — |
| 10a .. 11_teams | ~2,100 in a row | three waves of up to four (group 1), then 10f3 | `XCP_STREAMS=1` |
| 10f2_projection (bg, the run's tail) | 8,668 | chunks on 6 spawned workers, put back in chunk order | `XCP_PROJ_WORKERS=1` |
| 12b_course_pages | 1,541 | 6 shards, not 3 | `XCP_COURSE_SHARDS=3` |
| 13j_record_books (bg) | 4,095 | school pages on 8 spawned workers; the parent writes | `XCP_RECORD_WORKERS=1` |
| every step | | 4 Postgres workers per query and per index build, not 2 | `XCP_DB_QUIET=strict` |

stepsN now asks `--from`, `--skip` and `_ALWAYS` of each name (master made
the same change with `beforeFrom`), so a mixed group keeps 04b's
run-on-every-`--from` rule and `--from 11` still builds the teams.

Connections at the busiest point: 10f2's workers (3 each, 18) and 13j's (1
each, 8, plus the parent) beside the site's (up to 48), under
`max_connections = 100`.

The Postgres side needs one change, made by the owner, live from the next
restart (06:00 UTC): the 4-per-query caps can only draw on the server's
worker pool, which is 8 by default.

    sudo -u postgres psql -P pager=off -c "ALTER SYSTEM SET max_worker_processes = 24" \
        -c "ALTER SYSTEM SET max_parallel_workers = 16" \
        -c "ALTER SYSTEM SET max_parallel_maintenance_workers = 4"

`max_worker_processes` takes effect only at a restart; the nightly one
picks it up. deploy/server_setup.sh writes the same three lines for a new
box.
