# Anti-detection coverage

This page is the honest map of what veil-chromium hides, how, and what it
does **not** hide. Every claim in the "status" columns is enforced in C++
inside the kernel — no page-visible JavaScript participates in spoofing.

## Spoofed fingerprint surfaces (kernel patches 000–030)

| Surface | JS observable | Implementation |
|---|---|---|
| `navigator.userAgent`, UA-CH, brand/version | yes | platform/brand switches, single source of truth |
| `screen.*`, `window.devicePixelRatio` | yes | `--fingerprint-screen-*` switches |
| `navigator.hardwareConcurrency` | yes | seeded from real-world core-count pools |
| Intl / `Date` timezone | yes | ICU override from `--timezone` |
| Canvas 2D pixels (`getImageData`, `toDataURL`) | yes | deterministic seed-derived noise |
| `measureText`, client rects, SVG bbox | yes | deterministic noise |
| WebGL vendor/renderer + limits | yes | per-platform GPU strings, consistent limits |
| Audio (AudioContext) fingerprint | yes | seed-derived consistent values |
| Fonts (metric embedding) | yes | kernel binary carries metric-compatible metrics |
| `navigator.webdriver` | yes | always `false` (patch 009) |
| Headless environment tells (`document.hasFocus`, notification/permission "denied by default", focus masking) | yes | patches 010/020 — headless sessions report headful values |
| Speech voices, media devices, storage quota, network quality, bluetooth availability | yes | seeded plausible values, cross-consistent |

## DevTools & CDP invisibility (patches 031–032, since v0.10.21)

Goal: **opening DevTools with F12, or attaching any CDP client, leaves no
trace beyond what stock Chrome shows for any human user** — at any time,
headful or headless.

### How each known vector is closed

| Vector | Detector pattern | Status |
|---|---|---|
| `debugger` statement timing | `const t=now(); debugger; if (now()-t>100) report()` — pauses only while a debugger is attached | **closed** (031): the statement never pauses; breakpoints and the DevTools pause button still work |
| Console serialization getters | `console.log(objWithGetter)` fires the getter while DevTools/CDP is attached (preview building) | **closed** (001 + 032): object previews are never generated for protocol delivery — DevTools shows `Object` and invokes getters only when *you* click to expand |
| Uncaught-exception previews | `throw objWithGetter` fires getters when CDP delivers `Runtime.exceptionThrown` | **closed** (032): exceptions are delivered (F12 shows them again) but never previewed |
| `console.table` getter sweep | `console.table([{get id(){…}}])` | **closed**: table path never invokes page getters |
| Window geometry delta | docked DevTools shrinks the viewport → `resize` event, `innerWidth` drop, `outerWidth-innerWidth` gap | **stock behavior** (033 removed, v0.10.28): DevTools docks **inside the browser window** exactly as in stock Chrome (default bottom, per-profile dock preference, undock via the ⋮ menu) — the geometry delta is identical to what any real user opening DevTools produces, so it is not a bot signal; fingerprint screen values are constant anyway |
| `navigator.webdriver` | automation flag | **closed** (009): always `false` |
| F12 keypress reaching the page | `keydown` listeners | n/a: F12 is consumed by the browser and never dispatched to page JS |
| Console-only globals (`getEventListeners`, `$0`, `__commandLineAPI`) | `'getEventListeners' in window` | n/a: these exist only in console evaluation contexts, never in the page realm |
| `Runtime.enable` side effects (console replay, bindings) | getters firing during message replay | **closed**: replay and live delivery are preview-free; `Runtime.addBinding` is a no-op |

### What the F12 experience looks like

DevTools opens **docked inside the browser window** — stock Chrome's default
(bottom edge); the per-profile dock preference and the undock option in the
panel's ⋮ menu work as usual — and is fully usable: Elements,
Sources, breakpoints, pause-on-exception, stepping, Network, Performance.
The Console panel shows messages and uncaught exceptions normally; object
values render as expandable placeholders (`Object`, `[…]`) instead of
eagerly-computed inline previews — expanding one invokes getters at
click-time only, which passive detector scripts cannot trigger.

### Verification methodology

Probes drive a real CDP session against the built kernel
(`--headless=new --remote-debugging-port=…`) and check for page-visible
side effects:

```
webdriver = false
getter fired with Runtime.enable live        : 0   (stock fires 1+)
getter fired on Runtime.enable replay        : 0
getter fired via Console.enable              : 0
getter fired via console.table               : 0
getter fired via uncaught exception          : 0
debugger statement pause with Debugger.enable: no (0 ms; stock ~2500 ms hang)
Runtime.consoleAPICalled live delivery       : yes (F12 console works)
Symbol.toStringTag on logged objects         : fires identically with AND
                                               without any CDP client — a
                                               constant signal, useless to
                                               detectors
```

### Honest limitations

- **Focus/blur**: clicking into the docked DevTools panel moves focus away
  from the page, so `window.onblur` fires — exactly as it does in stock
  Chrome for any user. This is a generic focus signal, not a
  DevTools-specific one, and `document.hasFocus()`
  stays truthful. Masking focus would break ordinary web behavior.
- **Element hover**: hovering nodes in the Elements panel can trigger
  `:hover`-dependent styles on the inspected element, observable via
  `getComputedStyle` at that instant.
- **Source maps**: DevTools fetching `.map` files is visible to the *server*
  (request-level correlation), not to page JS.
- **The client's own choices**: CDP *usage* itself is invisible
  (`Runtime.evaluate`, screenshots, input events are trusted). But an
  automation client can *choose* page-visible changes — e.g.
  `Emulation.setUserAgentOverride` or `Page.addScriptToEvaluateOnNewDocument`.
  Those are the client's scripts by design, not a kernel leak.
- **Timing noise**: message delivery serializes small arguments
  (id-only, no getters) synchronously; microsecond-scale differences exist
  but no published detector keys on them, and stock Chrome does more work
  in the same paths.

## Proxy / network layer

- WebRTC: with a proxy configured, ICE policy is `disable_non_proxied_udp`
  — the real IP cannot leak around the proxy.
- TLS: the kernel's TLS stack is byte-for-byte ja3-consistent with the
  stock Chromium build (verified in CI).
- `navigator.connection` reports seed-derived plausible values (a real
  measurement from a headless/proxied host would be the `rtt=0` tell).

## Detector suites

`veilbrowser check` runs a self-check battery (kernel-active confirmation,
UA/UA-CH consistency, screen coherence, canvas/WebGL/audio noise, proxy
exit IP) and prints a pass/fail report — use it after any kernel or
profile change:

```bash
veilbrowser check --vanilla --seed 1001 --preset windows-us-office
```
