#!/usr/bin/env python3
"""verdicts.py - the shared stage-verdict engine for the CMP 170HX gate.

Single source of truth for everything CONTRACT.md defines as a machine
signal: the threshold constants, the Xid classifier, the power-brake
classifier, and stage_verdict() (receipt -> PASS/WARN/FAIL/INCOMPLETE/
SKIPPED). scripts/gate.py enforces the live column during the run,
scripts/make-report.py re-derives verdicts from the receipts afterward,
and tests/contract_sync.py imports THIS module directly - a local copy in
either script fails the source-marker check, so the two cannot drift
apart silently.
"""
import re
from dataclasses import dataclass

# ---- CONTRACT.md constants (gate.py re-exports these via import) ----
TFLOPS_FAIL = 60.0      # burn: sustained tensor TFLOP/s below this = FAIL
TFLOPS_WARN = 70.0      # burn: below this = WARN (measured band 74.6+)
TFLOPS_DEAD = 20.0      # burn: below this at ANY cap = failed compute unlock (~12)
CORE_STOP_C = 80        # watchdog stop threshold, core
MEM_STOP_C = 85         # watchdog stop threshold, memory
WD_INTERVAL_S = 10      # watchdog sample period
WD_SMI_TIMEOUT_S = 5    # per-probe nvidia-smi timeout inside the watchdog
LOW_POWER_INFO_W = 150  # burn TFLOPS floors are informational below this cap
XID_FATAL = {13, 31, 48, 79, 94, 95, 154}  # + any unrecognized number
XID_NOTED = {43}        # software-class channel reset: record, continue
XID_PROC = {45, 119}    # kill/GSP-timeout artifacts: record, never score

HARD_FAIL_STAGES = ("mem_pattern", "mem_retention", "mem_vulkan", "burn", "matmul", "warm_rerun")
# Every hard-fail stage's receipt carries xid_fatal - one constant to
# extend when a new hard-fail stage appears (they were identical tuples).
XID_GATED_STAGES = HARD_FAIL_STAGES
NON_VERDICT_STAGES = ("run", "intake", "idle", "pcie")  # informational only

XID_RE = re.compile(r"Xid[^0-9]*(\d+)")
FOREIGN_BUS_RE = re.compile(r"0000:[0-9a-f]{2}:[0-9a-f]{2}", re.I)
PAREN_RE = re.compile(r"\([^)]*\)")
SYSFS_BUS_RE = re.compile(r"\b0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]\b", re.I)

BRAKE_PASS = {"not active", "inactive", "n/a", "not supported", ""}


def parse_tflops(text):
    """Last gpu-burn throughput number in text -> TFLOP/s, or None.

    Comma-tolerant (some locales print "74,800 GFLOP/s"); malformed
    tokens (e.g. "1.2.3" from wrapped columns) are skipped rather than
    crashing the stage - everything unparsed stays the WARN-class
    "unparsed" verdict with the log left for a human.
    """
    m = re.findall(r"([\d.,]+)\s*(tflop|gflop)s?(?:/s)?", text.lower())
    for num, unit in reversed(m):
        try:
            return round(float(num.replace(",", ""))
                         / (1000.0 if unit == "gflop" else 1.0), 2)
        except ValueError:
            continue
    return None


def classify_tflops(tflops, low_power=False):
    """One throughput number -> fail / warn / pass / unparsed.

    Below TFLOPS_DEAD the card is dead at ANY cap (failed compute unlock
    ~12, REFERENCE-NUMBERS sC: the low-power informational rule excuses
    throttling, not a broken tensor path). Above it the bands are
    180 W-calibrated - at a degraded-stand cap (low_power) the mid band
    flattens to warn instead of fail.
    """
    if tflops is None:
        return "unparsed"
    if tflops < TFLOPS_DEAD:
        return "fail"
    if low_power:
        return "warn"
    if tflops < TFLOPS_FAIL:
        return "fail"
    if tflops < TFLOPS_WARN:
        return "warn"
    return "pass"


def classify_xid(line, bus_short):
    """One dmesg line -> ("fatal"|"noted"|"proc"|None). None = not ours /
    not an Xid. Foreign-GPU lines (a bus token without ours) are excluded;
    unknown Xid numbers classify as fatal (conservative)."""
    if "NVRM: Xid" not in line:
        return None
    if FOREIGN_BUS_RE.search(line) and bus_short not in line:
        return None
    # Strip bus tokens before extracting the number: NVRM prints
    # "Xid (PCI:0000:01:00): 31" - the naive regex would capture 0000.
    s = PAREN_RE.sub(" ", line)
    s = SYSFS_BUS_RE.sub(" ", s)
    m = XID_RE.search(s)
    n = int(m.group(1)) if m else -1
    if n in XID_NOTED:
        return "noted"
    if n in XID_PROC:
        return "proc"
    return "fatal"


def classify_brake(raw):
    """A brake reading -> pass / asserting / unknown / unrecognized.

    The exact spelling of "not asserting" varies by driver (Not Active,
    Inactive, N/A, Not Supported). A value ASSERTS an active brake when it
    contains "active" without "inactive"/"not active" and is not a pass
    spelling - that catches verbose "Active (PWRBRK#)" / "Active: Sync
    Boost" while "Inactive" / "Not Active (...)" never assert. "unknown"
    is the gate's marker for a missing/unreadable field. Shared by
    gate.py (live intake death) and make-report.py (aborted-run
    stand-fault reason + report warns), so both sides agree by
    construction.
    """
    s = (raw or "").strip().lower()
    if s == "unknown":
        return "unknown"
    if s in BRAKE_PASS:
        return "pass"
    if "active" in s and "inactive" not in s and "not active" not in s:
        return "asserting"
    return "unrecognized"


@dataclass(frozen=True)
class Verdict:
    """One stage verdict: class + display detail. The string form is the
    receipt/report display format (CONTRACT.md table): FAIL(tflops=11.9),
    WARN-TFLOPS(warn:65.0), INCOMPLETE(memtest-never-ran), WARN-BF16-SPEED."""

    cls: str            # PASS / WARN / FAIL / INCOMPLETE / SKIPPED
    detail: str = ""
    label: str = ""     # WARN subtype (TFLOPS, BF16-SPEED)

    def __str__(self):
        head = ("WARN-" + self.label
                if self.cls == "WARN" and self.label else self.cls)
        return head + "(" + self.detail + ")" if self.detail else head


def stage_verdict(name, r):
    """One stage -> Verdict per CONTRACT.md (gate and report share this)."""
    stage = name.split("-", 1)[0]
    if stage in NON_VERDICT_STAGES:
        return None
    rc_val = str(r.get("rc", ""))
    if rc_val.startswith("skip"):
        return Verdict("SKIPPED")
    # warm_rerun has no single rc: it carries rc_memtest/rc_burn (gate.py writes
    # no "rc" key - branching here first avoids misreading them as rc).
    if stage == "warm_rerun":
        xid_key = "xid_fatal" if "xid_fatal" in r else "xid_count"
        ok = (str(r.get("rc_memtest", "")) == "0"
              and str(r.get("rc_burn", "")) == "0"
              and str(r.get(xid_key, "0")) == "0"
              and str(r.get("thermal_stop", "0")) == "0"
              and str(r.get("gpu_gone", "0")) == "0")
        return Verdict("PASS" if ok else "FAIL")
    # Stop events supersede every other classification - including the
    # mem_vulkan setup-error class: a thermal kill racing a setup-error exit is
    # a FAIL, not an INCOMPLETE.
    if str(r.get("thermal_stop", "0")) != "0" or str(r.get("gpu_gone", "0")) != "0":
        return Verdict("FAIL")
    # Per-stage Xid gate: the CONTRACT PASS columns require xid_fatal=0
    # for every stage that carries the key (xid_count is the legacy key
    # from the bash gate); noted/proc classes are recorded, never scored.
    if stage in XID_GATED_STAGES:
        xid_key = "xid_fatal" if "xid_fatal" in r else "xid_count"
        if str(r.get(xid_key, "0")) != "0":
            return Verdict("FAIL")
    if stage == "mem_vulkan" and rc_val == "64":
        # memtest_vulkan setup error persisting after the automatic
        # retry: the memory test NEVER RAN. Not a card fail, but never an
        # ACCEPT either - --accept-warnings must not skip the core
        # memory test.
        return Verdict("INCOMPLETE", "memtest-never-ran")
    if rc_val != "0":
        return Verdict("FAIL")
    if stage == "burn":
        tv = str(r.get("tflops_verdict", "unparsed"))
        if tv == "fail":
            return Verdict("FAIL", "tflops=" + str(r.get("tflops")))
        if tv in ("warn", "unparsed"):
            return Verdict("WARN", tv + ":" + str(r.get("tflops")), "TFLOPS")
    if stage == "matmul" and str(r.get("warn", "")) == "bf16-speed":
        return Verdict("WARN", "", "BF16-SPEED")
    if stage == "inference" and str(r.get("warn", "")):
        # T8 warn band (0.9x..floor per CONTRACT T8) or an informational
        # recipe note: rc=0 + warn must never read as a clean PASS.
        return Verdict("WARN", str(r.get("warn")), "INFERENCE")
    return Verdict("PASS")

