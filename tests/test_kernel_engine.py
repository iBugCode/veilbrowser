"""Pure C++ kernel engine — the fingerprint lives entirely in Blink C++.

Nothing is injected at runtime: the switches passed by ``fingerprint_flags``
are the whole story. Tests need a kernel built with kernel-patches/
(scripts/build-kernel.sh) and skip politely on an unpatched binary.
"""
from __future__ import annotations

import os
import time

import pytest

from veilbrowser import launch
from veilbrowser.browser import default_binary
from veilbrowser.inject import screen_metrics
from veilbrowser.profile import FingerprintProfile, from_preset


@pytest.fixture(scope="module")
def kernel():
    """A running pure-kernel browser; skips when the kernel can't do it."""
    binary = os.environ.get("VEIL_CHROME_PATH") or default_binary()
    if not binary or not os.path.isfile(binary):
        pytest.skip("kernel binary not found")
    prof = from_preset("windows-us-office", seed=1001)
    try:
        b = launch(prof, engine="kernel", headless=True)
    except Exception as exc:
        pytest.skip(f"kernel launch failed: {exc}")
    try:
        page = b.new_page("about:blank")
        # The pure kernel ships NO JS engine at all — the bundle symbols from
        # the v0.8 native engine must be gone, and the C++ MQ spoofing must
        # be live.
        if page.evaluate(
                "typeof veilNativeCfg === 'function'") is not False:
            pytest.skip("binary still carries a JS bundle engine")
        if page.evaluate(
                "matchMedia('(pointer: fine)').matches") is not True:
            pytest.skip("kernel lacks the media-query C++ patches")
        yield b, prof
    finally:
        b.stop()


# ----------------------------------------------------------------- kernel ---

def test_zero_injection(kernel):
    """No JS bundle, no injection machinery — switches only."""
    b, prof = kernel
    assert b.js_params is None
    with b.new_page("about:blank") as page:
        assert page.evaluate("typeof veilNativeCfg === 'undefined'") is True
        assert page.evaluate("typeof __veil_installed === 'undefined'") is True
    deadline = time.monotonic() + 10
    while b.kernel_active is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert b.kernel_active is True


def test_identity_matches_switches(kernel):
    b, prof = kernel
    sm = screen_metrics(prof.resolved())
    with b.new_page("about:blank") as page:
        ua = page.evaluate("navigator.userAgent")
        assert "Windows NT 10.0" in ua and "Headless" not in ua
        assert page.evaluate("navigator.platform") == "Win32"
        assert page.evaluate("navigator.webdriver") is False
        assert page.evaluate(
            "Intl.DateTimeFormat().resolvedOptions().timeZone") == prof.timezone
        assert page.evaluate("navigator.languages") == ["en-US", "en"]
        assert page.evaluate("[screen.width, screen.height, screen.availWidth,"
                             " screen.availHeight]") == \
            [sm["w"], sm["h"], sm["availW"], sm["availH"]]


def test_media_queries_consistent(kernel):
    """The v0.8 gap: headless reported (pointer: fine)/(hover: hover) false."""
    b, _ = kernel
    sm = screen_metrics(b.profile.resolved())
    with b.new_page("about:blank") as page:
        assert page.evaluate("matchMedia('(pointer: fine)').matches") is True
        assert page.evaluate("matchMedia('(any-pointer: fine)').matches") is True
        assert page.evaluate("matchMedia('(hover: hover)').matches") is True
        assert page.evaluate(
            f"matchMedia('(device-width: {sm['w']}px)').matches") is True
        assert page.evaluate(
            f"matchMedia('(device-height: {sm['h']}px)').matches") is True
        assert page.evaluate(
            "matchMedia('(prefers-color-scheme: light)').matches") is True


def test_metric_fonts_embedded(kernel):
    """Fontless hosts must still measure like a real Windows Chrome."""
    b, _ = kernel
    with b.new_page("about:blank") as page:
        widths = page.evaluate(
            "(() => { const c = document.createElement('canvas')"
            ".getContext('2d'); c.font = '72px Arial';"
            " const a = c.measureText('mmmmmmmmmmlli').width;"
            " c.font = '72px \\'Courier New\\'';"
            " const m = c.measureText('mmmmmmmmmmlli').width;"
            " c.font = '72px Calibri';"
            " const cb = c.measureText('mmmmmmmmmmlli').width;"
            " return [a, m, cb]; })()")
        assert all(w > 100 for w in widths), f"collapsed widths: {widths}"
        assert widths[0] != widths[1], "Arial and Courier must differ"
        assert widths[0] != widths[2], "Arial and Calibri must differ"
        assert page.evaluate("document.fonts.check('12px Calibri')") is True
        assert page.evaluate(
            "document.fonts.check('12px \\'Segoe UI\\'')") is True


def test_desktop_environment_synthesized(kernel):
    """voices / mediaDevices / quota / sampleRate must read like a desktop."""
    b, prof = kernel
    from veilbrowser.probe import open_probe_page
    page = open_probe_page(b)  # secure context: mediaDevices exists
    try:
        # voices populate asynchronously (voiceschanged) — wait for them
        page.evaluate(
            "(async () => { if (!speechSynthesis.getVoices().length)"
            " await new Promise(r => { speechSynthesis.onvoiceschanged = r;"
            " setTimeout(r, 2000); }); })()", await_promise=True)
        out = page.evaluate(
            "(async () => ({"
            " voices: speechSynthesis.getVoices().map(v => v.name + '|' + v.lang),"
            " devs: (await navigator.mediaDevices.enumerateDevices())"
            "   .map(d => d.kind),"
            " quota: (await navigator.storage.estimate()).quota,"
            " rate: new AudioContext().sampleRate }))()",
            await_promise=True)
    finally:
        page.close()
    assert len(out["voices"]) >= 3
    assert all(v.startswith("Microsoft") and "|en-US" in v
               for v in out["voices"]), out["voices"]
    assert out["voices"][0].startswith("Microsoft David")
    assert "audioinput" in out["devs"] and "audiooutput" in out["devs"]
    assert out["quota"] > 200_000_000_000
    assert out["rate"] == 48000


def test_webgl_limits_match_gpu_story(kernel):
    """SwiftShader's 8192 texture limit betrays the RTX renderer string."""
    b, _ = kernel
    with b.new_page("about:blank") as page:
        out = page.evaluate(
            "(() => { const gl = document.createElement('canvas')"
            ".getContext('webgl');"
            " const dbg = gl.getExtension('WEBGL_debug_renderer_info');"
            " return {r: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)"
            "               : '', max: gl.getParameter(gl.MAX_TEXTURE_SIZE),"
            " dims: [...gl.getParameter(gl.MAX_VIEWPORT_DIMS)]}; })()")
        assert out["max"] in (16384, 32768)
        assert out["dims"] == [out["max"], out["max"]]


def test_worker_and_iframe_consistent(kernel):
    """Blink-level patches apply in workers and iframes natively."""
    b, prof = kernel
    from veilbrowser.probe import open_probe_page
    page = open_probe_page(b)
    try:
        out = page.evaluate(
            "(async () => {"
            " const code = 'postMessage({ua: navigator.userAgent,"
            " tz: Intl.DateTimeFormat().resolvedOptions().timeZone,"
            " lang: navigator.language})';"
            " const w = new Worker(URL.createObjectURL(new Blob([code],"
            " {type: 'application/javascript'})));"
            " const worker = await new Promise("
            " r => { w.onmessage = e => r(e.data); });"
            " w.terminate();"
            " const ifr = document.createElement('iframe');"
            " ifr.srcdoc = '<script>window.result ="
            " {ua: navigator.userAgent.slice(0, 40),"
            " screenW: screen.width,"
            " ptr: matchMedia(\\'(pointer: fine)\\').matches}<\\/script>';"
            " document.body.appendChild(ifr);"
            " await new Promise(r => { ifr.onload = r; setTimeout(r, 1500); });"
            " const iframe = ifr.contentWindow.result || null;"
            " ifr.remove();"
            " return {worker, iframe}; })()",
            await_promise=True)
    finally:
        page.close()
    ua = out["worker"]["ua"]
    assert "Windows NT 10.0" in ua and "Headless" not in ua
    assert out["worker"]["tz"] == prof.timezone
    assert out["worker"]["lang"] == "en-US"
    assert out["iframe"] and "Windows" in out["iframe"]["ua"]
    assert out["iframe"]["screenW"] == screen_metrics(prof.resolved())["w"]
    assert out["iframe"]["ptr"] is True


def test_multipage_no_freeze(kernel):
    """Camoufox #279 analog: several pages from one browser, stable identity,
    no deadlock."""
    b, _ = kernel
    pages = [b.new_page("about:blank") for _ in range(3)]
    vals = []
    for p in pages:
        vals.append(p.evaluate(
            "navigator.userAgent + '|' + String(new Date().getTimezoneOffset())"))
    assert len(set(vals)) == 1
    assert "Headless" not in vals[0]
    for p in pages:
        try:
            p.close()
        except Exception:
            pass


# ---------------------------------------------------------- persistence ---

def test_profile_save_load_roundtrip(tmp_path):
    prof = from_preset("macos-jp-creator", seed=4242)
    path = str(tmp_path / "profile.json")
    prof.save(path)
    loaded = FingerprintProfile.load(path)
    assert loaded == prof
    # same file + same seed → identical fingerprint on re-resolution
    assert loaded.resolved() == prof.resolved()


def test_profile_from_dict_ignores_unknown_keys():
    prof = FingerprintProfile.from_dict({"seed": 7, "platform": "linux",
                                         "future_field": 1})
    assert prof.seed == 7 and prof.platform == "linux"


def test_profile_save_cli(tmp_path, capsys):
    from veilbrowser.cli import main
    out = str(tmp_path / "saved.json")
    rc = main(["profile-save", "--preset", "windows-us-office",
               "--seed", "1001", out])
    assert rc == 0
    prof = FingerprintProfile.load(out)
    assert prof.platform == "windows" and prof.seed == 1001
