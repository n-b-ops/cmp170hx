# REFERENCE-NUMBERS — every anchor, with its evidence label

> Convention (from the club repo): **measured** = reproduced from a pinned
> receipt; **community-reported** = published without a verifiable receipt
> chain; **inferred** = our reasoning; **untested** = plausible, never
> exercised. Sources: club = club-170hx submodule @ `92328c0`; unlocker =
> cmpunlocker submodule @ `bc2e727`; wiki = consensus-cmp170hx submodule @
> `257e54f` (corpus freeze 2026-07-28); ninfer = ninfer-cmp170hx @ `7aba8c0`
> (upstream `2276286` + local table-format commit); recipe = cmp-170hx-vllm @
> `69487ee` (upstream `192cc33` + local table-format commit — see README
> "Pinned sources" for the rumdl-tables policy); bench =
> CMP-170hx-64gb-LLM-benchmarks @ `07d982f` (upstream `70306e5` + local
> table-format commit); qwen-recipe = Qwen3.8-27B-CMP-170HX @ `be1172f`
> (upstream `41d2c41` + local table-format commit — the receipt-chain repo
> behind the club's §E anchor); lab docs = It-Still-Boots/cmp-170hx-docs @
> `fcb27c0` (the 16-model performance sweep).
>
> Pins: PINS.md is canonical (submodules, tools, driver stack).

## 0. Decisions (dated)

Short, dated answers to "what do we do and why". Numbers and full context
live in the sections below; nothing here overrides CONTRACT.md. Newest
first.

### 2026-10-26 — T1 memory WARN band machine-enforced (8 GB SKU)

SPEC-03 §5 always documented a T1 WARN band — 60000–65535 MiB unlocked
on the 8 GB SKU, below the 65536 target — but nothing enforced it: a
60000 MiB card read ACCEPT. `compute_verdict` now records an
`intake-warn-band` warn there (CONDITIONAL, overridable by
`--accept-warnings`); the CONTRACT intake row is updated to match.
The 40 GiB SKU analog (35000–40959) has no documented band and no
anchor data; deferred until a 10 GB SKU card is actually tested.

### 2026-10-26 — extended informational serving recipes added

Two non-acceptance serving recipes joined kit/inference/recipes/:
Qwen3.6-35B-A3B-FP8 (vLLM 0.28.0) and Nemotron-3.5-Lightning-30B-A3B
(NVFP4-on-Ampere W4A16 fallback). They run the SAME usage-counted
protocol as the acceptance recipe via smoke-model.py and write
inference-<key>.json receipts (rc=0 on protocol completion — anchors
are community/lab-reported and deltas are recorded, never gated). Their
anchor source, the It-Still-Boots lab sweep, is now vendored
(vendor/cmp-170hx-docs @ fcb27c0). The acceptance gate is unchanged: T8
stays the Qwen3.8-27B W4A16+DFlash2 recipe of record with CONTRACT
floors; these recipes are classification: informational and the test
suite fails if one ever grows floors.

### 2026-10-26 — T8 inference: test the club recipe of record

Qwen3.8-27B
W4A16 AutoRound (dbirks) + fast variant + DFlash2 k=7, vLLM 0.27.1 @ 180 W —
the only receipt-chained single-card W4A16 path (136.38 tok/s decode256,
three cards; §E); packaged in `kit/inference`. Driver rule: prefer 610.43.03 /
610.57.04 — the sm80 DFlash2 runtime Xid-31 reports (HyperQwen #72/#98, both
closed) were on 610.43.02, and the third-box reproduction on 610.57.04 could
not reproduce either fault. W4A16/W8A16 **Marlin** checkpoints wedge GA100 at
load (Xid 31) — only the pinned AutoRound + DFlash2 path is receipt-chained.
Alternatives (stock AutoRound, the W4A16 GPTQ endpoint notebook, the Qwen3.6
@300 W dataset) are context, never gates.

### 2026-10-26 — Qwen3.6-35B-A3B (MoE): working path identified, no gate change

On a stock-VBIOS 180 W stand the working path is the NInfer sm_80 fork plus
the receipt-chained `neroued/Qwen3.6-35B-A3B-NInfer` artifact (21.22 GiB,
sha256 `1fb9ea0b…`; field 93–126 tok/s decode, ~218 on long prompts, ~26 GiB
VRAM). The faster vLLM W8A8 figure (203.6–206.3 tok/s, §E) is 300 W OC,
single-source, VRAM-tight — context only. MoE is a Tier-B informational
extension; the acceptance gate stays the Qwen3.8 recipe of record.

## A. Card identity

| Fact                               | Value                                                                                                                                                                                              | Label / source                                                  |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 8 GB SKU PCI ID                    | `10de:20c2`, subsystem `10de:1585`                                                                                                                                                                 | measured (club) + wiki                                          |
| 10 GB SKU PCI ID                   | `10de:2082`, subsystem `10de:1557`                                                                                                                                                                 | wiki (community)                                                |
| Non-unlockable lookalike           | `10de:20b0` (A100 SXM4/ES; installs, never unlocks)                                                                                                                                                | wiki                                                            |
| Board P/N (physical discriminator) | `900-11001-0108-000` (8 GB) / `900-11001-0105-000` (10 GB)                                                                                                                                         | wiki; 8 GB confirmed measured (club)                            |
| Die / SM                           | GA100-105F, CC 8.0; 70 SM stock, **74** with pinned unlocker (`6c442ee`)                                                                                                                           | wiki (70) + measured (club, 74)                                 |
| HBM                                | 8 GB SKU: Hynix HBM2e, 4 stacks, 4096-bit, 16/24 FBPAs; 10 GB SKU: Samsung HBM2, 5 stacks, 5120-bit, 20/24 FBPAs                                                                                   | wiki                                                            |
| HBM vendor risk                    | Samsung-memory cards reportedly needed kernel workarounds (exllama/eager) and still threw **Xid 79** under sustained inference load; the cmp-170hx-vllm serving recipe is validated **Hynix-only** | community-reported (vendor/cmp-170hx-vllm README)               |
| Stock VRAM                         | 8192 MiB / 10240 MiB                                                                                                                                                                               | measured (club) / wiki                                          |
| Unlocked VRAM                      | **65536 MiB** (8 GB dual-rank) / **40960 MiB** (10 GB)                                                                                                                                             | measured (club, 8 GB) / wiki                                    |
| Single-rank 8 GB ceiling           | 32768 MiB (unlocker logs detection)                                                                                                                                                                | community (club issue #48)                                      |
| 80 GB on 10 GB                     | **rejected — unstable** (fold at 40 GiB, Xid 31/154, one-context-per-fire)                                                                                                                         | wiki, multiple testers                                          |
| VBIOS variants                     | `92.00.67.00.01` (250 W, HBM NDIV 54=1458 MHz) · `92.00.6D.00.0A` (300 W OC, NDIV 64=1728 MHz) · `92.00.66.00.02` (10 GB)                                                                          | measured (club §4) / wiki; VBIOS does not affect unlock success |
| Power envelope                     | 250 W default=max on stock VBIOS (min 100 W via `nvidia-smi -pl`); 300 W only on OC VBIOS                                                                                                          | wiki + measured (club fleet had one 300 W-default card)         |
| Power connector                    | 1× 8-pin **EPS** (CPU-style, 300 W, two 12 V rails); 2×PCIe→EPS Y adapter supplied                                                                                                                 | wiki                                                            |
| PCIe stock                         | Gen1 **x4 electrical** (~0.8–0.85 GB/s)                                                                                                                                                            | wiki + measured (club)                                          |
| PCIe unlocked                      | Gen2 x16 (link patch in pinned unlocker); Gen3 OTP-fuse-blocked; D2H 6.68 GB/s measured                                                                                                            | measured (club, 2026-10)                                        |
| Sensors                            | GPU temp + memory temp only; no fan tach/VRM/hotspot/voltage                                                                                                                                       | wiki                                                            |
| ECC                                | Fused off at silicon level; pinned unlocker synthesizes status at RMAPI layer — **green ECC display ≠ working ECC**                                                                                | wiki (fuses) + unlocker source (synthesis)                      |

## B. Memory integrity and bandwidth

| Fact                         | Value                                                                                                                                               | Label / source                       |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Used-card HBM failure rate   | ~50% in one buyer group, faults only above the 8 GiB mining window                                                                                  | community-reported (club LESSONS §h) |
| Fake-64 GB (single-rank)     | passes memtest_vulkan, **fails CUDA pattern test**                                                                                                  | community (club RESEARCH §2a)        |
| compare_vram target          | 62 GiB (64 GB geometry) / 38 GiB (40 GB geometry)                                                                                                   | measured method (club QC stage 2)    |
| memtest_vulkan               | v0.5.0; full-VRAM pass; no CLI device flag (PTY wrapper required); exit 64 = setup error, not card failure                                          | measured (club gate)                 |
| HBM bandwidth theoretical    | 1493–1555 GB/s by SKU and clock (8 GB clock disputed 1458/1592/1728 MHz)                                                                            | wiki                                 |
| HBM bandwidth measured range | **1305.86–1600 GB/s — no canonical figure**; unlock changes bandwidth 0×; 8-card rental mean ~1600, <2.5% spread; "1769.47" is tool-computed theory | wiki (multiple testers)              |
| Bandwidth verdict use        | **informational only — never a gate** (clock is VBIOS-dependent; tool metrics vary)                                                                 | inferred from wiki dispute           |
| vLLM memory utilization      | ≤ 0.90 (0.95 crashed: 65052 MB exposed vs 64733 usable)                                                                                             | wiki                                 |

## C. Compute (unlocked, single card)

| Workload                                                    | Expected                                                                                                               | Label / source                                                                |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| gpu-burn `-tc` (tensor)                                     | **74.6–78.5 TFLOP/s** @180 W, 0 errors, 10 min                                                                         | measured (club health gate, 70 SM; pinned unlocker = 74 SM → equal or higher) |
| gpu-burn FP32                                               | 12.2–12.8 TFLOPS sustained (30 min: 12,472–12,485 GFLOP/s, 0 errors, 75→77 °C @300 W)                                  | wiki (multiple testers)                                                       |
| gpu-burn FP64 (`-d`)                                        | **6.2–6.31 TFLOPS** non-tensor (exactly ½ FP32); `-m 63500 -d 30` = 0 errors                                           | wiki                                                                          |
| BF16 tensor (cuBLAS-class)                                  | 164.4–192.7 TFLOPS; **~12 = failed compute unlock**                                                                    | wiki (multiple testers)                                                       |
| Locked (stock) FP32                                         | 0.30–0.41 TFLOPS (throttle); a stock driver runs the card fine — locked ≠ broken                                       | wiki                                                                          |
| Matmul fingerprint (seed 170170, 4096², torch 2.13.0+cu130) | max-abs-err BF16 1.348 / FP16 0.166 / TF32 0.098 / INT8 0; cross-card bit-identical = pass                             | measured (club, 4 cards, 70 SM)                                               |
| Thermal peaks @180 W burn                                   | core 56–63 °C / mem 64–71 °C (ducted airflow)                                                                          | measured (club gate)                                                          |
| Idle @180 W cap                                             | core 37–38 °C / mem 41–51 °C / 33–41 W                                                                                 | measured (club, two snapshots)                                                |
| Throttle/stops                                              | driver limits: shutdown 98 / slowdown 95 / max-op 85 (core) / 95 (mem) °C; our gate stops at 80/85                     | wiki + club policy                                                            |
| Load character                                              | FP32 burn draws only ~60 W — **invalid cooling validation**; tensor ~75 W; hashcat/STREAM 160+ W; LLM decode 230–240 W | wiki                                                                          |

## D. Driver and unlock

| Fact                                     | Value                                                                                                                                                                | Label / source                             |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Supported drivers (pinned unlocker HEAD) | exact-string list: **615.78.08, 615.71.09, 610.57.04, 610.43.03, 610.43.02**                                                                                         | measured in code (unlocker driver/VERSION) |
| Club-validated combination               | Ubuntu 22.04 + kernel 6.8 + 610.43.03 open + unlocker; bare metal or Q35+SeaBIOS VM (OVMF fails `RmInitAdapter`/SEC2)                                                | measured (club INSTALLATION)               |
| Wiki-era driver matrix                   | 610.43.03 (default) / 610.43.02 both boot-tested; backport branches unbooted                                                                                         | wiki (corpus 2026-07-28)                   |
| Install requirements                     | root, kernel headers, Secure Boot **off**, network or pre-seeded tarball cache, initramfs tool                                                                       | wiki + unlocker source                     |
| Cold boot                                | full power-off, **60 s** wait (WPR2 clear); warm reboot insufficient                                                                                                 | wiki; 30 s floor measured (club, bus-drop) |
| Post-install memory check                | `verify.sh` exit 0: 8 GB SKU ≥60000 MiB; 10 GB SKU 35000–59999; stock bands 7680–8704 / 9728–10752; + Gen2 (`2,2`)                                                   | measured in code (unlocker verify.sh)      |
| dmesg unlock signature                   | `saved stock signature` → `POST-WRITE … CFG1/LMR` → `normal BooterLoad status=0x0` → `POST-BooterLoad verify` → `WPR meta updated` → `late PMA extension status=0x0` | wiki (exact strings)                       |
| Module path check                        | `modinfo -n nvidia` → `updates/cmpunlocker/nvidia.ko`; depmod ambiguity can silently pick stock — one `nvidia.ko` under `updates/` only                              | wiki + club INSTALLATION                   |
| Rollback                                 | `sudo ./remove.sh --yes` + reboot; card keeps unlocked size until cold boot                                                                                          | wiki + unlocker source                     |

## E. Inference (single card, 1× CMP 170HX)

| Recipe                                                                                                                                                                                           | Numbers                                                                                                                                                                                                                                                                                                                                                                                                 | Label / source                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Qwen3.8-27B W4A16 AutoRound + DFlash2 k=7, vLLM 0.27.1, 180 W, 64k** (recipe of record)                                                                                                        | decode256 **136.38 tok/s** mean (per-card 133.57/135.31/140.27), decode900 **122.00**, prefill **1,946 tok/s** @6,603 in, TTFT **190.8 ms**; peaks 51 °C core / 61 °C mem; re-reproduced 2026-10-10 (136.96/117.92/1,929.8)                                                                                                                                                                             | measured (club, 3 cards + reproduction)                                                                              |
| Same @255 W                                                                                                                                                                                      | 147.7 tok/s decode256 (180 W = 95% of decode at 71% of power)                                                                                                                                                                                                                                                                                                                                           | community-reported (club, hosted reference)                                                                          |
| Acceptance floors (SPEC-02 §T8)                                                                                                                                                                  | decode256 ≥130, decode900 ≥112, prefill ≥1750, TTFT ≤250 ms                                                                                                                                                                                                                                                                                                                                             | inferred (floor ~3% under the measured band floor: 133.6 to 130; decode900 similar)                                                                                  |
| Qwen3.6-35B-A3B-AWQ-4bit (vLLM)                                                                                                                                                                  | 113 tok/s decode / 452 @4-par / 1,700 prefill                                                                                                                                                                                                                                                                                                                                                           | community-reported (wiki, single tester)                                                                             |
| llama.cpp 27B Q4_K_M                                                                                                                                                                             | pp512 888.09 / tg128 36.87                                                                                                                                                                                                                                                                                                                                                                              | community-reported (wiki, one run)                                                                                   |
| Roadmap-era Qwen figures — now identified: **cmp-170hx-vllm** recipe (Qwen3.8-27B-Int8 GPTQ + MTP k=3, prefix caching, single 64 GB Hynix card)                                                  | nightly: decode prose 65.4 (greedy) / 66.8 (temp 0.7) / structured 74.0 tok/s; MTP acceptance 3.02/4; TTFT 7K prefix 1.44 s cached / ~6.7 s cold; ctx 262,144 (fp8 KV). 0.27.1 fallback: 60.0/62.5/74.1, acceptance 3.12/4                                                                                                                                                                              | community-reported, now **pinned** (vendor/cmp-170hx-vllm @ 192cc33) — context only, not gates                       |
| NOT usable as acceptance floors                                                                                                                                                                  | Ninfer 38.16 tok/s; megakernel GGUF 51–94; stock-FP32 anything                                                                                                                                                                                                                                                                                                                                          | measured (club) — different stacks                                                                                   |
| **bench dataset @ 300 W OC VBIOS** (Qwen3.6 gen — one generation older than the club's Qwen3.8 anchor; vLLM 0.24.0, driver 610, batch 1, greedy, usage-object token counting, warmup discipline) | Qwen3.6-27B W8A8 int8 (29.1 GiB, native INT8 cutlass): tg 92.6–99.4 (MTP k=3; k=4 97–108), pp512 2619, pp2048 3472. W4A16 GPTQ g128 Marlin (18.1 GiB): tg 129.8–139.5 (MTP k=3), pp512 1581. 35B-A3B MoE W8A8 (39.8 GiB): tg 203.6–206.3 (MTP k=2, acceptance 66%), VRAM 60.4/65.5 GiB                                                                                                                  | community-reported (vendor/CMP-170hx-64gb-LLM-benchmarks @ 07d982f) — context only, not gates                        |
| bench-dataset counterpoints                                                                                                                                                                      | (a) native INT8 tensor path works in vLLM 0.24 on this card — a newer-stack datapoint against the wiki's INT8-gating claim (§C); (b) `--gpu-memory-utilization 0.95` ran stably here, a counterexample to the wiki's ≤0.90 crash report — our T8 keeps ≤0.90 as the conservative rule; (c) MoE prefill is high-variance on untuned fused_moe configs — reinforces "different stacks are not comparable" | community-reported, single source                                                                                    |
| Nemotron-3.5-Lightning-30B-A3B W4A16-fallback (vLLM)                                                                                                                                             | 227.7 tok/s @16k / 217.9 @64k (TTFT 1.7/7.8 s), 1,306 tok/s aggregate @32k×16, 79% decode retention @128k, speculation off @300 W; DSpark drafter 1.40–2.11×                                                                                                                                                                                                                                            | third-party lab (vendored cmp-170hx-docs @ fcb27c0) — context only, never a gate                                     |
| Qwen3.6-35B-A3B-FP8 (vLLM 0.28.0)                                                                                                                                                                | short-context 355.7 tok/s / prefill 9,431 / TTFT 109 ms (single-stream, self-report); long-context 153.8 @16k / 135.9 @64k (TTFT 1.6/9.1 s) @300 W spec-off                                                                                                                                                                                                                                             | community-reported (LocalMaxxing) + third-party lab (vendored cmp-170hx-docs @ fcb27c0) — context only, never a gate |

> **§E anchor receipts** (vendored as **qwen-recipe** @ be1172f — see
> PINS.md): the decode256 136.38 band is backed by full raw evidence —
> per-card bench.jsonl, specdec acceptance data, nvidia telemetry, a clean
> 3-card rerun at 135–140 tok/s @180 W, and the postmortem of the 47.0
> tok/s outlier (host contention + pinned KV, not the card — the exact
> SPEC-02 T8 methodology-trap case). The derived acceptance floors in the
> table above remain **inferred**.

## F. Interconnect (context for multi-card ambitions)

| Path                              | Measured                                         | Label                                   |
| --------------------------------- | ------------------------------------------------ | --------------------------------------- |
| Gen2 x16 D2H                      | 6.68 GB/s (~84% of 8 GB/s)                       | measured (club)                         |
| Host-staged D2D (no P2P)          | 6.21–6.30 GB/s @256 MiB                          | measured (club)                         |
| Static-BAR1 P2P (4 extra patches) | 5.79 GB/s, 12/12 pairs                           | measured (club, experimental stack)     |
| Stock Gen1 x4                     | 0.80–0.85 GB/s                                   | wiki                                    |
| NVLink / Gen3                     | fused off / OTP-blocked — do not chase           | wiki + measured (club, 4/4 cards)       |

