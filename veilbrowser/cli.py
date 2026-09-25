"""veilbrowser command line: launch / check / profiles."""

from __future__ import annotations

import argparse
import sys

from .browser import default_binary, launch
from .profile import PRESETS, FingerprintProfile, from_preset


def _add_engine_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--engine", choices=("js", "kernel", "both"), default="js",
                   help="js = our injected bundle on any kernel (default); "
                        "kernel = fingerprint-chromium patches; both = stacked")
    p.add_argument("--vanilla", action="store_true",
                   help="prefer the vanilla ungoogled-chromium binary (engine=js)")


def _add_profile_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seed", type=int, default=None, help="fingerprint seed (32-bit int)")
    p.add_argument("--preset", choices=sorted(PRESETS), default=None,
                   help="coherent persona preset (seed still drives the rest)")
    p.add_argument("--platform", choices=("windows", "macos", "linux"))
    p.add_argument("--timezone")
    p.add_argument("--language")
    p.add_argument("--brand", help="Chrome | Edge | Opera | Vivaldi")
    p.add_argument("--concurrency", type=int, help="fingerprint-hardware-concurrency")
    p.add_argument("--proxy", help="scheme://user:pass@host:port (auth handled locally)")


def _profile_from_args(args: argparse.Namespace) -> FingerprintProfile:
    if args.preset:
        if args.seed is None:
            sys.exit("--preset requires --seed")
        prof = from_preset(args.preset, args.seed)
    else:
        prof = FingerprintProfile(seed=args.seed if args.seed is not None else 0)
    for attr in ("platform", "timezone", "language", "brand"):
        val = getattr(args, attr)
        if val:
            setattr(prof, attr, val)
    if args.concurrency:
        prof.hardware_concurrency = args.concurrency
    if args.proxy:
        prof.proxy = args.proxy
    return prof


def cmd_launch(args: argparse.Namespace) -> int:
    prof = _profile_from_args(args)
    b = launch(prof, engine=args.engine, headless=not args.headed,
               binary=default_binary(vanilla=args.vanilla) if args.vanilla else None)
    try:
        from .probe import collect, open_probe_page, print_report
        if args.url != "about:blank":
            with b.new_page(args.url) as page:
                page.navigate(args.url)
        else:
            with open_probe_page(b) as page:
                results = collect(page)
                if args.quiet:
                    import json
                    print(json.dumps(results, indent=2, ensure_ascii=False))
                else:
                    print_report(results)
    finally:
        if not args.keep:
            b.stop()
        else:
            print(f"browser running: pid={b.pid} devtools={b.port}", file=sys.stderr)
            input("press Enter to stop...") if sys.stdin.isatty() else None
            b.stop()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    prof = _profile_from_args(args)
    b = launch(prof, engine=args.engine, headless=True,
               binary=default_binary(vanilla=args.vanilla) if args.vanilla else None)
    try:
        from .probe import collect, open_probe_page, print_report
        with open_probe_page(b) as page:
            return 0 if print_report(collect(page)) else 1
    finally:
        b.stop()


def cmd_profiles(_args: argparse.Namespace) -> int:
    for name, spec in sorted(PRESETS.items()):
        print(f"{name:<20} platform={spec['platform']:<8} lang={spec['language']:<6} "
              f"brand={spec['brand']:<8} cores={spec['hardware_concurrency']}")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    from . import upgrade as up
    latest = up.latest_release()
    if args.version:
        latest["version"] = args.version
    if args.check:
        print(f"latest portablelinux kernel: {latest['version']}")
        print(f"  {latest['url']}")
        return 0
    print(f"installing ungoogled-chromium {latest['version']} ...", file=sys.stderr)
    result = up.upgrade(dest_root=args.dest, version=args.version)
    print(f"[{result['status']}] {result['version']} -> {result['binary']}")
    if result.get("config"):
        print(f"config updated: {result['config']}")
    return 0


def cmd_path(_args: argparse.Namespace) -> int:
    k = default_binary()
    v = default_binary(vanilla=True)
    print(f"fingerprint kernel: {k or '(not found — set VEIL_CHROME_PATH)'}")
    print(f"vanilla kernel:     {v or '(not found — set VEIL_VANILLA_CHROME_PATH)'}")
    return 0 if (k or v) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veilbrowser",
                                 description="CloakBrowser-style wrapper for fingerprint-chromium")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_launch = sub.add_parser("launch", help="launch and self-check, then exit")
    _add_profile_args(p_launch)
    _add_engine_args(p_launch)
    p_launch.add_argument("--url", default="about:blank")
    p_launch.add_argument("--headed", action="store_true", help="show a window (default headless)")
    p_launch.add_argument("--keep", action="store_true", help="keep browser running interactively")
    p_launch.add_argument("--quiet", action="store_true", help="dump raw JSON instead of a report")
    p_launch.set_defaults(func=cmd_launch)

    p_check = sub.add_parser("check", help="headless fingerprint self-check")
    _add_profile_args(p_check)
    _add_engine_args(p_check)
    p_check.set_defaults(func=cmd_check)

    sub.add_parser("profiles", help="list persona presets").set_defaults(func=cmd_profiles)

    p_up = sub.add_parser("upgrade",
                          help="pull the newest community ungoogled-chromium kernel")
    p_up.add_argument("--dest", help="install root (default ~/.veilbrowser/kernels)")
    p_up.add_argument("--version", help="pin a specific chromium version")
    p_up.add_argument("--check", action="store_true",
                      help="only show the latest available version")
    p_up.set_defaults(func=cmd_upgrade)
    sub.add_parser("path", help="print detected binary path").set_defaults(func=cmd_path)

    args = ap.parse_args(argv)
    return args.func(args)
