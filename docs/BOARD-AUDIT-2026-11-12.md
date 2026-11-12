# All-time board audit after run 20261112_015027 (2026-11-12)

Logged for later (owner: "log the board audit results for now and deal
with after you make faster"). XC performance and ability boards, top 25,
hs_m hs_f college_m college_f ms_m ms_f, read through /api/rankings.
College boards and the HS girls' top 15 look right. Five suspects, each
a class to fix in code (CLAUDE.md), none fixed yet:

1. **Short course read as full distance -- hs_m #11.** Emmanuel Sgouros,
   St John's (TX), Maverick Time Trial, Hermann Park, 2021-08-14, 3218 m
   in 9:01, rated 147.6; his other 2021 races rate 133-144 (~140). The
   whole field set PRs / season bests; won by 81 s. Course read +5.3%
   (the prior), day "slow 1.1%". 06c's own-races rule does not reach it:
   it judges only the last 400 days and needs a ~17% shift; a short
   course is 5-8%. Class: a race whose field runs far above its own level
   on a course with no other races to set its difficulty.

2. **An old race-day effect from too few voters -- hs_m #2, #9, #15, #23.**
   Kentucky State XC 1991 AAA, Kentucky Horse Park, 2026 data
   meet mamhu4ogciicnh / div dats7c1mr6znez: four of the all-time top 25
   at 15:39-16:00. Day "slow 8.3%" on a +5.8% course, so 15:39 reads as
   a 13:24 5K. Only the top 25 were posted and 1991 runners have few other
   races. Class: a big day effect resting on a handful of linked runners.

3. **Spring 2021 (COVID) season far above the athlete -- ms_f #1.** Logan
   Pang, Iolani (HI), 2021-03-13, 3218 m in 11:22, rated 163.8 (5 clear of
   #2); her fall 2021 races rate 142-145. Its race link from the board
   404s (/race/xc/mdeiq8fppeio5/ddyc9h8rqz2sq9) -- a site bug too.

4. **One race stored twice, a day apart, two schools -- ms_f #9 and #20.**
   Carly McNatt, 17:45.8 on 2018-09-28 ("Holy Trinity Academy" LA, grade
   "Senior") and 2018-09-29 ("Holy Trinity Episcopal" FL, grade 8). The
   copy check (twin_flag dup_race_copy) should take one; the "Senior" copy
   also puts a senior label on the middle-school board.

5. **A track mile filed as XC -- ms_f #12.** Elliana Lomeli, Alta Sierra
   (CA), 2020-07-29, 1609 m in 5:09.9 in July. The track-in-XC rule
   (_TRACK_IN_XC_RX) does not catch it.

Also seen: hs_f #12 school "Thorvaldson-Sydney", no state (a school label
from a feed).

Owners by area: 1-3 the rating model (solver chat); 4-5 the copy and
track-in-XC checks (data-cleaning chat).
