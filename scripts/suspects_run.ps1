# suspects_run.ps1 -- post-identity-recovery detect cycle
# SCRAPER/LAUNCHER MUST BE OFF: engine swaps tables; rows written
# mid-rebuild land in <table>_old and are LOST.

$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "MMdd_HHmm"

function Step($name, $cmd) {
    Write-Host "`n=== $name : $(Get-Date -Format 'HH:mm:ss') ===" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
        Write-Host "!! $name exited $LASTEXITCODE -- STOPPING CHAIN" -ForegroundColor Red
        exit 1
    }
}

Step "engine XC" "python engine\speed_ratings.py --sport XC 2>&1 | Tee-Object -FilePath eng_xc_$stamp.log"
Step "engine TF" "python engine\speed_ratings.py --sport TF 2>&1 | Tee-Object -FilePath eng_tf_$stamp.log"
Step "diag XC"   "python scripts\diag_suspects.py --sport XC --row-fast 0.06 --row-slow 0.25 --row-limit 3000000 2>&1 | Tee-Object -FilePath diag_xc_$stamp.log"
Step "diag TF"   "python scripts\diag_suspects.py --sport TF --row-fast 0.06 --row-slow 0.25 --row-limit 1000000 2>&1 | Tee-Object -FilePath diag_tf_$stamp.log"
Step "verify"    "python scripts\verify_redetect.py 2>&1 | Tee-Object -FilePath verify_$stamp.log"

Write-Host "`nCHAIN COMPLETE -- read verify_$stamp.log" -ForegroundColor Green