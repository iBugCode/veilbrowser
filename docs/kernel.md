# Kernel build & CI

The **veil-chromium kernel** is ungoogled-chromium 153.0.8010.52 plus the
`kernel-patches/` series, built in official (ThinLTO) mode. This page
documents the patch layout, the build scripts, and the release pipeline.

## Repository layout

```
kernel-patches/
├── series                  # top-level patch order (ungoogled tree)
└── extra/
    ├── series              # the fingerprint series actually applied
    └── fingerprint/
        ├── 000-add-fingerprint-switches.patch   # switches + plumbing
        ├── 001-disable-runtime.enable.patch     # CDP Runtime domain silencing
        ├── 002…030                              # one surface per patch
        ├── 031-devtools-debugger-statement.patch
        └── 032-devtools-no-preview.patch
scripts/
├── build-kernel-windows.py # full Windows x64 pipeline (fetch→patch→build→package)
├── gen_kernel_patches.py   # regenerate series helpers
└── gen_native_patch.py     # regenerate the native-injection patch
.github/workflows/kernel.yml # tag-triggered linux+win builds → GitHub Release
```

Each patch is a plain `patch -p1` git-style diff; adding a surface means
adding `NNN-name.patch` + one line in `extra/series`. Patches are applied
after ungoogled's own patch tree, then `pruning.list` and
`domain_substitution.list` run, then GN args come from
`kernel-patches/flags.gn` plus the platform file (`flags.linux.gn` /
`flags.windows.gn`).

## Building locally (Linux)

Prerequisites: depot_tools, ~100 GB disk, 16+ GB RAM (32 GB+ for ThinLTO
links). See [README → Building the kernel](../README.md#building-the-kernel)
for the bootstrap; in short:

```bash
fetch --nohooks chromium
cd src && ./build/install-build-deps.sh && gclient runhooks
# apply kernel-patches (ungoogled pipeline + extra/series), then:
gn gen out/Default --args="is_official_build=true is_debug=false use_thin_lto=true $(cat ../kernel-patches/flags.gn ../kernel-patches/flags.linux.gn | tr '\n' ' ')"
autoninja -C out/Default chrome
```

`VEIL_THINLTO=0` appends `use_thin_lto=false is_cfi=false` for faster
developer builds (the release kernel keeps ThinLTO).

## Building on Windows

`scripts/build-kernel-windows.py` automates the whole pipeline:

```bash
python scripts/build-kernel-windows.py \
  --ugc-win /path/ungoogled-chromium-windows \
  --kernel-patches /path/veilbrowser/kernel-patches \
  --out dist/
```

Pipeline stages: VS toolchain discovery (17.x) → ungoogled Windows patches
→ fingerprint series (`extra/series`) → rust toolchain layout (FULL
tarball; the script wipes `third_party/rust-toolchain/bin` first so no
Linux ELF `rustc` survives to trip `CreateProcess` with WinError 193) →
GN args (`flags.gn` + `flags.windows.gn`, PGO off) → ninja → package
(`veil-chromium-*-win-x64.zip`).

## GN flags

`flags.gn` (shared): `chrome_pgo_phase=0`, `disable_fieldtrial_testing_config=true`,
`enable_reporting=false`, `safe_browsing_mode=0`, `use_official_google_api_keys=false`,
`enable_widevine=true`, `exclude_unwind_tables=true`, … (trimmed services).

`flags.linux.gn`: `is_official_build=true`, `symbol_level=0`,
`ffmpeg_branding="Chrome"`, `proprietary_codecs=true`, `rtc_use_pipewire=true`,
`use_vaapi=true`, …

`flags.windows.gn`: `enable_rust=true`, `use_sysroot=false`, …

## CI release pipeline (`.github/workflows/kernel.yml`)

Triggered by pushing a tag (`v*`):

1. **linux** job — full build on `ubuntu-24.04` (hosted 6 h cap), uploads
   artifact `kernel-linux-x64` (`veil-chromium-<ver>-linux-x64.tar.zst`).
2. **windows** job — full build via `build-kernel-windows.py` on
   `windows-2025`, uploads artifact `kernel-win-x64` (`.zip`).
3. **release** job — `needs: [linux, windows]`, runs on tag refs, waits
   for the GitHub Release the version-bump CI creates, then attaches
   `dist/veil-chromium-*` with `--clobber`.

A release therefore carries, per tag: the Python wheel, the Linux kernel
tarball, and the Windows kernel zip. The Linux artifact builds in ~5 h;
the Windows build is longer and is the one to watch for the 6 h cap.

## Versioning

- `veilbrowser/__init__.py` + `pyproject.toml` — SDK version (wheel).
- Kernel version follows Chromium: `153.0.8010.52`.
- Tags `v0.10.x` are SDK releases; the kernel artifacts attached are built
  from the same tree.
