# scripts/overnight.ps1 -- the whole night, one command, no babysitting.
#
#     .\scripts\overnight.ps1              # smoke-capped training at the end
#     .\scripts\overnight.ps1 -FullChunks  # full training (GPU nights)
#
# Chain: overrides rebuild -> spline refit -> full pipeline -> feature
# extraction -> training. The one design rule: a failure DEGRADES instead
# of stranding the night wherever that is safe, and stops hard only where
# continuing could corrupt something.
#
#   overrides fail BEFORE 04_apply   corrections.py untouched -> WARN and
#                                    continue: the committed fixes (purge,
#                                    clamps, hand corrections) still rebuild.
#                                    Rerun run_overrides another day.
#   overrides fail AT/AFTER apply    corrections.py may be half-written ->
#                                    verify it imports; rerun dump_overrides;
#                                    hard stop only if the import fails.
#   fitter / pipeline fail           hard stop. Everything downstream reads
#                                    what they write; there is no safe
#                                    degradation past a broken rebuild.
#   features / train fail            the site is already rebuilt; only the
#                                    model run is lost.

param([switch]$FullChunks)

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dir = "logs\$stamp-overnight"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$summary = @()
Write-Host "  overnight run -- logging to $dir" -ForegroundColor Cyan

function Step($name, $cmd) {
    $log = "$dir\$name.log"
    Write-Host "`n$('=' * 70)`n  $name    $(Get-Date -Format 'HH:mm:ss')`n$('=' * 70)" -ForegroundColor Yellow
    $global:LASTEXITCODE = 0
    # ! OUT-HOST, OR THE BOOLEAN IS BURIED. Tee-Object passes every line
    #   THROUGH, and a function's unconsumed pipeline output joins its
    #   return value -- so without Out-Host this returned
    #   [line, line, ..., $false], a non-empty array, which is TRUTHY.
    #   `if (-not (Step ...))` could never fire: the 2026-08-25 night died
    #   at 08_golive, ran features+train anyway, and the summary said
    #   "pipeline: complete". Out-Host prints each line to the console
    #   (live -- the old version silently swallowed step output too) and
    #   emits nothing, leaving the boolean below as the ONLY return value.
    #   The exit code itself was never the problem: a child .ps1's `exit 1`
    #   does reach $LASTEXITCODE through `&` and the pipe.
    & $cmd 2>&1 | Tee-Object -FilePath $log | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Bail($why) {
    $script:summary += "STOPPED: $why"
    $script:summary | Out-File "$dir\SUMMARY.txt" -Encoding utf8
    Write-Host "`n  STOPPED: $why" -ForegroundColor Red
    Write-Host "  Summary in $dir\SUMMARY.txt" -ForegroundColor Red
    exit 1
}

# ---- 1. overrides ----------------------------------------------------- #
# -ForceApply: the owner's rule -- an unattended run ALWAYS applies.
# The caps warn in the log instead of ending the night; every proposal
# direction is clamped downward, so an excess can only over-deflate.
$ok = Step "01_overrides" { .\run_overrides.ps1 -Reset -Replace -SkipPipeline -ForceApply }
if ($ok) {
    $summary += "overrides: applied"
} else {
    # Did the failure happen before corrections.py was touched?
    $ovr = Get-ChildItem logs -Directory -Filter "*-overrides" |
           Sort-Object Name -Descending | Select-Object -First 1
    $applyStarted = $ovr -and (Test-Path (Join-Path $ovr.FullName "04_apply.log"))
    if ($applyStarted) {
        # Ambiguous: verify the file is whole, rebuild the dump, then judge.
        python -c "import sys; sys.path.insert(0,'engine'); import corrections" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Bail ("overrides failed mid-apply and corrections.py does not " +
                  "import. Restore the backup run_overrides printed, then rerun.")
        }
        Step "01b_dump_repair" { python engine\dump_overrides.py } | Out-Null
        $summary += "overrides: FAILED after apply began, but corrections.py imports and the dump was rebuilt -- CHECK $($ovr.Name) IN THE MORNING"
        Write-Host "  continuing: corrections.py is whole" -ForegroundColor Yellow
    } else {
        $summary += "overrides: FAILED before apply (corrections.py untouched) -- continuing with committed corrections only; rerun run_overrides later. See $($ovr.Name)"
        Write-Host "  continuing without the new pass layer (corrections.py untouched)" -ForegroundColor Yellow
    }
}

# ---- 2. spline refit -------------------------------------------------- #
if (-not (Step "02_fit_spline" { python engine\fit_distance_exponent.py --fresh })) {
    Bail "spline refit failed -- the pipeline would build on a stale or missing artifact. See 02_fit_spline.log"
}
$summary += "spline: refit (see 02_fit_spline.log for the MEASURED extension lines)"

# ---- 3. the pipeline -------------------------------------------------- #
if (-not (Step "03_pipeline" { .\run_pipeline.ps1 })) {
    Bail "pipeline failed -- database may be mid-rebuild. See 03_pipeline.log and the pipeline's own logs dir"
}
$summary += "pipeline: complete"

# ---- 4. features + training ------------------------------------------- #
if (-not (Step "04_features" { python model\feature_extraction.py })) {
    $summary += "features: FAILED (site is fine; model run lost). See 04_features.log"
    $summary | Out-File "$dir\SUMMARY.txt" -Encoding utf8
    exit 1
}
$summary += "features: extracted"

$train = "model\train.py"
$orig = Get-Content $train -Raw
if (-not $FullChunks) {
    Set-Content $train ($orig -replace "(?m)^MAX_CHUNKS = .*$", "MAX_CHUNKS = 20") -NoNewline
}
try {
    if (Step "05_train" { python model\train.py }) {
        $summary += if ($FullChunks) { "train: full run complete" }
                    else { "train: smoke run complete (MAX_CHUNKS=20)" }
    } else {
        $summary += "train: FAILED. See 05_train.log"
    }
} finally {
    if (-not $FullChunks) { Set-Content $train $orig -NoNewline }
}

$summary | Out-File "$dir\SUMMARY.txt" -Encoding utf8
Write-Host "`n$('=' * 70)" -ForegroundColor Green
Write-Host "  MORNING SUMMARY" -ForegroundColor Green
$summary | ForEach-Object { Write-Host "    $_" }
Write-Host "  (also in $dir\SUMMARY.txt)" -ForegroundColor Green
