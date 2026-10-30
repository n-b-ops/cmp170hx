# design.md - design rationale (the why)

> What the code does lives in the code and in
> [CONTRACT.md](CONTRACT.md); this file holds the WHY: decisions that
> cannot be read off the implementation. Commit-level provenance is in
> `git log`.

## 1. Why the gate is Python

The first seven review rounds ran a bash gate. Three failures killed it:
the bash watchdog ran in a subshell and could not see the workload's
process group (so it could not kill what it was watching), die() produced
no run-summary - a hard fail simply vanished the session - and awk
escaping caused repeated parsing defects. The Python gate keeps the
workload in-process bookkeeping (threads share state; no subshell
copies) and guarantees a receipt on every exit path.

## 2. Watchdog

- **A thread with shared state, not a subshell**: it must reach the live
  workload's process group while the parent waits on it.
- **Per-thread stop Event**: a new watchdog never clears a previous
  thread's Event, so an orphaned loop (stuck in a hung nvidia-smi past
  its join timeout) stays disarmed instead of waking as a second
  sampler. The runnable form of this invariant is
  tests/test_watchdog.py watchdog_scope_tests - an assert inside
  start_watchdog could only check a freshly constructed Event, which is
  always unset and proves nothing.
- **Kill is scoped to the thread's own proc reference**: an orphaned
  watchdog can never reach a later stage's workload.
- **A stop flag fires only on a true kill**: the card keeps rising after
  a clean workload exit; a thermal sample in that transient window is
  logged, never flagged.
- **A probe timeout is never gpu-gone**: gpu-gone drives REJECT and
  demands a confirmed disappearance; a hung probe is ambiguous (wedged
  driver vs busy host), so sampling continues.

## 3. die() and the run-summary invariant

Every abort path leaves a run-summary: a hard fail is an
aborted-but-verdictable session, never a vanished gate. run()'s finally
writes the summary FIRST and guards it, then runs shutdown with every
step individually guarded - a hostile host (no pkill, dying helpers)
still yields aborted=1 plus receipts (test_watchdog.py
shutdown_robustness_tests). Missing tools and session helpers die at
preflight, never as a mid-stage traceback, and every nvidia-smi call
carries a timeout: a wedged driver aborts cleanly, it never hangs the
gate.

## 4. Retry order

The one-shot retry shell counts Xids BEFORE the retry decision: a
first-attempt fatal fails fast instead of burning a second workload
budget. A stop event forbids the retry outright - work the watchdog just
killed for a thermal/disappearance breach is never restarted
automatically.

## 5. Orphan cleanup

memtest_vulkan re-execs into a detached grandchild outside the process
group; it pins ~full VRAM and starves every later stage. The cleanup
pkill is anchored at the vendored binary's full path - the same anchor
the vendor wrapper's own backstop uses - so it matches the grandchild
with or without args and can never match the gate's own wrapper argv.

## 6. The session helpers are Python too

- telemetry.py owns the CSV schema (FIELDS); make-report asserts the
  columns it reads exist in that schema - a rename on either side fails
  the assert instead of silently yielding empty stats.
- xid-watch.py annotates NVRM Xid lines with the same
  verdicts.classify_xid the gate counts with: the evidence log and the
  receipts cannot disagree about what a line means.
- setup-container.sh stays bash: a one-shot preflight with no shared
  logic.

## 7. Comment policy

- Comments explain **why** and **what-if**; code explains **what**.
- Keep comments that name a contract clause or a hard-won lesson (a
  PWRBRK# slot fault presents exactly as low TFLOPs; the dead-card floor
  holds at ANY cap because a broken tensor path is broken at every cap).
- Delete comments that restate the expression beneath them.
- Long design rationale moves here (or to CONTRACT for signal
  definitions); the site keeps a one-line pointer.
- Where an invariant can check itself, prefer an assert or a test over a
  comment (the telemetry schema assert; the watchdog scope test).
- No review-round archaeology in code - provenance lives in git history.

## 8. Anchors that look wrong but are not

The original Russian testing overview was directionally right; a review
of the pinned projects corrected or upgraded these points before the
spec suite existed (kept as history; the spec sections carry the
current truth):

| Original draft said                                              | Verified correction                                                                                                                   |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Driver "strictly 610.43.03 / 610.43.02"                          | Pinned unlocker accepts exactly **615.78.08, 615.71.09, 610.57.04, 610.43.03, 610.43.02**; 610.43.03 stays the club-validated default |
| "Always pass --profile explicitly (autodetect picks first card)" | Geometry is **per-PCI-ID at runtime**; --profile is metadata-only                                                                     |
| memtest_vulkan "v0.6.0+"                                         | Latest upstream tag is **v0.5.0** — the measured version; no CLI device flag, the PTY wrapper is mandatory                            |
| Bandwidth "~1592–1599 GB/s" as a pass criterion                  | No canonical figure exists (range 1305–1600; VBIOS-dependent). **Informational only, never a gate**                                   |
| Xid 31 "harmless single occurrence"                              | Under this gate Xid 31 is a **hard fail**; the folklore is stock-window mining                                                        |
| Qwen decode ~60–74 tok/s as the target                           | Different stack; the recipe of record is **133.6–140.3 tok/s decode256** @180 W (REFERENCE-NUMBERS §E)                                |
| gpu-burn COMPUTE=80 (implied FP32)                               | The **measured** burn mode is `-tc` (tensor); FP32 validates nothing thermally                                                        |
| (absent) ECC mention                                             | The unlocker **synthesizes** ECC status at the RMAPI layer — not hardware proof                                                       |
| (absent) single-rank fraud vector                                | Single-rank "fake 64 GB" cards **pass memtest_vulkan and fail CUDA pattern tests** — hence mem_pattern                                |
| (absent) stand confounders                                       | Added: PWRBRK# check, module-shadowing check, x8 topology, OVMF-vs-SeaBIOS, power-cap reset                                           |
