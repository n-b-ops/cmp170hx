# CMP 170HX — used-card acceptance testing

Working repo for evaluating a used NVIDIA CMP 170HX (GA100 mining card with the
community memory unlock) **before purchase**, on a test stand provided by the
seller. The card currently mines on the seller's rig; this repo defines the
stand, the tests, and the verdict rules.

What must be proven on the stand, before money changes hands:

1. The card **is the SKU we think** (PCI ID, subsystem, board P/N).
2. The unlock path **reproduces from pinned software** (not seller magic).
3. The unlocked HBM region — the entire reason to buy this card — is
   **electrically sound** under dense patterns and sustained load (the
   dominant used-card risk: field failures appear **only above the 8 GiB
   mining window**).
4. Compute (tensor cores) is **deterministic and in the measured band**.
5. Thermals under load are consistent with survival in the buyer's chassis,
   separated from stand-airflow effects.
6. The evidence, not anyone's opinion, decides: ACCEPT / CONDITIONAL / REJECT.

## The plan

The end-to-end plan, top to bottom. Phases 2-4 are the card-specific
journey; each links to its detailed procedure.

1. **Build the acceptance pipeline** - *done*. Gate, verdict engine,
   report, fingerprints, offline kit, CI parity; every rule contract-locked
   by the test suite.
2. **Rented-instance battle test** - *next*. Validate the scripts and the
   T8 kit against a rented 170HX (SPEC-01 s8: smoke -> standard -> report
   -> T8); the first card becomes the fingerprint reference for card #2.
   Its debrief is the next review round.
3. **Seller-stand session** - the actual card under test: stand
   requirements and bring-up (SPEC-01 s2/s4), the pre-visit checklist
   (SPEC-01 s7), then the full gate on the physical card (SPEC-02).
4. **Purchase decision** - the evidence decides: ACCEPT / CONDITIONAL /
   REJECT (CONTRACT.md; SPEC-03 s5-6).
5. **Home re-validation** inside the return window (SPEC-03 s8) - a clean
   gate rules out faults under the gate's own profile, not every fault.

## Documents

| Document                                                     | Purpose                                                                                                              |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| [PINS.md](PINS.md)                                           | **Canonical submodule/tool/driver pins** (vendor roles live here, not in per-doc copies)                             |
| [docs/SPEC-01-PROVISIONING.md](docs/SPEC-01-PROVISIONING.md) | The test stand: host/cooling/power requirements, OS + driver + unlock install, offline test kit, §8 rented instances |
| [docs/SPEC-02-TESTING.md](docs/SPEC-02-TESTING.md)           | Test catalog T0–T9 (sequence in §0.0): exact procedures, parameters, durations                                       |
| [docs/SPEC-03-ANALYSIS.md](docs/SPEC-03-ANALYSIS.md)         | Telemetry analysis, Xid matrix, acceptance gates, report format, residual risk                                       |
| [docs/CONTRACT.md](docs/CONTRACT.md)                         | **Authoritative stage contract**: exact pass/warn/fail signals enforced by gate.py and make-report.py                |
| [docs/REFERENCE-NUMBERS.md](docs/REFERENCE-NUMBERS.md)       | Every anchor with its evidence label; §0 carries the dated decisions                                                 |
| [docs/design.md](docs/design.md)                             | Design rationale: why Python, watchdog invariants, comment policy                                                    |
| [docs/VENDORS.md](docs/VENDORS.md)                           | What comes from where: every vendor's artifacts and where the pipeline uses them                                     |
| [kit/MANIFEST.md](kit/MANIFEST.md)                           | Offline test-kit manifest (what to download and carry to the seller)                                                 |

## Executable parts

```text
scripts/
├── gate.py           single-card acceptance gate (Python): runs T-stages, thermal watchdog thread
│                     kills the live workload group, class-aware Xid counting, aborts still write
│                     a run-summary, writes JSON receipts into an evidence bundle
├── verdicts.py       shared verdict engine: CONTRACT constants, Xid/brake classifiers, stage_verdict
├── telemetry.py      1 Hz CSV logger: temps, power, clocks, throttle reasons, PCIe, ECC
├── xid-watch.py      timestamped kernel watcher: NVRM / Xid / AER / SEC2, Xid-class tagged
├── matmul_check.py   tensor-core fingerprint + determinism + BF16 throughput gate (T5)
├── make-report.py    evidence bundle → REPORT.md with the acceptance verdict table + fingerprint.json
├── compare-fingerprints.py  reference-vs-candidate fingerprint diff (flags deltas >5%)
└── setup-container.sh rented-container preflight: visibility, driver whitelist, unlock band, dmesg
```

The gate reuses building blocks from the pinned `club-170hx` submodule
(`vendor/club-170hx/scripts/qc/`: `compare_vram.cu`, `memtest-select.py`,
`pcie-check.sh`; and `matmul_check.py` descends from the club `tensor-gate.sh`)
rather than forking them. Build or place external binaries
(`gpu-burn`, `memtest_vulkan`) next to the scripts per
[kit/MANIFEST.md](kit/MANIFEST.md) before running.

**T5 needs torch**: plain `python3` on a fresh stand has none — build the kit
venv ([kit/MANIFEST.md §5](kit/MANIFEST.md)) and export
`TG_MATMUL_PYTHON=<venv>/bin/python`, or T5 is recorded as SKIPPED (verdict
CONDITIONAL per [CONTRACT.md](docs/CONTRACT.md)).

## Pinned sources (git submodules, under `vendor/`)

Eight submodules; the **canonical pin list and each vendor's role live in
[PINS.md](PINS.md)** — per-doc copies were removed because they
drifted. External tool pins (not submodules; carried in the kit):
[kit/MANIFEST.md](kit/MANIFEST.md).

The ninfer, vllm, benchmarks, and qwen-recipe pins sit on local **`rumdl-tables`** branches (upstream
commit + one markdown-table-alignment commit, per the vendor tables-only
formatting policy in `.rumdl.toml`): re-pin to upstream when theirs lands an
equivalent fmt commit. Until then these commits exist only locally — the kit
bundle carries them; a plain `git clone --recurse-submodules` from a remote
would not (this repo has no remote today).

TODO: re-pin the rumdl-tables vendors (ninfer/vllm/benchmarks/qwen-recipe)
to upstream when an equivalent table-format commit lands there, and delete
the local `rumdl-tables` branches at that point. New vendor repos: run the table alignment INSIDE the repo
(`rumdl fmt -e MD060 .`, committed on `rumdl-tables`) — the parent-level
`fmt .` scan does not reliably pick up tables in newly added submodules.

## Evidence labels

Every factual claim in the specs carries one of these labels, following the
convention of the club repo:

- **measured** — reproduced from a pinned receipt in a pinned source above;
- **community-reported** — published by the community without a verifiable receipt chain;
- **inferred** — our reasoning from measured facts;
- **untested** — plausible but never exercised; verify before relying on it.

## Repo hygiene

- Markdown formatting: `.rumdl.toml` (repo-local override, first-line marker -
  same opt-out as kb) is what CI enforces via `rumdl check -c .rumdl.toml .` and
  `rumdl fmt --check -c .rumdl.toml .` (org basic-checks template, recursive
  from the repo root); locally `rumdl fmt -c .rumdl.toml .` applies the same
  fixes. The config lints vendor/ TABLES ONLY (the rumdl-tables branch
  scheme), excludes runtime dirs, and disables MD013 (long command lines;
  same call as ops.rules). Do NOT delete it: without the config, `fmt .`
  dirties the submodule working trees (restore with
  `git -C vendor/<sub> checkout -- .`). CI (`.gitea/workflows/ci.yml`)
  also runs the contract-sync suite on every PR.
- Scripts stay compile-clean and syntax-clean via GLOBS, so new files
  cannot be missed: `python3 -m py_compile scripts/*.py tests/*.py
  kit/inference/*.py` and `bash -n scripts/*.sh kit/inference/*.sh`
  (the bash helpers: setup-container.sh and the kit's fetch/install
  pair, including the per-model variants).
- CONTRACT/code sync is enforced: `python3 tests/contract_sync.py` must pass
  before any commit that touches gate.py, verdicts.py (the shared verdict
  engine), make-report.py, telemetry.py, matmul_check.py, or CONTRACT.md
  (verdict cases, row markers, threshold numbers - see the test
  docstring). It is a thin entry point over the per-family modules
  `tests/test_{verdicts,thresholds,watchdog,fingerprint,e2e_verdict}.py`.
- Never commit `evidence/`, `bin/`, or `logs/` (gitignored): they are runtime
  artifacts from a stand session.

## Safety rules (non-negotiable during tests)

1. Forced airflow through the heatsink before any load; the card is passive.
2. Stop at **80 °C core** or **85 °C memory** — the gate enforces this with a
   watchdog, not just post-hoc checks.
3. Stop on **any Xid**, GPU disappearance, or verification mismatch.
4. Never test a card that shares a power domain with the seller's mining rig
   while the rig is running.
5. Full cold power-off (mains removed, ≥ 60 s per SPEC-01) between rig removal
   and stand install, and whenever a card drops off the bus.

The complete non-negotiable set - six rules, including the sole-GPU stand
requirement and the Ctrl+C escape - is [SPEC-02 §0.2](docs/SPEC-02-TESTING.md).
