# Data audit, 2026-11-10: the all-time boards' bad rows, by class

Read from the live API (all six pools, performance and ability, top 25),
each suspect row opened on its athlete and race page. "Caught" means the
athlete page already marks the row "not ranked" (rating_outlier / result_twin
were rewritten after the last board build, 2026-11-09 03:36), so it leaves
the boards at the next step 10. Everything else needed a new rule.

## Already caught, pending the next board build

| Row(s) on the boards | Rule (2026-11-09) |
|---|---|
| Cole Hoff, Robert Swoboda, Johnny Rutford: ms_m #1-3 (Bowerman 5K, adults) | outliers `level` |
| Sammi Lee, Madison Strasner, Isabella Schrader: college_f #1, #2, #7 (Reeder/Sagebrush, women at the men's 7000 m) | outliers `mixed` |
| Taylor Smart: college_f #3 | outliers `fast` |
| Morgan Thompson ms_f #1, Julia Buckholtz ms_f #22, Benjamin Acker ms_m #10 | outliers `fast` / career |
| Carly McNatt "Holy Trinity Academy (LA), Senior": ms_f #12 (one Flrunners race under two meets a day apart) | twins (gone from her page) |
| Lucas Bourgoyne, Big Dipper Dash twice: ms_m #8-9 (one run in two divisions) | twins `dup_cross_div` (runs at 04c) |

## New classes, ranked by what they move

Top-25 rows counted before (from the live boards); after = what the rule
leaves once tonight's run has rebuilt the boards (to be re-read after it).

| # | Class | Board rows (top 25) | Rule | Test |
|---|---|---|---|---|
| 1 | **A whole field far above itself.** Whitney Point 2018 "Modified" boys/girls, stored 3000 m: the field sits a median **+46 / +38 points** over the same kids' other races (a short course). `wrong_distance` missed it: its runners had too few other races that season to testify one by one. | ms_m #5, #18; ms_f #4, #10; ability ms_f/ms_m seasons built on them | outliers side **`race`**: a division whose 8+ referenced runners sit a median 0.08 (log) or more over the mean of their own other races that season, same pool, 75% of them up. Rank-only. | `test_outlier_data_audit.py::test_the_whole_field_sql`, `...does_not_clear_itself` |
| 2 | **A day the division did not run.** KY state meet 1991: the girls' three races stored at 3000 m (winners 14:44-15:15) and Boys A at 4000 m (winner 15:54) are longer races under short labels; four of six divisions read slow, the venue-day term followed them ("slow 8.3%"), and the two 5000 m boys' races carry +8.3% in every rating. | hs_m **#2** (Kazlauskas 15:39), #9, #15, #21 | outliers side **`day`**: rows of a division the solve's own day guard wrote to `race_day_suspect_division` with its own day ≥ 0.05 faster than the day it carries. Read only; the solve is unchanged. | `test_the_day_sql_reads_the_solves_guard` |
| 3 | **Men and women in one list, not called mixed.** Coast-to-Coast Battle in Beantown 2022, "Combined 5k Women": 89 rows rated college_f (BU/Northeastern men among them), 15 college_m on the page. The `mixed` rule counted genders from `results.rating_pool`, which does not say what the page says. | college_f #9, #15, #20 | `mixed` now counts each row as the **person's** gender (`person_gender`), the pool only where the person is unknown. | `test_the_extra_sql_counts_people_and_reads_graduation_years` |
| 4 | **One-race open seasons on the all-time ability boards.** The open-season exemption (meant for September's season board) applied to the all-time view. | ms_f #3 (1 race), #15 (1), #19 (2); college_f #8, #16, #17, #20 (2 each) | `season_floor.boardFloorExplicit`: the all-time view (no season picked) holds open seasons to the 3-race floor; one answer for the board, its count, rank jump and stored seek positions. A season's own board keeps the exemption. | `test_season_floor.py`, `test_board_seek.py` |
| 5 | **Graduation-year grades.** Scott Compton, grade "2010" on 2008 rows: class of 2010, an 11th grader, rated middle school. `normGrade` nulls a year, so the season was placed with no grade. | ms_m #6, #15, #19; ability ms_m #2 | outliers `level`: a youth-pool row whose grade is a graduation year meaning grade 9-12 that season (July-June). | `test_a_graduation_year_that_means_high_school_is_level` |
| 6 | **A race a fifth above both races beside it.** Adrian Suarez, HEB Zone 2019: 10:05 for 3200 m (2nd place 13:47), his only other races 13:59 and 12:54 three weeks either side. Two races is under every existing rule's minimum. | ms_m **#4**; ability ms_m **#1** | outliers `fast` (close): 2+ races within 60 days, same pool, all more than 0.18 (log) below. | `test_the_close_sql` |
| 7 | **Season state from travel venues.** Oliver Horton (Coronado HS, Colorado Springs) is **#1 on Arizona's 2026 board**: three-race season, one Arizona invitational, the season state was the venue mode with ties alphabetical. Board labels showed "Coronado CA/AZ", "Northfield ... MN" for a Denver runner. | hs_m #24 label; AZ state board #1 | `athlete_season.state`: a school-age season that raced in the athlete's career home state at all is that state's season. The ability API passes it to `teamState` for the school label. | `test_season_home_state.py` |
| 8 | **A track time trial on the XC calendar.** "Alta Sierra 1 Mile Time Trial on District track", July 2020, rated as a course with a day term. | ms_f #13, #15 | twins `xc_placeholder`: the track-name pattern also reads "on (the/District) track" and "1600/800". | `test_twin_flag.py` fixture 1131/1132 |

No new pipeline step: every rule runs inside steps that already run nightly
(09d `rating_outliers.py`, 04c `twin_flag.py`, 10 `build_ranking_results.py`).

## Left on the boards, judged real

Grace Ping, Annabella Tomasic, Marcelo Mantecon, Katelyn Tuohy, the NCAA
champions, Kennedy Kithuka; Amanda White's 1990 Hereford 18:43 (she ran
17:18 there in 1992 and 10:29 for 3200 m: a real runner; her row's height is
the day term below, not her).

## For the solver chat: model, not data

1. **1986-94 Maryland (Hereford) rates high, and it is not bad rows.** The
   1990 4A girls rate a median **-2.9 points** against the same girls' other
   races that year: the race agrees with them. The whole era sits high: on
   the same course, Jenny Novak's 20:51 (1990) rates 142.1, Caroline
   Gregory's 18:30.8 (2023) 142.0; Amanda White's 18:43 in 1990 rates above
   her own 17:18 in 1992. The shown day term (+3.1%) is a fraction of the
   ~12% gap, so the rest is the era level / sparse-era scaling (the
   1990 population on these boards is state-meet fields only). Look at
   `fit_era_corrections` and how a season whose only data is championships
   is levelled.
2. **The day guard follows a majority of broken divisions.** KY 1991: four of
   six divisions carry too-short labels, so the "information-weighted
   median" is the broken side and the correct 5000 m races are the ones set
   aside. The `day` rule keeps those rows off the boards; their ratings stay
   8% high on the page until the guard prefers a day near zero when the
   majority's own distances are implausible for their winners (3000 m girls'
   champions at 14:44). The distances themselves are wrong_distance's to
   fix, which never raises a label by design.
3. **Youth fields carry large positive day terms** (Molly Mocko +10.5%,
   Tyler Hetz +8.0%, Whitney Point +4.1% while its Modified races ran ~25%
   fast): with few other races per runner the day absorbs field level.
4. **`thin` and `career` compare speed_rating across pools** (an 8th grader's
   ms number beside their 9th-grade hs number is two scales). The new
   `race` and `close` rules compare within a pool only.
