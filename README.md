# veilbrowser

[English](README.md) | [中文](README.zh-CN.md) | [日本語](README.ja.md)

[![CI](https://github.com/iBugCode/veilbrowser/actions/workflows/ci.yml/badge.svg)](https://github.com/iBugCode/veilbrowser/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-linux%20x86__64-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-informational)
![License](https://img.shields.io/badge/license-MIT-green)

An open-source fingerprint (anti-detect) browser SDK for automation, aiming
to be **the best open-source fingerprint browser**. It ships a dual-engine
design:

- **JS engine** — a CDP-injected JS bundle that runs on any vanilla
  [ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
  kernel (upgrading kernels is just downloading a new package), and
- **C++ kernel** (since v0.7.0) — the self-compiled **veil-chromium** kernel:
  ungoogled-chromium 153 + 19 C++ fingerprint patches, a full
  ninja/ThinLTO official build whose TLS layer is byte-for-byte
  ja3-verified against the stock binary.

The design references [Camoufox](https://github.com/daijro/camoufox)
(statistical realism and cross-signal coherence) and
[CloakBrowser](https://github.com/CloakHQ/CloakBrowser) (product shape and
feature set, used as the closed-source benchmark). The kernel patch set
builds on the fingerprint-chromium patch ideas. **Only open-source
components are used** — no proprietary code is included or derived (see
[License](#license)).

> ⚠️ **Disclaimer**: browser-fingerprint evasion is a cat-and-mouse field.
> Use veilbrowser only where you have the legal right to do so; automated
> access may violate the terms of service of individual sites. You are
> responsible for how you use it.

## Features

- **Seed → coherent fingerprint profile**: language ↔ timezone ↔ platform ↔
  GPU ↔ screen all agree (GPU strings use real per-platform ANGLE formats;
  macOS never gets 1366x768). Same seed, same fingerprint — reproducible;
  different seeds differ everywhere — no bot-cluster resemblance.
- **HTTP-layer consistency**: `User-Agent` / `Sec-CH-UA*` /
  `Accept-Language` are forced to match the in-page navigator exactly.
- **JS engine coverage**: navigator (UA/platform/UA-CH/brands/webdriver/
  deviceMemory/hardwareConcurrency/languages/plugins), full timezone
  (`Date` semantics + `Intl`), canvas noise (including pure-text canvases),
  client-rects jitter, audio noise, WebGL vendor/renderer + extension list
  (intersected with real Chrome) + shader precision, screen metrics,
  mediaDevices enumeration, AudioContext sample rate/latency, per-platform
  speech voices, fully synthesized Battery API, storage quotas (desktop
  scale, worker scopes too), Geolocation (proxy exit coords + jitter),
  WebRTC ICE exit-IP rewriting.
- **Proxy support**: SOCKS5/HTTP with password auth via a local forwarder,
  upstream-side DNS, and automatic WebRTC IP-policy presetting so the real
  IP never leaks past the proxy.
- **GeoIP alignment**: timezone/language/geolocation/WeBRTC IP derived from
  the proxy exit IP when not set explicitly (queried through the same
  proxy chain).
- **Fingerprint/live-window separation** (CloakBrowser-style):
  `screen` reports the profile, window metrics stay real, and
  `screen.__width/__height` expose the true host window — implemented at
  C++ IDL level in the patched kernel.
- **headless=new artifact masking**: Notification/permissions, hasFocus,
  Web Share, ContentIndex/ContactsManager/downlinkMax, CSS system colors,
  prefers-color-scheme — masked in C++ on the patched kernel and in JS
  elsewhere.
- **Lie-proof wrappers**: non-constructible native-shaped methods, getter
  brand checks, prototype-only accessors, cross-realm toString registry —
  0 hits on CreepJS lie detection.
- **Humanized input** (`humanize`): Bézier mouse paths with overshoot,
  typing cadence with thinking pauses, inertial scrolling — events land
  with `isTrusted: true` via CDP input domain.
- **Metric-compatible font pack** (`fontpack`): embedded woff2
  (Liberation/Carlito/Caladea/Gelasio) registered on demand and hidden from
  `FontFaceSet` enumeration; whitelisted font widths come from real glyphs.
- **TLS fingerprint**: the patched kernel's ClientHello is proven equal to
  the stock binary's (normalized ja3, GREASE and extension-order
  randomization accounted for) — the patch layer never touches the network
  stack.
- **CDP hygiene**: sessions never call `Runtime.enable` (published
  DevTools-detection trick), verified by a console-getter probe test.
- **Fingerprint self-check** (`veilbrowser check`) and one-command kernel
  upgrade with sha256 verification.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│ veilbrowser Python SDK (MIT)                              │
│  profile.py   seed → coherent fingerprint profile         │
│  inject.py    ★ fingerprint engine: JS bundle + UA override│
│  browser.py   launcher (DevTools / cleanup / WebRTC prefs)│
│  proxy.py     local auth forwarder (SOCKS5/HTTP)          │
│  geo.py       proxy-exit GeoIP alignment                  │
│  cdp.py       minimal CDP client                          │
│  probe.py     fingerprint self-check (12 items)           │
│  upgrade.py   one-command kernel upgrade (sha256)         │
│  humanize.py  human-like input (mouse/keys/scroll)        │
│  fontpack.py  metric-compatible font pack (woff2)         │
│  tls.py       ClientHello capture + normalized ja3 diff   │
│  kernel-patches/  19 C++ fingerprint patches for 153      │
├───────────────────────────────────────────────────────────┤
│ Kernel (pluggable)                                        │
│  · vanilla ungoogled-chromium 153 (default, --vanilla)    │
│  · veil-chromium 153 (self-compiled; C++ screen.__width/  │
│    __height, headless masking, system colors, seeded      │
│    canvas/audio/clientRects/fonts)                        │
└───────────────────────────────────────────────────────────┘
```

### Engines

| engine | fingerprint implementation | kernel needed | use case |
|--------|---------------------------|---------------|----------|
| `js` (default) | inject.py bundle + CDP `Network.setUserAgentOverride` | any vanilla Chromium | track latest kernel |
| `kernel` | veil-chromium C++ patches (`js_overlay=True` adds the canvas layer) | veil-chromium 153 | C++-level noise |
| `both` | kernel patches + JS overlay | veil-chromium 153 | **maximum coverage (recommended)** |

## Platform support

**Linux x86_64 only** for now (the kernels and the build pipeline are
Linux-first). macOS and Windows kernels/packaging are on the roadmap —
see [Roadmap](#roadmap-unfinished-work).

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[cdp,dev]"

veilbrowser upgrade --check          # latest community kernel (153.0.8010.52)
veilbrowser upgrade                  # download + sha256 + unpack + configure
veilbrowser check --vanilla --seed 1001 --preset windows-us-office
```

Kernel binary discovery order: env (`VEIL_VANILLA_CHROME_PATH` /
`VEIL_CHROME_PATH`) → `/etc/veilbrowser.conf` or `~/.veilbrowser.conf`
(`vanilla_binary =` / `binary =`) → common-directory globs.

### Python API

```python
from veilbrowser import from_preset, launch

profile = from_preset("windows-us-office", seed=42)
profile.timezone = "Asia/Tokyo"                        # or leave unset and
profile.proxy = "socks5://user:pass@proxy.example.com:1080"  # let GeoIP align it

with launch(profile, headless=True) as browser:        # engine="js" default
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))
```

With a proxy configured, the WebRTC IP policy is preset to
`disable_non_proxied_udp` — the real IP cannot bypass the proxy.

### CLI

```bash
veilbrowser check   --seed 1001 --platform macos --timezone Asia/Tokyo  # self-check report
veilbrowser check   --vanilla --engine js --quiet                       # JSON output
veilbrowser launch  --preset windows-cn-office --seed 42                # start a browser
veilbrowser profiles                                                    # list presets
veilbrowser upgrade [--check] [--version X.Y.Z.W] [--dest DIR]          # kernel upgrade
veilbrowser path                                                        # kernel paths
```

## Fingerprint profiles: coherence is the selling point

Raw seeds only guarantee "random". veilbrowser guarantees "self-consistent":
language and timezone pools are geographically aligned (`ja-JP` never pairs
with New York), CPU core counts follow real-world distributions, screen
resolutions are real per-platform combos, GPU strings are assigned per
platform, and UA ↔ platform ↔ UA-CH ↔ HTTP headers all agree. Seeds are
32-bit integers — the same seed always reproduces the same fingerprint,
different seeds differ in canvas/audio/GPU/screen — avoiding the
"multi-instance fingerprint resemblance" that flags bot clusters.

## Public bot-detection results (v0.7.0 sweep, flagship `engine="both"`)

Self-compiled veil-chromium 153 kernel + JS overlay, `windows-us-office`,
seed 1001, headless:

| Detector | Result |
|---|---|
| [bot.sannysoft.com](https://bot.sannysoft.com/) | **30/30 checks passed, 0 failed** |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **0 lies** · headless **0%** · stealth **0%** · like-headless 6% (dark-mode seeds: 0%) |
| [BrowserScan](https://www.browserscan.net/bot-detection) | "**No bots detected** — the visitor could be a human using a regular browser." |
| [Anti-CAPTCHA score detector](https://antcpt.com/score_detector/) (reCAPTCHA v3) | score **0.9 / 1.0** (≥ 0.7 = fast-captcha tier) |
| [deviceandbrowserinfo.com](https://deviceandbrowserinfo.com/are_you_a_bot) | `"isBot": false`, `"hasBotUserAgent": false` |
| Fingerprint Pro live ([fingerprint.com/github](https://fingerprint.com/github/), [playground](https://demo.fingerprint.com/playground)) | Identified, confidence **0.98**; **Bot / Incognito / DevTools: Not detected** |
| [BroTector](https://ttlns.github.io/brotector/) | **Average 0, zero detection rows** — trusted CDP clicks don't even trip `Input.untrusted` |
| [PixelScan](https://pixelscan.net/bot-check) bot-check | "**You're Definitely a Human**"; Navigator(73)/Webdriver(37)/CDP(2)/UA groups all **Clear** |
| [iphey.com](https://iphey.com/) | HARDWARE / SOFTWARE / LOCATION all "**Everything is fine**" after GeoIP alignment |

Honest caveats, measured on the same sweep:

- Canvas noise is flagged by CreepJS as "rgba noise" and by Fingerprint Pro
  as a Browser-Tampering signal — the inherent cost of noise-based
  spoofing (fingerprint-chromium has the same trade), exchanged for
  cross-instance unlinkability.
- Datacenter exit IPs draw VPN/VM/"ISP risk" flags from Fingerprint Pro
  and iphey regardless of the browser (iphey: Risk 42/medium,
  `Datacenter: true`). Residential proxies are required for
  IP-reputation-sensitive detectors.
- Without a proxy, an exit-IP/timezone mismatch is honestly reported by
  Fingerprint Pro ("VPN: timezone mismatch") and iphey — that is exactly
  what `veilbrowser.geo.align_profile` fixes; production setups should
  configure a proxy and let GeoIP align the profile.
- Pure `engine="kernel"` (no JS overlay) leaves like-headless at 38% —
  the JS engine's environment masking (Web Share/ContentIndex/downlinkMax)
  doesn't participate; use `engine="both"`.
- PixelScan's /fingerprint-check widget never left its "scanning…" state
  from our datacenter network (4 attempts) — environment-unreachable, not
  a detection verdict.

Raw page evidence from the sweep: `/tmp/sitecheck/` on the test machine.

## Tests

```bash
python -m pytest tests/ -q     # 133 tests green (unit + kernel integration)
```

CI (GitHub Actions) runs the unit layer; tests that drive a real Chromium
kernel skip automatically when kernel binaries are absent. Locally, point
the suite at your kernels:

```bash
VEIL_CHROME_PATH=/path/to/veil-chromium/chrome python -m pytest tests/ -q
```

Coverage includes: identity coherence on both kernels, UA-CH, canvas seed
noise + determinism, audio seed dependency, clientRects jitter, WebGL
strings/extensions/precision, timezone (`Date` semantics), plugin shape,
getter native-masking (toString probes), iframe injection coverage,
HTTP-header↔navigator consistency (captured on live targets), screen
metrics, WebRTC presets, full proxy-chain auth, worker-scope spoofing,
GeoIP alignment E2E, Geolocation/WebRTC exit-IP, storage quotas,
kernel+js_overlay, kernel C++ verification (`screen.__width/__height` IDL,
headless masking, ActiveText), TLS ja3 E2E, humanized-input isTrusted E2E,
console-getter silence, and a wrapper/apply-hook regression from the
BroTector finding.

## Building the kernel

This repository ships **patches only** — no Chromium sources or binaries
are committed. To build the patched kernel yourself:

```bash
bash scripts/build-kernel.sh dist/        # ~100 GB disk, ~100 min on 8 cores
```

Or run the `kernel (self-hosted)` GitHub Actions workflow on your own
runner (hosted runners don't have the disk). The script downloads
ungoogled-chromium, prunes it, applies upstream + fingerprint patches,
substitutes domains, and produces a `veil-chromium-*.tar.zst` kernel
tarball.

## Roadmap (unfinished work)

- **Proxy timing signals**: DNS/SSL handshake timing correlation is not
  masked yet.
- **Media-query layout consistency**: CSS layout viewport remains the real
  host size; making it match the profiled screen needs C++-level relayout.
- **Pure `engine="kernel"` residuals**: like-headless 38% (use `both`).
- **macOS / Windows support** (currently Linux x86_64 only).
- **Ecosystem**: Playwright/Puppeteer drop-in API, multi-language client
  UI, Docker/remote-CDP service mode, profile-management GUI.

## Acknowledgements

- [ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
  — the base kernel and tooling.
- [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium)
  — the kernel patch-set ideas our `kernel-patches/` build on.
- [Camoufox](https://github.com/daijro/camoufox) — design reference for
  statistical realism and cross-signal coherence.
- [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) — the
  closed-source benchmark this project measures itself against; **no code
  was taken from it** (its binary license prohibits reverse engineering).
- Validation tools: CreepJS, Fingerprint/BotD, BrowserScan, PixelScan,
  iphey, BroTector, sannysoft, Anti-CAPTCHA, deviceandbrowserinfo.

## License

veilbrowser code: MIT (see [LICENSE](LICENSE)). `kernel-patches/` derives
from fingerprint-chromium (BSD-3-Clause). Vanilla kernels follow
ungoogled-chromium licensing — obtain them through official channels.
