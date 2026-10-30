# PINS - canonical submodule and tool pins

> Single source of truth for every pinned version this project depends on.
> README "Pinned sources", SPEC-02 s0.4, and REFERENCE-NUMBERS carry
> convenience copies or pointers - when any of them disagrees with this
> file, THIS file wins; update both sides in the same commit.

## Submodules (vendor/)

| Repo                                | Pin       | Branch                 | Notes                                                    |
| ----------------------------------- | --------- | ---------------------- | -------------------------------------------------------- |
| club-170hx                          | `92328c0` | upstream               | QC methodology; compare_vram, memtest-select, pcie-check |
| cmpunlocker                         | `bc2e727` | upstream               | the unlock tool: driver patch, profiles, verify.sh       |
| consensus-cmp170hx                  | `257e54f` | upstream               | wiki (numbers, procedures, lessons)                      |
| ninfer-cmp170hx                     | `7aba8c0` | `rumdl-tables` (local) | context only                                             |
| cmp-170hx-vllm                      | `69487ee` | `rumdl-tables` (local) | context only                                             |
| CMP-170hx-64gb-LLM-benchmarks       | `07d982f` | `rumdl-tables` (local) | context only                                             |
| Qwen3.8-27B-CMP-170HX (qwen-recipe) | `be1172f` | `rumdl-tables` (local) | REFERENCE-NUMBERS sE anchor receipts                     |
| cmp-170hx-docs | `fcb27c0` | upstream | It-Still-Boots lab docs: the 16-model performance sweep (Nemotron W4A16 + Qwen3.6 FP8 anchors); docs-only reference, no explicit license at pin time |

TODO: re-pin the four `rumdl-tables` vendors to upstream once their
table-format commits land upstream, then delete the local branches.

## Tools

| Tool                                       | Pin                                                                                     | Source                                                          |
| ------------------------------------------ | --------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| gpu-burn                                   | wilicc/gpu-burn @ `0d19c94` (build with CUDA toolkit >= 12)                             | github.com/wilicc/gpu-burn                                      |
| memtest_vulkan                             | GpuZelenograd/memtest_vulkan v0.5.0 (`cdd418a`)                                         | github.com/GpuZelenograd/memtest_vulkan                         |
| compare_vram / memtest-select / pcie-check | club-170hx @ `92328c0` scripts/qc/ (compare_vram: build sm_80)                          | submodule                                                       |
| torch (T5/T8)                              | 2.13.0 + cu130 wheel (offline wheelhouse in kit)                                        | club gate measured on this exact version (REF_BAND calibration) |
| vLLM (T8)                                  | 0.27.1 + pinned patch set per club recipe.json (kit package: kit/inference/recipe.json) | club recipes/qwen3.8-27b-dflash2                                |

## Driver stack

| Item                                                    | Pin                                                                                     |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Supported drivers (pinned unlocker HEAD, exact strings) | 615.78.08, 615.71.09, 610.57.04, 610.43.03, 610.43.02                                   |
| Club-validated combination                              | Ubuntu 22.04 + kernel 6.8 + 610.43.03 open + cmpunlocker (bare metal or Q35+SeaBIOS VM) |

