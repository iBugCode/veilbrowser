"""JS injection engine on a vanilla ungoogled-chromium kernel.

These tests prove veilbrowser no longer depends on fingerprint-chromium's
release cadence: every spoofable surface is realized by our CDP-injected
bundle on an unpatched kernel, and the engine survives kernel upgrades.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import veilbrowser
from veilbrowser.inject import build_script, js_params
from veilbrowser.profile import FingerprintProfile, from_preset


def _resolved(seed: int, **kw) -> FingerprintProfile:
    return FingerprintProfile(seed=seed, **kw).resolved()


# ------------------------------------------------------------------ unit ---


class TestJsParamsUnit:
    def test_params_deterministic(self):
        p = _resolved(77)
        a, b = js_params(p, "153.0.8010.52"), js_params(p, "153.0.8010.52")
        assert a == b

    def test_params_seed_dependent_gpu(self):
        gpus = {js_params(_resolved(s), "153.0.8010.52")["webglRenderer"]
                for s in range(1, 30)}
        assert len(gpus) >= 2  # not everyone has the same GPU

    def test_script_embeds_config_and_runs_iife(self):
        params = js_params(_resolved(5), "153.0.8010.52")
        src = build_script(params)
        assert src.lstrip().startswith("(() =>")
        assert '"seed":5' in src or '"seed": 5' in src
        assert "__VEIL_CFG__" not in src

    def test_ua_matches_platform_and_chrome_version(self):
        for platform, token, plat in (("windows", "Windows NT 10.0", "Win32"),
                                      ("macos", "Macintosh", "MacIntel"),
                                      ("linux", "X11; Linux x86_64", "Linux x86_64")):
            params = js_params(_resolved(9, platform=platform), "153.0.8010.52")
            assert token in params["userAgent"]
            assert params["navPlatform"] == plat
            assert params["chromeFull"].startswith("153.")

    def test_kernel_fp_false_flags_fit_vanilla(self):
        p = _resolved(11)
        flags = p.fingerprint_flags(kernel_fp=False)
        joined = " ".join(flags)
        assert "--fingerprint" not in joined
        assert "--timezone" not in joined
        assert "--lang=" in joined and "--accept-lang=" in joined
        # and the kernel variant still carries the patch switches
        assert "--fingerprint=" in " ".join(p.fingerprint_flags(kernel_fp=True))


# ------------------------------------------------------- engine on kernel ---


class TestJsEngineIdentity:
    def test_windows_identity_coherent(self, js_probe_page):
        prof = from_preset("windows-us-office", seed=1001)
        b, page = js_probe_page(prof)
        r = veilbrowser.collect(page)
        assert "Windows NT 10.0" in r["userAgent"]
        assert r["platform"] == "Win32"
        assert r["uaPlatform"] == "Windows"
        assert any("Google Chrome" in x for x in r["uaBrands"])
        assert "Chrome/" in r["userAgent"] and "Headless" not in r["userAgent"]

    def test_macos_identity_coherent(self, js_probe_page):
        prof = from_preset("macos-us-designer", seed=2002)
        b, page = js_probe_page(prof)
        r = veilbrowser.collect(page)
        assert "Macintosh" in r["userAgent"]
        assert r["platform"] == "MacIntel"
        assert r["uaPlatform"] == "macOS"
        gh = page.evaluate(
            "navigator.userAgentData.getHighEntropyValues(['platformVersion'])",
            await_promise=True)
        import json
        assert "." in str(gh)

    def test_edge_brand_coherent(self, js_probe_page):
        prof = _resolved(3003, platform="windows", brand="Edge", language="en-US")
        b, page = js_probe_page(prof)
        ua = page.evaluate("navigator.userAgent")
        assert "Edg/" in ua
        assert any("Microsoft Edge" in x for x in page.evaluate(
            "navigator.userAgentData.brands.map(x => x.brand)"))

    def test_webdriver_hidden_and_hooks_look_native(self, js_probe_page):
        b, page = js_probe_page(_resolved(42))
        r = veilbrowser.collect(page)
        assert not r.get("webdriver")  # undefined (real-Chrome shape) or false
        native = page.evaluate(
            "String(Object.getOwnPropertyDescriptor(Navigator.prototype,'userAgent').get)"
            ".includes('[native code]')")
        assert native
        protoGetter = page.evaluate(
            "(() => { try { return Navigator.prototype.userAgent; } "
            "catch (e) { return 'ERR:' + e; } })()")
        assert protoGetter.startswith("Mozilla/5.0")

    def test_concurrency_and_memory(self, js_probe_page):
        prof = _resolved(77, platform="windows", hardware_concurrency=4)
        b, page = js_probe_page(prof)
        assert page.evaluate("navigator.hardwareConcurrency") == 4
        assert page.evaluate("[8,16,32].includes(navigator.deviceMemory)")

    def test_timezone_coherent_with_language_and_real_offset(self, js_probe_page):
        prof = _resolved(501, language="ja-JP")  # tz pool: Asia/Tokyo
        b, page = js_probe_page(prof)
        r = veilbrowser.collect(page)
        assert r["timezone"] == prof.timezone
        assert r["languages"] == ["ja-JP", "ja"]
        # getTimezoneOffset (minutes west) must equal python's utcoffset (minutes east)
        py_off = datetime.now(ZoneInfo(prof.timezone)).utcoffset().total_seconds() / 60
        assert r["timezoneOffset"] == -py_off

    def test_date_local_semantics(self, js_probe_page):
        prof = _resolved(502, language="ja-JP")
        b, page = js_probe_page(prof)
        checks = page.evaluate("""(() => {
          const d = new Date(2026, 8, 25, 12, 34, 0);   // local components
          return {
            hours: d.getHours(), minutes: d.getMinutes(),
            day: d.getDate(), month: d.getMonth(),
            s: d.toString(),
          };
        })()""")
        assert checks["hours"] == 12 and checks["minutes"] == 34
        assert checks["day"] == 25 and checks["month"] == 8
        assert "(JST)" in checks["s"] or "Japan" in checks["s"]
        assert "GMT+0900" in checks["s"]

    def test_plugins_shape(self, js_probe_page):
        b, page = js_probe_page(_resolved(43))
        assert page.evaluate("navigator.plugins.length") == 5
        assert page.evaluate("navigator.mimeTypes.length") == 2
        assert page.evaluate(
            "navigator.plugins.namedItem('PDF Viewer') !== null")
        assert page.evaluate(
            "navigator.plugins['Chrome PDF Viewer'].name === 'Chrome PDF Viewer'")
        assert page.evaluate("navigator.pdfViewerEnabled") is True

    def test_probe_rows_all_pass(self, js_probe_page):
        b, page = js_probe_page(from_preset("windows-us-office", seed=1001))
        r = veilbrowser.collect(page)
        rows = veilbrowser.check(r)
        failed = [name for name, ok, _ in rows if not ok]
        assert not failed, f"failed check rows: {failed}"


class TestJsEngineNoise:
    CANVAS_JS = """(() => {
      const c = document.createElement('canvas'); c.width = 220; c.height = 60;
      const ctx = c.getContext('2d');
      ctx.textBaseline = 'top'; ctx.font = '16px Arial';
      ctx.fillStyle = '#0f0'; ctx.fillText('veil-js', 2, 2);   // text-only drawing
      return c.toDataURL();
    })()"""

    def test_canvas_deterministic_per_seed(self, js_probe_page):
        b, page = js_probe_page(_resolved(61))
        a1 = page.evaluate(self.CANVAS_JS)
        b2, page2 = js_probe_page(_resolved(61))
        a2 = page2.evaluate(self.CANVAS_JS)
        assert a1 == a2

    def test_canvas_seed_dependent(self, js_probe_page):
        b, page = js_probe_page(_resolved(61))
        c, page2 = js_probe_page(_resolved(62))
        assert page.evaluate(self.CANVAS_JS) != page2.evaluate(self.CANVAS_JS)

    def test_textmetrics_brand_check_survives(self, js_probe_page):
        """measureText noise must not break the TextMetrics brand check:
        reading actualBoundingBox* off the returned object used to throw
        'Illegal invocation' (creepjs's canvas 'blocked')."""
        b, page = js_probe_page(_resolved(61))
        r = page.evaluate("""
          (() => {
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = '10px Arial';
            const m = ctx.measureText('\\ud83d\\ude00 hello');
            return [m.width % 1 !== 0, m.actualBoundingBoxAscent !== undefined,
                    m.fontBoundingBoxDescent !== undefined];
          })()
        """)
        assert r == [True, True, True]

    def test_fonts_no_host_leak(self, js_probe_page):
        """Width probing must not reveal the Linux host's font set: a
        non-whitelisted family (DejaVu Sans — Linux-only) measures exactly
        like a nonexistent font and fails fonts.check."""
        prof = _resolved(95, platform="windows")
        params = js_params(prof)
        assert "DejaVu Sans" not in params["fonts"]
        b, page = js_probe_page(prof)
        r = page.evaluate("""
          (() => {
            const m = (f) => {
              const x = document.createElement('canvas').getContext('2d');
              x.font = `12px '${f}'`;
              return x.measureText('mmmmmmmmmmlli').width;
            };
            return [m('DejaVu Sans') === m('NoSuchFontXYZ'),
                    document.fonts.check("12px 'DejaVu Sans'"),
                    document.fonts.check("12px 'Arial'"),
                    document.fonts.check('12px sans-serif')];
          })()
        """)
        assert r == [True, False, True, True]

    def test_canvas_noised_vs_vanilla_control(self, js_probe_page, vanilla_path):
        """Even a text-only canvas must differ from the unpatched baseline —
        this is the gap fingerprint-chromium's kernel leaves open."""
        prof = _resolved(61)
        b, page = js_probe_page(prof)
        spoofed = page.evaluate(self.CANVAS_JS)
        with veilbrowser.launch(prof, engine="kernel", binary=vanilla_path) as raw:
            page_raw = veilbrowser.probe.open_probe_page(raw)
            baseline = page_raw.evaluate(self.CANVAS_JS)
        assert spoofed != baseline

    def test_clientrects_jitter_deterministic(self, js_probe_page):
        JS = ("(()=>{const d=document.createElement('div');"
              "d.style.cssText='position:absolute;width:137.5px;font-size:13.37px';"
              "d.textContent='clientrects';document.body.appendChild(d);"
              "const rect=d.getClientRects()[0];"
              "const v=[rect.width, rect.height, rect.top].join(',');d.remove();return v})()")
        b, page = js_probe_page(_resolved(63))
        r1 = page.evaluate(JS)
        width = r1.split(",")[0]
        assert width != "137.5"  # width was perturbed
        b2, page2 = js_probe_page(_resolved(63))
        r2 = page2.evaluate(JS)
        assert r1 == r2

    def test_audio_seed_dependent(self, js_probe_page):
        b, page = js_probe_page(_resolved(64))
        c, page2 = js_probe_page(_resolved(65))
        s1 = page.evaluate(
            "(async()=>{const ac=new OfflineAudioContext(1,44100,44100);"
            "const o=ac.createOscillator();const g=ac.createGain();"
            "o.type='triangle';g.gain.value=0.5;o.connect(g);g.connect(ac.destination);"
            "o.start(0);const buf=await ac.startRendering();"
            "const d=buf.getChannelData(0);let s=0;for(let i=4500;i<5000;i++)s+=Math.abs(d[i]);"
            "return s})()", await_promise=True)
        s2 = page2.evaluate(
            "(async()=>{const ac=new OfflineAudioContext(1,44100,44100);"
            "const o=ac.createOscillator();const g=ac.createGain();"
            "o.type='triangle';g.gain.value=0.5;o.connect(g);g.connect(ac.destination);"
            "o.start(0);const buf=await ac.startRendering();"
            "const d=buf.getChannelData(0);let s=0;for(let i=4500;i<5000;i++)s+=Math.abs(d[i]);"
            "return s})()", await_promise=True)
        assert s1 != s2

    def test_webgl_strings_from_profile(self, js_probe_page):
        prof = _resolved(66)
        b, page = js_probe_page(prof)
        params = js_params(prof)
        r = page.evaluate(
            "(()=>{const gc=document.createElement('canvas').getContext('webgl');"
            "const dbg=gc.getExtension('WEBGL_debug_renderer_info');"
            "return [gc.getParameter(dbg.UNMASKED_VENDOR_WEBGL),"
            "gc.getParameter(dbg.UNMASKED_RENDERER_WEBGL)]})()")
        assert r == [params["webglVendor"], params["webglRenderer"]]

    def test_getters_survive_prototype_escape(self, js_probe_page):
        b, page = js_probe_page(_resolved(67, platform="windows"))
        v = page.evaluate(
            "Object.getOwnPropertyDescriptor(Navigator.prototype,'platform')"
            ".get.call(navigator)")
        assert v == "Win32"


class TestJsEngineFrames:
    def test_injection_covers_iframes(self, js_probe_page):
        b, page = js_probe_page(from_preset("windows-us-office", seed=1001))
        ua = page.evaluate("""(async () => {
          const f = document.createElement('iframe');
          f.srcdoc = '<html><body>frame</body></html>';
          document.body.appendChild(f);
          await new Promise(res => f.onload = res);
          return f.contentWindow.navigator.userAgent;
        })()""", await_promise=True)
        assert "Windows NT 10.0" in ua


class TestJsEngineWorkers:
    """addScriptToEvaluateOnNewDocument never reaches worker scopes; the
    Worker-constructor wrapper must carry the bundle into dedicated workers
    (creepjs's loudest leak: real HeadlessChrome UA + UTC in worker scope)."""

    _WORKER_PROBE = """
      new Promise(res => {
        const w = new Worker(URL.createObjectURL(new Blob([`
          postMessage([
            navigator.userAgent,
            navigator.platform,
            navigator.webdriver,
            new Date().getTimezoneOffset(),
            navigator.userAgentData ? navigator.userAgentData.platform : null,
            (() => { try {
              return new OffscreenCanvas(1, 1).getContext('webgl').getParameter(0x9246);
            } catch (e) { return null; } })(),
          ]);
        `], {type: 'application/javascript'})));
        w.onmessage = e => res(e.data);
        setTimeout(() => res(null), 5000);
      })
    """

    def test_worker_scope_spoofed(self, make_js_browser):
        prof = _resolved(91, platform="windows", timezone="America/New_York",
                         language="en-US")
        params = js_params(prof)
        b = make_js_browser(prof)
        try:
            page = b.new_page()
            r = page.evaluate(self._WORKER_PROBE, await_promise=True)
            assert r is not None, "worker never answered"
            ua, plat, webdriver, tzoff, uach, gpu = r
            assert ua == params["userAgent"]
            assert plat == params["navPlatform"]
            assert not webdriver
            py_off = datetime.now(
                ZoneInfo(params["timezone"])).utcoffset().total_seconds() / 60
            assert tzoff == -py_off
            assert uach == params["uachPlatform"]
            assert gpu == params["webglRenderer"]
        finally:
            b.stop()

    def test_worker_storage_estimate_spoofed(self, make_js_browser):
        # StorageManager only exists in secure contexts (127.0.0.1 counts),
        # so this probe runs on a local origin, not the file:// probe page.
        from tests._proxies import TargetSite
        prof = _resolved(97)
        params = js_params(prof)
        target = TargetSite()
        b = make_js_browser(prof)
        try:
            page = b.new_page(f"http://127.0.0.1:{target.port}/probe")
            r = page.evaluate("""
              new Promise(res => {
                const w = new Worker(URL.createObjectURL(new Blob([`
                  navigator.storage.estimate()
                    .then(e => postMessage([e.quota, e.usage]))
                    .catch(err => postMessage(['err:' + err.message, 0]));
                `], {type: 'application/javascript'})));
                w.onmessage = e => res(e.data);
                setTimeout(() => res(null), 5000);
              })""", await_promise=True)
            assert r is not None, "worker never answered"
            assert r[0] == params["storage"]["quota"]
            assert r[1] == params["storage"]["usage"]
        finally:
            b.stop()
            target.stop()

    def test_worker_identity_preserved(self, make_js_browser):
        b = make_js_browser(from_preset("windows-us-office", seed=1001))
        try:
            page = b.new_page()
            assert page.evaluate(
                "new Worker('data:,postMessage(1)').constructor.name") == "Worker"
        finally:
            b.stop()

    def test_worker_http_ua_header_matches(self):
        """--user-agent at launch: worker fetches hit the wire with the
        spoofed UA (CDP override alone only covers the page session)."""
        from tests._proxies import TargetSite

        target = TargetSite()
        try:
            prof = from_preset("windows-us-office", seed=1001)
            b = veilbrowser.launch(prof, engine="js",
                                   extra_flags=["--proxy-bypass-list=<-loopback>"])
            try:
                url = f"http://127.0.0.1:{target.port}/probe"
                page = b.new_page(url)
                nav_ua = page.evaluate("navigator.userAgent")
                r = page.evaluate(f"""
                  new Promise(res => {{
                    const w = new Worker(URL.createObjectURL(new Blob([`
                      fetch({url!r}).then(r => r.text())
                        .then(t => postMessage('ok'))
                        .catch(e => postMessage('err:' + e.message));
                    `], {{type: 'application/javascript'}})));
                    w.onmessage = e => res(e.data);
                    setTimeout(() => res('timeout'), 5000);
                  }})
                """, await_promise=True)
                assert r == "ok", r
                assert target.user_agents
                assert all(ua == nav_ua for ua in target.user_agents)
            finally:
                b.stop()
        finally:
            target.stop()


class TestJsEngineWithProxy:
    BYPASS_LOOPBACK = ["--proxy-bypass-list=<-loopback>"]

    def test_authenticated_http_chain_still_authenticates(self, js_probe_page):
        from tests._proxies import TargetSite, UpstreamHTTPProxy

        target, upstream = TargetSite(), UpstreamHTTPProxy("user", "pw")
        try:
            prof = _resolved(71, proxy=f"http://user:pw@127.0.0.1:{upstream.port}")
            b = veilbrowser.launch(prof, engine="js",
                                   extra_flags=self.BYPASS_LOOPBACK)
            try:
                page = b.new_page(f"http://127.0.0.1:{target.port}/probe")
                body = page.evaluate("document.body.innerText")
                assert '"ok": true' in body
                assert upstream.saw_auth  # credentials reached the upstream
                assert any("/probe" in p for p in target.requests)
            finally:
                b.stop()
        finally:
            target.stop()
            upstream.stop()


class TestJsEngineCoherence:
    """Camoufox-inspired coherence: HTTP headers, screen metrics, media devices."""

    def test_http_ua_header_matches_navigator(self, make_js_browser):
        """The gap pure-JS engines leave: Sec-CH-UA/User-Agent HTTP headers
        must agree with navigator.* — enforced via CDP UA override."""
        from tests._proxies import TargetSite

        prof = from_preset("windows-us-office", seed=1001)
        target = TargetSite()
        try:
            b = make_js_browser(prof)
            page = b.new_page(f"http://127.0.0.1:{target.port}/probe")
            nav_ua = page.evaluate("navigator.userAgent")
            nav_platform_ch = page.evaluate(
                "navigator.userAgentData.platform")
            assert target.user_agents, "target saw no request"
            assert all(ua == nav_ua for ua in target.user_agents), (
                f"header UA mismatch: {target.user_agents!r} vs {nav_ua!r}")
            assert all(p.strip('"') == nav_platform_ch
                       for p in target.client_hints.get("sec-ch-ua-platform", []))
            assert all("Google Chrome" in c
                       for c in target.client_hints.get("sec-ch-ua", []))
            assert any("/probe" in p for p in target.requests)
            b.stop()
        finally:
            target.stop()

    def test_screen_metrics_coherent(self, js_probe_page):
        prof = _resolved(81, platform="windows")
        params = js_params(prof)
        b, page = js_probe_page(prof)
        got = page.evaluate(
            "[screen.width, screen.height, screen.availWidth, screen.availHeight,"
            "window.outerWidth, window.outerHeight, screen.colorDepth]")
        s = params["screen"]
        assert got == [s["w"], s["h"], s["availW"], s["availH"], s["w"], s["h"], s["cd"]]

    def test_screen_seed_dependent(self, js_probe_page):
        widths = set()
        for seed in (81, 82, 83, 84):
            prof = _resolved(seed, platform="windows")
            params = js_params(prof)
            b, page = js_probe_page(prof)
            widths.add(page.evaluate("screen.width + 'x' + screen.height"))
            b.stop()
        assert len(widths) >= 2

    def test_media_devices_spoofed(self, js_probe_page):
        prof = _resolved(85)
        params = js_params(prof)
        b, page = js_probe_page(prof)
        kinds = page.evaluate(
            "navigator.mediaDevices.enumerateDevices().then(ds => ds.map(d => d.kind))",
            await_promise=True)
        assert sorted(kinds) == sorted(d["kind"] for d in params["mediaDevices"])
        assert "audioinput" in kinds

    def test_audio_context_rates_and_latency(self, js_probe_page):
        prof = _resolved(88)
        params = js_params(prof)
        b, page = js_probe_page(prof)
        r = page.evaluate(
            "(async()=>{"
            "const ac=new AudioContext();"
            "const oac=new OfflineAudioContext(1,4410,44100);"
            "const opt=new OfflineAudioContext({numberOfChannels:1,length:2205,"
            "sampleRate:22050});"
            "return [ac.sampleRate, ac.baseLatency, ac.outputLatency,"
            "oac.sampleRate, opt.sampleRate,"
            "ac instanceof AudioContext, oac instanceof OfflineAudioContext];"
            "})()", await_promise=True)
        assert r[0] == params["audioSampleRate"]
        assert r[1] == params["audioBaseLatency"]
        assert r[2] == params["audioOutputLatency"]
        # explicit constructor rates are preserved (render math depends on them)
        assert r[3] == 44100
        assert r[4] == 22050
        assert r[5] is True and r[6] is True

    def test_battery_spoofed(self, js_probe_page):
        prof = _resolved(89)
        params = js_params(prof)["battery"]
        b, page = js_probe_page(prof)
        r = page.evaluate(
            "(async()=>{const bm=await navigator.getBattery();"
            "return [bm.charging, bm.level, bm.chargingTime === Infinity,"
            "bm.dischargingTime === Infinity, bm.dischargingTime,"
            "bm instanceof BatteryManager];"
            "})()", await_promise=True)
        assert r[0] == params["charging"]
        assert r[1] == params["level"]
        assert r[2] == math.isinf(params["chargingTime"])
        assert r[3] == math.isinf(params["dischargingTime"])
        assert r[4] == params["dischargingTime"]
        assert r[5] is True

    def test_speech_voices_match_platform_and_language(self, js_probe_page):
        prof = _resolved(90, platform="windows", language="de-DE")
        params = js_params(prof)
        b, page = js_probe_page(prof)
        r = page.evaluate(
            "(()=>{const vs=speechSynthesis.getVoices();"
            "return [vs.length, vs[0] instanceof SpeechSynthesisVoice,"
            "vs.map(v => v.name).join('|'), vs.map(v => v.lang).join('|'),"
            "speechSynthesis.voices.length];"
            "})()")
        expected_names = [v["name"] for v in params["speechVoices"]]
        expected_langs = [v["lang"] for v in params["speechVoices"]]
        assert r[0] == len(expected_names)
        assert r[1] is True
        assert r[2] == "|".join(expected_names)
        assert r[3] == "|".join(expected_langs)
        assert "German" in "|".join(expected_names)  # de-DE session speaks German
        assert r[4] == len(expected_names)

    def test_battery_seed_dependent(self):
        bats = {json.dumps(js_params(_resolved(s), "153.0.8010.52")["battery"],
                           sort_keys=True)
                for s in range(1, 25)}
        assert len(bats) >= 3  # not every instance carries the same charge

    def test_gpu_pool_matches_platform_format(self):
        # Windows reports D3D11, macOS the Metal renderer, Linux Mesa/OpenGL;
        # a Mesa string next to Win32 is an immediate contradiction.
        for platform, marker in (("windows", "Direct3D11"),
                                 ("macos", "ANGLE Metal Renderer: Apple"),
                                 ("linux", "OpenGL 4.6")):
            for s in (3, 42, 77):
                params = js_params(_resolved(s, platform=platform), "153.0.8010.52")
                assert marker in params["webglRenderer"]
                if platform == "macos":
                    assert params["webglVendor"] == "Google Inc. (Apple)"
                else:
                    assert params["webglVendor"].startswith("Google Inc. (")

    def test_webgl_extensions_intersect_chrome_set(self, js_probe_page):
        # reported list ⊆ canonical Chrome set AND every entry really
        # resolves via getExtension (listed-but-null is a detection tell)
        b, page = js_probe_page(_resolved(91))
        r = page.evaluate("""
          (() => {
            const g1 = document.createElement('canvas').getContext('webgl');
            const g2 = document.createElement('canvas').getContext('webgl2');
            const exts1 = g1.getSupportedExtensions() || [];
            const exts2 = (g2 && g2.getSupportedExtensions()) || [];
            const missing = [];
            for (const e of exts1) if (!g1.getExtension(e)) missing.push(e);
            for (const e of exts2) if (!g2.getExtension(e)) missing.push(e);
            return [exts1.length, exts2.length, missing.length,
                    exts1.includes('WEBGL_debug_renderer_info')];
          })()""")
        assert r[0] > 0 and r[1] > 0
        assert r[2] == 0
        assert r[3] is True

    def test_shader_precision_canonical(self, js_probe_page):
        b, page = js_probe_page(_resolved(92))
        r = page.evaluate("""
          (() => {
            const g = document.createElement('canvas').getContext('webgl');
            const f = g.getShaderPrecisionFormat(g.FRAGMENT_SHADER, g.HIGH_FLOAT);
            const i = g.getShaderPrecisionFormat(g.VERTEX_SHADER, g.HIGH_INT);
            return [f instanceof WebGLShaderPrecisionFormat,
                    [f.rangeMin, f.rangeMax, f.precision].join(','),
                    [i.rangeMin, i.rangeMax, i.precision].join(',')];
          })()""")
        # ANGLE D3D11 canon: floats 127/127/23, ints 31/30/0
        assert r == [True, "127,127,23", "31,30,0"]

    def test_storage_estimate_spoofed(self, js_probe_page):
        prof = _resolved(93)
        params = js_params(prof)
        b, page = js_probe_page(prof)
        r = page.evaluate(
            "navigator.storage.estimate().then(e => [e.quota, e.usage])",
            await_promise=True)
        assert r[0] == params["storage"]["quota"]
        assert r[1] == params["storage"]["usage"]
        assert r[0] > 100 * 1024 ** 3  # not an incognito-sized quota

    def test_audio_destination_max_channels(self, js_probe_page):
        b, page = js_probe_page(_resolved(95))
        v = page.evaluate("new AudioContext().destination.maxChannelCount")
        assert v == 2

    def test_webrtc_ice_exit_ip(self, js_probe_page):
        prof = _resolved(94, webrtc_ip="203.0.113.7")
        b, page = js_probe_page(prof)
        r = page.evaluate("""
          (async () => {
            const pc = new RTCPeerConnection();
            const got = [];
            pc.addEventListener('icecandidate', e => {
              if (e.candidate) got.push(e.candidate.candidate);
            });
            pc.onicecandidate = e => {
              if (e.candidate) got.push('on:' + e.candidate.candidate);
            };
            const cand = new RTCIceCandidate({
              candidate: 'candidate:842163049 1 udp 1677729535 192.168.1.4 54433 typ host',
              sdpMid: '0', sdpMLineIndex: 0});
            const ev = new Event('icecandidate');
            Object.defineProperty(ev, 'candidate', {value: cand});
            pc.dispatchEvent(ev);
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);  // throws if we broke the brand
            return [got.join('|'), pc.localDescription.sdp.length > 0,
                    pc instanceof RTCPeerConnection];
          })()""", await_promise=True)
        assert "203.0.113.7" in r[0]
        assert "192.168.1.4" not in r[0]
        assert r[0].count("203.0.113.7") == 2  # both registration paths
        assert r[1] is True and r[2] is True

    def test_no_webrtc_ip_no_munging(self, js_probe_page):
        b, page = js_probe_page(_resolved(96))
        r = page.evaluate("""
          (() => {
            const pc = new RTCPeerConnection();
            const got = [];
            pc.onicecandidate = e => { if (e.candidate) got.push(e.candidate.candidate); };
            const cand = new RTCIceCandidate({
              candidate: 'candidate:1 1 udp 1 192.168.1.4 5000 typ host',
              sdpMid: '0', sdpMLineIndex: 0});
            const ev = new Event('icecandidate');
            Object.defineProperty(ev, 'candidate', {value: cand});
            pc.dispatchEvent(ev);
            return got.join('|');
          })()""")
        assert "192.168.1.4" in r  # untouched without a known exit IP

    def test_webrtc_prefs_seeded_with_proxy(self, make_js_browser):
        import json
        import os
        prof = _resolved(86, proxy="socks5://u:p@127.0.0.1:1080")
        b = make_js_browser(prof)
        try:
            prefs = json.load(open(os.path.join(b.user_data_dir, "Default",
                                                "Preferences"), encoding="utf-8"))
            assert prefs["webrtc"]["ip_handling_policy"] == "disable_non_proxied_udp"
            assert prefs["webrtc"]["nonproxied_udp_enabled"] is False
        finally:
            b.stop()

    def test_no_proxy_no_webrtc_pref(self, make_js_browser):
        import os
        prof = _resolved(87)
        b = make_js_browser(prof)
        try:
            assert not os.path.exists(os.path.join(b.user_data_dir, "Default",
                                                   "Preferences"))
        finally:
            b.stop()
