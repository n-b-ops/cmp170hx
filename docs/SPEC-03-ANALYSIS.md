# SPEC-03 — Analysis, verification, and the verdict

> Status: specification · Input: the evidence bundle from SPEC-02 ·
> Output: `REPORT.md` with an ACCEPT / CONDITIONAL / REJECT verdict.
> Anchor numbers with sources: [REFERENCE-NUMBERS.md](REFERENCE-NUMBERS.md).

## 1. Principles

1. **A test that did not record evidence did not happen.** Every verdict line
   in §5 must trace to a receipt file.
2. **Separate card verdicts from stand verdicts.** Thermals, link width, and
   some failures are properties of the stand (airflow, slot, PSU). The
   isolation rules in §4 decide which side a symptom belongs to.
3. **Labels travel with numbers.** Every threshold below is marked
   [measured] (pinned receipt in a pinned source), [community-reported], or
   [inferred]. A verdict may not rest on untested numbers.
4. **Absence of evidence is not health.** A clean gate rules out faults under
   the gate's own profile (duration, footprint, pressure) — not every fault
   [club FAILURE-RISK, residual-risk note].
**Residual risk beyond the gate (open items).** These stay open by design;
none blocks a verdict, each is marked in the anchors it affects:

- No club-measured HBM bandwidth figure exists (community range only) —
  bandwidth stays informational until measured with a pinned method
  (candidate: a bandwidth mode on the mem_pattern harness).
- The FP64 burn anchor (6.2–6.31 TFLOPS) is wiki-community-sourced; a
  club-style receipt chain would upgrade it to measured.
- The 10 GB SKU (2082 → 40 GB) has no club-measured anchors; its bands are
  community-reported and labeled as such.
- The T8 vLLM patch set ships WITH the kit and is applied by install.sh
  at install time; it must be verified to
  serve offline before T8 is trusted on the stand (kit/inference).

## 2. Telemetry analysis

### 2.1 Schema

`scripts/telemetry.py` writes 1 Hz CSV (field names verified against
nvidia-smi — `temperature.memory`, not `memory.temperature`; the wrong name
silently produces empty columns [measured bug, club LESSONS §f.2]):

```text
timestamp, pci.bus_id, temperature.gpu, temperature.memory,
power.draw, power.limit, clocks.sm, clocks.mem, utilization.gpu,
memory.used, pcie.link.gen.current, pcie.link.width.current,
clocks_throttle_reasons.active, ecc.mode.current, ecc.errors.uncorrected.volatile.total,
ecc.errors.corrected.volatile.total
```

(The column set matches `scripts/telemetry.py`'s FIELDS exactly — the
GPU index is fixed per run via `--gpu N`, so it is not a column. Fields the
driver does not expose are recorded as `n/a` — the CSV column set is fixed so
parsers do not break.)

### 2.2 Idle baseline comparison (from T2)

| Signal      | Healthy band [measured, club fleet, 180 W cap, ducted airflow] | Reading outside band means                                     |
| ----------- | -------------------------------------------------------------- | -------------------------------------------------------------- |
| Core temp   | 37–38 °C                                                       | stand airflow problem, or heat-retaining card (warn)           |
| Memory temp | 41–51 °C                                                       | >55 °C at idle: investigate before load (warn)                 |
| Power draw  | 33–41 W                                                        | high idle draw: leakage (warn); re-check with persistence mode |

A card slightly outside these bands is not condemned — the bands come from one
fleet in one frame [club]; they gate *investigation*, not the verdict.
make-report automates the investigation trigger on the T2 **means** with
headroom over the fleet band: mean core > 45 °C or mean mem > 55 °C records a
`idle-hot` warn (CONDITIONAL unless accepted); other out-of-band readings
stay human review.

### 2.3 Load telemetry rules (T3/T4/T7/T8)

1. **Throttle reasons** (`clocks_throttle_reasons.active`):
   - `sw_power_cap` — expected under burn at 180 W. Normal.
   - `hw_thermal_slowdown` / any `hw_` reason — stop-level thermal event;
     charge to cooling (stand) unless it recurs with proven cooling (then card).
   - `hw_power_brake` / "HW Power Brake Slowdown: Active" — stand slot
     problem (PWRBRK#), never a card property [measured, club LESSONS §f].
2. **Peak temperature scoring** (at 180 W, real airflow):
   - core ≤ 71 °C, memory ≤ 71 °C — normal [measured band from club gate:
     peaks 56–63 °C core / 64–71 °C mem].
   - memory 72–80 °C — stand-limited; repeat T4 with better airflow before
     scoring the card.
   - any hit of 80 °C core / 85 °C memory — the watchdog should have killed
     the stage; treat as thermal stop event and analyze §4.
3. **Clocks**: sustained SM clock during -tc burn ≈ 1.1–1.2 GHz at 180 W
   [measured, club A/B context]; a card pinned near ~1140 MHz *with*
   `hw_power_brake` is the PWRBRK# signature.
4. **ECC counters**: informational only (synthesized status — SPEC-01 §4.6);
   never gate on them.

## 3. The Xid matrix

Consolidated from club incidents + wiki documentation [labels per row]:

| Xid      | Meaning                                                             | Class             | On our stand                                                                                                                                                                                                                                                                 |
| -------- | ------------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 13       | SM warp/global exception                                            | stop-and-verify   | Hard fail under the gate (same no-retry policy as Xid 31); see CONTRACT.md "Xid counting"                                                                                                                                                                                    |
| 45       | GPU wedged after SIGKILL of a live kernel                           | procedure fault   | Our watchdog must kill process *groups* gracefully; if 45 appears, fix procedure and re-run — do not score the card [wiki, medium confidence]                                                                                                                                |
| 119      | GSP RPC timeout (two flavors: 60 s/GSP_INIT_DONE, 6 s/GSP_RM_ALLOC) | recoverable       | Reset ladder (§4); under our gate it indicates stand/driver state, not card health — but re-run the stage after recovery [wiki]                                                                                                                                              |
| 31       | MMU fault (often `FAULT_INFO_TYPE_REGION_VIOLATION`)                | conditional       | Single occurrence during our *controlled* tests (no OOM pressure, no oversize allocations) = hard fail. (Club saw 31 only under host-OOM cascades, a driver WPR/PMA bug fixed in the pinned unlocker, or a >65k-token engine fault — none of which our gate stages trigger.) |
| 43       | GPU reset by software channel ("channel reset")                     | note-and-continue | Record and continue; verify with T5/T7 afterward [measured recoverable, club]                                                                                                                                                                                                |
| 48/94/95 | hardware-class errors (uncorrectable memory/bus)                    | stop-and-verify   | Any occurrence during load = hard fail                                                                                                                                                                                                                                       |
| 79       | GPU has fallen off the bus                                          | fatal             | Cold power cycle (mains ≥ 60 s per SPEC-01) → re-enumerate. If it recovers: isolation-test §4; recurrence on our stand = reject. Community-reported predisposition: Samsung-memory cards under sustained inference load [vendor/cmp-170hx-vllm] — see T0.1 HBM-vendor note   |
| 154      | UVM global fatal ("OS Reboot" action)                               | fatal             | Reboot required; module reload insufficient [measured]. After reboot, re-run T2+T3a+T4 before any verdict; the event itself, under our controlled footprint, is a hard fail                                                                                                  |

Rules:

- The Xid watcher log is authoritative (`xid/xid.log`), not memory. Correlate
  timestamps with telemetry.
- Non-Xid driver signatures with the same weight [wiki recovery]:
  `RmInitAdapter failed! (0x62:0x40:2028)` = WPR2 already up → cold cycle
  (60 s); `(0x62:0x65:2028)` GSP init timeout → reset ladder; BAR0 reading
  `0xffffffff` = card off-bus. Recovery ladder: driver reload → FLR →
  remove/rescan (+ restore bus mastering: `setpci -s <bdf> COMMAND=0x0546`)
  → cold cycle → physical removal (last resort).
- An Xid cluster right after an *our-side* OOM (e.g., mis-sized T8 attempt) is
  a procedure fault: fix the procedure, re-run the stage; do not score the
  card on it (but the re-run must be clean).
- Xid 31 folklore ("harmless single occurrence") comes from mining-era
  stock-window usage; **under our gate it is not harmless** — the gate never
  allocates beyond verified geometry.

## 4. Stand-vs-card isolation

When any stage fails ambiguously (hang, disappearance, thermal stop, link
drop), change **one variable per shutdown** [club QC isolation matrix]:

| Swap                          | If failure follows the …                     |
| ----------------------------- | -------------------------------------------- |
| Card → other slot/riser       | card: card is the suspect                    |
| Other card → our slot/power   | slot/riser/power path: stand is the suspect  |
| Card → other power lead       | lead: stand is the suspect                   |

On a seller's stand there is rarely a second card; substitutes that still
work: different slot, different power lead, riser removal (direct slot), and
power-cap reduction (125 W) as a stress-reduction probe. **A failure that
persists across slot, power lead, AND reduced power cap is attributable to
the card.** A thermal failure that persists with proven airflow (paper-strip
test + temp deltas across the fins) is attributable to the card.

Special cases:

- **Xid 79 / D3cold**: only a true cold cycle (mains off ≥ 30 s, standby rails
  discharged [measured, club]) re-enumerates the card. Never host-side PCI
  "remove" games.
- **Link trains x8**: if the stand board shares lanes (neighboring slot
  occupied), x8 is topology, not fault [measured, club]. Score Gen2 width
  accordingly; T8 thresholds assume x16 — note the caveat in the report.
- **PLX-switch hosts**: two cards behind one switch need the serialized BAR1
  resize (in the pinned unlocker patch set). Single-card stands: irrelevant.

## 5. Stage pass/fail gates

> **The machine-checkable subset of this table is defined authoritatively in
> [CONTRACT.md](CONTRACT.md)** (receipt keys, exact thresholds, and where each
> rule is enforced — gate.py live, make-report.py post-hoc). The table below
> adds the human-judgment rows on top. If prose and CONTRACT disagree,
> CONTRACT wins.

| Stage | Signal                          | PASS                                                                                                | WARN (conditional)                                                                                                                                      | FAIL                                                                                                                                                                                  |
| ----- | ------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T0.2  | PCI ID                          | 20c2 / 2082                                                                                         | —                                                                                                                                                       | 20b0 or absent                                                                                                                                                                        |
| T0.2  | HW Power Brake Slowdown         | Not Active                                                                                          | — (fix stand first)                                                                                                                                     | Active after stand fix                                                                                                                                                                |
| T1    | Unlocked memory                 | = target MiB (65536/40960)                                                                          | 60000–65535 (8 GB SKU) — machine-enforced: report-side `intake-warn-band` warn → CONDITIONAL                                                                                     | stock band, ≈32768 (single-rank), MISSING                                                                                                                                             |
| T1    | verify.sh + dmesg signature     | exit 0 + full signature set                                                                         | Gen1 only (Gen2 service issue — stand note)                                                                                                             | FAILED strings, no signature                                                                                                                                                          |
| T2    | Idle baseline                   | in §2.2 band                                                                                        | slightly out — investigate                                                                                                                              | rising idle / >55 °C mem                                                                                                                                                              |
| T3a/b | compare_vram                    | exit 0 (both runs)                                                                                  | —                                                                                                                                                       | any mismatched word                                                                                                                                                                   |
| T3c   | memtest_vulkan                  | wrapper exit 0                                                                                      | persistent setup error (rc=64 after the automatic retry) → INCOMPLETE: CONDITIONAL that `--accept-warnings` cannot override — the memory test never ran | any error line; faulty-section prompt; a stop event (thermal/disappearance) FAILs and **skips the retry** — never restart a memory test after a thermal kill (CONTRACT.md mem_vulkan) |
| T4    | gpu-burn -tc                    | 0 errors, ≥70 TFLOP/s sustained                                                                     | 60–70 TFLOP/s (investigate clocks/throttle)                                                                                                             | errors, hang, <60 TFLOP/s; <20 at ANY cap (failed unlock ≈ 12)                                                                                                                        |
| T4    | Peak temps @180 W               | ≤71/71 °C core/mem                                                                                  | 72–80 °C mem (stand-limited; retry airflow)                                                                                                             | thermal stop reached                                                                                                                                                                  |
| T5    | Matmul fingerprint + BF16 speed | no NaN/Inf; ≈2× ref values; INT8=0; 3× bit-identical; BF16 ≥ 100 TFLOPS (single-op probe, inferred) | BF16 80–100 TFLOPS (correctness holds)                                                                                                                  | non-deterministic, NaN/Inf, INT8≠0, BF16 < 80 TFLOPS                                                                                                                                  |
| T6    | PCIe/AER delta                  | zero uncorrectable, zero replay delta                                                               | single correctable tick (record)                                                                                                                        | climbing correctable / any uncorrectable                                                                                                                                              |
| T7    | Warm rerun                      | T3a + T4-short PASS                                                                                 | —                                                                                                                                                       | any degradation vs cold                                                                                                                                                               |
| T8    | Inference (if run)              | ≥ floors: 130 / 112 / 1750 / 250 ms                                                                 | 0.9×floor..floor (re-check methodology: warmup discard, usage-object tokens, 3 reps)                                                                    | < 0.9×floor with clean methodology                                                                                                                                                    |
| T9    | Cold-boot persistence           | unlocked geometry persists                                                                          | —                                                                                                                                                       | unlock lost (driver/stand issue — not card, but blocks purchase until understood)                                                                                                     |

**Card-level hard fails** (any one ⇒ REJECT regardless of other results):
T3a/T3b mismatch; T3c card error (a persistent *setup* error, rc=64, is
INCOMPLETE → CONDITIONAL, not overridable by `--accept-warnings`, per
CONTRACT.md — never a silent pass); T4 errors/hang
or sustained TFLOP/s < 60; Xid 48/94/95/79/154 under controlled load; Xid
31/13 under controlled load — **no automatic retries**: under this gate an Xid
31/13 is a real signal; the only legitimate re-run is after a *procedure*
fault (our own over-sized allocation), fixed and documented; T5
non-deterministic, NaN, or BF16 < 80 TFLOPS; T7 degradation; single-rank
32 GB geometry when a 64 GB card was the deal.

## 6. Verdict classes

- **ACCEPT** — every stage PASS (warn-only notes allowed and recorded).
- **CONDITIONAL** — all hard-fail stages PASS; warn entries remain that the
  buyer accepts with eyes open (e.g., 10 GB SKU with community-only anchors;
  stand-limited thermals with a committed home-cooling plan; T8 not run).
  The report must state exactly which warnings are accepted and the planned
  re-validation at home (repeat T2–T7 in the destination chassis).
- **REJECT** — any hard fail, or an unresolved stand/card attribution with
  no path to isolation on site.
- **REJECT-INCOMPLETE** — no card verdict exists: missing/unparseable
  run-summary, core memory stages not run (coverage), or an aborted run
  with no hard fail (machine: CONTRACT.md verdict mapping).
- **SMOKE** — a `--profile smoke` run caps every non-REJECT outcome: a
  quick-alive check, never an acceptance verdict (machine: CONTRACT.md).

Negotiation guidance (informational, not engineering): a REJECT on T3 memory
integrity is not a price conversation — the card's headline feature (64 GB)
is what fails. A CONDITIONAL on stand-limited thermals is re-testable at
home under buyer-controlled cooling; agree on a return window.

## 7. Report format

`make-report.py` generates `REPORT.md` in the bundle root:

```text
# CMP 170HX acceptance report — <date> — <card-tag>
Verdict: ACCEPT | CONDITIONAL | REJECT | REJECT-INCOMPLETE | SMOKE
## Card identity        (SKU, PCI ID, VBIOS, serial photo refs, SM count, stock/unlocked MiB)
## Stand                (host, slot, cooling, PSU line, cap, link gen/width)
## Software             (OS, kernel, driver, unlocker commit, module path, tool pins + hashes)
## Stage results        (table from §5 with measured values, pass/warn/fail, receipt refs)
## Incidents            (Xid/events timeline, aborts, re-runs and why)
## Deviations           (profile used, skipped stages + why, stand limitations)
## Evidence index       (file list + sha256)
```

Redaction rules before sharing with the seller or anyone else (club
publication discipline): no hostnames, no private IPs, no full serials or
UUIDs (suffix only), no unrelated logs. Photos: crop seller's other hardware.

## 8. Post-purchase plan (buyer's bench)

The seller-stand verdict is provisional. At home, within the return window:

1. Rebuild the stand per SPEC-01 on the buyer's own hardware (this also
   re-validates the unlock reproducibility — T9 discipline).
2. Re-run T2, T3a, T3c (full duration), T4, T5, T7.
3. Only then move to real workloads; first long model loads stay supervised
   (watch Xid + memory temps through the first full-VRAM fill).
4. Register the card in the buyer's fleet health-watch: idle baseline + the
   same signals/cadence rules as club HEALTH-WATCH (per-session Xid check,
   post-incident tensor gate).

