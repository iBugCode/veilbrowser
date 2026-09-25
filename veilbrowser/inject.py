"""Kernel-independent fingerprint engine.

Instead of relying on fingerprint-chromium's C++ patches (which pin us to
whatever kernel the upstream maintainer has released), the fingerprint lives
in a JS bundle injected into every frame at document-start via CDP
``Page.addScriptToEvaluateOnNewDocument``.  Any vanilla Chromium/ungoogled
kernel becomes a fingerprint browser; upgrading the kernel is just a
download.

Surfaces covered (all parameterised by the profile seed so two sessions with
different seeds never agree, and the same seed is bit-stable):

  * navigator UA / platform / userAgentData + getHighEntropyValues
  * webdriver removal, plugins/mimeTypes (PDF viewer set), deviceMemory,
    hardwareConcurrency, languages
  * timezone: Date.getTimezoneOffset / toString / toLocale*String,
    Intl.DateTimeFormat construction + resolvedOptions, local component
    getters and the local-parsing Date constructor
  * canvas: getImageData + toDataURL/toBlob noise, measureText jitter
  * client rects: getBoundingClientRect/getClientRects micro-jitter
  * audio: OfflineAudioContext.startRendering + AnalyserNode buffers
  * webgl: getParameter vendor/renderer strings (+ readPixels noise)
  * automation conveniences: fakeShadowRoot, Headless UA scrub
"""

from __future__ import annotations

import json

from .profile import FingerprintProfile

# ---------------------------------------------------------------- params ---

# System fonts each platform is allowed to report as installed. Anything
# outside the list measures/ checks as "not installed" so the Linux host's
# font set (DejaVu etc.) never leaks through width probing.
_FONT_POOLS: dict[str, tuple[str, ...]] = {
    "windows": (
        "Arial", "Arial Black", "Calibri", "Cambria", "Comic Sans MS",
        "Consolas", "Courier New", "Georgia", "Impact", "Lucida Console",
        "Palatino Linotype", "Segoe UI", "Segoe UI Symbol", "Tahoma",
        "Times New Roman", "Trebuchet MS", "Verdana", "Webdings", "Wingdings",
    ),
    "macos": (
        "American Typewriter", "Andale Mono", "Arial", "Avenir", "Avenir Next",
        "Courier New", "Geneva", "Georgia", "Helvetica", "Helvetica Neue",
        "Menlo", "Monaco", "Optima", "Palatino", "SF Pro Text", "Tahoma",
        "Times New Roman", "Trebuchet MS", "Verdana",
    ),
    "linux": (
        "DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif", "FreeMono",
        "FreeSans", "Liberation Mono", "Liberation Sans", "Liberation Serif",
        "Noto Sans", "Noto Serif", "Ubuntu", "Ubuntu Mono",
    ),
}

_GENERIC_FAMILIES = (
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-serif", "ui-sans-serif", "ui-monospace", "math", "fangsong", "emoji",
)

# Platform-plausible speechSynthesis voices. Chrome adds its own "Google …"
# network voices on every OS; the others follow what the OS ships. A voice
# matching the profile language is appended so de-DE sessions don't speak
# with an English-only voice set.
_VOICE_BASES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "windows": (
        ("Microsoft David - English (United States)", "en-US", True),
        ("Microsoft Zira - English (United States)", "en-US", True),
        ("Microsoft Sonia - English (United Kingdom)", "en-GB", False),
        ("Google US English", "en-US", False),
    ),
    "macos": (
        ("Samantha", "en-US", True),
        ("Alex", "en-US", True),
        ("Karen", "en-AU", False),
        ("Daniel", "en-GB", False),
        ("Moira", "en-IE", False),
        ("Tessa", "en-ZA", False),
        ("Google US English", "en-US", False),
    ),
    "linux": (
        ("eSpeak NG English (Great Britain)", "en-GB", True),
        ("eSpeak NG English (United States)", "en-US", True),
    ),
}

_VOICE_LANG_MATCHES: dict[str, tuple[str, str, bool]] = {
    "zh-CN": ("Microsoft Huihui - Chinese (Simplified, PRC)", "zh-CN", True),
    "zh-TW": ("Microsoft Hanhan - Chinese (Traditional, Taiwan)", "zh-TW", True),
    "ja-JP": ("Microsoft Haruka - Japanese (Japan)", "ja-JP", True),
    "ko-KR": ("Microsoft Heami - Korean (Korea)", "ko-KR", True),
    "de-DE": ("Microsoft Hedda - German (Germany)", "de-DE", True),
    "fr-FR": ("Microsoft Hortense - French (France)", "fr-FR", True),
    "es-ES": ("Microsoft Helena - Spanish (Spain)", "es-ES", True),
    "it-IT": ("Microsoft Elsa - Italian (Italy)", "it-IT", True),
    "ru-RU": ("Microsoft Irina - Russian (Russia)", "ru-RU", True),
    "pt-BR": ("Microsoft Maria - Portuguese (Brazil)", "pt-BR", True),
    "en-IN": ("Microsoft Heera - English (India)", "en-IN", True),
}


def _ua_strings(platform: str, chrome_full: str, brand: str) -> tuple[str, str, str]:
    """(userAgent, navigator.platform, UA-CH platform) for a spoofed OS."""
    tok = {
        "Chrome": f"Chrome/{chrome_full}",
        "Chromium": f"Chrome/{chrome_full}",
        "Edge": f"Chrome/{chrome_full} Edg/{chrome_full}",
        "Vivaldi": f"Chrome/{chrome_full}",
        "Opera": f"Chrome/{chrome_full} OPR/1.0.0",
    }.get(brand, f"Chrome/{chrome_full}")
    base = f"Mozilla/5.0 ({{os}}) AppleWebKit/537.36 (KHTML, like Gecko) {tok} Safari/537.36"
    if platform == "windows":
        return base.format(os="Windows NT 10.0; Win64; x64"), "Win32", "Windows"
    if platform == "macos":
        return base.format(os="Macintosh; Intel Mac OS X 10_15_7"), "MacIntel", "macOS"
    return base.format(os="X11; Linux x86_64"), "Linux x86_64", "Linux"


_PLATFORM_VERSIONS = {
    "windows": ("10.0.0", "15.0.0", "13.0.0", "11.0.0"),
    "macos": ("10.15.7", "14.7.1", "15.3.1"),
    "linux": ("6.1.0", "6.12.0", "5.15.0"),
}

# GPU strings are ANGLE-serialized and the format differs per OS: Windows
# reports Direct3D11, macOS the Metal renderer, Linux Mesa/OpenGL — a Mesa
# string next to a Win32 navigator.platform is an immediate contradiction.
_GPU_POOLS: dict[str, tuple[tuple[str, str], ...]] = {
    "windows": (
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00003EA0) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x00009A49) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 (0x00001F82) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics (0x0000164E) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ),
    "macos": (
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Intel(R) Iris(TM) Plus Graphics 655, Unspecified Version)"),
    ),
    "linux": (
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics (CML GT2), OpenGL 4.6)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, Mesa Radeon(R) Graphics (Renoir), OpenGL 4.6)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, Mesa NVIDIA RTX 3060/PCIe/SSE2, OpenGL 4.6)"),
    ),
}

_SCREENS = {
    "windows": ((1920, 1080), (2560, 1440), (1366, 768), (1680, 1050), (3840, 2160)),
    "macos": ((1440, 900), (1536, 960), (1680, 1050), (1710, 1112)),
    "linux": ((1920, 1080), (1600, 900), (2560, 1440), (1366, 768)),
}


def js_params(profile: FingerprintProfile, chrome_full: str | None = None) -> dict:
    """Everything the JS bundle needs, derived deterministically from profile."""
    import random
    import hashlib

    def rng(*salt: str) -> random.Random:
        h = hashlib.sha256(f"{profile.seed}:{'|'.join(salt)}".encode()).digest()
        return random.Random(int.from_bytes(h, "big"))

    resolved = profile.resolved()
    chrome_full = chrome_full or resolved.brand_version or "153.0.8010.52"
    brand_name = {
        "Chrome": "Google Chrome", "Chromium": "Chromium", "Edge": "Microsoft Edge",
        "Vivaldi": "Vivaldi", "Opera": "Opera",
    }.get(resolved.brand, "Google Chrome")
    ua, nav_platform, uach_platform = _ua_strings(
        resolved.platform, chrome_full, resolved.brand)

    r_ver = rng("platformVersion")
    platform_version = r_ver.choice(_PLATFORM_VERSIONS[resolved.platform])

    greases = [("8", "8.0.0.0"), ("1", "1.2.3.4"), ("24", "24.1.2.3"), ("10", "10.9.8.7")]
    r_br = rng("brands")
    g1 = r_br.choice(greases)
    g2 = r_br.choice([g for g in greases if g != g1])
    major = chrome_full.split(".")[0]
    brands = [
        {"brand": f"Not.A/Brand;{g1[0]}", "version": g1[1]},
        {"brand": f"{brand_name};{major}", "version": chrome_full},
        {"brand": f"Not?A_Brand;{g2[0]}", "version": g2[1]},
    ]
    full_list = [{"brand": f"{brand_name};{major}", "version": chrome_full}]

    r_gl = rng("webgl")
    gpu = r_gl.choice(_GPU_POOLS[resolved.platform])

    r_scr = rng("screen")
    sw, sh = r_scr.choice(_SCREENS[resolved.platform])
    taskbar = r_scr.choice((40, 48, 60, 72)) if resolved.platform == "windows" \
        else r_scr.choice((24, 25, 38))
    # Screen follows the fingerprint; window metrics stay real (media queries
    # read the true viewport). The real host window size reaches the operator
    # via screen.__width/__height — CloakBrowser-style.
    screen = {"w": sw, "h": sh, "availW": sw, "availH": sh - taskbar,
              "cd": r_scr.choice((24, 30)),
              "dpr": 2 if resolved.platform == "macos" else 1}

    r_dev = rng("devices")
    devices = []
    for _ in range(r_dev.choice((1, 1, 2))):          # microphones
        devices.append({"kind": "audioinput", "label": "", "deviceId": "",
                        "groupId": ""})
    if r_dev.random() < 0.75:                          # webcam
        devices.append({"kind": "videoinput", "label": "", "deviceId": "",
                        "groupId": ""})
    devices.append({"kind": "audiooutput", "label": "", "deviceId": "",
                    "groupId": ""})

    r_rate = rng("audioctx")
    sample_rate = r_rate.choice((44100, 48000, 48000))
    base_latency = round(512 / sample_rate, 6)
    output_latency = round(base_latency + r_rate.choice((0.01, 0.013, 0.02, 0.033)), 6)

    r_sto = rng("storage")
    storage = {"usage": r_sto.randint(2_000_000, 40_000_000),
               "quota": r_sto.randint(120, 480) * 1024 ** 3}

    if resolved.geolocation:
        r_geo = rng("geolocation")
        geolocation = {
            "lat": round(resolved.geolocation[0] + r_geo.uniform(-0.02, 0.02), 6),
            "lon": round(resolved.geolocation[1] + r_geo.uniform(-0.02, 0.02), 6),
            "accuracy": round(r_geo.uniform(20, 150), 1),
        }
    else:
        geolocation = None

    r_bat = rng("battery")
    charging = r_bat.random() < 0.5
    level = round(r_bat.uniform(0.15, 1.0), 2)
    if charging:
        charging_time = 0 if level >= 1.0 else int(r_bat.uniform(600, 10800))
        discharging_time = float("inf")
    else:
        charging_time = float("inf")
        discharging_time = int(level * r_bat.uniform(9000, 30000))
    battery = {"charging": charging, "level": level,
               "chargingTime": charging_time, "dischargingTime": discharging_time}

    r_vo = rng("voices")
    voice_tuples = list(_VOICE_BASES[resolved.platform])
    lang_voice = _VOICE_LANG_MATCHES.get(resolved.language)
    if lang_voice and lang_voice not in voice_tuples:
        voice_tuples.append(lang_voice)
    speech_voices = [{"voiceURI": name, "name": name, "lang": lang,
                      "localService": local, "default": i == 0}
                     for i, (name, lang, local) in enumerate(voice_tuples)]

    return {
        "seed": resolved.seed,
        "platform": resolved.platform,
        "userAgent": ua,
        "navPlatform": nav_platform,
        "uachPlatform": uach_platform,
        "platformVersion": platform_version,
        "chromeFull": chrome_full,
        "brands": brands,
        "fullVersionList": full_list,
        "languages": [resolved.language, resolved.language.split("-")[0]],
        "acceptLanguage": resolved.accept_language(),
        "timezone": resolved.timezone,
        "timezoneName": _tz_display_name(resolved.timezone),
        "hardwareConcurrency": resolved.hardware_concurrency,
        "deviceMemory": r_gl.choice([8, 8, 16, 32]),
        "webglVendor": gpu[0],
        "webglRenderer": gpu[1],
        "screen": screen,
        "mediaDevices": devices,
        "audioSampleRate": sample_rate,
        "audioBaseLatency": base_latency,
        "audioOutputLatency": output_latency,
        "battery": battery,
        "speechVoices": speech_voices,
        "storage": storage,
        "geolocation": geolocation,
        "webrtcIp": resolved.webrtc_ip,
        "fonts": list(_FONT_POOLS[resolved.platform]),
        "userAgentMetadata": {
            "brands": [{"brand": b["brand"].split(";")[0], "version": b["version"]}
                       for b in brands],
            "fullVersionList": [{"brand": f["brand"].split(";")[0],
                                 "version": f["version"]} for f in full_list],
            "fullVersion": chrome_full,
            "platform": uach_platform,
            "platformVersion": platform_version,
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "mobile": False,
            "wow64": False,
        },
        "spoof": {
            "ua": True, "tz": True, "canvas": True, "rects": True,
            "audio": True, "webgl": True, "plugins": True, "shadow": True,
            "screen": True, "media": True, "worker": True, "fonts": True,
            "env": True,
            "audioRate": True, "battery": True, "speech": True, "storage": True,
        },
        "automation": True,
    }


def _tz_display_name(tz: str) -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)).tzname() or tz
    except Exception:
        return tz


# ---------------------------------------------------------------- script ---

_SCRIPT_TEMPLATE = r"""
(() => {
  const scope = typeof window !== 'undefined' ? window : self;
  const isWorker = typeof window === 'undefined';
  const cfg = __VEIL_CFG__;
  if (!cfg || scope.__veil_installed) return;
  try { Object.defineProperty(scope, '__veil_installed', {value: true, enumerable: false}); } catch (e) {}

  // ---- helpers -----------------------------------------------------------
  function hash32(str, seed) {
    let h = 2166136261 ^ (seed >>> 0);
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function prng(seed) {
    let s = (seed >>> 0) || 0x9e3779b9;
    return function () { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
  }
  const nativeFns = new Map();
  const origFnToString = Function.prototype.toString;
  function markNative(fn, name) {
    nativeFns.set(fn, name || fn.name);
    try { Object.defineProperty(fn, 'name', {value: name || fn.name, configurable: true}); } catch (e) {}
    return fn;
  }
  Function.prototype.toString = markNative(function toString() {
    if (nativeFns.has(this)) return 'function ' + nativeFns.get(this) + '() { [native code] }';
    return origFnToString.call(this);
  }, 'toString');
  function redefine(obj, prop, getter, setter) {
    let enumerable = true, have = null;
    try { have = Object.getOwnPropertyDescriptor(obj, prop); } catch (e) {}
    if (have) enumerable = have.enumerable;
    try {
      Object.defineProperty(obj, prop, {
        get: markNative(getter, 'get ' + prop),
        set: setter, configurable: true, enumerable: enumerable,
      });
    } catch (e) {}
  }
  // for methods: a plain (writable, configurable) value property
  function redefFn(obj, prop, fn) {
    let enumerable = true, have = null;
    try { have = Object.getOwnPropertyDescriptor(obj, prop); } catch (e) {}
    if (have) enumerable = have.enumerable;
    try {
      Object.defineProperty(obj, prop,
        {value: fn, writable: true, configurable: true, enumerable: enumerable});
    } catch (e) {}
  }

  // ---- navigator ---------------------------------------------------------
  if (cfg.spoof.ua) {
    // window: Navigator.prototype — worker: WorkerNavigator.prototype
    const proto = Object.getPrototypeOf(navigator);
    redefine(navigator, 'userAgent', () => cfg.userAgent);
    redefine(proto, 'userAgent', () => cfg.userAgent);
    redefine(navigator, 'appVersion', () => cfg.userAgent.replace(/^Mozilla\//, ''));
    redefine(proto, 'appVersion', () => cfg.userAgent.replace(/^Mozilla\//, ''));
    redefine(navigator, 'platform', () => cfg.navPlatform);
    redefine(proto, 'platform', () => cfg.navPlatform);
    // real-Chrome shape: webdriver is a prototype accessor returning false,
    // NOT an own property — own-key probes (lodash _.has) flag the instance
    // property even when its value is undefined.
    redefine(proto, 'webdriver', () => false);
    redefine(navigator, 'hardwareConcurrency', () => cfg.hardwareConcurrency);
    redefine(proto, 'hardwareConcurrency', () => cfg.hardwareConcurrency);
    redefine(navigator, 'deviceMemory', () => cfg.deviceMemory);
    redefine(proto, 'deviceMemory', () => cfg.deviceMemory);
    const langs = Object.freeze(cfg.languages.slice());
    redefine(navigator, 'languages', () => langs);
    redefine(proto, 'languages', () => langs);
    redefine(navigator, 'language', () => langs[0]);
    redefine(proto, 'language', () => langs[0]);

    // UA-CH
    const uaData = {
      brands: cfg.brands.map(b => ({brand: b.brand.split(';')[0], version: b.version})),
      mobile: false,
      platform: cfg.uachPlatform,
      toJSON: markNative(function toJSON() {
        return {brands: this.brands, mobile: this.mobile, platform: this.platform};
      }, 'toJSON'),
    };
    uaData.getHighEntropyValues = markNative(function getHighEntropyValues(hints) {
      return Promise.resolve().then(() => {
        const out = {mobile: uaData.mobile, platform: uaData.platform, model: ''};
        (hints || []).forEach(h => {
          if (h === 'platformVersion') out[h] = cfg.platformVersion;
          else if (h === 'architecture') out[h] = 'x86';
          else if (h === 'bitness') out[h] = '64';
          else if (h === 'uaFullVersion') out[h] = cfg.chromeFull;
          else if (h === 'fullVersionList') out[h] = cfg.fullVersionList;
          else if (h === 'model') out[h] = '';
          else if (h === 'wow64') out[h] = false;
        });
        return out;
      });
    }, 'getHighEntropyValues');
    redefine(navigator, 'userAgentData', () => uaData);
    redefine(proto, 'userAgentData', () => uaData);

    if (cfg.spoof.plugins && typeof PluginArray !== 'undefined') {
      const mimes = [
        {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format'},
        {type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format'},
      ];
      const names = ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer',
                     'Microsoft Edge PDF Viewer', 'WebKit built-in PDF'];
      const pluginArr = Object.create(PluginArray.prototype);
      const mimeArr = Object.create(MimeTypeArray.prototype);
      const pluginList = [], mimeList = [];
      names.forEach((name, i) => {
        const p = Object.create(Plugin.prototype);
        Object.defineProperties(p, {
          name: {value: name}, filename: {value: 'internal-pdf-viewer'},
          description: {value: 'Portable Document Format'}, length: {value: mimes.length},
        });
        mimes.forEach((m, j) => {
          const mt = Object.create(MimeType.prototype);
          Object.defineProperties(mt, {
            type: {value: m.type}, suffixes: {value: m.suffixes},
            description: {value: m.description},
            enabledPlugin: {value: p},
          });
          Object.defineProperty(p, String(j), {value: mt});
          if (i === 0) { mimeList.push(mt); Object.defineProperty(mimeArr, String(j), {value: mt}); }
        });
        p.item = markNative(function item(i2) { return this[String(i2)] || null; }, 'item');
        p.namedItem = markNative(function namedItem(n) { return this[n] || null; }, 'namedItem');
        pluginList.push(p);
        Object.defineProperty(pluginArr, String(i), {value: p});
        Object.defineProperty(pluginArr, name, {value: p});
      });
      // plain `arr.length = n` would silently no-op against the read-only
      // WebIDL accessor on the prototype, so define own properties explicitly
      Object.defineProperty(pluginArr, 'length',
        {value: pluginList.length, writable: true, configurable: true, enumerable: true});
      pluginArr.item = markNative(function item(i2) { return pluginList[i2] || null; }, 'item');
      pluginArr.namedItem = markNative(function namedItem(n) { return pluginArr[n] || null; }, 'namedItem');
      pluginArr[Symbol.iterator] = markNative(function* () { yield* pluginList; }, '[Symbol.iterator]');
      Object.defineProperty(mimeArr, 'length',
        {value: mimeList.length, writable: true, configurable: true, enumerable: true});
      mimeArr.item = markNative(function item(i2) { return mimeList[i2] || null; }, 'item');
      mimeArr.namedItem = markNative(function namedItem(n) {
        return mimeList.find(m => m.type === n) || null;
      }, 'namedItem');
      mimeArr[Symbol.iterator] = markNative(function* () { yield* mimeList; }, '[Symbol.iterator]');
      redefine(navigator, 'plugins', () => pluginArr);
      redefine(proto, 'plugins', () => pluginArr);
      redefine(navigator, 'mimeTypes', () => mimeArr);
      redefine(proto, 'mimeTypes', () => mimeArr);
      redefine(navigator, 'pdfViewerEnabled', () => true);
      redefine(proto, 'pdfViewerEnabled', () => true);
    }
  }

  // ---- timezone ----------------------------------------------------------
  if (cfg.spoof.tz) {
    function offsetMinutes(d) {
      try {
        const parts = new Intl.DateTimeFormat('en-US',
          {timeZone: cfg.timezone, timeZoneName: 'longOffset'}).formatToParts(d);
        const name = (parts.find(p => p.type === 'timeZoneName') || {}).value || 'GMT';
        const m = name.match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/);
        if (!m) return 0;
        const sign = m[1] === '-' ? -1 : 1;
        return -(sign * (parseInt(m[2], 10) * 60 + parseInt(m[3] || '0', 10)));
      } catch (e) { return 0; }
    }
    // getTimezoneOffset: minutes *west* of UTC
    redefFn(Date.prototype, 'getTimezoneOffset', markNative(
      function getTimezoneOffset() { return offsetMinutes(this); }, 'getTimezoneOffset'));

    // Intl.DateTimeFormat: inject our tz when the caller didn't pin one
    const RealDTF = Intl.DateTimeFormat;
    const realRO = RealDTF.prototype.resolvedOptions;
    const pinned = new WeakMap();
    function VeilDTF(locales, options) {
      const opts = options || {};
      let inst;
      if (opts.timeZone) inst = Reflect.construct(RealDTF, [locales, opts]);
      else inst = Reflect.construct(RealDTF, [locales, Object.assign({}, opts, {timeZone: cfg.timezone})]);
      pinned.set(inst, !!opts.timeZone);
      return inst;
    }
    VeilDTF.prototype = RealDTF.prototype;
    VeilDTF.supportedLocalesOf = RealDTF.supportedLocalesOf;
    markNative(VeilDTF, 'DateTimeFormat');
    try { Object.defineProperty(Intl, 'DateTimeFormat',
      {value: VeilDTF, writable: true, configurable: true}); } catch (e) {}
    Intl.DateTimeFormat.prototype.resolvedOptions = markNative(function resolvedOptions() {
      const r = realRO.call(this);
      if (pinned.has(this) && !pinned.get(this)) r.timeZone = cfg.timezone;
      return r;
    }, 'resolvedOptions');

    // Date.prototype.toString tail: "GMT-04:00" -> "GMT-0400 (Name)"
    const origToString = Date.prototype.toString;
    redefFn(Date.prototype, 'toString', markNative(function toString() {
      const s = origToString.call(this);
      const off = -offsetMinutes(this);
      const sign = off < 0 ? '-' : '+';
      const a = Math.abs(off);
      const tail = 'GMT' + sign +
        String(Math.floor(a / 60)).padStart(2, '0') + String(a % 60).padStart(2, '0') +
        ' (' + cfg.timezoneName + ')';
      return s.replace(/GMT[+-]\d{2}\d{2}.*$/, tail);
    }, 'toString'));

    // toLocale*String honour our tz explicitly
    ['toLocaleString', 'toLocaleDateString', 'toLocaleTimeString'].forEach(m => {
      const orig = Date.prototype[m];
      redefFn(Date.prototype, m, markNative(function (locales, options) {
        const o = Object.assign({}, options || {}, {timeZone: cfg.timezone});
        try { return orig.call(this, locales || cfg.languages[0], o); }
        catch (e) { return orig.call(this, locales, options); }
      }, m));
    });

    // local component getters
    const localFields = ['FullYear', 'Month', 'Date', 'Day', 'Hours', 'Minutes'];
    localFields.forEach(f => {
      const orig = Date.prototype['get' + f];
      const utcGet = Date.prototype['getUTC' + f];
      redefFn(Date.prototype, 'get' + f, markNative(function () {
        const shifted = new RealDate(this.getTime() - offsetMinutes(this) * 60000);
        return utcGet.call(shifted);
      }, 'get' + f));
    });
    // new Date(y, m, ...) local-parsing constructor
    const RealDate = Date;
    function offsetAt(ms) { return offsetMinutes(new RealDate(ms)); }
    function VeilDate(...args) {
      if (!new.target) return RealDate(...args);
      if (args.length >= 2) {
        let [y, mo, d, h, mi, s, ms] = args;
        y = Number(y); mo = Number(mo);
        if (y >= 0 && y <= 99) y += 1900;
        // components are *our-local*: instant = UTC(components) + westOffset
        let guess = RealDate.UTC(y, mo, d || 1, h || 0, mi || 0, s || 0, ms || 0);
        guess += offsetAt(guess) * 60000;
        return new RealDate(guess);
      }
      return Reflect.construct(RealDate, args, new.target);
    }
    VeilDate.prototype = RealDate.prototype;
    VeilDate.now = RealDate.now; VeilDate.parse = RealDate.parse; VeilDate.UTC = RealDate.UTC;
    markNative(VeilDate, 'Date');
    try { Object.defineProperty(scope, 'Date',
      {value: VeilDate, writable: true, configurable: true}); } catch (e) {}
  }

  // ---- canvas (main thread only: needs document.createElement) -----------
  if (cfg.spoof.canvas && !isWorker) {
    function noiseBytes(seed, w, h, len) {
      const r = prng(hash32(w + 'x' + h, seed));
      const mask = new Uint8Array(Math.min(len, 512));
      for (let i = 0; i < mask.length; i++) mask[i] = (r() * 256) | 0;
      return mask;
    }
    function noisedImageData(ctx, imageData, seed) {
      const d = imageData.data;
      const mask = noiseBytes(seed, imageData.width, imageData.height, d.length);
      for (let i = 0; i < mask.length; i++) {
        const p = (i * 251) % d.length;
        if (p % 4 !== 3) d[p] ^= mask[i] & 1;
      }
      return imageData;
    }
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = markNative(function getImageData() {
      const id = origGetImageData.apply(this, arguments);
      return noisedImageData(this, id, cfg.seed);
    }, 'getImageData');
    const origGetImageData2 = OffscreenCanvasRenderingContext2D.prototype.getImageData;
    OffscreenCanvasRenderingContext2D.prototype.getImageData = markNative(function getImageData() {
      const id = origGetImageData2.apply(this, arguments);
      return noisedImageData(this, id, cfg.seed);
    }, 'getImageData');

    function redrawingClone(canvas) {
      const c2 = document.createElement('canvas');
      c2.width = canvas.width; c2.height = canvas.height;
      const ctx2 = c2.getContext('2d');
      const ctx = canvas.getContext('2d');
      const id = origGetImageData.call(ctx, 0, 0, canvas.width, canvas.height);
      ctx2.putImageData(noisedImageData(ctx, id, cfg.seed), 0, 0);
      return c2;
    }
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = markNative(function toDataURL() {
      try { return origToDataURL.apply(redrawingClone(this), arguments); }
      catch (e) { return origToDataURL.apply(this, arguments); }
    }, 'toDataURL');
    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = markNative(function toBlob(cb, type, q) {
      try { return origToBlob.call(redrawingClone(this), cb, type, q); }
      catch (e) { return origToBlob.call(this, cb, type, q); }
    }, 'toBlob');
    const origConvertToBlob = OffscreenCanvas.prototype.convertToBlob;
    OffscreenCanvas.prototype.convertToBlob = markNative(function convertToBlob(opts) {
      try {
        const c2 = document.createElement('canvas');
        c2.width = this.width; c2.height = this.height;
        c2.getContext('2d').putImageData(
          noisedImageData(this.getContext('2d'),
            origGetImageData2.call(this.getContext('2d'), 0, 0, this.width, this.height), cfg.seed), 0, 0);
        return origToDataURL === null ? null : convertImpl(c2, opts);
      } catch (e) { return origConvertToBlob.call(this, opts); }
    }, 'convertToBlob');
    function convertImpl(canvas, opts) {
      const url = origToDataURL.call(canvas, (opts || {}).type || 'image/png', (opts || {}).quality);
      const bin = atob(url.split(',')[1]);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return Promise.resolve(new Blob([bytes], {type: (opts || {}).type || 'image/png'}));
    }
  }

  // ---- fonts ---------------------------------------------------------------
  // Width-probe font enumeration: any family outside cfg.fonts measures as
  // the generic fallback so the host's real font set never leaks. Same
  // verdict for document.fonts.check.
  const GENERIC = new Set(['serif', 'sans-serif', 'monospace', 'cursive',
    'fantasy', 'system-ui', 'ui-serif', 'ui-sans-serif', 'ui-monospace',
    'math', 'fangsong', 'emoji']);
  const allowedFonts = new Set(cfg.fonts.map(f => f.toLowerCase()));
  const SIZE_TAIL_RE = /(?:\d+(?:\.\d+)?(?:px|pt|pc|in|cm|mm|q|em|rem|ex|ch|vw|vh|vmin|vmax)|0(?:px|pt)?)\s*(?:\/\s*(?:[\d.]+|normal|bold)(?:\s+[\d.]+)?\s*)?([\s\S]*)$/i;
  function familiesOf(font) {
    const m = String(font).match(SIZE_TAIL_RE);
    if (!m) return [];
    return m[1].split(',').map(s =>
      s.trim().replace(/^['"]+|['"]+$/g, '').toLowerCase()).filter(Boolean);
  }
  function familiesAllowed(fams) {
    return fams.length === 0 ||
      fams.some(f => GENERIC.has(f) || allowedFonts.has(f));
  }

  if (cfg.spoof.fonts && !isWorker) {
    function substituteFamilies(font) {
      const m = String(font).match(SIZE_TAIL_RE);
      if (!m || !m[1].trim()) return font;
      return String(font).slice(0, String(font).length - m[1].length) + 'sans-serif';
    }
    const origMeasure = CanvasRenderingContext2D.prototype.measureText;
    CanvasRenderingContext2D.prototype.measureText = markNative(function measureText(text) {
      let out;
      if (familiesAllowed(familiesOf(this.font))) {
        out = origMeasure.call(this, text);
      } else {
        const saved = this.font;
        try { this.font = substituteFamilies(saved); } catch (e) {}
        try { out = origMeasure.call(this, text); }
        finally { try { this.font = saved; } catch (e) {} }
      }
      const eps = ((hash32(String(text), cfg.seed) % 1000) - 500) / 50000;  // +-0.01px
      // Own-property override on the real TextMetrics — an Object.create()
      // wrapper would fail the WebIDL brand check when callers read
      // actualBoundingBox*/fontBoundingBox* (creepjs does exactly that).
      try { Object.defineProperty(out, 'width',
        {value: out.width + eps, enumerable: true, configurable: true}); } catch (e) {}
      return out;
    }, 'measureText');

    if (typeof FontFaceSet !== 'undefined' && document.fonts) {
      const origCheck = FontFaceSet.prototype.check;
      FontFaceSet.prototype.check = markNative(function check(font, text) {
        try {
          const fams = familiesOf(font);
          if (fams.length) {
            if (familiesAllowed(fams)) return true;
            for (const f of document.fonts) {
              const fam = String(f.family).replace(/^['"]+|['"]+$/g, '').toLowerCase();
              if (fams.includes(fam)) break;  // registered webfont: real verdict
            }
            return false;
          }
        } catch (e) {}
        return origCheck.call(this, font, text);
      }, 'check');
    }

    // FontFace local() probing (creepjs): new FontFace(f, 'local("f")').load()
    // resolves iff the host really has font f. Rewrite non-whitelisted
    // local() sources to a name no system provides so the verdict matches
    // our fonts.check story.
    if (typeof FontFace !== 'undefined') {
      const RealFontFace = FontFace;
      function VeilFontFace(family, source, descriptors) {
        if (typeof source === 'string') {
          source = source.replace(
            /local\(\s*(['"]?)([^'")]*)\1\s*\)/g,
            (m, q, name) => {
              const n = String(name).trim().toLowerCase();
              if (GENERIC.has(n) || allowedFonts.has(n)) return m;
              return "local('__veil_not_installed__')";
            });
        }
        return Reflect.construct(RealFontFace, [family, source, descriptors]);
      }
      VeilFontFace.prototype = RealFontFace.prototype;
      markNative(VeilFontFace, 'FontFace');
      try {
        Object.defineProperty(scope, 'FontFace',
          {value: VeilFontFace, writable: true, configurable: true});
        Object.defineProperty(RealFontFace.prototype, 'constructor',
          {value: VeilFontFace, writable: true, configurable: true});
      } catch (e) {}
    }
  }

  // ---- client rects (main thread only) -----------------------------------
  if (cfg.spoof.rects && !isWorker) {
    function adjust(rect, el) {
      const key = [rect.left, rect.top, rect.width, rect.height,
                   el && el.tagName || ''].join(',');
      const dx = ((hash32(key, cfg.seed) % 2000) - 1000) / 100000;  // +-0.01px
      return new DOMRect(rect.x + dx, rect.y + dx, rect.width + dx, rect.height + dx);
    }
    const origGBR = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = markNative(function getBoundingClientRect() {
      return adjust(origGBR.call(this), this);
    }, 'getBoundingClientRect');
    const origGCR = Element.prototype.getClientRects;
    Element.prototype.getClientRects = markNative(function getClientRects() {
      const list = origGCR.call(this);
      const out = [];
      for (let i = 0; i < list.length; i++) out.push(adjust(list[i], this));
      const wrapped = Object.create(list.constructor.prototype);
      for (let i = 0; i < out.length; i++)
        Object.defineProperty(wrapped, String(i),
          {value: out[i], writable: true, configurable: true, enumerable: true});
      Object.defineProperty(wrapped, 'length',
        {value: out.length, writable: true, configurable: true, enumerable: true});
      wrapped.item = markNative(function item(i2) { return out[i2] || null; }, 'item');
      wrapped[Symbol.iterator] = markNative(function* () { yield* out; }, '[Symbol.iterator]');
      return wrapped;
    }, 'getClientRects');
  }

  // ---- audio -------------------------------------------------------------
  if (cfg.spoof.audio && typeof OfflineAudioContext !== 'undefined') {
    function jitterBuffer(buf, ctx) {
      const out = ctx.createBuffer(buf.numberOfChannels, buf.length, buf.sampleRate);
      for (let c = 0; c < buf.numberOfChannels; c++) {
        const src = buf.getChannelData(c);
        const dst = out.getChannelData(c);
        const r = prng(hash32('audio' + c + buf.length, cfg.seed));
        for (let i = 0; i < src.length; i++) dst[i] = src[i] + (r() - 0.5) * 2e-7;
      }
      return out;
    }
    const origStart = OfflineAudioContext.prototype.startRendering;
    OfflineAudioContext.prototype.startRendering = markNative(function startRendering() {
      return origStart.call(this).then(buf => jitterBuffer(buf, this));
    }, 'startRendering');
    const origGetFloat = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = markNative(function getFloatFrequencyData(arr) {
      origGetFloat.call(this, arr);
      const r = prng(hash32('analyser' + arr.length, cfg.seed));
      for (let i = 0; i < arr.length; i++) arr[i] += (r() - 0.5) * 1e-4;
    }, 'getFloatFrequencyData');
  }

  // ---- audio context rates & latencies ------------------------------------
  // sampleRate is read straight off BaseAudioContext, so the reported rate
  // never contradicts the seeded noise math. Contexts constructed with an
  // explicit sampleRate keep it (OfflineAudioContext render length math
  // depends on the requested rate).
  if (cfg.spoof.audioRate && typeof BaseAudioContext !== 'undefined' &&
      typeof AudioContext !== 'undefined') {
    const ctxRates = new WeakMap();
    const RealAC = AudioContext;
    function VeilAudioContext(...args) {
      const inst = Reflect.construct(RealAC, args, new.target);
      const o = args[0];
      if (o && typeof o === 'object' && o.sampleRate) ctxRates.set(inst, o.sampleRate);
      return inst;
    }
    VeilAudioContext.prototype = RealAC.prototype;
    markNative(VeilAudioContext, 'AudioContext');
    try {
      Object.defineProperty(scope, 'AudioContext',
        {value: VeilAudioContext, writable: true, configurable: true});
      Object.defineProperty(RealAC.prototype, 'constructor',
        {value: VeilAudioContext, writable: true, configurable: true});
    } catch (e) {}
    if (typeof OfflineAudioContext !== 'undefined') {
      const RealOAC = OfflineAudioContext;
      function VeilOAC(...args) {
        const inst = Reflect.construct(RealOAC, args, new.target);
        const o = args[0];
        if (o && typeof o === 'object' && o.sampleRate) ctxRates.set(inst, o.sampleRate);
        else if (typeof args[2] === 'number') ctxRates.set(inst, args[2]);
        return inst;
      }
      VeilOAC.prototype = RealOAC.prototype;
      markNative(VeilOAC, 'OfflineAudioContext');
      try {
        Object.defineProperty(scope, 'OfflineAudioContext',
          {value: VeilOAC, writable: true, configurable: true});
        Object.defineProperty(RealOAC.prototype, 'constructor',
          {value: VeilOAC, writable: true, configurable: true});
      } catch (e) {}
    }
    redefine(BaseAudioContext.prototype, 'sampleRate', function sampleRate() {
      const rec = ctxRates.get(this);
      return rec === undefined ? cfg.audioSampleRate : rec;
    });
    if (!isWorker) {
      redefine(AudioContext.prototype, 'baseLatency', () => cfg.audioBaseLatency);
      redefine(AudioContext.prototype, 'outputLatency', () => cfg.audioOutputLatency);
      if (typeof AudioDestinationNode !== 'undefined') {
        redefine(AudioDestinationNode.prototype, 'maxChannelCount', () => 2);
      }
    }
  }

  // ---- battery ------------------------------------------------------------
  // ungoogled-chromium removes the Battery API entirely; a "Google Chrome"
  // fingerprint without navigator.getBattery is itself a tell, so the whole
  // surface (class + manager + promise) is synthesized when missing.
  if (cfg.spoof.battery && !isWorker) {
    const b = cfg.battery;
    if (typeof BatteryManager === 'undefined') {
      const BM = function BatteryManager() {};
      BM.prototype = Object.create(
        typeof EventTarget !== 'undefined' ? EventTarget.prototype : Object.prototype);
      Object.defineProperty(BM.prototype, 'constructor',
        {value: BM, writable: true, configurable: true});
      markNative(BM, 'BatteryManager');
      ['charging', 'level', 'chargingTime', 'dischargingTime'].forEach(p =>
        redefine(BM.prototype, p, () => b[p]));
      ['chargingchange', 'levelchange', 'chargingtimechange',
       'dischargingtimechange'].forEach(t => {
        try { Object.defineProperty(BM.prototype, 'on' + t,
          {value: null, writable: true, configurable: true, enumerable: true}); } catch (e) {}
      });
      try { Object.defineProperty(scope, 'BatteryManager',
        {value: BM, writable: true, configurable: true}); } catch (e) {}
    } else {
      ['charging', 'level', 'chargingTime', 'dischargingTime'].forEach(p =>
        redefine(BatteryManager.prototype, p, () => b[p]));
    }
    const bmInst = Object.create(BatteryManager.prototype);
    const navProto = Object.getPrototypeOf(navigator);
    const stub = markNative(function getBattery() {
      return Promise.resolve(bmInst);
    }, 'getBattery');
    redefine(navigator, 'getBattery', () => stub);
    redefine(navProto, 'getBattery', () => stub);
  }

  // ---- speech synthesis ----------------------------------------------------
  if (cfg.spoof.speech && !isWorker && typeof speechSynthesis !== 'undefined') {
    const voices = cfg.speechVoices.map(v => {
      const vo = Object.create(SpeechSynthesisVoice.prototype);
      Object.defineProperties(vo, {
        voiceURI: {value: v.voiceURI}, name: {value: v.name},
        lang: {value: v.lang}, localService: {value: v.localService},
        default: {value: v.default},
      });
      return vo;
    });
    redefine(SpeechSynthesis.prototype, 'voices', () => voices.slice());
    redefFn(SpeechSynthesis.prototype, 'getVoices',
      markNative(function getVoices() { return voices; }, 'getVoices'));
  }

  // ---- storage quota --------------------------------------------------------
  // Ephemeral profiles report tiny quotas that read as private/incognito
  // (BrowserScan docks 10% for it); report a plausible desktop value. Also
  // applies in workers: StorageManager exists there and detectors probe it.
  if (cfg.spoof.storage && typeof StorageManager !== 'undefined' &&
      navigator.storage) {
    const s = cfg.storage;
    redefFn(StorageManager.prototype, 'estimate', markNative(function estimate() {
      return Promise.resolve({usage: s.usage, quota: s.quota});
    }, 'estimate'));
  }

  // ---- geolocation ------------------------------------------------------------
  // Coordinates come from the proxy-exit geo query (geo.py), jittered per
  // seed. Only hooked when the profile carries a location; otherwise the
  // native prompt/deny path is untouched.
  if (cfg.geolocation && !isWorker && navigator.geolocation) {
    const g = cfg.geolocation;
    function makePosition() {
      const coords = Object.create(GeolocationCoordinates.prototype);
      Object.defineProperties(coords, {
        latitude: {value: g.lat, enumerable: true},
        longitude: {value: g.lon, enumerable: true},
        accuracy: {value: g.accuracy, enumerable: true},
        altitude: {value: null, enumerable: true},
        altitudeAccuracy: {value: null, enumerable: true},
        heading: {value: null, enumerable: true},
        speed: {value: null, enumerable: true},
      });
      const pos = Object.create(GeolocationPosition.prototype);
      Object.defineProperties(pos, {
        coords: {value: coords, enumerable: true},
        timestamp: {value: Date.now(), enumerable: true},
      });
      return pos;
    }
    redefFn(Geolocation.prototype, 'getCurrentPosition', markNative(
      function getCurrentPosition(success, error, options) {
        setTimeout(() => { try { success(makePosition()); } catch (e) {} }, 30);
      }, 'getCurrentPosition'));
    let watchId = 0;
    redefFn(Geolocation.prototype, 'watchPosition', markNative(
      function watchPosition(success, error, options) {
        watchId += 1;
        setTimeout(() => { try { success(makePosition()); } catch (e) {} }, 30);
        return watchId;
      }, 'watchPosition'));
    redefFn(Geolocation.prototype, 'clearWatch', markNative(
      function clearWatch(id) {}, 'clearWatch'));
  }

  // ---- WebRTC ICE exit IP ----------------------------------------------------
  // Rewrite every IPv4 in candidate lines (local, raddr and the offer/answer
  // SDP) to the proxy exit IP so the page's WebRTC story matches its network
  // story. WebRTC media is already neutered by the non-proxied-UDP pref, so
  // munging costs nothing functional; only hook when an exit IP is known.
  if (cfg.webrtcIp && !isWorker && typeof RTCPeerConnection !== 'undefined') {
    const EXIT = cfg.webrtcIp;
    const IP_RE = /\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g;
    const IP_TEST_RE = /\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/;
    function mungeSdp(sdp) {
      if (!sdp || sdp.indexOf('a=candidate:') === -1) return sdp;
      return sdp.split('\n').map(line => {
        if (line.indexOf('a=candidate:') !== 0) return line;
        return line.replace(IP_RE, EXIT);
      }).join('\n');
    }
    const NativePC = RTCPeerConnection.prototype;
    const origOfferFn = NativePC.createOffer;
    const origAnswerFn = NativePC.createAnswer;
    const origSetLocal = NativePC.setLocalDescription;
    function mungedDesc(desc) {
      if (desc && typeof desc.sdp === 'string') {
        const sdp = mungeSdp(desc.sdp);
        if (sdp !== desc.sdp) {
          try { return new RTCSessionDescription({type: desc.type, sdp}); } catch (e) {}
        }
      }
      return desc;
    }
    redefFn(NativePC, 'createOffer', markNative(function createOffer(...a) {
      return origOfferFn.apply(this, a).then(mungedDesc);
    }, 'createOffer'));
    redefFn(NativePC, 'createAnswer', markNative(function createAnswer(...a) {
      return origAnswerFn.apply(this, a).then(mungedDesc);
    }, 'createAnswer'));
    redefFn(NativePC, 'setLocalDescription', markNative(function setLocalDescription(desc, ...a) {
      return origSetLocal.call(this, mungedDesc(desc), ...a);
    }, 'setLocalDescription'));
    // candidate events: swap in a real RTCIceCandidate carrying the exit IP
    function wrapListener(fn) {
      return function (event) {
        let e = event;
        try {
          if (e && e.candidate && e.candidate.candidate &&
              IP_TEST_RE.test(e.candidate.candidate)) {
            const c = e.candidate;
            const munged = new RTCIceCandidate({
              candidate: c.candidate.replace(IP_RE, EXIT),
              sdpMid: c.sdpMid, sdpMLineIndex: c.sdpMLineIndex,
              usernameFragment: c.usernameFragment,
            });
            e = Object.create(Object.getPrototypeOf(e), {
              candidate: {value: munged, enumerable: true, configurable: true},
            });
          }
        } catch (err) {}
        return fn.call(this, e);
      };
    }
    const origAEL = NativePC.addEventListener;
    redefFn(NativePC, 'addEventListener', markNative(function addEventListener(type, fn, opts) {
      return origAEL.call(this, type, type === 'icecandidate' && typeof fn === 'function'
        ? wrapListener(fn) : fn, opts);
    }, 'addEventListener'));
    const ON_KEY = 'onicecandidate';
    const origOn = Object.getOwnPropertyDescriptor(NativePC, ON_KEY);
    if (origOn && origOn.set) {
      Object.defineProperty(NativePC, ON_KEY, {
        get: markNative(function onicecandidate() { return origOn.get.call(this); },
                       'get onicecandidate'),
        set: markNative(function onicecandidate(fn) {
          origOn.set.call(this, typeof fn === 'function' ? wrapListener(fn) : fn);
        }, 'set onicecandidate'),
        configurable: true, enumerable: origOn.enumerable,
      });
    }
  }

  // ---- webgl -------------------------------------------------------------
  if (cfg.spoof.webgl) {
    const VENDOR = 0x1F00, RENDERER = 0x1F01, VERSION = 0x1F02;
    const UNMASKED_VENDOR = 0x9245, UNMASKED_RENDERER = 0x9246;
    // Canonical Chrome extension sets: the reported list is the intersection
    // with what the host really supports, so every entry still resolves via
    // getExtension (a listed-but-null extension is itself a detection tell)
    // while host-specific extras (Mesa/SwiftShader variants) never leak.
    const CHROME_EXT1 = new Set([
      'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_half_float',
      'EXT_disjoint_timer_query', 'EXT_float_blend', 'EXT_frag_depth',
      'EXT_shader_texture_lod', 'EXT_sRGB', 'EXT_texture_compression_bptc',
      'EXT_texture_compression_rgtc', 'EXT_texture_filter_anisotropic',
      'KHR_parallel_shader_compile', 'OES_element_index_uint',
      'OES_fbo_render_mipmap', 'OES_standard_derivatives', 'OES_texture_float',
      'OES_texture_float_linear', 'OES_texture_half_float',
      'OES_texture_half_float_linear', 'OES_vertex_array_object',
      'WEBGL_color_buffer_float', 'WEBGL_compressed_texture_astc',
      'WEBGL_compressed_texture_etc', 'WEBGL_compressed_texture_etc1',
      'WEBGL_compressed_texture_pvrtc', 'WEBGL_compressed_texture_s3tc',
      'WEBGL_compressed_texture_s3tc_srgb', 'WEBGL_debug_renderer_info',
      'WEBGL_debug_shaders', 'WEBGL_lose_context', 'WEBGL_multi_draw',
    ]);
    const CHROME_EXT2 = new Set([
      'EXT_color_buffer_float', 'EXT_color_buffer_half_float',
      'EXT_disjoint_timer_query_webgl2', 'EXT_float_blend',
      'EXT_texture_compression_bptc', 'EXT_texture_compression_rgtc',
      'EXT_texture_filter_anisotropic', 'EXT_texture_norm16',
      'KHR_parallel_shader_compile', 'OES_draw_buffers_indexed',
      'OES_texture_float_linear', 'WEBGL_blend_equation_advanced_coherent',
      'WEBGL_compressed_texture_astc', 'WEBGL_compressed_texture_etc',
      'WEBGL_compressed_texture_etc1', 'WEBGL_compressed_texture_pvrtc',
      'WEBGL_compressed_texture_s3tc', 'WEBGL_compressed_texture_s3tc_srgb',
      'WEBGL_debug_renderer_info', 'WEBGL_debug_shaders', 'WEBGL_lose_context',
      'WEBGL_multi_draw', 'WEBGL_render_shared_exponent',
    ]);
    // ANGLE D3D11 canonical precision: floats 127/127/23, ints 31/30/0
    const FLOAT_PREC = {rangeMin: 127, rangeMax: 127, precision: 23};
    const INT_PREC = {rangeMin: 31, rangeMax: 30, precision: 0};
    [typeof WebGLRenderingContext !== 'undefined' && WebGLRenderingContext,
     typeof WebGL2RenderingContext !== 'undefined' && WebGL2RenderingContext].forEach(Ctor => {
      if (!Ctor) return;
      const isGL2 = (typeof WebGL2RenderingContext !== 'undefined' &&
                     Ctor === WebGL2RenderingContext);
      const allow = isGL2 ? CHROME_EXT2 : CHROME_EXT1;
      const origGSE = Ctor.prototype.getSupportedExtensions;
      redefFn(Ctor.prototype, 'getSupportedExtensions', markNative(
        function getSupportedExtensions() {
          const real = origGSE.call(this) || [];
          return real.filter(e => allow.has(e));
        }, 'getSupportedExtensions'));
      const origGP = Ctor.prototype.getParameter;
      Ctor.prototype.getParameter = markNative(function getParameter(p) {
        if (p === UNMASKED_VENDOR) return cfg.webglVendor;
        if (p === UNMASKED_RENDERER) return cfg.webglRenderer;
        if (p === VERSION) return 'WebGL 2.0 (OpenGL ES 3.0 Chromium)';
        return origGP.call(this, p);
      }, 'getParameter');
      const origRP = Ctor.prototype.readPixels;
      if (origRP) {
        Ctor.prototype.readPixels = markNative(function readPixels() {
          origRP.apply(this, arguments);
          const px = arguments[6];
          if (px && px.length) {
            const r = prng(hash32('rp' + px.length, cfg.seed));
            for (let i = 0; i < Math.min(px.length, 64); i++)
              if (i % 4 !== 3) px[i] = (px[i] + ((r() * 2) | 0)) & 0xff;
          }
        }, 'readPixels');
      }
      // precision formats differ by host driver (Mesa/SwiftShader vs D3D11);
      // report the ANGLE D3D11 canon. Wrapping the real instance keeps the
      // WebGLShaderPrecisionFormat brand (nothing consumes it natively).
      const origGSPF = Ctor.prototype.getShaderPrecisionFormat;
      if (origGSPF) {
        Ctor.prototype.getShaderPrecisionFormat = markNative(
          function getShaderPrecisionFormat(shaderType, precisionType) {
            const fmt = origGSPF.call(this, shaderType, precisionType);
            if (!fmt) return fmt;
            const canon = (precisionType >= 0x8DF0 && precisionType <= 0x8DF2)
              ? FLOAT_PREC : INT_PREC;
            return Object.create(Object.getPrototypeOf(fmt), {
              rangeMin: {value: canon.rangeMin, enumerable: true, configurable: true},
              rangeMax: {value: canon.rangeMax, enumerable: true, configurable: true},
              precision: {value: canon.precision, enumerable: true, configurable: true},
            });
          }, 'getShaderPrecisionFormat');
      }
    });
  }

  // ---- screen & window metrics (main thread only) ------------------------
  if (cfg.spoof.screen && !isWorker) {
    const s = cfg.screen;
    const scr = window.screen, scrProto = Screen.prototype;
    // Real host window size (CloakBrowser-style operator escape hatch: page
    // JS sees only the fingerprint). Captured lazily via the native getters —
    // at document-start the fresh document's window metrics aren't final,
    // and this keeps tracking resizes.
    const origWinOW = Object.getOwnPropertyDescriptor(window, 'outerWidth');
    const origWinOH = Object.getOwnPropertyDescriptor(window, 'outerHeight');
    const origScrW = Object.getOwnPropertyDescriptor(scrProto, 'width');
    const origScrH = Object.getOwnPropertyDescriptor(scrProto, 'height');
    try {
      Object.defineProperty(scr, '__width', {get: function __width() {
        const v = origWinOW ? origWinOW.get.call(window) : 0;
        return v || (origScrW ? origScrW.get.call(scr) : 0);
      }, enumerable: false, configurable: true});
      Object.defineProperty(scr, '__height', {get: function __height() {
        const v = origWinOH ? origWinOH.get.call(window) : 0;
        return v || (origScrH ? origScrH.get.call(scr) : 0);
      }, enumerable: false, configurable: true});
    } catch (e) {}
    ['width', 'availWidth'].forEach(p => {
      redefine(scr, p, () => s.w); redefine(scrProto, p, () => s.w);
    });
    ['height'].forEach(p => {
      redefine(scr, p, () => s.h); redefine(scrProto, p, () => s.h);
    });
    ['availHeight'].forEach(p => {
      redefine(scr, p, () => s.availH); redefine(scrProto, p, () => s.availH);
    });
    ['availLeft', 'availTop'].forEach(p => {
      redefine(scr, p, () => 0); redefine(scrProto, p, () => 0);
    });
    ['colorDepth', 'pixelDepth'].forEach(p => {
      redefine(scr, p, () => s.cd); redefine(scrProto, p, () => s.cd);
    });
    // Window metrics (inner/outer/screenX-Y/visualViewport) stay REAL: CSS
    // media queries read the true layout viewport, so claiming a spoofed
    // innerWidth trips matchMedia cross-checks (bot.sannysoft MQ_SCREEN).
    // A fingerprint screen with a non-maximized window is ordinary; the real
    // window size is available above via screen.__width/__height.
    if (s.dpr !== window.devicePixelRatio) {
      redefine(window, 'devicePixelRatio', () => s.dpr);
    }
  }

  // ---- headless env masking (main thread only) ---------------------------
  // headless=new still leaks a few C++-level behaviors no UA override
  // reaches; report whatever a headful browser on this platform would.
  if (cfg.spoof.env && !isWorker) {
    if (typeof Notification !== 'undefined') {
      redefine(Notification, 'permission', () => 'default');
    }
    if (navigator.permissions && navigator.permissions.query) {
      const origQuery = navigator.permissions.query;
      navigator.permissions.query = markNative(function query(desc) {
        const p = origQuery.call(this, desc);
        if (desc && desc.name === 'notifications') {
          return p.then(st => {
            if (st.state === 'denied') {
              return Object.create(Object.getPrototypeOf(st), {
                state: {value: 'prompt', enumerable: true, configurable: true},
                onchange: {value: null, writable: true, enumerable: true,
                           configurable: true},
              });
            }
            return st;
          });
        }
        return p;
      }, 'query');
    }
    redefFn(document, 'hasFocus',
            markNative(function hasFocus() { return true; }, 'hasFocus'));
  }

  // ---- media devices ------------------------------------------------------
  if (cfg.spoof.media && navigator.mediaDevices) {
    const devices = cfg.mediaDevices.map(d => Object.assign({}, d));
    navigator.mediaDevices.enumerateDevices = markNative(function enumerateDevices() {
      return Promise.resolve(devices);
    }, 'enumerateDevices');
  }

  // ---- automation conveniences (main thread only) ------------------------
  if (cfg.automation && cfg.spoof.shadow && !isWorker) {
    const origAttach = Element.prototype.attachShadow;
    Element.prototype.attachShadow = markNative(function attachShadow(init) {
      const root = origAttach.call(this, init);
      try { Object.defineProperty(this, 'fakeShadowRoot',
        {value: root, configurable: true, enumerable: false}); } catch (e) {}
      return root;
    }, 'attachShadow');
  }

  // ---- dedicated worker scope --------------------------------------------
  // addScriptToEvaluateOnNewDocument never runs inside workers, so wrap the
  // Worker constructor and prepend the same bundle to every classic worker
  // script. blob: URLs are read synchronously (XHR) so pages that revoke the
  // object URL right after construction keep working; http(s) scripts are
  // reached through importScripts.
  if (!isWorker && cfg.spoof.worker && cfg.workerBundle) {
    const RealWorker = Worker;
    function VeilWorker(scriptURL, options) {
      const opts = options || {};
      let url = scriptURL;
      if ((opts.type || 'classic') !== 'module') {
        try {
          const abs = new URL(String(scriptURL), document.baseURI).href;
          if (abs.startsWith('blob:')) {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', abs, false);
            xhr.send();
            const src = cfg.workerBundle + '\n;' + (xhr.responseText || '');
            url = URL.createObjectURL(new Blob([src], {type: 'application/javascript'}));
          } else if (/^https?:/.test(abs)) {
            const src = cfg.workerBundle + '\n;importScripts(' + JSON.stringify(abs) + ');';
            url = URL.createObjectURL(new Blob([src], {type: 'application/javascript'}));
          }
        } catch (e) { url = scriptURL; }
      }
      return new RealWorker(url, opts);
    }
    VeilWorker.prototype = RealWorker.prototype;
    markNative(VeilWorker, 'Worker');
    try {
      Object.defineProperty(scope, 'Worker',
        {value: VeilWorker, writable: true, configurable: true});
      Object.defineProperty(RealWorker.prototype, 'constructor',
        {value: VeilWorker, writable: true, configurable: true});
    } catch (e) {}
  }
})();
"""


def build_script(params: dict) -> str:
    """Render the injection bundle with the profile parameters embedded.

    The rendered bundle is embedded a second time (``workerBundle``) so the
    page-level copy can prepend it to dedicated worker scripts — CDP's
    addScriptToEvaluateOnNewDocument does not reach worker scopes.
    """
    base = dict(params)
    worker_bundle = _SCRIPT_TEMPLATE.replace(
        "__VEIL_CFG__", json.dumps(base, separators=(",", ":"), sort_keys=True))
    cfg = json.dumps(dict(base, workerBundle=worker_bundle),
                     separators=(",", ":"), sort_keys=True)
    return _SCRIPT_TEMPLATE.replace("__VEIL_CFG__", cfg)


def install(page_cdp, params: dict, headers: bool = True) -> None:
    """Install the bundle on an attached page session; applies to every new
    document (including iframes) from now on.

    With ``headers`` (default) also pins UA/platform/accept-language and the
    UA-CH client-hint metadata at the CDP layer (Network.setUserAgentOverride)
    so the HTTP ``User-Agent`` and ``Sec-CH-UA*`` headers agree with
    navigator.* — the gap pure-JS spoofing leaves open. Pass ``headers=False``
    for kernel+overlay mode, where the C++ patches own the identity.
    """
    page_cdp.call("Page.enable")
    if headers:
        page_cdp.call("Network.enable")
        page_cdp.call("Network.setUserAgentOverride",
                      userAgent=params["userAgent"],
                      acceptLanguage=params["acceptLanguage"],
                      platform=params["navPlatform"],
                      userAgentMetadata=params["userAgentMetadata"])
    page_cdp.call("Page.addScriptToEvaluateOnNewDocument",
                  source=build_script(params))
