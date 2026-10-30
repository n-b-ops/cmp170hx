#!/usr/bin/env bash
# install.sh - OFFLINE stand-side installer/verifier for the T8 stack
# (kit/inference/README.md). No egress: wheels and models arrive via the
# USB kit built by fetch.sh. Version mismatches against recipe.json are
# hard failures - an unverified stack must never produce a T8 receipt.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
RECIPE="$REPO/kit/inference/recipe.json"
LOCK="$REPO/kit/inference/requirements.lock"
CHECK_ONLY=0

usage() {
  cat <<'EOF'
install.sh - offline installer/verifier for the T8 inference stack
Usage: install.sh [--kit-root DIR] [--venv DIR] [--check]
  --kit-root  kit location on the stand (default: ~/kit; expects
              wheelhouse-inference/, model/, manifest-inference.sha256)
  --venv      venv to build/use (default: <kit-root>/venv-inference;
              python3.12 required - the pinned wheels are cp312)
  --check     verify only (manifest, lock sha, model tree, installed
              versions when the venv exists); installs nothing

Exit 0 = ready (or verified); 1 = not ready, reason printed.
EOF
}

KIT_ROOT="$HOME/kit"
VENV=""
while [ $# -gt 0 ]; do
  case "$1" in
    --kit-root) KIT_ROOT=$2; shift 2 ;;
    --venv) VENV=$2; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done
if [ -z "$VENV" ]; then VENV="$KIT_ROOT/venv-inference"; fi
WHEELS="$KIT_ROOT/wheelhouse-inference"
RUNTIME_DIR="$KIT_ROOT/model/qwen38-27b-rtx3090"
log() { echo "install: $*"; }
die() { echo "install: FAIL: $*" >&2; exit 1; }

[ -f "$RECIPE" ] && [ -f "$LOCK" ] || die "run from a full repo checkout"
[ -d "$WHEELS" ] || die "no $WHEELS - build the kit at home with fetch.sh (kit/MANIFEST.md item 9)"

# --- 1. kit integrity (spare stick on failure) --------------------------------
if [ -f "$KIT_ROOT/manifest-inference.sha256" ]; then
  log "verifying manifest-inference.sha256 (hashes ~40 GB; go get coffee)"
  ( cd "$KIT_ROOT" && sha256sum -c manifest-inference.sha256 --quiet ) \
    || die "kit corrupt (sha256 mismatch) - use the spare stick"
  log "manifest OK"
else
  die "no manifest-inference.sha256 in $KIT_ROOT - the kit is incomplete"
fi

# --- 2. the carried lock must BE the club lock --------------------------------
WANT_SHA=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["source_of_record"]["requirements_sha256"])' "$RECIPE")
GOT_SHA=$(sha256sum "$LOCK" | cut -d" " -f1)
[ "$GOT_SHA" = "$WANT_SHA" ] \
  || die "requirements.lock sha256 $GOT_SHA != recipe pin $WANT_SHA - regenerate the kit"

# --- 3. model tree presence -----------------------------------------------------
FAST="$RUNTIME_DIR/models/Qwen3.8-27B-W4A16-AutoRound-fast"
DRAFT="$RUNTIME_DIR/models/Qwen3.8-27B-DFlash2-W4A16"
BASE="$RUNTIME_DIR/models/Qwen3.8-27B-W4A16-AutoRound"
for f in "$FAST/model.safetensors.index.json" \
         "$FAST/model_extra_tensors.safetensors" \
         "$FAST/mtp_draft_vocab_ids.pt" \
         "$FAST/config.json" \
         "$DRAFT/config.json"; do
  [ -f "$f" ] || die "missing model artifact $f - re-run fetch.sh at home"
done
N_SHARDS=$(ls "$BASE"/model-0000*-of-00007.safetensors 2>/dev/null | wc -l)
[ "$N_SHARDS" -eq 7 ] || die "base model has $N_SHARDS/7 shards - re-run fetch.sh"
log "model tree OK (base 7 shards, fast overlay, draft)"

# --- 4. venv + offline install --------------------------------------------------
verify_versions() {
  "$VENV/bin/python" - "$RECIPE" <<'PY'
import importlib.metadata, json, sys
recipe = json.load(open(sys.argv[1]))
bad = []
for dist, want in sorted(recipe["verify_versions"].items()):
    try:
        got = importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        bad.append(f"{dist}: missing (want {want})")
        continue
    if got != want:
        bad.append(f"{dist}: {got} (want {want})")
if bad:
    print("install: FAIL: version mismatch: " + "; ".join(bad), file=sys.stderr)
    sys.exit(1)
print("install: versions OK: " + ", ".join(f"{d}=={v}" for d, v in sorted(recipe["verify_versions"].items())))
PY
}

if [ "$CHECK_ONLY" = "1" ]; then
  if [ -x "$VENV/bin/python" ]; then
    verify_versions || die "installed versions diverge from recipe.json"
  else
    log "check-only: no venv at $VENV yet (kit itself verified)"
  fi
  log "CHECK OK"
  exit 0
fi

command -v patch >/dev/null 2>&1 || die "patch(1) missing - needed for the vLLM dflash2 backport (apt install patch)"
command -v python3.12 >/dev/null 2>&1 || die "python3.12 missing (the pinned wheels are cp312; kit/MANIFEST.md s5)"
if [ ! -x "$VENV/bin/pip" ]; then
  log "creating $VENV (python3.12)"
  python3.12 -m venv "$VENV" || die "venv creation failed"
  "$VENV/bin/pip" install --no-index --find-links "$WHEELS" --upgrade pip wheel \
    || die "offline pip bootstrap failed (wheelhouse incomplete?)"
fi
log "installing the locked stack offline (no index)"
"$VENV/bin/pip" install --no-index --find-links "$WHEELS" \
  -r "$LOCK" torch==2.13.0 \
  || die "offline install failed (wheelhouse incomplete? wrong python ABI?)"

# --- 5. vLLM dflash2 backport patches (the onstart-v9 sequence) ------------------
SP=$("$VENV/bin/python" -c 'import vllm, os; print(os.path.dirname(vllm.__file__))')
if grep -q _is_dflash2_draft "$SP/config/vllm.py" 2>/dev/null; then
  log "vLLM patches: already applied"
else
  log "applying the runtime vLLM patches"
  for p in "$RUNTIME_DIR"/patches/*.patch; do
    echo "  == $(basename "$p")"
    patch -p1 -d "$SP" < "$p" || die "patch $p failed"
  done
fi

# --- 6. curand.h for the flashinfer JIT (best effort, as onstart-v9) -------------
mkdir -p /usr/local/cuda/include 2>/dev/null || true
CURAND_H=$(find "$VENV" -name curand.h 2>/dev/null | head -1 || true)
if [ -n "$CURAND_H" ]; then
  ln -sf "$CURAND_H" /usr/local/cuda/include/curand.h 2>/dev/null \
    && log "curand.h linked from the venv" || log "curand.h link failed (JIT may fall back to torch.topk)"
else
  log "WARN: no curand.h in the venv - the fast-topk JIT may fall back to torch.topk (slower)"
fi

# --- 7. version gate --------------------------------------------------------------
verify_versions || die "installed versions diverge from recipe.json after install"
log "install complete. Next: kit/inference/smoke.py (needs the GPU + airflow)"

