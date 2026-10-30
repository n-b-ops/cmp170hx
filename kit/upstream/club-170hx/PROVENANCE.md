# PROVENANCE - carried club-170hx subset

> `kit/upstream/` makes the pipeline GitHub-free at run time: the exact
> upstream bytes the gate and the T8 kit need are carried in-repo, checksummed
> in `../SHA256SUMS`, and materialized to `vendor/club-170hx` by
> `scripts/instance-bootstrap.sh`. The full club submodule stays canonical for
> everything else (PINS.md); this subset is a distribution copy, not a fork.

| Item       | Value                                                        |
| ---------- | ------------------------------------------------------------ |
| Upstream   | <https://github.com/PixelML/club-170hx>                      |
| Pin        | `6eeae24` (PINS.md; same content as the `vendor/` submodule) |
| License    | Apache-2.0 (`LICENSE`, carried verbatim)                    |
| Copied     | 2026-10-30, by `instance-bootstrap` R1 (battle-test round)  |

## What is carried and why

| Carried path                                              | Used by                                          |
| --------------------------------------------------------- | ------------------------------------------------ |
| `scripts/qc/memtest-select.py`                           | gate.py mem_vulkan stage (PTY device selector)   |
| `scripts/qc/compare_vram.cu`                             | gate.py mem_pattern/mem_retention (sm_80 build)  |
| `scripts/qc/pcie-check.sh`                               | gate.py pcie stage                                |
| `recipes/qwen3.8-27b-dflash2/prepare_pinned_models.py`   | kit/inference/fetch.sh (model acquisition)       |
| `recipes/qwen3.8-27b-dflash2/live_benchmark.py`          | kit/inference/smoke.py (benchmark protocol)      |
| `recipes/qwen3.8-27b-dflash2/live_support.py`            | kit/inference/smoke.py (fail-closed guard)       |
| `recipes/qwen3.8-27b-dflash2/recipe.json`                | prepare_pinned_models.py input                   |
| `recipes/qwen3.8-27b-dflash2/requirements.lock`          | recipe pin of record                             |

Everything else in the club repo (docs, results, notebooks, other recipes) is
NOT needed at run time and is not carried. The runtime files
(`scripts/qc/memtest_vulkan/memtest_vulkan`, built binaries under `bin/`)
are build outputs placed by the bootstrap, not carried content.
