#!/usr/bin/env bash
# Build the patched "veil-chromium" kernel from source.
#
# This is the pipeline used for the v0.7.0 kernel (Chromium 153). It runs on
# a beefy Linux x86_64 box only: ~100 GB free disk, ~8 GB RAM per build job,
# roughly 100 minutes for the first full build on 8 cores (ThinLTO).
#
# Usage:  bash scripts/build-kernel.sh [dist-dir]
#
# Only PATCHES are versioned in this repository; this script assembles the
# upstream sources locally and never redistributes them.
set -euo pipefail

VER="${VEIL_CHROMIUM_VERSION:-153.0.8010.52}"     # Chromium version
UGC_TAG="${VEIL_UGC_TAG:-$VER-1}"                 # matching ungoogled-chromium tag
JOBS="${VEIL_JOBS:-$(nproc)}"
OUT="${1:-dist}"
WORK="${VEIL_WORK:-$(mktemp -d -t veilbuild.XXXXXX)}"

echo "==> workdir: $WORK  (delete it yourself when done)"

mkdir -p "$OUT" "$WORK" && cd "$WORK"

# ---- 1. upstream sources ---------------------------------------------------
curl -LO "https://commondatastorage.googleapis.com/chromium-browser-official/chromium-$VER.tar.xz"
tar xf "chromium-$VER.tar.xz"
mv "chromium-$VER" src
git clone --depth 1 --branch "$UGC_TAG" \
    https://github.com/ungoogled-chromium/ungoogled-chromium.git ugc

# ---- 2. prune, patch, substitute ------------------------------------------
# Lists and the fingerprint patch set live in this repo (../kernel-patches).
PATCHES_DIR="$(cd "$(dirname "$0")/../kernel-patches" && pwd)"
python3 ugc/utils/prune_binaries.py src "$PATCHES_DIR/pruning.list"
python3 ugc/utils/apply_patches.py src ugc/patches
python3 ugc/utils/domain_substitution.py apply -r "$PATCHES_DIR/domain_substitution.list" src
python3 ugc/utils/apply_patches.py src "$PATCHES_DIR/extra"
# 022-026 are the pure-C++ engine patches: media-query/screen consistency,
# embedded metric fonts, desktop environment (voices/mediaDevices/quota/
# sampleRate), WebGL limits and window.devicePixelRatio. Regenerate after
# editing the build tree:  python3 scripts/gen_kernel_patches.py --tree src
# (font payloads: scripts/kernel-fonts/, regenerable from veilbrowser.fontpack)

# ---- 3. toolchain (depot_tools provides gn + bootstrap) -------------------
git clone --depth 1 https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$PWD/depot_tools:$PATH"
python3 ugc/utils/ensure_bootstrap.py src   # clang/rust/sysroot + gn
export PATH="$WORK/src/third_party/llvm-build/Release+Asserts/bin:$PATH"

# ---- 4. build (official + ThinLTO, like the v0.7.0 release kernel) --------
gn gen out/Default --args='is_official_build = true
is_component_build = false
chrome_pgo_phase = 0
symbol_level = 0
use_thin_lto = true
enable_nacl = false'
autoninja -C out/Default chrome -j "$JOBS"

# ---- 5. package the kernel -------------------------------------------------
tar -C out/Default -caf "$OUT/veil-chromium-$VER.tar.zst" \
    chrome chrome_sandbox chrome_100_percent.pak chrome_200_percent.pak \
    icudtl.dat resources.pak v8_context_snapshot.bin locales
echo "==> kernel tarball: $OUT/veil-chromium-$VER.tar.zst"
echo "    use it with:  VEIL_CHROME_PATH=<unpacked>/chrome veilbrowser check"
