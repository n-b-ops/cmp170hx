# PINS - canonical submodule and tool pins

> Single source of truth for every pinned version this project depends on.
> README "Pinned sources", SPEC-02 s0.4, and REFERENCE-NUMBERS carry
> convenience copies or pointers - when any of them disagrees with this
> file, THIS file wins; update both sides in the same commit.

## Submodules (vendor/)

| Repo                                | Pin       | Branch                 | Notes                                                                                                                                                |
| ----------------------------------- | --------- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| club-170hx                          | `6eeae24` | upstream               | QC methodology; compare_vram, memtest-select, pcie-check                                                                                             |
| cmpunlocker                         | `00fdf5c` | upstream               | the unlock tool: driver patch, profiles, verify.sh                                                                                                   |
| consensus-cmp170hx                  | `e1972a7` | upstream               | wiki (numbers, procedures, lessons)                                                                                                                  |
| ninfer-cmp170hx                     | `81b1166` | `rumdl-tables` (local) | context only                                                                                                                                         |
| cmp-170hx-vllm                      | `d176b35` | `rumdl-tables` (local) | context only                                                                                                                                         |
| CMP-170hx-64gb-LLM-benchmarks       | `0cbd9bd` | `rumdl-tables` (local) | context only                                                                                                                                         |
| Qwen3.8-27B-CMP-170HX (qwen-recipe) | `7a6f83d` | `rumdl-tables` (local) | REFERENCE-NUMBERS sE anchor receipts                                                                                                                 |
| cmp-170hx-docs                      | `fcb27c0` | upstream               | It-Still-Boots lab docs: the 16-model performance sweep (Nemotron W4A16 + Qwen3.6 FP8 anchors); docs-only reference, no explicit license at pin time |

Pin provenance (2026-10-30 restore): pre-loss pins club `92328c0` /
cmpunlocker `bc2e727` / consensus `257e54f` / ninfer `7aba8c0` / vllm
`69487ee` / benchmarks `07d982f` / qwen `be1172f` no longer exist
upstream (history rewrites). Replacements are content-anchored: club
verified byte-exact via the recipe `requirements_sha256` and lock sha256;
cmpunlocker = newest commit with the exact 5-driver `driver/VERSION` set
and install.sh/verify.sh/ecc-reporting.patch present; consensus = the
corpus-freeze-2026-07-28 state; the four rumdl-tables pins are fresh
`rumdl fmt -e MD060 .` commits on the documented upstream pins. Known
layout drift at the club pin: `scripts/qc/memtest_vulkan/memtest_vulkan`
and `recipes/qwen3.8-27b-dflash2/patches/` (documented in VENDORS.md from
the pre-loss tree) do not exist in ANY current-upstream club commit; the
pkill anchor in gate.py falls back to the bare process name, and nothing
in scripts/kit references the patches dir. The memtest_vulkan path is
created by scripts/instance-bootstrap.sh from the carried tarball (2026-10-30).

TODO: the T8 runtime re-pin above is DONE (2026-10-30): HyperQwen @
`f382342` is content-anchored via the carried tarball + tree sha256;
smoke.py verifies the tree hash (git fallback kept for legacy bundles).
After the first good T8 run, fold the resolved engine revisions into
recipe.json per the revision_policy fields.

TODO: re-pin the four `rumdl-tables` vendors to upstream once their
table-format commits land upstream, then delete the local branches.

## Tools

| Tool                                       | Pin                                                                                     | Source                                                          |
| ------------------------------------------ | --------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| gpu-burn                                   | wilicc/gpu-burn @ `0d19c94` (build with CUDA toolkit >= 12)                             | github.com/wilicc/gpu-burn                                      |
| memtest_vulkan                             | GpuZelenograd/memtest_vulkan v0.5.0 (`cdd418a`)                                         | github.com/GpuZelenograd/memtest_vulkan                         |
| compare_vram / memtest-select / pcie-check | club-170hx @ `6eeae24` scripts/qc/ (compare_vram: build sm_80)                          | submodule                                                       |
| torch (T5/T8)                              | 2.13.0 + cu130 wheel (offline wheelhouse in kit)                                        | club gate measured on this exact version (REF_BAND calibration) |
| vLLM (T8)                                  | 0.27.1 + pinned patch set per club recipe.json (kit package: kit/inference/recipe.json) | club recipes/qwen3.8-27b-dflash2                                |

## Carried sources (kit/upstream/)

Run-time copies of exactly what the gate and T8 kit import - no git, no
github.com at session time (2026-10-30 decision, REFERENCE-NUMBERS s0).
Checksums: kit/upstream/SHA256SUMS; provenance:
kit/upstream/club-170hx/PROVENANCE.md.

| Item                                        | Pin       | Notes                                                                          |
| ------------------------------------------- | --------- | ------------------------------------------------------------------------------ |
| club-170hx subset (qc scripts + recipe dir) | `6eeae24` | same bytes as the submodule; Apache-2.0; materialized to vendor/ by bootstrap |
| gpu-burn source tarball                     | `0d19c94` | builds sm_80; installed as bin/gpu-burn/gpu-burn (dash = gate layout contract)  |
| memtest_vulkan source tarball               | `cdd418a` | v0.5.0; placed at club qc memtest_vulkan/ path memtest-select.py resolves       |
| qwen3.8 runtime tree (HyperQwen)            | `7105f2e` | content-anchored re-pin 2026-10-30 (69ba4d06 lost upstream); newest commit pinning vllm==0.27.1 = the club lock; tree sha256 in recipe.json + fetch.sh |

## Driver stack

| Item                                                    | Pin                                                                                     |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Supported drivers (pinned unlocker HEAD, exact strings) | 615.78.08, 615.71.09, 610.57.04, 610.43.03, 610.43.02                                   |
| Club-validated combination                              | Ubuntu 22.04 + kernel 6.8 + 610.43.03 open + cmpunlocker (bare metal or Q35+SeaBIOS VM) |
