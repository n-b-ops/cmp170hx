#!/usr/bin/env bash
# run-acceptance.sh - the zero-argument seller-stand session.
#
# One command does the whole job: verify the carried kit, bring the stand
# up, AUTO-DETECT the gate flags the environment needs (sole/shared GPU,
# dmesg readability, settable power cap vs provider preset), run the gate,
# then write REPORT.md + a human-readable SUMMARY.md into one evidence dir.
#
# No CLI arguments by design - everything is auto-detected or overridable
# via environment variables:
#   PROFILE       standard (default) | express | extended | smoke
#   MEMTEST_MIN / BURN_MIN / IDLE_MIN   stage overrides (minutes)
#   GPU           card index (default 0)
#   POWER_W       force a cap; default: set 180 W, else the live preset
#   TAG           evidence dir name (default stand-<date>-<time>)
#   EXTRA_RECEIPTS  space-separated informational inference receipts to
#                   fold into SUMMARY.md
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
log(){ echo "[run $(date +%H:%M:%S)] $*"; }
die(){ echo "[run] FAIL: $*" >&2; exit 1; }

TAG=${TAG:-stand-$(date +%Y%m%d-%H%M)}
PROFILE=${PROFILE:-standard}
GPU=${GPU:-0}
EVID=${EVID_DIR:-$REPO/evidence/$TAG}

log "1/6 kit selftest (fails fast on a corrupt kit)"
bash "$REPO/scripts/instance-bootstrap.sh" --selftest || die "carried kit failed selftest"

log "2/6 bring-up: prebuilt tools + gate venv (idempotent)"
bash "$REPO/scripts/instance-bootstrap.sh" --torch || die "bring-up failed"

log "3/6 environment probe -> gate flags"
FLAGS=""
if [ "$(nvidia-smi -L | wc -l)" != "1" ]; then
  FLAGS="$FLAGS --allow-shared-host"
  log "multi-GPU host -> --allow-shared-host (CONDITIONAL by contract)"
fi
if ! dmesg >/dev/null 2>&1; then
  FLAGS="$FLAGS --allow-no-dmesg"
  log "dmesg unreadable -> --allow-no-dmesg (Xid channel unverified)"
fi
POWER=${POWER_W:-}
if [ -z "$POWER" ]; then
  if nvidia-smi -i "$GPU" -pl 180 >/dev/null 2>&1 \
      || sudo -n nvidia-smi -i "$GPU" -pl 180 >/dev/null 2>&1; then
    POWER=180
    log "power cap set to 180 W (club policy)"
  else
    POWER=$(nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits -i "$GPU" | head -1 | cut -d. -f1)
    log "power cap NOT settable (container?) -> provider preset ${POWER} W"
  fi
fi
if [ -x "$REPO/venv-gate/bin/python" ]; then
  export TG_MATMUL_PYTHON="$REPO/venv-gate/bin/python"
  log "matmul python: $TG_MATMUL_PYTHON"
else
  log "WARN: no venv-gate - matmul will record SKIPPED (CONDITIONAL)"
fi

log "4/6 gate: profile=$PROFILE tag=$TAG power=${POWER}W$FLAGS"
python3 "$REPO/scripts/gate.py" \
  --profile "$PROFILE" --gpu "$GPU" --power-w "$POWER" \
  --evidence-dir "$EVID" ${MEMTEST_MIN:+--memtest-min "$MEMTEST_MIN"} \
  ${BURN_MIN:+--burn-min "$BURN_MIN"} \
  ${IDLE_MIN:+--idle-min "$IDLE_MIN"} \
  $FLAGS 2>&1 | tee "$REPO/gate-$TAG.log"
GATE_RC=${PIPESTATUS[0]}

log "5/6 machine report"
python3 "$REPO/scripts/make-report.py" "$EVID" || die "make-report failed"

log "6/6 human summary"
python3 "$REPO/scripts/session-summary.py" "$EVID" \
  ${EXTRA_RECEIPTS:+--extra $EXTRA_RECEIPTS} || die "summary failed"

log "DONE: $EVID/SUMMARY.md (gate rc=$GATE_RC)"
exit "$GATE_RC"
