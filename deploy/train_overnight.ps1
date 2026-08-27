# Project: xc-predictor / deploy
# File:    train_overnight.ps1
# Purpose: The whole model run, chained, unattended, logged. Start it and
#          go to sleep.
#
#     .\deploy\train_overnight.ps1
#     .\deploy\train_overnight.ps1 -SkipExtract      # chunks already built
#
#   extract -> train -> verify artifacts -> upload (only if it can be
#   done without a prompt)
#
# ★ IT NEVER BLOCKS ON A PROMPT. An overnight run that stops at
#   "root@host's password:" has wasted the night AND the GPU, so ssh is
#   tested in BatchMode first and the upload is SKIPPED with
#   instructions rather than left hanging. The model is safe on disk
#   either way; uploading it in the morning costs minutes.
#
# ★ EACH STAGE GATES THE NEXT. train.py on a half-written chunk set
#   produces a model fitted to whatever happened to be there, which is
#   worse than no model because it looks like one.

param(
    [string] $Python  = "C:\venvs\rocm-train\Scripts\python.exe",
    [string] $Server  = "root@104.243.32.7",
    [switch] $SkipExtract,
    [switch] $SkipUpload,
    [switch] $SkipSmoke,
    [int]    $SmokeAthletes = 4000
)

$ErrorActionPreference = "Continue"
$stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
New-Item -ItemType Directory -Force -Path logs | Out-Null
$log = "logs\overnight-$stamp.log"

function Say {
    param([string] $Text, [string] $Colour = "White")
    $line = "[{0}] {1}" -f (Get-Date).ToString("HH:mm:ss"), $Text
    Write-Host $line -ForegroundColor $Colour
    Add-Content -Path $log -Value $line
}

function Run-Stage {
    # ! NOT $Args. Like $Host, it is a PowerShell AUTOMATIC variable --
    #   the array of arguments to the function -- so a parameter by that
    #   name does not bind, @Args expands to nothing, and the stage runs
    #   bare `python -u`, which opens a REPL and waits on stdin forever.
    #   It looks exactly like a hung job: START printed, a 21 MB python
    #   process, zero CPU, no database activity.
    param([string] $Name, [string[]] $ScriptArgs)
    Say "START $Name" "Cyan"
    # ! -u IS NOT OPTIONAL HERE. Piping to Tee-Object makes stdout a pipe,
    #   and Python block-buffers a pipe -- so the log stays empty for
    #   hours and the run looks hung when it is fine. Unbuffered means
    #   every "Saved chunk_NNNN.pt" lands the moment it happens.
    & $Python -u @ScriptArgs 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        Say "FAILED $Name (exit $LASTEXITCODE) -- chain stops here." "Red"
        return $false
    }
    Say "OK $Name" "Green"
    return $true
}

Say "logging to $log"
if (-not (Test-Path $Python)) {
    Say "no python at $Python -- pass -Python <path>" "Red"; exit 1
}

# ---- preflight ------------------------------------------------------- #
# ! CHECK THE GPU BEFORE THE HOURS, NOT AFTER. Training on CPU would not
#   finish in any useful time, and finding that out at dawn is the
#   expensive way.
& $Python -c "import torch,sys; ok=torch.cuda.is_available(); print('GPU:', torch.cuda.get_device_name(0) if ok else 'CPU ONLY'); sys.exit(0 if ok else 1)" 2>&1 |
    Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Say "no GPU visible -- refusing to start." "Red"; exit 1 }

& $Python -c "import psycopg2, sklearn, torch" 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) {
    Say "missing deps -- run: pip install scikit-learn psycopg2-binary" "Red"; exit 1
}

$free = (Get-PSDrive C).Free / 1GB
Say ("free disk on C: {0:N1} GB" -f $free)
if ($free -lt 30) { Say "under 30 GB free -- chunks may not fit." "Yellow" }

# ---- 1. extract ------------------------------------------------------ #
if (-not $SkipExtract) {
    # ★ SMOKE FIRST. The full extraction is an hour, and a crash at
    #   minute 68 over one malformed row costs the night. This runs the
    #   entire path -- encoders, vocab, stream, example build, chunk
    #   write -- over a few thousand athletes, so a fault shows up in
    #   minutes and points at the same line. -SkipSmoke to bypass.
    if (-not $SkipSmoke) {
        Remove-Item -Recurse -Force model\data_smoke -ErrorAction SilentlyContinue
        if (-not (Run-Stage "smoke (extraction, $SmokeAthletes athletes)" @(
                "model\feature_extraction.py",
                "--max-athletes", "$SmokeAthletes",
                "--out", "model\data_smoke"))) {
            Say "The SMOKE run failed, so the full run would fail the same way." "Red"
            Say "Nothing was wasted. Fix the error above and re-run." "Red"
            exit 1
        }
        foreach ($f in @("metadata.pkl", "encoders.pkl", "venue_vocab.pkl")) {
            if (-not (Test-Path (Join-Path "model\data_smoke" $f))) {
                Say "smoke run produced no $f -- stopping before the long run." "Red"
                exit 1
            }
        }
        Say "SMOKE PASSED -- the full path works on real data." "Green"
        Remove-Item -Recurse -Force model\data_smoke -ErrorAction SilentlyContinue
    }
    if (-not (Run-Stage "feature_extraction" @("model\feature_extraction.py"))) { exit 1 }
} else {
    Say "SKIPPED feature_extraction (-SkipExtract)" "Yellow"
}

# ! encoders.pkl and venue_vocab.pkl are written LAST by saveAll, so
#   their presence is the proof extraction finished rather than died
#   somewhere in the middle leaving a plausible pile of chunks.
foreach ($f in @("model\data\metadata.pkl", "model\data\encoders.pkl",
                 "model\data\venue_vocab.pkl")) {
    if (-not (Test-Path $f)) {
        Say "extraction did not produce $f -- it did not finish. Stopping." "Red"
        exit 1
    }
}
Say "extraction artifacts present"

# ---- 2. train -------------------------------------------------------- #
if (-not (Run-Stage "train" @("model\train.py"))) { exit 1 }

# ---- 3. verify what the SITE needs ----------------------------------- #
$need = @("model.pt", "target_stats.pkl", "encoders.pkl", "venue_vocab.pkl")
$missing = $need | Where-Object { -not (Test-Path (Join-Path "model\data" $_)) }
if ($missing) {
    Say "MISSING for the site: $($missing -join ', ')" "Red"
    exit 1
}
Say "all four site artifacts present" "Green"
& $Python -u "model\predict_check.py" 2>&1 | Tee-Object -FilePath $log -Append

# ---- 4. upload, but only if nothing will prompt ---------------------- #
if ($SkipUpload) { Say "upload skipped (-SkipUpload)"; exit 0 }

& ssh -o BatchMode=yes -o ConnectTimeout=10 $Server "true" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Say "ssh needs a password, so the upload would hang until morning." "Yellow"
    Say "The model is SAFE on disk. Upload it when you wake:" "Yellow"
    Say "  .\deploy\ship_when_ready.ps1 -Only model -AcceptExistingModel" "Yellow"
    Say "To make it automatic next time, set up a key first:" "Yellow"
    Say "  ssh-keygen -t ed25519 -C racecast" "Yellow"
    Say "  type `$env:USERPROFILE\.ssh\id_ed25519.pub | ssh $Server `"mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys`"" "Yellow"
    exit 0
}

Say "key auth works -- uploading the model" "Cyan"
& ssh $Server "mkdir -p /srv/xc-predictor/model/data"
$paths = $need | ForEach-Object { Join-Path "model\data" $_ }
& scp @paths "${Server}:/srv/xc-predictor/model/data/"
if ($LASTEXITCODE -eq 0) {
    Say "MODEL UPLOADED. Verify on the server:" "Green"
    Say "  /srv/venv/bin/python /srv/xc-predictor/model/predict_check.py" "Green"
} else {
    Say "upload failed (exit $LASTEXITCODE) -- the model is still on disk." "Red"
}
Say "DONE"
