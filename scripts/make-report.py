#!/usr/bin/env python3
"""make-report.py - build REPORT.md from a gate.py evidence bundle.

Implements the stage contract in docs/CONTRACT.md together with gate.py:

- FAIL      -> REJECT (hard fails, or xid_total > 0)
- INCOMPLETE-> CONDITIONAL, NOT overridable by --accept-warnings (mem_vulkan setup
              error that never produced a memory-test result; missing
              run-summary = crashed/aborted run)
- WARN/SKIP -> CONDITIONAL, overridable to ACCEPT by --accept-warnings
- PASS      -> contributes to ACCEPT

Usage: make-report.py <evidence-dir> [--card-tag TAG] [--accept-warnings]
                      [--no-hash]
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telemetry import FIELDS as TELEMETRY_FIELDS  # noqa: E402
from verdicts import (HARD_FAIL_STAGES, classify_brake,  # noqa: E402
                      stage_verdict)

# The columns telemetry_stats() reads must exist in the logger's schema
# (telemetry.py FIELDS; SPEC-03 s2.1 documents the same set): a rename
# on either side fails HERE instead of silently yielding empty stats.
_TELEMETRY_READ = ("temperature.gpu", "temperature.memory", "power.draw",
                   "clocks.sm", "clocks_throttle_reasons.active")
assert all(c in TELEMETRY_FIELDS for c in _TELEMETRY_READ), \
    "make-report reads a column outside telemetry.py's schema"


def load_receipts(ev):
    out = {}
    for p in sorted(glob.glob(os.path.join(ev, "receipts", "*.json"))):
        name = os.path.basename(p).replace(".json", "")
        try:
            with open(p) as f:
                out[name] = json.load(f)
        except Exception as e:  # noqa: BLE001
            out[name] = {"_error": str(e), "rc": "parse-error"}
    return out


def telemetry_stats(ev):
    stats = {}
    for p in glob.glob(os.path.join(ev, "telemetry", "*.csv")):
        with open(p) as f:
            r = csv.reader(f)
            header = next(r, None)
            if not header:
                continue
            idx = {h: i for i, h in enumerate(header)}
            rows = [row for row in r if len(row) == len(header)]

        def col(name):
            i = idx.get(name)
            if i is None:
                return []
            vals = []
            for row in rows:
                try:
                    vals.append(float(row[i]))
                except ValueError:
                    pass  # header artifacts, n/a columns
            return vals

        # Distinct active throttle reasons (excluding the quiet value):
        # evidence for the report's hw-throttle correlation - a HW-level
        # reason is a stop-class thermal event per SPEC-03 s2.3 even when
        # no stage receipt caught it.
        reasons = set()
        i = idx.get("clocks_throttle_reasons.active")
        if i is not None:
            for row in rows:
                for tok in row[i].split(","):
                    t = tok.strip().lower()
                    if t and t not in ("not active", "n/a"):
                        reasons.add(t)
        stats[os.path.basename(p)] = {
            "samples": len(rows),
            "max_core_c": max(col("temperature.gpu"), default=None),
            "max_mem_c": max(col("temperature.memory"), default=None),
            "max_power_w": max(col("power.draw"), default=None),
            "min_sm_clock": min(col("clocks.sm"), default=None),
            "throttle_reasons": sorted(reasons),
        }
    return stats


def build_fingerprint(rc, tel, ev, card_tag):
    """Measured invariants for card-vs-card comparison (fingerprint.json).

    Only stable measured numbers land in "measured" (burn sustained
    TFLOP/s, matmul speeds and max errors, T8 throughputs when present);
    thermal peaks are reported separately in thermal_context - they
    track the cooling, not the silicon, and are informational in
    compare-fingerprints.py.
    """
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    run = rc.get("run-summary", {})
    t0 = rc.get("intake", {})
    burn = rc.get("burn", {})
    measured = {}
    tf = _num(burn.get("tflops"))
    if tf is not None:
        measured["t4_tflops"] = tf
    pw = _num(run.get("power_w"))
    if pw is not None:
        measured["t4_power_w"] = pw
    # matmul.json: prefer the receipt's retry artifact, then the first run
    mj = None
    ra = str(rc.get("matmul", {}).get("retry_artifact", ""))
    for rel in [r for r in (ra, "logs/matmul/matmul.json") if r]:
        p = os.path.join(ev, rel)
        if os.path.exists(p):
            try:
                with open(p) as fh:
                    mj = json.load(fh)
            except ValueError:
                mj = None
            if mj:
                break
    torch_ver = cuda_ver = None
    if mj:
        torch_ver = mj.get("torch")
        cuda_ver = mj.get("cuda")
        for dt in ("bf16", "fp16", "tf32", "int8"):
            d = mj.get("dtypes", {}).get(dt, {})
            v = _num(d.get("tflops"))
            if v is not None:
                measured[dt + "_tflops"] = v
            v = _num(d.get("max_abs_err"))
            if v is not None:
                measured[dt + "_max_abs_err"] = v
    # Inference throughput receipts, when present. Keys are namespaced
    # so the acceptance recipe and the informational recipes can never
    # collide (a plain shared key silently dropped whichever receipt
    # loaded last): t8_<metric> for the acceptance receipt,
    # info_<receipt>_<metric> for informational ones (informational:
    # true in the receipt).
    for name, r in rc.items():
        if not name.startswith("inference"):
            continue
        prefix = ("info_" + name + "_") if r.get("informational") else "t8_"
        for k in ("decode256", "decode900", "prefill", "ttft"):
            v = _num(r.get(k))
            if v is not None:
                measured[prefix + k] = v
    mem = _num(run.get("memory_total_mib"))
    if mem is None:
        mem = _num(t0.get("memory_total_mib"))
    session = max(tel.items(), key=lambda kv: kv[1]["samples"], default=("", {}))[1]
    fp = {
        "schema": 1,
        "generated": str(datetime.date.today()),
        "card_tag": card_tag,
        "profile": run.get("profile", ""),
        "torch": torch_ver,
        "cuda": cuda_ver,
        "sku": {"memory_total_mib": mem, "profile": t0.get("profile", "")},
        "measured": measured,
        "thermal_context": {k: session.get(k) for k in
                            ("max_core_c", "max_mem_c", "max_power_w")
                            if session.get(k) is not None},
    }
    return fp


def compute_verdict(rc, sv, missing, ev, accept_warnings, tel=None):
    """Bundle-level verdict per CONTRACT.md's priority chain.

    Pure: no printing, no markdown, no sys.exit. Returns (verdict,
    reason, incompletes); incompletes is None unless the completed-run
    path ran (it drives the --strict-exit code-3 distinction). tel is
    make-report's telemetry_stats() output; hardware-level throttle
    reasons found there record a warn (SPEC-03 s2.3).
    """
    run = rc.get("run-summary", {})
    if "run-summary" not in rc or "_error" in run or "xid_total" not in run:
        return ("REJECT-INCOMPLETE",
                "no, unparseable, or malformed run-summary receipt (missing "
                "xid_total): the gate did not complete, or the final receipt "
                "was truncated/hand-edited", None)
    if str(run.get("aborted", "0")) == "1" and not [
            k for k, v in sv.items()
            if v.startswith("FAIL") and any(k.startswith(s) for s in HARD_FAIL_STAGES)] \
            and str(run.get("xid_total", "0")) == "0":
        # CONTRACT: aborted with no hard fail and no Xid = no card verdict
        # (REJECT-INCOMPLETE); a hard fail or Xid falls through to REJECT -
        # a found defect beats missing evidence, even on the aborted path.
        reason = ("gate aborted (aborted=1) with no hard-fail stage receipt: "
                  "no card verdict exists - rerun the gate")
        # If the brake was asserting at intake, name the stand as the cause:
        # PWRBRK# aborts are stand slot faults, not transients. Shared
        # classifier (verdicts.py) - same decision as gate.py's live check.
        if classify_brake(str(run.get("brake", ""))) == "asserting":
            reason = ("gate aborted at intake: HW Power Brake Slowdown is "
                      "'" + str(run.get("brake", "")) + "' - PWRBRK# stand "
                      "slot fault (~88 W cap), not the card; fix the stand "
                      "(different slot/board) and rerun")
        return ("REJECT-INCOMPLETE", reason, None)
    reason = ""
    fails = [k for k, v in sv.items() if v.startswith("FAIL") and any(k.startswith(s) for s in HARD_FAIL_STAGES)]
    warns = [k for k, v in sv.items() if v.startswith("WARN") or v == "SKIPPED"]
    incompletes = [k for k, v in sv.items() if v.startswith("INCOMPLETE")]
    # Roadmap s7 CONDITIONAL triggers, machine-enforced:
    # a 10 GB SKU carries community-only anchors, and a non-express run
    # without Tier-B evidence leaves the workload level unexercised.
    t0 = rc.get("intake", {})
    if "10gb" in str(t0.get("profile", "")).lower():
        warns.append("10gb-sku: community-only anchors (REFERENCE-NUMBERS)")
    if t0.get("brake_class") in ("unknown", "unrecognized"):
        # Fail-open with the blind spot MARKED, not silent:
        # stages all ran, so this is overridable by --accept-warnings.
        warns.append("brake-" + str(t0["brake_class"]) +
                     ": PWRBRK# check unreadable at intake - verify the "
                     "stand slot manually")

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    # T1 unlock band (SPEC-03 s5, machine-enforced): the unlock bands
    # accept >=60000 MiB, but the 8 GB SKU target is 65536 - a card in
    # between unlocked to less than advertised. CONDITIONAL,
    # overridable (same source order as build_fingerprint: run-summary
    # first, the intake receipt as fallback).
    mem_mib = _f(run.get("memory_total_mib"))
    if mem_mib is None:
        mem_mib = _f(t0.get("memory_total_mib"))
    if mem_mib is not None and 60000 <= mem_mib < 65536:
        warns.append("intake-warn-band: memory.total=" + str(int(mem_mib)) +
                     " MiB (8 GB SKU WARN band, target 65536 - "
                     "CONDITIONAL per SPEC-03 s5)")
    # T2 idle bands (SPEC-03 s2.2): machine-gated on the MEANS with
    # headroom over the fleet band - a single max blip in the idle
    # window must not condemn the stand; everything else stays human
    # review.
    idle = rc.get("idle", {})
    imem, icore = _f(idle.get("mean_mem_c")), _f(idle.get("mean_core_c"))
    if imem is not None and imem > 55:
        warns.append("idle-hot: mean memory " + str(idle.get("mean_mem_c")) +
                     " C at idle (SPEC-03 s2.2: investigate before load)")
    if icore is not None and icore > 45:
        warns.append("idle-hot: mean core " + str(idle.get("mean_core_c")) +
                     " C at idle (SPEC-03 s2.2: stand airflow or "
                     "heat-retaining card)")
    # Degraded-mode markers (gate-recorded, CONTRACT run row) are
    # warns - the degraded mode stays visible in the verdict, never
    # silent.
    try:
        gpus_host = int(run.get("shared_host", 1) or 0)
    except (TypeError, ValueError):
        # Unreadable marker: warn rather than pass silently (the gate
        # writes an integer count, so this is a hand-edited receipt).
        gpus_host = 2
    if gpus_host > 1:
        warns.append("shared-host: " + str(run.get("shared_host")) +
                     " GPUs on the host - Xid attribution is "
                     "bus-filtered, but thermal/PSU confounds cannot "
                     "be excluded (SPEC-01 s2.4; --allow-shared-host)")
    if str(run.get("xid_counting", "dmesg")) == "unavailable":
        warns.append("xid-unavailable: dmesg unreadable "
                     "(--allow-no-dmesg) - Xid channel unverified; "
                     "residual risk increased")
    proc_x = _f(run.get("xid_proc"))
    if proc_x is not None and proc_x > 0:
        # Never scored as fatal, but never silent either: 45 is usually
        # our own kill artifact, while 119 (GSP RPC timeout) is a real
        # driver-reset event with its own procedure.
        warns.append("xid-proc=" + str(run.get("xid_proc")) +
                     ": kill/GSP-timeout artifacts recorded - if any is "
                     "Xid 119 (GSP RPC timeout), run the reset ladder and "
                     "re-run T3/T4 (SPEC-03 s3)")
    hw = sorted({r for s in (tel or {}).values()
                 for r in s.get("throttle_reasons", [])
                 if "hw_" in r or r.startswith("hw ")})
    if hw:
        warns.append("throttle-hw: " + ", ".join(hw) +
                     " in telemetry - hardware-level thermal events "
                     "(SPEC-03 s2.3); correlate with stage receipts and "
                     "check stand cooling")
    profile = str(run.get("profile", ""))
    smoke = profile == "smoke"
    has_t8 = (glob.glob(os.path.join(ev, "logs", "inference*"))
              or glob.glob(os.path.join(ev, "receipts", "inference*")))
    if profile and profile not in ("express", "smoke") and not has_t8:
        warns.append("T8-not-run: inference level unexercised (Tier B optional)")
    xid_total = run.get("xid_total", "?")
    # Acceptance vs informational inference receipts (CONTRACT T8 note):
    # only the acceptance recipe's FAIL forces CONDITIONAL; an
    # informational recipe that crashed (informational: true) records a
    # warn - context, not an acceptance criterion.
    t8_fail = [k for k, v in sv.items()
               if k.startswith("inference") and v.startswith("FAIL")
               and not rc.get(k, {}).get("informational")]
    for k in [k for k, v in sv.items()
              if k.startswith("inference") and v.startswith("FAIL")
              and rc.get(k, {}).get("informational")]:
        warns.append("info-inference: " + k + " protocol failure - "
                     "informational recipe (context, not an acceptance "
                     "fail; see its receipt)")
    # Coverage (CONTRACT): core memory stages missing on a completed run
    # -> no card verdict (single-rank fakes pass memtest_vulkan and fail
    # the CUDA test - REFERENCE-NUMBERS sB); non-core missing -> per-stage
    # warns; real hard fails keep their REJECT.
    core_missing = [] if smoke else (
        [s for s in missing if s in ("mem_pattern", "mem_retention", "mem_vulkan")]
        if str(run.get("aborted", "0")) == "0" else [])
    if missing and not core_missing and not smoke \
            and str(run.get("aborted", "0")) == "0":
        # Completed runs only: a die() mid-stage legitimately leaves
        # later stages unrun (CONTRACT: aborted runs are exempt).
        warns.extend(s + "-not-run: no receipt in bundle" for s in missing)
    # Priority: a found defect (fails/xid) beats missing evidence (core
    # coverage) beats a soft workload failure (inference). A T8 FAIL is never a
    # card REJECT, but it must never downgrade or mask one either.
    if fails or str(xid_total) != "0":
        return ("REJECT", "", incompletes)
    if smoke:
        # A smoke run can never be an acceptance verdict: idle/mem_retention/mem_vulkan/warm_rerun
        # are profile-skipped by design, so core coverage does not apply
        # and --accept-warnings must not manufacture an ACCEPT.
        return ("SMOKE",
                "smoke profile: quick-alive check, not an acceptance run "
                "(no full-VRAM memtest, no retention check, no heat-soak "
                "rerun; " + str(len(warns)) + " warn(s) in the stage table)"
                " - rerun --profile standard for a purchase verdict",
                incompletes)
    if core_missing:
        return ("REJECT-INCOMPLETE",
                "core memory test(s) not run: " + ", ".join(core_missing) +
                " - no card verdict exists without the CUDA-side memory "
                "test (--skip/--only cannot produce an ACCEPT)", incompletes)
    if t8_fail:
        # T8 is optional Tier B, so a FAIL there is not a card REJECT -
        # but a failed workload level must never read ACCEPT.
        return ("CONDITIONAL",
                "T8 inference FAILED (" + ", ".join(t8_fail) +
                ") - workload level not functional; verdict forced to CONDITIONAL",
                incompletes)
    if incompletes:
        # INCOMPLETE is NOT overridable by --accept-warnings
        return ("CONDITIONAL", reason, incompletes)
    if warns:
        return ("ACCEPT" if accept_warnings else "CONDITIONAL", reason, incompletes)
    return ("ACCEPT", reason, incompletes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence_dir")
    ap.add_argument("--card-tag", default="card0")
    ap.add_argument("--accept-warnings", action="store_true",
                    help="ACCEPT despite WARN/SKIPPED stages (REJECT and INCOMPLETE stand regardless)")
    ap.add_argument("--no-hash", action="store_true", help="skip evidence file hashing")
    ap.add_argument("--strict-exit", action="store_true",
                    help="REJECT exits 2 (default: REJECT exits 0, because the report "
                         "itself was generated fine). CONDITIONAL and ACCEPT always exit 0; "
                         "REJECT-INCOMPLETE always exits 1 regardless of this flag.")
    args = ap.parse_args()
    ev = args.evidence_dir
    if not os.path.isdir(ev):
        sys.exit("no such directory: " + ev)

    rc = load_receipts(ev)
    tel = telemetry_stats(ev)
    # Coverage: a hard-fail stage with NO receipt is
    # evidence that did not happen; --skip/--only must not be able to
    # bypass the gate into ACCEPT. Displayed for every bundle; the verdict
    # effect is applied only to COMPLETED runs (aborted=0) below - a
    # die() mid-stage legitimately leaves later stages unrun and is
    # already mapped by the aborted rules.
    missing = [s for s in HARD_FAIL_STAGES
               if not any(k.startswith(s) for k in rc)]
    sv = {}
    for name, r in rc.items():
        v = stage_verdict(name, r)
        if v is not None:
            sv[name] = str(v)

    # No run-summary, an unparseable one (truncated mid-write), or one
    # missing xid_total = no completed session: never ACCEPT on an
    # empty or foreign bundle, never treat an unknown Xid count as zero
    # (compute_verdict maps all three to REJECT-INCOMPLETE).
    verdict, reason, incompletes = compute_verdict(rc, sv, missing, ev,
                                                   args.accept_warnings, tel)
    fp = build_fingerprint(rc, tel, ev, args.card_tag)
    session = max(tel.items(), key=lambda kv: kv[1]["samples"], default=("", {}))[1]

    lines = []
    a = lines.append
    a("# CMP 170HX acceptance report - {} - {}".format(datetime.date.today(), args.card_tag))
    a("")
    a("**Verdict: {}**".format(verdict))
    if reason:
        a("")
        a(reason)
    if verdict == "ACCEPT" and args.accept_warnings:
        w = [k for k, v in sv.items() if v.startswith("WARN") or v == "SKIPPED"]
        if w:
            a("")
            a("(warnings accepted by operator: {})".format(", ".join(w)))
    a("")
    a("## Stage results")
    a("")
    a("| stage | verdict | receipt data |")
    a("| ----- | ------- | ------------ |")
    for name, r in rc.items():
        detail = ", ".join("{}={}".format(k, v) for k, v in r.items() if k != "ts")
        a("| {} | {} | {} |".format(name, sv.get(name, "-"), detail))
    t6_path = os.path.join(ev, "receipts", "pcie-after.txt")
    start_marker = os.path.join(ev, "receipts", ".run_start")
    # stale-proof: show pcie only when it belongs to THIS session (mtime >= the
    # .run_start marker gate.py writes at session start; gate.log cannot be
    # used - it is appended after pcie, so the old comparison never fired).
    if os.path.exists(t6_path) and (not os.path.exists(start_marker) or
                                    os.path.getmtime(t6_path) >= os.path.getmtime(start_marker)):
        a("| pcie-after | see receipts/pcie-after.txt | human diff vs env/inventory.txt (CONTRACT.md pcie) |")
    a("")
    if missing:
        a("**Missing hard-fail stage receipts**: " + ", ".join(missing) +
          " - evidence that did not happen (--skip/--only coverage; see verdict note)")
        a("")
    if (sv.get("burn", "").startswith("FAIL")
            and rc.get("intake", {}).get("brake_class") in
            ("unknown", "unrecognized")):
        a("**Note**: burn FAILED while the brake state was "
          + str(rc.get("intake", {}).get("brake_class"))
          + " at intake - a PWRBRK# slot fault presents exactly as low "
          "TFLOPs; check the stand slot before blaming the card")
        a("")
    a("## Telemetry peaks")
    a("")
    a("| file | samples | max core C | max mem C | max W | min SM clock |")
    a("| ---- | ------- | ---------- | --------- | ----- | ------------ |")
    for fn, s in tel.items():
        a("| {} | {} | {} | {} | {} | {} |".format(fn, s["samples"], s["max_core_c"], s["max_mem_c"], s["max_power_w"], s["min_sm_clock"]))
    a("")
    if session:
        a("- Session telemetry peak: core {} C, mem {} C, {} W".format(session.get("max_core_c"), session.get("max_mem_c"), session.get("max_power_w")))
    all_reasons = sorted({r for s in tel.values()
                          for r in s.get("throttle_reasons", [])})
    if all_reasons:
        a("- Throttle reasons observed: " + ", ".join(all_reasons))
    if fp.get("torch"):
        a("- T5 matmul stack: torch " + str(fp.get("torch")) +
          " (correctness bands calibrated on torch 2.13.0+cu130; a "
          "different build shifts them, REFERENCE-NUMBERS sC)")
    if "run-summary" in rc:
        run = rc["run-summary"]
        a("- Xid total during run: {}".format(run.get("xid_total", "?")))
        a("- Thermal stop events: {}; GPU disappearance: {}".format(run.get("thermal_stop", "?"), run.get("gpu_gone", "?")))
    a("")
    a("## Fingerprint")
    a("")
    a("Measured invariants written to `fingerprint.json` - compare cards via")
    a("`scripts/compare-fingerprints.py` ref.json candidate.json (flags deltas > 5%).")
    a("")
    a("## Manual checklist (fill in)")
    a("")
    t8_files = sorted(glob.glob(os.path.join(ev, "logs", "inference*")))
    t1_dmesg = glob.glob(os.path.join(ev, "logs", "**", "dmesg-unlock.log"), recursive=True)
    a("- [ ] T0.1 physical inspection photos (photos/) - notes:")
    t1_note = "see " + ", ".join(os.path.relpath(p, ev) for p in t1_dmesg) if t1_dmesg else "SPEC-01 s4.4 - dmesg signature:"
    a("- [ ] T1 unlock verification gate - " + t1_note)
    a("- [ ] T9 cold-boot persistence - result:")
    t8_note = ("see " + ", ".join(os.path.basename(f) for f in t8_files)) if t8_files else "not run (Tier B optional)"
    a("- [ ] T8 inference - " + t8_note)
    a("")
    a("## Residual risk (fixed footer, SPEC-03 s1.4)")
    a("")
    a("This report rules out faults under the gate's own profile (duration,")
    a("footprint, pressure). It does not rule out every fault. Re-verify at")
    a("home within the return window (SPEC-03 s8).")
    a("")
    a("## Evidence index")
    a("")
    if args.no_hash:
        for root, _dirs, files in os.walk(ev):
            for f in sorted(files):
                a("- " + os.path.relpath(os.path.join(root, f), ev))
    else:
        for root, _dirs, files in os.walk(ev):
            for f in sorted(files):
                p = os.path.join(root, f)
                # Chunked: a bundle can carry multi-GB memtest raw logs or
                # T8 model weights - reading whole files into memory to
                # hash them is a real resource problem on the stand.
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                a("- {} (sha256:{})".format(
                    os.path.relpath(p, ev), h.hexdigest()[:12]))

    out = os.path.join(ev, "REPORT.md")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    if fp.get("measured"):
        with open(os.path.join(ev, "fingerprint.json"), "w") as f:
            json.dump(fp, f, indent=2)
    print("wrote {} - verdict: {}".format(out, verdict))
    if reason:
        print(reason)
    for label, keys in (("hard-fail", [k for k, v in sv.items() if v.startswith("FAIL")]),
                        ("incomplete", [k for k, v in sv.items() if v.startswith("INCOMPLETE")]),
                        ("warn/skipped", [k for k, v in sv.items() if v.startswith("WARN") or v == "SKIPPED"])):
        if keys:
            print("{} stages:".format(label), ", ".join(keys))
    if missing:
        print("not-run (no receipt):", ", ".join(missing))
    if (sv.get("burn", "").startswith("FAIL")
            and rc.get("intake", {}).get("brake_class") in
            ("unknown", "unrecognized")):
        print("note: burn FAILED with brake", 
              rc.get("intake", {}).get("brake_class"),
              "- PWRBRK# slot fault presents as low TFLOPs; check the stand slot")
    if verdict == "REJECT-INCOMPLETE":
        sys.exit(1)
    if args.strict_exit and verdict == "REJECT":
        sys.exit(2)
    if args.strict_exit and verdict == "CONDITIONAL" and incompletes:
        sys.exit(3)  # CONDITIONAL driven by an INCOMPLETE stage (memory test
                     # never ran) - distinct from a warn-only CONDITIONAL


if __name__ == "__main__":
    main()

