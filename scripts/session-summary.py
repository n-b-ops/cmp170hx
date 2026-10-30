#!/usr/bin/env python3
"""session-summary.py - human-readable end-of-session report.

Reads one gate evidence bundle (receipts/, telemetry/, REPORT.md,
fingerprint.json) plus optional informational inference receipts, and
writes SUMMARY.md next to REPORT.md: verdict first in plain language, what
was tested and what each check means, key numbers, deviations, and what
stayed untested (residual risk, SPEC-03 s6).

Usage: session-summary.py <evidence-dir> [--extra receipt.json ...]
       run-acceptance.sh calls this automatically; standalone works too.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

STAGE_MEANING = {
    "intake": "card identity + unlock band (the SKU we think, fully unlocked)",
    "idle": "thermal baseline the load stages are judged against",
    "mem_pattern": "every word of ~62 GiB written+verified above the 8 GiB mining window - the #1 used-card risk",
    "mem_retention": "memory still correct after idle - catches refresh/retention faults",
    "mem_vulkan": "independent full-VRAM memory test (second engine)",
    "burn": "sustained tensor-core load at the power cap - throughput + thermals",
    "matmul": "deterministic compute fingerprint, throughput in the measured band",
    "pcie": "PCIe link stable after load (Gen2 x16 unlocked path)",
    "warm_rerun": "heat-soaked repeat - no degradation when hot",
}


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def verdict_from_report(ev):
    report = ev / "REPORT.md"
    if report.is_file():
        for line in report.read_text().splitlines():
            if "Verdict:" in line:
                return line.replace("*", "").strip()
    return "Verdict: UNKNOWN (no REPORT.md - run make-report.py first)"


def stage_rows(ev):
    rows = []
    for stage, meaning in STAGE_MEANING.items():
        r = load(ev / "receipts" / f"{stage}.json")
        if r is None:
            rows.append((stage, "not run", meaning))
            continue
        if str(r.get("rc", 0)) == "0" and not (r.get("thermal_stop") or r.get("gpu_gone")):
            outcome = "PASS"
        elif stage == "mem_vulkan" and r.get("rc") == 64:
            outcome = "INCOMPLETE (setup error - never ACCEPT)"
        else:
            outcome = 'FAIL (rc={})'.format(r.get("rc"))
        extra = ""
        if stage == "burn" and r.get("tflops_gflops") is not None:
            extra = f" - {r['tflops_gflops'] / 1000:.1f} TFLOP/s"
        rows.append((stage, outcome + extra, meaning))
    return rows


def telemetry_peaks(ev):
    csv_path = ev / "telemetry" / "session.csv"
    if not csv_path.is_file():
        return None
    try:
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            cols = {"temperature.gpu": [], "temperature.memory": [],
                    "power.draw": []}
            for row in reader:
                for k in cols:
                    try:
                        cols[k].append(float(row[k]))
                    except (KeyError, ValueError, TypeError):
                        pass
        return {k: (max(v) if v else None) for k, v in cols.items()}
    except OSError:
        return None


def fingerprint_numbers(ev):
    fp = load(ev / "fingerprint.json") or {}
    measured = fp.get("measured", fp)
    keys = ("t4_tflops", "bf16_tflops", "fp16_tflops", "fp16_max_abs_err")
    return {k: measured[k] for k in keys if k in measured}


def deviations(rs):
    out = []
    if rs.get("xid_counting") == "unavailable":
        out.append("Xid channel UNVERIFIED (dmesg unreadable / --allow-no-dmesg): "
                   "hardware error events during the run could not be counted")
    if rs.get("shared_host") == 1:
        out.append("shared host: neighbor-GPU thermal/PSU confounding possible "
                   "(report is CONDITIONAL by contract)")
    if str(rs.get("power_w", "")) not in ("", "180"):
        out.append(f"power cap {rs.get('power_w')} W is the provider preset "
                   f"(180 W club-policy cap could not be set)")
    if rs.get("profile") in ("express", "smoke"):
        out.append(f"{rs.get('profile')} profile: pass is necessary, NOT "
                   "sufficient for a purchase verdict")
    return out


def not_tested(rs):
    out = ["T9 cold-boot persistence of the unlock (manual, SPEC-02 sT9)"]
    if rs.get("xid_counting") == "unavailable":
        out.append("live Xid detection (channel was disabled)")
    return out


def stage_durations(ev):
    """Measured per-stage durations: delta between consecutive stage-end
    receipt timestamps (exact, no estimates; the first stage's delta is its
    own end minus the session start in telemetry)."""
    from datetime import datetime
    order = [s for s in STAGE_MEANING if (ev / "receipts" / f"{s}.json").is_file()]
    out = []
    prev = None
    for s in order:
        r = load(ev / "receipts" / f"{s}.json")
        try:
            ts = datetime.fromisoformat(r["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        if prev is not None:
            # Receipts carry stage-END timestamps: the delta between
            # consecutive ends is the LATER stage's duration.
            out.append((s, (ts - prev).total_seconds()))
        prev = ts
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence_dir")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="informational inference receipt json paths")
    args = ap.parse_args()
    ev = Path(args.evidence_dir)
    rs = load(ev / "receipts" / "run-summary.json") or {}

    lines = []
    add = lines.append
    add(f"# Session summary - {ev.name}")
    add("")
    add(f"**{verdict_from_report(ev)}**")
    add("")
    add("## What was tested")
    add("")
    add("| Check | Outcome | What it means |")
    add("| ----- | ------- | ------------- |")
    for stage, outcome, meaning in stage_rows(ev):
        add(f"| {stage} | {outcome} | {meaning} |")
    for extra in args.extra:
        r = load(extra)
        if r:
            add(f"| {r.get('model', Path(extra).stem)} (informational) | "
                f"rc={r.get('rc')} - never gates | real serving workload: "
                f"decode {r.get('decode256')} tok/s, prefill {r.get('prefill')} tok/s |")
    fp = fingerprint_numbers(ev)
    if fp:
        add("")
        add("## Fingerprint numbers (reference for future runs / card #2)")
        add("")
        for k, v in fp.items():
            add(f"- {k}: {v}")
    durs = stage_durations(ev)
    if durs:
        add("")
        add("## Measured durations (deltas between consecutive stage-end receipts)")
        add("")
        total = 0.0
        for name, secs in durs:
            total += secs
            add(f"- {name}: {secs / 60:.2f} min")
        add(f"- total across stages: {total / 60:.2f} min")
    peaks = telemetry_peaks(ev)
    if peaks:
        add("")
        add("## Session peaks (watchdog limits: 80 C core / 85 C memory)")
        add("")
        add(f"- max core temp: {peaks['temperature.gpu']} C")
        add(f"- max memory temp: {peaks['temperature.memory']} C")
        add(f"- max power draw: {peaks['power.draw']} W")
    dev = deviations(rs)
    if dev:
        add("")
        add("## Deviations / caveats")
        add("")
        for d in dev:
            add(f"- {d}")
    add("")
    add("## Not tested (residual risk)")
    add("")
    for n in not_tested(rs):
        add(f"- {n}")
    add("")
    add("Machine-readable detail: REPORT.md, receipts/, fingerprint.json, "
        "telemetry/session.csv in this directory.")

    out = ev / "SUMMARY.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
