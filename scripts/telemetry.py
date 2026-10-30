#!/usr/bin/env python3
"""telemetry.py - 1 Hz GPU telemetry logger for CMP 170HX acceptance testing.

Writes a fixed-schema CSV: unsupported driver fields degrade to literal
"n/a" columns (nvidia-smi rejects the WHOLE query if one field is
unknown; on this card ECC fields are typically N/A).

FIELDS is the single schema source: make-report.py imports it and
asserts every column it reads exists here; SPEC-03 s2.1 documents the
same set. Field-name note: temperature.memory, NOT memory.temperature -
the wrong name silently yields an empty CSV [measured bug, club-170hx
LESSONS].

Usage: telemetry.py [--duration SEC] [--out FILE] [--gpu N] [--interval SEC]
  --duration 0 (default) means: log until killed (SIGTERM/SIGINT exit 0).
"""
import argparse
import csv
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

FIELDS = [
    "timestamp", "pci.bus_id", "temperature.gpu", "temperature.memory",
    "power.draw", "power.limit", "clocks.sm", "clocks.mem",
    "utilization.gpu", "memory.used",
    "pcie.link.gen.current", "pcie.link.width.current",
    "clocks_throttle_reasons.active",
    "ecc.mode.current", "ecc.errors.uncorrected.volatile.total",
    "ecc.errors.corrected.volatile.total",
]
NA = "n/a"
SMI_TIMEOUT_S = 10


def smi(fields, gpu):
    """One bounded query -> the first output line, or None on any
    failure: a wedged driver must not hang the logger, and a failed
    sample records nothing rather than a gap in the schema."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + ",".join(fields),
             "--format=csv,noheader,nounits", "-i", str(gpu)],
            capture_output=True, text=True, timeout=SMI_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip().splitlines()[0]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=int, default=0,
                    help="stop after SEC seconds (0 = until killed)")
    ap.add_argument("--interval", type=int, default=1,
                    help="sample period in seconds")
    ap.add_argument("--gpu", type=int, default=0, help="GPU index")
    ap.add_argument("--out", default=None,
                    help="output CSV (default: telemetry_<UTC stamp>.csv)")
    args = ap.parse_args()
    out_path = args.out or ("telemetry_"
                            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            + ".csv")
    # Foreground and gate-launched (background) use both stop cleanly on
    # SIGTERM/SIGINT: exit 0, close the file.
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))

    # Probe each field once: the driver rejects the WHOLE query if one
    # field is unknown, so unsupported fields become literal n/a columns
    # and the column set stays fixed for parsers.
    query, na = [], []
    for f in FIELDS:
        if smi([f], args.gpu) is not None:
            query.append(f)
        else:
            na.append(f)
    print(f"telemetry: gpu={args.gpu} interval={args.interval}s "
          f"duration={args.duration}s out={out_path}", file=sys.stderr)
    print("telemetry: probed fields: " + ",".join(query), file=sys.stderr)
    if na:
        print("telemetry: n/a fields: " + ",".join(na), file=sys.stderr)

    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(query + na)
        start = time.monotonic()
        while True:
            if args.duration > 0 and time.monotonic() - start >= args.duration:
                break
            line = smi(query, args.gpu)
            if line is not None:
                # Proper CSV quoting: multi-value fields (throttle
                # reasons "HW ..., SW ...") stay one column, and each row
                # is flushed so a SIGKILLed logger keeps every sample.
                w.writerow([v.strip() for v in line.split(",")] + [NA] * len(na))
                fh.flush()
            time.sleep(args.interval)


if __name__ == "__main__":
    main()

