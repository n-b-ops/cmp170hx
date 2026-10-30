# kit/inference - the Tier-B T8 stack, packaged

**Two serving paths.** (1) The **acceptance recipe** - the five files
below (`recipe.json`, `requirements.lock`, `fetch.sh`, `install.sh`,
`smoke.py`) - is the receipt-chained Qwen3.8-27B recipe of record with
CONTRACT floors. (2) The **informational recipes** in `recipes/` (plus
`fetch-model.sh`, `install-model.sh`, `smoke-model.py`) ride the same
protocol but never gate. Status: both implemented and test-locked
(tests/test_thresholds.py); what remains is the at-home kit build and
the stand-side runs.

The acceptance path - five files, no egress on the stand:

| File                | Runs            | What it does                                                                                                                                                                                                                                              |
| ------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipe.json`       | -               | Pins of record, copied verbatim from the club recipe (model revisions, runtime `69ba4d06`, vLLM 0.27.1 stack) plus the serve env (DFlash2 k=7, GPU_UTIL 0.90), the benchmark protocol, and the CONTRACT floors                                            |
| `requirements.lock` | -               | Byte-exact copy of the club lock - its sha256 must equal the recipe's `requirements_sha256` (enforced by `tests/test_thresholds.py`)                                                                                                                      |
| `fetch.sh`          | at home         | Egress builder: locked wheels + torch, runtime clone at the pin, and the three model snapshots via the club's own `prepare_pinned_models.py`; writes `manifest-inference.sha256`                                                                          |
| `install.sh`        | stand (offline) | Verifies the manifest and lock sha, builds `venv-inference` (python3.12), installs `--no-index`, applies the vLLM dflash2 backport patches, links `curand.h`, and hard-fails any version mismatch vs `recipe.json`; `--check` verifies without installing |
| `smoke.py`          | stand (GPU)     | Serves via the runtime's own `single-user/start_qwen.sh`, benchmarks with the club's `live_benchmark.py` under its fail-closed telemetry guard, and writes `receipts/inference-smoke.json` (rc, decode256, decode900, prefill, ttft)                      |

## Sequence

1. Home: `kit/inference/fetch.sh --kit-root <usb-stage>` (network; peak
   ~40 GB staged, ~25 GB carried - MANIFEST item 9's Tier-B estimate).
2. Carry `wheelhouse-inference/`, `model/`, `manifest-inference.sha256` on the stick.
3. Stand: `kit/inference/install.sh --check` after the MANIFEST s4 restore,
   then the full `install.sh`.
4. Stand (airflow on, 180 W cap asserted): `kit/inference/smoke.py`.
   The receipt lands in the gate's evidence layout; `make-report.py`
   already knows the keys. rc=1 below 0.9x a floor (CONDITIONAL per
   CONTRACT); 0.9x..floor records rc=0 plus a `warn` field.

## Informational serving recipes (recipes/)

Non-acceptance models ride the same rails. Each `recipes/<key>.json`
carries the model repo/revision policy, an engine pin, the vLLM serve
argv (`@MODEL_DIR@` substituted at launch), the benchmark protocol,
ANCHORS with evidence labels, and cautions:

| Recipe | Engine | Anchors (label) |
| ------ | ------ | --------------- |
| `qwen3.6-35b-a3b-fp8.json` | vLLM 0.28.0 (exact) | 355.7 tok/s short-context (community self-report); 153.8/135.9 @16k/64k (lab sweep) |
| `nemotron-3.5-lightning-30b-a3b.json` | vLLM >=0.28.0 | 227.7/217.9 @16k/64k, 1,306 agg @32k×16 (lab sweep) — the fastest model measured on this card |

| Script | Runs | Purpose |
| ------ | ---- | ------- |
| `fetch-model.sh --recipe X` | home (egress) | model snapshot (resolved revision recorded to REVISION.txt) + engine wheels into `wheelhouse-<key>/` |
| `install-model.sh --recipe X` | stand (offline) | manifest check for the key + dedicated `venv-<key>` + engine version gate |
| `smoke-model.py --recipe X` | stand (GPU) | `vllm serve` per the recipe argv + the club protocol + `receipts/inference-<key>.json` |

Each recipe gets its **own** `venv-<key>` - informational engines pin
different vLLM versions than the acceptance stack and cannot share
`venv-inference` (or each other).

Receipts carry the same metric keys (`decode256`, `decode900`,
`prefill`, `ttft`) so cross-MODEL and cross-CARD comparison works; in
`fingerprint.json` they are namespaced `info_<receipt>_<metric>` (the
acceptance recipe writes `t8_<metric>` - the two can never collide).
rc=0 on protocol completion — informational models never gate; a
crashed server still records rc=1, which the report shows as an
overridable info-inference warn, never the acceptance T8 CONDITIONAL.
The anchors were measured at different prompt shapes (16k/64k
coding corpus, 300 W): our standard three cases compare within OUR
protocol only, and 180 W sessions should expect proportionally lower
absolute tok/s. Anchor sources: the vendored lab sweep
(`vendor/cmp-170hx-docs`) and the research notes in `tmp/`.
License note: the lab-docs vendor has **no explicit upstream license**
at its pin - treat it as reference-only inside this repo/kit and verify
before redistributing the bundle beyond your own use.

## Safety and cautions

- Forced airflow before load - the card is passive; the vendor guard
  stops at core 80 C / memory 85 C / missing telemetry / any Xid.
- `gpu_memory_utilization` 0.95 crashes this stack - the pinned env
  uses 0.90 (community-measured; see recipe cautions).
- Record host load with every result: the 47-tok/s outlier was host
  contention plus pinned KV, not the card (smoke.py captures loadavg).
- The serving recipe is validated Hynix-only; Samsung-memory cards
  reportedly threw Xid 79 under sustained inference.
- **Driver matters for DFlash2 on sm80**: community reports on driver
  610.43.02 saw runtime Xid 31 wedges within minutes ([HyperQwen
  #72](https://github.com/syv-ai/HyperQwen/issues/72),
  [#98](https://github.com/syv-ai/HyperQwen/issues/98), both closed);
  a third box on 610.57.04 with the same image and W4A16-AutoRound-fast
  could [not reproduce
  either](https://github.com/syv-ai/HyperQwen/blob/main/docs/reproductions/cmp-170hx-64gb.md).
  Prefer 610.43.03 (club-validated) or 610.57.04; any Xid 31 during
  smoke stops the run (fail-closed guard) and is fatal in the gate.
- W4A16/W8A16 **Marlin** checkpoints wedge GA100 at load time (Xid 31) -
  only the pinned AutoRound + DFlash2 path is receipt-chained.

Pins are canonical in `PINS.md` / this recipe; the club vendor copy is
the source of record and the test suite fails on any divergence.

## Maintenance note: why the scripts stay bash

`fetch.sh`/`install.sh` and the per-model variants are deliberately
bash: manifest verification, venv setup, and offline pip installs are
shell-idiomatic, and `install*.sh --check` verifies the actual installed
kit at stand-side run time. Convert to a single Python entry point only
if a trigger fires: the manifest/version-gate logic grows another edge
case (a second fallback, `~=` specifiers), the kit must be built on
macOS/Windows, or the four scripts start drifting apart.

