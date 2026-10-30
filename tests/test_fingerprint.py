#!/usr/bin/env python3
"""test_fingerprint.py - make-report writes fingerprint.json with the
measured invariants (burn TFLOPs, matmul speeds/errors from
logs/matmul*/matmul.json, sku, thermal context from telemetry), and
compare-fingerprints.py flags measured deltas beyond tolerance (exit 1)
while passing small ones. Run via tests/contract_sync.py.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run():
    fails = []
    tmp = tempfile.mkdtemp(prefix="cs-fp-")
    try:
        ev = os.path.join(tmp, "ev")
        os.makedirs(os.path.join(ev, "receipts"))
        os.makedirs(os.path.join(ev, "logs", "matmul"))
        os.makedirs(os.path.join(ev, "telemetry"))
        ok = {"rc": "0", "thermal_stop": "0", "gpu_gone": "0",
              "xid_fatal": "0"}
        rec = {
            "mem_pattern": dict(ok), "mem_retention": dict(ok),
            "mem_vulkan": {**ok, "retried": "0"},
            "burn": {**ok, "tflops": "77.2", "tflops_verdict": "pass"},
            "matmul": {"rc": "0", "retried": "0"},
            "warm_rerun": {"rc_memtest": "0", "rc_burn": "0",
                           "xid_fatal": "0", "thermal_stop": "0",
                           "gpu_gone": "0"},
            "run-summary": {"xid_total": "0", "aborted": "0",
                            "profile": "express",
                            "memory_total_mib": "65536",
                            "power_w": "180"},
            "intake": {"profile": "8gb(64GiB)",
                       "brake_class": "pass"},
            "inference-smoke": {"rc": "0", "decode256": 133.0,
                                "decode900": 120.0, "prefill": 1900.0,
                                "ttft": 200.0},
            "inference-nemotron-3.5-lightning-30b-a3b":
                {"rc": "0", "informational": True, "decode256": 210.0,
                 "ttft": 300.0},
        }
        for k, v in rec.items():
            json.dump(v, open(os.path.join(ev, "receipts", k + ".json"), "w"))
        json.dump({"torch": "2.13.0+cu130",
                   "dtypes": {"bf16": {"tflops": 175.2, "max_abs_err": 1.35},
                              "fp16": {"tflops": 88.0, "max_abs_err": 0.17},
                              "tf32": {"tflops": 44.0, "max_abs_err": 0.1},
                              "int8": {"max_abs_err": 0}}},
                  open(os.path.join(ev, "logs", "matmul", "matmul.json"), "w"))
        with open(os.path.join(ev, "telemetry", "session.csv"), "w") as fh:
            fh.write("timestamp,temperature.gpu,temperature.memory,"
                     "power.draw,clocks.sm\n0,60,64,175,1000\n")
        subprocess.run([sys.executable,
                        os.path.join(ROOT, "scripts", "make-report.py"),
                        ev, "--no-hash"], capture_output=True, text=True)
        fp_path = os.path.join(ev, "fingerprint.json")
        if not os.path.exists(fp_path):
            fails.append("make-report must write fingerprint.json next to REPORT.md")
        else:
            fp = json.load(open(fp_path))
            m = fp.get("measured", {})
            if m.get("t4_tflops") != 77.2 or m.get("bf16_tflops") != 175.2 \
                    or m.get("int8_max_abs_err") != 0:
                fails.append("fingerprint measured fields wrong: " + str(m))
            # Namespaced inference keys: acceptance t8_* and
            # informational info_<receipt>_* must never collide (the
            # old shared keys silently kept whichever loaded last).
            if m.get("t8_decode256") != 133.0 or m.get("t8_prefill") != 1900.0:
                fails.append("fingerprint t8_* keys wrong: " + str(m))
            if m.get("info_inference-nemotron-3.5-lightning-30b-a3b_decode256") != 210.0:
                fails.append("fingerprint info_* key missing: " + str(m))
            if "decode256" in m or "ttft" in m:
                fails.append("fingerprint must not carry un-namespaced "
                             "inference keys: " + str(sorted(m)))
            if fp.get("sku", {}).get("memory_total_mib") != 65536:
                fails.append("fingerprint sku wrong: " + str(fp.get("sku")))
            if fp.get("torch") != "2.13.0+cu130":
                fails.append("fingerprint must carry the matmul torch version")
            tc = fp.get("thermal_context", {})
            if tc.get("max_core_c") != 60 or tc.get("max_mem_c") != 64:
                fails.append("fingerprint thermal context wrong: " + str(tc))

        ref = os.path.join(tmp, "ref.json")
        cand = os.path.join(tmp, "cand.json")
        base = {"schema": 1, "measured": {"t4_tflops": 77.2,
                                          "bf16_tflops": 175.2},
                "thermal_context": {"max_core_c": 60}}
        json.dump(base, open(ref, "w"))
        json.dump({"schema": 1, "measured": {"t4_tflops": 76.0,
                                             "bf16_tflops": 150.0},
                   "thermal_context": {"max_core_c": 70}}, open(cand, "w"))
        cmp_script = os.path.join(ROOT, "scripts", "compare-fingerprints.py")
        r1 = subprocess.run([sys.executable, cmp_script, ref, cand],
                            capture_output=True, text=True)
        if r1.returncode != 1 or "bf16_tflops" not in r1.stdout:
            fails.append("compare must flag the >5% bf16 delta (rc="
                         + str(r1.returncode) + ")")
        json.dump({"schema": 1, "measured": {"t4_tflops": 76.0,
                                             "bf16_tflops": 174.0},
                   "thermal_context": {}}, open(cand, "w"))
        r2 = subprocess.run([sys.executable, cmp_script, ref, cand],
                            capture_output=True, text=True)
        if r2.returncode != 0:
            fails.append("compare must pass within-tolerance deltas (rc="
                         + str(r2.returncode) + ")")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails

