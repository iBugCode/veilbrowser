#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2019 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Build the patched "veil-chromium" Windows x64 kernel by cross-compiling on Linux.

Why cross: GitHub-hosted windows-2025 runners are 4 vCPU and cannot finish a
Chromium win-x64 build inside the 350-minute job cap (~13.6 h measured,
71 edges/min); ubuntu runners drive the same graph ~3x faster. Chromium
supports linux-hosted win cross builds natively: clang-cl/lld-link/llvm-ml
run as host tools, MIDL outputs are pregenerated under
third_party/win_build_output, and rc has a committed linux64 binary. The
missing pieces are the MSVC/SDK headers and libs — fetched from Microsoft's
own public VS manifests (msvc-wine's vsdownload.py, vendored) and laid out on
an ext4 casefold loop mount, because the Windows SDK headers rely on
case-insensitive include lookup (the job ciopfs does in Googler cross builds).

Pipeline:
  1. chromium tarball -> prune -> patches (ungoogled -> windows -> veil)
     -> domain substitution              (same as the native Windows pipeline)
  2. host toolchain: linux clang via tools/clang/scripts/update.py, plus the
     clang-win-runtime-library package (clang_rt.*.lib) into the tree
  3. rust: the tarball's linux rust, plus x86_64-pc-windows-msvc std merged
     from the Win rust-toolchain package at the same pinned revision
  4. MSVC/SDK via vsdownload.py onto a casefold ext4 loop mount, plus
     hand-written SetEnv.{x64,x86}.json (the toolchain env files the Googler
     package would ship, regenerated for the pruned layout)
  5. gn gen with target_os="win" + visual_studio_path args, ninja chrome,
     portable zip packaging

Run on ubuntu (needs: patch ninja-build gperf golang-go node npm esbuild
msitools cabextract e2fsprogs, sudo for the loop mount, python3-httplib2):

    python scripts/build-kernel-windows-cross.py \\
        --ugc-win ../ungoogled-chromium-windows \\
        --kernel-patches kernel-patches --out dist
"""

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

_ROOT_DIR = None  # set in main(): the ungoogled-chromium-windows clone

_CDS_URL = 'https://commondatastorage.googleapis.com/chromium-browser-clang'
_VSDOWNLOAD_RELPATH = Path('scripts/vendor/msvc-wine/vsdownload.py')


def _init_ugc_imports(ugc_win: Path) -> None:
    sys.path.insert(0, str(ugc_win / 'ungoogled-chromium' / 'utils'))
    global downloads, domain_substitution, prune_binaries, patches
    global _common
    import downloads
    import domain_substitution
    import prune_binaries
    import patches
    from _common import ENCODING, ExtractorEnum, get_logger
    globals()['ENCODING'] = ENCODING
    globals()['ExtractorEnum'] = ExtractorEnum
    globals()['get_logger'] = get_logger


def _retrieve_with_retry(download_info, downloads_cache, attempts=3):
    """retrieve + hash-check, retrying transient download failures."""
    for attempt in range(1, attempts + 1):
        try:
            downloads.retrieve_downloads(download_info, downloads_cache, None, True)
            downloads.check_downloads(download_info, downloads_cache, None)
            return
        except subprocess.CalledProcessError as exc:
            if attempt == attempts:
                raise
            get_logger().warning('download attempt %d failed (%s), retrying',
                                 attempt, exc)
        except downloads.HashMismatchError as exc:
            if attempt == attempts:
                get_logger().error('File checksum does not match: %s', exc)
                raise
            bad = Path(str(exc))
            if bad.exists():
                bad.unlink()
            get_logger().warning('hash mismatch on attempt %d for %s — purged, retrying',
                                 attempt, bad.name)


def _unpack_tarball(cache: Path, source_tree: Path) -> None:
    """Extracts the hash-verified chromium-*.tar.xz with bsdtar (system tar).

    --keep-newer-files guards against tarball timestamps clashing with
    pre-created placeholders; the linux sysroot exclusions from the native
    Windows pipeline are kept (they are dead weight for the win target too)."""
    archives = sorted(cache.glob('chromium-*.tar.xz'))
    if len(archives) != 1:
        raise RuntimeError('expected one chromium tarball in %s' % cache)
    cmd = ['tar', '-xJf', str(archives[0]),
           '--strip-components=1', '-C', str(source_tree),
           '--exclude=third_party/llvm-build-tools/*sysroot*',
           '--exclude=third_party/llvm-build-tools/*_sysroot']
    subprocess.run(cmd, check=True)


def _write_extra_series(kernel_patches: Path) -> Path:
    """Derives kernel-patches/extra/series from the fingerprint series file."""
    extra_dir = kernel_patches / 'extra'
    series = extra_dir / 'series'
    if not series.exists():
        lines = [ln[len('extra/'):] for ln in
                 (kernel_patches / 'series').read_text(encoding='utf-8').splitlines()
                 if ln.startswith('extra/fingerprint/')]
        series.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return extra_dir


def _package(source_tree: Path, out_dir: Path, name: str) -> Path:
    out = source_tree / 'out' / 'Default'
    required = ['chrome.exe', 'chrome.dll', 'chrome_elf.dll', 'icudtl.dat',
                'resources.pak', 'v8_context_snapshot.bin']
    optional = ['chrome_100_percent.pak', 'chrome_200_percent.pak']
    missing = [f for f in required if not (out / f).exists()]
    if missing:
        raise RuntimeError('build output missing files: {}'.format(missing))
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in required + optional:
            if (out / f).exists():
                zf.write(out / f, f)
        locales = out / 'locales'
        if locales.is_dir():
            for f in sorted(locales.iterdir()):
                zf.write(f, 'locales/{}'.format(f.name))
    return zip_path


# ---- cross-specific toolchain helpers --------------------------------------

def _unmask_toolchain_urls(source_tree: Path) -> None:
    """The tarball ships domain-substituted URLs; unmask the ones the pinned
    toolchain updaters need (mirrors scripts/build-kernel.sh)."""
    sed = [
        ('s/commondatastorage.9oo91eapis.qjz9zk/commondatastorage.googleapis.com/g',
         ['src/tools/clang/scripts/update.py',
          'src/tools/rust/update_rust.py']),
        ('s/chromium.9oo91esource.qjz9zk/chromium.googlesource.com/g',
         ['src/tools/rust/build_rust.py',
          'src/tools/rust/build_bindgen.py']),
        ('s/chrome-infra-packages.8pp2p8t.qjz9zk/chrome-infra-packages.appspot.com/g',
         ['src/tools/rust/build_rust.py']),
    ]
    for expr, files in sed:
        for rel in files:
            path = source_tree / rel
            if path.exists():
                subprocess.run(['sed', '-i', expr, str(path)], check=True)


def _clang_revision(source_tree: Path) -> str:
    text = (source_tree / 'tools' / 'clang' / 'scripts' / 'update.py') \
        .read_text(encoding='utf-8')
    rev = re.search(r"^CLANG_REVISION\s*=\s*'([^']+)'", text, re.M).group(1)
    sub = re.search(r'^CLANG_SUB_REVISION\s*=\s*(\d+)', text, re.M).group(1)
    return f'{rev}-{sub}'


def _rust_revision(source_tree: Path) -> str:
    text = (source_tree / 'tools' / 'rust' / 'update_rust.py').read_text(encoding='utf-8')
    rust_rev = re.search(r"^RUST_REVISION\s*=\s*'([^']+)'", text, re.M).group(1)
    rust_sub = re.search(r'^RUST_SUB_REVISION\s*=\s*(\d+)', text, re.M).group(1)
    clang_rev = re.search(r"^CLANG_REVISION\s*=\s*'([^']+)'",
                          (source_tree / 'tools' / 'clang' / 'scripts' / 'update.py')
                          .read_text(encoding='utf-8'), re.M).group(1)
    return f'{rust_rev}-{rust_sub}-{clang_rev}'


def _fetch_and_extract(url: str, dest: Path, subdir: str = None) -> None:
    """Download a .tar.xz and extract (optionally only one member subtree)."""
    import tempfile
    get_logger().info('Fetching %s', url)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'pkg.tar.xz'
        with urllib.request.urlopen(url) as resp, open(archive, 'wb') as fh:
            shutil.copyfileobj(resp, fh)
        with tarfile.open(archive, 'r:xz') as tf:
            if subdir is None:
                tf.extractall(dest)
            else:
                members = [m for m in tf.getmembers()
                           if m.name == subdir or m.name.startswith(subdir + '/')]
                tf.extractall(dest, members=members)


def _install_host_clang(source_tree: Path) -> None:
    """Linux clang per the pinned revision + the win runtime libs."""
    subprocess.run([sys.executable, 'tools/clang/scripts/update.py'],
                   check=True, cwd=source_tree)
    pkg = f'clang-win-runtime-library-{_clang_revision(source_tree)}.tar.xz'
    llvm_root = source_tree / 'third_party' / 'llvm-build' / 'Release+Asserts'
    _fetch_and_extract(f'{_CDS_URL}/Win/{pkg}', llvm_root)


def _install_rust_win_std(source_tree: Path) -> None:
    """Merge x86_64-pc-windows-msvc std from the Win-host rust package into
    the tarball's linux rust (same pinned revision, compiler-identical)."""
    rustlib = source_tree / 'third_party' / 'rust-toolchain' / 'lib' / 'rustlib'
    if (rustlib / 'x86_64-pc-windows-msvc').exists():
        return
    pkg = f'rust-toolchain-{_rust_revision(source_tree)}.tar.xz'
    import tempfile
    get_logger().info('Fetching %s', f'{_CDS_URL}/Win/{pkg}')
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'rust.tar.xz'
        with urllib.request.urlopen(f'{_CDS_URL}/Win/{pkg}') as resp, open(archive, 'wb') as fh:
            shutil.copyfileobj(resp, fh)
        with tarfile.open(archive, 'r:xz') as tf:
            wanted = [m for m in tf.getmembers()
                      if '/rustlib/x86_64-pc-windows-msvc' in m.name
                      or m.name.endswith('rustlib/manifest-rust-std-x86_64-pc-windows-msvc')]
            tf.extractall(tmp, members=wanted)
        extracted = None
        for p in Path(tmp).rglob('rustlib'):
            if (p / 'x86_64-pc-windows-msvc').exists():
                extracted = p
                break
        if extracted is None:
            raise RuntimeError('win-msvc rust std not found in package')
        shutil.copytree(extracted / 'x86_64-pc-windows-msvc',
                        rustlib / 'x86_64-pc-windows-msvc')
        manifest = extracted / 'manifest-rust-std-x86_64-pc-windows-msvc'
        if manifest.exists():
            shutil.copy2(manifest, rustlib / manifest.name)


def _patch_tree_for_cross_sdk(source_tree: Path) -> None:
    """Tree-side fixes the manual visual_studio_path mode needs. Idempotent;
    applied on every run (they live in the source tree, not the SDK mount)."""
    # Upstream regression: the manual-toolchain branch of
    # visual_studio_version.gni passes a list where a string is required.
    gni = source_tree / 'build' / 'config' / 'win' / 'visual_studio_version.gni'
    text = gni.read_text(encoding='utf-8')
    if 'visual_studio_runtime_dirs = []' in text:
        text = text.replace('visual_studio_runtime_dirs = []',
                            'visual_studio_runtime_dirs = ""')
        gni.write_text(text, encoding='utf-8')


def sdk_ver(sdk_root: Path) -> str:
    return next((sdk_root / 'Windows Kits' / '10' / 'Include').iterdir()).name


def _patch_sdkddkver(sdk_root: Path) -> None:
    """Chromium pins NTDDI_VERSION=NTDDI_WIN11_BR (SDK 10.0.28000) and
    base/win/windows_version.cc hard-errors without the constant; the
    public SDK 26100 sdkddkver.h ladder stops at NTDDI_WIN11_GE. Nothing
    else gates on BR (grep: windows_version.cc only), so define it.
    Idempotent; applied on every run."""
    sdkddk = (sdk_root / 'Windows Kits' / '10' / 'Include' / sdk_ver(sdk_root)
              / 'shared' / 'sdkddkver.h')
    text = sdkddk.read_text(encoding='utf-8', errors='replace')
    if 'NTDDI_WIN11_BR' not in text:
        text += ('\n/* veilbrowser cross build: the public 26100 sdkddkver.h predates the\n'
                 ' * 28000 ladder; chromium only requires the constant to exist. */\n'
                 '#define NTDDI_WIN11_BR 0x0A000011\n')
        sdkddk.write_text(text, encoding='utf-8')



def _fix_rc_wrapper(source_tree: Path) -> None:
    """The windows patch series makes tool_wrapper.py's rc wrapper refuse
    non-Windows hosts (their fix routes .rc straight to rc.exe). Restore
    upstream's behaviour on other hosts: the pure-python rc compiler in
    build/toolchain/win/rc/, which exists precisely for linux cross builds.
    Idempotent; applied on every run."""
    tw = source_tree / 'build' / 'toolchain' / 'win' / 'tool_wrapper.py'
    text = tw.read_text(encoding='utf-8')
    broken = ("        if sys.platform == 'win32':\n"
              "            rc_exe_exit_code = subprocess.call(args, shell=True, env=env)\n"
              "            return rc_exe_exit_code\n"
              "        else:\n"
              "            raise RuntimeError('Must run on Windows.')")
    fixed = ("        if sys.platform == 'win32':\n"
             "            rc_exe_exit_code = subprocess.call(args, shell=True, env=env)\n"
             "            return rc_exe_exit_code\n"
             "        rcpy_args = args[:]\n"
             "        rcpy_args[0:1] = [sys.executable, os.path.join(BASE_DIR, 'rc', 'rc.py')]\n"
             "        rcpy_args.append('/showIncludes')\n"
             "        return subprocess.call(rcpy_args, env=env)")
    if broken in text:
        tw.write_text(text.replace(broken, fixed), encoding='utf-8')


def _ensure_linux_rc_binary(source_tree: Path) -> None:
    """Fetch the prebuilt linux64 resource compiler from the
    chromium-browser-clang/rc bucket (the DEPS 'rc_linux' hook does this for
    gclient checkouts; a tarball tree only carries the .sha1 marker).
    Idempotent."""
    rc_dir = source_tree / 'build' / 'toolchain' / 'win' / 'rc' / 'linux64'
    rc_bin = rc_dir / 'rc'
    if rc_bin.exists():
        return
    sha1 = (rc_dir / 'rc.sha1').read_text(encoding='utf-8').strip()
    url = f'{_CDS_URL}/rc/{sha1}'
    get_logger().info('Fetching %s', url)
    with urllib.request.urlopen(url) as resp:
        rc_bin.write_bytes(resp.read())
    rc_bin.chmod(0o755)


def _provision_msvc_sdk(sdk_root: Path, cache_dir: Path) -> None:
    """Fetch MSVC+SDK via vsdownload.py and lay it out on a casefold mount.

    Windows SDK headers include each other with inconsistent casing; on a
    case-sensitive FS clang fails on e.g. <windows.h> vs Windows.h. Googler
    cross builds use ciopfs (not packaged); we use an ext4 loop image with
    the casefold feature instead — kernel-native, no FUSE needed.
    """
    sdk_root.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if (sdk_root / 'Windows Kits').exists():
        _patch_sdkddkver(sdk_root)
        get_logger().info('MSVC/SDK already provisioned at %s', sdk_root)
        return

    # Keep everything inside the (user-writable) workspace; only the
    # image create/format/mount steps need sudo.
    base = sdk_root.parent          # the loop-mount point, e.g. <ws>/win-sdk
    img = base.parent / 'sdk-casefold.img'
    mount = base
    subprocess.run(['sudo', 'truncate', '-s', '12G', str(img)], check=True)
    subprocess.run(['sudo', 'mkfs.ext4', '-q', '-O', 'casefold', '-F', str(img)],
                   check=True)
    subprocess.run(['sudo', 'mkdir', '-p', str(mount)], check=True)
    mounted = subprocess.run(['findmnt', '-n', '-o', 'TARGET', str(mount)],
                             capture_output=True, text=True).stdout.strip()
    if mounted != str(mount):
        subprocess.run(['sudo', 'mount', '-o', 'loop', str(img), str(mount)], check=True)
    subprocess.run(['sudo', 'chown', f'{os.getuid()}:{os.getgid()}', str(mount)], check=True)

    scratch = base.parent / 'msvc-unpacked'
    if not (scratch / 'VC').exists():
        scratch.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent
                                            / _VSDOWNLOAD_RELPATH),
                        '--dest', str(scratch), '--cache', str(cache_dir),
                        '--architecture', 'host', 'x64', '--accept-license'],
                       check=True)

    # Layout: skeleton dirs first (casefold flag can only be set on empty
    # dirs and is not inherited on creation by cp -a), then the files. Names
    # that collide under case folding are SDK metadata doubles that Windows
    # itself cannot host either — skipped.
    root = sdk_root
    src_dirs = sorted(p for p in scratch.rglob('*') if p.is_dir())
    for d in src_dirs:
        rel = d.relative_to(scratch)
        (root / rel).mkdir(parents=True, exist_ok=True)
        subprocess.run(['chattr', '+F', str(root / rel)],
                       stderr=subprocess.DEVNULL)
    skipped = 0
    for f in sorted(p for p in scratch.rglob('*') if p.is_file()):
        rel = f.relative_to(scratch)
        dst = root / rel
        try:
            shutil.copy2(f, dst)
        except (OSError, FileNotFoundError):
            skipped += 1
    get_logger().info('MSVC/SDK laid out at %s (%d case-collisions skipped)',
                      root, skipped)

    # Regenerate the toolchain env files the Googler package ships: clang-cl
    # (via setup_toolchain.py) reads INCLUDE/LIB/PATH from them. Forward
    # slashes: entries are os.path.join'ed on the linux host.
    msvc_ver = next((root / 'VC' / 'Tools' / 'MSVC').iterdir()).name
    sdk_ver = next((root / 'Windows Kits' / '10' / 'Include').iterdir()).name
    for cpu in ('x64', 'x86'):
        lib = 'x64' if cpu == 'x64' else 'x86'
        env = {
            'VSINSTALLDIR': [['.']],
            'VCINSTALLDIR': [['VC']],
            'INCLUDE': [
                [f'VC/Tools/MSVC/{msvc_ver}/include'],
                [f'VC/Tools/MSVC/{msvc_ver}/atlmfc/include'],
                ['VC/Auxiliary/VS/include'],
                [f'Windows Kits/10/Include/{sdk_ver}/ucrt'],
                [f'Windows Kits/10/Include/{sdk_ver}/um'],
                [f'Windows Kits/10/Include/{sdk_ver}/shared'],
                [f'Windows Kits/10/Include/{sdk_ver}/winrt'],
                [f'Windows Kits/10/Include/{sdk_ver}/cppwinrt'],
            ],
            'LIB': [
                [f'VC/Tools/MSVC/{msvc_ver}/lib/{lib}'],
                [f'VC/Tools/MSVC/{msvc_ver}/atlmfc/lib/{lib}'],
                [f'Windows Kits/10/Lib/{sdk_ver}/ucrt/{lib}'],
                [f'Windows Kits/10/Lib/{sdk_ver}/um/{lib}'],
            ],
            'PATH': [
                [f'VC/Tools/MSVC/{msvc_ver}/bin/Hostx64/{cpu}'],
                [f'Windows Kits/10/bin/{sdk_ver}/{cpu}'],
            ],
        }
        env['LIBPATH'] = env['LIB']
        path_json = root / 'Windows Kits' / '10' / 'bin' / f'SetEnv.{cpu}.json'
        path_json.parent.mkdir(parents=True, exist_ok=True)
        path_json.write_text(json.dumps({'env': env}, indent=2), encoding='utf-8')
        # The x86 toolchain data is dead weight for an x64 build, but gn
        # evaluates it anyway and checks that cl.exe exists in PATH.
        cl_dir = root / 'VC' / 'Tools' / 'MSVC' / msvc_ver / 'bin' / 'Hostx64' / cpu
        cl_dir.mkdir(parents=True, exist_ok=True)
        if not (cl_dir / 'cl.exe').exists():
            (cl_dir / 'cl.exe').write_bytes(b'')

    _patch_sdkddkver(sdk_root)


def _link_node_esbuild(source_tree: Path) -> None:
    """Host node/esbuild for devtools-frontend (mirrors scripts/build-kernel.sh)."""
    node_bin = shutil.which('node')
    esbuild = None
    npm_root = subprocess.run(['npm', 'root', '-g'], capture_output=True, text=True)
    if npm_root.returncode == 0:
        candidate = Path(npm_root.stdout.strip()) / 'esbuild'
        if candidate.is_dir():
            esbuild = candidate
    node_dir = source_tree / 'third_party' / 'node' / 'linux' / 'node-linux-x64' / 'bin'
    node_dir.mkdir(parents=True, exist_ok=True)
    if node_bin and not (node_dir / 'node').exists():
        os.symlink(node_bin, node_dir / 'node')
    for rel in ('third_party/devtools-frontend/src/node_modules/esbuild',
                'third_party/devtools-frontend/src/third_party/esbuild'):
        if not esbuild:
            continue
        target = source_tree / rel
        if target.is_symlink():
            continue
        if target.exists() and target.is_dir() and any(target.iterdir()):
            continue  # real content — leave it alone
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(esbuild, target)


def _unpack_ugc_content_downloads(ugc_win: Path, source_tree: Path,
                                  downloads_cache: Path) -> None:
    """Retrieve + unpack the ugc-win download entries that carry tree CONTENT
    the windows patches reference (typescript, directx-headers, webauthn).
    The windows-native tool entries are intentionally skipped: the cross
    build uses pinned linux clang, system ninja/patch/node/gperf/go and the
    tarball's linux rust instead."""
    SKIP = {'llvm', 'bison-bin', 'bison-dep', 'bison-lib', 'ninja', 'git',
            'nodejs', 'go-x64', 'go-arm64', 'esbuild', 'gperf',
            'rust-x64', 'rust-x86', 'rust-arm', 'rust-windows-create'}
    cp = configparser.ConfigParser(strict=False)
    cp.read(ugc_win / 'downloads.ini', encoding=globals()['ENCODING'])
    for sec in list(cp.sections()):
        if sec in SKIP:
            cp.remove_section(sec)
    filtered = ugc_win / 'build' / 'downloads-cross.ini'
    filtered.parent.mkdir(parents=True, exist_ok=True)
    with open(filtered, 'w', encoding=globals()['ENCODING']) as f:
        cp.write(f)
    download_info = downloads.DownloadInfo([filtered])
    _retrieve_with_retry(download_info, downloads_cache)
    # The tarball's dxheaders placeholder collides with the download, exactly
    # like in the native pipeline.
    directx = source_tree / 'third_party' / 'microsoft_dxheaders' / 'src'
    if directx.exists():
        shutil.rmtree(directx)
        directx.mkdir()
    extractors = {globals()['ExtractorEnum'].SEVENZIP: shutil.which('7z') or '7z',
                  globals()['ExtractorEnum'].TAR: shutil.which('tar') or 'tar'}
    downloads.unpack_downloads(download_info, downloads_cache, None,
                               source_tree, extractors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ugc-win', required=True, type=Path,
                        help='ungoogled-chromium-windows clone (with its '
                             'ungoogled-chromium submodule)')
    parser.add_argument('--kernel-patches', required=True, type=Path,
                        help='veilbrowser kernel-patches directory')
    parser.add_argument('--out', default='dist', type=Path,
                        help='output directory for the kernel zip')
    parser.add_argument('--sdk-root', default=Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())) / 'win-sdk' / 'msvc', type=Path,
                        help='casefold mount point for the MSVC/SDK tree')
    parser.add_argument('--sdk-cache', default=Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())) / 'vsdownload-cache', type=Path,
                        help='persistent vsdownload package cache')
    parser.add_argument('-j', type=int, dest='thread_count', default=None,
                        help='ninja parallelism (default: all cores)')
    args = parser.parse_args()

    ugc_win = args.ugc_win.resolve()
    kernel_patches = args.kernel_patches.resolve()
    _init_ugc_imports(ugc_win)

    global _ROOT_DIR
    _ROOT_DIR = ugc_win
    source_tree = ugc_win / 'build' / 'src'
    downloads_cache = ugc_win / 'build' / 'download_cache'

    version = (ugc_win / 'ungoogled-chromium' / 'chromium_version.txt') \
        .read_text(encoding=globals()['ENCODING']).strip()

    # ---- source + patches (idempotent: skip when the tree is already set up)
    if not (source_tree / 'BUILD.gn').exists():
        source_tree.mkdir(parents=True, exist_ok=True)
        downloads_cache.mkdir(parents=True, exist_ok=True)

        get_logger().info('Downloading chromium tarball...')
        source_ini = kernel_patches / 'downloads.ini'
        if not source_ini.exists():
            source_ini = ugc_win / 'ungoogled-chromium' / 'downloads.ini'
        download_info = downloads.DownloadInfo([source_ini])
        _retrieve_with_retry(download_info, downloads_cache)
        _unpack_tarball(downloads_cache, source_tree)

        # Prune binaries
        pruning_list = ugc_win / 'ungoogled-chromium' / 'pruning.list'
        unremovable = prune_binaries.prune_files(
            source_tree, pruning_list.read_text(encoding=globals()['ENCODING']).splitlines())
        if unremovable:
            get_logger().error('Files could not be pruned: %s', unremovable)
            return 1

        get_logger().info('Installing host toolchain (linux clang + win runtime + rust std)...')
        # The full tarball ships placeholder llvm-build content that collides
        # with the pinned toolchain — drop it first.
        llvm_build = source_tree / 'third_party' / 'llvm-build'
        if llvm_build.exists():
            shutil.rmtree(llvm_build)
        _unmask_toolchain_urls(source_tree)
        _install_host_clang(source_tree)
        _install_rust_win_std(source_tree)
        _unpack_ugc_content_downloads(ugc_win, source_tree, downloads_cache)
        _link_node_esbuild(source_tree)

        # patch_bin_path=None -> ugc's find_and_check_patch() (system patch)
        get_logger().info('Applying ungoogled-chromium patches...')
        patches.apply_patches(
            patches.generate_patches_from_series(
                ugc_win / 'ungoogled-chromium' / 'patches', resolve=True),
            source_tree, patch_bin_path=None)
        get_logger().info('Applying Windows-specific patches...')
        patches.apply_patches(
            patches.generate_patches_from_series(ugc_win / 'patches', resolve=True),
            source_tree, patch_bin_path=None)
        get_logger().info('Applying veilbrowser fingerprint patches...')
        patches.apply_patches(
            patches.generate_patches_from_series(
                _write_extra_series(kernel_patches), resolve=True),
            source_tree, patch_bin_path=None)

        get_logger().info('Substituting domains...')
        domain_substitution.apply_substitution(
            ugc_win / 'ungoogled-chromium' / 'domain_regex.list',
            ugc_win / 'ungoogled-chromium' / 'domain_substitution.list',
            source_tree, None)

    # ---- MSVC/SDK on the casefold mount ------------------------------------
    _patch_tree_for_cross_sdk(source_tree)
    _fix_rc_wrapper(source_tree)
    _ensure_linux_rc_binary(source_tree)
    _provision_msvc_sdk(args.sdk_root.resolve(), args.sdk_cache.resolve())

    # ---- rust toolchain layout ----------------------------------------------
    RUST_DIR_DST = source_tree / 'third_party' / 'rust-toolchain'
    RUST_FLAG_FILE = RUST_DIR_DST / 'INSTALLED_VERSION'
    # The tarball ships linux rust binaries directly in bin/ — exactly what a
    # cross build needs; no component copying (that is the native-Windows path).
    if not (RUST_DIR_DST / 'bin' / 'rustc').exists():
        raise RuntimeError('tarball linux rust toolchain missing under '
                           'third_party/rust-toolchain')
    if not RUST_FLAG_FILE.exists():
        with open(RUST_FLAG_FILE, 'w') as f:
            subprocess.run([str(RUST_DIR_DST / 'bin' / 'rustc'), '--version'], stdout=f)

    # ---- GN args -------------------------------------------------------------
    out_dir = source_tree / 'out' / 'Default'
    out_dir.mkdir(parents=True, exist_ok=True)
    enc = globals()['ENCODING']
    sdk_root = args.sdk_root.resolve()
    sdk_ver = next((sdk_root / 'Windows Kits' / '10' / 'Include').iterdir()).name
    gn_flags = (kernel_patches / 'flags.gn').read_text(encoding=enc)
    gn_flags += '\n' + (kernel_patches / 'flags.windows.gn').read_text(encoding=enc)
    gn_flags += '\nchrome_pgo_phase=0\n'  # tarball builds have no PGO profile
    gn_flags += f'target_os = "win"\ntarget_cpu = "x64"\n'
    gn_flags += f'visual_studio_path = "{sdk_root}"\n'
    gn_flags += 'visual_studio_version = "2022"\n'
    gn_flags += f'wdk_path = "{sdk_root}/Windows Kits/10"\n'
    gn_flags += f'windows_sdk_path = "{sdk_root}/Windows Kits/10"\n'
    gn_flags += f'windows_sdk_version = "{sdk_ver}"\n'
    # flags.windows.gn sets false (meaningless for a native windows build);
    # on a linux host the toolchain needs the sysroot for its own configs.
    gn_flags += 'use_sysroot = true\n'
    gn_flags += os.environ.get('VEIL_GN_EXTRA', '').replace('\\n', '\n') + '\n'
    (out_dir / 'args.gn').write_text(gn_flags, encoding=enc)

    # ---- build ----------------------------------------------------------------
    os.chdir(source_tree)
    # The host (linux) toolchain still evaluates linux configs during
    # gen; with the sysroot installed, its pkg-config lookups (nss, glib,
    # ...) resolve inside the sysroot instead of needing host -dev packs.
    subprocess.run([sys.executable, 'build/linux/sysroot_scripts/install-sysroot.py',
                    '--arch=x64'], check=True)
    if not os.path.exists('out/Default/gn'):
        # The windows patch series pins bootstrap.py's ninja target to
        # 'gn.exe'; on linux the target is plain 'gn'. Replicate its steps.
        # gen.py defaults to 'clang++' from PATH — point it at the tree's.
        gn_env = dict(os.environ,
                      CXX=os.path.abspath(
                          'third_party/llvm-build/Release+Asserts/bin/clang++'))
        subprocess.run([sys.executable, 'tools/gn/build/gen.py',
                        '--no-last-commit-position',
                        '--out-path=out/Release/gn_build'],
                       check=True, env=gn_env)
        shutil.copy2('tools/gn/bootstrap/last_commit_position.h',
                     'out/Release/gn_build/')
        subprocess.run(['ninja', '-C', 'out/Release/gn_build', 'gn'], check=True)
        shutil.copy2('out/Release/gn_build/gn', 'out/Default/gn')
    subprocess.run(['out/Default/gn', 'gen', 'out/Default',
                    '--fail-on-unused-args'], check=True)
    if not (source_tree / 'third_party' / 'rust-toolchain' / 'bin' / 'bindgen').exists():
        subprocess.run([sys.executable, 'tools/rust/build_bindgen.py', '--skip-test'],
                       check=True)

    ninja = ['ninja']
    if args.thread_count:
        ninja += ['-j', str(args.thread_count)]
    ninja += ['-C', 'out/Default', 'chrome']
    subprocess.run(ninja, check=True)

    zip_path = _package(source_tree, args.out.resolve(),
                        'veil-chromium-{}-win-x64.zip'.format(version))
    print('==> kernel zip: {}'.format(zip_path))
    print('    use it with:  VEIL_CHROME_PATH=<unpacked>/chrome.exe veilbrowser check')
    return 0


if __name__ == '__main__':
    sys.exit(main())
