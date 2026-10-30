# Offline test-kit manifest

> The seller's stand is assumed to have **no usable internet**. Everything
> below is downloaded at home, checksummed, dry-run, and carried on **two**
> USB sticks. Kit build is the only phase that needs egress; the stand itself
> never does.

## 1. Contents

| #   | Item                                                    | Version / pin                                                                                                                                                                                                                                                                                                                                                                                                                               | Source                                                                                                                                             | Size (approx)   |
| --- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- |
| 1   | Ubuntu Server installer                                 | 22.04.x LTS (kernel ≈6.8)                                                                                                                                                                                                                                                                                                                                                                                                                   | releases.ubuntu.com                                                                                                                                | ~2 GB           |
| 2   | NVIDIA driver .run                                      | **610.43.03** (open branch)                                                                                                                                                                                                                                                                                                                                                                                                                 | [download.nvidia.com/XFree86/Linux-x86_64/610.43.03](https://download.nvidia.com/XFree86/Linux-x86_64/610.43.03/NVIDIA-Linux-x86_64-610.43.03.run) | ~440 MB         |
| 3   | open-gpu-kernel-modules tarballs (unlocker build cache) | `610.43.03.tar.gz` (mandatory), `615.78.08.tar.gz`, `615.71.09.tar.gz`                                                                                                                                                                                                                                                                                                                                                                      | [github.com/NVIDIA/open-gpu-kernel-modules](https://github.com/NVIDIA/open-gpu-kernel-modules) (tag tarballs)                                      | ~150 MB each    |
| 4   | This repo, git bundle with submodules                   | cmp.170.hx @ current HEAD; submodules under `vendor/`: club-170hx `92328c0`, cmpunlocker `bc2e727`, consensus-cmp170hx `257e54f`, ninfer-cmp170hx `7aba8c0` (rumdl-tables branch), cmp-170hx-vllm `69487ee` (rumdl-tables branch - bundle MUST carry local branches, see README; submodule pins canonical in PINS.md), CMP-170hx-64gb-LLM-benchmarks `07d982f` (rumdl-tables branch), Qwen3.8-27B-CMP-170HX `be1172f` (rumdl-tables branch), cmp-170hx-docs `fcb27c0` (upstream; docs-only reference, NO explicit upstream license at pin time — verify before redistributing the bundle beyond your own use) | this repo                                                                                                                                          | ~500 MB         |
| 5   | memtest_vulkan                                          | **v0.5.0** (`cdd418a`) source tarball                                                                                                                                                                                                                                                                                                                                                                                                       | [github.com/GpuZelenograd/memtest_vulkan](https://github.com/GpuZelenograd/memtest_vulkan)                                                         | ~1 MB           |
| 6   | gpu-burn                                                | `0d19c94` source                                                                                                                                                                                                                                                                                                                                                                                                                            | [github.com/wilicc/gpu-burn](https://github.com/wilicc/gpu-burn)                                                                                   | ~50 KB          |
| 7   | CUDA toolkit (nvcc) — offline installer or runfile      | 12.8+ (sm_80 build of compare_vram; wiki pins used 12.8.93/13.x)                                                                                                                                                                                                                                                                                                                                                                            | developer.nvidia.com                                                                                                                               | ~4 GB (runfile) |
| 8   | Python wheelhouse                                       | torch 2.13.0+cu130 (+ deps), numpy (matmul_check import), PyYAML, pytest                                                                                                                                                                                                                                                                                                                                                                    | download.pytorch.org / pypi                                                                                                                        | ~3 GB           |
| 9   | (Tier B) vLLM recipe bundle                             | vLLM 0.27.1 + pinned patch set + model weights per `kit/inference/recipe.json` (built by `kit/inference/fetch.sh`; pins copied from the club recipe of record)                                                                                                                                                                                                                                                                              | club submodule / Hugging Face                                                                                                                      | ~20–40 GB       |
| 10  | Hardware spares                                         | 2×PCIe-8pin→EPS Y adapter, ESD strap, Torx T10/T15, thermal paste (if re-paste agreed)                                                                                                                                                                                                                                                                                                                                                      | —                                                                                                                                                  | —               |
| 11  | Printed runbooks                                        | SPEC-02, SPEC-03, this manifest                                                                                                                                                                                                                                                                                                                                                                                                             | this repo                                                                                                                                          | —               |

Tier B (item 9) is optional per SPEC-02 §T8; skip it for the Express profile.

Item version pins here are convenience copies — [PINS.md](../PINS.md) is
canonical; update both together. Rented-container instances: this
bare-metal kit does not apply as-is — run `scripts/instance-bootstrap.sh`
(materializes `vendor/club-170hx` from `kit/upstream/`, builds
compare-vram/gpu-burn/memtest_vulkan with no GitHub access, optionally
builds the torch venv via `--torch`, then runs `scripts/setup-container.sh`)
and see SPEC-01 §8 (rented instances); the wheelhouse (item 8) is the
piece to carry over (installed into a venv inside the container's CUDA
base, validated per §6 the same way — or bootstrapped on-instance from
download.pytorch.org, which is not a GitHub dependency).

## 2. Layout on the stick

```text
usb/
├── checksums.sha256            # generated at build time (see §3)
├── iso/ubuntu-22.04-live-server-amd64.iso
├── driver/NVIDIA-Linux-x86_64-610.43.03.run
├── ogkm/open-gpu-kernel-modules-610.43.03.tar.gz       # rename: <VERSION>.tar.gz
├── ogkm/open-gpu-kernel-modules-615.78.08.tar.gz
├── ogkm/open-gpu-kernel-modules-615.71.09.tar.gz
├── repo/cmp.170.hx.bundle      # git bundle --all + submodule clones
├── tools/memtest_vulkan-v0.5.0.tar.gz
├── tools/gpu-burn-0d19c94.tar.gz
├── cuda/cuda_12.8.0_*_linux.run
├── wheelhouse/*.whl
└── model/ (Tier B only)
```

The tarball filenames for item 3 must be exactly `<VERSION>.tar.gz` — the
unlocker's build cache (`vendor/cmpunlocker/driver/.build/`) keys on that name.

## 3. Build procedure (at home)

```bash
# after populating usb/ :
cd usb && find . -type f ! -name checksums.sha256 -print0 \
  | xargs -0 sha256sum > checksums.sha256

# record the driver checksum separately — verify it on the stand before use:
sha256sum driver/NVIDIA-Linux-x86_64-610.43.03.run
```

(NVIDIA does not publish a checksum file at that URL; the recorded value is
ours, taken at download time — it still catches stick corruption.)

## 4. Stand-side restore

```bash
cd /media/usb && sha256sum -c checksums.sha256 --quiet || echo 'KIT CORRUPT — use spare stick'
mkdir -p ~/kit && cp -r iso driver ogkm tools cuda wheelhouse repo ~/kit/
# unlocker build cache (offline build):
mkdir -p ~/kit/cmp.170.hx/vendor/cmpunlocker/driver/.build
cp ~/kit/ogkm/*.tar.gz ~/kit/cmp.170.hx/vendor/cmpunlocker/driver/.build/
```

Restore the repo from the carried bundle (`repo/cmp.170.hx.bundle`), or
clone it from `github.com/n-b-ops/cmp170hx` when the stand has network:
since the 2026-10-30 restore the vendor trees are tracked as plain files,
so a plain clone is complete (no submodule fetch; kit/upstream +
kit/prebuilt included).

## 5. Stand-side Python environment (T5/T8)

The system python3 on a fresh stand has no torch. Build the venv from the
wheelhouse — offline, exact steps:

```bash
sudo apt install -y python3-venv
python3 -m venv ~/kit/venv
~/kit/venv/bin/pip install --no-index --find-links ~/kit/wheelhouse torch numpy pyyaml
~/kit/venv/bin/python -c "import torch, numpy, yaml; print(torch.__version__)"
# then: export TG_MATMUL_PYTHON=~/kit/venv/bin/python   (gate.py matmul)
```

## 6. Dry-run validation (at home, mandatory)

On any x86-64 Linux box (a GPU is NOT required for this checklist):

1. `sha256sum -c` passes on both sticks.
2. Repo bundle clones; submodules checkout at the pinned commits.
3. `bash -n` passes over the glob `scripts/*.sh kit/inference/*.sh`
   (glob-driven so new helpers cannot be missed); `shellcheck` clean
   (or deviations recorded).
4. memtest_vulkan and gpu-burn **build** from the carried sources (build
   machine needs CUDA; the artifacts are re-built on the stand anyway to match
   its driver, but a source that fails to build at home will fail there).
5. `gate.py --help` and `make-report.py --help` run and print usage.
6. The §5 venv recipe works **from the wheelhouse alone** (`--no-index`):
   `import torch` succeeds in the fresh venv. Without this, T5 silently
   skips on the stand.
7. (Tier B) `kit/inference/install.sh --check` passes against the staged
   kit (manifest + lock sha + model tree); on any available GPU,
   `kit/inference/smoke.py` serves + one completion — proves the
   wheelhouse is complete.
