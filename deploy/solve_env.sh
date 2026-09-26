# Project: xc-predictor / deploy
# File:    solve_env.sh
# Purpose: the solve's CONSTANTS, in one reviewable place. Sourced by
#          scripts/overnight_fit_pool_solve.sh; harmless to source by hand
#          before an interactive deploy/run_pipeline.sh.
#
# ⚠ WHY THIS FILE EXISTS. Every documented run of this pipeline carries a
#   line of XCP_ variables, and they are not defaults -- they are the run's
#   settings, and a run without them is a DIFFERENT MODEL. The unattended
#   chain previously called scripts/pipeline.py with none of them, which
#   would have produced a solve unlike any before it and published it.
#
# ★ THE VALUES BELOW ARE THE LAST DOCUMENTED SET (runs 22/24,
#   docs/HANDOFF-2026-09-13.md §2). They are not invented here. Change them
#   deliberately, and say so in the run log -- the chain prints every one
#   before it starts.
#
# ! XCP_SPORT_LEVEL SUPERSEDED XCP_WINTER_GAIN. The older runs (08-24, and
#   ISSUES-RUNNING) pin the winter gain directly with XCP_WINTER_GAIN=0.02
#   plus XCP_WINTER_GAIN_BANDS; the newer ones (RESEARCH-ENGINE-2026-09-11,
#   runs 22 and 24) state XCP_SPORT_LEVEL=0.0583 instead. Both are ways of
#   pinning the same cross-sport scalar. The newer spelling is used here
#   because it is what the most recent real runs carried; set
#   XCP_WINTER_GAIN yourself if you want the old behaviour back, and expect
#   to justify it.
#
# ★ XCP_DIFFICULTY=bracket IS WHAT MAKES THE BRACKET ENGINE THE PUBLISHED
#   ANSWER. Without it, 08_golive still solves everything but publishes the
#   joint model's course numbers instead (run_joint.bracketDifficulties).
#   The bracket engine is the engine this project uses, so it is set.

# --- the model's settings -------------------------------------------------
: "${XCP_DIFFICULTY:=bracket}"      # publish the BRACKET engine's courses
: "${XCP_SPORT_LEVEL:=0.0583}"      # the cross-sport scalar (ex-winter-gain)
: "${XCP_ERA_YEARS:=2}"             # course eras, in years
: "${XCP_ALTITUDE:=1}"              # altitude correction on
: "${XCP_INDOOR_LEVEL:=0.003}"      # indoor's level against outdoor

# ★★ THE GAUGE, AND THE SAME INDOOR NUMBER REACHING THE BRACKET ENGINE (plan
#    §2 and §3; owner, 2026-09-19: "we're gonna make all falt outdoor 400m
#    tracks 0.0, and indoor tracks on avg +0.3 slower").
#
#    flat400 holds every flat outdoor 400m cell at 0.0 EXACTLY, so the zero is
#    a fixed reference instead of a vote-weighted mean. That mean is how "every
#    course outside California went negative" was possible at all: a
#    redistribution with nothing absolute to push against.
#
# ⚠ XCP_INDOOR_LEVEL ABOVE ONLY EVER REACHED THE JOINT SOLVE. The bracket
#   engine -- the one that publishes -- never saw it, and run_joint then
#   re-centred every cell on the outdoor mean after the engine had already set
#   its zero, so the asserted +0.3% could not survive. Both are fixed; this is
#   the same number, said to the engine that publishes.
: "${XCP_GAUGE:=flat400}"                   # flat outdoor 400s at 0.0 each
: "${XCP_BRACKET_INDOOR_CENTRE:=0.003}"     # indoor's asserted centre

# ★★ THE TWO THINGS THE OWNER SAID WERE STILL WRONG ON 2026-09-20, and what
#    each one changes. Both are DEFAULTS IN CODE; they are set here so the run
#    log records them, and either can be flipped back for one run to price it.
#
#  1. "track difficulty is not set to 0 for all outdoor 400m tracks". It was
#     not. track_geometry demanded a POSITIVELY KNOWN 400m length, and an
#     unrecorded track_length is the commonest value in the corpus, so most
#     outdoor ovals never entered the reference class and were never pinned.
#     assume400 reads an unrecorded length as the standard oval -- which is
#     already what normalize_distance._resolveTrackLength does when it
#     normalises those very times. A STATED non-400 length is still refused.
#       strict = the fact-only reading (the 2026-09-19 behaviour).
: "${XCP_GAUGE_UNKNOWN_LENGTH:=assume400}"
#
#  2. "indoor is still way too 'easy' difficulty wise". The centre above was
#     only a SHRINKAGE TARGET, and indoor ovals are among the most heavily
#     raced cells there are -- the same few facilities all winter -- so the
#     evidence swamped the prior and the group stayed near the fit's -1.68%,
#     i.e. indoor reading FASTER than outdoor. pin sets the indoor group's
#     vote-weighted MEAN to the centre by one additive shift, which is what
#     "indoor tracks on avg +0.3 slower" says. The spread between ovals is
#     untouched, so no cell stops responding to its own races.
#       shrink = target-only (the 2026-09-19 behaviour).
: "${XCP_BRACKET_INDOOR_MODE:=pin}"
# ★★ CROSS COUNTRY'S LEVEL IS ASSERTED, NOT DISCOVERED (owner, 2026-09-21:
#    "the xc difficutly is fucked ngl"). The run before this published
#    XC - TF = +1.27% where conversions.venueEffect, difficulty_view and the
#    course pages all assume +5.83% (joint_solve.XC_TRACK_GAP = ln 1.06).
#    The bracket engine had NO term for the gap -- XCP_SPORT_LEVEL above has
#    been asserting it for the JOINT model all along, and XCP_DIFFICULTY=
#    bracket publishes the bracket engine's courses.
#    ⚠⚠ SET TO pin FOR THE 2026-09-21 RUN AND REVERTED THE NEXT DAY. That
#       run came back "XC difficulty is way too high" and "tf is rated way
#       too low compared to xc". Under gauge=flat400 the go-live's
#       track-level-by-population shift is skipped, so both sports sit at
#       their own zero and the XC-vs-TF relationship is carried by the
#       athlete term (resolvePool keys it without the sport). Adding the
#       grass cost to XC's difficulty moved one sport with nothing to
#       absorb it. See bracket_engine.XC_LEVEL_MODES.
: "${XCP_BRACKET_XC_LEVEL_MODE:=off}"
#
#  3. XC's zero. "sport" (default) is today: cross country pins on its own
#     vote-weighted mean, which is the see-saw -- sum(w*D)=0 forces courses
#     negative when the heavily-raced ones read high. "merge" shares one zero
#     with track per era so the flat-400 reference anchors XC.
#
#     ⚠ THE BRIDGE merge RESTS ON IS THIN, MEASURED: 0.2-0.5% of XC rows at
#       the bracket window, 2.7% at 60 days (diag_xc_track_bridge.py). Try it
#       on ONE run and read "[bracket] gauge scope=" for how far XC moved and
#       what share of XC weight is still negative; that is the owner's test.
#     The other route is engine/xc_reference.py -- name ordinary XC courses
#     and they become the zero, which breaks the see-saw without the bridge.
#
#     ★ IT WAS SET TO merge FOR THE 2026-09-20 RUN, and the reasoning was:
#       the generic bridge is thin, but the route does not need it wide, it
#       needs one end KNOWN -- engine/indoor_reference.py asserts Boston
#       University's oval at 0.0 ("BU which is as fast as a flat 400m so it
#       works") and merge is what lets that pin reach cross country.
#       Kept here because it was a good argument and the run that tested it
#       is the reason the line below now reads sport.
# ⚠⚠ BACK TO sport (2026-09-21), AND THE MERGE RUN IS WHY. merge exists to
#    MEASURE cross country's level from athletes who race both sports; the
#    run measured +1.27% against an expected +5.83%, on a bridge reaching
#    0.2-0.5% of XC rows (2.7% at 60 days). That is the experiment's answer.
#    With XCP_BRACKET_XC_LEVEL_MODE=pin the level is asserted anyway, so
#    merge would only measure it and then be overwritten -- two questions,
#    one answer kept. The engine prints a warning if both are set.
: "${XCP_GAUGE_SCOPE:=sport}"
#
#  4. The indoor gates are ENFORCED now (owner, 2026-09-20: "they should not
#     be allowed outside the gates"). A cell outside -0.3%..+2.0% is pulled to
#     the nearest gate. The count is printed every run, because a clamped cell
#     stops responding to its own races and a rising count is the only evidence
#     that the GATES are wrong. `report` restores counting without clamping.
: "${XCP_BRACKET_INDOOR_GATES:=clamp}"

# ★ §4 THE RACE-DAY TERM. "fitted" keeps the historic numerator (each group's
#   own multi-race courses) and MEASURES the pinned cells' race-day spread
#   beside it; the gap between the two is the selection bias, printed. Left at
#   fitted deliberately for this run: the measurement comes first, and
#   "reference" is scored against it with
#   scripts/bracket_holdout.py --gauge flat400 --day-noise reference
#   before it decides anything.
: "${XCP_DAY_NOISE:=fitted}"
# ★ MEASURED, NOT CHOSEN. run_joint's default is 21 days. Swept on the SAME
#   held-out rows -- the only fair comparison, since a wider window also
#   changes coverage -- against a 21-day baseline, 571,290 rows covered by all:
#
#       window 21   0.041938   (baseline)
#       window 30   0.041401   -1.28%   <-- best
#       window 45   0.041434   -1.20%
#       window 60   0.041603   -0.80%
#       window 90   0.041658   -0.67%
#
#   Every one beats 21, and the curve turns over: an INTERIOR optimum around
#   30-45 (those two differ by 0.08%, a tie), declining after. That shape is
#   the finding. It says the window is a real fitness horizon of about a month
#   either side, NOT "the athlete term just wants more rows" -- which would
#   have kept improving out to 90.
#
# ⚠ AND IT REFUTES THE STRATUM STORY IT WAS RUN TO TEST. The idea was that a
#   December championship's voters only see other championship races, so a
#   wider window would let their October form in and fix Foot Locker. But
#   early December plus 30 days reaches back only to about 5 November -- still
#   postseason. The window that WOULD bridge to the September-October season
#   is 90, and 90 is the worst of the four. Bridging costs more in fitness
#   drift than it buys in evidence. So this is a general improvement and NOT
#   the Foot Locker / NXN fix.
#
#   Coverage also rises (63.1% -> 69.3% at 45), which is tens of thousands
#   more rows getting a rating at all -- a second, independent gain that the
#   headline sds hide, since they are then computed on different populations.
#
# ⚠ LEFT UNSET UNTIL THE RESTORATION RUN IS DONE (2026-09-19). It was briefly
#   set to 30 here, which would have made the next solve differ from the last
#   known-good one by TWO things -- the curve revert AND the window -- and the
#   whole point of the restoration is that it changes nothing but undoing a bad
#   run. Set it on the run AFTER the restoration verifies, one change at a
#   time:  XCP_BRACKET_WINDOW=30 bash deploy/run_pipeline.sh --from 8 ...
# ★ ON BY DEFAULT FROM 2026-09-26 (owner: "have 30 day as default now").
#   XCP_BRACKET_WINDOW=21 in the environment puts run_joint's old default back
#   for one run.
: "${XCP_BRACKET_WINDOW:=30}"
# ⚠ LEAVE THIS AT none. I argued for `field` here on 2026-09-19 and the owner
#   refuted it on the spot, with the right instrument: if an untapered
#   championship field were the cause, NXN AND FOOT LOCKER WOULD BOTH SHOW IT,
#   and they do not -- and among athletes who raced both, Foot Locker's
#   normalised times are slightly SLOWER, so its difficulty should sit at or
#   above NXN's rather than below. A taper term cannot produce a difference
#   between two races that share a field and a week. Do not add it to chase
#   this; it would paper over whatever is actually moving the cells.
: "${XCP_IMPORTANCE:=none}"         # `field` is run_joint's own default

# --- pooling ---------------------------------------------------------------
# ★ THE 15-ATHLETES-ALL-TIME RULE IS ON (owner, 2026-09-24: "I like this
#   rule"). A team with fewer than fifteen distinct athletes in its whole
#   history is not a school (engine/build_team_pool.py), and its rows pool
#   professional (speed_ratings.loadProTeams -> resolvePool's team_pro).
#   overnight_fit_pool_solve.sh rebuilds team_pool before the solve.
# ! THE ABILITY GATE SITS BELOW IT (pool_resolve: `if pro_ability is False`
#   runs after `if team_pro`), so a small team's athletes who are not sub-14:00
#   / sub-15:30 fall through to their grade instead of into the pro pool.
# ! XCP_TEAM_POOL=0 turns it off for one run.
: "${XCP_TEAM_POOL:=1}"

# ★ THE PER-LEVEL TF<->XC GAP, AS A DEFAULT (sweep, 2026-09-26). The owner
#   has typed exactly this on every run since 2026-09-24 (HANDOFF-ENGINE
#   §0), and a run where it was forgotten underrated every track athlete.
#   A value already in the environment still wins, and 'off' turns it off.
: "${XCP_SPORT_LEVEL_POOLS:=college=0.0037,hs=0.0092,ms=0.0191,elem=0.0186}"

# --- housekeeping the pipeline expects -----------------------------------
: "${XCP_DB_QUIET:=1}"

export XCP_DIFFICULTY XCP_SPORT_LEVEL XCP_ERA_YEARS XCP_ALTITUDE \
       XCP_INDOOR_LEVEL XCP_GAUGE XCP_BRACKET_INDOOR_CENTRE XCP_DAY_NOISE \
       XCP_GAUGE_UNKNOWN_LENGTH XCP_BRACKET_INDOOR_MODE XCP_GAUGE_SCOPE \
       XCP_BRACKET_XC_LEVEL XCP_BRACKET_XC_LEVEL_MODE \
       XCP_BRACKET_INDOOR_GATES \
       XCP_IMPORTANCE XCP_TEAM_POOL XCP_DB_QUIET XCP_SPORT_LEVEL_POOLS \
       XCP_BRACKET_WINDOW

# Anything else already in the environment is left alone, so a one-off
#   XCP_PROBES=16 bash scripts/overnight_fit_pool_solve.sh
# still works.
# ! XCP_TEAM_POOL IS LISTED so every run records whether the
#   15-athletes-all-time rule was on -- on 2026-09-20 it was absent from this
#   list, and the progress log could not answer "was the new pooling on?"
#   about the run whose ratings were being diagnosed. It is SET above since
#   2026-09-24 (it was deliberately unset until the owner decided).
SOLVE_ENV_VARS="XCP_DIFFICULTY XCP_SPORT_LEVEL XCP_ERA_YEARS XCP_ALTITUDE \
XCP_INDOOR_LEVEL XCP_GAUGE XCP_BRACKET_INDOOR_CENTRE XCP_DAY_NOISE \
XCP_IMPORTANCE XCP_TEAM_POOL XCP_GAUGE_UNKNOWN_LENGTH \
XCP_BRACKET_INDOOR_MODE XCP_GAUGE_SCOPE XCP_BRACKET_INDOOR_GATES \
XCP_BRACKET_XC_LEVEL XCP_BRACKET_XC_LEVEL_MODE \
XCP_WINTER_GAIN XCP_WINTER_GAIN_BANDS \
XCP_SPORT_LEVEL_POOLS XCP_BRACKET_PRIOR XCP_BRACKET_PLACE_RADIUS \
XCP_BRACKET_PLACE_PRIOR XCP_BRACKET_WINDOW XCP_COURSE_SCALE \
XCP_FROM_STATE XCP_PROBES"
