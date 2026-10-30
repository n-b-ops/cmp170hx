# PROVENANCE - prebuilt gate binaries

> Built once from the pinned sources in [kit/upstream/](../upstream/) and
> carried so a fresh instance needs NO toolchain (no nvcc, no cargo, no -dev
> packages) - only the driver and libraries every vast.ai CUDA image already
> has. Checksums: SHA256SUMS. Bootstrap uses these by default; `--force-build`
> rebuilds from kit/upstream instead.

| Item           | Source pin                        | License                          |
| -------------- | --------------------------------- | -------------------------------- |
| compare-vram   | club-170hx `6eeae24` (sm_80)      | Apache-2.0 (club, carried)       |
| gpu-burn       | wilicc/gpu-burn `0d19c94` (sm_80) | BSD-2-Clause (LICENSE-gpu-burn)  |
| memtest_vulkan | GpuZelenograd `cdd418a` (v0.5.0)  | zlib (LICENSE-memtest_vulkan)    |

## Build environment (2026-10-30)

- vast.ai container, Ubuntu 24.04, kernel 7.0.0-38-generic, x86-64
- nvcc: CUDA 13.4 (Build cuda_13.4.r13.4/compiler.38855100_0)
- rustc/cargo 1.75.0 (memtest_vulkan)
- commands: `nvcc -O3 -arch=sm_80 compare_vram.cu -o compare-vram`;
  gpu-burn `make COMPUTE=80`; memtest `cargo build --release`

## Runtime dependencies (probed with ldd on the build host)

| Binary        | Needs                                                                  |
| ------------- | ---------------------------------------------------------------------- |
| compare-vram  | libcuda only (driver; cudart linked statically)                        |
| gpu-burn      | libcublas.so.13 + libcublasLt.so.13 (any vast CUDA image; else apt     |
|               | `libcublas-<maj-min>`), plus compare.fatbin next to it (carried)       |
| memtest_vulkan | libvulkan.so.1 at runtime via dlopen (vast images ship it; bare stands |
|                | need `apt install libvulkan1`) + the NVIDIA Vulkan ICD                 |

First exercised by the 2026-10-30 vast.ai battle-test session (gate intake +
express profile). If a future driver/CUDA break ever invalidates them,
`instance-bootstrap.sh --force-build` regenerates everything from source.
