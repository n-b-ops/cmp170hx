#!/usr/bin/env python3
"""matmul_check.py - single-card tensor-core correctness fingerprint + BF16
throughput gate (CONTRACT.md matmul).

Correctness: adapted from club-170hx scripts/qc/tensor-gate.sh matmul harness
(authored by the club-170hx authors; same seed and shapes) for the single-card
seller-stand case: instead of cross-card byte-identity, PASS = bit-identical
results across REPEATS (determinism) + max-abs-error in the expected band vs
the CPU float64 reference + INT8 exactly 0.

Reference errors (torch 2.13.0+cu130, seed 170170, 4096x4096, 70-SM stack,
measured by club across 4 cards):
    BF16 1.348 / FP16 0.166 / TF32 0.098 / INT8 0
The 74-SM stack may shift values slightly (cuBLAS tiling); band = 2x.

Throughput: a sustained-gemm BF16 probe separates a healthy card (community
band 164-193 TFLOPS, REFERENCE-NUMBERS sC) from a failed compute unlock
(~12 TFLOPS). Thresholds are INFERRED for a single 4096^3 op: BF16 >= 100
pass, 80-100 warn, < 80 fail. FP16/TF32 speeds are recorded, not gated.

Exit codes (CONTRACT.md matmul): 0 = full pass, 2 = correctness pass with BF16
speed in the warn band, 1 = hard fail (non-determinism, out-of-band error,
INT8 != 0, or BF16 < 80 TFLOPS). gate.py re-runs once on a nonzero-fail
exit per the nondeterminism policy.

Usage: matmul_check.py <gpu_index> <seed> <out_dir> [repeats=3]
"""
import contextlib
import collections
import hashlib
import json
import os
import sys

import numpy as np
import torch

REF_BAND = {"bf16": (1.348, 2.0), "fp16": (0.166, 2.0), "tf32": (0.098, 2.0)}
SPEED_FLOOR_BF16 = 100.0   # inferred; see CONTRACT.md matmul
SPEED_WARN_BF16 = 80.0     # inferred
SPEED_WARMUP = 3
SPEED_ITERS = 10
DTYPES = {"bf16": torch.bfloat16, "fp16": torch.float16, "tf32": torch.float32}
# (seed, n) -> (a64, b64, ref). Each entry holds ~384 MB of fp64 tensors, so
# the cache is FIFO-capped: a harness looping seeds must not
# grow it unbounded.
_REF_CACHE = collections.OrderedDict()
_REF_CACHE_MAX = 4


def reference_pair(seed, n=4096):
    key = (seed, n)
    if key in _REF_CACHE:
        _REF_CACHE.move_to_end(key)
        return _REF_CACHE[key]
    torch.manual_seed(seed)
    a64 = torch.randn(n, n, dtype=torch.float64)
    b64 = torch.randn(n, n, dtype=torch.float64)
    _REF_CACHE[key] = (a64, b64, (a64 @ b64).numpy())
    while len(_REF_CACHE) > _REF_CACHE_MAX:
        _REF_CACHE.popitem(last=False)
    return _REF_CACHE[key]


def run(dtype_name, gpu_index, seed, n=4096):
    a64, b64, ref = reference_pair(seed, n)
    dtype = DTYPES[dtype_name]
    dev = f"cuda:{gpu_index}"
    _was_tf32 = torch.backends.cuda.matmul.allow_tf32
    torch.backends.cuda.matmul.allow_tf32 = dtype_name == "tf32"
    try:
        # Cast on the CPU, transfer the small dtype only: the device never holds
        # fp64 operands (~256 MB saved). Values stay identical to
        # the fp64 reference pair, which the correctness check requires.
        # The cache hit is intentional: fp64 -> dtype casting is deterministic,
        # so one cached reference pair serves every dtype and repeat - do NOT
        # "fix" this by re-seeding per dtype.
        a = a64.to(dtype).to(dev)
        b = b64.to(dtype).to(dev)
        out = (a @ b).to(torch.float64).cpu().numpy()
    finally:
        torch.backends.cuda.matmul.allow_tf32 = _was_tf32
        with contextlib.suppress(NameError):
            del a, b
        torch.cuda.empty_cache()
    return float(np.abs(out - ref).max()), hashlib.sha256(out.tobytes()).hexdigest()


def timed_tflops(dtype_name, gpu_index, n=4096):
    """Warm up, then time SPEED_ITERS back-to-back gemms with CUDA events."""
    dev = f"cuda:{gpu_index}"
    dtype = DTYPES[dtype_name]
    was = torch.backends.cuda.matmul.allow_tf32
    torch.backends.cuda.matmul.allow_tf32 = dtype_name == "tf32"
    a = torch.randn(n, n, device=dev, dtype=dtype)
    b = torch.randn(n, n, device=dev, dtype=dtype)
    for _ in range(SPEED_WARMUP):
        a @ b
    torch.cuda.synchronize()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    for _ in range(SPEED_ITERS):
        a @ b
    end.record()
    torch.cuda.synchronize()
    torch.backends.cuda.matmul.allow_tf32 = was
    secs = start.elapsed_time(end) * 1e-3
    del a, b
    torch.cuda.empty_cache()
    return 2 * SPEED_ITERS * n**3 / secs / 1e12


# (seed, n) -> (ai, bi, ref): the int64 CPU GEMM is slow (~minutes at
# 4096^2), compute once. FIFO-capped like _REF_CACHE: a harness looping
# seeds must not grow it unbounded either.
_INT8_CACHE = collections.OrderedDict()
_INT8_CACHE_MAX = 2


def int8_reference_pair(seed, n=4096):
    key = (seed, n)
    if key in _INT8_CACHE:
        _INT8_CACHE.move_to_end(key)
        return _INT8_CACHE[key]
    torch.manual_seed(seed)
    ai = torch.randint(-128, 128, (n, n), dtype=torch.int8)
    bi = torch.randint(-128, 128, (n, n), dtype=torch.int8)
    _INT8_CACHE[key] = (ai, bi, (ai.to(torch.int64) @ bi.to(torch.int64)).numpy())
    while len(_INT8_CACHE) > _INT8_CACHE_MAX:
        _INT8_CACHE.popitem(last=False)
    return _INT8_CACHE[key]


def run_int8(gpu_index, seed, n=4096):
    ai, bi, ref = int8_reference_pair(seed, n)
    dev = f"cuda:{gpu_index}"
    out = torch._int_mm(ai.to(dev), bi.to(dev)).to(torch.int64).cpu().numpy()
    return int(np.abs(out - ref).max()), hashlib.sha256(out.tobytes()).hexdigest()


def main():
    gpu_index, seed, out_dir = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    try:
        repeats = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    except ValueError:
        sys.exit("repeats must be a positive integer")
    if repeats < 1:
        sys.exit("repeats must be >= 1")
    os.makedirs(out_dir, exist_ok=True)
    result = {
        "gpu": gpu_index, "seed": seed, "repeats": repeats, "dtypes": {},
        "pass": True, "speed_warn": False, "notes": [],
        # Stack identity: REF_BAND is calibrated on one torch build; a
        # future stand with a newer torch shifts the (still correct)
        # errors and the report must say which build produced them.
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "speed_thresholds": {"bf16_floor": SPEED_FLOOR_BF16, "bf16_warn": SPEED_WARN_BF16, "label": "inferred"},
    }

    for name in ("bf16", "fp16", "tf32"):
        errs, hashes = [], []
        for _ in range(repeats):
            err, h = run(name, gpu_index, seed)
            errs.append(err)
            hashes.append(h)
        deterministic = len(set(hashes)) == 1
        finite = all(np.isfinite(e) for e in errs)
        ref, mult = REF_BAND[name]
        in_band = all(e <= ref * mult for e in errs)
        ok = deterministic and finite and in_band
        tflops = None
        if ok:
            tflops = round(timed_tflops(name, gpu_index), 2)
        speed_verdict = "not-run"
        if name == "bf16" and tflops is not None:
            if tflops < SPEED_WARN_BF16:
                speed_verdict = "fail"
                ok = False
            elif tflops < SPEED_FLOOR_BF16:
                speed_verdict = "warn"
                result["speed_warn"] = True
                result["notes"].append(f"bf16 speed {tflops} TFLOPS in warn band {SPEED_WARN_BF16}-{SPEED_FLOOR_BF16}")
            else:
                speed_verdict = "pass"
        result["dtypes"][name] = {
            "max_abs_err": max(errs) if errs else None,
            "errs_all": errs,
            "deterministic": deterministic,
            "finite": finite, "in_band": in_band, "sha256": hashes[0],
            "tflops": tflops, "speed_verdict": speed_verdict, "pass": ok,
        }
        if not ok:
            result["pass"] = False
            result["notes"].append(f"{name}: det={deterministic} finite={finite} in_band={in_band} speed={speed_verdict} errs={errs}")

    errs, hashes = [], []
    for _ in range(repeats):
        err, h = run_int8(gpu_index, seed)
        errs.append(err)
        hashes.append(h)
    ok = len(set(hashes)) == 1 and all(e == 0 for e in errs)
    result["dtypes"]["int8"] = {
        "max_abs_err": max(errs, default=None),
        "deterministic": len(set(hashes)) == 1,
        "exact_zero": all(e == 0 for e in errs), "sha256": hashes[0],
        "tflops": None, "speed_verdict": "not-gated", "pass": ok,
    }
    if not ok:
        result["pass"] = False
        result["notes"].append(f"int8: errs={errs}")

    out_path = os.path.join(out_dir, "matmul.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    if not result["pass"]:
        sys.exit(1)
    if result["speed_warn"]:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()

