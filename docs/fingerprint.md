# Fingerprint profiles

veilbrowser's identity model has three layers:

1. **Seed** — any 32-bit integer. The same seed always derives the same
   fingerprint; nothing else is stored.
2. **Profile** — explicit overrides (platform, timezone, language, brand, …)
   layered on top of the seed. Unset fields are filled *deterministically*
   from the seed, keeping cross-signal coherence.
3. **Preset** — a curated persona (`windows-us-office`, `macos-jp-creative`,
   …) that pins the realism constraints (real screen combos, geographically
   aligned locale pools, per-platform GPU strings).

Everything ends up as **launch switches** handed to the veil-chromium
kernel — no env vars, no page injection.

## Seeds and presets

```python
from veilbrowser import from_preset, PRESETS

profile = from_preset("windows-us-office", seed=42)   # full persona
PRESETS.keys()                                        # list every persona
```

```bash
veilbrowser profiles                                  # CLI: list presets
veilbrowser check --seed 1001 --platform macos --timezone Asia/Tokyo
```

Preset names follow `<platform>-<locale>-<persona>`. Each preset fixes:

- platform-specific screen resolution pools (real combos only),
- CPU core-count distributions (`hardware_concurrency`),
- GPU vendor/renderer strings per platform,
- language pools whose timezones are geographically aligned
  (`ja-JP` never pairs with a US timezone),
- brand distribution (Chrome / Edge / Opera / Vivaldi with matching
  UA-CH and brand-version strings).

## JSON profile files: save / load / resume

A resolved profile can be saved to a JSON file and reloaded weeks later —
the same file plus the same seed reproduces an identical fingerprint on
any machine and on both engines (kernel and JS-injection).

```bash
# save (fingerprint-save is an alias of profile-save)
veilbrowser fingerprint-save --preset windows-us-office --seed 42 myprofile.json

# load anywhere, any later time
veilbrowser check --fingerprint myprofile.json
veilbrowser launch --fingerprint myprofile.json
```

```python
profile.save("myprofile.json")            # write
profile = FingerprintProfile.load("myprofile.json")   # read

# --fingerprint accepts a bare seed OR a path to a saved file:
from veilbrowser import launch, resolve_fingerprint
launch(fingerprint="myprofile.json")      # file mode
launch(fingerprint=42)                    # seed mode
resolve_fingerprint("myprofile.json")     # inspect what a file resolves to
```

### File schema

`FingerprintProfile.to_dict()` output — every field is optional except
`seed`; unset fields are re-derived from the seed on load, so a saved file
never goes stale when the kernel gains new spoofing surfaces:

| Field | Type | Meaning |
|---|---|---|
| `seed` | int | master identity; drives every derived field |
| `platform` | `windows` \| `macos` \| `linux` | OS persona (None = derive) |
| `platform_version` | str | e.g. `"10.0.0"` (Windows), `"15.2.0"` (macOS) |
| `brand` | str | `Chrome` \| `Edge` \| `Opera` \| `Vivaldi` (None = Chromium) |
| `brand_version` | str | override the brand's default version string |
| `timezone` | str | IANA name, e.g. `Asia/Tokyo` |
| `language` | str | e.g. `en-US`; drives `--lang` and `--accept-lang` |
| `hardware_concurrency` | int | `navigator.hardwareConcurrency` |
| `disable_spoofing` | list | opt out per surface: `font,audio,canvas,clientrects,gpu` |
| `proxy` | str | `scheme://[user:pass@]host:port` |
| `geolocation` | [lat, lon] | `navigator.geolocation` coordinates |
| `webrtc_ip` | str | public IP reported in WebRTC ICE candidates |
| `extra_flags` | list | raw Chromium switches appended verbatim |

## Kernel switch reference

These are the switches the veil-chromium kernel itself consumes
(the Python layer expands profiles into them; you can also pass them
directly when launching the binary by hand):

| Switch | Effect |
|---|---|
| `--fingerprint=<seed>` | master switch — enables C++ spoofing, seeds all derived values |
| `--fingerprint-platform=` | `windows` / `macos` / `linux` persona |
| `--fingerprint-platform-version=` | OS version reported to JS |
| `--fingerprint-brand=` / `--fingerprint-brand-version=` | UA brand + UA-CH |
| `--fingerprint-screen-width/height=` | `screen.width` / `screen.height` |
| `--fingerprint-screen-avail-width/height=` | `screen.availWidth` / `availHeight` |
| `--fingerprint-device-scale-factor=` | `window.devicePixelRatio` |
| `--fingerprint-hardware-concurrency=` | `navigator.hardwareConcurrency` |
| `--fingerprint-timezone=` / `--timezone=` | Intl + Date timezone |
| `--fingerprint-location=lat,lon` | geolocation |
| `--fingerprinting-canvas-image-data-noise=` | deterministic canvas pixel noise level |
| `--fingerprinting-canvas-measure-text-noise=` | `measureText` noise level |
| `--fingerprinting-client-rects-noise=` | `getBoundingClientRect` noise level |
| `--disable-spoofing=font,audio,canvas,clientrects,gpu` | opt out per surface |
| `--lang=` / `--accept-lang=` | navigator.language(s), header alignment |

Switches the launcher adds for every kernel session:
`--no-first-run --no-default-browser-check --disable-sync`
(+ `--no-sandbox` when running as root, `--disable-dev-shm-usage` in
containers). WebRTC defaults to `disable_non_proxied_udp` whenever a proxy
is configured, so the real IP cannot bypass the proxy.

## Coherence rules (why profiles, not random values)

Raw random values are *worse* than a real fingerprint: they disagree with
each other and every detector cross-checks. veilbrowser enforces:

- **UA ↔ platform ↔ UA-CH ↔ HTTP headers** all agree (one source of truth
  in the kernel).
- **Language ↔ timezone** pools are geographically aligned.
- **Screen**: real per-platform resolution/avail/DPR combos — never
  `1920×1080` with `availHeight=300`.
- **CPU cores**: sampled from real-world distributions, not uniform noise.
- **GPU strings**: per-platform assignment, WebGL renderer/params and
  `WEBGL_debug_renderer_info` stay consistent with the persona.
- **Fonts**: metric-compatible font metrics are embedded in the kernel
  binary, so a "windows" persona on a Linux host measures Windows font
  metrics (seed-consistent, not host-consistent).

Deterministic noise (canvas, client rects, measureText) is seeded from the
same seed: re-running the seed reproduces bit-identical noise, which is
what session-replay and continuity checks require.

## Identity-bound profile dirs

Persistent profile directories are identity-bound: the first launch into a
`user_data_dir` stores the resolved identity (`veil-identity.json` inside
the dir); a later launch with a *different* seed or persona fails with
`IdentityMismatch` instead of silently becoming a different device.

```python
veilbrowser.launch(profile, user_data_dir="/srv/profiles/acct-42", rebind=True)
```

`rebind=True` is the deliberate "replace this identity" escape hatch.
