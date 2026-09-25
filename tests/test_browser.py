"""Integration tests against the real fingerprint-chromium kernel."""

import pytest

from veilbrowser import FingerprintProfile
from veilbrowser.probe import collect, open_probe_page

pytestmark = pytest.mark.integration

CANVAS_JS = """
(() => { const c = document.createElement('canvas'); c.width = 300; c.height = 60;
  const x = c.getContext('2d');
  x.textBaseline = 'top'; x.font = '16px Arial';
  x.fillStyle = '#f60'; x.fillRect(0, 0, 100, 30);
  x.fillStyle = '#0f0'; x.fillText('veil-\\u2764', 2, 2);
  return c.toDataURL(); })()
"""


class TestAutomationStealth:
    def test_webdriver_false_and_no_headless_ua(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=5))
        r = collect(page)
        assert r["webdriver"] is False
        assert "Headless" not in r["userAgent"]
        assert "Chrome/" in r["userAgent"]

    def test_fake_shadow_root_exposes_closed_shadow(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=5))
        r = collect(page)
        assert r["fakeShadowRoot"] is True


class TestPlatformSpoofing:
    def test_windows_identity_is_consistent_across_surfaces(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=11, platform="windows",
                                                brand="Chrome"))
        r = collect(page)
        assert "Windows NT 10.0" in r["userAgent"]
        assert r["platform"] == "Win32"
        assert r["uaPlatform"] == "Windows"
        assert any(b.startswith("Google Chrome") for b in r["uaBrands"]), r["uaBrands"]

    def test_macos_identity(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=12, platform="macos",
                                                brand="Edge"))
        r = collect(page)
        assert "Macintosh" in r["userAgent"]
        assert "Edg/" in r["userAgent"]
        assert r["platform"] == "MacIntel"
        assert r["uaPlatform"] == "macOS"

    def test_platform_version_via_high_entropy_ch(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=13, platform="windows",
                                                platform_version="15.0.0"))
        assert page.evaluate(
            "navigator.userAgentData.getHighEntropyValues(['platformVersion'])"
            ".then(v => v.platformVersion.startsWith('15.0'))",
            await_promise=True) is True

    def test_linux_default_keeps_linux_identity(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=14, platform="linux"))
        r = collect(page)
        assert "Linux" in r["userAgent"] or r["platform"].startswith("Linux")


class TestHardwareAndLocale:
    def test_device_memory_in_realistic_pool(self, probe_page):
        seen = set()
        for seed in (21, 22, 23, 24):
            b, page = probe_page(FingerprintProfile(seed=seed))
            seen.add(collect(page)["deviceMemory"])
        assert seen <= {8, 16, 32}
        assert len(seen) > 1

    def test_hardware_concurrency_flag(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=31,
                                                hardware_concurrency=4))
        assert collect(page)["hardwareConcurrency"] == 4

    def test_timezone_and_language(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=32, timezone="Europe/Berlin",
                                                language="de-DE"))
        r = collect(page)
        assert r["timezone"] == "Europe/Berlin"
        assert r["languages"] == ["de-DE", "de"]


def _is_dst(tz: str) -> bool:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).astimezone() \
        .dst() != datetime.timedelta(0)


class TestCanvasAndWebGL:
    def test_canvas_noise_depends_on_seed(self, make_browser):
        def canvas_for(seed):
            b = make_browser(FingerprintProfile(seed=seed))
            with open_probe_page(b) as page:
                return page.evaluate(CANVAS_JS)

        assert canvas_for(41) != canvas_for(42)

    def test_disable_spoofing_canvas_makes_seeds_identical(self, make_browser):
        def canvas_for(seed):
            b = make_browser(FingerprintProfile(seed=seed,
                                                disable_spoofing=("canvas",)))
            with open_probe_page(b) as page:
                return page.evaluate(CANVAS_JS)

        assert canvas_for(41) == canvas_for(42)

    def test_text_only_canvas_is_not_noised_known_gap(self, make_browser):
        """Documented kernel gap: text-only canvases are seed-invariant
        (headless). Closed by engine="kernel" + js_overlay=True — see
        test_js_overlay_closes_text_canvas_gap."""
        def canvas_for(seed):
            b = make_browser(FingerprintProfile(seed=seed))
            with open_probe_page(b) as page:
                return page.evaluate("""
                    (() => { const c = document.createElement('canvas');
                      c.width = 300; c.height = 60;
                      const x = c.getContext('2d');
                      x.font = '16px Arial'; x.fillText('veil', 2, 20);
                      return c.toDataURL(); })()""")

        assert canvas_for(41) == canvas_for(42)

    def test_js_overlay_closes_text_canvas_gap(self, make_browser):
        """kernel + js_overlay: only the canvas section is injected on top of
        the C++ patches, so the pure-text canvas the kernel misses is noised
        per seed while the kernel-owned identity stays untouched."""
        def canvas_for(seed):
            b = make_browser(FingerprintProfile(seed=seed), js_overlay=True)
            with open_probe_page(b) as page:
                return page.evaluate("""
                    (() => { const c = document.createElement('canvas');
                      c.width = 300; c.height = 60;
                      const x = c.getContext('2d');
                      x.font = '16px Arial'; x.fillText('veil', 2, 20);
                      return c.toDataURL(); })()""")

        assert canvas_for(41) != canvas_for(42)
        assert canvas_for(41) == canvas_for(41)  # bit-stable per seed

    def test_js_overlay_keeps_kernel_identity(self, make_browser):
        b = make_browser(FingerprintProfile(seed=11, platform="windows"),
                         js_overlay=True)
        with open_probe_page(b) as page:
            r = collect(page)
        assert r["platform"] == "Win32"
        assert "Windows NT 10.0" in r["userAgent"]

    def test_webgl_gpu_simulated_not_swiftshader(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=51))
        r = collect(page)
        assert isinstance(r["webglRenderer"], str) and r["webglRenderer"]
        assert "SwiftShader" not in r["webglRenderer"]

    def test_audio_renderable(self, probe_page):
        b, page = probe_page(FingerprintProfile(seed=52))
        r = collect(page)
        assert isinstance(r["audio"], (int, float)) and r["audio"] > 0

    def test_audio_fingerprint_depends_on_seed(self, make_browser):
        from veilbrowser.probe import open_probe_page

        def audio_for(seed):
            b = make_browser(FingerprintProfile(seed=seed))
            with open_probe_page(b) as page:
                return collect(page)["audio"]

        assert audio_for(53) != audio_for(54)


class TestSessions:
    def test_persistent_context_survives_restart(self, make_browser, tmp_path):
        udd = str(tmp_path / "profile")
        b = make_browser(FingerprintProfile(seed=61), user_data_dir=udd)
        with open_probe_page(b) as page:
            page.evaluate("localStorage.setItem('veil', 'persisted')")
        b.stop()

        b2 = make_browser(FingerprintProfile(seed=61), user_data_dir=udd)
        with open_probe_page(b2) as page:
            assert page.evaluate("localStorage.getItem('veil')") == "persisted"

    def test_temp_user_data_dir_cleaned_on_stop(self, make_browser):
        b = make_browser(FingerprintProfile(seed=62))
        udd = b.user_data_dir
        b.stop()
        import os
        assert not os.path.exists(udd)
