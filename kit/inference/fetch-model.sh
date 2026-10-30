#!/usr/bin/env bash
# fetch-model.sh - AT-HOME (egress) builder for one INFORMATIONAL serving
# recipe (kit/inference/recipes/<model>.json). Downloads the model
# snapshot and the engine wheels into the kit staging root; the stand
# never needs egress. Companion to fetch.sh (the acceptance recipe).
#
# Pinning policy: recipes carry model.revision = null until a first good
# run; this script records the RESOLVED revision (REVISION.txt) and the
# manifest locks the bytes. Set the hash in the recipe afterwards.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
KIT_ROOT="$HOME/kit-build"
RECIPE=""
usage() {
  cat <<'EOF'
fetch-model.sh - at-home builder for one informational serving recipe
Usage: fetch-model.sh --recipe <recipes/xxx.json> [--kit-root DIR]
  --recipe    path to the recipe json (relative to kit/inference or absolute)
  --kit-root  staging dir (default: ~/kit-build; same root fetch.sh uses)

Downloads (pinned by the recipe): the model snapshot via huggingface_hub
(revision pinned at fetch time -> models/<key>/REVISION.txt) and the
engine wheel set (wheelhouse-<key>/) for the recipe vLLM version spec,
then writes manifest-<key>.sha256 (verified by install-model.sh).
To fold everything into one shared manifest instead:
  (cd <kit-root> && find wheelhouse* model -type f \
     ! -path '*/.git/*' ! -name .complete -print0 \
     | LC_ALL=C sort -z | xargs -0 sha256sum > manifest-inference.sha256)
Safe to re-run: completed steps are skipped.
EOF
}
while [ $# -gt 0 ]; do
  case "$1" in
    --recipe) RECIPE=$2; shift 2 ;;
    --kit-root) KIT_ROOT=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[ -n "$RECIPE" ] || { usage >&2; exit 2; }
case "$RECIPE" in /*) ;; *) RECIPE="$REPO/kit/inference/$RECIPE" ;; esac
[ -f "$RECIPE" ] || { echo "fetch-model: recipe not found: $RECIPE" >&2; exit 2; }
KIT_ROOT=$(mkdir -p "$KIT_ROOT" && cd "$KIT_ROOT" && pwd)
log() { echo "fetch-model: $*"; }
die() { echo "fetch-model: FAIL: $*" >&2; exit 1; }

read -r KEY MREPO MREV ENGINE_VER <<EOF
$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["key"], r["model"]["repo"], r["model"].get("revision") or "", r["engine"]["version"])' "$RECIPE")
EOF
[ -n "$KEY" ] && [ -n "$MREPO" ] && [ -n "$ENGINE_VER" ] \
  || die "cannot parse recipe (key/model.repo/engine.version)"
WHEELS="$KIT_ROOT/wheelhouse-$KEY"
MDIR="$KIT_ROOT/model/$KEY"

command -v python3.12 >/dev/null 2>&1 || die "python3.12 not found (cp312 wheels)"

# --- 1. engine wheels ---------------------------------------------------------
if [ -f "$WHEELS/.complete" ]; then
  log "wheels: $WHEELS already complete"
else
  log "wheels: downloading vllm $ENGINE_VER (+deps, python3.12)"
  mkdir -p "$WHEELS"
  python3.12 -m pip download --only-binary :all: \
    -d "$WHEELS" pip wheel setuptools "vllm$ENGINE_VER" \
    || die "pip download failed for vllm$ENGINE_VER"
  touch "$WHEELS/.complete"
fi

# --- 2. model snapshot --------------------------------------------------------
if [ -f "$MDIR/.complete" ]; then
  log "model: $MDIR already complete"
else
  log "model: downloading $MREPO -> $MDIR"
  FETCH_VENV="$KIT_ROOT/.fetch-venv"
  if [ ! -x "$FETCH_VENV/bin/python" ]; then
    python3.12 -m venv "$FETCH_VENV" || die "cannot create the fetch venv"
    "$FETCH_VENV/bin/pip" install --upgrade pip wheel \
      || die "cannot bootstrap the fetch venv"
    "$FETCH_VENV/bin/pip" install huggingface_hub hf_transfer \
      || die "cannot install huggingface_hub"
  fi
  export HF_HOME="$KIT_ROOT/.hf-cache" HF_HUB_ENABLE_HF_TRANSFER=1
  "$FETCH_VENV/bin/python" - "$MREPO" "$MREV" "$MDIR" <<'PYEOF'
import sys
from huggingface_hub import model_info, snapshot_download
repo, rev, mdir = sys.argv[1], sys.argv[2], sys.argv[3]
kw = {"revision": rev} if rev else {}
info = model_info(repo, **kw)
snapshot_download(repo, local_dir=mdir, **kw)
open(mdir + "/REVISION.txt", "w").write(
    "repo=%s\nrequested_revision=%s\nresolved_sha=%s\n" % (repo, rev or "default", info.sha))
print("resolved revision:", info.sha)
PYEOF
  touch "$MDIR/.complete"
fi

# --- 3. per-key manifest --------------------------------------------------------
log "manifest: writing manifest-$KEY.sha256"
( cd "$KIT_ROOT" \
  && find "wheelhouse-$KEY" "model/$KEY" -type f ! -name .complete \
       -print0 | LC_ALL=C sort -z | xargs -0 sha256sum \
  > "manifest-$KEY.sha256.tmp" \
  && mv "manifest-$KEY.sha256.tmp" "manifest-$KEY.sha256" ) \
  || die "manifest generation failed"
log "done for $KEY: model + wheels + manifest-$KEY.sha256."
log "Next on the stand: install-model.sh --recipe, then smoke-model.py --recipe."

