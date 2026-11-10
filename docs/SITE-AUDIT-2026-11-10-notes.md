# Site audit notes, 2026-11-10 (owner decisions, second round)

- **Coaches pages cut** (owner: "maybe just cut it all, but log for the
  feature chat that a coaches racecast would be nice"). /coaches,
  /coaches/recruits and /recruiting/search 301 to /recruiting. The recruit
  search API (/api/recruiting) and static/recruiting-search.js stay for
  whoever builds it.
  **For the new-features chat: a coaches' Racecast.** A coach's home with
  their own teams and a recruit search. The old search had nine sorts,
  five of them "most improved" variants, and the projection sorts led on
  nonsense (a 96.9 runner "projected" 158.7); start from three (highest
  rating, most improved, best single race).
- **Goal calculator kept**, and for the new-features chat: let a runner
  set their own goal (a time, a rating, a place at a meet) and see whether
  they are on track, beyond the fixed "qualify / top 8 at a meet" goals.
- **Goal calculator, NCAA goals**: the college feed's copy of each NCAA
  championship (8,306 results) matched 0 ranked rows, because the twin copy
  of a race carried by both feeds never ranks (result_twin). The meet set
  now takes both copies via canon_meet_id (college_goal._twinMeets).
- **Predict next year** (owner: "really really good ... the issue is if it
  doesn't take senirors off"; "keep it"): the race page's link now carries a
  date one year on, so predict.raceDate ages each squad into the next
  season (seasonsAhead/_ageForward: the graduating class out, grades +1).
  Before, the page proposed this year's running, which kept the seniors
  until August.
- **Still open**: the Herriman school widget leaves out Trevin Lounsbury
  (person 29382566, hs_m 137.8, 4 races): his 2026 season is in
  athlete_season and the school page lists him first, but
  /embed/school/Herriman?state=UT&level=hs starts at the #2 runner. Same
  schoolRoster call on both; not reproduced without the database.
