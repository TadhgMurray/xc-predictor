#!/usr/bin/env bash
# oct23_arm.sh -- one arm of the Oct 23 factorial: the nightly 08_golive
# solve exactly as deploy/run_pipeline.sh runs it, but --golive-dry, on the
# pack and with the tilt given, into its own folder. Nothing reaches the
# database or engine/data.
#
#   bash scripts/oct23_arm.sh REPO PACK TILT ARM_DIR
#   bash scripts/oct23_arm.sh /srv/xc-predictor /srv/oct23/pack_noplace.npz own /srv/oct23/A
#
# ★ THE COMMAND IS THE PIPELINE'S OWN. The 08_golive block is read out of
#   REPO/deploy/run_pipeline.sh at run time, with the env file and
#   deploy/solve_env.sh loaded as the pipeline loads them, so an arm cannot
#   drift from the nightly flags. Only these change: --golive becomes
#   --golive-dry, run_joint.py becomes scripts/oct23_dry.py (which keeps the
#   would-be writes), and --pack / --out point at the arm.
# ! XCP_FROM_STATE IS CLEARED: an arm solves, it never loads another
#   run's state.
# ! NOT `deploy/run_pipeline.sh --dry-run`: it prints the plan, but it
#   also clears stale maintenance and asks db_activity to stop a running
#   pipeline before it gets there.
set -eo pipefail
REPO=$(cd "$1" && pwd); PACK=$(readlink -f "$2"); TILT="$3"; ARM=$(readlink -m "$4")
HERE=$(cd "$(dirname "$0")" && pwd)
case "$TILT" in own|hs) ;; *) echo "TILT is own or hs" >&2; exit 2 ;; esac
[ -f "$PACK" ] || { echo "no pack at $PACK" >&2; exit 2; }
case "$ARM" in "$REPO"/engine/data*) echo "ARM_DIR must not be in engine/data" >&2; exit 2 ;; esac
mkdir -p "$ARM"
PY=${PY:-/srv/venv/bin/python}

cmd=$(awk '/^ *step 08_golive /{on=1} on{print; if ($0 !~ /\\$/) exit}' "$REPO/deploy/run_pipeline.sh")
[ -n "$cmd" ] || { echo "no 08_golive step in $REPO/deploy/run_pipeline.sh" >&2; exit 1; }
cmd=$(printf '%s\n' "$cmd" | sed -e 's/^ *step 08_golive *//' \
        -e "s#engine/run_joint.py#$HERE/oct23_dry.py --repo $REPO --arm-dir $ARM --#" \
        -e 's/ --golive / --golive-dry /')
case "$cmd" in *--golive-dry*) ;; *) echo "could not turn --golive into --golive-dry" >&2; exit 1 ;; esac

cd "$REPO"
set -a; . /etc/xc-predictor.env; set +a
. "$REPO/deploy/solve_env.sh"
export XCP_TILT_SCALE="$TILT"
unset XCP_FROM_STATE
{
  echo "[oct23] arm $ARM: pack $PACK, tilt $TILT, code $(git -C "$REPO" rev-parse --short HEAD)"
  # only the model settings the command reads, never the rest of the env file
  for v in $(printf '%s\n' "$cmd" | grep -o 'XCP_[A-Z0-9_]*' | sort -u); do
    [ -n "${!v:-}" ] && printf '%s=%s ' "$v" "${!v}"
  done; echo
} | tee "$ARM/arm.log"
eval "$cmd --pack \"$PACK\" --out \"$ARM/joint_difficulty.npz\"" 2>&1 | tee -a "$ARM/arm.log"
