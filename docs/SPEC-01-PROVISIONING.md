# SPEC-01 — Provisioning the testing stand

> Status: specification · Owner: buyer · Audience: buyer + seller (stand prep)
>
> This document defines what the test stand must provide, how it is brought up
> to a known-good driver+unlock state, and what must be carried to the seller's
> site. Tests themselves are [SPEC-02](SPEC-02-TESTING.md); verdict rules are
> [SPEC-03](SPEC-03-ANALYSIS.md).

## 1. Context and constraints

The card under test is a used NVIDIA CMP 170HX (GA100, passive, compute-only,
stock 250 W) currently installed in the seller's mining rig. The seller offered
a test stand. Constraints that shape this spec:

- The card is **passively cooled** — it has no fan; every load test depends on
  stand airflow, so airflow is part of the instrument, not background.
- The unlock is a **patched Linux kernel module** — it lives on the stand's
  disk, not on the card. No test result is meaningful until the stand's
  driver+unlock state is verified (§5).
- The seller's stand may have **no usable internet** — the complete toolchain
  travels on a USB stick (§6), prepared and verified at home.
- The seller's rig keeps other cards mining — power work on the stand must be
  electrically independent of the rig (§2.3).
- Time on site is bounded — provisioning must reduce to checklists, not
  troubleshooting (§7).

## 2. Stand requirements (to request from the seller in advance)

Send §2.1–§2.6 to the seller as the stand requirement list. Anything the seller
cannot provide becomes a known limitation recorded in the final report, not a
surprise on test day.

> **Кратко (для разговора с продавцом).** Покупаем б/у CMP 170HX; проверяем на
> стенде продавца. До приезда готовим офлайн-кит (драйвер, анлокер, тесты —
> всё на флешке, интернет не нужен) и список требований к стенду: bare-metal
> x86-64 Linux, слот x16 (в идеале карта — единственный GPU в хосте; на
> чужом риге или арендованном инстансе гейт идёт в деградированном режиме
> `--allow-shared-host` — Xid фильтруются по шине, но термика/БП остаются
> под вопросом, и отчёт получит warn), обдув (карта пассивная!), питание
> отдельным EPS-кабелем, разрешение на холодные перезагрузки, ~4,5 часа.
> На месте: инвентаризация и фото → установка драйвера+анлока → холодный
> старт → тест памяти CUDA-паттерном на полный объём (~62 GiB) и memtest_vulkan
> (2 ч) → вычислительный бёрн с термометрией → тензорный фингерпринт →
> тёплый повтор → отчёт. Любая ошибка памяти в разблокированной зоне, Xid
> аппаратного класса, недетерминированная математика или деградация на
> тёплом повторе = отказ от покупки. Полные процедуры — в спецификациях ниже.

### 2.1 Host and slot

| Requirement  | Minimum                                                                                                                                      | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Architecture | x86-64 host, Linux-capable, **with the card under test as its only GPU**                                                                     | The unlock has no Windows path. A mining-rig host with other cards installed does not qualify as the stand: `gate.py` refuses mixed-GPU hosts BY DEFAULT (fleet-clear preflight; `--allow-shared-host` is the documented degraded mode, SPEC-02 §0.2) — neighbor load confounds thermal/PSU verdicts and pollutes Xid attribution. A separate sole-GPU machine, or the rig host with every other GPU physically removed, is required. Degraded alternatives for mixed hosts and rented containers: see the paragraph below the table. |
| Form factor  | **Bare metal strongly preferred**                                                                                                            | A VM adds passthrough failure modes; club's known-good VM path is Q35+**SeaBIOS** (OVMF/UEFI produced `RmInitAdapter`/SEC2 failures) [measured, club INSTALLATION]                                                                                                                                                                                                                                                                                                  |
| Free slot    | 1× PCIe x16 electrical (riser acceptable if the rig already uses one — then test **with that riser**, since that is how the card will leave) | Gen1/Gen2 x16 is the card's ceiling; Gen3+ never trains [measured, club HARDWARE]                                                                                                                                                                                                                                                                                                                                                                                   |
| BIOS control | Secure Boot off, Above 4G Decoding on                                                                                                        | Patched modules are unsigned; the card maps 64 GiB BAR1 after unlock                                                                                                                                                                                                                                                                                                                                                                                                |
| Boot control | Ability to boot USB, and to perform full cold power-off (mains/PSU switch)                                                                   | Cold boot is required by the unlock; a card that drops off the bus may need a 30 s depower [measured, club TROUBLESHOOTING]                                                                                                                                                                                                                                                                                                                                         |
| Console      | Monitor+keyboard or serial console                                                                                                           | No display output exists on the card itself                                                                                                                                                                                                                                                                                                                                                                                                                         |
| OS           | Ubuntu Server 22.04 (any current LTS with kernel ≈6.8 and matching headers)                                                                  | The club-validated combination is Ubuntu 22.04 + kernel 6.8 + driver 610.43.03 open [measured, club HARDWARE]                                                                                                                                                                                                                                                                                                                                                       |

**Mixed hosts and containers (degraded alternatives).** `gate.py
--allow-shared-host --gpu N` runs the full gate on one card of a mixed
host — Xid attribution stays bus-filtered, but thermal/PSU confounds
remain possible and the report forces a CONDITIONAL warn; use it for
rented instances and seller rigs where removing neighbor GPUs is
impossible, and record it in Deviations. Containerized rented instances:
`scripts/setup-container.sh` checks what a container can check (GPU
visibility, driver whitelist, unlocked memory band, dmesg readability); a
container cannot do T9 cold boot and usually cannot install the unlock
itself — T1 becomes "verify the provider's unlock matches the verify.sh
bands" (§4.4), and the kit wheelhouse (torch 2.13.0+cu130) is carried
into a venv for matmul/inference.

### 2.2 Cooling — the instrument, not the background

- **Forced front-to-back airflow through the heatsink fins is mandatory under
  load.** An open frame + room fan was measured insufficient: four idle cards
  at a 180 W cap already dissipated ~141 W with memory at 41–51 °C
  [measured, club COOLING-AND-POWER]; a single card heats quickly when airflow
  stalls.
- Acceptable stand cooling (any one):
  1. a server chassis with the card in a GPU tray behind high-static-pressure
     80 mm fans (the card's native environment);
  2. an open frame with a ducted blower seated on the heatsink inlet — the
     club rig uses one 80 mm blower + 3D-printed duct [measured, club HARDWARE];
  3. a 120 mm+ high-static-pressure fan within ~5 cm of the heatsink intake,
     ducted with cardboard if necessary (non-conductive, non-flammable
     material only; nothing may touch the PCB, components, or the EPS
     connector area).
- If the seller cannot provide directed airflow, the plan degrades: run the
  gate at a reduced power cap (125 W, see SPEC-02 §T4) and record that thermal
  validation is **stand-limited** — thermal numbers then say nothing about the
  card's fitness at 250 W in a proper chassis. Below a 150 W cap the T4
  TFLOPS floors do not apply either (they are calibrated at 180 W): gate.py
  records the number as informational (`tflops_gate=informational`, WARN
  class, CONTRACT.md burn) and the verdict rests on rc/Xid/thermal plus the
  cap-independent dead-card floor (< 20 TFLOP/s = failed compute unlock at
  any cap) — such a run can never end ACCEPT-clean, which is correct for a
  stand-limited session.

### 2.3 Power

- The card takes a **single 8-pin EPS (CPU-style) socket** rated 300 W, carrying
  two 12 V rails. It is **not** a PCIe 8-pin: forcing a PCIe cable in damages
  the board — classified a **permanent** damage risk in the wiki risk register
  [community-reported→documented, wiki start/risks].
- Cards ship with a dual 8-pin-PCIe → 8-pin-EPS Y adapter; ask the seller to
  include the original adapter. If missing, bring one.
- PSU sizing: 250 W for the card + host + fans + transient margin, on a line
  that does **not** share the mining rig's GPUs. No splitters, no mixed modular
  cables between PSU models [club COOLING-AND-POWER, PSU lessons].
- Stock power envelope: 250 W default and maximum on the stock VBIOS,
  `nvidia-smi -pl` accepts 100–250 W [documented, wiki procedures/verify;
  hardware/ga100-silicon]. (300 W exists only on an OC mining VBIOS — out of
  scope.)

### 2.4 The seller's rig — removal protocol

1. Photograph the card in the rig before removal (both sides if possible,
   connectors, serial sticker, bracket, heatsink faces).
2. Ask the seller to shut down the rig properly; verify zero voltage before
   touching connectors.
3. Remove the card, inspect physically (SPEC-02 §T0.1), install into the stand
   on its own power domain.
4. The rig's remaining cards may keep mining — **on the rig's own host**, which
   is now a different machine from the stand (see §2.1: the stand host carries
   only the card under test). They share nothing but mains: separate PSU
   lines, and any rig instability during our tests must not be billed to the
   card under test — or ours to the rig.

### 2.5 Network

Not required if the kit (§6) is complete. If the seller's network is usable,
it only saves nothing — everything is pre-carried. Treat the stand as offline
by design.

### 2.6 Time window

Book the Standard window; agree in advance that the card can stay on the stand
for the Extended profile if any gate is marginal. Note: a matmul retry or a mem_vulkan
setup retry adds 10–30 min — mention the possible overrun when agreeing the
window with the seller.

Time profiles (the gate's `--profile`; the canonical profile table is SPEC-02 s0.1):

| Profile  | Duration   | Use                                                                       |
| -------- | ---------- | ------------------------------------------------------------------------- |
| Express  | ~2.5 h     | walk-away gate: T0–T4, 60 min memtest — pass is necessary, not sufficient |
| Standard | ~4.5 h     | full sequence, 2 h memtest, optional T8 — the default booking             |
| Extended | 8 h+       | overnight memtest 4 h+, T8 recommended — for marginal results             |
| Smoke    | ~20–30 min | rented quick-alive — capped at SMOKE, never ACCEPT (SPEC-02 §0.1)         |

Estimates: the rented-machine battle test replaces these with measured
wall-clock times (§8.5).

## 3. Card variants and what they mean for the stand

Two SKUs exist; identify before unlocking (SPEC-02 §T0.2):

| SKU        | PCI ID (lspci) | Stock VRAM | Unlock target | Notes                                                                                                            |
| ---------- | -------------- | ---------- | ------------- | ---------------------------------------------------------------------------------------------------------------- |
| 8 GB card  | `10de:20c2`    | 8192 MiB   | **65536 MiB** | Club's fleet; every measured anchor in REFERENCE-NUMBERS is this SKU                                             |
| 10 GB card | `10de:2082`    | 10240 MiB  | **40960 MiB** | Fewer enabled stacks; wiki: 80 GB config on this SKU was built, tested, **rejected as unstable** — never attempt |

All thresholds in SPEC-03 and numbers in REFERENCE-NUMBERS assume the 8 GB SKU
unless marked. For a 10 GB card, memory-test targets scale to ~38 GiB and most
measured anchors become **community-reported only**.

## 4. Stand software bring-up (known-good path)

Follow this order exactly; every step has a verify line. The combination is
club-measured on bare metal and on a SeaBIOS VM.

### 4.1 OS and kernel

1. Install Ubuntu Server 22.04 (or verify an existing install of any LTS with
   kernel ≈6.8). Install headers:

   ```bash
   sudo apt install -y build-essential "linux-headers-$(uname -r)" \
     python3 python3-yaml patch curl ccache
   ```

2. Disable Secure Boot in BIOS; verify: `mokutil --sb-state` → `SecureBoot disabled`.
3. Enable Above 4G Decoding; disable CSM if the platform allows.

### 4.2 NVIDIA driver 610.43.03 (open kernel module)

From the kit (offline), verify the recorded sha256, then:

```bash
sudo sh NVIDIA-Linux-x86_64-610.43.03.run \
  --silent --kernel-module-type=open --dkms \
  --no-x-check --disable-nouveau --rebuild-initramfs
```

Verify: `nvidia-smi` lists the card (stock VRAM is fine at this point),
`modinfo -n nvidia` resolves under `updates/dkms`.

> Driver version policy: the unlocker accepts an **exact-match** list of
> nvidia-open versions, checked by string equality at install time
> [measured in code, cmpunlocker install.sh §version_supported]:
> **615.78.08, 615.71.09, 610.57.04, 610.43.03, 610.43.02**. 610.43.03 is the
> club-validated choice; anything else on the list works per the unlocker's own
> CI (all patches apply on all five), but measured anchors stay 610.43.03. Any
> other version aborts the install before touching the host. **Do not
> "upgrade to latest" on the stand.**

### 4.3 cmpunlocker (pinned)

```bash
cd vendor/cmpunlocker     # the pinned submodule snapshot, carried in the kit
# Offline stand: seed the build cache first (kit carries the tarballs):
mkdir -p driver/.build && cp /media/usb/kit/open-gpu-kernel-modules-*.tar.gz driver/.build/

# Bare-metal stand (recommended — minimal host footprint, keep Gen2):
sudo ./install.sh --no-passthrough --no-iommu
# VM stand (Q35+SeaBIOS): also disable the Gen2 service — it hung QEMU guests
# in club testing [measured, club INSTALLATION]:
#   sudo ./install.sh --no-passthrough --no-iommu --no-gen2-service
```

Notes [from the pinned cmpunlocker source]:

- **Geometry is selected per PCI device ID at runtime** (20c2→64 GB,
  2082→40 GB); `--profile` is metadata/label only and is ignored on mixed
  hosts. The install prints a per-GPU expectation table — read it, don't trust
  the flag. On a stand that hosts other NVIDIA GPUs (a mining rig host often
  does), still pass `--profile` explicitly per T0.2 inventory: it keeps the
  recorded metadata honest even though geometry itself is PCI-ID-driven
  [wiki multi-gpu guidance].
- install.sh itself removes conflicting stock DKMS nvidia modules for all
  supported versions and runs `depmod -a`, then builds 14 patches on top of
  the matching `open-gpu-kernel-modules` tag, installing into
  `/lib/modules/$(uname -r)/updates/cmpunlocker/`.
- First build **downloads** the kernel-module tarball from
  `github.com` unless \`driver/.build/\` already holds it — hence the cache
  seeding above on offline stands.
- It also installs: PCIe Gen2 support (`modprobe.d` + early-boot retrain unit
  `gen2.service`), and optionally VFIO passthrough helpers + IOMMU kernel
  cmdline edits (both disabled by the flags above on a bare-metal stand).
- Everything is reversible: `sudo ./remove.sh --yes` + reboot restores stock
  DKMS modules, bootloader cmdline (from its own \`.bak\`), and removes all
  units and helpers. Use this when leaving the seller's stand pristine.

Then a **cold boot**: `sudo shutdown -h now`, **PSU off / mains unplugged,
wait 60 s** (capacitor discharge + WPR2 clear — the wiki prescribes 60 s;
club measured 30 s as a floor for bus-drop recovery), power on. A warm
`reboot` is not equivalent — the unlock writes registers during GSP bootstrap,
and some ACR state is write-protected once set; only a full power cycle
reliably re-runs the unlock path [cmpunlocker source; wiki procedures].

### 4.4 Post-boot verification gate (stand is not ready until all pass)

```bash
nvidia-smi --query-gpu=index,name,pci.bus_id,memory.total,power.limit,temperature.gpu,temperature.memory --format=csv
modinfo -n nvidia                     # MUST resolve to .../updates/cmpunlocker/nvidia.ko
sudo dmesg | grep -Ei 'NVRM|Xid|SEC2|RmInitAdapter|AER'   # expect clean + unlock signature
cd vendor/cmpunlocker && sudo ./verify.sh    # tool's own verification
```

Pass criteria (all required):

1. `memory.total` = **65536 MiB** (8 GB SKU) / **40960 MiB** (10 GB SKU).
2. `modinfo -n nvidia` points into `updates/cmpunlocker`. If it points to
   `updates/dkms` the stock module is shadowing the patch — the classic
   "still 8 GiB despite successful install" failure [measured, club
   INSTALLATION §4]. Fix:

   ```bash
   sudo dkms remove nvidia/610.43.03 --all && sudo depmod -a && sudo update-initramfs -u
   ```

   then cold-boot again.
3. `verify.sh` exits 0 (see REFERENCE-NUMBERS §D for expected strings).
4. No `module verification failed` in dmesg (Secure Boot leftover), no Xid.

### 4.5 Power policy for testing

```bash
sudo nvidia-smi -i 0 -pm 1            # persistence mode
sudo nvidia-smi -i 0 -pl 180          # club benchmark policy; all anchors at 180 W
```

The cap **resets to the 250 W vBIOS default on every reboot and every module
reload** [measured, club HEALTH-WATCH/TROUBLESHOOTING]. The gate scripts
re-assert and re-verify it before every stage; never trust a previously set
cap.

### 4.6 ECC (informational only — do not use as evidence)

The pinned cmpunlocker enables DRAM and SRAM ECC paths via boot-time register
writes. **Caveat that matters for a used-card verdict:** the patched driver
also *synthesizes* the ECC status answers at the RMAPI layer — a green
"ECC: Enabled" in `nvidia-smi` proves the patched module is loaded and
answering, **not** that the ECC hardware functions; the unlocker ships no ECC
functional test [cmpunlocker source, ecc-reporting.patch]. Record ECC mode and
counters in telemetry; treat them as module-provenance signal only. Memory
integrity is proven by the CUDA pattern test and memtest (SPEC-02 §T3), never
by ECC status.

## 5. Stand readiness checklist

The stand is ready when **all** of the following hold:

- [ ] §4.4 verification gate passed after cold boot (exact MiB, module path, verify.sh 0)
- [ ] Power cap verified live: `nvidia-smi --query-gpu=power.limit` → 180 W
- [ ] Telemetry logger runs and writes CSV (scripts/telemetry.py)
- [ ] Xid watcher runs (scripts/xid-watch.py) with a clean baseline window
- [ ] Airflow running and physically verified (paper-strip test on intake)
- [ ] Idle baseline recorded for ≥5 min (SPEC-03 §3.2 compares against it)
- [ ] All kit binaries execute: `gpu-burn`, `memtest_vulkan`, `compare_vram`

## 6. Offline test kit

Contents, versions, checksums: [kit/MANIFEST.md](../kit/MANIFEST.md). Built and
**dry-validated at home** on any machine (scripts must run, checksums verify,
image builds) — never on the seller's stand for the first time. The kit ships
on two USB sticks (primary + spare).

## 7. Pre-visit checklist

**Ask the seller (before the visit):**

1. Card variant if known (8 GB / 10 GB) — else we identify on site (T0.2).
2. Original PCIe→EPS adapter present?
3. Bare-metal host or VM available? (bare metal preferred, §2.1)
4. Free x16 slot + riser if the card ships in one.
5. Directed airflow available (§2.2)? If not — we run at 125 W and say so.
6. PSU line independent from the rig's GPUs (§2.3)?
7. Cold power-off allowed (wall switch / PSU switch access)?
8. USB boot allowed? Secure Boot change allowed?
9. Time window: Standard 4.5 h booked; Extended possible?
10. Card history: hours counter? previous thermal events? re-pasted? repaired?
    (answers are recorded, not trusted — T0 verifies independently)

**Prepare at home:** kit built + dry-run §6 · git bundle of this repo with
submodules · photos checklist empty · printed SPEC-02/SPEC-03 · spare EPS
adapter · insulated tools · ESD strap.

## 8. Rented instances (VPS)

> Requirements for renting a CMP 170HX instance (vast.ai class) to
> download every model, run the full acceptance pipeline, and battle-test
> the scripts. The bare-metal seller-stand kit is SPEC-01/MANIFEST; this
> page is the rented-container counterpart. First command on any
> candidate instance: `scripts/setup-container.sh` - it checks GPU
> visibility, the driver whitelist, the unlock band, dmesg readability,
> and host GPU count, and prints the exact gate flags the instance needs.

### 8.1 Instance requirements

| Requirement    | Need                                                                               | Why                                                                                                                                    |
| -------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| GPU            | 1 x CMP 170HX, already unlocked to 64 GB (memory.total >= 60000 MiB)               | a container cannot install the unlocker; 30000-34000 MiB = single-rank reject; intake dies on anything outside the bands               |
| Driver         | on the pinned whitelist (PINS.md; machine source pins/drivers.json) | unlocker whitelist; the T8 driver rule (prefer 610.43.03/610.57.04; the sm80 DFlash2 Xid-31 reports were on 610.43.02) is canonical in REFERENCE-NUMBERS §0 and the recipe cautions |
| Power cap      | `nvidia-smi -pl` as root or provider-preset 180 W                                  | the gate asserts and verifies the cap before every stage (CONTRACT); a 250 W default without root rights dies at preflight - by design |
| SSD free space | >= 200 GB (160 GB minimum without the optional W8A8 model)                         | budget in §8.2                                                                                                                         |
| RAM            | >= 32 GB (64 comfortable)                                                          | model prep/quant steps + page cache for 20-40 GB checkpoints                                                                           |
| CPU            | >= 8 cores                                                                         | flashinfer JIT, ninja/CUDA builds (the NInfer sm80 build alone is 245 steps)                                                           |
| Network        | unmetered >= 100 Mbit                                                              | ~90-130 GB of one-time downloads (wheels + three model sets)                                                                           |
| OS image       | Ubuntu 24.04 preferred                                                             | the inference venv needs python3.12 (cp312 wheels); 24.04 has it native, 22.04 needs it installed                                      |
| Privileges     | root/sudo nice-to-have; required only for power-cap and dmesg                      | without root: --allow-no-dmesg (Xid channel marked unverified); xid-watch is skipped with a WARN                                       |
| Docker         | needed only for the NInfer MoE path                                                | the fork ships a compose file (ninfer-cmp170hx:sm80, CUDA 13.1 image - driver >= 580, every whitelist driver qualifies)                |
| Exit plan      | rsync/scp the evidence/ bundles off before destroying the instance                 | the bundle is the deliverable, not the instance                                                                                        |

### 8.2 SSD budget - download and test all models

| Item                                                                                                                       |                      Size |
| -------------------------------------------------------------------------------------------------------------------------- | ------------------------: |
| OS + Docker + CUDA 13.1 container image                                                                                    |                    ~20 GB |
| Repo + vendors + gate tools (prebuilt bin/)                                                                                |                     ~5 GB |
| T8 kit staging: wheels ~5 + model tree ~23 (base 18 GiB; fast variant +2.5, shards 1-6 hardlinked; draft ~2) + HF cache ~3 |                    ~31 GB |
| venv-inference installed (torch cu13 + vLLM + flashinfer)                                                                  |                     ~8 GB |
| Gate venv (torch for matmul)                                                                                               |                     ~4 GB |
| NInfer MoE path: container ~12 + build ~5 + artifact 21.22 GiB                                                             |                    ~39 GB |
| Optional: Avesed W8A8 (39.8 GiB) - context-only, not a gate                                                                |                    ~40 GB |
| Evidence bundles, memtest raw logs, JIT caches, headroom                                                                   |                    ~15 GB |
| Total (all models incl. optional)                                                                                          |    ~162 GB -> rent 200 GB |
| Total without the optional W8A8 | ~122 GB -> minimum 160 GB |
| Optional add-on: extended serving set (Qwen3.6-35B-FP8 ~34 GB + Nemotron NVFP4 ~22 GB + per-model venvs/wheelhouses ~12 GB) — additive on top of either total above | +~68 GB |

Arithmetic: the extended serving set adds ~68 GB to either total.
Base + extended = ~190 GB total (rent 200+ GB). Base + Avesed +
extended = ~230 GB total (rent 260 GB).

### 8.3 Session runbook (the battle test itself)

1. `scripts/instance-bootstrap.sh --torch` - materializes
   `vendor/club-170hx` from `kit/upstream/`, builds compare-vram,
   gpu-burn and memtest_vulkan (no GitHub; `--selftest` verifies the
   carried kit on a GPU-less host), builds the gate venv, then runs
   `scripts/setup-container.sh` - record its verdict and flags.
2. `gate.py --profile smoke --allow-shared-host [--allow-no-dmesg]`
   (~20-30 min): intake, mem_pattern, 5-min burn, matmul, pcie - the
   first end-to-end exercise of the round-14 hardening.
3. `gate.py --profile standard --allow-shared-host [--allow-no-dmesg]`
   (~4.5 h): full gate incl. 2 h memtest_vulkan + warm_rerun.
4. `make-report.py <evidence-dir>` - expect ACCEPT/CONDITIONAL with
   degraded-mode warns (shared-host/no-dmesg are CONDITIONAL unless
   accepted; SMOKE caps smoke runs, never ACCEPT).
5. Tier-B T8: `kit/inference/fetch.sh` (egress) -> `install.sh --check`
   -> `install.sh` -> `smoke.py` (airflow is the provider problem; the
   180 W cap is the instrument).
6. Optional MoE: NInfer sm_80 fork via its compose + the sha256-gated
   artifact (REFERENCE-NUMBERS §0) - informational only.
7. `compare-fingerprints.py` - note: no reference exists yet; the first
   rented card becomes the reference for card #2 (the club anchors in
   REFERENCE-NUMBERS decide card #1).

### 8.4 Readiness review - per component

| Component                                    | Status                                                                        | Notes / gaps for the rented session                                                                                                                                                                                                                    |
| -------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| gate.py (preflight, stages, watchdog, retry) | ready - suite-locked (39 e2e bundles + 5 family modules; the contract_sync entry point runs them all)                       | round-14 P0s fixed: idle no longer reads FAIL, shared_host=1 no longer warns on sole-GPU hosts; shutdown fail-safe + shutdown-robustness test                                                                                                          |
| preflight on containers                      | ready - 3 environment checks needed                                           | (a) power-cap control: root nvidia-smi -pl or provider preset, else preflight dies by design; (b) bin/ tools carried PREBUILT (sm_80 fatbins per MANIFEST s6.4) or built on-instance with a CUDA toolkit; (c) python3.12 for the inference venv        |
| telemetry.py / xid-watch.py                  | ready (ported from the bash gate; header smoke-tested on a GPU-less host)               | xid-watch needs root + dmesg - usually unavailable in containers; the gate skips it with a WARN and xid_counting=unavailable marks the channel                                                                                                         |
| make-report.py / compare-fingerprints.py     | ready                                                                         | fingerprint comparison is meaningful from card #2; card #1 verdicts rest on the club anchors                                                                                                                                                           |
| setup-container.sh                           | ready                                                                         | does NOT check power-cap control - verify sudo -n nvidia-smi -pl 180 manually before booking time                                                                                                                                                      |
| kit/inference (fetch/install/smoke)          | code-complete, NOT yet exercised end-to-end - this session is its battle test | soft spots: the start_qwen.sh env contract comes from the recorded onstart sequence (a mismatch fails loudly at the health check with the server log captured); fetch.sh needs ~40 GB staging + network; install.sh hard-fails version drift by design |
| NInfer MoE path                              | vendored + club field-proven, not packaged by us                              | manual path: fork compose build + artifact sha256 check (REFERENCE-NUMBERS §0); no gate impact                                                                                                                                                         |
| Docs/contract sync                           | green                                                                         | contract_sync OK (full suite green); rumdl clean; vendors pristine                                                                                                        |

Overall: ready to battle-test. The suite locks the verdict logic; the
open risks are environmental (driver quirks, provider image, power cap,
the T8 launcher contract) - exactly what a rented session exists to
surface. The known-unknowns list above is the debrief template.

### 8.5 Timeline provenance - estimates until measured

Every duration in this suite is an **estimate** until the rented-machine
battle test (s8.3) replaces it: the s2.6 profile table, SPEC-02 s0.1
totals and per-stage durations, and the kit's <30 min install target.
The session debrief MUST update each timeline with the measured
wall-clock numbers - stage-by-stage where the gate log carries them
(gate.log timestamps every stage start and finish), and the profile
totals from session start to report. Until then every "~" figure is
planning guidance, not a measured anchor.
