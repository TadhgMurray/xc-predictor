import sqlite3

conn = sqlite3.connect("data/xc.db")
cursor = conn.cursor()

cursor.execute("SELECT COUNT(DISTINCT meet_id) FROM meet_queue")
print("Distinct meet IDs:", cursor.fetchone()[0])

cursor.execute("SELECT COUNT(*) FROM meet_queue")
print("Total rows:", cursor.fetchone()[0])

conn.close()