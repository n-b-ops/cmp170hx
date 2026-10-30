#!/usr/bin/env python3
"""xid-watch.py - timestamped kernel watcher for NVRM/Xid/AER/SEC2 events.

Adapted from club-170hx scripts/qc/xid-watch.sh (same filter set) with
timestamped, tee-able output for the evidence bundle. NVRM Xid lines
are annotated [fatal]/[noted]/[proc] by the SAME classifier the gate
counts with (verdicts.classify_xid, --bus BB:DD): the evidence log and
the receipts can no longer disagree about what a line means. Foreign-bus
Xid lines stay in the log, untagged.

Usage: sudo xid-watch.py [--out FILE] [--bus BB:DD]   (root required)
"""
import argparse
import os
import re
import signal
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verdicts import classify_xid  # noqa: E402

FILTER_RE = re.compile(
    r"NVRM|Xid|fallen off|RmInitAdapter|SEC2|AER|PCIe Bus Error", re.I)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None,
                    help="also append matched lines to this file")
    ap.add_argument("--bus", default="",
                    help="short bus id (e.g. 01:00): annotates Xid lines "
                         "with the gate's attribution filter")
    args = ap.parse_args()
    if os.geteuid() != 0:
        print("Run as root so kernel messages are visible: sudo "
              + sys.argv[0], file=sys.stderr)
        sys.exit(1)
    print("xid-watch: watching kernel log for: " + FILTER_RE.pattern,
          file=sys.stderr)
    fh = open(args.out, "a") if args.out else None
    proc = subprocess.Popen(["dmesg", "--follow", "--human"],
                            stdout=subprocess.PIPE, text=True,
                            errors="replace")

    def stop(*_a):
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not FILTER_RE.search(line):
                continue
            stamp = datetime.now().isoformat(timespec="seconds")
            # Classify only when the bus is known: classify_xid(line, "")
            # would treat every bus-token-carrying line as foreign.
            cls = classify_xid(line, args.bus) if args.bus else None
            tagged = (stamp + " [" + cls + "] " + line) if cls \
                else (stamp + " " + line)
            print(tagged, flush=True)
            if fh:
                fh.write(tagged + "\n")
                fh.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if fh:
            fh.close()
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()

