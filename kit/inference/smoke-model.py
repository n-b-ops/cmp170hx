#!/usr/bin/env python3
"""smoke-model.py - informational serving smoke for a recipe model.

Serves one kit/inference/recipes/<model>.json recipe via plain "vllm
serve" (the recipe's argv, @MODEL_DIR@ substituted), waits for /health,
runs the club's usage-accounted benchmark protocol (vendor
live_benchmark.py) under its fail-closed telemetry guard, and writes
receipts/inference-<key>.json with the SAME keys the report and
fingerprint already read (rc, decode256, decode900, prefill, ttft).

INFORMATIONAL semantics (unlike the acceptance smoke.py): rc=0 whenever
the protocol completes - anchors are community/lab-reported context and
deltas are recorded, never gated. rc=1 only on a protocol failure
(server crash, health timeout, safety stop, missing usage objects) -
that IS a finding and reads as a failed inference stage in the report.

Safety (SPEC-02 s0.2): the vendor guard stops the run at core >= 80 C,
memory >= 85 C, missing telemetry, unexpected/locked device, or any Xid
/ fallen-off-the-bus line in the kernel log. Forced airflow first - the
card is passive.

Usage: <venv-key>/bin/python kit/inference/smoke-model.py --recipe <x.json>
       Auto re-execs under <kit-root>/venv-<key> when needed.
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
RECIPES = REPO / "kit" / "inference" / "recipes"
VENDOR = REPO / "vendor" / "club-170hx" / "recipes" / "qwen3.8-27b-dflash2"
sys.path.insert(0, str(VENDOR))
from live_benchmark import LONG, P256, Benchmark  # noqa: E402
from live_support import (FailClosedTelemetryGuard,  # noqa: E402
                          SafetyViolation, preflight_snapshot)


def run_model_smoke(args, recipe, runtime_box, ev, receipt):
    kit = Path(args.kit_root)
    key = recipe["key"]
    model_dir = kit / "model" / key
    venv = Path(args.venv or kit / ("venv-" + key))
    rev_file = model_dir / "REVISION.txt"
    if not rev_file.is_file():
        raise RuntimeError(f"missing {rev_file} - run fetch-model.sh/install-model.sh")
    receipt["model_revision"] = rev_file.read_text().strip()

    # fail-closed preflight: right device, unlocked, temps safe
    receipt["device"] = preflight_snapshot(args.gpu)
    guard = FailClosedTelemetryGuard(
        args.gpu, ev / "logs" / ("safety-" + key + ".jsonl"),
        sample_interval_s=1.0)
    receipt["host"] = {"nproc": os.cpu_count(),
                       "loadavg_before": open("/proc/loadavg").read().split()[:3]}
    guard.start()
    runtime_box["guard"] = guard

    # Engine gate BEFORE any GPU work: a drifted venv would serve fine
    # but record numbers the anchors are not comparable to (install-model
    # checks at install time; this closes the gap at run time).
    model_bin = venv / "bin" / "vllm"
    if not model_bin.is_file():
        raise RuntimeError(f"{model_bin} missing - run install-model.sh --recipe")
    spec = str(recipe["engine"]["version"])
    got = subprocess.run(
        [str(venv / "bin" / "python"), "-c",
         "import importlib.metadata as m; print(m.version('vllm'))"],
        capture_output=True, text=True).stdout.strip()
    want = spec.lstrip(">=")
    ok = (got == want) if not spec.startswith(">=") else (got >= want)
    if not ok:
        raise RuntimeError(f"venv vllm {got} does not satisfy the recipe "
                           f"spec {spec} - re-run install-model.sh")
    receipt["engine"] = {"vllm": got, "spec": spec}
    argv = []
    for a in recipe["serve"]["argv"]:
        argv.append(str(model_dir) if a == "@MODEL_DIR@" else a)
    served = recipe["serve"].get("served_model_name", key)
    port = args.port or recipe["serve"].get("port_default", 18030)
    argv += ["--port", str(port)]
    env = dict(os.environ)
    env.update({k: str(v) for k, v in recipe["serve"].get("env", {}).items()})
    env["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", "")
    env.setdefault("HF_HOME", str(kit / ".hf-cache"))
    env["HF_HUB_OFFLINE"] = "1"
    env["VLLM_NO_USAGE_STATS"] = "1"
    env["DO_NOT_TRACK"] = "1"
    api_key = secrets.token_urlsafe(16)
    env["VLLM_API_KEY"] = api_key
    server_log = open(ev / "logs" / ("server-" + key + ".log"), "w")
    server = subprocess.Popen([str(a) for a in argv], env=env,
                              stdout=server_log, stderr=subprocess.STDOUT,
                              start_new_session=True)
    runtime_box["server"] = server
    receipt["serve_argv"] = argv

    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + args.load_timeout
    healthy = False
    while time.monotonic() < deadline:
        guard.assert_safe()
        if server.poll() is not None:
            raise RuntimeError("server exited during startup - see "
                               f"{ev}/logs/server-{key}.log")
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as r:
                if r.status == 200:
                    healthy = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    if not healthy:
        raise RuntimeError(f"no healthy server after {args.load_timeout}s - "
                           f"see {ev}/logs/server-{key}.log")
    receipt["server"] = {"port": port,
                         "load_s": round(time.monotonic()
                                         - (deadline - args.load_timeout), 1)}
    time.sleep(10)  # settle

    # the club protocol, usage-object token counting; /tokenize needs the
    # recipe's served-model name, hence the small subclass.
    sink = io.StringIO()

    class RecipeBenchmark(Benchmark):
        def __init__(self, base_url, api_key, safety_check, served):
            super().__init__(base_url, api_key, safety_check)
            self.served = served

        def token_count(self, prompt):
            req = urllib.request.Request(
                self.base_url + "/tokenize",
                data=json.dumps({"model": self.served,
                                 "prompt": prompt}).encode(),
                headers=self.headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                toks = json.load(r).get("tokens")
            if not isinstance(toks, list):
                raise RuntimeError("tokenizer response omitted tokens")
            return len(toks)

    bench = RecipeBenchmark(base, api_key, guard.assert_safe, served)
    story = bench.token_count(P256)
    long = bench.token_count(LONG)
    bench.run_case(sink, "decode256", P256, 256, story,
                   warmups=args.warmup, samples=args.samples)
    bench.run_case(sink, "decode900", P256, 900, story,
                   warmups=args.warmup, samples=args.samples)
    bench.run_case(sink, "prefill_long", LONG, 8, long,
                   warmups=args.warmup, samples=args.samples)
    summaries = {}
    for line in sink.getvalue().splitlines():
        rec = json.loads(line)
        if rec.get("type") == "summary":
            summaries[rec["case"]] = rec
    (ev / "logs" / ("bench-" + key + ".jsonl")).write_text(sink.getvalue())
    receipt.update({
        "decode256": summaries.get("decode256", {}).get("decode_tok_s"),
        "decode900": summaries.get("decode900", {}).get("decode_tok_s"),
        "prefill": summaries.get("prefill_long", {}).get("prefill_tok_s"),
        "ttft": summaries.get("decode256", {}).get("mean_ttft_ms"),
        "prompt_tokens": {"story": story, "long": long},
        "case_summaries": summaries,
        "anchors": recipe.get("anchors", {}),
        "informational": True,
    })
    receipt["host"]["loadavg_after"] = open("/proc/loadavg").read().split()[:3]
    receipt["safety"] = dict(guard.peaks)
    receipt["rc"] = 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipe", required=True,
                    help="recipe json (name under kit/inference/recipes or path)")
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--kit-root", default=os.path.expanduser("~/kit"))
    ap.add_argument("--venv", default=None,
                    help="default: <kit-root>/venv-<key>")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--load-timeout", type=int, default=1800)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=1)
    args = ap.parse_args()

    rpath = Path(args.recipe)
    if not rpath.is_file():
        rpath = RECIPES / args.recipe
    recipe = json.loads(rpath.read_text())
    key = recipe["key"]
    ev = Path(args.evidence_dir or f"evidence/info-{key}-{datetime.now():%Y%m%d}")
    (ev / "receipts").mkdir(parents=True, exist_ok=True)
    (ev / "logs").mkdir(parents=True, exist_ok=True)

    kit = Path(args.kit_root)
    vpy = kit / ("venv-" + key) / "bin" / "python"
    if vpy.exists() and Path(sys.executable).resolve() != vpy.resolve():
        os.execv(str(vpy), [str(vpy), str(Path(__file__).resolve()),
                            *sys.argv[1:]])
    if not vpy.is_file():
        # Fast fail before any guard/server work starts: without the
        # per-recipe venv the smoke cannot run (previously this only
        # surfaced at the vllm binary check, seconds later).
        sys.exit(f"smoke-model: venv '{vpy.parent.parent.name}' missing "
                 f"under {kit} - run install-model.sh --recipe "
                 f"{args.recipe} first")

    receipt = {"ts": datetime.now(timezone.utc).isoformat(),
               "model": key, "informational": True}
    runtime_box = {}
    t0 = time.monotonic()
    error = ""
    try:
        run_model_smoke(args, recipe, runtime_box, ev, receipt)
    except SafetyViolation as e:
        error = f"safety gate: {e}"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        guard = runtime_box.get("guard")
        server = runtime_box.get("server")
        if guard is not None:
            guard.stop()
        if server is not None and server.poll() is None:
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
        (ev / "receipts" / f"inference-{key}.json").write_text(
            json.dumps(receipt, indent=2))
        print(f"smoke-model: wrote {ev}/receipts/inference-{key}.json "
              f"rc={receipt['rc']}", file=sys.stderr)
    return receipt["rc"]


if __name__ == "__main__":
    sys.exit(main())

