#!/usr/bin/env bash
# setup-container.sh - preflight for running the gate INSIDE a rented
# container (driver on the host, CUDA via the nvidia-container toolkit).
#
# A container usually cannot install the unlocker (a host driver
# operation) and cannot perform a cold boot (T9). What it CAN check,
# this checks: GPU visibility, the driver string against the pinned
# whitelist, the unlocked memory band, dmesg readability, and host GPU
# count - the last three map onto gate.py --allow-no-dmesg and
# --allow-shared-host flags.
#
# Usage: scripts/setup-container.sh [--gpu N]
# Exit codes: 0 = container is gate-ready, 1 = not ready, 2 = usage.

set -euo pipefail

usage() {
  cat <<'EOF'
setup-container.sh - container preflight for the CMP 170HX gate
Usage: setup-container.sh [--gpu N]
Checks: nvidia-smi visibility, driver whitelist (PINS.md), unlocked
memory band, dmesg readability, host GPU count. Prints the gate flags
the container needs; exits 1 when the container is not gate-ready.
Full rented-instance requirements, SSD budget, and readiness review:
SPEC-01 s8.
EOF
}

GPU=0
while [ $# -gt 0 ]; do
  case "$1" in
    --gpu) GPU=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "setup-container: FAIL: $*" >&2; exit 1; }

command -v nvidia-smi >/dev/null 2>&1 \
  || fail "nvidia-smi not found - was the container started with --gpus / NVIDIA_VISIBLE_DEVICES?"
nvidia-smi -i "$GPU" >/dev/null 2>&1 || fail "GPU $GPU not visible via nvidia-smi"
command -v python3 >/dev/null 2>&1 \
  || fail "python3 not found - required to read pins/drivers.json (driver whitelist)"

DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader -i "$GPU" | head -1)
# The whitelist's machine source is pins/drivers.json (canonical pin
# list: PINS.md "Driver stack") - single source, no hand-copied case
# pattern to drift; tests/test_thresholds.py enforces the sync.
REPO=$(cd "$(dirname "$0")/.." && pwd)
WHITELIST=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["supported_drivers"]))' "$REPO/pins/drivers.json")
case " $WHITELIST " in
  *" $DRV "*)
    echo "driver $DRV: on the pinned whitelist (PINS.md)" ;;
  *)
    echo "NOTE: driver $DRV is NOT on the pinned unlocker whitelist (PINS.md) -"
    echo "      an already-unlocked provider image is fine; a locked one cannot"
    echo "      be unlocked from inside the container" ;;
esac

MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i "$GPU" | head -1)
if [ "$MEM" -ge 60000 ]; then
  echo "memory.total=${MEM} MiB -> 8gb(64GiB) unlocked band"
elif [ "$MEM" -ge 35000 ]; then
  echo "memory.total=${MEM} MiB -> 10gb(40GiB) unlocked band"
elif [ "$MEM" -ge 30000 ]; then
  echo "memory.total=${MEM} MiB -> ~32 GiB: single-rank suspect (gate intake will die on it)"
else
  fail "memory.total=${MEM} MiB looks locked/stock - the provider's unlock did not happen, and a container cannot install it"
fi

if dmesg >/dev/null 2>&1; then
  echo "dmesg readable: full gate (Xid channel active)"
else
  echo "dmesg NOT readable: run gate.py with --allow-no-dmesg (Xid channel marked unverified)"
fi

NGPUS=$(nvidia-smi -L | wc -l)
if [ "$NGPUS" -eq 1 ]; then
  echo "host GPUs: 1"
else
  echo "note: $NGPUS GPUs visible - use gate.py --gpu N --allow-shared-host"
fi

echo "setup-container: ready - T9 cold boot is impossible in a container"
echo "(record as stand limitation); T1 = verify the provider's unlock"
echo "(SPEC-01 s4.4 bands); carry the kit wheelhouse (torch 2.13.0+cu130)"
echo "into a venv for matmul/inference."

