#!/usr/bin/env bash
# install-model.sh - OFFLINE stand-side installer/verifier for one
# INFORMATIONAL serving recipe. Builds a dedicated venv-<key> so the
# recipe engine can never drift the acceptance stack (venv-inference).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
RECIPE=""
KIT_ROOT="$HOME/kit"
CHECK_ONLY=0
VENV=""
usage() {
  cat <<'EOF'
install-model.sh - offline installer for one informational serving recipe
Usage: install-model.sh --recipe <recipes/xxx.json> [--kit-root DIR] [--venv DIR] [--check]
  --recipe    path to the recipe json (relative to kit/inference or absolute)
  --kit-root  kit location on the stand (default: ~/kit)
  --venv      venv to build/use (default: <kit-root>/venv-<key>; python3.12)
  --check     verify only (manifest lines for this key + engine version
              when the venv exists); installs nothing
EOF
}
while [ $# -gt 0 ]; do
  case "$1" in
    --recipe) RECIPE=$2; shift 2 ;;
    --kit-root) KIT_ROOT=$2; shift 2 ;;
    --venv) VENV=$2; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[ -n "$RECIPE" ] || { usage >&2; exit 2; }
case "$RECIPE" in /*) ;; *) RECIPE="$REPO/kit/inference/$RECIPE" ;; esac
[ -f "$RECIPE" ] || { echo "install-model: recipe not found: $RECIPE" >&2; exit 2; }
log() { echo "install-model: $*"; }
die() { echo "install-model: FAIL: $*" >&2; exit 1; }

read -r KEY ENGINE_VER <<EOF
$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["key"], r["engine"]["version"])' "$RECIPE")
EOF
[ -n "$KEY" ] || die "cannot parse recipe key"
WHEELS="$KIT_ROOT/wheelhouse-$KEY"
MDIR="$KIT_ROOT/model/$KEY"
[ -z "$VENV" ] && VENV="$KIT_ROOT/venv-$KEY"

# --- 1. kit integrity for this key -------------------------------------------
if [ -f "$KIT_ROOT/manifest-$KEY.sha256" ]; then
  log "verifying per-key manifest-$KEY.sha256"
  ( cd "$KIT_ROOT" && sha256sum -c "manifest-$KEY.sha256" --quiet ) \
    || die "kit corrupt for $KEY (sha mismatch) - use the spare stick / re-fetch"
  log "manifest OK for $KEY"
elif [ -f "$KIT_ROOT/manifest-inference.sha256" ]; then
  log "no per-key manifest; verifying shared manifest lines for $KEY"
  LINES=$(grep -E "(wheelhouse-$KEY|model/$KEY)/" "$KIT_ROOT/manifest-inference.sha256" || true)
  [ -n "$LINES" ] \
    || die "shared manifest has no lines for $KEY - regenerate it (fetch-model.sh --help)"
  printf '%s\n' "$LINES" \
    | ( cd "$KIT_ROOT" && sha256sum -c --quiet ) \
    || die "kit corrupt for $KEY (sha mismatch) - use the spare stick / re-fetch"
  log "manifest OK for $KEY (shared)"
else
  die "no manifest-$KEY.sha256 or manifest-inference.sha256 in $KIT_ROOT - run the fetch side first"
fi
[ -f "$MDIR/REVISION.txt" ] || die "missing $MDIR/REVISION.txt - re-run fetch-model.sh"

# --- 2. venv + offline install ------------------------------------------------
verify_engine() {
  "$VENV/bin/python" - "$ENGINE_VER" <<'PYEOF'
import importlib.metadata, sys
spec = sys.argv[1]          # "0.28.0" (exact) or ">=0.28.0" (minimum)
got = importlib.metadata.version("vllm")
want = spec.lstrip(">=")
ok = (got == want) if not spec.startswith(">=") else (got >= want)
if not ok:
    print(f"install-model: FAIL: vllm {got} does not satisfy {spec}", file=sys.stderr)
    sys.exit(1)
print(f"install-model: engine OK (vllm {got}, spec {spec})")
PYEOF
}

if [ "$CHECK_ONLY" = "1" ]; then
  if [ -x "$VENV/bin/python" ]; then verify_engine || die "engine mismatch"; fi
  log "CHECK OK for $KEY"
  exit 0
fi

command -v python3.12 >/dev/null 2>&1 || die "python3.12 missing (cp312 wheels)"
if [ ! -x "$VENV/bin/pip" ]; then
  log "creating $VENV (python3.12)"
  python3.12 -m venv "$VENV" || die "venv creation failed"
  "$VENV/bin/pip" install --no-index --find-links "$WHEELS" --upgrade pip wheel \
    || die "offline pip bootstrap failed"
fi
log "installing vllm$ENGINE_VER offline into $VENV"
"$VENV/bin/pip" install --no-index --find-links "$WHEELS" "vllm$ENGINE_VER" \
  || die "offline install failed (wheelhouse incomplete for $KEY?)"
verify_engine || die "installed vllm does not satisfy the recipe spec"
log "install complete for $KEY. Next: kit/inference/smoke-model.py --recipe ..."

