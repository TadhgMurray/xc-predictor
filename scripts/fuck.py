import sys
sys.path.insert(0, "scripts")
from database import initPool, closePool, getConn
 
# _formatTime
# Purpose: Converts seconds float to MM:SS.xx string.
def _formatTime(seconds) -> str:
    if seconds is None:
        return "N/A"
    mins = int(seconds) // 60
    secs = seconds % 60
    return f"{mins}:{secs:05.2f}"
 
# _checkPoolStats
# Purpose: Prints count and avg speed rating per pool from athlete_ratings.
#          Tells us if the engine rated all pools and ratings are centered ~100.
def _checkPoolStats(cursor):
 
    print("\n--- Pool Stats (should be centered near 100) ---")
    print(f"{'Pool':<25} {'Athletes':>10} {'Avg Rating':>12} {'Min':>8} {'Max':>8}")
    print("-" * 66)
 
    cursor.execute("""
        SELECT
            pool,
            COUNT(*) as n,
            ROUND(AVG(speed_rating)::numeric, 2) as avg_rating,
            ROUND(MIN(speed_rating)::numeric, 2) as min_rating,
            ROUND(MAX(speed_rating)::numeric, 2) as max_rating
        FROM athlete_ratings
        GROUP BY pool
        ORDER BY pool
    """)
 
    for pool, n, avg, mn, mx in cursor.fetchall():
        print(f"{pool:<25} {n:>10,} {avg:>12} {mn:>8} {mx:>8}")
 
# _checkTopAthletes
# Purpose: Prints top 10 athletes per pool. Names should be recognizable
#          elite runners if the engine is working correctly.
def _checkTopAthletes(cursor, pool: str):
 
    print(f"\n--- Top 10 {pool} ---")
    print(f"{'#':<4} {'Name':<25} {'School':<30} {'Rating':>8} {'Races':>6}")
    print("-" * 76)
 
    cursor.execute("""
        SELECT
            a.first_name,
            a.last_name,
            a.school,
            ar.speed_rating,
            ar.n_races
        FROM athlete_ratings ar
        JOIN athletes a ON ar.athlete_id = a.athlete_id
        WHERE ar.pool = %s
        ORDER BY ar.speed_rating DESC
        LIMIT 10
    """, (pool,))
 
    for i, (first, last, school, rating, n_races) in enumerate(cursor.fetchall(), 1):
        name   = f"{first} {last}"[:24]
        school = (school or "")[:29]
        print(f"{i:<4} {name:<25} {school:<30} {rating:>8.2f} {n_races:>6}")
 
# _checkTopResults
# Purpose: Prints top 10 individual results by speed rating.
#          Times should be fast but realistic (no 12-second 5Ks).
def _checkTopResults(cursor):
 
    print("\n--- Top 10 Results All-Time ---")
    print(f"{'#':<4} {'Name':<25} {'School':<20} {'Course':<25} {'Time':>8} {'Rating':>8}")
    print("-" * 98)
 
    cursor.execute("""
        SELECT
            a.first_name,
            a.last_name,
            a.school,
            m.course_name,
            r.time_seconds,
            r.speed_rating
        FROM results r
        JOIN athletes a ON r.athlete_id = a.athlete_id
        JOIN meets    m ON r.div_id     = m.div_id
        WHERE r.speed_rating IS NOT NULL
        AND r.time_seconds BETWEEN 700 AND 2400
        ORDER BY r.speed_rating DESC
        LIMIT 10
    """)
 
    for i, (first, last, school, course, time_s, rating) in enumerate(cursor.fetchall(), 1):
        name   = f"{first} {last}"[:24]
        school = (school or "")[:19]
        course = (course or "")[:24]
        print(f"{i:<4} {name:<25} {school:<20} {course:<25} {_formatTime(time_s):>8} {rating:>8.2f}")
 
# _checkCourseSanity
# Purpose: Prints well-known courses so we can verify their difficulties
#          make intuitive sense. Detweiller should be easy (~-0.05),
#          Van Cortlandt should be hard (~0.10+).
def _checkCourseSanity(cursor):
 
    known_courses = [
        'Detweiller Park',
        'Van Cortlandt Park',
        'Lavern Gibson',
        'Sunken Meadow',
        'Hole in the Wall',
    ]
 
    print("\n--- Known Course Difficulties ---")
    print(f"{'Course':<30} {'Difficulty':>12} {'N Results':>10} {'N Athletes':>12}")
    print("-" * 67)
 
    for course in known_courses:
        cursor.execute("""
            SELECT difficulty, n_results, n_athletes
            FROM course_difficulties
            WHERE course_name = %s
        """, (course,))
        row = cursor.fetchone()
        if row:
            diff, n_res, n_ath = row
            print(f"{course:<30} {diff:>12.4f} {n_res:>10,} {n_ath:>12,}")
        else:
            print(f"{course:<30} {'NOT FOUND':>12}")
 
# _checkCoverage
# Purpose: How many results got a speed rating vs total normalized results.
def _checkCoverage(cursor):
 
    cursor.execute("SELECT COUNT(*) FROM results WHERE speed_rating IS NOT NULL")
    rated = cursor.fetchone()[0]
 
    cursor.execute("SELECT COUNT(*) FROM results WHERE normalized_time IS NOT NULL")
    normalized = cursor.fetchone()[0]
 
    pct = (rated / normalized * 100) if normalized > 0 else 0
    print(f"\n--- Coverage ---")
    print(f"Results with speed_rating:  {rated:>12,}")
    print(f"Results with normalized:    {normalized:>12,}")
    print(f"Coverage:                   {pct:>11.1f}%")
 
def main():
 
    initPool()
 
    try:
        with getConn() as conn:
            cursor = conn.cursor()
            _checkCoverage(cursor)
            _checkPoolStats(cursor)
            _checkTopResults(cursor)
            _checkCourseSanity(cursor)
            for pool in ["hs_m", "hs_f", "college_m", "college_f"]:
                _checkTopAthletes(cursor, pool)
            print()
 
    finally:
        closePool()
 
if __name__ == "__main__":
    main()