# Site notes -- the MVP cut and everything else

Updated 2026-08-26. Split the way the work is actually scheduled: Section 1
is what the MVP needs, divided into items that ride a pipeline night and
items that are pure display/app work; Section 2 is everything that can wait.
Issue ids (#n) are stable -- commits and conversation reference them.
[mock] items get a mock for approval before code, per the house rule.

---

## Tomorrow -- 2026-08-27 (owner's list, in order)

1. Check whether there is still a STATE offset.
2. Check for a SPORT offset -- or any offset that shows itself in
   cross-sport comparisons. Instruments: the _reportDelta XC<->TF spread
   on the engine run, and the "high-side extension" lines in
   02_fit_spline.log (see appendix).
3. Check the small training thing that happened at the end (of the last
   train run).
4. Check that all the noticeable races actually got corrected: Ox Bow /
   NLC pages, then `python scripts\find_dropped_divisions.py --min-gap 20`
   for the residual.
5. Decide whether 80% for speed rating is correct or not.
6. Then rerun the engine as necessary.
7. Then run the model NO MATTER WHAT.
8. Then transfer everything to Hetzner (or another online DB) and get the
   site online. Decisions made 2026-08-26:
   - **Architecture (c): the pipeline runs ON the server, weekly.** The
     server is sized for the full rebuild (dedicated NVMe box, 64-128 GB
     RAM, 1 TB+ disk), the overnight runs there on a weekly cron, and the
     scrapers feed it directly.
   - **GPU stays local**, so training splits from the pipeline: features
     are extracted server-side after each weekly rebuild, chunk files sync
     down to the 7900 XTX box, train runs there, and model.pt + the three
     sidecar artifacts push back up. A small sync script each way.
   - **Clean the DB first -- it is over 200 GB.** Before the dump: drop
     the known leftovers (any *_old undo copies, results_speed_rating
     backups once the rebuild is trusted, ovr_shift / reb_* / pair_*
     staging, dist_override_snap regenerates), then VACUUM FULL or
     pg_repack the big tables to return the space. Audit first with the
     size query in the appendix; deletes need eyes, not a script.

---

## Section 1 -- MVP

### 1a. Needs a pipeline night

- **#1 Twin-race sweep.** The same real-world race exists once per feed
  with different keys: split athlete results, double-counted boards, and
  the Ox Bow one-copy-fixed confusion. Canonical-race keying, then a
  rebuild to apply it. Unlocks the twin-race banner and cleans every board.
- **#6 School alias map.** Identity clustering cannot join spelling and
  campus variants ("Highland - P"). Hand alias map consulted by
  school_identity, then rerun 10b + search index (partial pipeline).
- **#9 Search trigram index.** pg_trgm on search_text kills the ~2.6 s
  cold tail. One migration in add_page_indexes; rides any night. Do it
  before any search feature work.
- **#13 First full model train.** Features + train after the first clean
  rebuild (chunks now exist only after the weather fix). The Predictions
  tab is wired and waiting; GPU venv is proven. Arguably post-MVP if
  launch pressure demands, but the tab is a headline feature.
- **#14 Canary verification.** The launch gate, not a feature: rosters and
  home boards populated, 9:01 3200 rating in 135-137, normalized 5K near
  15:05-15:10, Ox Bow / NLC pages corrected or dropped, record-flag
  covering index in use, cold sweep benchmark. Requires a completed
  rebuild to check.

### 1b. No rebuild needed (display / app layer)

- **#8 About page rewrite.** It describes the pre-fill world. Must say:
  every row with a normalized time carries a rating; out-of-band rows are
  visible but board-excluded by the pace band. Add a short "how distances
  get corrected" section -- badge-clickers will land here.
- **Corrected-distance badge** on race pages: "listed 8046 m, corrected to
  5000 m" instead of silently rendering the corrected number. Trust, plus
  free error reports when a correction is wrong.
- **Withheld-ratings notice** on nuked divisions: "ratings withheld --
  recorded distance fails plausibility" instead of a silent dash column.
- **Race-day weather line** on race pages (temp, wind, humidity at race
  hour). The weather table exists; display is cheap.
- **Meet TF cleanup**: fold "Open 1/2/3" into the bare stem when one
  exists (Arcadia 2); dedupe identical twin divisions (Arcadia 3).
  Display-layer.
- **Report page prefill** (URL, meet/div ids) + a "received" state, so
  reports arrive actionable. The badge/notice work above will increase
  report volume; this makes it useful.
- **#16 DB password out of scripts/config.py.** Env var or ignored local
  file. Required before the repo is ever shared; trivial.

---

## Section 2 -- Everything else

### Data / engine (pipeline-bound, post-MVP)

- **#4 Apparent-temperature fix.** Grid writer feeds raw solar irradiance
  where net absorbed radiation belongs; daytime feels-like inflated
  20-35 C. Consistent, so fine as a model feature; wrong for display or a
  weather-correction refit. Fix = derivation change + grid column rebuild
  + correction refit.
- **#3 TF distance detection depth.** Track distance comes from event
  names; pass tools and pace tests are XC-centric; a mislabeled TF event
  leans on the anchor gate at board build.
  Related, already handled, do not re-fix: TF normalizing to the 5K
  anchor from spans that stop at 3200 is counteracted in
  fit_distance_exponent (_sampleClamped + _extensionSlopeHigh: the
  3200->5000 stretch uses a slope MEASURED from dual-distance athlete
  pairs, not the cubic's tangent; refit every overnight at 02_fit_spline,
  which prints "high-side extension: MEASURED k=..." per pool). Per-sport
  anchors (hs_m|TF etc.) exist in POOL_TARGET_METERS but are deliberately
  inert under the merged solve; unmerging is only on the table if
  _reportDelta ever shows the XC<->TF ability gap is not a constant.
- **#10 Incremental course boards.** Top 1200 precomputed; the tail
  renders live (fine warm, hundreds of ms cold). Build-on-first-request
  and persist.
- **#17 Course-boards builder is orders slower than the page query**
  (measured twice: the live page renders a course in a few hundred ms,
  the builder spends minutes per course -- 500 courses took 64 min).
  Correct budget is ~2 s/course (page query + write), so the builder is
  recomputing something corpus-wide per course instead of reusing the
  page's indexed query. Read its SQL against the page route's, EXPLAIN
  both, fix the shape. Owner hit this twice mid-pipeline; --resume and
  the 1200 cap are the workaround, not the fix.
- **#18 Wheelchair/seated rows minted runner ratings.** SHIPPED
  2026-08-27, lands with the next full backfill: the engine's old SQL
  filter covered anet XC only, and fill_ratings then priced what the
  engine refused. Now nuked at the backfill (three seams, one pattern:
  TF event names, tfrrs blob titles, anet division titles -- census
  reason "wheelchair"), with a board-gate belt for TF until the rebuild.
- **#19 A corrected distance poisoned its venue's difficulty.** Measured
  at Cabell Midland: +0.155, ~19 fake points on a clean 3000. Policy
  shipped 2026-08-27: a corrected division votes on NO course (engine
  loader nulls its venue) and is DISPLAYED, NEVER RANKED
  (build_ranking_results gate, both sports). Verify any suspect venue
  with scripts/explain_course_cell.py.
- **#21 Field strength leaks into difficulty at MS-dominated venues.**
  Found 2026-08-27 via explain_course_cell at Cabell Midland: the +0.155
  d3000 cell holds ~170 HS varsity rows against ~1,200 middle-school and
  MS-JV rows (median paces 0.30-0.44 s/m), and NOT ONE corrected
  division -- the second-order-correction hypothesis is disproved for
  this venue (the no-vote rule still closes that class in general).
  Mechanism: local MS fields whose careers live entirely at this venue
  are weakly identified, so their below-pool-mean strength reads as
  course slowness and inflates the shared cell the HS race lives in
  (Dial's 9:22 3000 wore ~19 points of it). Fix direction: weight
  difficulty votes by the athlete's EXTERNAL linkage (races away from
  this venue), or solve MS-heavy cells separately -- measure with the
  engine's linkage census before choosing. Needs the DB.
- **#20 Gender on the wrong board.** Olalekan Fadesere (Katy Tompkins):
  every race in Men's events, appeared on a female board. Boards take
  gender from `athletes` rows (first school alphabetically wins a
  disagreement), so one bad F row flips a person. Needs a detector:
  athletes whose event/division titles are overwhelmingly one gender but
  whose athletes.gender says the other -> report + gender_fix.
- **Triage ids from the owner, 2026-08-27, unexplained "fake races":**
  26063/2 (weird high difficulty), 25531/1, 267944/1064960,
  262560/1041852. Run explain_course_cell / find_dropped_divisions
  --meet on each with the DB in reach.
- **Model iteration.** Confidence intervals, retrain cadence after
  correction waves, batch-size/LR tuning against the measured epoch time.

### Accepted trades and watch items (documented, not scheduled)

- **#2 Small distance errors survive detection.** Labels 10-20 percent too
  long sit inside athlete-variance noise; bars deliberately above it
  (lowering them re-creates the 378-bad-proposals failure).
- **#5 One-night reset blindness.** Pass 0 judges the pre-wipe state; a
  wipe-lost, un-reproposed override is wrong for one night, named the same
  evening in 05c_lost, self-nuked next reset.
- **#7 Unpriceable rows dash.** No person/gender/pool = no honest rating.
  Watch fill_ratings' census counts; growth means an upstream linking
  regression.
- **#15 Metric drift after fill.** Tools counting NULL ratings as
  "refused" (audit_overrides, pass "recovered", n_shadow) now read near
  zero. Print-only; re-point at ranking_results membership when touched.

### Site features (post-MVP, no rebuild unless noted)

- **Home**: upcoming meets strip [mock]; persist state filter
  (localStorage); surface Predictions once the model ships.
- **Meets index**: state + sport filters, XC/TF badges, season picker.
- **Race XC**: twin-race banner (needs #1); per-km pace column.
- **Meet XC**: show each division's corrected distance and rated share.
- **Race TF**: event/venue record flags; wind display if the feed ever
  carries it.
- **Meet TF**: combined headline team table with divisions collapsed
  [mock -- proposed, awaiting go].
- **#11 Venue TF**: per-event records grid to match the course page.
- **Athlete**: PR progression chart; season-best strip (SB/PR/percentile);
  "compare with..." quick action; subtle marker on out-of-band filled rows
  ("outside the plausibility band, not ranked").
- **Compare**: three-plus athletes; verify URL state round-trips.
- **School**: team season trajectory [mock]; dual-meet simulator on the
  predictions plumbing [mock]; relay PRs.
- **Course**: map embed from GPS; difficulty context (n races, days,
  same-state comparison); typical race-day weather.
- **Rankings**: state and grade/class filters; surface the "where am I"
  jump the API already has.
- **Predictions**: weather scenario knob [mock]; hypothetical meet
  builder; save/share via URL state; confidence intervals (needs #13).
- **Search**: keyboard navigation; type-filter chips (after #9).
- **Conversions**: URL state. Low priority.
- **Forum.** [mock] Owner wants one. Scope question before anything:
  off-the-shelf (Discourse or NodeBB on a subdomain, own auth, running on
  the same server -- days to stand up, moderation tools included) versus
  homegrown threads hung off meets/athletes/schools (integrated with site
  identity, but auth + moderation + abuse handling become our code).
  Recommendation: off-the-shelf first; revisit integration once it has
  users.

---

## Appendix -- verification commands

Latest overnight's measured extension lines (one per pool; "MEASURED k="
means the 3200->5000 leg is data, "tangent" names why not):

    Get-ChildItem logs -Directory -Filter "*-overnight" | Sort-Object Name -Descending |
      Select-Object -First 1 | ForEach-Object {
        Select-String -Path "$($_.FullName)\02_fit_spline.log" -Pattern "high-side extension" }

Morning lists after a reset night: 05c_lost (overrides the wipe lost),
pass 0's NUKED section, pass 4's nuke counts, then
`python scripts\find_dropped_divisions.py --min-gap 20` for the residual.

What the 200 GB actually is (run before any cleanup; TOAST and indexes
included per relation):

    SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size
    FROM   pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE  n.nspname = 'public' AND c.relkind = 'r'
    ORDER  BY pg_total_relation_size(c.oid) DESC LIMIT 30;
