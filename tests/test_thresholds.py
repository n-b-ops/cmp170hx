#!/usr/bin/env python3
"""test_thresholds.py - CONTRACT.md marker/threshold sync, single-engine
and non-verdict guards. Row presence: every verdict stage has a row in
CONTRACT mentioning the receipt keys the code actually reads; threshold
numbers in code appear in CONTRACT with the same values. Run via
tests/contract_sync.py.
"""
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# CONTRACT row must mention these substrings (per stage prefix)
ROW_MARKERS = {
    "mem_pattern": ["xid_fatal"],
    "mem_retention": ["xid_fatal"],
    "mem_vulkan": ["INCOMPLETE", "supersedes", "thermal_stop", "xid_fatal"],
    "burn": ["tflops_verdict", "xid_fatal", "gpu_gone", "informational",
             "brake"],
    "matmul": ["warn=bf16-speed", "thermal_stop", "gpu_gone"],
    "warm_rerun": ["rc_memtest", "rc_burn"],
}


def num_from(path, name):
    src = open(path).read()
    m = re.search(rf"^{name}\s*=\s*([0-9.]+)", src, re.M)
    if not m:
        raise AssertionError(f"{name} not found in {path}")
    return m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1)


def run(verdicts, gate, mr, contract):
    """Returns (failures, number-of-threshold-needles-checked)."""
    fails = []

    # Single-engine enforcement: make-report must re-export the SAME
    # function object, and neither script may grow a local copy.
    if mr.stage_verdict is not verdicts.stage_verdict:
        fails.append("make-report.stage_verdict is not verdicts.stage_verdict "
                     "(a diverged copy)")
    for fname in ("gate.py", "make-report.py"):
        src = open(os.path.join(ROOT, "scripts", fname)).read()
        if "from verdicts import" not in src:
            fails.append(fname + " does not import the shared verdicts engine")
        if re.search(r"^def stage_verdict|^TFLOPS_FAIL\s*=|^def parse_tflops"
                     r"|^def classify_tflops", src, re.M):
            fails.append(fname + " defines a local verdict-engine copy")

    # Non-verdict stages must stay non-verdict (a refactor promoting
    # intake or idle to a verdict stage would otherwise pass silently).
    # These are the REAL receipt names - the impossible legacy name "t2"
    # once hid a whole bug class here.
    for stage in ("intake", "idle", "pcie"):
        got = verdicts.stage_verdict(f"{stage}-x", {})
        if got is not None:
            fails.append(f"{stage} must stay a non-verdict stage, got "
                         f"{got!r} for a bare receipt")

    # Stage sections: since the round-15 restructure the automated-stages
    # table is one "### <stage>" subsection per stage (PASS/WARN/FAIL as
    # bullet labels) - each marker must appear in ITS stage's section.
    def stage_section(name):
        m = re.search(rf"^### {name}$\n(.*?)(?=^### |^## )", contract,
                      re.M | re.S)
        return m.group(1) if m else ""

    for stage, markers in ROW_MARKERS.items():
        section = stage_section(stage)
        if not section:
            fails.append(f"CONTRACT.md has no stage section for {stage}")
            continue
        for marker in markers:
            if marker not in section:
                fails.append(f"CONTRACT.md {stage} section missing "
                             f"marker: {marker!r}")

    # Top mapping must cover the corrupt/malformed run-summary case
    for marker in ("unparseable", "xid_total", "core memory", "profile=smoke"):
        if marker not in contract:
            fails.append(f"CONTRACT.md top verdict mapping missing: {marker!r}")

    # Xid class membership: every class member number appears in CONTRACT
    for name, want in (("XID_NOTED", gate.XID_NOTED),
                       ("XID_PROC", gate.XID_PROC)):
        for n in want:
            if str(n) not in contract:
                fails.append(f"CONTRACT.md Xid-class section missing "
                             f"{name} member: {n}")

    # Threshold sync: code constants must appear in CONTRACT with the
    # same numbers.
    tf_fail = str(int(gate.TFLOPS_FAIL))
    tf_warn = str(int(gate.TFLOPS_WARN))
    bf_floor = num_from(os.path.join(ROOT, "scripts", "matmul_check.py"),
                        "SPEED_FLOOR_BF16")
    bf_warn = num_from(os.path.join(ROOT, "scripts", "matmul_check.py"),
                       "SPEED_WARN_BF16")
    needles = [
        (f"\u2265 {tf_warn} TFLOP/s", "burn PASS threshold"),
        (f"{tf_fail}\u2013{tf_warn}", "burn WARN band"),
        (f"< {tf_fail}", "burn FAIL threshold"),
        (f"\u2265 {bf_floor} TFLOPS", "matmul BF16 floor"),
        (f"{bf_warn}\u2013{bf_floor}", "matmul BF16 warn band"),
        (f"< {bf_warn} TFLOPS", "matmul BF16 fail threshold"),
        (f"< {int(gate.LOW_POWER_INFO_W)} W", "burn low-power informational cap"),
        (f"< {int(gate.TFLOPS_DEAD)} at ANY cap", "burn dead-card floor"),
    ]
    for needle, desc in needles:
        if needle not in contract:
            fails.append(f"CONTRACT.md missing {desc}: {needle!r}")

    # kit/inference package sync (roadmap s9.1): the kit recipe must
    # carry the club recipe of record verbatim, the carried lock must BE
    # the club lock (sha256), the floors must match CONTRACT T8, and
    # smoke.py must write the receipt keys make-report reads.
    kit_rc = json.load(open(os.path.join(ROOT, "kit", "inference",
                                         "recipe.json")))
    club_rc = json.load(open(os.path.join(
        ROOT, "vendor", "club-170hx", "recipes", "qwen3.8-27b-dflash2",
        "recipe.json")))
    for field in ("model_revision", "runtime_pin", "requirements_sha256"):
        if kit_rc["source_of_record"][field] == club_rc[field]:
            continue
        # Documented exception (2026-10-30 re-pin): the club runtime pin
        # 69ba4d06 died in an upstream history rewrite (GitHub API 422);
        # the kit recipe carries a content-anchored HyperQwen re-pin. The
        # divergence is allowed ONLY when the kit recipe still cites the
        # club pin verbatim inside its runtime_pin string AND pins the
        # replacement tree by sha256 (REFERENCE-NUMBERS s0, PINS.md).
        if (field == "runtime_pin"
                and club_rc[field].split(";")[0]
                in kit_rc["source_of_record"][field]
                and kit_rc["source_of_record"].get("runtime_tree_sha256")):
            continue
        fails.append(f"kit recipe {field} diverged from the club "
                     f"recipe of record")
    for dep, ver in club_rc["runtime_dependencies"].items():
        if kit_rc["verify_versions"].get(dep) != ver:
            fails.append(f"kit recipe verify_versions diverged: {dep}")
    lock_sha = hashlib.sha256(
        open(os.path.join(ROOT, "kit", "inference",
                          "requirements.lock"), "rb").read()).hexdigest()
    if lock_sha != club_rc["requirements_sha256"]:
        fails.append("kit requirements.lock is not the byte-exact club lock")
    for key, needle, want in (("decode256", "decode256 130", 130),
                              ("decode900", "decode900 112", 112),
                              ("prefill", "prefill 1750", 1750),
                              ("ttft_ms", "250 ms", 250)):
        if kit_rc["floors"].get(key) != want:
            fails.append(f"kit recipe floor {key} != {want} (CONTRACT T8)")
        if needle not in contract:
            fails.append(f"CONTRACT.md T8 row missing floor marker: "
                         f"{needle!r}")
    smoke_src = open(os.path.join(ROOT, "kit", "inference",
                                  "smoke.py")).read()
    for key in ("decode256", "decode900", "prefill", "ttft"):
        if f'"{key}":' not in smoke_src:
            fails.append(f"kit smoke.py must write the report key {key!r}")

    # Informational serving recipes (kit/inference/recipes/*.json): they
    # must STAY informational - classified as such, structurally
    # complete, anchors labeled - and must never define CONTRACT floors
    # (acceptance floors live only in the root recipe.json + CONTRACT).
    recipes_dir = os.path.join(ROOT, "kit", "inference", "recipes")
    for name in sorted(os.listdir(recipes_dir)):
        if not name.endswith(".json"):
            continue
        r = json.load(open(os.path.join(recipes_dir, name)))
        if "informational" not in str(r.get("classification", "")):
            fails.append(f"{name} must be classified informational")
        if "floors" in r:
            fails.append(f"{name} must not define CONTRACT floors "
                         "(acceptance floors live only in recipe.json)")
        for block in ("model", "engine", "serve", "benchmark", "anchors"):
            if block not in r:
                fails.append(f"{name} missing block: {block}")
        anchors = r.get("anchors", {})
        for sub in ("long_context",):
            if not str(anchors.get(sub, {}).get("label", "")):
                fails.append(f"{name} anchors.{sub} must carry an "
                             f"evidence label")
    # Driver whitelist single-source (pins/drivers.json): must match
    # PINS.md's canonical row, and setup-container.sh must read the JSON
    # (not a hand-copied case pattern).
    drivers = json.load(open(os.path.join(ROOT, "pins", "drivers.json")))
    pins_src = open(os.path.join(ROOT, "PINS.md")).read()
    for d in drivers["supported_drivers"]:
        if d not in pins_src:
            fails.append(f"pins/drivers.json driver {d} missing from "
                         f"PINS.md - update together")
    # Reverse direction: a driver listed in PINS.md but missing from
    # drivers.json would silently NOT be whitelisted by
    # setup-container.sh. Set equality on the anchored PINS.md row.
    m = re.search(r"^\| Supported drivers[^\n]*\|([0-9., ]+)\|\s*$",
                  pins_src, re.M)
    if not m:
        fails.append("PINS.md 'Supported drivers' row not found - keep "
                     "the anchor stable (parsed against drivers.json)")
    else:
        in_pins = set(re.findall(r"\d{3}\.\d{2}\.\d{2}", m.group(1)))
        if in_pins != set(drivers["supported_drivers"]):
            fails.append(f"PINS.md driver set {sorted(in_pins)} != "
                         f"drivers.json {sorted(drivers['supported_drivers'])}"
                         " - update together")
    if "pins/drivers.json" not in open(os.path.join(
            ROOT, "scripts", "setup-container.sh")).read():
        fails.append("setup-container.sh must read pins/drivers.json "
                     "(driver whitelist single source)")
    return fails, len(needles)

