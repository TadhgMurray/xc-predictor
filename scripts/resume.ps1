# resume.ps1 -- finish the cycle the crash interrupted, then run the loop out.
# Enters at the ENGINE of $ResumeCycle, because that cycle's backfill already landed.
param(
    [int]$ResumeCycle = 2,      # cycle whose bf_c2 completed at 02:24
    [int]$MaxCycles   = 8,
    [int]$PrevReg     = 1210    # cycle 1's REGRESSION -- the bar cycle 2 must beat
)
$ErrorActionPreference = "Continue"

# ---- HELPER: one step, gated on exit code ----------------------------------
# FIX vs loop.ps1: `| Out-Null` swallows the pipeline output so the function
# returns ONLY $true/$false. Tee-Object is a pass-through, so the .log file is
# still written -- nothing is lost. But the caller's `if (-not (Step ...))` now
# sees a real boolean instead of a truthy array, so `break` actually fires.
# Write-Output (not Write-Host) so this narration survives into the log file.
function Step($name, $cmd) {
    Write-Output "`n=== $name : $(Get-Date -Format 'HH:mm:ss') ==="
    Invoke-Expression $cmd | Out-Null
    if ($LASTEXITCODE -gt 1) { Write-Output "!! $name exited $LASTEXITCODE -- STOPPING"; return $false }
    return $true
}

# ---- HELPER: read the cycle's verdict off disk ------------------------------
function Get-Reg($i) {
    $m = Select-String -Path "diag_c$i.log" -Pattern 'REGRESSION\s+(\d+)' | Select-Object -First 1
    if (-not $m) { return $null }
    return [int]$m.Matches[0].Groups[1].Value
}

# ---- HELPER: front half of a cycle (triage -> backfill) ---------------------
function Invoke-CycleHead($i) {
    if (-not (Step "triage"     "python scripts\triage_regressions.py --sport XC --from-worksheet 2>&1 | Tee-Object triage_c$i.log")) { return $false }
    if (-not (Step "adjudicate" "python scripts\adjudicate_regressions.py --sport XC --log triage_c$i.log 2>&1 | Tee-Object adj_c$i.log")) { return $false }
    if (-not (Step "merge"      "python scripts\do_merge.py 2>&1 | Tee-Object merge_c$i.log")) { return $false }
    if (-not (Step "apply"      "python scripts\apply_triage.py 2>&1 | Tee-Object apply_c$i.log")) { return $false }
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Step "backfill"   "python backfill\backfill_normalize.py --sport XC --apply 2>&1 | Tee-Object bf_c$i.log")) { return $false }
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    return $true
}

# ---- HELPER: back half (engine -> verify) ----------------------------------
# Split from the head *specifically* so the resume can enter here, mid-cycle.
function Invoke-CycleTail($i) {
    if (-not (Step "engine" "python engine\speed_ratings.py --sport XC 2>&1 | Tee-Object eng_c$i.log")) { return $false }
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Step "diag"   "python scripts\diag_suspects.py --sport XC --c-threshold 0.10 --row-limit 3000000 2>&1 | Tee-Object diag_c$i.log")) { return $false }
    Step "verify" "python scripts\verify_redetect.py 2>&1 | Tee-Object verify_c$i.log" | Out-Null
    Step "drop" "python scripts\_drop_old.py" | Out-Null
    return $true
}

# ---- HELPER: the bail decision ---------------------------------------------
# [ref] because $prev must persist across calls -- PowerShell passes by value.
function Test-Improved($i, [ref]$prev) {
    $reg = Get-Reg $i
    if ($null -eq $reg) { Write-Output "can't read REGRESSION from diag_c$i.log -- stopping"; return $false }
    Write-Output "CYCLE $i : REGRESSION = $reg (was $($prev.Value))"
    if ($reg -ge $prev.Value) { Write-Output "no improvement -- stopping. Read the snapper instead."; return $false }
    $prev.Value = $reg
    return $true
}

# ---- MAIN ------------------------------------------------------------------
Write-Output "########## RESUME CYCLE $ResumeCycle (backfill already landed) ##########"
if (-not (Invoke-CycleTail $ResumeCycle)) { Write-Output "resume failed -- stop"; exit 1 }

$prev = $PrevReg
if (-not (Test-Improved $ResumeCycle ([ref]$prev))) { Write-Output "LOOP DONE"; exit 0 }

for ($i = $ResumeCycle + 1; $i -le $MaxCycles; $i++) {
    Write-Output "`n########## CYCLE $i ##########"
    if (-not (Invoke-CycleHead $i)) { break }
    if (-not (Invoke-CycleTail $i)) { break }
    if (-not (Test-Improved $i ([ref]$prev))) { break }
}
Write-Output "`nLOOP DONE -- read verify_c*.log"