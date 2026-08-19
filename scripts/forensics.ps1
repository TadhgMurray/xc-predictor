# forensics.ps1 -- reconstruct what loop.ps1 printed to a console that no longer exists.
# READ-ONLY. Touches no tables, kills no processes, deletes nothing.
# Run from the REPO ROOT (the folder holding scripts\, engine\, backfill\).

# --- param block: must be the first executable line in the file -------------
# $Root defaults to the current directory because the loop's Tee-Object paths
# were relative -- the logs live at the launch CWD, NOT beside this script.
param(
    [string]$Root = (Get-Location).Path
)

$ErrorActionPreference = "Stop"   # a forensics tool that fails silently is worse than none


# ============================================================================
# HELPER 1 -- LEVEL 0: is anything still running?
# ============================================================================
# Do this BEFORE any DB reasoning. If a backfill is still alive, the table
# state you're about to inspect is a moving target.
function Test-LoopAlive {
    # Win32_Process is the CIM class that exposes CommandLine -- Get-Process does NOT.
    # -Filter uses WQL (SQL-ish), so it's OR/AND, not PowerShell's -or/-and.
    $procs = Get-CimInstance Win32_Process `
        -Filter "Name='python.exe' OR Name='pwsh.exe' OR Name='powershell.exe'"

    if (-not $procs) {
        Write-Output "  (no python/powershell processes -- the loop is dead)"
        return
    }
    $procs | Select-Object ProcessId, Name, CreationDate, CommandLine
}


# ============================================================================
# HELPER 2 -- rebuild the "=== step : HH:mm:ss ===" timeline
# ============================================================================
# Those Write-Host lines are gone, but file mtimes carry the same information.
function Get-LoopTimeline {
    param([string]$Root)

    Get-ChildItem -Path $Root -Filter '*_c*.log' -File |
        Select-Object `
            @{ n = 'stage'; e = { ($_.BaseName -split '_c')[0] } },       # 'bf_c3' -> 'bf'
            @{ n = 'cycle'; e = { [int](($_.BaseName -split '_c')[1]) } }, # 'bf_c3' -> 3
            @{ n = 'ended'; e = { $_.LastWriteTime } },                    # ~when the step stopped
            @{ n = 'kb';    e = { [math]::Round($_.Length / 1KB, 1) } } |
        Sort-Object ended
}
# @{n=..;e=..} is a CALCULATED PROPERTY: n = the column name, e = a scriptblock
# evaluated per item, with $_ bound to that item. It's how you invent a column
# that doesn't exist on the source object.


# ============================================================================
# HELPER 3 -- the REGRESSION verdict the loop printed and threw away
# ============================================================================
function Get-RegressionTrend {
    param([string]$Root)

    Get-ChildItem -Path $Root -Filter 'diag_c*.log' -File |
        # -replace '\D','' strips every NON-digit, so 'diag_c3' -> '3'. Sorts 10 after 9.
        Sort-Object { [int]($_.BaseName -replace '\D', '') } |
        ForEach-Object {
            # Same regex the loop itself used, so this reproduces its arithmetic exactly.
            $m = Select-String -Path $_.FullName -Pattern 'REGRESSION\s+(\d+)' |
                 Select-Object -First 1

            [pscustomobject]@{
                cycle      = [int]($_.BaseName -replace '\D', '')
                regression = if ($m) { [int]$m.Matches[0].Groups[1].Value } else { 'UNREADABLE' }
            }
        }
}


# ============================================================================
# HELPER 4 -- did any step actually FAIL? (the bail bug means you must ask)
# ============================================================================
# Step() never returned a usable $false, so a crashed step did NOT stop the loop.
# A traceback here means every downstream cycle is suspect.
function Find-StepFailures {
    param([string]$Root)

    $hits = Select-String -Path (Join-Path $Root '*_c*.log') `
                          -Pattern 'Traceback|Exception|FATAL|Error:' `
                          -ErrorAction SilentlyContinue
    if (-not $hits) {
        Write-Output "  (clean -- no tracebacks in any step log)"
        return
    }
    $hits | Select-Object Filename, LineNumber, Line
}


# ============================================================================
# MAIN -- order is deliberate: liveness, then timeline, then trend, then errors
# ============================================================================
function Invoke-LoopForensics {
    param([string]$Root)

    # Write-Output, NOT Write-Host -- so this whole report can be tee'd to a file.
    # That is exactly the bug that lost last night's console.
    Write-Output "=== FORENSICS on $Root  ($(Get-Date -Format 'yyyy-MM-dd HH:mm')) ==="

    Write-Output "`n--- [0] STILL RUNNING? ---"
    Test-LoopAlive | Format-List | Out-String

    Write-Output "`n--- [1] TIMELINE (last row = what died) ---"
    Get-LoopTimeline -Root $Root | Format-Table -AutoSize | Out-String

    Write-Output "`n--- [2] REGRESSION TREND ---"
    Get-RegressionTrend -Root $Root | Format-Table -AutoSize | Out-String

    Write-Output "`n--- [3] STEP FAILURES (loop did NOT bail on these) ---"
    Find-StepFailures -Root $Root | Format-Table -AutoSize | Out-String
}
# Format-* emits formatting objects, not text. Out-String renders them to a real
# string so Tee-Object writes readable output instead of format-record garbage.


# --- entry point ------------------------------------------------------------
Invoke-LoopForensics -Root $Root