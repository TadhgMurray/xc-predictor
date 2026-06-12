import sys
sys.path.insert(0, 'scripts')
from database import initPool, getConn
initPool()
with getConn() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT r.grade, a.gender, COUNT(*), 
               AVG(r.normalized_time), MIN(r.normalized_time), MAX(r.normalized_time)
        FROM results r
        JOIN meets m ON r.div_id = m.div_id
        JOIN athletes a ON r.athlete_id = a.athlete_id
        WHERE m.course_name = 'IDEA Frontier'
        AND r.normalized_time > 600
        AND r.normalized_time < 3600
        GROUP BY r.grade, a.gender
        ORDER BY count DESC
    """)
    for row in c.fetchall():
        print(row)
    
    print()
    c.execute("""
        SELECT difficulty, n_results, n_athletes
        FROM course_difficulties
        WHERE course_name = 'IDEA Frontier'
    """)
    print(c.fetchone())