#!/usr/bin/env python3
"""test_e2e_verdict.py - end-to-end: synthetic bundles -> real
make-report.py verdicts (the top-level mapping). Every case runs the
actual script as a subprocess, the same way an operator does. Run via
tests/contract_sync.py.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run():
    ok = {"rc": "0", "thermal_stop": "0", "gpu_gone": "0", "xid_fatal": "0"}
    warm_rerun = {"rc_memtest": "0", "rc_burn": "0", "xid_fatal": "0",
                  "thermal_stop": "0", "gpu_gone": "0"}
    fails_out = []

    def bundle(receipts):
        tmp = tempfile.mkdtemp(prefix="cs-e2e-")
        os.makedirs(os.path.join(tmp, "receipts"))
        os.makedirs(os.path.join(tmp, "telemetry"))
        for fn, content in receipts.items():
            if fn.startswith("telemetry/"):
                # raw file (CSV) for cases that exercise telemetry parsing
                with open(os.path.join(tmp, fn), "w") as fh:
                    fh.write(content)
                continue
            json.dump(content, open(os.path.join(tmp, "receipts", fn + ".json"), "w"))
        return tmp

    def run_report(ev, extra=()):
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "scripts", "make-report.py"),
                            ev, "--no-hash", *extra], capture_output=True, text=True)
        head = (r.stdout.splitlines() or ["?"])[0]
        return head.split("- verdict: ")[-1], r.stdout

    run_ok = {"xid_total": "0", "aborted": "0", "profile": "express"}
    all_ok = {"mem_pattern": dict(ok), "mem_retention": dict(ok),
              "mem_vulkan": {**ok, "retried": "0"},
              "burn": {**ok, "tflops": "77.2", "tflops_verdict": "pass"},
              "matmul": {"rc": "0", "retried": "0"},
              "warm_rerun": dict(warm_rerun), "run-summary": dict(run_ok)}
    cases = [
        # aborted gate + hard-fail receipt -> REJECT (the die() path)
        ({"mem_pattern": {**ok, "rc": "1"},
          "run-summary": {**run_ok, "aborted": "1"}}, "REJECT"),
        # aborted gate, no hard fails -> REJECT-INCOMPLETE (preflight death)
        ({"mem_pattern": dict(ok), "run-summary": {**run_ok, "aborted": "1"}},
         "REJECT-INCOMPLETE"),
        # clean completed run -> ACCEPT (express profile: no T8 downgrade)
        ({"mem_pattern": dict(ok), "mem_retention": dict(ok),
          "mem_vulkan": {**ok, "retried": "0"},
          "burn": {**ok, "tflops": "77.2", "tflops_verdict": "pass"},
          "matmul": {"rc": "0", "retried": "0"},
          "warm_rerun": dict(warm_rerun), "run-summary": dict(run_ok)}, "ACCEPT"),
        # no run-summary at all -> REJECT-INCOMPLETE
        ({"mem_pattern": dict(ok)}, "REJECT-INCOMPLETE"),
        # a FAILED inference receipt forces CONDITIONAL, never ACCEPT
        ({**all_ok, "inference-manual": {"rc": "1", "decode256": "95.0"}},
         "CONDITIONAL"),
        # regression guard: matmul xid_fatal=1 must FAIL the stage row
        ({**all_ok, "matmul": {"rc": "0", "retried": "0", "xid_fatal": "1"}},
         "REJECT"),
    ]
    # reduced-power informational TFLOPS -> warn-class CONDITIONAL
    # (never REJECT), overridable to ACCEPT by --accept-warnings.
    burn_info = {**all_ok,
                 "burn": {**ok, "tflops": "52.3", "tflops_verdict": "warn",
                          "tflops_gate": "informational"},
                 "run-summary": {**run_ok, "power_w": "125"}}
    cases.append((burn_info, "CONDITIONAL"))
    cases.append((burn_info, "ACCEPT", ["--accept-warnings"]))
    # --skip mem_pattern,mem_retention leaves only memtest_vulkan, which
    # single-rank fake-64GB cards PASS (REFERENCE-NUMBERS sB) - the exact
    # fraud vector mem_pattern exists for. The bundle can never read ACCEPT.
    skip_core = {"mem_vulkan": {**ok, "retried": "0"},
                 "burn": {**ok, "tflops": "77.2", "tflops_verdict": "pass"},
                 "matmul": {"rc": "0", "retried": "0"},
                 "warm_rerun": dict(warm_rerun), "run-summary": dict(run_ok)}
    cases.append((skip_core, "REJECT-INCOMPLETE"))
    # missing NON-core hard-fail stage (warm_rerun) -> per-stage warn -> CONDITIONAL
    skip_warm = {**all_ok}
    del skip_warm["warm_rerun"]
    cases.append((skip_warm, "CONDITIONAL"))
    # die-at-burn regression: aborted + hard fail stays REJECT even though
    # matmul/warm_rerun have no receipts (coverage must not downgrade the die path)
    die_burn = {**all_ok, "burn": {**ok, "rc": "1"},
                "run-summary": {**run_ok, "aborted": "1"}}
    del die_burn["matmul"]
    del die_burn["warm_rerun"]
    cases.append((die_burn, "REJECT"))
    # brake correlation: burn FAIL + intake brake_class unknown -> the report
    # must point at the stand slot (PWRBRK# presents as low TFLOPs)
    brake_note = {**die_burn, "intake": {"brake": "unknown",
                                         "brake_class": "unknown"}}
    cases.append((brake_note, "REJECT", [], "PWRBRK"))
    # a T8 FAIL is a soft signal - it must never mask a
    # hard-fail REJECT (was CONDITIONAL before the reorder) ...
    cases.append(({**all_ok, "burn": {**ok, "rc": "1"},
                   "inference-manual": {"rc": "1", "decode256": "95.0"}},
                  "REJECT"))
    # ... nor a core-missing REJECT-INCOMPLETE (the fraud vector).
    cases.append(({**skip_core, "inference-manual": {"rc": "1"}},
                  "REJECT-INCOMPLETE"))
    # unknown brake_class on a COMPLETED run -> marked warn
    # (CONDITIONAL, overridable by --accept-warnings - stages all ran)
    brake_unknown = {**all_ok, "intake": {"brake": "unknown",
                                          "brake_class": "unknown"}}
    cases.append((brake_unknown, "CONDITIONAL"))
    cases.append((brake_unknown, "ACCEPT", ["--accept-warnings"]))
    # T2 idle bands (SPEC-03 s2.2): mean > 55 C mem / > 45 C core is a
    # machine warn (CONDITIONAL, overridable); in-band values add nothing.
    idle_hot = {**all_ok, "idle": {"mean_core_c": "61.0",
                                   "mean_mem_c": "48.2",
                                   "max_core_c": "64", "max_mem_c": "51",
                                   "rows": "300", "minutes": "5"}}
    cases.append((idle_hot, "CONDITIONAL"))
    cases.append((idle_hot, "ACCEPT", ["--accept-warnings"]))
    idle_ok = {**all_ok, "idle": {"mean_core_c": "38.0",
                                  "mean_mem_c": "44.0",
                                  "max_core_c": "39", "max_mem_c": "46",
                                  "rows": "300", "minutes": "5"}}
    cases.append((idle_ok, "ACCEPT"))
    # Degraded modes: a shared-host or no-dmesg run summary forces a warn
    # (CONDITIONAL, overridable - the degraded mode is visible, not silent).
    shared = {**all_ok, "run-summary": {**run_ok, "shared_host": "4"}}
    cases.append((shared, "CONDITIONAL"))
    cases.append((shared, "ACCEPT", ["--accept-warnings"]))
    # A sole-GPU run records shared_host=1 (the gate writes the host GPU
    # count): that is the intended stand, not a degraded mode - it must
    # not warn (regression: the marker fired on every real gate run, so
    # no bundle could ever read ACCEPT-clean; CONTRACT run row: >1 warns).
    cases.append(({**all_ok, "run-summary": {**run_ok, "shared_host": "1"}},
                  "ACCEPT"))
    nodm = {**all_ok, "run-summary": {**run_ok,
                                      "xid_counting": "unavailable"}}
    cases.append((nodm, "CONDITIONAL"))
    cases.append((nodm, "ACCEPT", ["--accept-warnings"]))
    # xid_proc>0 (kill/GSP artifacts, never scored fatal) records a warn
    # pointing at the SPEC-03 s3 reset ladder (Xid 119 = GSP timeout).
    procs = {**all_ok, "run-summary": {**run_ok, "xid_proc": "2"}}
    cases.append((procs, "CONDITIONAL"))
    cases.append((procs, "ACCEPT", ["--accept-warnings"]))
    # T1 unlock band (SPEC-03 s5): 60000-65535 MiB is inside the unlock
    # band but below the 65536 target - machine-enforced CONDITIONAL
    # (overridable); the exact target reads clean.
    band = {**all_ok, "intake": {"memory_total_mib": "60000",
                                 "profile": "8gb(64GiB)"}}
    cases.append((band, "CONDITIONAL"))
    cases.append((band, "ACCEPT", ["--accept-warnings"]))
    cases.append(({**all_ok, "intake": {"memory_total_mib": "65536",
                                        "profile": "8gb(64GiB)"}}, "ACCEPT"))
    # Hardware throttle reasons in telemetry -> throttle-hw warn
    # (CONDITIONAL, overridable); software-only reasons add nothing.
    hw_tel = {"telemetry/session.csv":
              "timestamp,temperature.gpu,clocks_throttle_reasons.active\n"
              "0,40,Not Active\n1,62,HW Thermal Slowdown\n"}
    cases.append(({**all_ok, **hw_tel}, "CONDITIONAL"))
    cases.append(({**all_ok, **hw_tel}, "ACCEPT", ["--accept-warnings"]))
    sw_tel = {"telemetry/session.csv":
              "timestamp,temperature.gpu,clocks_throttle_reasons.active\n"
              "0,40,Not Active\n1,62,SW Power Cap\n"}
    cases.append(({**all_ok, **sw_tel}, "ACCEPT"))
    # Smoke profile: capped at SMOKE - never an ACCEPT, even with
    # --accept-warnings; a dead card still reads REJECT.
    smoke_ok = {
        "intake": {"profile": "8gb(64GiB)", "brake_class": "pass"},
        "mem_pattern": dict(ok),
        "burn": {**ok, "tflops": "77.2", "tflops_verdict": "pass"},
        "matmul": {"rc": "0", "retried": "0"},
        "run-summary": {**run_ok, "profile": "smoke"},
    }
    cases.append((smoke_ok, "SMOKE"))
    cases.append((smoke_ok, "SMOKE", ["--accept-warnings"]))
    cases.append(({**smoke_ok,
                   "burn": {**ok, "tflops": "12.0",
                            "tflops_verdict": "fail"}}, "REJECT"))
    # T8 warn band: rc=0 + warn machine-reads WARN-INFERENCE ->
    # CONDITIONAL, overridable to ACCEPT (round-16 fix: used to read
    # PASS, contradicting the CONTRACT T8 row).
    t8_warn = {**all_ok, "inference-smoke": {"rc": "0",
                                             "warn": "below-floor: decode256=131.0"}}
    cases.append((t8_warn, "CONDITIONAL"))
    cases.append((t8_warn, "ACCEPT", ["--accept-warnings"]))
    # Informational recipe crash (informational: true): records a warn
    # (overridable), NEVER the acceptance "T8 inference FAILED"
    # forced-CONDITIONAL.
    info_fail = {**all_ok,
                 "inference-nemotron-3.5-lightning-30b-a3b":
                     {"rc": "1", "informational": True,
                      "error": "health timeout"}}
    cases.append((info_fail, "CONDITIONAL"))
    cases.append((info_fail, "ACCEPT", ["--accept-warnings"]))
    # aborted with an asserting brake names the stand in the
    # reason (PWRBRK# stand slot fault, not a transient)
    cases.append(({"run-summary": {**run_ok, "aborted": "1",
                                   "brake": "Active (PWRBRK#)"}},
                  "REJECT-INCOMPLETE", [], "PWRBRK"))
    # An Xid between stages (no FAIL receipt) on an ABORTED run is a card
    # signal: xid_total>0 -> REJECT, never the missing-evidence class.
    cases.append(({"mem_pattern": dict(ok),
                   "run-summary": {**run_ok, "aborted": "1",
                                   "xid_total": "1"}}, "REJECT"))
    # ... and it outranks the aborted-stand-fault reading too: an asserting
    # brake names the stand ONLY when no Xid was recorded.
    cases.append(({"run-summary": {**run_ok, "aborted": "1",
                                   "brake": "Active (PWRBRK#)",
                                   "xid_total": "1"}}, "REJECT"))
    tmpdirs = []
    try:
        for case in cases:
            receipts, want = case[0], case[1]
            extra = case[2] if len(case) > 2 else []
            contains = case[3] if len(case) > 3 else None
            ev = bundle(receipts)
            tmpdirs.append(ev)
            got, out = run_report(ev, extra)
            if got != want:
                fails_out.append(f"e2e: bundle({sorted(receipts)}) extra={extra} "
                                 f"-> {got!r}, want {want!r}")
            if contains and contains not in out:
                fails_out.append(f"e2e: bundle({sorted(receipts)}) stdout missing "
                                 f"expected marker {contains!r}")
    finally:
        # a raised exception must not leak the tmp bundles
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)
    return fails_out, len(cases)

