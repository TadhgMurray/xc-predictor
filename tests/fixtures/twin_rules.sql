DROP TABLE IF EXISTS results, results_tf, meets, meets_tf, result_twin;
CREATE TABLE results (result_id bigint, person_id bigint, source text, meet_id bigint, div_id bigint,
  canon_meet_id bigint, place int, time_seconds double precision, date text);
CREATE TABLE results_tf (result_id bigint, person_id bigint, source text, meet_id bigint, div_id bigint,
  event_id bigint, canon_meet_id bigint, place int, time_seconds double precision, date text);
CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text);
CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint, source text, meet_name text, state text);
CREATE TABLE result_twin (sport text NOT NULL, result_id bigint NOT NULL, reason text NOT NULL, PRIMARY KEY (sport, result_id));

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
