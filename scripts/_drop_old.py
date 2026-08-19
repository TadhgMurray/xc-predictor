import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool
initPool()
cm = getConn(); conn = cm.__enter__(); cur = conn.cursor()
for t in ("results_old", "results_tf_old"):
    cur.execute(f"DROP TABLE IF EXISTS {t}")
conn.commit()
print("[drop] cleared")
