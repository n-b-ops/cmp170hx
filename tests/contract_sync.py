#!/usr/bin/env python3
"""contract_sync.py - mechanical guard against CONTRACT.md drift.

The change-discipline rule in docs/CONTRACT.md says code and CONTRACT move
together. This ENTRY POINT keeps the one-command contract
(`python3 tests/contract_sync.py`, exit 0 = in sync, 1 = failure,
2 = could not run) and loads the real scripts once; the test families
live in sibling modules so an individual failure bisects to one file:

  test_verdicts      stage_verdict + classifier unit cases
  test_thresholds    CONTRACT stage-section markers, threshold numbers,
                     single-engine + non-verdict guards
  test_watchdog      watchdog scope, retry-skip, missing-tool,
                     shutdown robustness
  test_fingerprint   fingerprint.json writes + compare-fingerprints
  test_e2e_verdict   synthetic bundles -> make-report verdicts
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import test_verdicts      # noqa: E402
import test_thresholds    # noqa: E402
import test_watchdog      # noqa: E402
import test_fingerprint   # noqa: E402
import test_e2e_verdict   # noqa: E402


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_verdicts():
    mod = _load(os.path.join(ROOT, "scripts", "verdicts.py"), "verdicts_mod")
    # Register under its real name too: gate.py and make-report.py do
    # "from verdicts import ..." when exec'd by _load below, and must bind
    # THIS module's objects, not a second fresh copy (the identity check
    # in test_thresholds depends on it).
    sys.modules["verdicts"] = mod
    return mod


def main():
    try:
        verdicts = load_verdicts()
        gate = _load(os.path.join(ROOT, "scripts", "gate.py"), "gate_mod")
        mr = _load(os.path.join(ROOT, "scripts", "make-report.py"),
                   "make_report")
    except Exception as e:  # noqa: BLE001
        print(f"contract_sync: cannot load the verdict engine: {e}", file=sys.stderr)
        sys.exit(2)  # 2 = test could not run (vs 1 = test failed)
    contract = open(os.path.join(ROOT, "docs", "CONTRACT.md")).read()
    failures = []

    failures.extend(test_verdicts.run(verdicts, gate))
    th_fails, n_needles = test_thresholds.run(verdicts, gate, mr, contract)
    failures.extend(th_fails)
    failures.extend(test_watchdog.run(gate))
    failures.extend(test_fingerprint.run())
    e2e_fails, e2e_n = test_e2e_verdict.run()
    failures.extend(e2e_fails)

    if failures:
        print("CONTRACT SYNC FAILURES:")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"contract_sync: OK ({len(test_verdicts.CASES)} verdict cases, "
          f"{sum(len(v) for v in test_thresholds.ROW_MARKERS.values())} stage markers, "
          f"{n_needles} thresholds, {len(test_verdicts.XID_CASES)} xid classes, "
          f"{len(test_verdicts.BRAKE_CASES)} brake classes, "
          f"{len(test_verdicts.TFLOPS_CASES)} tflops parses, "
          f"{len(test_verdicts.CLASSIFY_TFLOPS_CASES)} tflops classes, "
          f"{e2e_n} e2e bundles, watchdog scope + retry-skip + missing-tool + "
          f"shutdown-robustness + fingerprint tests)")


if __name__ == "__main__":
    main()

