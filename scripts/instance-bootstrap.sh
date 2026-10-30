#!/usr/bin/env bash
# instance-bootstrap.sh - GitHub-free bring-up of the gate stack on a rented
# container instance (SPEC-01 s8). Every input either ships in this repo
# (kit/upstream/, checksummed in kit/upstream/SHA256SUMS) or comes from the
# distribution package mirror / pytorch.org. There are NO git clones and NO
# github.com requests - the rented-instance session of 2026-10-30 measured
# mid-stream clone deaths on exactly that path.
#
# What it builds (idempotent; safe to re-run after an interruption):
#   vendor/club-170hx/              materialized from kit/upstream/club-170hx
#   bin/compare-vram                nvcc -arch=sm_80 from the carried .cu
#   bin/gpu-burn/gpu-burn           from kit/upstream/gpu-burn-0d19c94.tar.gz
#   vendor/club-170hx/scripts/qc/memtest_vulkan/memtest_vulkan
#                                   cargo build --release from
#                                   kit/upstream/memtest_vulkan-cdd418a.tar.gz
#   then runs scripts/setup-container.sh and prints the gate command line.
#
# Usage: scripts/instance-bootstrap.sh [--selftest] [--gpu N] [--no-apt]
#                                     [--torch] [--torch-venv DIR] [--force-build]
#   --force-build   rebuild tools from kit/upstream sources instead of
#                   installing the kit/prebuilt binaries (default)
#   --selftest      verify the carried kit only; works on a GPU-less host:
#                   SHA256SUMS, tarball integrity, carried script syntax.
#   --no-apt        skip the apt stage (build deps already installed)
#   --torch         also build the gate venv: torch 2.13.0+cu130 (PINS.md;
#                   REF_BAND is calibrated on this exact version)
# Exit codes: 0 = ok, 1 = failure (see log), 2 = usage.

set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
UP="$REPO/kit/upstream"
LOG_PREFIX="[bootstrap $(date +%H:%M:%S)]"
log() { echo "$LOG_PREFIX $*"; }
die() { echo "$LOG_PREFIX FAIL: $*" >&2; exit 1; }

SELFTEST=0 NO_APT=0 TORCH=0 GPU=0 FORCE_BUILD=0 TORCH_VENV="$REPO/venv-gate"
while [ $# -gt 0 ]; do
  case "$1" in
    --selftest) SELFTEST=1; shift ;;
    --no-apt) NO_APT=1; shift ;;
    --torch) TORCH=1; shift ;;
    --force-build) FORCE_BUILD=1; shift ;;
    --gpu) GPU=$2; shift 2 ;;
    --torch-venv) TORCH_VENV=$2; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---- stage 0: carried-kit selftest (no GPU, no apt, no network) -----------
selftest() {
  cd "$UP" || die "kit/upstream missing"
  sha256sum -c SHA256SUMS --quiet || die "carried kit checksum mismatch"
  for t in gpu-burn-0d19c94.tar.gz memtest_vulkan-cdd418a.tar.gz; do
    tar tzf "$t" >/dev/null 2>&1 || die "tarball corrupt: $t"
  done
  bash -n club-170hx/scripts/qc/pcie-check.sh || die "pcie-check.sh syntax"
  python3 -m py_compile club-170hx/scripts/qc/memtest-select.py     club-170hx/recipes/qwen3.8-27b-dflash2/prepare_pinned_models.py     club-170hx/recipes/qwen3.8-27b-dflash2/live_benchmark.py     club-170hx/recipes/qwen3.8-27b-dflash2/live_support.py     || die "carried python fails py_compile"
  for f in scripts/gate.py scripts/setup-container.sh scripts/telemetry.py \
           scripts/xid-watch.py; do
    [ -f "$REPO/$f" ] || die "repo script missing: $f"
  done
  if [ -f "$UP/../prebuilt/SHA256SUMS" ]; then
    (cd "$UP/../prebuilt" && sha256sum -c SHA256SUMS --quiet) \
      || die "kit/prebuilt checksum mismatch"
    local listed found
    listed=$(wc -l < "$UP/../prebuilt/SHA256SUMS")
    found=$(find "$UP/../prebuilt" -type f ! -name SHA256SUMS | wc -l)
    [ "$listed" = "$found" ] \
      || die "kit/prebuilt drift: $listed checksummed vs $found present - regenerate SHA256SUMS"
  fi
  log "selftest OK: checksums, tarballs, carried scripts, prebuilt"
}

# ---- stage 1: vendor/ materialization --------------------------------------
materialize_vendor() {
  [ -d "$UP/club-170hx" ] || die "kit/upstream/club-170hx missing"
  mkdir -p "$REPO/vendor"
  if [ ! -d "$REPO/vendor/club-170hx/.git" ]; then
    rm -rf "$REPO/vendor/club-170hx"
    cp -a "$UP/club-170hx" "$REPO/vendor/club-170hx"
    log "vendor/club-170hx materialized from kit/upstream (no .git: run-time copy)"
  else
    log "vendor/club-170hx: real submodule checkout present - leaving it alone"
  fi
}

# ---- stage 2: apt deps (distribution mirror only) --------------------------
apt_deps() {
  log "apt: build deps"
  apt-get update -qq >/dev/null 2>&1 || log "apt update failed (offline?)"
  local pkgs="build-essential cargo libvulkan-dev libvulkan1 glslang-tools pciutils"
  # cublas headers: match the installed CUDA toolkit major-minor (e.g.
  # /usr/local/cuda-13.3 -> libcublas-dev-13-3), fall back to the unversioned
  # package when the versioned one is absent from the mirror.
  local cuda_dir ver suffix
  cuda_dir=$(ls -d /usr/local/cuda-* 2>/dev/null | sort -V | tail -1)
  if [ -n "${cuda_dir:-}" ] && [ -x "${cuda_dir}/bin/nvcc" ]; then
    ver=${cuda_dir##*/cuda-}
    suffix=${ver//./-}
    if apt-get install -y -qq "libcublas-dev-$suffix" >/dev/null 2>&1; then
      log "apt: libcublas-dev-$suffix installed"
    else
      log "apt: libcublas-dev-$suffix unavailable - trying unversioned"
    fi
  fi
  apt-get install -y -qq $pkgs >/dev/null 2>&1 || log "apt: some deps failed - builds will tell"
}

# ---- stage 3a: prebuilt install (default: no toolchain needed) -------------
install_prebuilt() {
  local PB="$REPO/kit/prebuilt"
  [ -f "$PB/SHA256SUMS" ] || { log "prebuilt: no kit/prebuilt - source build"; return 1; }
  (cd "$PB" && sha256sum -c SHA256SUMS --quiet) \
    || { log "prebuilt: checksum mismatch - source build"; return 1; }
  local f
  for f in compare-vram gpu-burn/gpu-burn gpu-burn/compare.fatbin memtest_vulkan; do
    [ -f "$PB/$f" ] || { log "prebuilt: missing $f - source build"; return 1; }
  done
  mkdir -p "$REPO/bin/gpu-burn" "$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan"
  cp "$PB/compare-vram" "$REPO/bin/" \
    && cp "$PB/gpu-burn/gpu-burn" "$PB/gpu-burn/compare.fatbin" "$REPO/bin/gpu-burn/" \
    && cp "$PB/memtest_vulkan" "$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan/" \
    || return 1
  chmod +x "$REPO/bin/compare-vram" "$REPO/bin/gpu-burn/gpu-burn" \
    "$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan/memtest_vulkan"
  local b miss=0
  for b in "$REPO/bin/compare-vram" "$REPO/bin/gpu-burn/gpu-burn" \
           "$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan/memtest_vulkan"; do
    # libcuda is EXCLUDED from the failure set: inside a container image
    # (docker build) the driver is absent by design - the nvidia runtime
    # injects it at `docker run --gpus` time.
    if ldd "$b" 2>/dev/null | grep "not found" | grep -qv libcuda; then
      log "prebuilt $(basename "$b"): unresolved libs:"
      ldd "$b" 2>/dev/null | grep "not found" | grep -v libcuda | sed 's/^/    /'
      miss=1
    elif ldd "$b" 2>/dev/null | grep -q "libcuda.*not found"; then
      log "prebuilt $(basename "$b"): libcuda unresolved here - expected in"
      log "  container images (nvidia runtime injects it at run time)"
    fi
  done
  # dlopen'd deps are invisible to ldd: memtest_vulkan dlopens the vulkan
  # loader. Hosts without one (hiveOS seller rigs) get the carried loader
  # (kit/prebuilt/lib, Apache-2.0) via the wrapper template; the carried
  # ICD applies only when the system has no ICD of its own.
  local MT_DST="$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan/memtest_vulkan"
  if ! ldconfig -p 2>/dev/null | grep -q libvulkan.so.1 \
      && [ -f "$PB/lib/libvulkan.so.1" ] \
      && [ -f "$PB/lib/memtest-vulkan-wrapper.sh" ]; then
    mv "$MT_DST" "$MT_DST.bin"
    sed "s|@KIT_LIB@|$PB/lib|g" "$PB/lib/memtest-vulkan-wrapper.sh" > "$MT_DST"
    chmod +x "$MT_DST"
    log "no system vulkan loader - wrapper + carried loader installed"
  elif ! ldconfig -p 2>/dev/null | grep -q libvulkan.so.1; then
    log "WARN: no system libvulkan.so.1 and none carried - memtest_vulkan will fail at runtime"
  fi
  [ "$miss" = 0 ] \
    && log "prebuilt tools installed (checksums verified, ldd clean)" \
    || die "prebuilt libs unresolved - install the CUDA runtime/libvulkan1, or rerun with --force-build"
}

# ---- stage 3b: source builds (fallback, --force-build) ---------------------
build_tools() {
  local cuda_bin="${CUDA_HOME:-/usr/local/cuda}/bin"
  command -v nvcc >/dev/null 2>&1 || [ -x "$cuda_bin/nvcc" ] \
    || die "nvcc not found - use a CUDA devel image or install the toolkit"
  [ -f "${CUDA_HOME:-/usr/local/cuda}/include/cublas_v2.h" ] \
    || die "cublas_v2.h missing - apt stage did not land the dev package"

  mkdir -p "$REPO/bin"
  log "build compare-vram (sm_80)"
  "$cuda_bin/nvcc" -O3 -arch=sm_80 \
    "$REPO/vendor/club-170hx/scripts/qc/compare_vram.cu" \
    -o "$REPO/bin/compare-vram" || die "compare-vram build"

  log "build gpu-burn (0d19c94)"
  rm -rf /tmp/rb-gpu-burn
  mkdir -p /tmp/rb-gpu-burn
  tar xzf "$UP/gpu-burn-0d19c94.tar.gz" -C /tmp/rb-gpu-burn --strip-components=1
  (cd /tmp/rb-gpu-burn && make COMPUTE=80 \
      CUDA_PATH="${CUDA_HOME:-/usr/local/cuda}" >/tmp/rb-gpu-burn.log 2>&1) \
    || { tail -5 /tmp/rb-gpu-burn.log; die "gpu-burn build"; }
  # gate.py expects bin/gpu-burn/gpu-burn (dash); upstream builds gpu_burn.
  mkdir -p "$REPO/bin/gpu-burn"
  cp /tmp/rb-gpu-burn/gpu_burn "$REPO/bin/gpu-burn/gpu-burn" || die "gpu_burn artifact missing"
  cp /tmp/rb-gpu-burn/compare.fatbin "$REPO/bin/gpu-burn/" || die "compare.fatbin missing"
  log "gpu-burn installed (gpu_burn -> bin/gpu-burn/gpu-burn)"

  log "build memtest_vulkan (v0.5.0, cargo)"
  rm -rf /tmp/rb-memtest
  mkdir -p /tmp/rb-memtest
  tar xzf "$UP/memtest_vulkan-cdd418a.tar.gz" -C /tmp/rb-memtest --strip-components=1
  # Rust project (not cmake): cargo build --release makes
  # target/release/memtest_vulkan; deps come from crates.io (a package
  # mirror, not github). Retry: crates.io fetches can drop mid-stream on
  # flaky instance uplinks; cargo caches, so retries converge.
  local mt_rc=1
  for i in 1 2 3; do
    (cd /tmp/rb-memtest && cargo build --release >/tmp/rb-memtest.log 2>&1) \
      && { mt_rc=0; break; }
    log "memtest_vulkan cargo attempt $i failed - retrying"
    tail -3 /tmp/rb-memtest.log || true
  done
  [ "$mt_rc" = 0 ] \
    || { tail -8 /tmp/rb-memtest.log; die "memtest_vulkan build"; }
  # memtest-select.py resolves MEMTEST_BIN relative to itself - the binary
  # must sit inside the (materialized) club tree at exactly this path.
  mkdir -p "$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan"
  cp /tmp/rb-memtest/target/release/memtest_vulkan \
    "$REPO/vendor/club-170hx/scripts/qc/memtest_vulkan/" \
    || die "memtest_vulkan artifact"
  log "memtest_vulkan placed at club qc path"
}

# ---- stage 4: gate venv (torch pin of record) -------------------------------
build_torch_venv() {
  local py
  py=$(command -v python3.12 || command -v python3) || die "no python3"
  [ -x "$TORCH_VENV/bin/python" ] || "$py" -m venv "$TORCH_VENV" || die "venv create"
  if "$TORCH_VENV/bin/python" -c 'import torch' >/dev/null 2>&1; then
    log "torch already importable in $TORCH_VENV"
  else
    log "pip: torch==2.13.0+cu130 + numpy (download.pytorch.org/PyPI; retries on)"
    "$TORCH_VENV/bin/pip" install --retries 10 --timeout 90 \
      --index-url https://download.pytorch.org/whl/cu130 \
      --extra-index-url https://pypi.org/simple \
      'torch==2.13.0+cu130' numpy || die "torch install"
  fi
  "$TORCH_VENV/bin/python" -c 'import torch; print("torch", torch.__version__)'
  log "export TG_MATMUL_PYTHON=$TORCH_VENV/bin/python"
}

# ---- main -------------------------------------------------------------------
if [ "$SELFTEST" = 1 ]; then
  selftest
  exit 0
fi

log "start: repo=$REPO"
materialize_vendor
if [ "$FORCE_BUILD" = 1 ] || ! install_prebuilt; then
  [ "$NO_APT" = 0 ] && apt_deps
  build_tools
fi
if [ "$TORCH" = 1 ]; then build_torch_venv; fi

if command -v nvidia-smi >/dev/null 2>&1; then
  log "setup-container.sh verdict:"
  bash "$REPO/scripts/setup-container.sh" --gpu "$GPU" 2>&1 | sed 's/^/  /'
  log "gate command line:"
  echo "  cd $REPO && export TG_MATMUL_PYTHON=$TORCH_VENV/bin/python"
  echo "  python3 scripts/gate.py --profile express --memtest-min 15 --burn-min 5"
  echo "      --gpu $GPU --power-w $(nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits -i $GPU | cut -d. -f1)"
  echo "      --allow-no-dmesg [--allow-shared-host if setup-container says >1 GPU]"
else
  log "no nvidia-smi - GPU stages skipped (selftest/builds only)"
fi
log "disk: $(df -h / | tail -1 | awk '{print $4" free"}')"
log "BOOTSTRAP DONE"
