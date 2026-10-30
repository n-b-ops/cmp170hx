#!/usr/bin/env python3
"""test_watchdog.py - lifecycle families that drive the REAL gate module:
watchdog scope, retry-skip policy, missing-tool aborts, and shutdown
robustness. Run via tests/contract_sync.py.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time


def _args(tmp, tag):
    return argparse.Namespace(profile="express", gpu=0,
                              evidence_dir=os.path.join(tmp, "ev"),
                              tag=tag, skip="", only=None, power_w=180,
                              memtest_min=None, burn_min=None)


def watchdog_scope_tests(gate):
    """kill_proc is scoped to ITS process (an orphaned watchdog
    cannot kill a later stage's workload), and a stopped watchdog's stop
    Event stays set across a later start_watchdog (an orphaned loop can
    never be resurrected as a second sampler)."""
    fails = []
    tmp = tempfile.mkdtemp(prefix="cs-wd-")
    g = gate.Gate(_args(tmp, "wd"))
    p1 = subprocess.Popen(["sleep", "30"], start_new_session=True)
    p2 = subprocess.Popen(["sleep", "30"], start_new_session=True)
    if g.kill_proc(p1) is not True or p1.poll() is None:
        fails.append("kill_proc(live) must kill and report its own proc")
    if g.kill_proc(p1) is not False:
        fails.append("kill_proc(already dead) must report False")
    if p2.poll() is not None:
        fails.append("kill_proc(p1) must not touch a different process")
    g.kill_proc(p2)  # cleanup
    # Orphan disarm: a set (stopped) Event must survive a later start.
    old_stop = g.wd_stop = threading.Event()
    old_stop.set()
    dead = subprocess.Popen(["true"])
    dead.wait()
    g.smi = lambda *a, **k: "99,99"  # breach values on every sample
    g.start_watchdog(dead)           # must use a FRESH Event
    time.sleep(0.5)
    if not old_stop.is_set():
        fails.append("start_watchdog cleared a previous watchdog's stop "
                     "Event (orphan resurrection)")
    if g.thermal_flag.exists() or g.gone_flag.exists():
        fails.append("watchdog flagged a stop event for an already-exited "
                     "proc (post-exit transient)")
    g.stop_watchdog()
    shutil.rmtree(tmp, ignore_errors=True)
    return fails


def retry_skip_tests(gate):
    """A fatal Xid during the FIRST attempt skips the mem_vulkan/matmul retry: fail
    fast, keep the receipt totals, never burn a second workload run. A
    clean retryable rc still retries, and per-run counts merge."""
    fails = []
    real_run = gate.subprocess.run
    real_sleep = gate.time.sleep

    class _Ok:
        returncode = 0

    gate.subprocess.run = lambda *a, **k: _Ok()
    gate.time.sleep = lambda s: None
    tmpdirs = []
    try:
        def mk():
            tmp = tempfile.mkdtemp(prefix="cs-retry-")
            tmpdirs.append(tmp)
            return gate.Gate(_args(tmp, "retry"))

        def stub(g, rc_seq, xid_seq):
            runs = []
            rec = {}

            def fake_work(logrel, cmd, watchdog=True):
                runs.append(logrel)
                return rc_seq[min(len(runs) - 1, len(rc_seq) - 1)]

            g.run_work = fake_work
            g.wd_flags = lambda: (0, 0)
            seq = iter(list(xid_seq))
            g.count_xids = lambda: next(seq)
            g.receipt = lambda name, **kw: rec.setdefault(name, kw)
            g.check_ring_wrap = lambda: None
            g.assert_cap = lambda: None
            g.log = lambda msg: None
            g.matmul_py = "/usr/bin/python3"
            g.bus_id = "00000000:01:00.0"
            return runs, rec

        # matmul: rc=3 + fatal Xid on run 1 -> GateAbort, exactly one run
        g = mk()
        runs, rec = stub(g, [3], [{"fatal": 1, "noted": 0, "proc": 0}])
        try:
            g.stage_matmul()
            fails.append("matmul: fatal xid on run 1 must die, not return")
        except gate.GateAbort:
            pass
        if len(runs) != 1:
            fails.append(f"matmul: fatal xid on run 1 must not retry ({len(runs)} runs)")
        if rec.get("matmul", {}).get("xid_fatal") != 1:
            fails.append("matmul: fatal-xid receipt must carry xid_fatal=1")

        # matmul: rc=3 clean -> retry once, merged counts, PASS (no die)
        g = mk()
        runs, rec = stub(g, [3, 0], [{"fatal": 0, "noted": 1, "proc": 0},
                                     {"fatal": 0, "noted": 0, "proc": 1}])
        g.stage_matmul()
        if len(runs) != 2:
            fails.append(f"matmul: clean rc=3 must retry once ({len(runs)} runs)")
        r = rec.get("matmul", {})
        if (r.get("xid_noted"), r.get("xid_proc"), r.get("retried")) != (1, 1, 1):
            fails.append(f"matmul: retry receipt must merge per-run xid counts: {r}")

        # mem_vulkan: rc=64 + fatal Xid on run 1 -> GateAbort, exactly one run
        g = mk()
        runs, rec = stub(g, [64], [{"fatal": 1, "noted": 0, "proc": 0}])
        try:
            g.stage_mem_vulkan()
            fails.append("mem_vulkan: fatal xid on run 1 must die, not return")
        except gate.GateAbort:
            pass
        if len(runs) != 1:
            fails.append(f"mem_vulkan: fatal xid on run 1 must not retry ({len(runs)} runs)")
        if rec.get("mem_vulkan", {}).get("xid_fatal") != 1:
            fails.append("mem_vulkan: fatal-xid receipt must carry xid_fatal=1")

        # mem_vulkan: rc=64 clean -> retry once, INCOMPLETE return (no die)
        g = mk()
        runs, rec = stub(g, [64, 64], [{"fatal": 0, "noted": 0, "proc": 0},
                                       {"fatal": 0, "noted": 0, "proc": 0}])
        g.stage_mem_vulkan()
        if len(runs) != 2:
            fails.append(f"mem_vulkan: clean rc=64 must retry once ({len(runs)} runs)")
        if rec.get("mem_vulkan", {}).get("retried") != 1:
            fails.append("mem_vulkan: retry must be recorded (retried=1)")
    finally:
        gate.subprocess.run = real_run
        gate.time.sleep = real_sleep
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)
    return fails


def missing_tool_tests(gate):
    """A missing workload binary is a clean GateAbort (preflight is the
    primary defense for the tools it knows; run_work is the fallback)."""
    fails = []
    tmp = tempfile.mkdtemp(prefix="cs-tool-")
    try:
        g = gate.Gate(_args(tmp, "tool"))
        g.log = lambda msg: None
        try:
            g.run_work("logs/missing-tool.log", ["/nonexistent-tool-xyz"])
            fails.append("run_work on a missing binary must die cleanly")
        except gate.GateAbort:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def shutdown_robustness_tests(gate):
    """Cleanup must never eat the run's outcome: run()'s finally writes
    the summary FIRST and guards it, shutdown() swallows per-step
    failures (a host without pkill), and an aborted run still leaves a
    run-summary + no lock. Driven through run() itself with a preexisting
    lock (a deterministic preflight GateAbort) and a subprocess layer in
    which only pkill is missing - a busybox-less minimal host where every
    probe still works."""
    fails = []
    tmp = tempfile.mkdtemp(prefix="cs-shut-")
    real_run = gate.subprocess.run
    real_sleep = gate.time.sleep

    def hostile_run(cmd, *a, **k):
        if cmd and str(cmd[0]).endswith("pkill"):
            raise FileNotFoundError("pkill")
        return real_run(cmd, *a, **k)

    gate.subprocess.run = hostile_run
    gate.time.sleep = lambda s: None
    try:
        g = gate.Gate(_args(tmp, "shut"))
        g.log = lambda msg: None
        g.lock.touch()  # deterministic preflight death: "lock exists"
        try:
            rc = g.run()
        except Exception as e:  # noqa: BLE001
            fails.append(f"run() must abort cleanly on a hostile host, "
                         f"got {e!r}")
            rc = None
        if rc != 1:
            fails.append(f"aborted run() must return 1, got {rc!r}")
        summary = os.path.join(tmp, "ev", "receipts", "run-summary.json")
        if not os.path.exists(summary):
            fails.append("run() must leave a run-summary even when "
                         "cleanup subprocesses fail")
        else:
            s = json.load(open(summary))
            if str(s.get("aborted")) != "1":
                fails.append("hostile-host run summary must record "
                             f"aborted=1, got {s.get('aborted')!r}")
        if g.lock.exists():
            fails.append("shutdown must remove the lock even when "
                         "subprocesses fail")
    finally:
        gate.subprocess.run = real_run
        gate.time.sleep = real_sleep
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def run(gate):
    fails = []
    fails.extend(watchdog_scope_tests(gate))
    fails.extend(retry_skip_tests(gate))
    fails.extend(missing_tool_tests(gate))
    fails.extend(shutdown_robustness_tests(gate))
    return fails

