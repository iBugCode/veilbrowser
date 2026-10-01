# Python API reference

Import surface (`from veilbrowser import …`):

```
Browser  CDP  CDPError  DevTools  FingerprintProfile  HumanInput
IdentityMismatch  PRESETS
align_profile  build_script  check  check_exit_ip  collect  default_binary
from_preset  install  js_params  latest_release  launch  locale_for_country
print_report  query_geo  resolve_fingerprint  start  upgrade_kernel
```

## Launching

```python
veilbrowser.launch(profile=None, **options) -> Browser   # blocking launch + self-check
veilbrowser.start(**options) -> Browser                  # alias
```

Key options:

| Option | Meaning |
|---|---|
| `fingerprint` | bare seed (`42`) **or** path to a saved JSON profile |
| `profile` | a `FingerprintProfile` (mutually exclusive with `fingerprint`) |
| `headless` | run headless (kernel masks headless tells) |
| `engine` | `"kernel"` (default) — C++ spoofing; `"js"` — CDP-injected bundle; `"both"` |
| `user_data_dir` | persistent profile dir (identity-bound, see below) |
| `rebind` | allow replacing the identity bound to `user_data_dir` |
| `proxy` | `scheme://[user:pass@]host:port` (WebRTC locked to non-proxied-UDP deny) |

The kernel engine spawns the veil-chromium binary with launch switches
only — no env vars, no injected JS. On launch the SDK runs a self-check
and exposes the result on the `Browser` object:

```python
with launch(from_preset("windows-us-office", seed=42)) as browser:
    browser.kernel_active   # True → C++ spoofing confirmed live
    browser.proxy_check     # exit-IP self-check through the proxy (dict)
```

## Browser & pages

```python
browser.new_page(url="about:blank") -> CDP   # new tab, returns a CDP session
```

`CDP` is a minimal, dependency-free Chrome DevTools Protocol client:

```python
page = browser.new_page("https://example.com")
page.evaluate("navigator.userAgent")                  # sync evaluate
page.evaluate("fetch('/x').then(r=>r.text())", await_promise=True)
page.close()
```

`DevTools` wraps the browser-level endpoint (`/json`): `new_page`,
`new_page_cdp`, `close_page`, target listing.

## Profiles

```python
from veilbrowser import FingerprintProfile, from_preset, PRESETS, resolve_fingerprint

p = FingerprintProfile(seed=42, platform="macos").resolved()  # fill from seed
p = from_preset("windows-us-office", seed=42)                 # curated persona
p.timezone = "Asia/Tokyo"
p.save("acct42.json")                    # persist (same seed = same fingerprint)
q = FingerprintProfile.load("acct42.json")
resolve_fingerprint(42)                  # what a seed/file resolves into
```

Every explicit field and the coherence rules are documented in
[fingerprint profiles](fingerprint.md).

## Self-check / probing

```python
from veilbrowser import collect, check, check_exit_ip, print_report

page = browser.new_page("https://example.com")
results = collect(page)          # gather fingerprint surfaces over CDP
failures = check(results)        # [(name, ok, detail), …] coherence battery
ok = print_report(results)       # human-readable report → bool
check_exit_ip(page, expected_ip) # proxy leak check
```

CLI equivalent: `veilbrowser check --vanilla --seed 1001 --preset windows-us-office`.

## Humanized input

`HumanInput` produces human-like mouse/keyboard sequences for CDP input
dispatch (bezier movement, per-key latencies) — see `veilbrowser/humanize.py`.

## Geo alignment

```python
from veilbrowser import align_profile, locale_for_country, query_geo

align_profile(profile, country="JP")      # align language+timezone to a country
locale_for_country("JP")                  # "ja-JP"
query_geo("1.2.3.4")                      # GeoIP lookup used by the launcher
```

## Kernel upgrades

```python
from veilbrowser import latest_release, upgrade_kernel

latest_release()            # newest kernel release info
upgrade_kernel(dest=…)      # download + sha256 + unpack + configure
```

CLI: `veilbrowser upgrade [--check] [--version X.Y.Z.W] [--dest DIR]`,
`veilbrowser path` prints the detected binary. Discovery order:
`VEIL_VANILLA_CHROME_PATH`/`VEIL_CHROME_PATH` → `/etc/veilbrowser.conf` or
`~/.veilbrowser.conf` → common-directory globs.

## Error types

- `IdentityMismatch` — a persistent `user_data_dir` was launched with a
  different identity than the one bound to it (use `rebind=True` to
  deliberately replace).
- `CDPError` — DevTools protocol transport failures.
