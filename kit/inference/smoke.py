#!/usr/bin/env python3
"""smoke.py - one serve + one benchmark pass on the card under test.

Launches the pinned runtime's own single-user/start_qwen.sh (the exact
serve path of record, kit/inference/recipe.json), waits for /health,
runs the club's usage-accounted benchmark protocol (vendor
live_benchmark.py) under its fail-closed telemetry guard (vendor
live_support.py), and writes the receipt the report already knows:
receipts/inference-smoke.json with rc, decode256, decode900, prefill,
ttft (CONTRACT.md T8 row; make-report reads the same keys for the
fingerprint).

rc=1 = protocol failure OR a below-0.9x-floor result (CONTRACT: forced
CONDITIONAL, never ACCEPT); the 0.9x..floor band records rc=0 plus a
warn field. ttft_ms is lower-is-better (fail above 1.11x floor).

Usage: <venv-inference>/bin/python kit/inference/smoke.py [options]
       Auto re-execs itself under --venv when the current interpreter
       is not it (so "./smoke.py" also works once install.sh has run).

Safety (SPEC-02 s0.2, non-negotiable): forced airflow before load - the
card is passive; the vendor guard stops the run at core >= 80 C,
memory >= 85 C, missing telemetry, an unexpected/locked device, or any
Xid / fallen-off-the-bus line in the kernel log.
"""
import argparse
import io
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RECIPE_PATH = REPO / "kit" / "inference" / "recipe.json"
VENDOR = REPO / "vendor" / "club-170hx" / "recipes" / "qwen3.8-27b-dflash2"
sys.path.insert(0, str(VENDOR))
from live_benchmark import LONG, P256, Benchmark  # noqa: E402
from live_support import (FailClosedTelemetryGuard,  # noqa: E402
                          SafetyViolation, preflight_snapshot,
                          verify_runtime_versions)


def floor_check(value, floor, lower_better=False):
    """One measured value vs its CONTRACT floor -> pass/warn/fail."""
    if value is None:
        return "fail"
    if lower_better:
        if value > floor * 1.11:
            return "fail"
        if value > floor:
            return "warn"
    else:
        if value < floor * 0.9:
            return "fail"
        if value < floor:
            return "warn"
    return "pass"


def run_smoke(args, recipe, runtime, venv, ev, receipt, server_box):
    kit = Path(args.kit_root)
    # --- verified stack: versions, runtime pin, backport patches ---
    receipt["versions"] = verify_runtime_versions()
    tree_sha = recipe["source_of_record"].get("runtime_tree_sha256")
    if tree_sha:
        # content-anchored pin (2026-10-30 re-pin: the historical git pin
        # died in an upstream history rewrite). Reproduces the fetch.sh
        # runtime_tree_hash() formula: sha256 over the sha256sum lines of
        # every file (./-relative, LC_ALL=C sorted), excluding models/ and
        # .git/ - the runtime tree is a tarball extraction, not a clone.
        entries = []
        for p in runtime.rglob("*"):
            if not p.is_file():
                continue
            rel = "./" + p.relative_to(runtime).as_posix()
            parts = p.relative_to(runtime).parts
            if parts[0] in ("models", ".git"):
                continue
            entries.append(rel)
        inner = "".join(
            f"{__import__('hashlib').sha256((runtime / e[2:]).read_bytes()).hexdigest()}  {e}\n"
            for e in sorted(entries))
        got = __import__("hashlib").sha256(inner.encode()).hexdigest()
        if got != tree_sha:
            raise RuntimeError(f"runtime tree hash {got} != recipe pin "
                               f"{tree_sha} - re-run fetch.sh/install.sh")
    else:
        pin = recipe["source_of_record"]["runtime_pin"].split("@")[1].split(";")[0]
        head = subprocess.run(["git", "-C", str(runtime), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if head != pin:
            raise RuntimeError(f"runtime HEAD {head} != recipe pin {pin} - "
                               f"re-run fetch.sh/install.sh")
    sp = subprocess.run(
        [str(venv / "bin" / "python"), "-c",
         "import vllm, os; print(os.path.dirname(vllm.__file__))"],
        capture_output=True, text=True).stdout.strip()
    arg_utils = os.path.join(sp, "vllm", "engine", "arg_utils.py")
    with open(arg_utils) as f:
        if "dflash2-backport" not in f.read():
            raise RuntimeError("vLLM dflash2 backport patches not applied "
                               f"({arg_utils}) - run kit/inference/install.sh")

    # --- fail-closed preflight: right device, unlocked, temps safe ---
    receipt["device"] = preflight_snapshot(args.gpu)
    guard = FailClosedTelemetryGuard(
        args.gpu, ev / "logs" / "inference-safety.jsonl",
        sample_interval_s=1.0)
    receipt["host"] = {
        "nproc": os.cpu_count(),
        "loadavg_before": open("/proc/loadavg").read().split()[:3],
    }
    guard.start()  # validates once, then monitors continuously
    server_box["guard"] = guard

    # --- launch the pinned serve path (the runtime's own launcher) ---
    api_key = secrets.token_urlsafe(16)
    env = dict(os.environ)
    env.update({k: str(v) for k, v in recipe["serve"]["env"].items()})
    env["PORT"] = str(args.port)
    env["VLLM_API_KEY"] = api_key
    env["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", "")
    env.setdefault("HF_HOME", str(kit / ".hf-cache"))
    env["HF_HUB_OFFLINE"] = "1"  # the kit is local; no egress on the stand
    env["VLLM_NO_USAGE_STATS"] = "1"
    env["DO_NOT_TRACK"] = "1"
    env["FLASHINFER_DISABLE_VERSION_CHECK"] = "1"
    launcher = runtime / "single-user" / "start_qwen.sh"
    if not launcher.is_file():
        raise RuntimeError(f"launcher missing: {launcher}")
    server_log = open(ev / "logs" / "inference-server.log", "w")
    server = subprocess.Popen(["bash", str(launcher)], cwd=str(runtime),
                              env=env, stdout=server_log,
                              stderr=subprocess.STDOUT,
                              start_new_session=True)
    server_box["server"] = server
    base_url = f"http://127.0.0.1:{args.port}"
    deadline = time.monotonic() + args.load_timeout
    healthy = False
    while time.monotonic() < deadline:
        guard.assert_safe()
        if server.poll() is not None:
            raise RuntimeError("server exited during startup - see "
                               f"{ev}/logs/inference-server.log")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=3) as r:
                if r.status == 200:
                    healthy = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    if not healthy:
        raise RuntimeError(f"no healthy server after {args.load_timeout}s - "
                           f"see {ev}/logs/inference-server.log")
    receipt["server"] = {
        "load_s": round(time.monotonic() - (deadline - args.load_timeout), 1),
        "port": args.port,
    }
    time.sleep(20)  # settle, as the recorded onstart sequence does

    # --- the club benchmark protocol, usage-object token counting ---
    bench = Benchmark(base_url, api_key, safety_check=guard.assert_safe)
    sink = io.StringIO()
    story_tokens = bench.token_count(P256)
    long_tokens = bench.token_count(LONG)
    bench.run_case(sink, "decode256", P256, 256, story_tokens,
                   warmups=args.warmup, samples=args.samples)
    bench.run_case(sink, "decode900", P256, 900, story_tokens,
                   warmups=args.warmup, samples=args.samples)
    bench.run_case(sink, "prefill_long", LONG, 8, long_tokens,
                   warmups=args.warmup, samples=args.samples)
    summaries = {}
    for line in sink.getvalue().splitlines():
        rec = json.loads(line)
        if rec.get("type") == "summary":
            summaries[rec["case"]] = rec
    (ev / "logs" / "inference-bench.jsonl").write_text(sink.getvalue())

    d256 = summaries.get("decode256", {}).get("decode_tok_s")
    d900 = summaries.get("decode900", {}).get("decode_tok_s")
    prefill = summaries.get("prefill_long", {}).get("prefill_tok_s")
    ttft = summaries.get("decode256", {}).get("mean_ttft_ms")
    floors = recipe["floors"]
    verdicts = {
        "decode256": floor_check(d256, floors["decode256"]),
        "decode900": floor_check(d900, floors["decode900"]),
        "prefill": floor_check(prefill, floors["prefill"]),
        "ttft": floor_check(ttft, floors["ttft_ms"], lower_better=True),
    }
    receipt.update({
        "decode256": d256,
        "decode900": d900,
        "prefill": prefill,
        "ttft": ttft,
        "prompt_tokens": {"story": story_tokens, "long": long_tokens},
        "samples": args.samples,
        "warmup": args.warmup,
        "case_summaries": summaries,
    })
    warns = []
    for key, verdict in verdicts.items():
        floor_key = "ttft_ms" if key == "ttft" else key
        if verdict == "warn":
            warns.append(f"{key} below floor {floors[floor_key]}")
    if warns:
        receipt["warn"] = "below-floor: " + "; ".join(warns)
    receipt["floor_verdicts"] = verdicts
    receipt["host"]["loadavg_after"] = open("/proc/loadavg").read().split()[:3]
    receipt["safety"] = dict(guard.peaks)
    failed = [k for k, v in verdicts.items() if v == "fail"]
    if failed:
        raise RuntimeError("below 0.9x CONTRACT floor: "
                           + ", ".join(f"{k}={receipt[k]}" for k in failed))
    receipt["rc"] = 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence-dir",
                    default=f"evidence/inference-{datetime.now():%Y%m%d}")
    ap.add_argument("--kit-root", default=os.path.expanduser("~/kit"))
    ap.add_argument("--runtime", default=None,
                    help="runtime repo (default: <kit-root>/model/qwen38-27b-rtx3090)")
    ap.add_argument("--venv", default=None,
                    help="inference venv (default: <kit-root>/venv-inference)")
    ap.add_argument("--port", type=int, default=18020)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--load-timeout", type=int, default=1800,
                    help="seconds to wait for /health (model load is slow)")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--keep-server", action="store_true")
    args = ap.parse_args()

    kit = Path(args.kit_root)
    runtime = Path(args.runtime or kit / "model" / "qwen38-27b-rtx3090")
    venv = Path(args.venv or kit / "venv-inference")
    vpy = venv / "bin" / "python"
    # Run inside the inference venv: verify_runtime_versions and the
    # launcher both need it. --help already exited above in any python.
    if vpy.exists() and Path(sys.executable).resolve() != vpy.resolve():
        os.execv(str(vpy), [str(vpy), str(Path(__file__).resolve())]
                 + sys.argv[1:])

    recipe = json.loads(RECIPE_PATH.read_text())
    ev = Path(args.evidence_dir)
    (ev / "receipts").mkdir(parents=True, exist_ok=True)
    (ev / "logs").mkdir(parents=True, exist_ok=True)
    receipt = {"ts": datetime.now(timezone.utc).isoformat()}
    server_box = {}
    t0 = time.monotonic()
    error = ""
    try:
        run_smoke(args, recipe, runtime, venv, ev, receipt, server_box)
    except SafetyViolation as e:
        error = f"safety gate: {e}"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        guard = server_box.get("guard")
        server = server_box.get("server")
        if guard is not None:
            guard.stop()
        if server is not None and server.poll() is None \
                and not args.keep_server:
            try:
                os.killpg(os.getpgid(server.pid), signal.SIGTERM)
                server.wait(timeout=15)
            except Exception:  # noqa: BLE001
                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    pass
        receipt["rc"] = 0 if receipt.get("rc") == 0 and not error else 1
        if error:
            receipt["error"] = error
        receipt["elapsed_s"] = round(time.monotonic() - t0, 1)
        (ev / "receipts" / "inference-smoke.json").write_text(
            json.dumps(receipt, indent=2))
        print(f"smoke: wrote {ev}/receipts/inference-smoke.json "
              f"rc={receipt['rc']}", file=sys.stderr)
    return receipt["rc"]


if __name__ == "__main__":
    sys.exit(main())

