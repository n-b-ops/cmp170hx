# CONTRACT — stage pass/fail signals (authoritative)

> This table is the single source of truth for what "pass", "warn", and
> "fail" mean for every stage. `scripts/gate.py` enforces the live column
> during the run; `scripts/make-report.py` re-derives the same verdicts from
> the receipts afterward; discrepancies between the two are bugs. If this
> file and any spec prose disagree, **this file wins** — change the specs to
> match, or change this file deliberately and both programs with it.
>
> Verdict mapping: any **FAIL** or `run-summary.xid_total > 0` → REJECT.
> Any **INCOMPLETE** stage → CONDITIONAL, **not overridable** by
> `--accept-warnings` (the stage's evidence does not exist; re-run it on a
> working stand). Any **WARN** or SKIPPED stage → CONDITIONAL (ACCEPT with
> `--accept-warnings`, which never overrides a REJECT or an INCOMPLETE).
> No **or unparseable or malformed** (e.g. missing `xid_total`)
> `run-summary.json` → **REJECT-INCOMPLETE** (gate.py died at preflight or
> mid-run, or the final receipt was truncated) — an empty or corrupted
> evidence directory can never produce ACCEPT, and an unknown Xid count is
> never treated as zero. A **completed** run (`aborted=0`) whose bundle
> lacks receipts for the core memory stages (mem_pattern/mem_retention/mem_vulkan) →
> **REJECT-INCOMPLETE** — no card verdict exists without the CUDA-side
> memory test (single-rank fakes pass memtest_vulkan and fail the CUDA
> test, so `--skip mem_pattern,mem_retention` can never produce an ACCEPT); a missing
> non-core hard-fail stage (burn/matmul/warm_rerun) records a per-stage `-not-run` warn
> (CONDITIONAL unless accepted). Aborted runs are exempt: a die() mid-stage
> legitimately leaves later stages unrun and follows the aborted rules
> above. **Priority** (a found defect beats missing evidence beats a soft
> workload failure): REJECT (hard fails / `xid_total>0`) → REJECT-INCOMPLETE
> (core-missing coverage) → CONDITIONAL (T8 FAIL, forced — never a REJECT,
> but never masking one either) → CONDITIONAL (INCOMPLETE stage) → warns.
> A `profile=smoke` run (quick-alive subset — intake/mem_pattern/burn/matmul/pcie,
> ~20–30 min) caps every non-REJECT outcome at **SMOKE**: dead cards still
> read REJECT, everything else is explicitly not an acceptance verdict.

## Stage names (semantic ↔ T-number ↔ receipt)

| semantic        | T-number                 | receipt                                 |
| --------------- | ------------------------ | --------------------------------------- |
| `intake`        | T0 post-unlock (automated)              | `intake`                                |
| `unlock`        | T1 (manual)              | (no receipt)                            |
| `idle`          | T2                       | `idle`                                  |
| `mem_pattern`   | T3a                      | `mem_pattern`                           |
| `mem_retention` | T3b                      | `mem_retention`                         |
| `mem_vulkan`    | T3c                      | `mem_vulkan`                            |
| `mem_localize`  | T3d (manual contingency) | `mem_localize` (manual)                 |
| `burn`          | T4                       | `burn`                                  |
| `matmul`        | T5                       | `matmul`                                |
| `pcie`          | T6                       | `pcie` (text snapshot `pcie-after.txt`) |
| `warm_rerun`    | T7                       | `warm_rerun`                            |
| `inference`     | T8 (optional)            | `inference-*` (if produced)             |
| `persistence`   | T9 (manual)              | (no receipt)                            |

T0.2 is the manual PRE-unlock enumeration (no receipt, SPEC-02 sT0.2); the
automated `intake` runs after T1. Code identifiers, receipt keys, log
paths, and this table all use the
semantic names; the T-numbers stay in prose headings for searchability.
`mem_*` is the memory suite — a completed run missing any of
the three automated members cannot ACCEPT (coverage rule below).

## Automated stages (gate.py → receipts/*.json → make-report.py)

One subsection per stage. **Receipt** names the receipt file and its key
fields; **PASS / WARN / FAIL** are the three verdict columns the old table
carried, verbatim. Cross-stage rules (Xid counting, stop flags, preflight,
coverage) follow in the paragraphs after the list.

### intake

- **Receipt**: `intake.json` (`memory_total_mib`, `target_gib`, `profile`, `brake`, `brake_class`)
- **PASS**: brake in {Not Active, Inactive, N/A, Not Supported} — dies only when the value **asserts** an active brake (contains `active`, contains neither `inactive` nor `not active`, and is not a pass spelling): catches verbose `Active (PWRBRK#)` / `Active: Sync Boost` spellings while `Inactive`/`Not Active (…)` never die; outcome recorded as `brake_class` (pass/asserting/unknown/unrecognized). An unreadable field (`unknown`: no brake line in `nvidia-smi -q` output) or unrecognized value warns loudly and is re-checked post-hoc in telemetry ∧ memory in unlock band; `target_gib` DERIVED from measured MiB (min(total/1024 − 2, 62/38)) — the bands accept ≥60000 MiB, which is only ~58.6 GiB, so a fixed 62 GiB allocation would false-fail a WARN-band card
- **WARN**: memory 60000–65535 MiB (8 GB SKU: unlocked, below the 65536 target) → report-side `intake-warn-band` warn (machine-enforced; CONDITIONAL per SPEC-03 §5, overridable)
- **FAIL**: brake Active (fix stand first); 30000–34000 MiB = single-rank (distinct die message); any other value outside the unlock bands

### idle

- **Receipt**: `idle.json` (`max/mean core,mem,power`)
- **PASS**: receipt written
- **WARN**: mean mem > 55 °C or mean core > 45 °C → `idle-hot` warn (machine-gated headroom over the SPEC-03 §2.2 fleet band 41–51/37–38 °C; CONDITIONAL unless accepted); other out-of-band values — human review vs SPEC-03 §2.2
- **FAIL**: — (informational; gate does not stop)

### mem_pattern

- **Receipt**: `mem_pattern.json` (`rc`, `xid_fatal`, `xid_noted`, `thermal_stop`, `gpu_gone`)
- **PASS**: `rc=0` ∧ `xid_fatal=0` ∧ `thermal_stop=0` ∧ `gpu_gone=0`
- **WARN**: — (none defined)
- **FAIL**: anything else (mismatched word = card reject)

### mem_retention

- **Receipt**: `mem_retention.json` (`rc`, `xid_fatal`, `xid_noted`, `thermal_stop`, `gpu_gone`)
- **PASS**: `rc=0` ∧ `xid_fatal=0` ∧ `thermal_stop=0` ∧ `gpu_gone=0` (same conjunction as mem_pattern)
- **WARN**: — (none defined)
- **FAIL**: anything else (retention mismatch)

### mem_vulkan

- **Receipt**: `mem_vulkan.json` (`rc`, `xid_fatal`, `xid_noted`, `retried`, `thermal_stop`, `gpu_gone`)
- **PASS**: `rc=0` ∧ `xid_fatal=0` ∧ `thermal_stop=0` ∧ `gpu_gone=0` (retry allowed; recorded in `retried`)
- **WARN**: — (none defined)
- **FAIL**: `thermal_stop=1` ∨ `gpu_gone=1` → **FAIL (supersedes INCOMPLETE)**. `xid_fatal>0` → FAIL (supersedes the rc=64 class). `rc` ∉ {0,64} → FAIL (card). retry is SKIPPED if a stop event fired (never restart a memory test after a thermal stop) or a fatal Xid was counted before the decision (fail fast — never burn a second memtest budget on a card that already Xid'd); `rc=64` **after the one automatic retry** → **INCOMPLETE(memtest-never-ran)** → CONDITIONAL that `--accept-warnings` cannot override

### burn

- **Receipt**: `burn.json` (`rc`, `tflops`, `tflops_verdict`, `tflops_gate`, `brake`, `xid_fatal`, `xid_noted`, `thermal_stop`, `gpu_gone`)
- **PASS**: `rc=0` ∧ `xid_fatal=0` ∧ `thermal_stop=0` ∧ `gpu_gone=0` ∧ `tflops_verdict=pass` (≥ 70 TFLOP/s sustained, normalized from tflop/s / tflops / GFLOP/s / GFLOPS output; floors apply at a ≥ 150 W cap; the dead-card floor < 20 at ANY cap)
- **WARN**: `tflops_verdict=warn` (60–70) or `unparsed` (regex missed → human reads log). At a cap < 150 W (`tflops_gate=informational`, SPEC-01 §2.2 degraded stand) any parsed number ≥ 20 records `warn`: the floors are 180 W-calibrated and do not gate — rc/Xid/thermal still do
- **FAIL**: `rc≠0` ∨ `thermal_stop=1` ∨ `gpu_gone=1` ∨ `xid_fatal>0` ∨ `tflops_verdict=fail` (< 60 at a ≥ 150 W cap, or < 20 at ANY cap — failed compute unlock ≈ 12, REFERENCE-NUMBERS §C: a dead tensor path is dead at every cap, so the < 150 W informational rule excuses throttling, not a broken card)

### matmul

- **Receipt**: `matmul.json` (`rc`, `warn`, `retried`, `xid_fatal`, `xid_noted`, `thermal_stop`, `gpu_gone`) + `logs/matmul/matmul.json`
- **PASS**: `rc=0` ∧ `warn` absent ∧ `thermal_stop=0` ∧ `gpu_gone=0`: deterministic ∧ in-band errors ∧ INT8=0 ∧ BF16 ≥ 100 TFLOPS (single-op probe, thresholds **inferred** — REFERENCE-NUMBERS §C band 164–193, failed unlock ≈ 12). matmul_check exits 0
- **WARN**: matmul_check exits **2** → receipt `rc=0 warn=bf16-speed` (BF16 80–100 TFLOPS, correctness holds), with no stop event
- **FAIL**: matmul_check exits 1, or `xid_fatal>0` (fatal supersedes **any** rc class, including the speed-warn one), or a stop event (`thermal_stop=1` ∨ `gpu_gone=1`) → FAIL: non-deterministic, NaN/Inf, out-of-band error, INT8≠0, BF16 < 80 TFLOPS, or watchdog kill. A failing exit is re-run **once** automatically (nondeterminism policy below) — but the retry is **skipped after a stop event or a fatal Xid** (the mem_vulkan rule: never restart work after a thermal kill; a first-attempt Xid is counted before the retry decision and fails fast instead of burning a second matmul run); second failure → FAIL

### pcie

- **Receipt**: `pcie-after.txt`
- **PASS**: (human diff vs `env/inventory.txt` pre-block: zero uncorrectable AER, zero replay delta)
- **WARN**: single correctable tick (record it)
- **FAIL**: climbing correctable / any uncorrectable (human verdict)

### warm_rerun

- **Receipt**: `warm_rerun.json` (`rc_memtest`, `rc_burn`, `xid_fatal`, `xid_noted`, `thermal_stop`, `gpu_gone`)
- **PASS**: `rc_memtest=0` ∧ `rc_burn=0` ∧ `xid_fatal=0` ∧ `thermal_stop=0` ∧ `gpu_gone=0` (no single `rc` key — the verdict reads the two sub-rcs)
- **WARN**: — (none defined)
- **FAIL**: anything else (heat-soak degradation)

### run

- **Receipt**: `run-summary.json` (`xid_total`=fatal count, `xid_noted`, `xid_proc`, `aborted`, `brake`, `shared_host`, `xid_counting`)
- **PASS**: `xid_total=0` ∧ `aborted=0`
- **WARN**: degraded-mode markers (`shared_host`>1, `xid_counting`=unavailable) → report-side warns (CONDITIONAL unless accepted); `profile=smoke` caps the verdict at **SMOKE** — `--accept-warnings` cannot turn it into ACCEPT, only a REJECT survives as-is
- **FAIL**: `xid_total>0` → REJECT regardless of stage outcomes; `aborted=1` with a hard-fail receipt or `xid_total>0` → REJECT (the die() path, or an Xid that fired between stages before any receipt could record it — a card signal, not missing evidence), `aborted=1` with neither → REJECT-INCOMPLETE (no card verdict exists)

Xid counting (classifier `classify_xid` in scripts/verdicts.py; cursor logic in gate.py): a dmesg line cursor marks the run start and advances
per stage, so receipts carry **per-stage** counts; the run summary counts from
the start marker. Lines are **classified** (SPEC-03 §3 machine classes):
**fatal** = 13, 31, 48, 79, 94, 95, 154 *and any unrecognized number*
(conservative) — these fail the stage and the run; **noted** = 43 (software
channel reset: recorded in `xid_noted`, never scored); **procedure** = 45,
119 (kill/GSP-timeout artifacts, including Xids our own cleanup can produce:
recorded in `xid_proc`, never scored; a non-zero `xid_proc` total still records an `xid-proc` warn — Xid 119 (GSP RPC timeout) is a real driver-reset event, so the report points at the reset ladder and the T3/T4 re-run (SPEC-03 §3), while 45 is usually our own kill artifact). Attribution filter: lines carrying a
`0000:xx:xx` token without our short bus id are excluded. `dmesg`
readability is a preflight requirement, and the ring-wrap guard dies if the
first dmesg line changes mid-run (the counting window would be unreliable).
Policy: **no automatic retries on fatal Xid** — under this gate's controlled
footprint an Xid 31/13 is a real signal (SPEC-03 §3); the only legitimate
"re-run" is after a *procedure* fault (e.g., our own over-sized allocation),
which must be fixed and re-run manually with the fix recorded.

Thermal/disappearance flags: the watchdog thread signals via **flag files**
(`receipts/.thermal_stop`, `receipts/.gpu_gone`), read at every receipt
site. A flag is set ONLY when a live workload was actually interrupted — a
temperature sample in the post-exit transient window (the card keeps rising
after a clean finish) is logged and ignored, so a healthy card on marginal
cooling cannot collect a phantom stop event. A stage that exits 0 after the
watchdog killed it is still a stop event — the flags, not the workload's
exit code, decide. Each watchdog instance owns a **per-thread stop Event**
and a reference to **its own workload**: a thread stuck in a hung
`nvidia-smi` past its join timeout is orphaned but disarmed — the next
stage's watchdog gets a fresh Event (no resurrection as a second sampler),
and the kill is scoped to the thread's own (exited) workload (no
cross-stage kill). Watchdog probes carry a 5 s timeout; a probe timeout is
logged and sampling continues — it is never mapped to `gpu_gone`, which
drives REJECT and demands a *confirmed* disappearance. Shutdown always runs
the memtest_vulkan orphan guard
(`pkill -f`): the binary re-execs into a detached grandchild that killpg
cannot reach and that would pin VRAM through every later stage.

Preflight hard requirements (clean abort, never a mid-stage traceback):
sole-GPU host, empty fleet-wide compute list, readable dmesg, and every
tool that a stage **scheduled in this run** needs — `bin/compare-vram`
(mem_pattern/mem_retention/warm_rerun), `bin/gpu-burn/gpu-burn` (burn/warm_rerun),
`scripts/qc/memtest-select.py` (mem_vulkan), and — when matmul is scheduled — a
`TG_MATMUL_PYTHON` (or `--matmul-python`) that exists and is executable (a
stale path is a missing tool like any other), plus the session helpers
`telemetry.py` (always launched) and `xid-watch.py` (launched whenever the
Xid channel is active). An unreadable `pci.bus_id` under
`--allow-shared-host` likewise dies at preflight instead of silently
skipping the compute-clean check. A missing tool dies at preflight
(→ REJECT-INCOMPLETE with a kit/MANIFEST.md pointer); the alternative is a
Python `FileNotFoundError` traceback at stage time, which says nothing
about the card. As a fallback behind preflight, `run_work` converts a
`FileNotFoundError` from any workload spawn into the same clean abort.
The sole-GPU and dmesg requirements are each relaxable deliberately:
`--allow-shared-host` targets one card of a multi-GPU host (workloads
pinned via `CUDA_VISIBLE_DEVICES`, Xid lines stay bus-filtered, the
compute-clean check is scoped to our card, and the report forces a
shared-host warn → CONDITIONAL), and `--allow-no-dmesg` runs without Xid
counting (`xid_counting=unavailable` in the run summary; the report warns
the channel is unverified — never silently zero). Every nvidia-smi probe in the gate is bounded by a 5 s
timeout (a wedged driver aborts the run cleanly; the watchdog's probe
timeouts are additionally never mapped to `gpu_gone`).

Coverage and correlation: **a test that did not record evidence did not
happen** — the report enforces this for hard-fail stages as described in
the verdict mapping above. And a FAILED burn whose intake receipt records
`brake_class` unknown/unrecognized is flagged in the report: a PWRBRK#
slot fault presents exactly as low TFLOPs, and the stand slot must be
checked before the finding is blamed on the card. An unknown/unrecognized
`brake_class` on a **completed** run records a warn (CONDITIONAL unless
accepted): the blind spot is marked in the verdict, never silent.
Hardware throttle reasons (`hw_` or `hw`-prefixed spellings) observed in
session telemetry record a `throttle-hw` warn — SPEC-03 §2.3 stop-class
thermal events the stage stop-flags may have missed.

Nondeterminism policy (matmul): cuBLAS bit-determinism on this stack is an
observed property [measured, club 4-card gate], not a guarantee. **Any
failing matmul_check run is retried once automatically** (nondeterminism,
out-of-band error, INT8!=0, or speed fail alike - a genuinely broken tensor
core reproduces, so the retry costs at most one run); a second failure is a
FAIL. Two exceptions: the retry is **skipped after a stop event** — never
restart work the watchdog just killed for a thermal/disappearance breach —
and **after a fatal Xid**, which is counted before the retry decision so a
first-attempt Xid fails fast instead of paying for a second workload run.
The mem_vulkan retry follows the same rules. The retry is recorded in the receipt (`retried`, plus `retry_artifact`
pointing at the second attempt's matmul.json). A mem_vulkan that passed on the
second attempt is PASS with `retried=1`: the first attempt's setup error is
recorded but does not colour the verdict.

## Manual stages (human verdict, recorded in REPORT.md checklist)

One subsection per manual stage: **Signal source**, then the human
**PASS / FAIL** columns.

### T0.1 — physical inspection

- **Signal source**: photos + SPEC-02 §T0.1 checklist
- **PASS**: inspection notes acceptable
- **FAIL**: condemned-mechanical findings (seller conversation)

### T0.2 — manual pre-unlock enumeration

- **Signal source**: manual, **pre-unlock**: lspci + stock `memory.total`
- **PASS**: ID 20c2/2082 ∧ stock band 7680–8704 / 9728–10752 MiB ∧ brake Not Active
- **FAIL**: 20b0, absent, or brake Active (fix stand first)

### T1 — unlock verification gate

- **Signal source**: SPEC-01 §4.4 gate (verify.sh, dmesg signature, module path)
- **PASS**: all four criteria
- **FAIL**: any missing criterion (diagnosis: SPEC-01 §4.4)

### T8 — Tier-B inference (optional)

- **Signal source**: recipe receipts; floors per SPEC-03 §5 (a machine-readable `inference-*` receipt, if present: FAIL → forced CONDITIONAL, never ACCEPT — and never masking a hard-fail REJECT or a core-missing REJECT-INCOMPLETE; WARN → CONDITIONAL; a receipt with `warn` set and rc=0 reads WARN-INFERENCE, never a clean PASS). **Informational split**: receipts carrying `informational: true` (the kit's non-acceptance serving recipes) never force a verdict — a FAIL records a warn instead; fingerprint.json keys are namespaced `t8_<metric>` (acceptance) vs `info_<receipt>_<metric>` (informational)
- **PASS**: ≥ floor (decode256 130 / decode900 112 / prefill 1750 / TTFT ≤ 250 ms)
- **FAIL**: < 0.9×floor with clean methodology; 0.9×floor..floor = WARN (re-check methodology)

### T9 — cold-boot persistence

- **Signal source**: post-cold-boot `verify.sh`
- **PASS**: unlocked geometry persists
- **FAIL**: unlock lost → blocks purchase until understood (stand/driver issue, not card damage)

## Change discipline

Changing any threshold or signal here requires updating, in the same commit:
the implementing line(s) in `scripts/gate.py` / `scripts/make-report.py` /
`scripts/matmul_check.py`, and the corresponding row in
[SPEC-03 §5](SPEC-03-ANALYSIS.md). The burn/matmul thresholds carry
`TFLOPS_FAIL/TFLOPS_WARN/TFLOPS_DEAD` (the dead-card floor: any cap),
`SPEED_FLOOR_BF16/SPEED_WARN_BF16`, and the
low-power informational cap `LOW_POWER_INFO_W` (150 W) as constants
precisely so the code and this table move together. The shared engine
lives in `scripts/verdicts.py` (constants, `classify_xid`, `classify_brake`,
`stage_verdict`); gate.py and make-report.py import it, and contract_sync fails if
either grows a local copy.

