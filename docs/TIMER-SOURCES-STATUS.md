# Local timing-company sources: status (2026-11-09)

A dry run of `python -m sources.timers all --dry --per-source 2` over every enabled source
(107), from this repository's cloud container. Before this pass three had ever produced rows.
**57 of 107 work.**

Status: **works** = parsed real meets in the dry run (two newest shown, dated and classed);
**nothing new** = reads fine, nothing from 2026 to give; **can't** = why not.

| source | status | meets parsed (date, name, sport/races/rows) or reason |
|---|---|---|
| flash-results-inc | works | 2026-10-30 ACC Cross Country Championships (XC 2 races 324 rows) |
| finished-results | works | 2026-11-08 PacWest Conference Championships (XC 1 races 59 rows); 2026-11-08 PacWest Conference Championships (XC 1 races 80 rows) |
| big-river-race-management | works | 2025-11-01 UAA XC CONFERENCE CHAMPIONSHIPS (XC 2 races 155 rows) |
| c-f-p-i-timing-data-inc | works | 2026-10-30 SOUTHLAND CONFERENCE CHAMPIONSHIPS 2026 (XC 1 races 93 rows); 2026-10-30 Southland Conference Championships 2026 (XC 2 races 191 rows) |
| diablo-timing | works | 2026-11-07 EBAL Championships (XC 6 races 505 rows); 2026-11-05 CMC Championships (XC 9 races 404 rows) |
| rt-results-rtspt-com | can't | OFF LIMITS: robots.txt Disallow: / for every crawler |
| rapid-results-timing | works | 2026-11-07 Open Race 2026 (XC 2 races 69 rows); 2026-11-07 2026 RMAC Cross Country Championships (XC 2 races 257 rows) |
| lancer-timing-services | can't | OFF LIMITS: TrackScoreboard app; its api.trackscoreboard.com robots.txt is Disallow: / |
| snap-timing | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| new-river-timing | works | 2026-04-22 Stuarts Draft High School Invitational (TF 26 races 222 rows) |
| elite-timing-event-management | works | 2026-11-07 Sunshine State Conference 2026 (XC 2 races 206 rows) |
| gunlap-running | works | 2026-11-06 2026 FHSAA 4A DISTRICT 7 XC CHAMPIONSHIP (XC 2 races 147 rows); 2026-10-14 2026 First Coast High School XC Meet 3 ( (XC 2 races 40 rows) |
| track-timing-and-data-management-llc | can't | own domain does not resolve; its live results are Live Running Results' (that source, now working) |
| mogo-timing | works | 2026-05-13 PBC MS Championship 2026 (TF 12 races 192 rows) |
| black-squirrel-timing | works | 2026-08-27 2026 Weeping Water XC Invitational (XC 6 races 177 rows); 2026-08-27 2026 Loganview XC Invitational (XC 4 races 406 rows) |
| b-b-race-kc | works | 2026-10-19 BORDER WAR CHAMPIONSHIP 2026 - TEAM (XC 6 races 299 rows); 2026-10-17 Crossroads Conference Championship (XC 2 races 69 rows) |
| ticker-timing | works | 2026-11-07 Heart of America Conference XC (XC 2 races 289 rows); 2026-10-19 Baldwin Sub-State (XC 4 races 182 rows) |
| eagle-eye-timing | can't | results go to MileSplit Live; its one own file is a field-events report |
| trxc-timing-llc | works | 2026-11-07 GLVC XC Championships (XC 1 races 154 rows); 2026-11-07 GLVC XC Championships (XC 1 races 134 rows) |
| on-the-mark-timing | nothing new | own page's links are URL shorteners (not followed); account's only 2026 folder is a combined-events meet |
| hero-s-timing | works | 2025-10-04 52ND ANNUAL LIONS XC INVITATIONAL MEET OF (XC 8 races 1084 rows); 2025-10-08 LAKE SUPERIOR CONFERENCE XC CHAMPIONSHIP (XC 6 races 370 rows) -- own pages parse (2025 season); its 2026 meets are on AthleticLIVE (live.herostiming.com), read by that step |
| dc-timing | nothing new | AthleticLIVE tenant; no MeetPro account found |
| alltrax-timing | works | 2023-11-02 2023 TSSAA State Cross Country Champions (XC 1 races 249 rows) |
| trojan-timing | works | 2026-04-17 2026 Carolina Distance Carnival (TF 4 races 1575 rows); 2026-04-10 2026 Scots Invitational (TF 23 races 184 rows) |
| slipstream-timing-meet-management | nothing new | own pages and both MeetPro accounts: nothing from 2026 |
| foothills-timing-and-meet-management | can't | no index of its own: its reports are posted by hosts (NCHSAA) |
| pierre-timing | works | 2026-11-05 Rocky River Time Trial 2026 (TF 6 races 58 rows); 2026-10-31 NJCAA Region 10 Championship (XC 8 races 78 rows) |
| mitchell-timing | works | 2026-10-30 USA South Athletic Conference Cross Coun (XC 2 races 92 rows); 2026-10-30 NCISAA Cross Country Championships (XC 8 races 779 rows) |
| yentiming-services | works | 2026-11-07 Section V Cross Country Championships (XC 8 races 815 rows); 2026-10-31 W-FL League Championships (XC 4 races 326 rows) |
| fmtiming | can't | no index of its own: reports posted by hosts |
| elitefeats | works | 2026-11-08 CACC XC Conference Championships (XC 2 races 117 rows); 2026-11-07 NE10 Conference Championship (XC 2 races 208 rows) |
| wil-time-event-management | works | 2026-10-21 2026 SUSQUEHANNA CLUSTER (XC 4 races 116 rows) |
| mac-fat-llc | works | 2026-10-31 2026 PIAA District 3 Cross Country Champ (XC 6 races 971 rows); 2026-10-17 2026 SCAC Cross Country Championships (XC 8 races 538 rows) |
| satrackresults | can't | OFF LIMITS: linktr.ee robots.txt disallows |
| athletic-timing | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| snake-river-timing | works | 2022-03-17 Disrtict 3 4A Quad @ BK (TF 16 races 465 rows) |
| delta-timing-group | nothing new | own site answers 530 (down); MeetPro account (Delta_Timing, 21 files): nothing from 2026 |
| timinginc-com-timing-inc | works | 2026-10-05 2026 Tri-County Conference XC Championsh (XC 2 races 174 rows); 2026-09-12 2026 Mike Moore XC Classic (XC 3 races 662 rows) |
| 2l-race-services | nothing new | AthleticLIVE tenant (its own step reads it); MeetPro account has nothing from 2026 |
| athleticlive-tenant-atmmeetmanagement | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| kauder-racing-llc-kauder-racing-results-service | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| kick-to-the-finish-llc | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| letsgorun-com | works | 2026-10-31 2026 Kansas Approved Schools State X-C C (XC 2 races 146 rows); 2026-10-24 2026 KSHSAA Class 6A Manhattan Sub-State (XC 4 races 290 rows) |
| athleticlive-tenant-pfv | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| westside-timing | works | 2026-11-04 Coastal League XC Finals 11-4-26 (XC 3 races 136 rows); 2026-11-03 Gold Coast XC Finals (XC 4 races 114 rows) |
| windsor-running-llc-conflict-between-research-passes-windsor | nothing new | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| payne-s-corner-timing-2 | can't | season page is a script-rendered Google Sheet with no links; results go to athletic.net |
| in-stride-timing | can't | results go to MileSplit Live (a feed): nothing of its own to read |
| pure-gold-timing | works | 2025-04-08 Atlantic Last Chance (TF 16 races 334 rows) |
| fayetteville-flyers | nothing new | nothing posted since 2025; links out to other platforms |
| mp-timing | nothing new | own page lists no meets; MeetPro account: nothing from 2026 |
| pirate-timing-durham-nc | nothing new | its one results link is a Feb 2022 meet |
| finish-line-results | works | 2026-09-08 Bourbon County All-Comers (XC 4 races 180 rows) |
| ktccca-kentucky-track-xc-coaches-assoc | can't | results go to MileSplit; own pages end 2013 |
| trxcky | works | 2020-10-08 Striders High HS-MS XC Showcase (XC 2 races 75 rows); 2024-05-21 Class 3A Region 6 (TF 16 races 307 rows) |
| wilderresults | nothing new | page on record is a feed's; both MeetPro accounts: nothing from 2026 |
| miller-sports-timing-llc | works | 2026-11-04 D6-Region 2 XC Championship (Hackberry) (XC 2 races 114 rows); 2026-10-10 Run For A Cure (Middle School) 2026 (XC 2 races 248 rows) |
| swobie-timing | nothing new | page on record is a feed's; MeetPro account: nothing from 2026 |
| ft-timing | works | 2026-08-15 The Honor Connor FWC Invite (XC 6 races 203 rows); 2024-02-08 Denton Bronco Invitational (TF 30 races 434 rows) |
| run-texas-timing-services | can't | its PDFs (on Constant Contact storage) print no meet date, so cannot be rated on a day; the same meets are on MileSplit; MeetPro account: nothing from 2026 |
| ez-meet-services | can't | no index of its own: reports posted by hosts and leagues |
| cag-s-timing | nothing new | own meets (read now via their onclick links) end with 2025 |
| common-timing | works | 2026-09-12 LCXCEagleInvite26 (XC 4 races 432 rows); 2026-09-02 WCMSXC26 (XC 2 races 83 rows) |
| aldrich-timing-race-management | works | 2026-05-20 26baycounty (TF 16 races 184 rows); 2026-05-14 ELFIELDDAY (TF 30 races 348 rows) |
| meetpro-directathletics-inc-flosports-owned-since-june-2023 | can't | the software vendor, not a timer: no meets of its own |
| heartland-racing-co | works | 2026-04-16 2026 Victoria MS Squire Invite (TF 32 races 464 rows) |
| ftr-race-management | works | 2026-10-15 2026-District-18-6A-XC-Championships (XC 5 races 339 rows) |
| illinois-prep-top-timing | nothing new | its one 2026 folder (Chi Town Miles) holds no individual races |
| jacksonville-athletic-club-jac | works | 2026-11-06 The Final 8 @ IMGA (TF 2 races 21 rows); 2026-09-05 2026 Parker Valby Invitational (XC 6 races 416 rows) |
| tiffin-timing-llc | works | 2026-05-01 Hopewell HS Invite (TF 10 races 146 rows) |
| big-red-running-llc | works | 2026-09-12 2026 Douglas County Middle School Champi (XC 4 races 224 rows); 2026-08-29 2026 Centaurus Bernie Gay Invitational (XC 6 races 998 rows) |
| river-forest-meet-timing | nothing new | MeetPro account: nothing from 2026 |
| laurence-loesel-individual-meetpro-account | works | 2026-10-24 VT Time Trials 10.24.26 (TF 10 races 74 rows) |
| juan-diego-reyes-individual-meetpro-account | works | 2026-05-05 2026 Polk County MS Meet # 2 (TF 6 races 70 rows) |
| dennis-weber-individual-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| skip-rafferty-individual-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| howard-atkinson-individual-meetpro-account | works | 2026-02-28 Butte College Black and Gold at Butte Co (TF 18 races 260 rows) |
| jeff-offringa-individual-meetpro-account | nothing new | 2026 folders are relays meets (Watervliet Relays): no individual races |
| ken-grace-individual-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| chris-quick-individual-meetpro-account | works | 2026-11-05 North Central College Red/White XC Open- (XC 3 races 237 rows); 2026-10-31 2026 NJCAA Region 4 Cross Country Champi (XC 4 races 135 rows) |
| pete-wareham-individual-meetpro-account | works | 2026-05-09 spa ms may 9 (TF 10 races 252 rows); 2026-05-04 carver ms (TF 34 races 438 rows) |
| clennie-wilkins-individual-meetpro-account | works | 2026-10-08 Triangle Athletic Conference Championshi (XC 4 races 78 rows); 2026-10-08 riangle Athletic Conference Championship (XC 4 races 74 rows) |
| scott-siettmann-individual-meetpro-account | works | 2026-10-03 West Elk Challenge (XC 2 races 100 rows); 2026-10-02 Delta Pantherfest XC Invite (XC 4 races 566 rows) |
| eagleton-meetpro-account | works | 2024-05-02 Last Chance Meet @ ECCA (TF 16 races 366 rows); 2026-04-09 ECCA MS Meet #2 2026 (TF 12 races 303 rows) |
| trey-jackson-individual-meetpro-account | nothing new | its newest 2026 folders are empty templates (results never uploaded); older ones wait for later nights |
| dawn-driver-individual-meetpro-account | nothing new | newest 2026 folders: a Canadian club meet (left out) and a throws meet; others wait for later nights |
| meeshelia-blackwell-individual-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| butler-track-meetpro-account | nothing new | its one 2026 meet (CSAC MS) has no result pages posted |
| lt-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| frontrunner-timing-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| university-of-nebraska-at-kearney-school-meetpro-account | works | 2026-05-09 2026 Loper Twilight (TF 14 races 75 rows); 2026-04-16 2026 Ravenna Invite (TF 16 races 200 rows) |
| colorado-school-of-mines-school-meetpro-account | works | 2026-04-16 Mines Midweek Invite (TF 13 races 68 rows); 2026-04-11 Mines Kit Mayer Classic (TF 8 races 214 rows) |
| pueblo-school-district-60-school-meetpro-account | works | 2026-05-01 The Pueblo Twilight 2026 (TF 16 races 941 rows); 2026-04-29 MS CITY CHAMP MEET (TF 12 races 212 rows) |
| mancos-school-district-re-6-school-meetpro-account | works | 2026-04-28 2026 San Juan Basin League Meet (TF 17 races 213 rows); 2026-04-30 2026 SJBL League Championships (TF 14 races 190 rows) |
| texico-schools-school-meetpro-account | nothing new | both MeetPro accounts: nothing from 2026 |
| goddard-schools-usd-265-school-meetpro-account | works | 2026-09-11 Wamego Invitational (XC 8 races 744 rows); 2026-02-07 Shocker Track Club February Freeze Track (TF 68 races 522 rows) |
| portage-high-school-school-meetpro-account | works | 2026-05-26 2026 Portage Girls IHSAA Regional (TF 11 races 152 rows); 2026-05-21 Portage Boys 2026 IHSAA Sectional (TF 10 races 132 rows) |
| hamilton-southeastern-high-school-school-meetpro-account | works | 2026-03-07 Blue & White Relays (TF 6 races 142 rows) |
| james-island-charter-high-school-school-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| meadowbrook-high-school-school-meetpro-account | works | 2026-05-06 MMS Vs Coshocton, Morgan (TF 14 races 157 rows); 2026-05-05 Meadowbrook Vs Morgan, Coshocton (TF 15 races 137 rows) |
| cheney-high-school-school-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| air-force-academy-track-school-meetpro-account | nothing new | both MeetPro accounts: nothing from 2026 |
| ohio-northern-university-school-meetpro-account | works | 2026-04-17 6th Annual- Mike Becraft Invitational (TF 16 races 173 rows) |
| university-of-indianapolis-school-meetpro-account | nothing new | both MeetPro accounts: nothing from 2026 |
| indiana-wesleyan-university-school-meetpro-account | nothing new | MeetPro account: nothing from 2026 |
| smart-event-management-llc | can't | no index of its own: reports posted on hosts' SportsEngine pages |
| live-running-results-track-timing-and-data-management-result | works | 2026-10-17 ABQ Academy Extravaganzqa (XC 10 races 1191 rows) |

## 2026-11-10: more timers

- **Arrow Live Results** (`sources/arrow.py`): one job reads every state's recent races, groups a timer's races of one day into a meet, and lands the cross country ones (34 timers in the research file post here). Arrow pages state no distance: those races land without one.
- **Race Roster** (`sources/raceroster.py`): the search index since Aug 1 plus the research file's meets; 129 school meets this season on 2026-11-10 (70 in the October research). Structured rows are mapped by column label, text reports by their header columns. A distance in the race title wins over Race Roster's 1000 m placeholder; races with no school and no grade (open races) are left out.
- **Custom timers switched on** (`registry.CUSTOM_ON`): Heartland Timing (KS), Y Timing (AR), Extra Mile Timing (CA, LAUSD leagues), Crescent City Timing (LA). A dry run of all 88 `custom` sources (`python -m sources.timers all --dry --try-kind custom`) found these parse; of the rest, 27 post only to a feed and most others list no results pages on their own site.
- **Fixed**: the meet finder looped on pages linking each other (BAYTAF, Chuck Score, Platt Systems, ScoreYourRace: RecursionError); "5 Kilometers (3.11 Miles)" read as 5005.1 m.
- **The server needs `pdftotext`** (`apt-get install -y poppler-utils`): without it every PDF report fails (Finished Results' CCAA PDFs, 2026-11-07). The timer job now says so at the start.
- **Also checked** (2026-11-10): the 123 `unknown` sources (one more on: Sub5 Track Club's hub, CO); Gopher State Events (MN) lists no meet after 2025-10-25; RunnerCard's 2026 meet pages come back empty; CCR Timing and MyChipTime are road races.
- **The OnlineRaceResults archive** is built: `bash deploy/rr_archive.sh` (resumable). Not yet: the OnlineRaceResults archive on Race Roster (about 600 school meets, 2019-2025; a one-time ~5-hour walk, needs the owner's go-ahead and the lock).
