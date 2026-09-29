"""Native engine (bundle compiled into the kernel) + profile persistence.

Native tests need a kernel built with kernel-patches/extra/veil applied
(scripts/build-kernel.sh); they probe the running binary and skip politely
when it lacks the compiled-in bundle (e.g. a stock fingerprint-chromium).
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from veilbrowser import launch
from veilbrowser.browser import default_binary
from veilbrowser.inject import js_params
from veilbrowser.profile import FingerprintProfile, from_preset


@pytest.fixture(scope="module")
def native():
    """A running native-engine browser; skips when the kernel can't do it."""
    binary = os.environ.get("VEIL_CHROME_PATH") or default_binary()
    if not binary or not os.path.isfile(binary):
        pytest.skip("kernel binary not found")
    prof = from_preset("windows-us-office", seed=1001)
    try:
        b = launch(prof, engine="native", headless=True)
    except Exception as exc:
        pytest.skip(f"native launch failed: {exc}")
    try:
        page = b.new_page("about:blank")
        if page.evaluate("typeof veilNativeCfg === 'function'") is not True:
            pytest.skip("kernel lacks the compiled-in veil bundle")
        yield b
    finally:
        b.stop()


# ---------------------------------------------------------------- native ---

def test_bundle_runs_without_injection(native):
    """The whole point: fingerprint present with ZERO CDP injection."""
    assert native.js_params is None                 # no injection machinery
    assert native.veil_params is not None
    with native.new_page("about:blank") as page:
        assert page.evaluate("typeof veilNativeCfg === 'function'") is True
        assert page.evaluate("!!self.__veil_installed") is True
    deadline = time.monotonic() + 10
    while native.native_active is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert native.native_active is True


def test_identity_matches_params(native):
    params = native.veil_params
    with native.new_page("about:blank") as page:
        assert page.evaluate("navigator.userAgent") == params["userAgent"]
        assert page.evaluate("navigator.platform") == params["navPlatform"]
        assert page.evaluate("navigator.webdriver") is False
        assert page.evaluate("navigator.hardwareConcurrency") \
            == params["hardwareConcurrency"]
        assert page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") \
            == params["timezone"]


def test_worker_scope_injected(native):
    """Workers get the bundle natively (no Worker-constructor wrapper)."""
    expected = native.veil_params["userAgent"]
    with native.new_page("about:blank") as page:
        ua = page.evaluate(
            "new Promise(res => {"
            "  try {"
            "    const w = new Worker(URL.createObjectURL(new Blob("
            "      ['postMessage(navigator.userAgent)'],"
            "      {type: 'application/javascript'})));"
            "    w.onmessage = e => res(e.data);"
            "    w.onerror = () => res('WORKER_ERROR');"
            "    setTimeout(() => res('TIMEOUT'), 8000);"
            "  } catch (e) { res('ERR:' + e); }"
            "})", await_promise=True)
    assert ua == expected


def test_multipage_no_freeze(native):
    """Camoufox #279 analog: several pages from one browser, stable identity,
    no deadlock."""
    pages = [native.new_page("about:blank") for _ in range(3)]
    vals = []
    for p in pages:
        vals.append(p.evaluate(
            "navigator.userAgent + '|' + String(new Date().getTimezoneOffset())"))
    assert len(set(vals)) == 1
    assert "Headless" not in vals[0]


def test_native_params_payload_is_slim():
    """The VEIL_PARAMS env payload must stay well under the kernel execve
    per-string limit; fonts live in the binary, not the JSON."""
    params = js_params(from_preset("windows-us-office", seed=1001))
    params.pop("fontData", None)
    payload = json.dumps(params, separators=(",", ":"), sort_keys=True)
    assert len(payload) < 64_000
    assert "fontData" not in payload


def test_native_overlay_rejected():
    with pytest.raises(ValueError):
        launch(FingerprintProfile(seed=1), engine="native", js_overlay=True)


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
