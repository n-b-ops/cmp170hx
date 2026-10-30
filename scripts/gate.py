#!/usr/bin/env python3
"""gate.py - single-card CMP 170HX acceptance gate (SPEC-02 executor).

The stage contract (pass/warn/fail signals) is defined AUTHORITATIVELY in
docs/CONTRACT.md; this program and make-report.py implement it, and
tests/contract_sync.py enforces the sync by importing the constants below.

Design rationale and invariants: docs/design.md (why Python, the
watchdog's per-thread Event and proc-scoped kill, die()'s guaranteed
run-summary, the retry order). CONTRACT.md defines the signals;
git log has the commit-level provenance.

Exit codes: 0 = run completed (see make-report for the verdict),
1 = run aborted (die/exception; an aborted run-summary is still written),
2 = usage error.

Usage: gate.py [--profile smoke|express|standard|extended] [--gpu N] [--tag X]
               [--evidence-dir DIR] [--only STAGES] [--skip STAGES]
               [--power-w W] [--memtest-min MIN] [--burn-min MIN]
               [--allow-shared-host] [--allow-no-dmesg]
Env: CLUB, BIN, TG_MATMUL_PYTHON - each overridable by the --club,
     --bin, --matmul-python flags.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# The CONTRACT.md constants, the Xid/brake classifiers, and the shared
# stage-verdict engine live in verdicts.py (single source of truth -
# make-report.py imports the same module; tests/contract_sync.py fails if
# either script grows a local copy).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verdicts import (TFLOPS_FAIL, TFLOPS_WARN, TFLOPS_DEAD, CORE_STOP_C,  # noqa: E402
                      MEM_STOP_C, WD_INTERVAL_S, WD_SMI_TIMEOUT_S,
                      LOW_POWER_INFO_W, XID_FATAL, XID_NOTED, XID_PROC,
                      classify_xid, classify_brake, classify_tflops,
                      parse_tflops)

STAGES = ["intake", "idle", "mem_pattern", "mem_retention", "mem_vulkan", "burn", "matmul", "pcie", "warm_rerun"]


def _dmesg_cksum(line):
    """Ring-wrap checksum of the first dmesg line: length + first 8 hex
    of md5. Transparent and process-stable - hash() is salted per
    process (PYTHONHASHSEED), opaque to a bundle reader and impossible
    to reproduce from a shell against the evidence."""
    return f"{len(line)}:{hashlib.md5(line.encode()).hexdigest()[:8]}"


class GateAbort(Exception):
    """Raised by die(); the finally-block still writes a run-summary."""


class Gate:
    def __init__(self, args):
        self.args = args
        self.profile = args.profile
        self.gpu = args.gpu
        root = Path(__file__).resolve().parent.parent
        # CLI flags override the env vars; getattr keeps unit-test
        # Namespaces (which lack these attrs) on the env path.
        self.club = Path(getattr(args, "club", None)
                         or os.environ.get("CLUB", root / "vendor" / "club-170hx"))
        self.bin = Path(getattr(args, "bin", None)
                        or os.environ.get("BIN", root / "bin"))
        self.matmul_py = (getattr(args, "matmul_python", None)
                          or os.environ.get("TG_MATMUL_PYTHON", ""))
        if self.profile == "smoke":
            self.memtest_min, self.burn_min, self.idle_min = 10, 5, 2
        elif self.profile == "express":
            self.memtest_min, self.burn_min, self.idle_min = 60, 10, 5
        elif self.profile == "extended":
            self.memtest_min, self.burn_min, self.idle_min = 240, 30, 10
        else:
            self.memtest_min, self.burn_min, self.idle_min = 120, 30, 5

        self.evid = Path(args.evidence_dir or
                         f"evidence/{datetime.now():%Y%m%d}-{args.tag}")
        for sub in ("env", "telemetry", "xid", "receipts", "logs"):
            (self.evid / sub).mkdir(parents=True, exist_ok=True)
        self.lock = self.evid / "gate.lock"
        self.gate_log = self.evid / "gate.log"
        self.thermal_flag = self.evid / "receipts" / ".thermal_stop"
        self.gone_flag = self.evid / "receipts" / ".gpu_gone"
        self.run_start = self.evid / "receipts" / ".run_start"
        self.summary_written = False
        self.bus_id = ""
        self.bus_short = ""
        self.brake_state = "unknown"
        self.brake_class = "unknown"
        self.mem_tot = None
        self.target_gib = None
        self.profile_kind = ""
        self.dm_start_lines = 0
        self.dm_cursor = 0
        self.dm_first_cksum = ""
        self.current_proc = None
        self.proc_lock = threading.Lock()
        self.wd_stop = threading.Event()
        self.wd_thread = None
        self.tele_proc = None
        self.xid_proc = None
        self.skip = set(args.skip.split(",")) if args.skip else set()
        self.only = set(args.only.split(",")) if args.only else None
        if self.profile == "smoke":
            # Quick-alive subset (rented instances): a smoke run is never
            # an acceptance verdict - make-report caps it at SMOKE.
            self.skip |= {"idle", "mem_retention", "mem_vulkan", "warm_rerun"}
        # Degraded-mode flags (seller rigs, rented instances, containers).
        # getattr defaults keep unit-test Namespaces working.
        self.allow_shared = getattr(args, "allow_shared_host", False)
        self.no_dmesg = getattr(args, "allow_no_dmesg", False)
        self.gpu_count = 1

    # ---------------- infrastructure ----------------
    def log(self, msg):
        line = f"[{datetime.now().isoformat(timespec='seconds')}] gate: {msg}"
        print(line, file=sys.stderr)
        with open(self.gate_log, "a") as f:
            f.write(line + "\n")

    def die(self, msg):
        raise GateAbort(msg)

    def receipt(self, name, **kw):
        kw.setdefault("ts", datetime.now(timezone.utc).isoformat())
        p = self.evid / "receipts" / f"{name}.json"
        p.write_text(json.dumps(kw, indent=2))

    def smi(self, *fields, idx=None, timeout=None):
        i = ["-i", str(self.gpu)] if idx is None else ["-i", str(idx)]
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=" + ",".join(fields),
                 "--format=csv,noheader,nounits", *i],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip().split("\n")[0]

    def stage_enabled(self, name):
        if name in self.skip:
            return False
        if self.only is not None and name not in self.only:
            return False
        return True

    def assert_cap(self):
        # Round and allow +-1 W AFTER ROUNDING: drivers report 179.99 for a
        # set 180 cap; the effective window is [w-1.49, w+1.49], slightly
        # wider than the label, in the safe direction.
        try:
            if os.geteuid() == 0:
                subprocess.run(["nvidia-smi", "-i", str(self.gpu),
                                "-pl", str(self.args.power_w)],
                               capture_output=True, timeout=WD_SMI_TIMEOUT_S)
            elif shutil.which("sudo"):
                subprocess.run(["sudo", "-n", "nvidia-smi", "-i", str(self.gpu),
                                "-pl", str(self.args.power_w)],
                               capture_output=True, timeout=WD_SMI_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            pass
        cur = self.smi("power.limit", timeout=WD_SMI_TIMEOUT_S)
        if cur is None:
            self.die("power cap unreadable (nvidia-smi failed or timed out)")
        try:
            c = round(float(cur.split(",")[0]))
        except ValueError:
            self.die(f"power cap unreadable (got {cur!r})")
        if not (self.args.power_w - 1 <= c <= self.args.power_w + 1):
            self.die(f"power cap is {c} W, wanted {self.args.power_w} W "
                     f"(+-1 W after rounding) and could not set it "
                     f"(needs sudo or root)")

    # ---------------- watchdog (thread: shared state, no subshell copies) -
    def wd_flags(self):
        return (1 if self.thermal_flag.exists() else 0,
                1 if self.gone_flag.exists() else 0)

    def kill_proc(self, proc):
        """TERM(->KILL) ONE workload process group. Returns True iff it was
        live - callers use this so a stop-event FLAG is only set when a
        workload was actually interrupted: a thermal sample in
        the gap after a clean workload exit must not poison the rest of the
        run. Taking the proc as an argument (not self.current_proc) also
        means an orphaned watchdog thread can only ever reach ITS OWN
        stage's workload, never a later stage's."""
        killed = False
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                killed = True
            except (ProcessLookupError, PermissionError):
                pass
        return killed

    def kill_work(self):
        """Kill the CURRENT workload group (shutdown path); same
        flag-only-on-true-kill contract as kill_proc."""
        with self.proc_lock:
            proc = self.current_proc
        return self.kill_proc(proc)

    def watchdog_loop(self, stop, proc):
        """One watchdog per workload: this thread owns its own stop Event
        and its own proc reference. stop_watchdog() sets THAT Event; the
        next stage's watchdog gets a fresh one, so a thread stuck in a hung
        nvidia-smi past its join timeout can never be resurrected as a
        second sampler, and kill_proc(proc) means it can never reach a
        later stage's workload."""
        while not stop.is_set():
            line = self.smi("temperature.gpu", "temperature.memory",
                            timeout=WD_SMI_TIMEOUT_S)
            if line:
                try:
                    core, mem = (int(float(x)) for x in line.split(",")[:2])
                    if core >= CORE_STOP_C or mem >= MEM_STOP_C:
                        self.log(f"WATCHDOG STOP: core={core}C mem={mem}C "
                                 f"- killing workload group")
                        # Flag ONLY if a live workload was interrupted: the
                        # card keeps rising after a clean exit, and a sample
                        # in that window must not fail every later stage
                        #.
                        if self.kill_proc(proc):
                            self.thermal_flag.touch()
                        else:
                            self.log("WATCHDOG: breach sampled with no live "
                                     "workload (post-exit transient) - not "
                                     "recorded as a stop event")
                        return
                except ValueError:
                    pass
            # A probe timeout is NOT gpu-gone: that flag drives REJECT and
            # demands a confirmed disappearance, while a hung probe is
            # ambiguous (wedged driver vs busy host). subprocess.run kills
            # the hung child itself.
            try:
                present = subprocess.run(
                    ["nvidia-smi", "-i", str(self.gpu)],
                    capture_output=True,
                    timeout=WD_SMI_TIMEOUT_S).returncode == 0
            except subprocess.TimeoutExpired:
                self.log(f"WATCHDOG: nvidia-smi timed out after "
                         f"{WD_SMI_TIMEOUT_S}s (wedged driver?) - not "
                         f"treated as gpu-gone; sampling continues")
                stop.wait(WD_INTERVAL_S)
                continue
            if not present:
                self.log(f"WATCHDOG: GPU {self.gpu} disappeared from nvidia-smi")
                if self.kill_proc(proc):
                    self.gone_flag.touch()
                return
            stop.wait(WD_INTERVAL_S)

    def start_watchdog(self, proc):
        # Per-thread Event: a new watchdog NEVER clears a previous thread's
        # stop Event, so an orphaned loop (stuck past its join timeout)
        # stays disarmed instead of waking as a second sampler.
        stop = threading.Event()
        self.wd_stop = stop
        self.wd_thread = threading.Thread(
            target=self.watchdog_loop, args=(stop, proc), daemon=True)
        self.wd_thread.start()

    def stop_watchdog(self):
        if self.wd_stop:
            self.wd_stop.set()
        if self.wd_thread:
            self.wd_thread.join(timeout=WD_INTERVAL_S + 5)
            if self.wd_thread.is_alive():
                self.log(f"WARN: watchdog thread still alive after "
                         f"{WD_INTERVAL_S + 5}s (stuck in nvidia-smi?); it "
                         f"is orphaned but disarmed - its stop Event is "
                         f"set and it can only reach its own (exited) "
                         f"workload")
            self.wd_thread = None

    def run_work(self, logrel, cmd, watchdog=True):
        """Run a workload as its own process group; the watchdog can kill it.
        Returns the exit code."""
        logp = self.evid / logrel
        # Children see exactly the card under test as CUDA device 0: stage
        # argv then uses the single visible index regardless of the host's
        # GPU count (the CUDA runtime remaps; the gate's own nvidia-smi
        # probes keep physical indices, which is what NVML expects).
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = str(self.gpu)
        with open(logp, "w") as lf:
            with self.proc_lock:
                try:
                    self.current_proc = proc = subprocess.Popen(
                        cmd, stdout=lf, stderr=subprocess.STDOUT,
                        start_new_session=True, env=env)
                except FileNotFoundError as e:
                    # Belt-and-braces behind preflight: preflight checks the
                    # tools it knows about; anything else that fails to
                    # spawn still aborts cleanly, never a traceback.
                    self.die(f"missing tool: {e} - stand/setup gap, "
                             f"not a card defect")
            if watchdog:
                # Started AFTER the Popen and bound to THIS proc: no
                # sampling window before the workload exists, and the
                # thread cannot reach any other stage's process.
                self.start_watchdog(proc)
            rc = proc.wait()
        # Stop the watchdog BEFORE clearing current_proc: kill_work() must
        # never observe a half-torn-down stage.
        if watchdog:
            self.stop_watchdog()
        with self.proc_lock:
            self.current_proc = None
        return rc

    # ---------------- dmesg / Xid accounting ----------------
    @staticmethod
    def dmesg_lines():
        out = subprocess.run(["dmesg"], capture_output=True, text=True)
        return out.stdout.splitlines() if out.returncode == 0 else []

    def check_ring_wrap(self):
        lines = self.dmesg_lines()
        if not lines:
            return
        ck = _dmesg_cksum(lines[0])
        if ck != self.dm_first_cksum:
            self.die("dmesg ring buffer wrapped (first line changed) - Xid "
                     "attribution window unreliable; rerun with a larger "
                     "kernel ring (log_buf_len)")

    def count_xids(self):
        """Xids in (dm_cursor, now] by class; advances the cursor."""
        lines = self.dmesg_lines()
        new = lines[self.dm_cursor:]
        self.dm_cursor = len(lines)
        counts = {"fatal": 0, "noted": 0, "proc": 0}
        for ln in new:
            c = classify_xid(ln, self.bus_short)
            if c:
                counts[c] += 1
        return counts

    def xid_totals_since_start(self):
        if self.dm_start_lines == 0 or not self.bus_short:
            # Preflight died before session markers existed: counting now
            # would attribute the whole kernel ring (bus filter empty) to a
            # run that never touched the GPU.
            return {"fatal": 0, "noted": 0, "proc": 0}
        lines = self.dmesg_lines()
        counts = {"fatal": 0, "noted": 0, "proc": 0}
        for ln in lines[self.dm_start_lines:]:
            c = classify_xid(ln, self.bus_short)
            if c:
                counts[c] += 1
        return counts

    # ---------------- stages ----------------
    def read_brake_state(self):
        """Current HW Power Brake Slowdown value from nvidia-smi -q;
        "unknown" when the field is missing or the probe times out -
        never a silent pass (the classifier warns loudly on unknown)."""
        try:
            q = subprocess.run(["nvidia-smi", "-q", "-i", str(self.gpu)],
                               capture_output=True, text=True,
                               timeout=WD_SMI_TIMEOUT_S).stdout
        except subprocess.TimeoutExpired:
            return "unknown"
        m = re.search(r"HW Power Brake Slowdown\s*:\s*(\S.*?)\s*$", q, re.M)
        return m.group(1) if m else "unknown"

    def collect_inventory(self):
        """One structured inventory snapshot: section -> raw text.

        Written verbatim (title + body) to env/inventory.txt by
        write_inventory(); pcie reuses the SAME collector for its
        after-snapshot, so the before/after section sets are identical
        by construction instead of maintained twice. Also captures the
        brake state (a missing/unreadable field -> "unknown", never a
        silent pass).
        """
        sec = {}
        sec["lspci"] = subprocess.run(
            ["sh", "-c", "lspci -nn | grep -iE '10de:20b0|10de:20c2|10de:2082' || true"],
            capture_output=True, text=True).stdout
        sec["nvidia-smi"] = str(self.smi(
            "index,name,pci.bus_id,pci.device_id,pci.sub_device_id,"
            "memory.total,power.limit,temperature.gpu,temperature.memory,vbios_version",
            timeout=WD_SMI_TIMEOUT_S))
        sec["module"] = subprocess.run(
            ["sh", "-c", "modinfo -n nvidia; modinfo nvidia | grep -E '^(filename|version|srcversion):' || true"],
            capture_output=True, text=True).stdout
        sec["brake"] = self.read_brake_state()
        sec["unlocker meta"] = subprocess.run(
            ["sh", "-c", f"cat /lib/modules/$(uname -r)/updates/cmpunlocker/"
                         f"{{driver_version,card_profile,unlock_geometry}} 2>/dev/null || true"],
            capture_output=True, text=True).stdout
        sec["kernel/driver"] = subprocess.run(
            ["sh", "-c", "uname -a; cat /proc/driver/nvidia/version 2>/dev/null || true"],
            capture_output=True, text=True).stdout
        sec["pcie before"] = subprocess.run(
            ["bash", str(self.club / "scripts/qc/pcie-check.sh")],
            capture_output=True, text=True).stdout
        sec["AER before"] = subprocess.run(
            ["sh", "-c", f"lspci -vv -s {self.bus_short} 2>/dev/null | grep -E 'DevSta|UESta|CESta' || true"],
            capture_output=True, text=True).stdout
        if self.gpu_count > 1:
            sec["host GPUs (shared host, --allow-shared-host)"] = subprocess.run(
                ["sh", "-c", "nvidia-smi -L || true"],
                capture_output=True, text=True).stdout
        return sec

    def write_inventory(self, sec):
        (self.evid / "env" / "inventory.txt").write_text(
            "\n".join(f"== {k} ==\n{v}" for k, v in sec.items()))

    def stage_intake(self):
        self.log("intake: inventory (manual T0.2 enumeration before the "
                 "unlock is in SPEC-02 sT0.2)")
        sec = self.collect_inventory()
        self.brake_state = sec["brake"]
        self.write_inventory(sec)

        # Spellings and the pass/asserting/unknown/unrecognized classes
        # live in verdicts.classify_brake (its docstring explains why a
        # bare "active" substring dies and verbose "Inactive (...)" never
        # does). Here: die on asserting, warn on unknown/unrecognized,
        # and record brake_class so the report can correlate a burn FAIL
        # with a PWRBRK# slot fault (it presents as low TFLOPs).
        self.brake_class = classify_brake(self.brake_state)
        if self.brake_class == "asserting":
            self.die(f"intake: HW Power Brake Slowdown is '{self.brake_state}' "
                     f"- stand slot fault (PWRBRK#, ~88 W cap); fix the stand "
                     f"(different slot/board) before any performance verdict")
        if self.brake_class == "unknown":
            self.log("WARN: brake state unreadable (no 'HW Power Brake "
                     "Slowdown' field in nvidia-smi -q output - driver "
                     "format drift?) - not gating; verify PWRBRK# manually "
                     "and in telemetry")
        elif self.brake_class == "unrecognized":
            self.log(f"WARN: brake state '{self.brake_state}' unrecognized; "
                     f"treating as not-asserting (verify in telemetry)")
        mem = self.smi("memory.total")
        try:
            self.mem_tot = int(float(mem.split(",")[0]))
        except (ValueError, AttributeError):
            self.die(f"memory.total unreadable (got {mem!r})")
        if self.mem_tot >= 60000:
            self.profile_kind, cap = "8gb(64GiB)", 62
        elif self.mem_tot >= 35000:
            self.profile_kind, cap = "10gb(40GiB)", 38
        elif 30000 <= self.mem_tot <= 34000:
            self.die(f"intake: memory.total={self.mem_tot} MiB ~ 32 GiB - "
                     f"single-rank 8 GB card (half the usable capacity of a "
                     f"dual-rank 64 GB card); distinct finding, not an install "
                     f"failure - renegotiate or reject")
        else:
            self.die(f"intake: memory.total={self.mem_tot} MiB is not an "
                     f"unlocked geometry - run T1 (SPEC-01 s4) first")
        # Derive the test target from the MEASURED total, not the SKU label:
        # the unlock bands accept >=60000 MiB, but that is only ~58.6 GiB -
        # a fixed 62 GiB allocation would false-fail a WARN-band card.
        self.target_gib = min(self.mem_tot // 1024 - 2, cap)
        if self.target_gib < 8:
            self.die(f"intake: derived test target {self.target_gib} GiB is "
                     f"too small to be meaningful")
        self.receipt("intake", memory_total_mib=self.mem_tot,
                     target_gib=self.target_gib, profile=self.profile_kind,
                     brake=self.brake_state, brake_class=self.brake_class)
        self.log(f"intake: memory.total={self.mem_tot} MiB -> {self.profile_kind}, "
                 f"compare_vram target {self.target_gib} GiB (derived), "
                 f"brake={self.brake_state}")

    def stage_idle(self):
        self.log(f"idle: baseline {self.idle_min} min")
        self.assert_cap()  # cheap invariant: nothing should have reset it
        time.sleep(self.idle_min * 60)
        csv_path = self.evid / "telemetry" / "session.csv"
        stats = {}
        if csv_path.exists():
            with open(csv_path) as fh:
                rows = list(csv.DictReader(fh))
            def col(name):
                vals = []
                for r in rows:
                    try:
                        vals.append(float(r.get(name, "n/a")))
                    except (TypeError, ValueError):
                        pass
                return vals
            stats = {
                "max_core_c": max(col("temperature.gpu"), default=None),
                "max_mem_c": max(col("temperature.memory"), default=None),
                "max_power_w": max(col("power.draw"), default=None),
                "rows": len(rows),
            }
            for k, c in (("mean_core_c", "temperature.gpu"),
                         ("mean_mem_c", "temperature.memory")):
                v = col(c)
                stats[k] = round(sum(v) / len(v), 1) if v else "n/a"
        self.receipt("idle", minutes=self.idle_min, **stats)

    def _finish_stage(self, name, rc=None, counts=None, **extra):
        """Write a load-stage receipt with the common tail (ring-wrap
        check, Xid counts, stop flags) and return (counts, ts, gg) for
        the caller's die logic. Does NOT decide failure - stages differ
        in check ORDER, not in the data collection. Callers that already
        hold cumulative counts (after a retry merge) pass them in;
        rc=None omits the rc key (stages with sub-rcs, like the warm
        rerun)."""
        self.check_ring_wrap()
        if counts is None:
            counts = self.count_xids()
        ts, gg = self.wd_flags()
        fields = dict(thermal_stop=ts, gpu_gone=gg,
                      xid_fatal=counts["fatal"], xid_noted=counts["noted"],
                      xid_proc=counts["proc"], **extra)
        if rc is not None:
            fields["rc"] = rc
        self.receipt(name, **fields)
        return counts, ts, gg

    def _fail_if_hard(self, name, rc, ts, gg, counts):
        """The immediate hard-fail conjunction every load stage shares:
        a nonzero rc, a stop event, or a fatal Xid (CONTRACT.md)."""
        if rc != 0 or ts == 1 or gg == 1 or counts["fatal"] != 0:
            self.die(f"{name} FAILED rc={rc} xid_fatal={counts['fatal']} "
                     f"thermal={ts} gpu_gone={gg} (hard fail per CONTRACT.md)")

    def _retry_budget(self, run_first, run_retry, should_retry, cleanup=None):
        """Run a workload with the shared one-shot retry shell: retry a
        retryable rc unless a stop event or a fatal Xid already fired -
        Xids are counted BEFORE the retry decision, so a first-attempt
        fatal fails fast instead of burning a second budget. The
        returned counts merge both attempts - the dmesg cursor advance
        would otherwise hide run 1's lines."""
        retried = 0
        rc = run_first()
        ts, gg = self.wd_flags()
        counts = self.count_xids()
        if should_retry(rc) and ts == 0 and gg == 0 and counts["fatal"] == 0:
            if cleanup:
                cleanup()
            self.log(f"retrying once (rc={rc} retryable, no stop event, "
                     f"no fatal Xid)")
            retried = 1
            rc = run_retry()
            ts, gg = self.wd_flags()
            retry_counts = self.count_xids()
            for k in counts:
                counts[k] += retry_counts[k]
        return rc, retried, counts, ts, gg

    def stage_mem_pattern(self):
        self.log(f"mem_pattern: CUDA near-full-VRAM pattern test ({self.target_gib} GiB)")
        self.assert_cap()
        rc = self.run_work("logs/mem_pattern.log",
                           [str(self.bin / "compare-vram"),
                            "--device", "0",
                            "--gib", str(self.target_gib)])
        counts, ts, gg = self._finish_stage("mem_pattern", rc,
                                            gib=self.target_gib)
        self._fail_if_hard("mem_pattern", rc, ts, gg, counts)
        self.log("mem_pattern: PASS")

    def stage_mem_retention(self):
        self.log("mem_retention: idle 60 s then re-verify")
        time.sleep(60)
        self.assert_cap()
        rc = self.run_work("logs/mem_retention.log",
                           [str(self.bin / "compare-vram"),
                            "--device", "0",
                            "--gib", str(self.target_gib)])
        counts, ts, gg = self._finish_stage("mem_retention", rc)
        self._fail_if_hard("mem_retention", rc, ts, gg, counts)
        self.log("mem_retention: PASS")

    def stage_mem_vulkan(self):
        self.log(f"mem_vulkan: memtest_vulkan full-VRAM, {self.memtest_min} min budget "
                 f"(rc=64 = setup error -> one retry unless a stop event fired)")
        self.assert_cap()
        self._pkill_memtest()
        time.sleep(2)
        sel = str(self.club / "scripts/qc/memtest-select.py")
        maxs = self.memtest_min * 60 + 900

        def _run(suffix):
            return self.run_work(
                f"logs/mem_vulkan{suffix}.log",
                ["python3", sel, self.bus_id, str(maxs),
                 str(self.evid / f"logs/mem_vulkan{suffix}.raw.log")])

        def _retry_cleanup():
            # Deliberately NOT kill_memtest_orphans(): the retry relaunches
            # the wrapper immediately, so it settles 5 s (vs 2 s) after the
            # pkill to let the detached grandchild actually die first.
            self.log("mem_vulkan: rc=64 (setup error), no stop event - clearing "
                     "orphans and retrying once")
            self._pkill_memtest()
            time.sleep(5)

        rc, retried, counts, ts, gg = self._retry_budget(
            run_first=lambda: _run(""), run_retry=lambda: _run("-retry"),
            should_retry=lambda r: r == 64, cleanup=_retry_cleanup)
        counts, ts, gg = self._finish_stage(
            "mem_vulkan", rc, counts=counts, retried=retried,
            minutes_budget=self.memtest_min)
        if counts["fatal"] != 0:
            self.die(f"mem_vulkan FAILED rc={rc} xid_fatal={counts['fatal']} "
                     f"(fatal Xid supersedes the rc=64 setup-error class)")
        if ts == 1 or gg == 1:
            # No explicit orphan kill here: die() lands in run()'s finally,
            # whose guarded shutdown() runs kill_memtest_orphans() anyway -
            # the explicit call only duplicated it.
            self.die("mem_vulkan FAILED - stop event during the memory test "
                     "(supersedes any rc; no retry after a stop event)")
        if rc == 64:
            self.log("mem_vulkan: INCOMPLETE - setup error persists after retry; the "
                     "memory test never ran (CONTRACT.md: CONDITIONAL, not "
                     "overridable by --accept-warnings)")
            return
        if rc != 0:
            self.die(f"mem_vulkan FAILED rc={rc} (card error - hard fail)")
        self.log("mem_vulkan: PASS")

    def stage_burn(self):
        self.log(f"burn: gpu-burn -tc {self.burn_min} min @ {self.args.power_w} W")
        self.assert_cap()
        rc = self.run_work(
            "logs/burn.log",
            [str(self.bin / "gpu-burn/gpu-burn"), "-tc", "-c",
             str(self.bin / "gpu-burn/compare.fatbin"), str(self.burn_min * 60)])
        low_power = self.args.power_w < LOW_POWER_INFO_W
        tflops = None
        logp = self.evid / "logs/burn.log"
        if logp.exists():
            tflops = parse_tflops(logp.read_text(errors="replace"))
        # Dead-card floor (any cap), 180 W-calibrated bands, and the
        # low-power informational flattening all live with the
        # thresholds in verdicts.classify_tflops.
        verdict = classify_tflops(tflops, low_power)
        counts, ts, gg = self._finish_stage(
            "burn", rc,
            tflops=tflops if tflops is not None else "n/a",
            tflops_verdict=verdict, minutes=self.burn_min,
            tflops_gate=("informational" if low_power else "gated"),
            brake=self.brake_state)
        self._fail_if_hard("burn", rc, ts, gg, counts)
        if verdict == "fail":
            # classify_tflops returns "fail" only for a parsed number -
            # tflops cannot be None on this path.
            floor = TFLOPS_DEAD if tflops < TFLOPS_DEAD else TFLOPS_FAIL
            self.die(f"burn FAILED: {tflops} TFLOP/s sustained < {floor} "
                     f"(CONTRACT.md; failed compute unlock gives ~12)")
        if low_power and tflops is not None:
            self.log(f"burn: WARN (informational) - {tflops} TFLOP/s at a "
                     f"{self.args.power_w} W cap: below {LOW_POWER_INFO_W} W "
                     f"the TFLOPS floors do not gate (calibrated at 180 W; "
                     f"SPEC-01 s2.2 stand-limited run)")
        elif verdict == "warn":
            self.log(f"burn: WARN - {tflops} TFLOP/s in warn band "
                     f"{TFLOPS_FAIL}-{TFLOPS_WARN} (SPEC-03 s2.3)")
        elif verdict == "unparsed":
            self.log("burn: WARN - could not parse TFLOP/s; check "
                     "logs/burn.log manually")
        else:
            self.log(f"burn: PASS ({tflops} TFLOP/s)")

    def stage_matmul(self):
        py = self.matmul_py or shutil.which("python3")
        if not py:
            self.log("matmul: SKIPPED - no python3 interpreter found")
            self.receipt("matmul", rc="skipped", reason="no python3")
            return
        try:
            # Bounded: a wedged driver or broken torch install can hang
            # the import for minutes (CUDA init); a healthy import
            # measures <5 s on the club stand.
            probe = subprocess.run([py, "-c", "import torch"],
                                   capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            self.log("matmul: WARN - torch import timed out after 60 s "
                     "(wedged driver or broken install) - skipping stage")
            probe = None
        except OSError:
            # Interpreter vanished or is not executable since preflight -
            # a clean skip with the reason, never a traceback.
            probe = None
        if probe is None or probe.returncode != 0:
            self.log("matmul: SKIPPED - no torch interpreter (set "
                     "TG_MATMUL_PYTHON; see README and kit/MANIFEST.md s5)")
            self.receipt("matmul", rc="skipped", reason="no torch")
            return
        self.log(f"matmul: matmul fingerprint + BF16 throughput gate via {py}")
        self.assert_cap()
        script = str(Path(__file__).resolve().parent / "matmul_check.py")

        def run(outdir, logrel):
            return self.run_work(logrel, [py, script, "0", "170170",
                                          str(self.evid / outdir), "3"])

        rc, retried, counts, ts, gg = self._retry_budget(
            run_first=lambda: run("logs/matmul", "logs/matmul.log"),
            run_retry=lambda: run("logs/matmul-retry", "logs/matmul-retry.log"),
            should_retry=lambda r: r not in (0, 2))
        ra = "logs/matmul-retry/matmul.json" if retried else ""
        # One receipt write; rc 2 is the warn class, recorded as rc=0 +
        # warn=bf16-speed so the report reads WARN, not FAIL. The die
        # checks below use the FRESH flags this call returns - a stop
        # event racing the write must die, never log PASS next to it.
        counts, ts, gg = self._finish_stage(
            "matmul", "0" if rc == 2 else str(rc),
            counts=counts, retried=retried, retry_artifact=ra,
            **({"warn": "bf16-speed"} if rc == 2 else {}))
        if counts["fatal"] != 0:
            self.die(f"matmul FAILED xid_fatal={counts['fatal']} (fatal Xid "
                     f"supersedes any rc class, including the speed-warn "
                     f"class - CONTRACT.md)")
        if ts == 1 or gg == 1:
            # Stop flags ride in the shared tail for parity with every
            # other load stage: make-report's preempt FAILs on them, and
            # the gate must agree instead of logging PASS next to a
            # thermal kill.
            self.die("matmul FAILED - stop event during the matmul stage "
                     "(supersedes any rc; no retry after a stop event)")
        if rc == 2:
            self.log("matmul: WARN - BF16 in warn band; verdict CONDITIONAL per CONTRACT.md")
        elif rc == 0:
            self.log("matmul: PASS")
        else:
            self.die("matmul FAILED after retry (non-deterministic, out-of-band, "
                     "or slow matmul - hard fail per CONTRACT.md)")

    def stage_pcie(self):
        self.log("pcie: PCIe/AER after-snapshot (compare to env/inventory.txt)")
        sec = self.collect_inventory()
        replay = subprocess.run(
            ["sh", "-c",
             f"nvidia-smi -q -i {self.gpu} | grep -A1 Replay || true"],
            capture_output=True, text=True).stdout
        # Same collector as intake: before/after is the call site, the
        # section set stays identical by construction.
        (self.evid / "receipts/pcie-after.txt").write_text("\n".join(
            [sec["pcie before"], sec["AER before"], replay]))
        self.receipt("pcie", note="diff vs env/inventory.txt pcie-before block")

    def stage_warm_rerun(self):
        self.log("warm_rerun: warm rerun (compare_vram + 10 min burn, no reboot)")
        self.assert_cap()
        rc1 = self.run_work("logs/warm_rerun-compare-vram.log",
                            [str(self.bin / "compare-vram"),
                             "--device", "0",
                             "--gib", str(self.target_gib)])
        rc2 = self.run_work("logs/warm_rerun-burn.log",
                            [str(self.bin / "gpu-burn/gpu-burn"), "-tc", "-c",
                             str(self.bin / "gpu-burn/compare.fatbin"), "600"])
        counts, ts, gg = self._finish_stage("warm_rerun",
                                            rc_memtest=rc1, rc_burn=rc2)
        if rc1 != 0 or rc2 != 0 or ts == 1 or gg == 1 or counts["fatal"] != 0:
            self.die("warm_rerun FAILED (heat-soak degradation - hard fail)")
        self.log("warm_rerun: PASS")

    # ---------------- lifecycle ----------------
    def write_summary(self, aborted):
        if self.summary_written:
            return
        tot = self.xid_totals_since_start()
        ts, gg = self.wd_flags()
        self.receipt("run-summary", profile=self.profile, gpu=self.gpu,
                     bus=self.bus_id or "unknown",
                     power_w=self.args.power_w,
                     shared_host=(self.gpu_count if self.gpu_count > 1 else 0),
                     xid_total=tot["fatal"], xid_noted=tot["noted"],
                     xid_proc=tot["proc"], thermal_stop=ts, gpu_gone=gg,
                     aborted=1 if aborted else 0, brake=self.brake_state,
                     xid_counting=("unavailable" if self.no_dmesg
                                   else "dmesg"),
                     memory_total_mib=self.mem_tot if self.mem_tot else "n/a")
        self.summary_written = True

    def preflight(self):
        if not shutil.which("nvidia-smi"):
            self.die("nvidia-smi not found")
        # Every probe is bounded: a wedged driver aborts
        # the run cleanly instead of hanging the operator at preflight.
        try:
            visible = subprocess.run(
                ["nvidia-smi", "--query-gpu=index",
                 "--format=csv,noheader", "-i", str(self.gpu)],
                capture_output=True, timeout=WD_SMI_TIMEOUT_S).returncode == 0
        except subprocess.TimeoutExpired:
            visible = False
        if not visible:
            self.die(f"GPU {self.gpu} not visible (nvidia-smi failed or "
                     f"timed out - wedged driver?)")
        if self.lock.exists():
            self.die(f"lock exists: {self.lock} (previous run unfinished?)")
        try:
            gpus = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True,
                timeout=WD_SMI_TIMEOUT_S).stdout.strip().splitlines()
        except subprocess.TimeoutExpired:
            gpus = []
        gpus = [g for g in gpus if g.strip()]
        self.gpu_count = len(gpus)
        if len(gpus) != 1 and not self.allow_shared:
            self.die(f"host has {len(gpus)} GPUs - the card under test must "
                     f"be the ONLY GPU (SPEC-01 s2.1); use a sole-GPU stand, "
                     f"run stages manually per SPEC-02 s0.2, or pass "
                     f"--allow-shared-host (bus-filtered Xids + a forced "
                     f"report warn; thermal/PSU confounds stay possible)")
        if len(gpus) > 1:
            self.log(f"shared host: {len(gpus)} GPUs visible, testing GPU "
                     f"{self.gpu} (workloads pinned via "
                     f"CUDA_VISIBLE_DEVICES; Xid lines bus-filtered)")
        fields = "gpu_bus_id,pid" if self.allow_shared else "pid"
        try:
            apps = subprocess.run(
                ["nvidia-smi", f"--query-compute-apps={fields}",
                 "--format=csv,noheader"],
                capture_output=True, text=True,
                timeout=WD_SMI_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # Refuse on an unreadable answer, but name the real cause: the
            # dies below would otherwise claim compute processes are active.
            self.die("nvidia-smi --query-compute-apps timed out - cannot "
                     "confirm a clean stand; rerun once the driver answers")
        procs = [l for l in (apps.stdout or "").splitlines() if l.strip()]
        if self.allow_shared:
            if apps.returncode != 0:
                self.die("nvidia-smi --query-compute-apps=gpu_bus_id "
                         "unsupported - cannot scope the clean-stand check "
                         "to the card under test")
            # Other tenants' workloads are expected on a shared host; only
            # OUR card must be clean. The bus id is queried here because
            # the session bus id is resolved later, in start_session.
            bus = (self.smi("pci.bus_id", timeout=WD_SMI_TIMEOUT_S)
                   or "").replace(" ", "")
            if not bus:
                self.die("cannot resolve our bus id for the shared-host "
                         "compute-clean check (nvidia-smi pci.bus_id "
                         "unreadable) - an empty bus would silently skip "
                         "the check; rerun once the driver answers")
            ours = [l for l in procs if bus.lower() in l.lower()]
            if ours:
                self.die(f"{len(ours)} compute process(es) on the card under "
                         f"test ({bus}) - refusing (SPEC-02 s0.2)")
            if procs:
                self.log(f"note: {len(procs)} compute process(es) on OTHER "
                         f"GPUs of this shared host - allowed under "
                         f"--allow-shared-host (not the card under test)")
        elif procs:
            self.die(f"{len(procs)} compute process(es) active fleet-wide - "
                     f"refusing (SPEC-02 s0.2)")
        if subprocess.run(["dmesg"], capture_output=True).returncode != 0:
            if self.no_dmesg:
                self.log("WARN: dmesg not readable and --allow-no-dmesg set - "
                         "Xid counting DISABLED for this run; the run summary "
                         "and report mark the channel unverified")
            else:
                self.die("dmesg not readable (kernel.dmesg_restrict=1?) - "
                         "Xid counting impossible; run as root, relax "
                         "dmesg_restrict, or pass --allow-no-dmesg for a "
                         "degraded run with the channel marked unverified")
        # Missing tools are a clean preflight death, not a mid-stage
        # traceback: Popen on a missing path raises FileNotFoundError,
        # which is not a GateAbort and would escape run() as a Python
        # stack trace. Checked only for stages this run will
        # actually execute, so --only/--skip runs stay possible on a
        # partially built kit.
        need = []
        if any(self.stage_enabled(s) for s in ("mem_pattern", "mem_retention", "warm_rerun")):
            need.append(self.bin / "compare-vram")
        if any(self.stage_enabled(s) for s in ("burn", "warm_rerun")):
            need.append(self.bin / "gpu-burn/gpu-burn")
        if self.stage_enabled("mem_vulkan"):
            need.append(self.club / "scripts/qc/memtest-select.py")
        # Session helpers: telemetry always launches; xid-watch launches
        # whenever the Xid channel is active. A missing helper was an
        # unguarded Popen FileNotFoundError in start_session - it escapes
        # run() past the GateAbort handlers as a traceback, not a clean
        # REJECT-INCOMPLETE.
        here = Path(__file__).resolve().parent
        need.append(here / "telemetry.py")
        if not self.no_dmesg:
            need.append(here / "xid-watch.py")
        for tool in need:
            if not tool.is_file():
                self.die(f"missing {tool} - build/vendor it per "
                         f"kit/MANIFEST.md before the session (stand/setup "
                         f"gap, not a card defect)")
        # An explicitly configured TG_MATMUL_PYTHON that does not exist (or
        # is not executable) is a missing tool like the ones above: the matmul
        # torch probe would raise FileNotFoundError - not a GateAbort - and
        # escape run() as a traceback. Checked only when matmul is scheduled.
        if (self.stage_enabled("matmul") and self.matmul_py
                and not (Path(self.matmul_py).is_file()
                         and os.access(self.matmul_py, os.X_OK))):
            self.die(f"TG_MATMUL_PYTHON/--matmul-python={self.matmul_py} is "
                     f"missing or not executable - point it at the torch "
                     f"venv interpreter or unset it (kit/MANIFEST.md s5)")
        # Fresh evidence: archive leftovers from a previous run in this dir.
        old = [p for p in (self.evid / "receipts").iterdir() if p.is_file()]
        if old:
            prev = self.evid / f"receipts.prev-{datetime.now():%Y%m%dT%H%M%SZ}"
            prev.mkdir()
            for p in old:
                shutil.move(str(p), str(prev / p.name))
            self.log(f"archived {len(old)} previous receipts to {prev.name}")

    def start_session(self):
        self.lock.touch()
        self.thermal_flag.unlink(missing_ok=True)
        self.gone_flag.unlink(missing_ok=True)
        self.run_start.write_text(datetime.now().isoformat())
        raw = (self.smi("pci.bus_id", timeout=WD_SMI_TIMEOUT_S) or "").replace(" ", "")
        if not raw:
            # An empty bus id would disable the foreign-Xid filter ("" is
            # "in" every line) - count nothing rather than everything.
            self.die("pci.bus_id unreadable (nvidia-smi failed or timed "
                     "out) - Xid attribution impossible")
        # nvidia-smi prints e.g. 00000000:01:00.0 - extract bus:device
        m = re.search(r"([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})\.[0-7]$", raw)
        self.bus_id = raw
        self.bus_short = m.group(1) if m else raw
        lines = self.dmesg_lines()
        self.dm_start_lines = len(lines)
        self.dm_cursor = len(lines)
        if lines:
            self.dm_first_cksum = _dmesg_cksum(lines[0])
        here = Path(__file__).resolve().parent
        self.log(f"start: profile={self.profile} gpu={self.gpu} "
                 f"evidence={self.evid} bus={self.bus_id} "
                 f"dmesg_marker={self.dm_start_lines}")
        # Both helpers run under the gate's own interpreter: no exec-bit
        # dependency (a zip-copied kit works), preflight already verified
        # the files exist, and each Popen still dies cleanly (never a
        # traceback) if a file vanished between preflight and here -
        # run_work's belt-and-braces pattern.
        try:
            self.tele_proc = subprocess.Popen(
                [sys.executable, str(here / "telemetry.py"), "--gpu",
                 str(self.gpu), "--out",
                 str(self.evid / "telemetry/session.csv")])
        except OSError as e:
            self.die(f"telemetry failed to start: {e} - kit/setup gap, "
                     f"not a card defect")
        if self.no_dmesg:
            self.log("xid-watch not started (dmesg unreadable under "
                     "--allow-no-dmesg); the Xid channel is marked "
                     "unverified in the run summary")
        else:
            xw = [str(here / "xid-watch.py"), "--out",
                  str(self.evid / "xid/xid.log"), "--bus", self.bus_short]
            try:
                if os.geteuid() == 0:
                    self.xid_proc = subprocess.Popen([sys.executable, *xw])
                elif subprocess.run(["sudo", "-n", "true"],
                                    capture_output=True).returncode == 0:
                    self.xid_proc = subprocess.Popen(
                        ["sudo", "-n", sys.executable, *xw])
                else:
                    self.log("WARN: no root - xid-watch not started; "
                             "check dmesg manually between stages")
            except OSError as e:
                self.die(f"xid-watch failed to start: {e} - kit/setup "
                         f"gap, not a card defect")
        self.assert_cap()

    def _pkill_memtest(self):
        """Kill stray memtest_vulkan processes, path-anchored: the
        pattern is the vendored binary's full path - exactly the
        wrapper's own backstop (vendor memtest-select.py runs
        pkill -9 -f MEMTEST_BIN). The bare name "memtest_vulkan" also
        matched any argv MENTIONING it, i.e. potentially our own wrapper
        after a future log-path edit. Never raises: a host without pkill
        logs a WARN and continues."""
        try:
            subprocess.run(["pkill", "-f",
                            str(self.club / "scripts/qc/memtest_vulkan"
                                "/memtest_vulkan")],
                           capture_output=True)
        except OSError as e:
            self.log(f"WARN: pkill unavailable ({e}) - memtest_vulkan "
                     f"orphan cleanup skipped")

    def kill_memtest_orphans(self):
        """memtest_vulkan re-execs into a detached grandchild that killpg
        cannot reach; it pins ~full VRAM and starves every later stage."""
        self._pkill_memtest()
        time.sleep(2)

    def shutdown(self):
        # Every step is guarded: shutdown runs in run()'s finally block,
        # and an exception raised here (pkill absent, terminate on an
        # already-reaped pid) would replace the run's own outcome with a
        # traceback - the exact failure the finally block exists to
        # prevent.
        for step in (self.kill_work, self.stop_watchdog,
                     self.kill_memtest_orphans):
            try:
                step()
            except Exception as e:  # noqa: BLE001
                self.log(f"WARN: shutdown {step.__name__}: {e}")
        for p in (self.tele_proc, self.xid_proc):
            if p and p.poll() is None:
                try:
                    p.terminate()
                except OSError as e:
                    self.log(f"WARN: shutdown terminate: {e}")
        self.lock.unlink(missing_ok=True)

    def run(self):
        aborted = True  # cleared only on a fully completed stage loop
        try:
            self.preflight()
            self.start_session()
            for name in STAGES:
                if not self.stage_enabled(name):
                    continue
                getattr(self, f"stage_{name}")()
            self.check_ring_wrap()
            self.log(f"stages complete. xid_fatal={self.xid_totals_since_start()['fatal']} "
                     f"thermal_stop={self.wd_flags()[0]} brake={self.brake_state}")
            self.log(f"next: T9 cold-boot persistence (manual), then "
                     f"scripts/make-report.py {self.evid}")
            aborted = False
        except GateAbort as e:
            self.log(f"FATAL: {e}")
        except KeyboardInterrupt:
            self.log("interrupted (Ctrl+C)")
        finally:
            # Always leaves a run-summary: a die() mid-stage is an ABORTED
            # COMPLETED-ENOUGH session (stage receipts decide the verdict),
            # not a vanished gate. make-report maps aborted=1 + hard-fail
            # receipts -> REJECT, aborted=1 without fails -> REJECT-INCOMPLETE.
            # The summary lands FIRST and guarded: a shutdown step that
            # raises (pkill missing, a wedged join) must not eat it, and a
            # failing summary write must not skip cleanup either.
            try:
                self.write_summary(aborted=aborted)
            except Exception as e:  # noqa: BLE001
                self.log(f"WARN: run-summary write failed: {e}")
            self.shutdown()
        return 1 if aborted else 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile",
                    choices=["smoke", "express", "standard", "extended"],
                    default="standard")
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--tag", default="card0")
    ap.add_argument("--evidence-dir")
    ap.add_argument("--only", help="comma-separated stages from: " + ",".join(STAGES))
    ap.add_argument("--skip", help="comma-separated stages")
    ap.add_argument("--power-w", type=int, default=180)
    ap.add_argument("--memtest-min", type=int)
    ap.add_argument("--burn-min", type=int)
    ap.add_argument("--allow-shared-host", action="store_true",
                    help="proceed on a multi-GPU host (seller rig / rented "
                         "instance): workloads are pinned to the card under "
                         "test via CUDA_VISIBLE_DEVICES and Xid lines stay "
                         "bus-filtered, but thermal/PSU confounds remain - "
                         "the report records a forced warn (CONDITIONAL)")
    ap.add_argument("--allow-no-dmesg", action="store_true",
                    help="degraded mode where dmesg is unreadable "
                         "(containers): Xid counting is disabled and marked "
                         "xid_counting=unavailable; the report warns the "
                         "Xid channel is unverified")
    ap.add_argument("--club", default=os.environ.get("CLUB"),
                    help="club-170hx checkout (default $CLUB or vendor/club-170hx)")
    ap.add_argument("--bin", default=os.environ.get("BIN"),
                    help="tool directory (default $BIN or <repo>/bin)")
    ap.add_argument("--matmul-python", default=os.environ.get("TG_MATMUL_PYTHON"),
                    help="interpreter with torch for matmul (default $TG_MATMUL_PYTHON)")
    ap.add_argument("--idle-min", type=int, default=0,
                    help="override the profile's idle-baseline minutes (0 = profile "
                         "default; SPEC-02 keeps >=5 min for seller-stand runs - "
                         "shorten only for rented quick checks where the thermal "
                         "baseline has less bearing)")
    args = ap.parse_args()
    g = Gate(args)
    if args.memtest_min:
        g.memtest_min = args.memtest_min
    if args.burn_min:
        g.burn_min = args.burn_min
    if args.idle_min:
        g.idle_min = args.idle_min
    sys.exit(g.run())


if __name__ == "__main__":
    main()

