DROP TABLE IF EXISTS results, results_tf, meets, meets_tf, result_twin, college_directory;
-- grade / team_id / team_slug / school LAST, so every positional INSERT below
-- that stops at date leaves them NULL (level_conflict reads them)
CREATE TABLE results (result_id bigint, person_id bigint, source text, meet_id bigint, div_id bigint,
  canon_meet_id bigint, place int, time_seconds double precision, date text,
  grade text, team_id int, team_slug text, school text);
CREATE TABLE results_tf (result_id bigint, person_id bigint, source text, meet_id bigint, div_id bigint,
  event_id bigint, canon_meet_id bigint, place int, time_seconds double precision, date text,
  grade text, team_id int, team_slug text,
  event_short text, is_relay int, is_field int, school text);
CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text);
CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint, source text, meet_name text, state text);
CREATE TABLE result_twin (sport text NOT NULL, result_id bigint NOT NULL, reason text NOT NULL, PRIMARY KEY (sport, result_id));
-- build_college_directory's table: level_conflict's known colleges
CREATE TABLE college_directory (name_norm text, name text, state text, division text, source text);
INSERT INTO college_directory VALUES ('middlebury', 'Middlebury College', 'VT', 'D3', 'wikipedia'),
  ('williams', 'Williams College', 'MA', 'D3', 'wikipedia'),
  ('hamilton', 'Hamilton College', 'NY', 'D3', 'wikipedia');

-- XC ------------------------------------------------------------
-- meet 1 (div 11): 8 runners; meet 2 (div 21): the SAME race listed again 10 days later (race copy, later loses)
INSERT INTO meets VALUES (1,11,'Big Invite'),(2,21,'Big Invite (copy)'),(3,31,'State Meet'),(4,41,'State Meet'),(5,51,'Dual'),(6,61,'Dual');
INSERT INTO results SELECT 100+g, 1000+g, 'anet', 1, 11, 1, g, 900+g*3.14, '2025-10-01' FROM generate_series(1,8) g;
INSERT INTO results SELECT 200+g, 1000+g, 'anet', 2, 21, 2, g, 900+g*3.14, '2025-10-11' FROM generate_series(1,8) g;
-- cross-date: 'State Meet' under meet 3 (big, 6 rows) and meet 4 (small: 2 rows), same person/place/time 5 days apart -> small copy loses
INSERT INTO results SELECT 300+g, 2000+g, 'anet', 3, 31, 3, g, 1000+g, '2025-11-01' FROM generate_series(1,6) g;
INSERT INTO results VALUES (401, 2001, 'anet', 4, 41, 4, 1, 1001, '2025-11-06'), (402, 2002, 'anet', 4, 41, 4, 2, 1002, '2025-11-06');
-- same feed exact duplicate inside meet 5: 501 keeps, 502 goes
INSERT INTO results VALUES (501, 3001, 'anet', 5, 51, 5, 1, 950.0, '2025-09-01'), (502, 3001, 'anet', 5, 51, 5, 1, 950.04, '2025-09-01');
-- prelim/final shape in meet 6: same people, different times -> nothing
INSERT INTO results SELECT 600+g, 4000+g, 'anet', 6, 61, 6, g, 800+g, '2025-09-10' FROM generate_series(1,6) g;
INSERT INTO results SELECT 650+g, 4000+g, 'anet', 6, 62, 6, g, 790+g, '2025-09-10' FROM generate_series(1,6) g;
-- cross-feed twins at canon meet 1: tfrrs rows with same place+time (twin_race) and one by person (twin_person)
INSERT INTO results VALUES (701, NULL, 'tfrrs', 71, 711, 1, 1, 903.14, '2025-10-01'), (702, 1002, 'tfrrs', 71, 711, 1, 99, 1234.0, '2025-10-01');
-- one race under two names, no canon link (the UVU pair, 2026-10-06): the
-- tfrrs copy at whole seconds goes; a same-day tfrrs row 14 s off stays
INSERT INTO results VALUES (7201, 5101, 'anet', 73, 731, NULL, 1, 801.4, '2026-09-04'),
  (721, 5101, 'tfrrs', 72, 721, NULL, 1, 801.0, '2026-09-04'),
  (7202, 5102, 'anet', 74, 741, NULL, 3, 815.0, '2026-09-05'),
  (722, 5102, 'tfrrs', 75, 751, NULL, 3, 801.0, '2026-09-05');
-- a lone row that pairs with nothing
INSERT INTO results VALUES (801, 5001, 'anet', 8, 81, 8, 1, 999.9, '2025-10-20');

-- TF ------------------------------------------------------------
INSERT INTO meets_tf VALUES (11,111,1,'anet','Spring Relays','CA'),(12,121,1,'anet','Spring Relays','CA'),(13,131,1,'anet','Twilight','CA'),(13,131,2,'anet','Twilight','CA'),(14,141,2,'anet','Twilight','CA');
-- race copy: meet 11 event 1 (10 rows) copied as meet 12 event 1 same date but only 8 rows -> the smaller (meet 12) loses
INSERT INTO results_tf SELECT 1000+g, 7000+g, 'anet', 11, 111, 1, 11, g, 120+g*0.5, '2026-04-04' FROM generate_series(1,10) g;
INSERT INTO results_tf SELECT 1100+g, 7000+g, 'anet', 12, 121, 1, 12, g, 120+g*0.5, '2026-04-04' FROM generate_series(1,8) g;
-- cross-date under 'Twilight': meet 13 event 2 (5 rows) vs meet 14 event 2 (3 rows), 3 days apart -> the smaller loses
INSERT INTO results_tf SELECT 1300+g, 8000+g, 'anet', 13, 131, 2, 13, g, 240+g, '2026-05-01' FROM generate_series(1,5) g;
INSERT INTO results_tf SELECT 1400+g, 8000+g, 'anet', 14, 141, 2, 14, g, 240+g, '2026-05-04' FROM generate_series(1,3) g;
-- same feed dup with different event ids is NOT a dup on track
INSERT INTO results_tf VALUES (1501, 9001, 'anet', 13, 131, 1, 13, 1, 60.0, '2026-05-01'), (1502, 9001, 'anet', 13, 131, 2, 13, 1, 60.0, '2026-05-01');
-- and the same event id IS
INSERT INTO results_tf VALUES (1601, 9002, 'anet', 13, 131, 1, 13, 2, 61.0, '2026-05-01'), (1602, 9002, 'anet', 13, 131, 1, 13, 2, 61.0, '2026-05-01');
-- rows with a bad date must not crash the date parse
INSERT INTO results_tf VALUES (1701, 9003, 'anet', 13, 131, 1, 13, 3, 62.0, 'TBA');

-- 2026-09-28 rules --------------------------------------------------
-- dup_same_day (XC): one run under two meet names on one day (Mariner 2021); meet 9 has 3 rows, meet 10 has 1
INSERT INTO meets VALUES (9,91,'38th P. Wilder Mariner XC Invitational'),(10,101,'38th Mariner-XC-Invitational');
INSERT INTO results VALUES (901, 6001, 'anet', 9, 91, 9, 23, 1135.1, '2021-10-16'),
                           (902, 6002, 'anet', 9, 91, 9, 24, 1136.0, '2021-10-16'),
                           (903, 6003, 'anet', 9, 91, 9, 25, 1137.0, '2021-10-16'),
                           (1001, 6001, 'anet', 10, 101, 10, 23, 1135.1, '2021-10-16');
-- xc_placeholder: a track 1600 on the XC calendar
INSERT INTO meets VALUES (11,111,'Mid-Season Mania 1600m Invitational (XC Calendar Placeholder)');
INSERT INTO results VALUES (1111, 6101, 'anet', 11, 111, 11, 5, 269.4, '2022-10-05');
-- dup_converted (TF): 2 miles 9:35.09 and its 3200m conversion 9:31.74, same meet and day;
-- and a real 1600 + 3200 double the same day, which must stay
INSERT INTO results_tf VALUES (1801, 9101, 'anet', 18, 181, 5, 18, 3, 575.09, '2025-12-13', NULL, NULL, NULL, '2miles', 0, 0),
                              (1802, 9101, 'anet', 18, 181, 6, 18, 3, 571.74, '2025-12-13', NULL, NULL, NULL, '3200m', 0, 0),
                              (1803, 9102, 'anet', 18, 181, 7, 18, 1, 255.36, '2025-12-13', NULL, NULL, NULL, '1600m', 0, 0),
                              (1804, 9102, 'anet', 18, 181, 6, 18, 2, 554.72, '2025-12-13', NULL, NULL, NULL, '3200m', 0, 0);
-- LEVEL CONFLICT (the NESCAC review, 2026-09-29) --------------------
-- meets 80-99 are in no meets table, so no cross-date pairing; no canon
-- meet, so no cross-feed twin; every (person, feed, time) once, so no copy.
-- A tfrrs row is college by its college SLUG, or -- slugless -- by the
-- FR-1 form AT A KNOWN COLLEGE (college_directory above); never by the form
-- alone (the server run, 2026-09-29: tfrrs-hosted high school meets).
-- 6001: the review's shape. Four college days (a college slug, or SO-2 at
--       Middlebury, which the directory knows), one Middlesex League row on
--       2025-10-26 -> the high school row. His own 2023 grade-11 race is
--       another season and stays.
INSERT INTO results VALUES
 (9001, 6001, 'tfrrs', 90, 901, NULL, 5, 1500.1, '2025-09-06', 'SO-2', NULL, 'VT_college_m_Middlebury', 'Middlebury'),
 (9002, 6001, 'tfrrs', 91, 911, NULL, 7, 1510.2, '2025-09-20', 'SO-2', NULL, NULL, 'Middlebury'),
 (9003, 6001, 'tfrrs', 92, 921, NULL, 9, 1490.3, '2025-10-18', 'SO',   NULL, 'VT_college_m_Middlebury', 'Middlebury'),
 (9004, 6001, 'tfrrs', 93, 931, NULL, 3, 1480.4, '2025-11-01', 'SO-2', NULL, 'VT_college_m_Middlebury', 'Middlebury'),
 (9005, 6001, 'anet',  94, 941, NULL, 12, 917.1, '2025-10-26', '12', 5501, NULL, 'Winchester'),
 (9006, 6001, 'anet',  95, 951, NULL, 4,  960.0, '2023-10-20', '11', 5502, NULL, 'Winchester');
-- 6002: the reverse -- a high schooler's season with one college row on it
INSERT INTO results VALUES
 (9011, 6002, 'anet',  94, 941, NULL, 20, 950.5, '2025-10-26', '12', 5503, NULL),
 (9012, 6002, 'anet',  96, 961, NULL, 2,  955.0, '2025-09-13', '12', 5503, NULL),
 (9013, 6002, 'anet',  97, 971, NULL, 1,  940.0, '2025-11-08', '12', 5503, NULL),
 (9014, 6002, 'tfrrs', 91, 911, NULL, 30, 1600.0, '2025-09-20', 'FR-1', NULL, 'ME_college_m_Bates');
-- 6003: one race each way, interleaved -- person 6339154's shape (a Kenston
--       HS race and an RPI race) -> a TIE, and a tie flags NOTHING (it is no
--       evidence which side is foreign; the first cut flagged both and cost
--       a real athlete his season). level_conflict reports it instead.
INSERT INTO results VALUES
 (9021, 6003, 'tfrrs', 90, 901, NULL, 40, 1650.0, '2025-09-06', 'JR-3', NULL, 'NY_college_m_RPI', 'RPI'),
 (9022, 6003, 'anet',  94, 941, NULL, 50, 1030.0, '2025-10-26', '10', 5504, NULL, 'Kenston');
-- 6004: one twelfth grader's race in both feeds, '12' and a bare 'SR' -> no conflict
INSERT INTO results VALUES
 (9031, 6004, 'anet',  98, 981, NULL, 1, 930.0, '2025-10-04', '12', 5505, NULL),
 (9032, 6004, 'tfrrs', 99, 991, NULL, 1, 930.0, '2025-10-04', 'SR', NULL, NULL);
-- 6005: a senior's autumn, then college the NEXT autumn -> two seasons, nothing
INSERT INTO results VALUES
 (9041, 6005, 'anet',  95, 952, NULL, 2,  945.0, '2024-10-19', '12', 5506, NULL),
 (9042, 6005, 'tfrrs', 92, 922, NULL, 11, 1495.0, '2025-10-18', 'FR-1', NULL, 'VT_college_m_Middlebury');
-- 6006: grade 12 on anet team 0 ("no team") is not a high school row
INSERT INTO results VALUES
 (9051, 6006, 'tfrrs', 90, 902, NULL, 12, 1505.0, '2025-09-06', 'SR-4', NULL, 'VT_college_m_Middlebury'),
 (9052, 6006, 'anet',  94, 942, NULL, 3,  1800.0, '2025-10-26', '12', 0, NULL);
-- 6007: persons 14178814/14178817's shape -- a high schooler's own races at a
--       tfrrs-hosted high school meet, printed 'SR-4', no slug, school
--       "Winnisquam" (no college of that name) -> not college, nothing. The
--       first cut read the form as college, tied, and flagged both.
INSERT INTO results VALUES
 (9061, 6007, 'tfrrs', 85, 851, NULL, 6, 1010.0, '2025-09-27', 'SR-4', NULL, NULL, 'Winnisquam'),
 (9062, 6007, 'anet',  86, 861, NULL, 4, 1000.0, '2025-10-11', '12', 5507, NULL, 'Winnisquam');
-- 6008: the directory's collision -- a high school with a college's name.
--       'Hamilton' is Hamilton College to the directory, but tfrrs put the
--       same string on a HIGH SCHOOL slug (9074), and that vetoes it: the
--       slugless SR-4 row 9073 is not college, and two anet days do not
--       outvote it.
INSERT INTO results VALUES
 (9071, 6008, 'anet',  87, 871, NULL, 2, 990.0,  '2025-09-13', '12', 5508, NULL, 'Hamilton'),
 (9072, 6008, 'anet',  88, 881, NULL, 3, 995.0,  '2025-10-11', '12', 5508, NULL, 'Hamilton'),
 (9073, 6008, 'tfrrs', 89, 891, NULL, 5, 1001.0, '2025-09-27', 'SR-4', NULL, NULL, 'Hamilton'),
 (9074, 6008, 'tfrrs', 80, 801, NULL, 7, 1002.0, '2025-10-04', 'SR-4', NULL, 'NY_hs_m_Hamilton', 'Hamilton');
-- 7101 (track): a December graduate racing for a college in January -- high
--       school, then college, never back -> a transition, nothing
INSERT INTO results_tf VALUES
 (9101, 7101, 'anet',  81, 811, 1, NULL, 1, 250.0, '2025-12-13', '12', 5601, NULL),
 (9102, 7101, 'anet',  82, 821, 1, NULL, 1, 251.0, '2025-12-20', '12', 5601, NULL),
 (9103, 7101, 'tfrrs', 83, 831, 1, NULL, 5, 248.0, '2026-01-17', 'FR-1', NULL, 'MA_college_m_Tufts'),
 (9104, 7101, 'tfrrs', 84, 841, 1, NULL, 4, 247.0, '2026-02-14', 'FR-1', NULL, 'MA_college_m_Tufts');
-- 7102 (track): a namesake's April inside a college spring -> the two high
--       school rows; the 'TBA' row has no season and is left alone. The
--       college rows carry no slug: they are college because Williams is a
--       known college (the directory path, school set below)
INSERT INTO results_tf VALUES
 (9111, 7102, 'tfrrs', 83, 831, 1, NULL, 9, 252.0, '2026-01-17', 'JR-3', NULL, NULL),
 (9112, 7102, 'tfrrs', 84, 841, 1, NULL, 8, 253.0, '2026-02-14', 'JR-3', NULL, NULL),
 (9113, 7102, 'tfrrs', 85, 851, 1, NULL, 7, 254.0, '2026-04-11', 'JR-3', NULL, NULL),
 (9114, 7102, 'tfrrs', 86, 861, 1, NULL, 6, 255.0, '2026-05-02', 'JR-3', NULL, NULL),
 (9115, 7102, 'anet',  87, 871, 1, NULL, 3, 280.0, '2026-04-18', '11', 5602, NULL),
 (9116, 7102, 'anet',  88, 881, 1, NULL, 2, 281.0, '2026-04-25', '11', 5602, NULL),
 (9117, 7102, 'anet',  88, 881, 2, NULL, 1, 11.0,  'TBA',        '11', 5602, NULL);
UPDATE results_tf SET school = 'Williams' WHERE result_id BETWEEN 9111 AND 9114;

-- twin_same_day on the track (2026-10-06): 1901 the tfrrs copy of an anet
-- 1500 (same person, day, time to the hundredth) goes; 1902 is a heat
-- 0.03 s off the anet final and stays; 1903 a relay leg, never
INSERT INTO results_tf (result_id, person_id, source, meet_id, div_id, event_id, canon_meet_id, place, time_seconds, date, is_relay, is_field) VALUES
  (19001, 9901, 'anet',  91, 911, 1, NULL, 2, 245.31, '2026-04-11', 0, 0),
  (1901,  9901, 'tfrrs', 92, 921, 1, NULL, 2, 245.31, '2026-04-11', 0, 0),
  (19002, 9902, 'anet',  91, 911, 2, NULL, 1, 11.02, '2026-04-11', 0, 0),
  (1902,  9902, 'tfrrs', 92, 921, 2, NULL, 1, 11.05, '2026-04-11', 0, 0),
  (19003, 9903, 'anet',  91, 911, 3, NULL, 1, 50.10, '2026-04-11', 1, 0),
  (1903,  9903, 'tfrrs', 92, 921, 3, NULL, 1, 50.10, '2026-04-11', 1, 0);

-- 2026-10-08: the TwiKnight pair -- one run, two meet entries a day apart,
-- 1st in both, 14:50.6 and 14:50.5; meet 120 is the bigger entry and keeps it
INSERT INTO results VALUES (1201, 7701, 'anet', 120, 1201, NULL, 1, 890.6, '2026-09-25'),
                           (1203, 7702, 'anet', 120, 1201, NULL, 2, 900.0, '2026-09-25'),
                           (1204, 7703, 'anet', 120, 1201, NULL, 3, 905.0, '2026-09-25'),
                           (1202, 7701, 'anet', 121, 1211, NULL, 1, 890.5, '2026-09-26');
