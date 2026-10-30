#!/usr/bin/env python3
"""compare-fingerprints.py - reference-vs-candidate card fingerprint diff.

The gate verdict is per-card; the purchase decision is fitness relative
to a known-good reference (a rented-card battle test, an earlier
purchase). make-report writes fingerprint.json with the stable measured
invariants; this script diffs two of them and flags any "measured"
field deviating more than --tol percent (default 5) from the reference.
thermal_context deltas are printed for information only - they track
the cooling, not the silicon.

Usage: compare-fingerprints.py REF.json CAND.json [--tol PCT]
Exit codes: 0 = every measured field within tolerance, 1 = at least one
field beyond tolerance or missing (or unreadable input).
"""
import argparse
import json
import sys


def load(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit("cannot read " + path + ": " + str(e))
    if "measured" not in d:
        sys.exit(path + ": no 'measured' section (not a fingerprint.json?)")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--tol", type=float, default=5.0,
                    help="flag deviations beyond this percent of the "
                         "reference (default 5)")
    args = ap.parse_args()
    ref = load(args.reference)
    cand = load(args.candidate)
    flagged = 0

    rs, cs = ref.get("sku", {}), cand.get("sku", {})
    if rs and cs and rs.get("memory_total_mib") != cs.get("memory_total_mib"):
        print("NOTE: sku differs (" + str(rs.get("memory_total_mib")) +
              " vs " + str(cs.get("memory_total_mib")) +
              " MiB) - the comparison may be apples-to-oranges")

    print("{:<24} {:>12} {:>12} {:>10}  verdict".format(
        "field", "ref", "cand", "delta"))
    rm, cm = ref["measured"], cand["measured"]
    for k in sorted(rm):
        if k not in cm:
            print("{:<24} {:>12} {:>12} {:>10}  FLAG".format(
                k, str(rm[k]), "MISSING", ""))
            flagged += 1
            continue
        try:
            r, c = float(rm[k]), float(cm[k])
        except (TypeError, ValueError):
            print("{:<24} non-numeric pair - FLAG".format(k))
            flagged += 1
            continue
        if r == 0:
            delta = 0.0 if c == 0 else float("inf")
        else:
            delta = abs(c - r) / abs(r) * 100.0
        if delta > args.tol:
            flagged += 1
        print("{:<24} {:>12.2f} {:>12.2f} {:>9.1f}%  {}".format(
            k, r, c, delta, "ok" if delta <= args.tol else "FLAG"))
    for k in sorted(set(cm) - set(rm)):
        print("{:<24} {:>12} {:>12} {:>10}  note (candidate-only)".format(
            k, "absent", str(cm[k]), ""))

    print()
    print("thermal context (informational - tracks cooling, not silicon):")
    rt, ct = ref.get("thermal_context", {}), cand.get("thermal_context", {})
    for k in sorted(set(rt) | set(ct)):
        print("  " + k + ": " + str(rt.get(k, "n/a")) + " -> " +
              str(ct.get(k, "n/a")))

    if flagged:
        print()
        print(str(flagged) + " measured field(s) beyond " + str(args.tol) +
              "% of the reference")
        sys.exit(1)
    print()
    print("all measured fields within " + str(args.tol) +
          "% of the reference")


if __name__ == "__main__":
    main()

