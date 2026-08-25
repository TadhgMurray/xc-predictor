# scripts/watch_then_train.ps1 -- wait for the rebuild, then run the model.
#
#     # find the PowerShell window running the pipeline / overrides run:
#     Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
#         Select-Object ProcessId, CommandLine
#     # then, from the project root:
#     .\scripts\watch_then_train.ps1 -TargetPid 12345
#
# Waits for that process to exit, REFUSES to train on a failed rebuild
# (a crashed pipeline leaves half-swapped tables; chunks extracted from
# that mix two scale regimes -- the train/serve split in a new costume),
# then runs feature extraction and training, everything logged.

param(
    [Parameter(Mandatory = $true)][int]$TargetPid,
    # Smoke by default: proves the chain end to end on CPU-sized data.
    # Pass -FullChunks once the GPU is real and bbar has been set.
    [switch]$FullChunks
)

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dir = "logs\$stamp-model"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Host "  waiting on pid $TargetPid; logging to $dir" -ForegroundColor Cyan

# ---- 1. wait ---------------------------------------------------------- #
try {
    Wait-Process -Id $TargetPid -ErrorAction Stop
} catch {
    Write-Host "  pid $TargetPid is not running -- assuming it already finished." -ForegroundColor Yellow
}

# ---- 2. refuse a failed rebuild --------------------------------------- #
# The newest pipeline log dir tells the truth: a completed run's combined
# log carries the KEY NUMBERS banner; a crashed one carries FAILED.
$latest = Get-ChildItem logs -Directory |
          Where-Object { $_.Name -notlike "*-model" -and $_.Name -notlike "*-overrides" } |
          Sort-Object Name -Descending | Select-Object -First 1
if ($latest) {
    $log = Join-Path $latest.FullName "pipeline.log"
    if (Test-Path $log) {
        if (Select-String -Path $log -SimpleMatch -Quiet -Pattern "FAILED") {
            Write-Host "  $log reports a FAILED step -- NOT training on a" -ForegroundColor Red
            Write-Host "  half-rebuilt database. Fix the pipeline first." -ForegroundColor Red
            exit 1
        }
        Write-Host "  $($latest.Name)\pipeline.log looks clean" -ForegroundColor Green
    } else {
        Write-Host "  (no pipeline.log under $($latest.Name); proceeding on your word)" -ForegroundColor Yellow
    }
}

function Step($name, $cmd) {
    $log = "$dir\$name.log"
    Write-Host "`n  $name    $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Yellow
    $global:LASTEXITCODE = 0
    & $cmd 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  $name FAILED (exit $LASTEXITCODE) -- stopping. See $log" -ForegroundColor Red
        exit 1
    }
    Write-Host "  $name ok" -ForegroundColor Green
}

# ---- 3. features, then train ------------------------------------------ #
Step "01_features" { python model\feature_extraction.py }

# MAX_CHUNKS lives in model\train.py; flip it for this run only, put it back.
$train = "model\train.py"
$orig = Get-Content $train -Raw
if (-not $FullChunks) {
    # Smoke cap: enough to prove chunks load, loss falls, checkpoint saves.
    $patched = $orig -replace "(?m)^MAX_CHUNKS = .*$", "MAX_CHUNKS = 20"
    Set-Content $train $patched -NoNewline
    Write-Host "  MAX_CHUNKS = 20 for this run (smoke); -FullChunks for everything" -ForegroundColor Cyan
}
try {
    Step "02_train" { python model\train.py }
} finally {
    if (-not $FullChunks) { Set-Content $train $orig -NoNewline }
}

Write-Host "`n  DONE. Loss curves in $dir\02_train.log" -ForegroundColor Green
