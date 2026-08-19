# p6.ps1 -- adjudicate p5 regressions -> merge -> apply -> backfill -> engine -> diag -> verify
$ErrorActionPreference = "Continue"

# drop helper (leftover _old blocks every merge)
@'
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool
initPool()
cm = getConn(); conn = cm.__enter__(); cur = conn.cursor()
for t in ("results_old", "results_tf_old"):
    cur.execute(f"DROP TABLE IF EXISTS {t}")
conn.commit()
print("[drop] leftovers cleared")
'@ | Out-File -Encoding ascii scripts\_drop_old.py

function Step($name, $cmd) {
    Write-Host "`n=== $name : $(Get-Date -Format 'HH:mm:ss') ===" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -gt 1) {
        Write-Host "!! $name exited $LASTEXITCODE -- STOPPING" -ForegroundColor Red
        exit 1
    }
}

Step "adjudicate" "python scripts\adjudicate_regressions.py --sport XC --log triage_p5.log 2>&1 | Tee-Object adjudicate_p6.log"
Step "merge"      "python scripts\do_merge.py 2>&1 | Tee-Object merge_p6.log"
Step "apply"      "python scripts\apply_triage.py 2>&1 | Tee-Object apply_p6.log"
Step "drop"       "python scripts\_drop_old.py"
Step "backfill"   "python backfill\backfill_normalize.py --sport XC --apply 2>&1 | Tee-Object bf_p6.log"
Step "drop"       "python scripts\_drop_old.py"
Step "engine"     "python engine\speed_ratings.py --sport XC 2>&1 | Tee-Object eng_p6.log"
Step "drop"       "python scripts\_drop_old.py"
Step "diag"       "python scripts\diag_suspects.py --sport XC --row-fast 0.06 --row-slow 0.25 --row-limit 3000000 2>&1 | Tee-Object diag_p6.log"
Step "verify"     "python scripts\verify_redetect.py 2>&1 | Tee-Object verify_p6.log"
Step "drop"       "python scripts\_drop_old.py"

Write-Host "`nDONE -- read verify_p6.log" -ForegroundColor Green