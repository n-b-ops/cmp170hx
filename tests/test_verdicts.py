#!/usr/bin/env python3
"""test_verdicts.py - stage_verdict and classifier unit cases.

Every case mirrors a CONTRACT table cell or classifier rule and is
asserted against the REAL scripts/verdicts.py (loaded by the
contract_sync entry point, never copied). Run via tests/contract_sync.py.
"""
OK = {"rc": "0", "xid_count": "0", "thermal_stop": "0", "gpu_gone": "0"}

# (stage receipt name, receipt dict, expected verdict prefix)
CASES = [
    ("mem_pattern", {**OK, "gib": "62"}, "PASS"),
    ("mem_pattern", {**OK, "gib": "62", "rc": "1"}, "FAIL"),
    ("mem_retention", dict(OK), "PASS"),
    ("mem_retention", {**OK, "rc": "1"}, "FAIL"),
    ("mem_vulkan", {**OK, "retried": "0"}, "PASS"),
    ("mem_vulkan", {**OK, "retried": "1"}, "PASS"),  # retry-pass is PASS
    ("mem_vulkan", {"rc": "64", "retried": "1", "xid_count": "0",
                    "thermal_stop": "0", "gpu_gone": "0"},
     "INCOMPLETE(memtest-never-ran)"),
    # stop events supersede the setup-error class
    ("mem_vulkan", {"rc": "64", "retried": "1", "xid_count": "0",
                    "thermal_stop": "1", "gpu_gone": "0"}, "FAIL"),
    ("mem_vulkan", {"rc": "64", "retried": "1", "xid_count": "0",
                    "thermal_stop": "0", "gpu_gone": "1"}, "FAIL"),
    ("mem_vulkan", {**OK, "rc": "1", "retried": "1"}, "FAIL"),
    ("burn", {**OK, "tflops": "77.2", "tflops_verdict": "pass"}, "PASS"),
    ("burn", {**OK, "tflops": "65.0", "tflops_verdict": "warn"},
     "WARN-TFLOPS(warn:65.0)"),
    ("burn", {**OK, "tflops": "n/a", "tflops_verdict": "unparsed"},
     "WARN-TFLOPS(unparsed:n/a)"),
    ("burn", {**OK, "tflops": "11.9", "tflops_verdict": "fail"},
     "FAIL(tflops=11.9)"),
    ("burn", {**OK, "tflops": "77.2", "tflops_verdict": "pass",
              "thermal_stop": "1"}, "FAIL"),
    ("matmul", {"rc": "0", "retried": "0"}, "PASS"),
    ("matmul", {"rc": "0", "warn": "bf16-speed", "retried": "1"},
     "WARN-BF16-SPEED"),
    ("matmul", {"rc": "1", "retried": "1"}, "FAIL"),
    ("matmul", {"rc": "skipped", "reason": "no torch"}, "SKIPPED"),
    ("warm_rerun", {"rc_memtest": "0", "rc_burn": "0", "xid_count": "0",
                    "thermal_stop": "0", "gpu_gone": "0"}, "PASS"),
    ("warm_rerun", {"rc_memtest": "0", "rc_burn": "1", "xid_count": "0",
                    "thermal_stop": "0", "gpu_gone": "0"}, "FAIL"),
    ("warm_rerun", {"rc_memtest": "0", "rc_burn": "0", "xid_count": "1",
                    "thermal_stop": "0", "gpu_gone": "0"}, "FAIL"),
    # per-stage Xid gate for every xid-carrying stage
    ("mem_pattern", {**OK, "gib": "62", "xid_count": "1"}, "FAIL"),
    ("mem_retention", {**OK, "xid_count": "1"}, "FAIL"),
    ("mem_vulkan", {**OK, "retried": "0", "xid_count": "1"}, "FAIL"),
    ("burn", {**OK, "tflops": "77.2", "tflops_verdict": "pass",
              "xid_count": "1"}, "FAIL"),
    # the stop-event preempt must hold for mem_pattern/mem_retention too
    ("mem_pattern", {**OK, "gib": "62", "thermal_stop": "1"}, "FAIL"),
    ("mem_retention", {**OK, "gpu_gone": "1"}, "FAIL"),
    # matmul is xid-gated like the other load stages
    ("matmul", {"rc": "0", "retried": "0", "xid_fatal": "1"}, "FAIL"),
    ("matmul", {"rc": "0", "warn": "bf16-speed", "xid_fatal": "0"},
     "WARN-BF16-SPEED"),
    # stop-event parity - gpu_gone fails burn even at rc=0, and
    # matmul receipts carry the stop flags (report preempt + gate die agree)
    ("burn", {**OK, "tflops": "77.2", "tflops_verdict": "pass",
              "gpu_gone": "1"}, "FAIL"),
    ("matmul", {"rc": "0", "retried": "0", "thermal_stop": "1"}, "FAIL"),
    ("matmul", {"rc": "0", "retried": "0", "gpu_gone": "1"}, "FAIL"),
    # below LOW_POWER_INFO_W the TFLOPS number is informational
    # (warn class even under the floors - never FAIL on the number alone)
    ("burn", {**OK, "tflops": "52.3", "tflops_verdict": "warn",
              "tflops_gate": "informational"}, "WARN-TFLOPS(warn:52.3)"),
    # Dead-card floor holds even in the low-power informational regime:
    # a failed compute unlock (~12 TFLOP/s) is a broken card at ANY cap.
    ("burn", {**OK, "tflops": "12.0", "tflops_verdict": "fail",
              "tflops_gate": "informational"}, "FAIL(tflops=12.0)"),
    # T8 warn band (0.9x..floor): rc=0 + warn reads WARN-INFERENCE,
    # never a clean PASS (CONTRACT T8 row; the informational recipes
    # ride the same branch).
    ("inference-smoke", {"rc": "0", "warn": "below-floor: decode256"},
     "WARN-INFERENCE(below-floor: decode256)"),
]

# Xid classification (SPEC-03 s3 machine classes; verdicts.py is authoritative)
XID_CASES = [
    ("NVRM: Xid (PCI:0000:01:00): 31, ErrCtx", "01:00", "fatal"),
    ("NVRM: Xid (PCI:0000:02:00): 48, foreign", "01:00", None),
    ("NVRM: Xid 43 no bus token", "01:00", "noted"),
    ("NVRM: Xid (PCI:0000:01:00): 45, wedged", "01:00", "proc"),
    ("NVRM: Xid 119 GSP timeout", "01:00", "proc"),
    ("NVRM: Xid 999 unknown number", "01:00", "fatal"),  # conservative
    ("NVRM: Xid 0000:01:00.0: 43 sysfs-style", "01:00", "noted"),
    ("NVRM: Xid 0000:09:00.0: 79 foreign sysfs", "01:00", None),
    ("random kernel line", "01:00", None),
]

# classify_brake (verdicts.py) - shared by gate (live intake death) and
# make-report (aborted-run stand-fault reason); both sides agree by
# construction, these lock the spellings.
BRAKE_CASES = [
    ("Not Active", "pass"),
    ("Inactive", "pass"),
    ("N/A", "pass"),
    ("Not Supported", "pass"),
    ("", "pass"),
    ("unknown", "unknown"),
    ("Active (PWRBRK#)", "asserting"),
    ("Active: Sync Boost", "asserting"),
    ("Not Active (PWRBRK#)", "unrecognized"),
    ("gibberish", "unrecognized"),
]

# parse_tflops (verdicts.py) - comma-tolerant gpu-burn throughput parsing;
# malformed tokens skip instead of crashing, nothing parseable = None.
TFLOPS_CASES = [
    ("unfused wall 76.5 TFLOP/s", 76.5),
    ("74800 GFLOP/S at the end", 74.8),
    ("74,800 GFLOP/s", 74.8),
    ("bad 1.2.3 tflops then 80 tflops", 80.0),
    ("no throughput line here", None),
]

# classify_tflops (verdicts.py) - the burn threshold pipeline as ONE unit:
# dead floor at any cap, 180 W-calibrated bands, low-power flattening.
# Directly unit-tested here instead of only through the receipt->e2e path.
CLASSIFY_TFLOPS_CASES = [
    (None, False, "unparsed"),
    (12.0, False, "fail"),
    (12.0, True, "fail"),      # dead floor ignores the low-power excuse
    (19.99, True, "fail"),
    (20.0, True, "warn"),      # informational from here up at low power
    (52.3, True, "warn"),
    (55.0, False, "fail"),     # below FAIL at a gating cap
    (65.0, False, "warn"),
    (70.0, False, "pass"),
    (77.2, False, "pass"),
]


def run(verdicts, gate):
    """Assert every case against the shared engine; returns failures."""
    fails = []
    for name, receipt, expected in CASES:
        got = verdicts.stage_verdict(name, receipt)
        if str(got) != expected:
            fails.append(f"stage_verdict({name}, {receipt}) = {str(got)!r}, "
                         f"want {expected!r}")
    for line, bus, want in XID_CASES:
        got = gate.classify_xid(line, bus)
        if got != want:
            fails.append(f"classify_xid({line!r}, {bus!r}) = {got!r}, want {want!r}")
    for raw, want in BRAKE_CASES:
        got = verdicts.classify_brake(raw)
        if got != want:
            fails.append(f"classify_brake({raw!r}) = {got!r}, want {want!r}")
    for text, want in TFLOPS_CASES:
        got = verdicts.parse_tflops(text)
        if got != want:
            fails.append(f"parse_tflops({text!r}) = {got!r}, want {want!r}")
    for tf, lp, want in CLASSIFY_TFLOPS_CASES:
        got = verdicts.classify_tflops(tf, lp)
        if got != want:
            fails.append(f"classify_tflops({tf}, low_power={lp}) = {got!r}, "
                         f"want {want!r}")
    return fails

