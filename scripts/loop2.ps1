# loop2.ps1 -- run correction cycles unattended, and STOP when there is
# nothing left to apply. Replaces loop.ps1.
#
# WHY THIS EXISTS
#   loop.ps1 ran triage -> adjudicate -> merge -> apply -> backfill (14m)
#   -> engine (22m) -> diag (13m), and only THEN read the regression count.
#   It spent 49 minutes to discover whether it should have bothered. On
#   2026-07-15 it burned six cycles reproducing REGRESSION=1210 because apply
#   was silently rolling back every time.
#
#   The decision now happens FIRST. triage + adjudicate cost 30 seconds and
#   adjudicate prints CHANGE = how many repairs DIFFER from what corrections.py
#   already holds. CHANGE == 0 means a full cycle is mathematically incapable
#   of moving the ruler. So we stop, in 30 seconds, instead of 49 minutes.
#
# THE BAIL BUG (present in loop.ps1 AND in the resume.ps1 written earlier today)
#   function Step($name,$cmd) {
#       Write-Output "=== $name ==="        <- goes into the OUTPUT STREAM
#       Invoke-Expression $cmd              <- so does all of python's stdout
#       if (...) { return $false }
#       return $true
#   }
#   A PowerShell function returns EVERYTHING it emits, not just what follows
#   `return`. So Step returned @("=== triage ===", "line", ..., $false) -- an
#   ARRAY. PowerShell casts a non-empty array to $true by LENGTH, so
#   `if (-not (Step ...)) { break }` never fired. Every failed step was run
#   straight over.
#
#   Fixed here by rule: a function that returns a boolean emits NOTHING ELSE.
#   Narration goes to Write-Host (host only, never the pipeline) AND to a log
#   file via Add-Content. Two destinations, neither of them the return value.
#   That is also the "every artifact must outlive its console" fix -- the
#   loop's own narration now survives in $LogFile.
#
# USAGE  (run from the REPO ROOT, detached so VS Code cannot take it with it)
#   Start-Process powershell -WorkingDirectory "$PWD" -ArgumentList `
#     '-ExecutionPolicy','Bypass','-File','scripts\loop2.ps1'
#
# READ AFTERWARDS
#   Get-Content loop2_<stamp>.log

param(
    [string]$Sport     = "XC",
    [int]$StartCycle   = 11,      # diag_c10 wrote the worksheet this reads
    [int]$MaxCycles    = 18,
    [int]$PrevReg      = 999999,  # the bar cycle 1 must beat; 999999 = unknown
    [double]$CThresh   = 0.10
)

$ErrorActionPreference = "Continue"
$script:LogFile = "loop2_$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
$sport_lc = $Sport.ToLower()


# ================================================================== #
# CHUNK 1 -- LOGGING (the narration that must outlive the console)
# ================================================================== #

# Write-Log
# Purpose : one line to the console AND to the log file.
# Note    : Write-Host writes to the HOST, not the pipeline -- that is exactly
#           why it is safe to call from inside a function that returns a bool.
#           Add-Content writes the file. Neither touches the output stream.
function Write-Log {
    param([string]$Msg, [string]$Colour = "Gray")
    $line = "$(Get-Date -Format 'HH:mm:ss')  $Msg"
    Write-Host $line -ForegroundColor $Colour
    Add-Content -Path $script:LogFile -Value $line
}


# ================================================================== #
# CHUNK 2 -- ONE STEP (returns a BOOLEAN and nothing else)
# ================================================================== #

# Invoke-Step
# Purpose : run one python step; return $true on success, $false on failure.
# Arguments: Name -- for the log; Cmd -- the command string (its own Tee-Object
#           writes the step log, which is pass-through so nothing is lost).
# Output  : [bool] ONLY. This is load-bearing -- see the header.
function Invoke-Step {
    param([string]$Name, [string]$Cmd)

    Write-Log "  -> $Name" "DarkGray"
    Invoke-Expression $Cmd | Out-Null      # swallow stdout: it is already tee'd

    if ($LASTEXITCODE -gt 1) {
        Write-Log "  !! $Name exited $LASTEXITCODE -- STOPPING" "Red"
        return $false
    }
    return $true
}


# ================================================================== #
# CHUNK 3 -- THE GATE (30 seconds, decides the next 49 minutes)
# ================================================================== #

# Get-ChangeCount
# Purpose : run triage + adjudicate and read adjudicate's CHANGE line -- the
#           number of repairs that DIFFER from the live corrections.py values.
# Output  : [int] count, or -1 if unreadable.
# Note    : Select-String, not findstr. Tee-Object writes UTF-16 on PS 5.1 and
#           findstr cannot read it ("input file is in Unicode format").
function Get-ChangeCount {
    param([int]$Cycle)

    if (-not (Invoke-Step "triage" `
        "python scripts\triage_regressions.py --sport $Sport --from-worksheet 2>&1 | Tee-Object triage_c$Cycle.log")) {
        return -1
    }
    if (-not (Invoke-Step "adjudicate" `
        "python scripts\adjudicate_regressions.py --sport $Sport --log triage_c$Cycle.log 2>&1 | Tee-Object adj_c$Cycle.log")) {
        return -1
    }

    $m = Select-String -Path "adj_c$Cycle.log" -Pattern 'CHANGE\s*:\s*([\d,]+)' |
         Select-Object -First 1
    if (-not $m) {
        Write-Log "  !! cannot read CHANGE from adj_c$Cycle.log" "Red"
        return -1
    }
    return [int]($m.Matches[0].Groups[1].Value -replace ',', '')
}


# ================================================================== #
# CHUNK 4 -- THE EXPENSIVE HALF (only reached when CHANGE > 0)
# ================================================================== #

# Invoke-Apply
# Purpose : merge the adjudicated repairs into corrections.py.
function Invoke-Apply {
    param([int]$Cycle)
    if (-not (Invoke-Step "merge" "python scripts\do_merge.py 2>&1 | Tee-Object merge_c$Cycle.log")) { return $false }
    if (-not (Invoke-Step "apply" "python scripts\apply_triage.py 2>&1 | Tee-Object apply_c$Cycle.log")) { return $false }

    # apply_triage RESTORES from backup and exits 1 if the merged file fails to
    # import -- but check the log too, because a rollback means the repairs did
    # NOT land and the 49 minutes below would measure nothing. This is the
    # failure that hid all day on 7/15.
    if (Select-String -Path "apply_c$Cycle.log" -Pattern 'RESTORED' -Quiet) {
        Write-Log "  !! apply ROLLED BACK -- corrections.py unchanged. STOPPING." "Red"
        return $false
    }
    return $true
}

# Invoke-Rebuild
# Purpose : backfill -> engine -> diag. ~49 min. The _drop_old calls between
#           stages clear <table>_old from the previous swap.
function Invoke-Rebuild {
    param([int]$Cycle)
    if (-not (Invoke-Step "backfill" "python backfill\backfill_normalize.py --sport $Sport --apply 2>&1 | Tee-Object bf_c$Cycle.log")) { return $false }
    Invoke-Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Invoke-Step "engine" "python engine\speed_ratings.py --sport $Sport 2>&1 | Tee-Object eng_c$Cycle.log")) { return $false }
    Invoke-Step "drop" "python scripts\_drop_old.py" | Out-Null
    if (-not (Invoke-Step "diag" "python scripts\diag_suspects.py --sport $Sport --c-threshold $CThresh --row-limit 3000000 2>&1 | Tee-Object diag_c$Cycle.log")) { return $false }
    Invoke-Step "verify" "python scripts\verify_redetect.py 2>&1 | Tee-Object verify_c$Cycle.log" | Out-Null
    Invoke-Step "drop" "python scripts\_drop_old.py" | Out-Null
    return $true
}

# Get-Regression
# Purpose : this cycle's REGRESSION count, read off diag's log.
# Output  : [int], or -1 if unreadable.
function Get-Regression {
    param([int]$Cycle)
    $m = Select-String -Path "diag_c$Cycle.log" -Pattern 'REGRESSION\s+([\d,]+)' |
         Select-Object -First 1
    if (-not $m) { return -1 }
    return [int]($m.Matches[0].Groups[1].Value -replace ',', '')
}


# ================================================================== #
# CHUNK 5 -- MAIN
# ================================================================== #

Write-Log "########## LOOP2  sport=$Sport  cycles $StartCycle..$MaxCycles ##########" "Yellow"
Write-Log "log: $script:LogFile"

$prev = $PrevReg

for ($i = $StartCycle; $i -le $MaxCycles; $i++) {
    Write-Log ""
    Write-Log "########## CYCLE $i ##########" "Yellow"

    # --- THE GATE: 30 seconds ---------------------------------------------
    $changes = Get-ChangeCount $i
    if ($changes -lt 0) { Write-Log "gate unreadable -- stopping" "Red"; break }

    Write-Log "  CHANGE = $changes" "Cyan"
    if ($changes -eq 0) {
        Write-Log "  ZERO real changes -- a cycle CANNOT move the ruler." "Green"
        Write-Log "  The distance-repair loop is exhausted on this crop." "Green"
        Write-Log "  -> go to the worklist / z-cut. STOPPING (0 min wasted)." "Green"
        break
    }

    # --- earned the 49 minutes --------------------------------------------
    if (-not (Invoke-Apply $i))   { break }
    if (-not (Invoke-Rebuild $i)) { break }

    $reg = Get-Regression $i
    if ($reg -lt 0) { Write-Log "cannot read REGRESSION from diag_c$i.log -- stopping" "Red"; break }
    Write-Log "  CYCLE $i : REGRESSION = $reg  (was $prev)" "Green"

    if ($reg -ge $prev) {
        Write-Log "  no improvement -- stopping. Read the snapper / z-cut." "Red"
        break
    }
    $prev = $reg
}

Write-Log ""
Write-Log "LOOP2 DONE -- read $script:LogFile and verify_c*.log" "Green"