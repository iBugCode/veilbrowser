<div align="center">
  <img src="assets/logo.svg" width="140" alt="veilbrowser logo"/>
  <h1>veilbrowser</h1>
  <p><a href="README.md">English</a> | <a href="README.zh-CN.md">中文</a> | <a href="README.ja.md">日本語</a></p>
  <p>
    <a href="https://github.com/iBugCode/veilbrowser/actions/workflows/ci.yml"><img src="https://github.com/iBugCode/veilbrowser/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
    <img src="https://img.shields.io/badge/platform-linux%20x86__64-blue" alt="Platform"/>
    <img src="https://img.shields.io/badge/python-3.10%2B-informational" alt="Python"/>
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License"/>
  </p>
</div>

An open-source fingerprint (anti-detect) browser SDK for automation, aiming
to be **the best open-source fingerprint browser**. It ships a dual-engine
design:

- **JS engine** — a CDP-injected JS bundle that runs on any vanilla
  [ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
  kernel (upgrading kernels is just downloading a new package), and
- **C++ kernel** (since v0.7.0) — the self-compiled **veil-chromium** kernel:
  ungoogled-chromium 153 + C++ fingerprint patches, a full
  ninja/ThinLTO official build whose TLS layer is byte-for-byte
  ja3-verified against the stock binary.

Since **v0.9.0** the kernel goes all the way: **every fingerprint surface
lives in Blink C++** — no JavaScript engine at all. Launching the executable
with launch switches (`--fingerprint=… --fingerprint-platform=windows …`)
yields the full fingerprint: identity, media queries, Windows font metrics
(metric-compatible fonts embedded in the binary), speech voices, media
devices, storage quota, audio sample rate and WebGL limits. Nothing is
injected, nothing runs in the page that isn't the browser itself.

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
- **Pure C++ engine** (`engine="kernel"`, v0.9.0): the whole fingerprint is
  Blink C++ patches (`kernel-patches/extra/fingerprint/022-030`) driven by
  launch switches only — media-query/screen consistency, embedded
  metric-compatible fonts (Carlito/Caladea/Gelasio/Liberation), desktop
  speech voices (host voices replaced, not just filled), media-device
  synthesis, desktop storage quota, 48 kHz audio, GPU-plausible WebGL
  limits, seed-derived `navigator.connection` quality (never the headless
  `rtt=0 / downlink=10` tell), `getBBox()` sharing the DOMRect offset, and
  per-seed `navigator.bluetooth.getAvailability()`. Zero JS runs that isn't
  the browser's own.
- **Identity binding**: a persistent `user_data_dir` records its resolved
  identity (`veil-identity.json`) on first launch; relaunching it with a
  different seed/platform/timezone raises `IdentityMismatch` instead of
  silently drifting the account's fingerprint. `rebind=True` (or
  `veilbrowser launch --rebind`) replaces it deliberately.
- **Saved profiles**: `profile.save(path)` / `FingerprintProfile.load(path)`
  (and `veilbrowser profile-save`) — same file + same seed reproduces the
  same fingerprint for session reuse weeks later. One argument covers both
  forms: `--fingerprint=42` reuses seed 42, `--fingerprint=myprofile.json`
  reloads a saved identity (`veilbrowser fingerprint-save` writes one;
  `launch(fingerprint=...)` in the API).
- **Proxy exit-IP self-check**: after launch with a proxy, the browser
  fetches its public IP through its own network stack and compares it with
  the externally measured proxy exit — catching the silent direct-connection
  fallback users hit with authenticated SOCKS5 (`browser.proxy_check`).
- **CDP hygiene**: sessions never call `Runtime.enable` (published
  DevTools-detection trick), verified by a console-getter probe test.
- **DevTools/CDP invisibility** (kernel patches 031–033, v0.10.21): pressing
  F12 — which always opens **undocked**, so the page's geometry never
  changes — or attaching any CDP client is unobservable from page JS. The
  `debugger` statement never pauses (kills timing probes), console and
  exception delivery never build previews (getter-fire probes see nothing,
  where stock Chrome fires them), while the DevTools Console, breakpoints
  and pause-on-exception stay fully usable. See
  [docs/anti-detection.md](docs/anti-detection.md).
- **Normal-install extensions** (`--extension_paths=dir1,dir2`, kernel
  patch 027): installs unpacked extensions into the profile exactly like the
  chrome://extensions "Load unpacked" flow — `chrome.runtime.onInstalled`
  fires with reason `"install"` only the **first** launch into a profile,
  reason `"update"` only when the on-disk version changes, and restarts load
  it from prefs with **no event**.  `--load-extension` instead re-runs the
  whole install flow on every launch, so its `onInstalled` fires every time
  (a loud automation tell).  Installations via this switch are also immune
  to the developer-mode disable gate: the flow flips the (persistent)
  developer-mode pref before policy evaluation and re-arms on every launch —
  no expiry, no silent disabling.
- **Fingerprint self-check** (`veilbrowser check`) and one-command kernel
  upgrade with sha256 verification.

## Documentation

| Document | Contents |
|---|---|
| [Fingerprint profiles](docs/fingerprint.md) | seeds, presets, JSON profile files, save/load, every kernel switch |
| [Anti-detection coverage](docs/anti-detection.md) | spoofed surfaces, DevTools/CDP invisibility, honest limitations |
| [Kernel build & CI](docs/kernel.md) | building veil-chromium, patch layout, release pipeline |
| [Python API](docs/python-api.md) | launch / Browser / CDP / profiles / probing reference |

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
| `kernel` (default, recommended) | veil-chromium C++ patches driven by launch switches — **no JS, no injection** | veil-chromium ≥ v0.9.0 | **pure-engine stealth** |
| `js` (legacy) | inject.py bundle + CDP `Network.setUserAgentOverride` | any vanilla Chromium | track latest kernel without rebuilding |
| `both` (legacy) | kernel patches + JS overlay | veil-chromium 153 | maximum coverage on a shared kernel |
| `native` | deprecated alias of `kernel` (the v0.8 compiled-in bundle was retired) | — | back-compat |

## Platform support

The wrapper is cross-platform (Python). Kernel packages: **linux-x64** is
battle-tested; **win-x64** is built automatically on every release by CI
(newer, less real-world mileage); macOS is on the roadmap —
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

with launch(profile, headless=True) as browser:        # engine="kernel" default
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))
    print(browser.kernel_active)      # True: C++ spoofing confirmed live
    print(browser.proxy_check)        # exit-IP self-check through the proxy

# The kernel reads launch switches only — no env vars, no injection:
#   launch(profile, engine="kernel") spawns, e.g.,
#   chrome --fingerprint=1001 --fingerprint-platform=windows
#          --fingerprint-screen-width=1920 --timezone=America/New_York ...
```

Persistence — reuse an identity across sessions:

```bash
veilbrowser fingerprint-save --preset windows-us-office --seed 42 myprofile.json
veilbrowser check --fingerprint myprofile.json   # or --fingerprint 42 for the bare seed
```

With a proxy configured, the WebRTC IP policy is preset to
`disable_non_proxied_udp` — the real IP cannot bypass the proxy.

Persistent profile dirs are also **identity-bound**: the first launch into a
`user_data_dir` stores the resolved identity, and a later launch with a
different seed or persona fails with `IdentityMismatch` rather than quietly
becoming a different device. Deliberate replacement:

```python
veilbrowser.launch(profile, user_data_dir="/srv/profiles/acct-42", rebind=True)
```

### Honest notes

- Rejection-rate spikes on reCAPTCHA/Cloudflare are usually **industry-wide
  events** (FingerprintJS agent updates, Google-side rollouts), not a
  regression of one browser build — check public detector sites before
  blaming a version bump.
- `navigator.connection` reports seed-derived plausible values. That is a
  deliberate trade: real measurements from a headless/proxied host would be
  the `rtt=0 / downlink=10` unknown-network tell.
- `Math.tanh`-style floating-point fingerprints identify the *build
  architecture* of the kernel binary. One shipped binary has one behavior;
  a windows persona on an unusual host CPU cannot bit-match every Windows
  Chrome build. Accepted residual, under evaluation.

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

### v0.9.0 pure C++ kernel: zero JS, zero injection

Flagship profile (`windows-us-office`, seed 1001, headless) with
`engine="kernel"` — the process is launched with switches only; **no JS
exists in the page beyond the browser's own** (`typeof veilNativeCfg ===
"undefined"`, `typeof __veil_installed === "undefined"`):

| Detector | pure-kernel result |
|---|---|
| [bot.sannysoft.com](https://bot.sannysoft.com/) | **57/57 rows passed, 0 failed** |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **0 lies** · headless **0%** · stealth **0%** (like-headless 38% — soft env classification, see roadmap) |
| [deviceandbrowserinfo.com](https://deviceandbrowserinfo.com/are_you_a_bot) | `"isBot": false` |
| [BroTector](https://ttlns.github.io/brotector/) | **Average 0, zero detection rows** (trusted humanized click) |

Font metrics on a fontless Linux host now match real Windows Chrome exactly
(measureText `mmmmmmmmmmlli` @72px): Arial 647.75, Calibri 624.73, Cambria
644.33, Times New Roman 620.05, Courier New 561.69, Georgia 696.52 — served
from metric-compatible fonts embedded in the binary.

Evidence: `/tmp/kernel_e2e/` on the test machine.

## Tests

```bash
python -m pytest tests/ -q     # 144 tests green (unit + kernel integration)
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

**Prebuilt kernels ship on every release.** A `v*` tag triggers GitHub
Actions builds of the patched kernel for **linux-x64** (`veil-chromium-*-linux-x64.tar.zst`)
and **win-x64** (`veil-chromium-*-win-x64.zip`) on hosted runners —
`symbol_level=0`, no PGO, no ThinLTO to fit the 4-core/6-hour CI limits.
The thin self-hosted build below stays the release-grade path.

This repository ships **patches only** — no Chromium sources or binaries
are committed. To build the patched kernel yourself:

```bash
bash scripts/build-kernel.sh dist/        # ~100 GB disk, ~100 min on 8 cores (ThinLTO)
VEIL_THINLTO=0 bash scripts/build-kernel.sh dist/   # faster, CI-grade
```

Windows x64: clone
[ungoogled-chromium-windows](https://github.com/ungoogled-software/ungoogled-chromium-windows)
at the tag matching the Chromium version and drive it with
`scripts/build-kernel-windows.py` (see the script header; same flow the
`kernel` GitHub Actions workflow runs).

The script downloads the hash-verified chromium-lite tarball, prunes it,
applies upstream + fingerprint patches, substitutes domains, bootstraps the
pinned clang/rust/gn toolchain (no depot_tools), and produces a
`veil-chromium-*.tar.zst` kernel tarball. Patches 022–026 are generated from
a build tree (`scripts/gen_kernel_patches.py`); 028–030 are maintained by hand.

## Roadmap (unfinished work)

- **Proxy timing signals**: DNS/SSL handshake timing correlation is not
  masked yet.
- **CreepJS like-headless residual (38%)**: zero lies, 0% headless, 0%
  stealth, but CreepJS's soft environment classification still weights the
  profile as headless-*like* (datacenter host + SwiftShader env cues).
  `engine="both"` scores 6% if you need that last soft point.
- **Media-query layout viewport**: pointer/hover/device-size/DPR queries are
  now consistent with the profile (C++), but the CSS *layout* viewport
  remains the real host window size; relayout to the profiled screen needs
  deeper C++ work.
- **Android profiles** (CloakBrowser #533-style request): needs kernel
  platform switches plus mobile GPU/screen pools.
- **macOS support** (linux-x64 and win-x64 kernels now ship from CI; the
  win-x64 kernel is new and less battle-tested than the Linux one).
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
