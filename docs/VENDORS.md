# VENDORS - what comes from where

> Reference map of every pinned source this repo depends on, and exactly
> which of its artifacts the pipeline uses. Pins are canonical in
> [PINS.md](../PINS.md), which carries a one-phrase role note per
> vendor; the artifact-level detail lives here. Submodule checkouts are shared
> between sessions - never rebase or reset them in place.

## Submodules (vendor/)

| Vendor | Pin | Role | Artifacts used by this repo |
| ------ | --- | ---- | --------------------------- |
| club-170hx | `92328c0` (upstream) | QC methodology + measured anchors | `scripts/qc/compare_vram.cu` (built sm_80 -> bin/, T3a/T3b/T7), `scripts/qc/memtest-select.py` (T3c PTY wrapper), `scripts/qc/pcie-check.sh` (T6 + inventory), `scripts/qc/memtest_vulkan/memtest_vulkan` (pkill anchor), `scripts/qc/tensor-gate.sh` (ancestor of matmul_check.py), `recipes/qwen3.8-27b-dflash2/` (recipe.json, requirements.lock, prepare_pinned_models.py, live_benchmark.py, live_support.py, patches/) |
| cmpunlocker | `bc2e727` (upstream) | the unlock tool | install.sh/verify.sh (SPEC-01 s4.3), driver/VERSION (whitelist strings -> pins/drivers.json), ecc-reporting.patch (s4.6) |
| consensus-cmp170hx | `257e54f` (upstream) | the 55-page wiki | numbers/procedures/lessons cited throughout REFERENCE-NUMBERS (PCI IDs, board P/N, Xid classes, cold-boot 60 s, ...) |
| ninfer-cmp170hx | `7aba8c0` (rumdl-tables) | context only | the sm_80 NInfer fork + the 35B-A3B MoE path (REFERENCE-NUMBERS s0/sE); not used by scripts |
| cmp-170hx-vllm | `69487ee` (rumdl-tables) | context only | Int8 GPTQ+MTP serving recipe, Hynix-only validation, Marlin-wedge warning (sE, recipe cautions) |
| CMP-170hx-64gb-LLM-benchmarks | `07d982f` (rumdl-tables) | context only | the 300 W OC dataset (sE); not used by scripts |
| Qwen3.8-27B-CMP-170HX | `be1172f` (rumdl-tables) | receipt chain | per-card bench.jsonl, specdec acceptance, the 47-tok/s outlier postmortem (sE anchors) |
| cmp-170hx-docs | `fcb27c0` (upstream) | lab performance sweep | the 16-model matrix (Nemotron W4A16 + Qwen3.6 FP8 anchors for the informational recipes); **no explicit upstream license at pin - reference-only** |

## External runtime (not a submodule)

| Source | Pin | Used by |
| ------ | --- | ------- |
| syv-ai/qwen38-27b-rtx3090 (renamed HyperQwen; old name redirects) | `69ba4d06` | fetch.sh clones it; install.sh applies its vLLM patches; smoke.py runs its single-user/start_qwen.sh |
| HyperQwen issues #72/#98 + docs/reproductions/cmp-170hx-64gb.md | - | the sm80 DFlash2 Xid-31 reports and the third-box non-repro (driver rule, REFERENCE-NUMBERS s0) |

## Model repositories (Hugging Face)

| Repo | Revision | Path |
| ---- | -------- | ---- |
| dbirks/Qwen3.8-27B-W4A16-AutoRound | `1f05c441` | acceptance base |
| syvai/qwen3.8-27b-3090-fast-variant | `124c14e7` | acceptance fast overlay |
| syvai/Qwen3.8-27B-DFlash2-W4A16 | `4d30ec73` | acceptance draft |
| Qwen/Qwen3.6-35B-A3B-FP8 | pinned at fetch | informational recipe |
| nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 | pinned at fetch | informational recipe |
| nvidia/...-NVFP4-DSpark | not wired (v1) | Nemotron speculative drafter |

Unverified alternatives (named in recipe notes, never used): GotoAI-Inc
and useful-quants W4A16 Nemotron quants, community GGUF variants.

