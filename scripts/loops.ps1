# loop.ps1 -- run N correction cycles unattended, stop when it stops helping
$ErrorActionPreference = "Continue"
$MAX_CYCLES = 8
$prevReg = 999999

@'
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool
initPool()
cm = getConn(); conn = cm.__enter__(); cur = conn.cursor()
for t in ("results_old", "results_tf_old"):
    cur.execute(f"DROP TABLE IF EXISTS {t}")
conn.commit()
print("[drop] cleared")
'@ | Out-File -Encoding ascii scripts\_drop_old.py

function Step($name, $cmd) {
    Write-Host "`n=== $name : $(Get-Date -Format 'HH:mm:ss') ===" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -gt 1) {
        Write-Host "!! $name exited $LASTEXITCODE" -ForegroundColor Red
        return $false
    }
    return $true
}

for ($i = 1; $i -le $MAX_CYCLES; $i++) {
    Write-Host "`n########## CYCLE $i ##########" -ForegroundColor Yellow

    if (-not (Step "triage"     "python scripts\triage_regressions.py --sport XC --from-worksheet 2>&1 | Tee-Object triage_c$i.log")) { break }
    if (-not (Step "adjudicate" "python scripts\adjudicate_regressions.py --sport XC --log triage_c$i.log 2>&1 | Tee-Object adj_c$i.log")) { break }
    if (-not (Step "merge"      "python scripts\do_merge.py 2>&1 | Tee-Object merge_c$i.log")) { break }
    if (-not (Step "apply"      "python scripts\apply_triage.py 2>&1 | Tee-Object apply_c$i.log")) { break }
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Step "backfill"   "python backfill\backfill_normalize.py --sport XC --apply 2>&1 | Tee-Object bf_c$i.log")) { break }
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Step "engine"     "python engine\speed_ratings.py --sport XC 2>&1 | Tee-Object eng_c$i.log")) { break }
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Step "diag" "python scripts\diag_suspects.py --sport XC --c-threshold 0.10 --row-limit 3000000 2>&1 | Tee-Object diag_c$i.log")) { break }    Step "verify" "python scripts\verify_redetect.py 2>&1 | Tee-Object verify_c$i.log" | Out-Null
    Step "drop" "python scripts\_drop_old.py" | Out-Null

    # read the regression count out of this cycle's diag
    $line = Select-String -Path "diag_c$i.log" -Pattern 'REGRESSION\s+(\d+)' | Select-Object -First 1
    if (-not $line) { Write-Host "can't read REGRESSION count -- stopping"; break }
    $reg = [int]$line.Matches[0].Groups[1].Value
    Write-Host "CYCLE $i : REGRESSION = $reg (was $prevReg)" -ForegroundColor Green

    if ($reg -ge $prevReg) {
        Write-Host "no improvement -- stopping. Read the snapper instead." -ForegroundColor Red
        break
    }
    $prevReg = $reg
}

Write-Host "`nLOOP DONE -- read verify_c*.log" -ForegroundColor Green