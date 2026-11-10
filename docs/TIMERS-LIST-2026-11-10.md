# Timing companies: what we scrape and why not (2026-11-10)

Every timing company in docs/timing_companies.csv (1,137), with what happens to it now.
Sources: the registry, the 2026-11-09 dry run (docs/TIMER-SOURCES-STATUS.md) and the 2026-11-10 dry runs of the `custom` and `unknown` kinds.

| status | count |
|---|---|
| scraped | 62 |
| scraped (platform) | 47 |
| scraped (other step) | 202 |
| scraped, nothing new | 36 |
| can't (yet) | 117 |
| can't | 22 |
| not read | 651 |

## scraped (62)

| timer | states | kind | why |
|---|---|---|---|
| Aldrich Timing & Race Management | MI | meetpro | 2026-05-20 26baycounty (TF 16 races 184 rows); 2026-05-14 ELFIELDDAY (TF 30 races 348 rows) |
| AllTrax Timing | KY,TN,TX | hytek | 2023-11-02 2023 TSSAA State Cross Country Champions (XC 1 races 249 rows) |
| B & B Race KC | KS,MO | meetpro | 2026-10-19 BORDER WAR CHAMPIONSHIP 2026 - TEAM (XC 6 races 299 rows); 2026-10-17 Crossroads Conference Championship (XC 2 races 69 rows) |
| Big Red Running LLC | CO | meetpro | 2026-09-12 2026 Douglas County Middle School Champi (XC 4 races 224 rows); 2026-08-29 2026 Centaurus Bernie Gay Invitational (XC 6 races 998 rows) |
| Big River Race Management | IL,LA,MO | meetpro | 2025-11-01 UAA XC CONFERENCE CHAMPIONSHIPS (XC 2 races 155 rows) |
| Black Squirrel Timing | NE | hytek | 2026-08-27 2026 Weeping Water XC Invitational (XC 6 races 177 rows); 2026-08-27 2026 Loganview XC Invitational (XC 4 races 406 rows) |
| C.F.P.I. Timing & Data, Inc. | AL,LA,NY,SC,TN | meetpro | 2026-10-30 SOUTHLAND CONFERENCE CHAMPIONSHIPS 2026 (XC 1 races 93 rows); 2026-10-30 Southland Conference Championships 2026 (XC 2 races 191 rows) |
| Chris Quick (individual MeetPro account) | IL | meetpro | 2026-11-05 North Central College Red/White XC Open- (XC 3 races 237 rows); 2026-10-31 2026 NJCAA Region 4 Cross Country Champi (XC 4 races 135 rows) |
| Clennie Wilkins (individual MeetPro account) | NC | meetpro | 2026-10-08 Triangle Athletic Conference Championshi (XC 4 races 78 rows); 2026-10-08 riangle Athletic Conference Championship (XC 4 races 74 rows) |
| Colorado School of Mines (school MeetPro account) | CO | meetpro | 2026-04-16 Mines Midweek Invite (TF 13 races 68 rows); 2026-04-11 Mines Kit Mayer Classic (TF 8 races 214 rows) |
| Common Timing | CO | meetpro | 2026-09-12 LCXCEagleInvite26 (XC 4 races 432 rows); 2026-09-02 WCMSXC26 (XC 2 races 83 rows) |
| Crescent City Timing Corp. | LA | custom | own site parses (switched on 2026-11-10) |
| Diablo Timing | CA | hytek | 2026-11-07 EBAL Championships (XC 6 races 505 rows); 2026-11-05 CMC Championships (XC 9 races 404 rows) |
| Eagleton (MeetPro account) | TN | meetpro | 2024-05-02 Last Chance Meet @ ECCA (TF 16 races 366 rows); 2026-04-09 ECCA MS Meet #2 2026 (TF 12 races 303 rows) |
| Elite Timing & Event Management | FL | meetpro | 2026-11-07 Sunshine State Conference 2026 (XC 2 races 206 rows) |
| elitefeats | NJ,NY | hytek | 2026-11-08 CACC XC Conference Championships (XC 2 races 117 rows); 2026-11-07 NE10 Conference Championship (XC 2 races 208 rows) |
| Extra Mile Timing | CA,NV | custom | own site parses (switched on 2026-11-10) |
| Finish Line Results | KY | meetpro | 2026-09-08 Bourbon County All-Comers (XC 4 races 180 rows) |
| Finished Results | AZ,CA,NV | hytek | 2026-11-08 PacWest Conference Championships (XC 1 races 59 rows); 2026-11-08 PacWest Conference Championships (XC 1 races 80 rows) |
| Flash Results, Inc. | NC,TX | hytek | 2026-10-30 ACC Cross Country Championships (XC 2 races 324 rows) |
| FT Timing | TX | meetpro | 2026-08-15 The Honor Connor FWC Invite (XC 6 races 203 rows); 2024-02-08 Denton Bronco Invitational (TF 30 races 434 rows) |
| FTR Race Management | TX | meetpro | 2026-10-15 2026-District-18-6A-XC-Championships (XC 5 races 339 rows) |
| Goddard Schools USD 265 (school MeetPro account) | KS | meetpro | 2026-09-11 Wamego Invitational (XC 8 races 744 rows); 2026-02-07 Shocker Track Club February Freeze Track (TF 68 races 522 rows) |
| GunLap Running | FL | meetpro | 2026-11-06 2026 FHSAA 4A DISTRICT 7 XC CHAMPIONSHIP (XC 2 races 147 rows); 2026-10-14 2026 First Coast High School XC Meet 3 ( (XC 2 races 40 rows) |
| Hamilton Southeastern High School (school MeetPro account) | IN | meetpro | 2026-03-07 Blue & White Relays (TF 6 races 142 rows) |
| Heartland Racing Co. | KS,MO,OK | meetpro | 2026-04-16 2026 Victoria MS Squire Invite (TF 32 races 464 rows) |
| Heartland Timing | KS | custom | own site parses (switched on 2026-11-10) |
| Hero's Timing | MN,ND | hytek | 2025-10-04 52ND ANNUAL LIONS XC INVITATIONAL MEET OF (XC 8 races 1084 rows); 2025-10-08 LAKE SUPERIOR CONFERENCE XC CHAMPIONSHIP (XC 6 races 370 rows) -- own pages parse (2025 season); its 2026 meets are on AthleticLIVE (live.herostiming.com), read by that step |
| Howard Atkinson (individual MeetPro account) | CA | meetpro | 2026-02-28 Butte College Black and Gold at Butte Co (TF 18 races 260 rows) |
| Jacksonville Athletic Club (JAC) | FL,GA | meetpro | 2026-11-06 The Final 8 @ IMGA (TF 2 races 21 rows); 2026-09-05 2026 Parker Valby Invitational (XC 6 races 416 rows) |
| Juan Diego Reyes (individual MeetPro account) | FL | meetpro | 2026-05-05 2026 Polk County MS Meet # 2 (TF 6 races 70 rows) |
| Laurence Loesel (individual MeetPro account) | VA | meetpro | 2026-10-24 VT Time Trials 10.24.26 (TF 10 races 74 rows) |
| LetsGoRun.com | KS | meetpro | 2026-10-31 2026 Kansas Approved Schools State X-C C (XC 2 races 146 rows); 2026-10-24 2026 KSHSAA Class 6A Manhattan Sub-State (XC 4 races 290 rows) |
| Live Running Results (Track Timing and Data Management resul | NM,TX | hytek | 2026-10-17 ABQ Academy Extravaganzqa (XC 10 races 1191 rows) |
| MAC-FAT, LLC | PA | meetpro | 2026-10-31 2026 PIAA District 3 Cross Country Champ (XC 6 races 971 rows); 2026-10-17 2026 SCAC Cross Country Championships (XC 8 races 538 rows) |
| Mancos School District RE-6 (school MeetPro account) | CO,UT | meetpro | 2026-04-28 2026 San Juan Basin League Meet (TF 17 races 213 rows); 2026-04-30 2026 SJBL League Championships (TF 14 races 190 rows) |
| Meadowbrook High School (school MeetPro account) | OH,VA | meetpro | 2026-05-06 MMS Vs Coshocton, Morgan (TF 14 races 157 rows); 2026-05-05 Meadowbrook Vs Morgan, Coshocton (TF 15 races 137 rows) |
| Miller Sports Timing LLC | LA | meetpro | 2026-11-04 D6-Region 2 XC Championship (Hackberry) (XC 2 races 114 rows); 2026-10-10 Run For A Cure (Middle School) 2026 (XC 2 races 248 rows) |
| Mitchell Timing | NC,SC | meetpro | 2026-10-30 USA South Athletic Conference Cross Coun (XC 2 races 92 rows); 2026-10-30 NCISAA Cross Country Championships (XC 8 races 779 rows) |
| MOGO Timing | FL | meetpro | 2026-05-13 PBC MS Championship 2026 (TF 12 races 192 rows) |
| New River Timing | VA,WV | meetpro | 2026-04-22 Stuarts Draft High School Invitational (TF 26 races 222 rows) |
| Ohio Northern University (school MeetPro account) | OH | meetpro | 2026-04-17 6th Annual- Mike Becraft Invitational (TF 16 races 173 rows) |
| Pete Wareham (individual MeetPro account) | MN | meetpro | 2026-05-09 spa ms may 9 (TF 10 races 252 rows); 2026-05-04 carver ms (TF 34 races 438 rows) |
| Pierre Timing | NC | meetpro | 2026-11-05 Rocky River Time Trial 2026 (TF 6 races 58 rows); 2026-10-31 NJCAA Region 10 Championship (XC 8 races 78 rows) |
| Portage High School (school MeetPro account) | IN | meetpro | 2026-05-26 2026 Portage Girls IHSAA Regional (TF 11 races 152 rows); 2026-05-21 Portage Boys 2026 IHSAA Sectional (TF 10 races 132 rows) |
| Pueblo School District 60 (school MeetPro account) | CO | meetpro | 2026-05-01 The Pueblo Twilight 2026 (TF 16 races 941 rows); 2026-04-29 MS CITY CHAMP MEET (TF 12 races 212 rows) |
| Pure Gold Timing | FL | meetpro | 2025-04-08 Atlantic Last Chance (TF 16 races 334 rows) |
| Rapid Results Timing | CO | meetpro | 2026-11-07 Open Race 2026 (XC 2 races 69 rows); 2026-11-07 2026 RMAC Cross Country Championships (XC 2 races 257 rows) |
| Scott Siettmann (individual MeetPro account) | CO | meetpro | 2026-10-03 West Elk Challenge (XC 2 races 100 rows); 2026-10-02 Delta Pantherfest XC Invite (XC 4 races 566 rows) |
| Snake River Timing | ID | meetpro | 2022-03-17 Disrtict 3 4A Quad @ BK (TF 16 races 465 rows) |
| Sub5 Track Club (results hub) | ME | unknown | own site parses (switched on 2026-11-10) |
| Ticker Timing | KS,MO | hytek | 2026-11-07 Heart of America Conference XC (XC 2 races 289 rows); 2026-10-19 Baldwin Sub-State (XC 4 races 182 rows) |
| Tiffin Timing LLC | OH | meetpro | 2026-05-01 Hopewell HS Invite (TF 10 races 146 rows) |
| TimingInc.com (Timing Inc.) | SC | meetpro | 2026-10-05 2026 Tri-County Conference XC Championsh (XC 2 races 174 rows); 2026-09-12 2026 Mike Moore XC Classic (XC 3 races 662 rows) |
| Trojan Timing | NE,TN,VA | meetpro | 2026-04-17 2026 Carolina Distance Carnival (TF 4 races 1575 rows); 2026-04-10 2026 Scots Invitational (TF 23 races 184 rows) |
| TRXC Timing, LLC | IL,MA,MO | hytek | 2026-11-07 GLVC XC Championships (XC 1 races 154 rows); 2026-11-07 GLVC XC Championships (XC 1 races 134 rows) |
| trXCky | KY | meetpro | 2020-10-08 Striders High HS-MS XC Showcase (XC 2 races 75 rows); 2024-05-21 Class 3A Region 6 (TF 16 races 307 rows) |
| University of Nebraska at Kearney (school MeetPro account) | NE | meetpro | 2026-05-09 2026 Loper Twilight (TF 14 races 75 rows); 2026-04-16 2026 Ravenna Invite (TF 16 races 200 rows) |
| Westside Timing | CA | meetpro | 2026-11-04 Coastal League XC Finals 11-4-26 (XC 3 races 136 rows); 2026-11-03 Gold Coast XC Finals (XC 4 races 114 rows) |
| Wil-Time Event Management | NJ,NY,PA | meetpro | 2026-10-21 2026 SUSQUEHANNA CLUSTER (XC 4 races 116 rows) |
| Y Timing | AR | custom | own site parses (switched on 2026-11-10) |
| YenTiming Services | NY | hytek | 2026-11-07 Section V Cross Country Championships (XC 8 races 815 rows); 2026-10-31 W-FL League Championships (XC 4 races 326 rows) |

## scraped (platform) (47)

| timer | states | kind | why |
|---|---|---|---|
| 12:1 Running | OK | arrow | read by the Arrow Live Results adapter |
| 3 Way Racing (Race Roster provider "Skip Rafferty" 160; asso | KY | raceroster | read by the Race Roster adapter |
| 5Kevents.org, LLC | IL | arrow | read by the Arrow Live Results adapter |
| 915 Timing | TX | arrow | read by the Arrow Live Results adapter |
| Above and Beyond Timing | IN | arrow | read by the Arrow Live Results adapter |
| Advantage Timing (Arrow presenter 'Advantagetiming') | MN,WI | arrow | read by the Arrow Live Results adapter |
| Alan and Robin (Race Roster providers "Alan and Robin" 580,  | IA,NE | raceroster | read by the Race Roster adapter |
| athletic Scoring providers | FL | arrow | read by the Arrow Live Results adapter |
| Bloomfield Timing (Arrow presenter 'Bloomfield Timing Servic | CT | arrow | read by the Arrow Live Results adapter |
| BUZZWORD (Race Roster provider 58; association 50) | CA | raceroster | read by the Race Roster adapter |
| Bv Timing | FL | arrow | read by the Arrow Live Results adapter |
| Chipped Timing | LA | arrow | read by the Arrow Live Results adapter |
| Chuck Magera (Race Roster provider 243; association 145) | SC | raceroster | read by the Race Roster adapter |
| decania | NV | arrow | read by the Arrow Live Results adapter |
| Eastwood Running Club Timing | TX | arrow | read by the Arrow Live Results adapter |
| ELE Race Timing | CA | arrow | read by the Arrow Live Results adapter |
| End Result Company (Race Roster provider label on ORR import | IL,IN,OR | raceroster | read by the Race Roster adapter |
| ERC Timing Solutions (Race Roster providers "Adam's Crew" 57 | AZ,IA,IL,NM,WI | raceroster | read by the Race Roster adapter |
| FlamesTC | TX | arrow | read by the Arrow Live Results adapter |
| Fleet Feet St. Louis (Race Roster provider "Jake" 149; assoc | MO | raceroster | read by the Race Roster adapter |
| Florida Race Day | FL | arrow | read by the Arrow Live Results adapter |
| Hardy Race Timing Services | GA | arrow | read by the Arrow Live Results adapter |
| Ignite Your Event (Race Roster provider 244; association 75) | MI | raceroster | read by the Race Roster adapter |
| IW2R / iwant2run (Race Roster providers "IW2R" 192, "IWant2R | IN | raceroster | read by the Race Roster adapter |
| J & M Timing Service | CA | arrow | read by the Arrow Live Results adapter |
| J7 Sports Event Management | FL | arrow | read by the Arrow Live Results adapter |
| Josh Carpenter (timer) | MI | arrow | read by the Arrow Live Results adapter |
| McDuffie Timing | LA | arrow | read by the Arrow Live Results adapter |
| Milepost Adventures | OR | arrow | read by the Arrow Live Results adapter |
| MOGO Timing, Inc. | FL | arrow | read by the Arrow Live Results adapter |
| Peak Performance Timing | WA | arrow | read by the Arrow Live Results adapter |
| Perfect Timing Race Management | IN | arrow | read by the Arrow Live Results adapter |
| Positive Vibes - Run Positive (Arrow presenter 'PositiveVibe | FL | arrow | read by the Arrow Live Results adapter |
| RCC Timing (Indiana) | IN,MI | arrow | read by the Arrow Live Results adapter |
| RPM Timing Solutions | CA | arrow | read by the Arrow Live Results adapter |
| RTS Race Timing | CA | arrow | read by the Arrow Live Results adapter |
| Run Roanoke (Arrow presenter 'Run roanoke LLC') | VA | arrow | read by the Arrow Live Results adapter |
| Run The Race Timing | MS | arrow | read by the Arrow Live Results adapter |
| RUN2WIN Timing (Race Roster provider 421) | TX | raceroster | read by the Race Roster adapter |
| Terkhorn Timing | KY | arrow | read by the Arrow Live Results adapter |
| Trinity Timing (ORR import provider label) | IN | raceroster | read by the Race Roster adapter |
| Unnamed northern Wisconsin / Upper Peninsula timer (Race Ros | MI,WI | raceroster | read by the Race Roster adapter |
| Viper Timing Systems | NJ | arrow | read by the Arrow Live Results adapter |
| Watt's My Time | IN | arrow | read by the Arrow Live Results adapter |
| We Run Faster | TN | arrow | read by the Arrow Live Results adapter |
| XM Timing | WI | arrow | read by the Arrow Live Results adapter |
| Yankee Timing | MA | custom | read by the iResultsLIVE adapter |

## scraped (other step) (202)

| timer | states | kind | why |
|---|---|---|---|
| 3B Timing (WiscoTrack) | WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| 417 Running | MO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| 5 North Timing (5 North Track) | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| ADE Timing / Athletic Directors Edge | IA,IL,MI,MN,WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AdkinsTrak Timing Associates | KY,LA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AdkinsTrak West | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Advanced Sports Timing LLC | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Advantage Timing (probable) | MN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| All-American Timing, LLC | IA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Alpha Timing LLC | IN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Altitude Endurance Timing Co. | CO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Anything Athletics | MS | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Armory Track (armorytrack.live) | NY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: 910athletics |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: bluefox |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: bolttiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: buffalo |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: bwracing |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: cctiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: chippedtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: enmotive |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: falcon |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: flracing |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: fortistiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: fstiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: fttiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: gigarun |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: greenferrettiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: halotiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: hdtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: highspeed |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: hnscats |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: iptt |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: irvine |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: jdl |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: jmtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: justfortrack |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: kcrc |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: kent |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: klearfinishtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: kleintiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: ledgestiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: lightspeedtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: lsatiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: lto |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: matstiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: mocotiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: montiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: ntsg |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: ohyesathletics |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: onyourmarkscol |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: ottcenter |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: phototiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: prathletics |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: premierresults |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: rapidtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: rpmtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: rtc |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: rts |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: sportmadesimple |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: sst |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: swtiming |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: thunder |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: trackmac |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: trackntime |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: vwmgmt |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| AthleticLIVE tenant: westlake |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Atlantic Coast Timing Systems | SC,VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Badger State Timing | WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Baltimore City Schools athletics | MD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Baltimore County Public Schools athletics | MD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Batavia Track | NY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Beartooth Track | MT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Bell Lap Timing | NC | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Bloomfield Timing | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Blue Ridge Timing | VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Bucknell Bison athletics | PA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Bulldog Timing | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Bullseye Running | DC,DE,MD,PA,VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| CalTiming | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| CCA TF (probable) | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Champion By Choice Timing | MS | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Championship Timing | MO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Charlotte Flights Timing | NC,SC | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Christopher Newport University T&F/XC (CNU) | VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| CHT Timing | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Clock It Timing | LA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Colorado Elite Timing | CO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Competitive Timing LLC | MT,WY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Dakota Timing | ND,SD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Denver Athletics | CO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| DL Pro Timing | MI,OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| DoMore Race Services, Inc | TX | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Endurance Race Timing (probable) | IL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| ENDURO Timing (Enduro Timing Network) | OK,TX | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Fast Finish Results | MN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Final Lap Timing Group | VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Finish Right Timing | NY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| First Lane Timing | MD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| First Time Out | MA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Flash Results Texas | TX | custom | AthleticLIVE tenant: read by the AthleticLIVE step |
| FlexTiming.Pro (TriStateRacer.com) | CONFLICT between resear | KY,OH,WV | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Fulton Accurate Timing | NY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Gingerbread Timing (Gingerbread Man Running Co.) | PA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Gold Medal Timing | WA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| GrimesTimes | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Grizzly Peak Timing | OR | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Happy Pace Timing | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Harrier Race Management | MA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Hartford Timing | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| High Speed Timing & Quick Lizard Timing | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| IC4A Timing | MA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| JAC Timing | FL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| JW Timing Group LLC | TX | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| K2 Timing, LLC | WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Knight Timing of Ellington (probable owner of ellingtontimin | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Knoxville Youth Athletics | TN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| KVT&FOA (Kanawha Valley Track & Field Officials Assoc.) | WV | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| L.A.B. Track Club (LAB TC) timing | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Lakeshore Athletic Services | IA,IL,IN,MI,OH,WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Lexicon Timing | NJ,PA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| LT Timing (probable) | IL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| M&D Timing, LLC | DC,DE,MD,NJ,PA,VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mankato East (school) | MN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mantronix Sports | MD,NC,VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| MAStiming.net | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| McCorory Meet Management Solutions (M3) | VA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mentzer Timing | CA,NV | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mercury Timing Systems | MA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Metro League (WA) | WA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mid South Race Timing | KY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Midwest Timing & Results | KS,MO,OK | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Millennium Running | NH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mitten Timing | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| MoFast Timing | MO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Mountain Timing Associates | WV | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| MSTCA (Mass. State Track Coaches Assoc.) | MA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Nevada Track Stats (NTS-Timing) | AZ,NV,UT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Northeast Sports Timing | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Northeast Sports Timing (duplicate of existing row) | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Northstar Timing | MN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| NTX Timing | OK,TX | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Olympics Edge Timing | WA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| OnTheDot Timing | AL,FL,GA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| OnYourMarks Timing | WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| OPE Racing (operacing.com) | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Palatine Pack Timing | IL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Payne's Corner Timing | CT | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Peak Performance Timing (probable WA) | WA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Pella Community Schools (Pella Dutch athletics) | IA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Performance Timing | WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Pioneer Timing | DE,NJ,PA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Pirate Timing LLC | MS | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| PNTFO (Pacific NW T&F Officials) | OR,WA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Port City Timing, LLC (Kevin Jones) | AL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Precision Timing Services |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Pymatuning Valley Timing | OH,PA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| QW Timing | MD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Race Timers (probable) | AZ | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Raceplace Events |  | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Record Timing (RT Timing Solutions) | CA,NE | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Red Dirt Running | OK | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Rhodes Race Timing | NC | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| River City Race Management (Race Results Plus) | IL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Roadrunner Race Timing (probable) | AZ | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Royal Results | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Run Nebraska | NE | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| run.tf | PA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Runaround Racing | MO | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Sanger Track and Field (Sanger HS) | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| SB Race Timing | WA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Scoles Sports Timing | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| SEO Timing | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| ShaZam Racing | GA,IL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| SMAC League Timing (Southern Maryland Athletic Conference) | MD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Speed Timing | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Speeding Bullet Timing | CA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Speedy Sneakers | CONFLICT between research passes: Speedy S | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Stuart Road Racing LLC | IN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Sub4Sports | IA,KS,NE | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Superior Timing | MI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| TDeming Timing and Events | NC | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Tempo Run Timing | IN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| The Perfect Timing Group | GA | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| The Timing Crew | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Timing First, LLC | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Timing King | KY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Timing MD | IL,IN | custom | AthleticLIVE tenant: read by the AthleticLIVE step |
| TimingSpot | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Tomahawk Timing | NJ | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Trackqua | NY | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| TrackSide Timing | WI | custom | AthleticLIVE tenant: read by the AthleticLIVE step |
| Ultimate Sports Productions (USP Sports) | DE | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| UpStart Timing | NH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| USA Race Timing & Event Management | OH | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| USATF Oregon | OR | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| UW-Whitewater Track | WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Wayzata Results, LLC | IA,MN,ND,SD,WI | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Western MD Track Timing | MD | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Wingfoot Finish | AZ | custom | AthleticLIVE tenant: read by the AthleticLIVE step |
| WWS Timing | IL | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |
| Xpress Timing | AL,GA,MO,MS,NC,TN | athleticlive | posts to AthleticLIVE: read by the AthleticLIVE step |

## scraped, nothing new (36)

| timer | states | kind | why |
|---|---|---|---|
| 2L Race Services | PA | meetpro | AthleticLIVE tenant (its own step reads it); MeetPro account has nothing from 2026 |
| Air Force Academy track (school MeetPro account) | CO | meetpro | both MeetPro accounts: nothing from 2026 |
| Athletic Timing | OR | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| AthleticLIVE tenant: atmmeetmanagement | MI | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| AthleticLIVE tenant: pfv | IL | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| Butler Track (MeetPro account) |  | meetpro | its one 2026 meet (CSAC MS) has no result pages posted |
| CAG's Timing | VA | meetpro | own meets (read now via their onclick links) end with 2025 |
| Cheney High School (school MeetPro account) | KS,WA | meetpro | MeetPro account: nothing from 2026 |
| Dawn Driver (individual MeetPro account) | TX | meetpro | newest 2026 folders: a Canadian club meet (left out) and a throws meet; others wait for later nights |
| DC Timing | KY | meetpro | AthleticLIVE tenant; no MeetPro account found |
| Delta Timing Group | TN | meetpro | own site answers 530 (down); MeetPro account (Delta_Timing, 21 files): nothing from 2026 |
| Dennis Weber (individual MeetPro account) | MO | meetpro | MeetPro account: nothing from 2026 |
| Fayetteville Flyers | NC | hytek | nothing posted since 2025; links out to other platforms |
| FrontRunner Timing (MeetPro account) |  | meetpro | MeetPro account: nothing from 2026 |
| Illinois Prep Top Timing | IL | meetpro | its one 2026 folder (Chi Town Miles) holds no individual races |
| Indiana Wesleyan University (school MeetPro account) | IN | meetpro | MeetPro account: nothing from 2026 |
| James Island Charter High School (school MeetPro account) | SC | meetpro | MeetPro account: nothing from 2026 |
| Jeff Offringa (individual MeetPro account) | MI | meetpro | 2026 folders are relays meets (Watervliet Relays): no individual races |
| Kauder Racing, LLC (Kauder Racing Results Service) | IA | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| Ken Grace (individual MeetPro account) | CA | meetpro | MeetPro account: nothing from 2026 |
| Kick To The Finish, LLC | MA | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| LT (MeetPro account) | IL | meetpro | MeetPro account: nothing from 2026 |
| Meeshelia Blackwell (individual MeetPro account) |  | meetpro | MeetPro account: nothing from 2026 |
| MP Timing | NC | meetpro | own page lists no meets; MeetPro account: nothing from 2026 |
| On The Mark Timing | OH | meetpro | own page's links are URL shorteners (not followed); account's only 2026 folder is a combined-events meet |
| Pirate Timing (Durham NC) | NC | hytek | its one results link is a Feb 2022 meet |
| River Forest Meet Timing | IL | meetpro | MeetPro account: nothing from 2026 |
| Skip Rafferty (individual MeetPro account) | KY | meetpro | MeetPro account: nothing from 2026 |
| Slipstream Timing & Meet Management | TN | meetpro | own pages and both MeetPro accounts: nothing from 2026 |
| Snap Timing | VA | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |
| Swobie Timing | LA | meetpro | page on record is a feed's; MeetPro account: nothing from 2026 |
| Texico Schools (school MeetPro account) | NM | meetpro | both MeetPro accounts: nothing from 2026 |
| Trey Jackson (individual MeetPro account) | MD | meetpro | its newest 2026 folders are empty templates (results never uploaded); older ones wait for later nights |
| University of Indianapolis (school MeetPro account) | IN | meetpro | both MeetPro accounts: nothing from 2026 |
| WilderResults | KY | meetpro | page on record is a feed's; both MeetPro accounts: nothing from 2026 |
| Windsor Running, LLC | CONFLICT between research passes: Win | DC,MD,VA | meetpro | AthleticLIVE tenant; MeetPro account has nothing from 2026 |

## can't (yet) (117)

| timer | states | kind | why |
|---|---|---|---|
| 802 Timing | NY,VT | custom | no results links on its own site (script-built or road-only) |
| A2 Race Management | IN,MI,OH | custom | no results links on its own site (script-built or road-only) |
| About Timing | CA | unknown | pages found but none parse as individual results: no results page on record |
| Absolute Race Timing | IL,WI | unknown | pages found but none parse as individual results: no results page on record |
| AccuRace Timing Services | IA,IL,MN,WI | custom | no results links on its own site (script-built or road-only) |
| ActNow Promotions (ActNow Racing) | AR,KS,MO | custom | no results links on its own site (script-built or road-only) |
| AllSports Timing | TX | custom | no results links on its own site (script-built or road-only) |
| Andy Newing (individual timer) | WA | unknown | pages found but none parse as individual results: no results page on record |
| Apple Raceberry JaM | MN | custom | no results links on its own site (script-built or road-only) |
| APTiming (Appalachian Timing Group LLC) | WV | custom | no results links on its own site (script-built or road-only) |
| ATX Timing | TX | custom | no results links on its own site (script-built or road-only) |
| Automattic Timing | NV | unknown | pages found but none parse as individual results: no results page on record |
| BAYTAF | FL | custom | pages found but none parse as individual results: discover failed: RecursionError: maximum recursion depth exc |
| Bell Lap Productions | WA | unknown | no results links on its own site (script-built or road-only) |
| Big Dawg Runnin' | FL | unknown | pages found but none parse as individual results: no results page on record |
| Blue Valley Timing | KS | unknown | no results links on its own site (script-built or road-only) |
| Brent Endsley Timing | OH | unknown | pages found but none parse as individual results: no results page on record |
| CCR Timing | NM | custom | no results links on its own site (script-built or road-only) |
| Central Time (timing) | TX | unknown | pages found but none parse as individual results: no results page on record |
| Chuck Block Timing | MI | unknown | pages found but none parse as individual results: no results page on record |
| Chuck Score Timing Services | TX | custom | pages found but none parse as individual results: discover failed: RecursionError: maximum recursion depth exc |
| Coastal Timing and Data | SC | unknown | no results links on its own site (script-built or road-only) |
| Colby Branch (individual timer) | MO | unknown | pages found but none parse as individual results: no results page on record |
| Colorado Race Timing | CO | unknown | no results links on its own site (script-built or road-only) |
| Creative Timing Solutions | IN,NE,OH | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| CSTS LLC | TX | unknown | pages found but none parse as individual results: no results page on record |
| Dan Ward Timing | VA | unknown | pages found but none parse as individual results: no results page on record |
| Dinwiddie Timing Crew | VA | unknown | pages found but none parse as individual results: no results page on record |
| DRC Sports | FL | custom | no results links on its own site (script-built or road-only) |
| DVGTCA | PA | custom | no results links on its own site (script-built or road-only) |
| EA Timing and Scoring | TX | custom | no results links on its own site (script-built or road-only) |
| Eclectic Edge Racing | OR | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| Elemental Race Timing | NV | custom | no results links on its own site (script-built or road-only) |
| Endurance House Timing | TX | unknown | pages found but none parse as individual results: no results page on record |
| EPI Sports | CA | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| Epic Race Timing | IL,IN,MI | custom | no results links on its own site (script-built or road-only) |
| FastFeet | AL,GA,NC,SC,TN | custom | pages found but none parse as individual results: 3 meet pages, 2 new |
| Finish Line Timing | CO,WY | custom | no results links on its own site (script-built or road-only) |
| Finish Timing | OH | custom | no results links on its own site (script-built or road-only) |
| First Call Timing | GA | custom | no results links on its own site (script-built or road-only) |
| Ford Timing | CA | custom | no results links on its own site (script-built or road-only) |
| Fraulo Race Timing | NJ | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| GC Racing | WA | custom | no results links on its own site (script-built or road-only) |
| GHG Timing | OH | unknown | pages found but none parse as individual results: no results page on record |
| Glen Allen Timing | VA | unknown | pages found but none parse as individual results: no results page on record |
| Godspeed Timing | FL | custom | no results links on its own site (script-built or road-only) |
| GSE Timing (Gopher State Events) | IA,MN,ND,SD,WI | custom | no results links on its own site (script-built or road-only) |
| Half-Mile Timing | FL | custom | no results links on its own site (script-built or road-only) |
| Hawkeye Timing | WI | custom | no results links on its own site (script-built or road-only) |
| High Performance Timing, LLC | AL,GA | unknown | pages found but none parse as individual results: no results page on record |
| Hill Country Timing, LLC | TX | unknown | pages found but none parse as individual results: no results page on record |
| Huber Timing, LLC | OR,WA | custom | no results links on its own site (script-built or road-only) |
| IndyTiming Service | IN | custom | no results links on its own site (script-built or road-only) |
| It's About Timing | OK,TX | unknown | pages found but none parse as individual results: no results page on record |
| JBAC Timers | NJ | unknown | pages found but none parse as individual results: no results page on record |
| Just In Time Racing | NY | custom | pages found but none parse as individual results: 3 meet pages (via 24 sub-index pages), 2 new |
| Kandu Timing | CO | custom | no results links on its own site (script-built or road-only) |
| Ken Emerick (timer) | WA | unknown | pages found but none parse as individual results: no results page on record |
| Lightning Event Management | OH | unknown | no results links on its own site (script-built or road-only) |
| Live Running Results | TX | unknown | pages found but none parse as individual results: 237 meet pages (via 5 sub-index pages), 2 new |
| MacDonald Timing, LLC | AR | unknown | pages found but none parse as individual results: 3 meet pages (via 1 sub-index pages), 2 new |
| Maryland Race Timing | MD | unknown | no results links on its own site (script-built or road-only) |
| Maryland Race Timing (Western Maryland Race Timing) | MD | custom | no results links on its own site (script-built or road-only) |
| Meetregister LLC | AZ,CA,NV | unknown | no results links on its own site (script-built or road-only) |
| Michiana Timing | IL,IN,MI,WI | custom | no results links on its own site (script-built or road-only) |
| Milestone Race Authority | FL | custom | pages found but none parse as individual results: 3 meet pages, 2 new |
| Motion Timing | DE,MD,PA | custom | no results links on its own site (script-built or road-only) |
| My Chip Timing | TX | custom | no results links on its own site (script-built or road-only) |
| Network Timing, LLC | KY,NC,SC,TN,VA | custom | no results links on its own site (script-built or road-only) |
| Nick Lawson (individual timer) | FL | unknown | pages found but none parse as individual results: no results page on record |
| Northshore Timing Services | WA | unknown | pages found but none parse as individual results: no results page on record |
| On Track Timing | MD,PA | unknown | pages found but none parse as individual results: no results page on record |
| Photo Finish Timing, LLC | IN | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| Platt Systems Timing | CT | custom | pages found but none parse as individual results: discover failed: RecursionError: maximum recursion depth exc |
| PMV Timing | VA | unknown | pages found but none parse as individual results: no results page on record |
| Power E Timing | KS | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| ProRaceTiming | ID | unknown | no results links on its own site (script-built or road-only) |
| PTFCA | PA | custom | no results links on its own site (script-built or road-only) |
| Racemaker Productions | IN | unknown | pages found but none parse as individual results: no results page on record |
| RacePenguin | OH | custom | pages found but none parse as individual results: 3 meet pages, 2 new |
| Radar Timing Service | CA | unknown | pages found but none parse as individual results: no results page on record |
| Raintree Timing | VA | unknown | pages found but none parse as individual results: no results page on record |
| RealTiming Inc. | IL | custom | no results links on its own site (script-built or road-only) |
| River City Races | CO | custom | pages found but none parse as individual results: 3 meet pages, 2 new |
| Rogue Timing | CA | custom | pages found but none parse as individual results: 2 meet pages, 2 new |
| RunFAR Racing Services, Inc. | TX | custom | no results links on its own site (script-built or road-only) |
| Runner's High | VA | unknown | pages found but none parse as individual results: no results page on record |
| RunnerCard | TX,UT | custom | no results links on its own site (script-built or road-only) |
| ScoreYourRace.com | NY | custom | pages found but none parse as individual results: discover failed: RecursionError: maximum recursion depth exc |
| Scullion's Timing | NC,NJ | unknown | pages found but none parse as individual results: no results page on record |
| Seth Brower (individual timer) | TX | unknown | pages found but none parse as individual results: no results page on record |
| Sin City Timing | NV | unknown | pages found but none parse as individual results: no results page on record |
| Skinny Raven Sports | AK | unknown | pages found but none parse as individual results: no results page on record |
| Spokane Sports | WA | unknown | no results links on its own site (script-built or road-only) |
| SportTrax / MeetTrax (platform + timing) | UT | custom | no results links on its own site (script-built or road-only) |
| Start 2 Finish (timing) | MO | unknown | pages found but none parse as individual results: no results page on record |
| Steeple Timing and Event Management |  | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| T&H Timing | IN | custom | pages found but none parse as individual results: 3 meet pages (via 1 sub-index pages), 2 new |
| Terminal Velocity Timing | CA | unknown | pages found but none parse as individual results: no results page on record |
| The Runner's High (RunHigh.com) | PA | custom | no results links on its own site (script-built or road-only) |
| Timberline Events LLC | WY | unknown | no results links on its own site (script-built or road-only) |
| Timberwolves Timing | CA | unknown | pages found but none parse as individual results: no results page on record |
| Timed Events | FL | custom | pages found but none parse as individual results: 3 meet pages (via 1 sub-index pages), 2 new |
| TNT Timing | NY | custom | no results links on its own site (script-built or road-only) |
| Tortoise and Hare Race Management | MI,WI | unknown | pages found but none parse as individual results: no results page on record |
| TrackLife Timing | VT | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| Triangle Timing | MA | custom | no results links on its own site (script-built or road-only) |
| Tumey Timing & Data | DE,NJ,PA | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| Tyler Timing | ME | custom | no results links on its own site (script-built or road-only) |
| Vector Timing & Race Management | ID | unknown | no results links on its own site (script-built or road-only) |
| WEW Timing | VA | unknown | pages found but none parse as individual results: no results page on record |
| White River Sports Timing | MN | unknown | pages found but none parse as individual results: no results page on record |
| Wilbur Race Systems | MA,RI | unknown | pages found but none parse as individual results: no results page on record |
| Wire2Wire Running | GA | custom | no results links on its own site (script-built or road-only) |
| Xcelerating Time (XMG Athletics) | VA | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| Xplosion Timing | MO | unknown | pages found but none parse as individual results: robots.txt unreachable (host down or refusing) |
| YR Timing | TX | unknown | pages found but none parse as individual results: no results page on record |

## can't (22)

| timer | states | kind | why |
|---|---|---|---|
| Big Kahuna Timing | NC | custom | index HTTP 409 |
| BoComServ Meet Management Specialists | OH,PA,WV | dead | domain gone |
| Eagle Eye Timing | KS,MO | hytek | results go to MileSplit Live; its one own file is a field-events report |
| EZ Meet Services | CT | hytek | no index of its own: reports posted by hosts and leagues |
| FMTiming | NY | hytek | no index of its own: reports posted by hosts |
| Foothills Timing and Meet Management | NC | hytek | no index of its own: its reports are posted by hosts (NCHSAA) |
| In Stride Timing | RI | hytek | results go to MileSplit Live (a feed): nothing of its own to read |
| K&J Timing, LLC | CONFLICT between research passes: K&J Timi | WV | dead | domain gone |
| KTCCCA (Kentucky Track & XC Coaches Assoc.) | KY | hytek | results go to MileSplit; own pages end 2013 |
| Lancer Timing Services | MA,NH | hytek | OFF LIMITS: TrackScoreboard app; its api.trackscoreboard.com robots.txt is Disallow: / |
| Leone Timing and Results Services | NY | blocked | terms forbid bots |
| Mac's Sports Timing | MI | blocked | terms forbid bots |
| MeetPro (DirectAthletics, Inc.; FloSports-owned since June 2 | KS | meetpro | the software vendor, not a timer: no meets of its own |
| Payne's Corner Timing | CT | hytek | season page is a script-rendered Google Sheet with no links; results go to athletic.net |
| PrimeTime Timing (PTTiming) | WI | blocked | terms forbid bots |
| Pro-Fit Race Timing | TX | unknown | index HTTP 404 |
| RT Results (rtspt.com) | CA | meetpro | OFF LIMITS: robots.txt Disallow: / for every crawler |
| Run Texas Timing Services | TX | hytek | its PDFs (on Constant Contact storage) print no meet date, so cannot be rated on a day; the same meets are on MileSplit; MeetPro account: nothing from 2026 |
| satrackresults | TX | hytek | OFF LIMITS: linktr.ee robots.txt disallows |
| Smart Event Management LLC | GA | hytek | no index of its own: reports posted on hosts' SportsEngine pages |
| Track Timing and Data Management LLC | FL,NM,TX | hytek | own domain does not resolve; its live results are Live Running Results' (that source, now working) |
| Uberthons | OR,WA | custom | index HTTP 404 |

## not read (651)

| timer | states | kind | why |
|---|---|---|---|
| 1st Place Sports | FL | milesplit | posts only to MileSplit (FloSports feed) |
| 2 Girls and a Clock, LLC | SC | milesplit | posts only to MileSplit (FloSports feed) |
| 321Timing | FL | custom | its results page is a feed's (MileSplit etc.) |
| 3D Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| 5k Sports | ME | milesplit | posts only to MileSplit (FloSports feed) |
| 5RunnersRacing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| AAA Racing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| AACPS | MD | milesplit | posts only to MileSplit (FloSports feed) |
| AAMSL timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Abernathy ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Abilene ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Accu-Split Timing Co. | IA | unknown | its results page is a feed's (MileSplit etc.) |
| AccuData Timing Service | KS | unknown | its results page is a feed's (MileSplit etc.) |
| Action Carolina | SC | milesplit | posts only to MileSplit (FloSports feed) |
| AD Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Adirondack Coast Events | NY | milesplit | posts only to MileSplit (FloSports feed) |
| ADS (timer) | AZ | unknown | its results page is a feed's (MileSplit etc.) |
| Agee Race Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Al Dorner | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Albemarle Race Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Alien City Timing | NM | unknown | its results page is a feed's (MileSplit etc.) |
| All Sports Events | ME | custom | its results page is a feed's (MileSplit etc.) |
| All Sports Timing & Race Management | OH | milesplit | posts only to MileSplit (FloSports feed) |
| All Stat Timing | MN | unknown | its results page is a feed's (MileSplit etc.) |
| All The Times Live LLC | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Alma Bryant high school | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Alpha & Omega Sports | PA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Alta Vista Sports | FL | milesplit | posts only to MileSplit (FloSports feed) |
| American Race Timing and Management,LLC | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Anderson County Track Club | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Anderson Race Management | MN | unknown | its results page is a feed's (MileSplit etc.) |
| Andrew Turner Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Ansin Sports Complex | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Antelope Racing | UT | unknown | its results page is a feed's (MileSplit etc.) |
| Apple City Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| APS Athletics timing | NM | unknown | its results page is a feed's (MileSplit etc.) |
| APTiming | WV | milesplit | posts only to MileSplit (FloSports feed) |
| Arnold Rock Timing Company | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Arsenal Events | VA | milesplit | posts only to MileSplit (FloSports feed) |
| ART - Artley Race Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| ART Timing Solutions | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Ascent Sports Technology | NE | unknown | its results page is a feed's (MileSplit etc.) |
| Asheboro XC/TF Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Astronaut Track Club | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Athlete Guild | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Athletes Edge | NM | unknown | its results page is a feed's (MileSplit etc.) |
| Athletics Scoring Providers | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Atlanta Track Club | GA | custom | its results page is a feed's (MileSplit etc.) |
| B&G Timing | MD | milesplit | posts only to MileSplit (FloSports feed) |
| B2R Timing | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| B3R Promotions | FL | milesplit | posts only to MileSplit (FloSports feed) |
| BA Blue Devil Timing Co | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Baghdasarian Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Baldassari & Leetsma Timing | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Balmorhea Bears | TX | milesplit | posts only to MileSplit (FloSports feed) |
| BART Race Service | ME | milesplit | posts only to MileSplit (FloSports feed) |
| Basilio Mendoza Running LLC | TX | milesplit | posts only to MileSplit (FloSports feed) |
| BC Sports Timing | CT | milesplit | posts only to MileSplit (FloSports feed) |
| Beach Bum Timing | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Beach Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Best Racing Systems | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Best Times | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Big Lenbowski Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Big Red Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Big White Trailer | PA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| BisonBlue | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Black-Jack Track Event Timing & Meet Management | OH | unknown | its results page is a feed's (MileSplit etc.) |
| Blaze Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Blue & Gold Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Blue Circuit Oaks | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Blue Hedgehog Race Management | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Blue Ridge Classic Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Boo Williams Sportsplex | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Bowie High School Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| BravesTiming | MS | milesplit | posts only to MileSplit (FloSports feed) |
| Brewer Timing Services | ME | milesplit | posts only to MileSplit (FloSports feed) |
| Bristow Triathlon & Swim | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Brookville Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Broughton HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Bruins Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Bryn Mawr Racing Company | PA | milesplit | posts only to MileSplit (FloSports feed) |
| BS Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Buchanan Timing | CA | unknown | its results page is a feed's (MileSplit etc.) |
| Buckeye Timing | OH | unknown | its results page is a feed's (MileSplit etc.) |
| Butts County Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| BW Sports Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| C & L Tech Workz | AR | milesplit | posts only to MileSplit (FloSports feed) |
| C3 Timing | KY | custom | its results page is a feed's (MileSplit etc.) |
| Cajun Timing | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Cal Murdock Timing | IA | unknown | its results page is a feed's (MileSplit etc.) |
| California Area School District | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Can't Stop Timing Co. | OH | milesplit | posts only to MileSplit (FloSports feed) |
| Carolina Running Company | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Carolina Time Machine | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Carpet Capital Running Club | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Cavern City Timing | NM | unknown | its results page is a feed's (MileSplit etc.) |
| CBE Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| CCAISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| CD Timing | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Central Kentucky Race Management | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Champion Timing LLC | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Chapel Hill High Track Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Charger Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Charlestowne Road Race Services | SC | milesplit | posts only to MileSplit (FloSports feed) |
| CharmCityRun | MD | milesplit | posts only to MileSplit (FloSports feed) |
| Chattanooga Track Club | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Chautauqua Striders, Inc. | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Chester County Racing Services | PA | milesplit | posts only to MileSplit (FloSports feed) |
| CK Timing & Event Management | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| CKET Timing LLC | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Clark Nocentelli Timing / Nocentelli Timing | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Clarksville Track Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Classic Race Services | GA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Clearfield Track and Field | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Clemente Performance & Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Clockwork Race Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Club Connecticut | CT | milesplit | posts only to MileSplit (FloSports feed) |
| CMTrack | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Coach Gilbert T&F | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Colonial Sports | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Colonial Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Colorado Running Company | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Colts Neck HS | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Compton Time and Measure | TX | milesplit | posts only to MileSplit (FloSports feed) |
| CompuScore | NJ | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Cookeville High School Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Cowtown Timing | OK,TX | milesplit | posts only to MileSplit (FloSports feed) |
| CP Timing Fast Start Athletics | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Croatan Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| CrossPlex Athletic Facility | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Crossroads Timing (OK) | OK | milesplit | posts only to MileSplit (FloSports feed) |
| Crossroads Timing Company | IN,MI,NV | milesplit | posts only to MileSplit (FloSports feed) |
| Cruces Timing Crew | NM | unknown | its results page is a feed's (MileSplit etc.) |
| Cuero ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Cuthbertson HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| CW Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| CW Track and Field | TX | milesplit | posts only to MileSplit (FloSports feed) |
| D11 Timing (Colorado Springs School District 11) | CO | unknown | its results page is a feed's (MileSplit etc.) |
| d5 Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Dallastown High School Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Darlington Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Dasher's, Inc. | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Dave Clark Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Dave Davis | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Day Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Dayton Timing Service | IA | unknown | its results page is a feed's (MileSplit etc.) |
| DCPS Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Denton ISD Track and Field | TX | milesplit | posts only to MileSplit (FloSports feed) |
| DePaul Mittman | NC | milesplit | posts only to MileSplit (FloSports feed) |
| DF Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| DH Conley High School Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Diamond H Services | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Diva Timing | MS | milesplit | posts only to MileSplit (FloSports feed) |
| Doddridge County | WV | milesplit | posts only to MileSplit (FloSports feed) |
| Down East Sports Timing | ME | milesplit | posts only to MileSplit (FloSports feed) |
| Durham Striders Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| DV Timing | AZ | unknown | its results page is a feed's (MileSplit etc.) |
| DVG Timers | PA | milesplit | posts only to MileSplit (FloSports feed) |
| E & M Timing Services | TX | milesplit | posts only to MileSplit (FloSports feed) |
| E4timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Eagle Timing | CT | milesplit | posts only to MileSplit (FloSports feed) |
| EagleEye LLC (Sutton NE) | NE | unknown | its results page is a feed's (MileSplit etc.) |
| East Pennsboro Area High School | PA | milesplit | posts only to MileSplit (FloSports feed) |
| East Rowan | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Eastern Alamance HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Ed Colona | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Edinburg CISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| EE Timer Services | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Elevate Timing & Meet Management, LLC | OK | milesplit | posts only to MileSplit (FloSports feed) |
| Elite Athletic Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Elite Runners Racing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Elite Timing (Athens GA) | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Ellwood City Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Emrey Track & Field Association | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Endurance Race Services | GA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Endurance Sports Management | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Endurance Sports Timing | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Enigma Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| EP Knights Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Eric Fritz/Reagan HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Erik Van Laecken (timer) | SD | unknown | its results page is a feed's (MileSplit etc.) |
| Essex TCA | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Event Technical Services, Inc | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Exact Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Extra Mile Race Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| F.A.T. Cats | NM | unknown | its results page is a feed's (MileSplit etc.) |
| Fairchild Sports Timing | MI | unknown | its results page is a feed's (MileSplit etc.) |
| Fairfield ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Falcon Timing | CA | milesplit | posts only to MileSplit (FloSports feed) |
| Falls City ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Fast Accurate Timing LLC | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Fast Pace Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Fast Track Timing | CT | milesplit | posts only to MileSplit (FloSports feed) |
| Fastrak Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| FAT Dog Timing | KS | milesplit | posts only to MileSplit (FloSports feed) |
| FAT Timing (Aurora) | IL | unknown | its results page is a feed's (MileSplit etc.) |
| FHRanger Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Finger Lakes Runners Club | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Finish Line Specialist / Clyde Neff Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Finish lynx timing system | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Finish Well Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| First Across Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Fisher Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| FITniche Events | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Flashtek Timing | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Flatlander Sports | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Fleet Express | NC | milesplit | posts only to MileSplit (FloSports feed) |
| FloDash Events LLC | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Florida Road Race Management | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| FM Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Footworks | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Fort Lauderdale Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Fort Myers Track Club | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Four Horsemen | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Friend Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Frisco Running Company | TX | milesplit | posts only to MileSplit (FloSports feed) |
| G-Whizz Timing | MS | milesplit | posts only to MileSplit (FloSports feed) |
| GAPPS | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Garden City HS | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Garman Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| GARNER TIMING | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Gayle's Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| GCXC Racing (Greater Cleveland XC) | OH,PA,WV | milesplit | posts only to MileSplit (FloSports feed) |
| George Febles - CHSAA | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Germantown Academy TF | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Go Meets LLC | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Go Race Productions | SC | custom | its results page is a feed's (MileSplit etc.) |
| Go Tri Events | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Go! Sports Timing and Events | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Goliad Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Granite State Race Services | NH | milesplit | posts only to MileSplit (FloSports feed) |
| Greater Lowell Road Runners | MA | milesplit | posts only to MileSplit (FloSports feed) |
| Green Light Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Greenhill Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Greensboro Pacesetters | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Greyhound Timing (Marietta GA) | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Greyhound Timing (Ocean Springs MS) | MS | milesplit | posts only to MileSplit (FloSports feed) |
| Greystone Racing | CT | milesplit | posts only to MileSplit (FloSports feed) |
| Grounded Running Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Groves Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Gulf Sport Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Guy Williams | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Hardin Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Harding Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| HawfieldsXC/TF | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Heang Uy | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Herd Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Heroes Stadium | TX | milesplit | posts only to MileSplit (FloSports feed) |
| HGP Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| HHI Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Hiestand Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Hilltopper Results Service | IA | unknown | its results page is a feed's (MileSplit etc.) |
| Hilton Head Running Company | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Hocorunning Timing and Data Serv | MD | milesplit | posts only to MileSplit (FloSports feed) |
| HogRacing Timing | TX | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Holyoke Community College | MA | milesplit | posts only to MileSplit (FloSports feed) |
| Hornet Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| House Hawks Timing | CT | milesplit | posts only to MileSplit (FloSports feed) |
| HR Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| HUABI Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Hudson Race Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Hugh Mills Stadium Timing Company | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Husky Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| IE ElecTronic Timing | CA | unknown | its results page is a feed's (MileSplit etc.) |
| iMV2 Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| In Dew Time | KY | milesplit | posts only to MileSplit (FloSports feed) |
| In Focus Timing by Movin' Shoes | WI | custom | its results page is a feed's (MileSplit etc.) |
| International Running Company | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| It's About Time | SC | milesplit | posts only to MileSplit (FloSports feed) |
| iTex | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Its Go Time | SC | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| J3Timing | IL | unknown | its results page is a feed's (MileSplit etc.) |
| Jack Rabbit Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| JAM Marketing & Consult., Inc | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Jason Fulbright | NC | milesplit | posts only to MileSplit (FloSports feed) |
| JB Sports Timing | NJ | custom | its results page is a feed's (MileSplit etc.) |
| Jensen Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Jerry Fuqua Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| JHTiming | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Jim Aylsworth | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Jim Thorpe Track & Field Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Jira Timing Texas | TX | custom | its results page is a feed's (MileSplit etc.) |
| Joe Cornett | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Joe Curcio | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Joe Sinicrope Timing Service | CT | milesplit | posts only to MileSplit (FloSports feed) |
| John Buchholtz | VA | milesplit | posts only to MileSplit (FloSports feed) |
| John Schwarz (Essex Co Coaches) | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Johnny Duncan | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Joseph Adams Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| JPALLEN-ENTERPRISES | NY | milesplit | posts only to MileSplit (FloSports feed) |
| JT Timing Plus | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Judson ISD Athletics | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Jungle Rock | PA | milesplit | posts only to MileSplit (FloSports feed) |
| K&G Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Kane Wolves Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Kennett Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Kevin Stoots | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Keystone Timing Service | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Kinetic Timing | IL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Kingdom CDC | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Kingston City Schools | NY | milesplit | posts only to MileSplit (FloSports feed) |
| KMathis Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Knightdale Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Kordsmeier Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Krueger Services | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Lace 'em Up Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Lake Forest High School | DE | milesplit | posts only to MileSplit (FloSports feed) |
| Lakeland Prep Track and Field | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Lakers Track Club & Timing | ME | milesplit | posts only to MileSplit (FloSports feed) |
| Lamar Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Lampert Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Landrum High School | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Lawrence Timing Systems | ME | milesplit | posts only to MileSplit (FloSports feed) |
| LB Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Lee County Middle Schools | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Lee Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Leslie Smith Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Level 2 Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| LG Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Liberty Creek High School | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Lickety Split Timing | UT | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Line of Sight Events | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Lion Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Little Elm ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Love Timing Company | VA | milesplit | posts only to MileSplit (FloSports feed) |
| LS Athletic Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| LS Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Lubbock ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Lubbock Run Project | TX | milesplit | posts only to MileSplit (FloSports feed) |
| m3 Solutions | VA | milesplit | posts only to MileSplit (FloSports feed) |
| M3 Solutions Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Mac Vault | TX | milesplit | posts only to MileSplit (FloSports feed) |
| MACH2K | PA | milesplit | posts only to MileSplit (FloSports feed) |
| MAD Racing Colorado | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Mad Sprocket Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Maggie Walker Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Mansfield ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Mansoor Timing | CA,NV | milesplit | posts only to MileSplit (FloSports feed) |
| Mapes Finish Line Service | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Martin Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Mary Wimmer Timing | AZ | milesplit | posts only to MileSplit (FloSports feed) |
| Matthew Coble | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Max Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Mcallen ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| McCallie School | TN | milesplit | posts only to MileSplit (FloSports feed) |
| MCCATS | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| McGill Toolen | AL | milesplit | posts only to MileSplit (FloSports feed) |
| McIvor Timing LLC | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Mckim Athletic Management | MD | milesplit | posts only to MileSplit (FloSports feed) |
| MCM Timing and Results LLC | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Memphis Youth Athletics | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Merrill Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Mesquite ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Mettle Events, Inc. | VA | custom | its results page is a feed's (MileSplit etc.) |
| MG Track Timing | MO | unknown | its results page is a feed's (MileSplit etc.) |
| Mid Area Track Club | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Milbank Timing | SD | unknown | its results page is a feed's (MileSplit etc.) |
| Miles and Minutes LLC | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Miles of Smiles Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Milliseconds Sports Timing | UT | unknown | its results page is a feed's (MileSplit etc.) |
| Mills Godwin Timing Crew | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Milton Area FAT Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Minersville Area | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Minute Man Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Minuteman Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| MMT | KY | milesplit | posts only to MileSplit (FloSports feed) |
| MOblas | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Model HS | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Moncer Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Morgan Sports | KS | milesplit | posts only to MileSplit (FloSports feed) |
| Morning Star Timing | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Morristown West High Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Mountain Goat Racing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| MS Race Timing & Management | MS | milesplit | posts only to MileSplit (FloSports feed) |
| Natrona County HS Timing | WY | unknown | its results page is a feed's (MileSplit etc.) |
| Negative Split Productions | TX | milesplit | posts only to MileSplit (FloSports feed) |
| NEISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Nelson Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Neptune High School | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Ness City Timing | KS | milesplit | posts only to MileSplit (FloSports feed) |
| New and OldTimers | NC | milesplit | posts only to MileSplit (FloSports feed) |
| New England Timing | NH | custom | its results page is a feed's (MileSplit etc.) |
| New Hope Athletics | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Nittany Valley Running Club | PA | milesplit | posts only to MileSplit (FloSports feed) |
| NJ Races | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| North Stokes HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Northeast Timing | IA | unknown | its results page is a feed's (MileSplit etc.) |
| Northern Knights Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Nova Race Timing LLC | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Ocean Breeze Track And Field | NY | milesplit | posts only to MileSplit (FloSports feed) |
| OK Timing Plus | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Old Timers Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Olige timing company | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Olympia Resources | GA | milesplit | posts only to MileSplit (FloSports feed) |
| On The Mark Sports | NC | milesplit | posts only to MileSplit (FloSports feed) |
| On The Right Track Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| On Time Timing, LLC | OK | milesplit | posts only to MileSplit (FloSports feed) |
| Oregon Timer | OR | milesplit | posts only to MileSplit (FloSports feed) |
| Osceola County | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Oxner Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Pace360 | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Pacers Running | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Pacific Sport Events & Timing (Finish Line Production) | HI | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Palmerton High School Timing Company | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Palmetto Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Panthers Track and Field | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Paris Chisum HS | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Peddie - FAT | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Pelican Timing Services | LA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Pelion High School | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Penn State Behrend T&F | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Perfect Timing - SC | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Performance Race Services | GA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Personal Record Race Timing | IL | unknown | its results page is a feed's (MileSplit etc.) |
| Pine Bluffs Timing | WY | unknown | its results page is a feed's (MileSplit etc.) |
| Pine Tree Race Services | ME | milesplit | posts only to MileSplit (FloSports feed) |
| Pinkerton Timing | NH | milesplit | posts only to MileSplit (FloSports feed) |
| Pioneer Running | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Pittsburgh Public Schools | PA | milesplit | posts only to MileSplit (FloSports feed) |
| PJB Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Play Hard Event Timing | SC | custom | its results page is a feed's (MileSplit etc.) |
| Pocono Mountain East Track | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Polk County | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Porter Gaud School | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Positive Vibes - Run Positive - Gina Dillon | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Post ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Precision One Timing | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Precision Race | NC | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Premium Race Timing & Management | NC | milesplit | posts only to MileSplit (FloSports feed) |
| PRS Race Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Q-MTA | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Queen City Timing | NC | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Quintanar Running Co. | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Quitman Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Race Chip Timing | TX | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Race Day Event Services | TX | custom | its results page is a feed's (MileSplit etc.) |
| Race Management Systems | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Race Northwest | OR,WA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Race Pace Multisport, LLC | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Race Timing Unlimited | VA | custom | its results page is a feed's (MileSplit etc.) |
| Race Wizard | AR | custom | its results page is a feed's (MileSplit etc.) |
| RaceDay Timing Solutions | MO | unknown | its results page is a feed's (MileSplit etc.) |
| RacePacket, Inc | VA | milesplit | posts only to MileSplit (FloSports feed) |
| RaceSmith, Inc. | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Racesonline | MS | milesplit | posts only to MileSplit (FloSports feed) |
| RaceTime | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Racing NJ | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Ragged Mountain Running | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Raleigh Parks | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Raymondville ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| RCC Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Record Finish | UT | unknown | its results page is a feed's (MileSplit etc.) |
| Records Racing Co. | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Red Cap Timing | CA | milesplit | posts only to MileSplit (FloSports feed) |
| Red Devil Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Red Hill Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Red Pointer Productions | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Red Stix Timing | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Revolution Running | GA | milesplit | posts only to MileSplit (FloSports feed) |
| REZ Time | AZ | unknown | its results page is a feed's (MileSplit etc.) |
| RGVCCCA | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Rhythm Run Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Rice Consolidated | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Rick Kates | MA | milesplit | posts only to MileSplit (FloSports feed) |
| Rick Spencer/Weddington HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| RidgePoint Timing | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Right Time Race Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Right Time Results | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Riverdale Track Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Rockport-Fulton Pirates Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Rockwall Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Rocky Mountain Timing | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Rod Ray Timing | AR | milesplit | posts only to MileSplit (FloSports feed) |
| Ron Erion (timer) | SD | unknown | its results page is a feed's (MileSplit etc.) |
| Roundhouse Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Royse City ISD Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Run Black Hills | SD | unknown | its results page is a feed's (MileSplit etc.) |
| Run Corps | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Run Houston Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Run in a Race | ME | milesplit | posts only to MileSplit (FloSports feed) |
| Run Manatee | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Run Roanoke | VA | custom | its results page is a feed's (MileSplit etc.) |
| Run Wild Race Management | LA | milesplit | posts only to MileSplit (FloSports feed) |
| runBUCKS.com | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Runlimited | CO | unknown | its results page is a feed's (MileSplit etc.) |
| Runner's Depot | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Runner's High Timing and Race Management | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Runners Fit Race Works | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Runners Roost Race Services | CO | milesplit | posts only to MileSplit (FloSports feed) |
| Running Wild | FL | milesplit | posts only to MileSplit (FloSports feed) |
| RunningMaryland.com | MD | milesplit | posts only to MileSplit (FloSports feed) |
| RunningZone, Inc. | FL | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| RunRideRace | VA | milesplit | posts only to MileSplit (FloSports feed) |
| RWB RamNation Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| S2 Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Salt Lake Running Company (race services) | UT | unknown | its results page is a feed's (MileSplit etc.) |
| Sanderson HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| SCCPSS Athletics Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Scenic City Multisport | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Scenic Route Sports Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Schools and Tavani Timing System | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Schuylkill Haven Area High School | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Scott Chellis | NC | milesplit | posts only to MileSplit (FloSports feed) |
| SCPS Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| SDR Timing Services | MD | milesplit | posts only to MileSplit (FloSports feed) |
| Sean Fitzgerald Timing | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Sean Leyh | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Second Wind Race Timing (Coatesville PA) | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Second Wind Race Timing (Jacksonville FL) | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Section X Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Servant Spirit Timing Services | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Set Up Events | SC | milesplit | posts only to MileSplit (FloSports feed) |
| SGCHS timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Shannon Event Timing | IA | unknown | its results page is a feed's (MileSplit etc.) |
| Shawn Loggins | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Shenandoah Valley Runners | VA | milesplit | posts only to MileSplit (FloSports feed) |
| ShikTFCC | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Shore Track Coaches | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Silliman Institute | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Silver Streak Timing Company | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Simply Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Sisu Race Timing | MS | milesplit | posts only to MileSplit (FloSports feed) |
| SMART Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| SMG Timing LLC | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Smitty's Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| SoCalTiming | CA | milesplit | posts only to MileSplit (FloSports feed) |
| Sole Roots Running Co. | GA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| South Plains Area Track Official | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Southern Alamance | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Southern Lehigh Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Southern Sports Group | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Southern Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| SouthPanola | MS | milesplit | posts only to MileSplit (FloSports feed) |
| SpectaSport LLC | NJ,PA | custom | its results page is a feed's (MileSplit etc.) |
| Speed Sport Timing | MA,NH | milesplit | posts only to MileSplit (FloSports feed) |
| Spirit Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Spitler Timing Service | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Split Second Racing | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Split Second Timing | FL | custom | its results page is a feed's (MileSplit etc.) |
| Split Second Timing SC | SC | milesplit | posts only to MileSplit (FloSports feed) |
| SplitFire Timing | OH | milesplit | posts only to MileSplit (FloSports feed) |
| Sport Alaska | AK | unknown | its results page is a feed's (MileSplit etc.) |
| StarSpeed Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Start To Finish Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Statsman910 | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Stearns Race Timing | AR | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Sterling City ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Steven Matthews | AR | milesplit | posts only to MileSplit (FloSports feed) |
| STF Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Stinger Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Strictly Running | SC | milesplit | posts only to MileSplit (FloSports feed) |
| StridersU Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Styles Athletic Performance | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Sudan ISD | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Suffield High School | CT | milesplit | posts only to MileSplit (FloSports feed) |
| SUMTER TIMING | SC | milesplit | posts only to MileSplit (FloSports feed) |
| Sun Valley HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| SVE Timing | SC | custom | its results page is a feed's (MileSplit etc.) |
| Swansboro Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| SYS Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| T3 Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Tailwind Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| TCNJ Timing | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| TeeplesTrackTiming | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Tennessee Endurance | TN | milesplit | posts only to MileSplit (FloSports feed) |
| TennesseeRunner Event Management | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Terry Times | SC | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| TFCAofGP Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| The Hill School | PA | milesplit | posts only to MileSplit (FloSports feed) |
| The Last Mile Racing LLC | CT | milesplit | posts only to MileSplit (FloSports feed) |
| The Lawrenceville School - FAT | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| The Timing Team | PA | milesplit | posts only to MileSplit (FloSports feed) |
| The Tracker | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Thrall High School | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Threshold Timing | LA | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Throwers Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Tidewater Striders | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Tidewater Timing Company |  | milesplit | posts only to MileSplit (FloSports feed) |
| Time 2 Run | MS | custom | its results page is a feed's (MileSplit etc.) |
| Timeless: Powered by ELHS | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Timer Guys LLC | KS | milesplit | posts only to MileSplit (FloSports feed) |
| TimeSpann Meet Management | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Timing by Josh Coon | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Timing Pros | TX | milesplit | posts only to MileSplit (FloSports feed) |
| To The Finish Timing | IL,IN,KY | unknown | its results page is a feed's (MileSplit etc.) |
| Tom Cook Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Tom Stewart Timing | CT | milesplit | posts only to MileSplit (FloSports feed) |
| Tornado Track Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Torrance Lee | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Total Race Solutions | TN | custom | its results page is a feed's (MileSplit etc.) |
| Track & Field Meet Productions | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Track For Life | TX | custom | its results page is a feed's (MileSplit etc.) |
| Track Over Everything | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Trackrabbit Timing & Entries | CA | unknown | its results page is a feed's (MileSplit etc.) |
| TRC Race Management | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Tri-Cities Road Runners | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Triad Timing Systems | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Triangle Champions | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Trojan Network Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Trust The Process Timing | MS | milesplit | posts only to MileSplit (FloSports feed) |
| TSTCA | PA | milesplit | posts only to MileSplit (FloSports feed) |
| TTR Timing Services | TX | milesplit | posts only to MileSplit (FloSports feed) |
| TXC Timing | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Underdog Race Timing | NY | custom | its results page is a feed's (MileSplit etc.) |
| Upson Lee Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Upstate Race Series | SC | milesplit | posts only to MileSplit (FloSports feed) |
| USATF-NE Timing Services | MA | milesplit | posts only to MileSplit (FloSports feed) |
| V Fit Productions | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Vac & Dash Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Van Deman Timing | SC | milesplit | posts only to MileSplit (FloSports feed) |
| VCB Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Velocity Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Vernon Parish School Board | LA | milesplit | posts only to MileSplit (FloSports feed) |
| Victory Racing Company | FL | milesplit | posts only to MileSplit (FloSports feed) |
| Viking Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Vineyard Timing | PA | milesplit | posts only to MileSplit (FloSports feed) |
| VP Timing | SD | unknown | its results page is a feed's (MileSplit etc.) |
| Wake Forest HS Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Wallkill Valley Timing | NJ | milesplit | posts only to MileSplit (FloSports feed) |
| Walter Williams HS | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Walton Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| Warren County Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| Warrior Timing | FL | milesplit | posts only to MileSplit (FloSports feed) |
| WDHS Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| We Run Events | TN | road | road races only (RunSignup/ChronoTrack/RaceResult) |
| Weaverville Elite Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Wellsville Track & Field Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| West Forsyth Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| West Perry High School | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Wheatland High School | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Wheeler | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Wildcat Timing | AL | milesplit | posts only to MileSplit (FloSports feed) |
| Wildcat Timing (MS) | MS | milesplit | posts only to MileSplit (FloSports feed) |
| Wilson Memorial Timing | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Winona Nordic Timing | MN | unknown | its results page is a feed's (MileSplit etc.) |
| Winston Brown | VA | milesplit | posts only to MileSplit (FloSports feed) |
| Wisconsin Runner Timing Systems | WI | unknown | its results page is a feed's (MileSplit etc.) |
| Wise Timing | KY | milesplit | posts only to MileSplit (FloSports feed) |
| WOL Triangle | NC | milesplit | posts only to MileSplit (FloSports feed) |
| Wolf Creek Race Management | PA | milesplit | posts only to MileSplit (FloSports feed) |
| Wolverine Timing | NY | milesplit | posts only to MileSplit (FloSports feed) |
| Woodson Timing Wizards | VA | milesplit | posts only to MileSplit (FloSports feed) |
| WTB Timing | CO | unknown | its results page is a feed's (MileSplit etc.) |
| WTCCL | TN | milesplit | posts only to MileSplit (FloSports feed) |
| Wyoming Timing | WY | unknown | its results page is a feed's (MileSplit etc.) |
| XC Tech | IN | unknown | its results page is a feed's (MileSplit etc.) |
| Xfactor Timing | GA | milesplit | posts only to MileSplit (FloSports feed) |
| YellowJacket Racing | NY | custom | its results page is a feed's (MileSplit etc.) |
| Zapata Roadrunners | TX | milesplit | posts only to MileSplit (FloSports feed) |
| Zoo City Timing | NC | custom | its results page is a feed's (MileSplit etc.) |
| ZR Prime Timing | NC | milesplit | posts only to MileSplit (FloSports feed) |

