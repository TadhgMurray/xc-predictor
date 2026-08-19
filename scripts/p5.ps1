# p5.ps1 -- drop leftovers between every merge step, then run the chain
$ErrorActionPreference = "Stop"

$dropPy = @'
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool
initPool()
cm = getConn(); conn = cm.__enter__(); cur = conn.cursor()
for t in ("results_old", "results_tf_old"):
    cur.execute(f"DROP TABLE IF EXISTS {t}")
conn.commit()
print("[drop] leftovers cleared")
'@
$dropPy | Out-File -Encoding ascii scripts\_drop_old.py

function Drop { python scripts\_drop_old.py }

Drop
python engine\speed_ratings.py --sport XC 2>&1 | Tee-Object eng_p5.log
Drop
python scripts\diag_suspects.py --sport XC --row-fast 0.06 --row-slow 0.25 --row-limit 3000000 2>&1 | Tee-Object diag_p5.log
python scripts\verify_redetect.py 2>&1 | Tee-Object verify_p5.log
Drop

Write-Host "`nDONE -- read verify_p5.log" -ForegroundColor Green