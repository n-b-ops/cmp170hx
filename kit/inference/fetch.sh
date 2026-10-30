#!/usr/bin/env bash
# fetch.sh - AT-HOME (egress) builder for the Tier-B T8 inference kit
# (kit/inference/README.md). Everything it downloads lands under one
# staging dir; the stand never needs egress. On the stand, install.sh
# verifies the manifest and installs offline; smoke.py runs the gate.
#
# Pins live in kit/inference/recipe.json - copied from the club recipe
# of record (vendor/club-170hx recipes/qwen3.8-27b-dflash2). The model
# step delegates to the club's own prepare_pinned_models.py so the kit
# can never diverge from the published acquisition path.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
RECIPE="$REPO/kit/inference/recipe.json"
LOCK="$REPO/kit/inference/requirements.lock"
# Runtime: carried tarball, content-pinned (2026-10-30). The historical
# git pin syv-ai/qwen38-27b-rtx3090@69ba4d06 died in an upstream history
# rewrite (repo renamed to HyperQwen, GitHub API 422) - cloning is no
# longer possible or needed. Pin selection matters: the runtime tree's
# patches/ must match the locked vLLM - 7105f2e is the newest HyperQwen
# commit with docker/requirements.txt pinning vllm==0.27.1 (the club
# recipe of record's lock); the newer HEAD (0.32-era patches) fails
# install.sh's patch step by design [measured 2026-10-30].
RUNTIME_TREE_SHA256="a4e8881914cf03dd78a49ea2281edbca08f891175f4d9e147630870c67d196dc"
RUNTIME_TARBALL="$REPO/kit/upstream/qwen38-runtime-7105f2e.tar.gz"
PREPARE="$REPO/vendor/club-170hx/recipes/qwen3.8-27b-dflash2/prepare_pinned_models.py"

usage() {
  cat <<'EOF'
fetch.sh - at-home kit builder for the T8 inference stack (roadmap s9.1)
Usage: fetch.sh [--kit-root DIR] [--keep-fetch-venv]
  --kit-root        staging dir (default: ./kit-build); creates
                    wheelhouse-inference/ and model/ inside it - copy both
                    to the USB stick next to the MANIFEST s2 layout
  --keep-fetch-venv keep the throwaway download venv (default: removed)

Needs: network, git, python3.12 (+venv); peak ~40 GB at the staging root,
~25 GB of which lands on the USB stick (MANIFEST item 9's 20-40 GB Tier-B
estimate): the 27B W4A16 weights are ~18 GiB (benchmarks repo, same size
class), overlay shard-7 + extras + the DFlash2 draft add ~5 GiB (shards
1-6 of the fast variant are HARDLINKS - no extra space), and the cp312
wheelhouse (torch cu13 stack + vLLM + flashinfer) is ~5 GB. The
throwaway fetch venv is removed by default; the HF cache stays at
<kit-root>/.hf-cache, outside the manifest.
Pinned downloads: the vLLM stack wheels (requirements.lock + torch
2.13.0), the runtime tree (carried tarball, content-pinned - no GitHub),
and the three pinned model snapshots (base / fast overlay / draft) via
the club prepare_pinned_models.py. Writes manifest-inference.sha256 for
stand-side verification (install.sh refuses a corrupt kit).
Safe to re-run: completed steps are skipped.
EOF
}

KIT_ROOT="$PWD/kit-build"
KEEP_VENV=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kit-root) KIT_ROOT=$2; shift 2 ;;
    --keep-fetch-venv) KEEP_VENV=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done
KIT_ROOT=$(mkdir -p "$KIT_ROOT" && cd "$KIT_ROOT" && pwd)
WHEELS="$KIT_ROOT/wheelhouse-inference"
RUNTIME_DIR="$KIT_ROOT/model/qwen38-27b-rtx3090"
MARKER="$WHEELS/.complete"
log() { echo "fetch: $*"; }
die() { echo "fetch: FAIL: $*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git not found"
command -v python3.12 >/dev/null 2>&1 || die "python3.12 not found (the vLLM 0.27.1 stack is cp312; the stand venv needs it too - kit/MANIFEST.md s5)"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum not found"
[ -f "$RECIPE" ] && [ -f "$LOCK" ] && [ -f "$PREPARE" ] \
  || die "run from a full repo checkout (recipe/lock/club script missing)"

# --- 1. wheels ---------------------------------------------------------------
mkdir -p "$WHEELS"
if [ -f "$MARKER" ]; then
  log "wheels: already complete"
else
  log "wheels: downloading the pinned vLLM stack + torch 2.13.0 (python3.12)"
  python3.12 -m pip download --only-binary :all: \
    -d "$WHEELS" pip wheel setuptools \
    || die "pip download of pip/wheel/setuptools failed"
  python3.12 -m pip download --only-binary :all: \
    -d "$WHEELS" -r "$LOCK" torch==2.13.0 \
    || die "pip download of the locked stack failed (network? PyPI availability?)"
  touch "$MARKER"
fi

# --- 2. runtime tree at the content pin ---------------------------------------
runtime_tree_hash() {
  (cd "$1" && find . -type f ! -path './models/*' ! -path './.git/*' -print0 \
    | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')
}
if [ -d "$RUNTIME_DIR" ] \
    && [ "$(runtime_tree_hash "$RUNTIME_DIR")" = "$RUNTIME_TREE_SHA256" ]; then
  log "runtime: already at the content pin"
else
  log "runtime: extracting the carried tarball (kit/upstream; no GitHub)"
  [ -f "$RUNTIME_TARBALL" ] || die "runtime tarball missing: $RUNTIME_TARBALL"
  rm -rf "$RUNTIME_DIR"
  mkdir -p "$(dirname "$RUNTIME_DIR")" "$RUNTIME_DIR"
  tar xzf "$RUNTIME_TARBALL" -C "$RUNTIME_DIR" --strip-components=1 \
    || die "runtime tarball extract failed"
  got=$(runtime_tree_hash "$RUNTIME_DIR")
  [ "$got" = "$RUNTIME_TREE_SHA256" ] \
    || die "runtime tree hash mismatch: got $got - re-vendor the tarball"
fi

# --- 3. pinned model snapshots (the club acquisition path) --------------------
# The runtime's docker/prepare.sh hardcodes its container layout (cd /app,
# PATH=/app/venv/bin): outside their compose rig, provide both as symlinks
# (verified 2026-10-30: fetch failed with CalledProcessError until /app
# existed; bare-metal stands are root, containers running this script are).
if [ ! -e /app ]; then
  ln -s "$RUNTIME_DIR" /app 2>/dev/null \
    || die "cannot symlink /app -> $RUNTIME_DIR (need root): sudo ln -s $RUNTIME_DIR /app"
fi
ln -sfn "${FETCH_VENV:-$KIT_ROOT/.fetch-venv}" "$RUNTIME_DIR/venv"
if [ -f "$RUNTIME_DIR/models/Qwen3.8-27B-W4A16-AutoRound-fast/model.safetensors.index.json" ] \
    && [ -f "$RUNTIME_DIR/models/Qwen3.8-27B-DFlash2-W4A16/config.json" ]; then
  log "models: prepared tree already present"
else
  log "models: running the club prepare_pinned_models.py (network; ~23 GB of pinned snapshots)"
  FETCH_VENV="$KIT_ROOT/.fetch-venv"
  # Reuse a working fetch venv across re-runs (fetch-model.sh does the
  # same): the runtime requirements install is minutes of wheels, and a
  # killed fetch used to restart it from zero every attempt.
  if [ ! -x "$FETCH_VENV/bin/python" ]; then
    python3.12 -m venv "$FETCH_VENV" || die "cannot create the fetch venv"
    "$FETCH_VENV/bin/pip" install --upgrade pip wheel \
      || die "cannot bootstrap pip in the fetch venv"
  fi
  "$FETCH_VENV/bin/pip" install -r "$RUNTIME_DIR/docker/requirements.txt" \
    || die "cannot install the runtime requirements (the exact set docker/prepare.sh expects)"
  export HF_HOME="$KIT_ROOT/.hf-cache"
  "$FETCH_VENV/bin/python" "$PREPARE" \
    --runtime-root "$RUNTIME_DIR" --model-root "$RUNTIME_DIR/models" \
    || die "prepare_pinned_models.py failed - the kit is INCOMPLETE; fix and re-run"
  if [ "$KEEP_VENV" != "1" ]; then rm -rf "$FETCH_VENV"; fi
fi

# --- 4. manifest ---------------------------------------------------------------
log "manifest: hashing wheelhouse-inference/ + model/"
( cd "$KIT_ROOT" \
  && find wheelhouse-inference model -type f \
       ! -path 'wheelhouse-inference/.complete' \
       ! -path 'model/qwen38-27b-rtx3090/.git/*' \
       -print0 | LC_ALL=C sort -z | xargs -0 sha256sum \
  > manifest-inference.sha256.tmp \
  && mv manifest-inference.sha256.tmp manifest-inference.sha256 ) \
  || die "manifest generation failed"

log "done. Copy to the USB stick: $KIT_ROOT/wheelhouse-inference,"
log "     $KIT_ROOT/model, $KIT_ROOT/manifest-inference.sha256"
log "     (next: install.sh --check on the stand, then smoke.py)"
du -sh "$WHEELS" "$RUNTIME_DIR/models" 2>/dev/null || true

