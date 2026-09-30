#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2019 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Build the patched "veil-chromium" kernel on Windows x64.

Adapted from ungoogled-chromium-windows' build.py (tag 153.0.8010.52-1.1),
with these differences:
  * tarball mode only (official chromium-lite tarball, hash-verified)
  * applies the veilbrowser fingerprint patch set after the ungoogled and
    Windows patches
  * kernel flags come from kernel-patches/flags.gn + flags.windows.gn
    (+ VEIL_GN_EXTRA extra args lines, e.g. use_thin_lto=false for CI)
  * builds only the `chrome` target (no mini_installer / chromedriver)
  * packages a portable zip instead of an installer

Run inside a clone of ungoogled-chromium-windows at the tag matching the
Chromium version, with this repository checked out somewhere:

    git clone --depth 1 --branch 153.0.8010.52-1.1 --recurse-submodules \
        --shallow-submodules \\
        https://github.com/ungoogled-software/ungoogled-chromium-windows
    python <veilbrowser>/scripts/build-kernel-windows.py \\
        --ugc-win <path>/ungoogled-chromium-windows \\
        --kernel-patches <veilbrowser>/kernel-patches \\
        --out <veilbrowser>/dist

Requires Visual Studio 2022 with the C++ workload (found via vswhere),
7-Zip, and Python 3 with the `httplib2` module. Long paths must be enabled.
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

_ROOT_DIR = None  # set in main(): the ungoogled-chromium-windows clone
_PATCH_BIN_RELPATH = Path('third_party/git/usr/bin/patch.exe')


def _init_ugc_imports(ugc_win: Path) -> None:
    sys.path.insert(0, str(ugc_win / 'ungoogled-chromium' / 'utils'))
    global downloads, domain_substitution, prune_binaries, patches
    global _common
    import downloads
    import domain_substitution
    import prune_binaries
    import patches
    from _common import ENCODING, USE_REGISTRY, ExtractorEnum, get_logger
    globals()['ENCODING'] = ENCODING
    globals()['USE_REGISTRY'] = USE_REGISTRY
    globals()['ExtractorEnum'] = ExtractorEnum
    globals()['get_logger'] = get_logger


def _get_vcvars_path(name='64'):
    """Returns the path to the corresponding vcvars*.bat path."""
    vswhere_exe = '%ProgramFiles(x86)%\\Microsoft Visual Studio\\Installer\\vswhere.exe'
    result = subprocess.run(
        '"{}" -products * -prerelease -latest -property installationPath'.format(vswhere_exe),
        shell=True, check=True, stdout=subprocess.PIPE, universal_newlines=True)
    vcvars_path = Path(result.stdout.strip(), 'VC/Auxiliary/Build/vcvars{}.bat'.format(name))
    if not vcvars_path.exists():
        raise RuntimeError('Could not find vcvars batch script: {}'.format(vcvars_path))
    return vcvars_path


def _run_build_process(*args):
    """Runs the subprocess inside a vcvars environment (like upstream)."""
    cmd_input = ['call "%s" >nul' % _get_vcvars_path()]
    cmd_input.append('set DEPOT_TOOLS_WIN_TOOLCHAIN=0')
    cmd_input.append(' '.join(map('"{}"'.format, args)))
    cmd_input.append('exit\n')
    subprocess.run(('cmd.exe', '/k'), input='\n'.join(cmd_input), check=True,
                   encoding=globals()['ENCODING'])


def _write_extra_series(kernel_patches: Path) -> Path:
    """Derives kernel-patches/extra/series from the fingerprint series file.

    Only extra/fingerprint/ entries are ours; the other extra/ lines
    (inox-patchset, iridium-browser, ...) belong to ungoogled's own patch
    tree and are already applied with ugc/patches.
    """
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ugc-win', required=True, type=Path,
                        help='ungoogled-chromium-windows clone (with its '
                             'ungoogled-chromium submodule)')
    parser.add_argument('--kernel-patches', required=True, type=Path,
                        help='veilbrowser kernel-patches directory')
    parser.add_argument('--out', default='dist', type=Path,
                        help='output directory for the kernel zip')
    parser.add_argument('--7z-path', dest='sevenz_path', default=None,
                        help="path to 7-Zip's 7z.exe (default: autodetect)")
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

    sevenz = args.sevenz_path
    if sevenz is None:
        for candidate in (r'C:\Program Files\7-Zip\7z.exe',
                          r'C:\Program Files (x86)\7-Zip\7z.exe'):
            if Path(candidate).exists():
                sevenz = candidate
                break
        else:
            sevenz = globals()['USE_REGISTRY']
    extractors = {
        globals()['ExtractorEnum'].SEVENZIP: sevenz,
        globals()['ExtractorEnum'].WINRAR: globals()['USE_REGISTRY'],
    }

    version = (ugc_win / 'ungoogled-chromium' / 'chromium_version.txt') \
        .read_text(encoding=globals()['ENCODING']).strip()

    # ---- source + patches (idempotent: skip when the tree is already set up)
    if not (source_tree / 'BUILD.gn').exists():
        source_tree.mkdir(parents=True, exist_ok=True)
        downloads_cache.mkdir(parents=True, exist_ok=True)

        # Official chromium tarball. Use the veilbrowser downloads.ini (FULL
        # tarball): the -lite tarball ungoogled ships is cut from a slightly
        # different snapshot (newer v8) that the fingerprint patches don't
        # match. downloads.py resolves %(_chromium_version)s from the
        # ungoogled clone's chromium_version.txt.
        get_logger().info('Downloading chromium tarball...')
        source_ini = kernel_patches / 'downloads.ini'
        if not source_ini.exists():
            source_ini = ugc_win / 'ungoogled-chromium' / 'downloads.ini'
        download_info = downloads.DownloadInfo([source_ini])
        downloads.retrieve_downloads(download_info, downloads_cache, None, True)
        try:
            downloads.check_downloads(download_info, downloads_cache, None)
        except downloads.HashMismatchError as exc:
            get_logger().error('File checksum does not match: %s', exc)
            return 1

        get_logger().info('Unpacking chromium tarball...')
        downloads.unpack_downloads(download_info, downloads_cache, None,
                                   source_tree, extractors)

        get_logger().info('Downloading toolchain (LLVM, rust, git, ninja, node)...')
        download_info_win = downloads.DownloadInfo([ugc_win / 'downloads.ini'])
        downloads.retrieve_downloads(download_info_win, downloads_cache, None, True)
        try:
            downloads.check_downloads(download_info_win, downloads_cache, None)
        except downloads.HashMismatchError as exc:
            get_logger().error('File checksum does not match: %s', exc)
            return 1

        # Prune binaries
        pruning_list = ugc_win / 'ungoogled-chromium' / 'pruning.list'
        unremovable = prune_binaries.prune_files(
            source_tree, pruning_list.read_text(encoding=globals()['ENCODING']).splitlines())
        if unremovable:
            get_logger().error('Files could not be pruned: %s', unremovable)
            return 1

        # Unpack toolchain downloads into the tree
        DIRECTX = source_tree / 'third_party' / 'microsoft_dxheaders' / 'src'
        ESBUILD = source_tree / 'third_party' / 'devtools-frontend' / 'src' / 'third_party' / 'esbuild'
        for d in (DIRECTX, ESBUILD):
            if d.exists():
                shutil.rmtree(d)
                d.mkdir()
        get_logger().info('Unpacking toolchain...')
        downloads.unpack_downloads(download_info_win, downloads_cache, None,
                                   source_tree, extractors)

        patch_bin = source_tree / _PATCH_BIN_RELPATH
        get_logger().info('Applying ungoogled-chromium patches...')
        patches.apply_patches(
            patches.generate_patches_from_series(
                ugc_win / 'ungoogled-chromium' / 'patches', resolve=True),
            source_tree, patch_bin_path=patch_bin)
        get_logger().info('Applying Windows-specific patches...')
        patches.apply_patches(
            patches.generate_patches_from_series(ugc_win / 'patches', resolve=True),
            source_tree, patch_bin_path=patch_bin)
        get_logger().info('Applying veilbrowser fingerprint patches...')
        patches.apply_patches(
            patches.generate_patches_from_series(
                _write_extra_series(kernel_patches), resolve=True),
            source_tree, patch_bin_path=patch_bin)

        get_logger().info('Substituting domains...')
        domain_substitution.apply_substitution(
            ugc_win / 'ungoogled-chromium' / 'domain_regex.list',
            ugc_win / 'ungoogled-chromium' / 'domain_substitution.list',
            source_tree, None)

    # ---- rust toolchain layout (upstream logic) -----------------------------
    HOST_CPU_IS_64BIT = sys.maxsize > 2**32
    RUST_DIR_DST = source_tree / 'third_party' / 'rust-toolchain'
    RUST_FLAG_FILE = RUST_DIR_DST / 'INSTALLED_VERSION'
    if not RUST_FLAG_FILE.exists():
        for rust_dir_src in ('rust-toolchain-x64', 'rust-toolchain-x86', 'rust-toolchain-arm'):
            src_dir = source_tree / 'third_party' / rust_dir_src
            for dir_to_copy in ('bin', 'lib'):
                if dir_to_copy == 'bin' and not rust_dir_src.endswith('x64'):
                    continue  # host is x64
                target_dir = RUST_DIR_DST / dir_to_copy
                if not target_dir.exists():
                    os.makedirs(target_dir)
                for cp_src in src_dir.glob('*/{0}/*'.format(dir_to_copy)):
                    cp_dst = target_dir / cp_src.name
                    if cp_src.is_dir():
                        shutil.copytree(cp_src, cp_dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(cp_src, cp_dst)
        with open(RUST_FLAG_FILE, 'w') as f:
            subprocess.run([str(source_tree / 'third_party' / 'rust-toolchain-x64'
                                / 'rustc' / 'bin' / 'rustc.exe'), '--version'], stdout=f)

    # ---- GN args -------------------------------------------------------------
    out_dir = source_tree / 'out' / 'Default'
    out_dir.mkdir(parents=True, exist_ok=True)
    enc = globals()['ENCODING']
    gn_flags = (kernel_patches / 'flags.gn').read_text(encoding=enc)
    gn_flags += '\n' + (kernel_patches / 'flags.windows.gn').read_text(encoding=enc)
    gn_flags += '\nchrome_pgo_phase=0\n'  # tarball builds have no PGO profile
    gn_flags += os.environ.get('VEIL_GN_EXTRA', '').replace('\\n', '\n') + '\n'
    (out_dir / 'args.gn').write_text(gn_flags, encoding=enc)

    # ---- build ----------------------------------------------------------------
    os.chdir(source_tree)
    if not os.path.exists(r'out\Default\gn.exe'):
        _run_build_process(sys.executable, r'tools\gn\bootstrap\bootstrap.py',
                           '-o', r'out\Default\gn.exe', '--skip-generate-buildfiles')
        _run_build_process(r'out\Default\gn.exe', 'gen', r'out\Default',
                           '--fail-on-unused-args')
    if not os.path.exists(r'third_party\rust-toolchain\bin\bindgen.exe'):
        _run_build_process(sys.executable, r'tools\rust\build_bindgen.py', '--skip-test')

    ninja = [r'third_party\ninja\ninja.exe']
    if args.thread_count:
        ninja += ['-j', str(args.thread_count)]
    ninja += ['-C', r'out\Default', 'chrome']
    _run_build_process(*ninja)

    zip_path = _package(source_tree, args.out.resolve(),
                        'veil-chromium-{}-win-x64.zip'.format(version))
    print('==> kernel zip: {}'.format(zip_path))
    print('    use it with:  set VEIL_CHROME_PATH=<unpacked>\\chrome.exe')
    return 0


if __name__ == '__main__':
    sys.exit(main())
