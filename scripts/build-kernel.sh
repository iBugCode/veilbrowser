#!/usr/bin/env bash
# Build the patched "veil-chromium" kernel from source (Linux x86_64).
#
# Self-contained pipeline — no depot_tools, no git checkout of Chromium:
#   1. official chromium-lite tarball, hash-verified via downloads.ini
#   2. prune + ungoogled-chromium patches (tag-pinned clone)
#   3. domain substitution, then the fingerprint patch set (kernel-patches)
#   4. pinned clang / rust / sysroot via Chromium's own update scripts
#   5. gn bootstrapped from the tarball, ninja chrome
#
# Flow mirrors ungoogled-chromium-portablelinux (BSD-3). First full build:
# ~100 min on 8 cores with ThinLTO (release-grade, like the published
# kernels); set VEIL_THINLTO=0 to skip LTO — fits a 4-core / 6-hour
# GitHub-hosted runner.
#
# Requirements: python3, curl, patch, ninja, gperf, go, node >= 20, esbuild
# (npm -g), plus a host C++ compiler for the gn bootstrap. On Debian/Ubuntu:
#   apt-get install -y curl xz-utils bzip2 patch ninja-build gperf golang-go \
#       clang-format nodejs npm build-essential
#
# Usage:  bash scripts/build-kernel.sh [dist-dir]
# Env:
#   VEIL_CHROMIUM_VERSION  source version (default 153.0.8010.52; must match
#                          the ungoogled tag and kernel-patches/chromium_version.txt)
#   VEIL_JOBS              ninja -j (default: nproc)
#   VEIL_THINLTO           1 = official ThinLTO link (default), 0 = no LTO
#   VEIL_WORK              build dir (default: mktemp under /tmp; needs ~55 GB)
#   NODE / GPERF / GO / CLANG_FORMAT / ESBUILD_DIR   host tool overrides
set -euo pipefail

VER="${VEIL_CHROMIUM_VERSION:-153.0.8010.52}"     # Chromium version
UGC_TAG="${VEIL_UGC_TAG:-$VER-1}"                 # matching ungoogled-chromium tag
JOBS="${VEIL_JOBS:-$(nproc)}"
THINLTO="${VEIL_THINLTO:-1}"
OUT="${1:-dist}"
WORK="${VEIL_WORK:-$(mktemp -d -t veilbuild.XXXXXX)}"

NODE_BIN="${NODE:-$(command -v node || true)}"
GPERF_BIN="${GPERF:-$(command -v gperf || true)}"
GO_BIN="${GO:-$(command -v go || true)}"
CLANG_FORMAT_BIN="${CLANG_FORMAT:-$(command -v clang-format || true)}"
ESBUILD_DIR="${ESBUILD_DIR:-$(npm root -g 2>/dev/null)/esbuild}"

echo "==> workdir: $WORK  (delete it yourself when done)"

# Only patches are versioned in this repository; upstream sources are
# assembled locally and never redistributed. Resolve repo paths while the
# caller's cwd is still current ($0 is often a relative path).
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PATCHES_DIR="$REPO_DIR/kernel-patches"
case "$OUT" in /*) ;; *) OUT="$(pwd)/$OUT" ;; esac
mkdir -p "$OUT" "$WORK" && cd "$WORK"

# ---- 1. upstream source (official FULL tarball, hash-verified) --------------
git clone --depth 1 --branch "$UGC_TAG" \
    https://github.com/ungoogled-software/ungoogled-chromium.git ugc
mkdir -p download_cache src
python3 ugc/utils/downloads.py retrieve -i "$PATCHES_DIR/downloads.ini" -c download_cache
python3 - ugc/utils "$PATCHES_DIR/downloads.ini" <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import downloads
info = downloads.DownloadInfo([Path(sys.argv[2])])
downloads.check_downloads(info, Path("download_cache"), None)
print("==> tarball hash OK")
EOF
python3 ugc/utils/downloads.py unpack -i "$PATCHES_DIR/downloads.ini" -c download_cache src

# ---- 2. prune + ungoogled patches -------------------------------------------
python3 ugc/utils/prune_binaries.py src "$PATCHES_DIR/pruning.list"
python3 ugc/utils/patches.py apply src ugc/patches
python3 ugc/utils/domain_substitution.py apply \
    -r "$PATCHES_DIR/domain_regex.list" \
    -f "$PATCHES_DIR/domain_substitution.list" src

# ---- 3. the fingerprint patch set (kernel-patches/series) -------------------
# The series file is a full manifest: core/ + extra/{inox,iridium,...} lines
# belong to ungoogled's own patch tree (applied above via ugc/patches); only
# the extra/fingerprint/ entries are ours. patches.py wants a dir-local series.
grep '^extra/fingerprint/' "$PATCHES_DIR/series" | sed 's|^extra/||' \
    > "$PATCHES_DIR/extra/series"
python3 ugc/utils/patches.py apply src "$PATCHES_DIR/extra"
# 022-030 are the pure-C++ engine patches. Regenerate after editing the build
# tree:  python3 scripts/gen_kernel_patches.py --tree src
# (font payloads: scripts/kernel-fonts/, regenerable from veilbrowser.fontpack)

# ---- 4. toolchain (pinned clang / rust / sysroot, no depot_tools) -----------
# Domain substitution masked the download URLs; unmask them first.
sed -i 's/commondatastorage.9oo91eapis.qjz9zk/commondatastorage.googleapis.com/g' \
    src/build/linux/sysroot_scripts/sysroots.json \
    src/tools/clang/scripts/update.py \
    src/tools/clang/scripts/build.py
sed -i 's/chromium.9oo91esource.qjz9zk/chromium.googlesource.com/g' \
    src/tools/clang/scripts/build.py \
    src/tools/rust/build_rust.py \
    src/tools/rust/build_bindgen.py
sed -i 's/chrome-infra-packages.8pp2p8t.qjz9zk/chrome-infra-packages.appspot.com/g' \
    src/tools/rust/build_rust.py

python3 src/tools/rust/update_rust.py
python3 src/tools/clang/scripts/update.py
python3 src/build/linux/sysroot_scripts/install-sysroot.py --arch=x64

# Host tools the build scripts expect under third_party/ (cipd normally
# provides them; we symlink distro / local copies).
mkdir -p src/third_party/node/linux/node-linux-x64/bin
ln -sf "${NODE_BIN:-node}" src/third_party/node/linux/node-linux-x64/bin/node
mkdir -p src/third_party/gperf/cipd/bin
ln -sf "${GPERF_BIN:-gperf}" src/third_party/gperf/cipd/bin/gperf
mkdir -p src/third_party/dawn/tools/golang/linux-amd64/bin
ln -sf "${GO_BIN:-go}" src/third_party/dawn/tools/golang/linux-amd64/bin/go
mkdir -p src/buildtools/linux64-format
ln -sf "${CLANG_FORMAT_BIN:-clang-format}" src/buildtools/linux64-format/clang-format
rm -rf src/third_party/devtools-frontend/src/node_modules/esbuild
ln -s "$ESBUILD_DIR" src/third_party/devtools-frontend/src/node_modules/esbuild

CLANG_BIN="$PWD/src/third_party/llvm-build/Release+Asserts/bin"
export CC="$CLANG_BIN/clang"
export CXX="$CLANG_BIN/clang++"

# ---- 5. build (official; ThinLTO unless disabled) ----------------------------
mkdir -p src/out/Default
cp "$PATCHES_DIR/flags.gn" src/out/Default/args.gn
cat "$PATCHES_DIR/flags.linux.gn" >> src/out/Default/args.gn
cat >> src/out/Default/args.gn <<EOF
target_cpu = "x64"
v8_target_cpu = "x64"
EOF
[ "$THINLTO" = "1" ] || echo 'use_thin_lto = false' >> src/out/Default/args.gn

cd src
python3 tools/gn/bootstrap/bootstrap.py -o out/Default/gn --skip-generate-buildfiles -j "$JOBS"
./out/Default/gn gen out/Default --fail-on-unused-args
ninja -C out/Default chrome chrome_sandbox -j "$JOBS"

# ---- 6. package the kernel ----------------------------------------------------
cd ..
tar -C src/out/Default -caf "$OUT/veil-chromium-$VER-linux-x64.tar.zst" \
    chrome chrome_sandbox chrome_100_percent.pak chrome_200_percent.pak \
    icudtl.dat resources.pak v8_context_snapshot.bin locales
echo "==> kernel tarball: $OUT/veil-chromium-$VER-linux-x64.tar.zst"
echo "    use it with:  VEIL_CHROME_PATH=<unpacked>/chrome veilbrowser check"
