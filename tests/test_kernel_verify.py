"""Kernel-engine verification: prove the C++ patch layer is live in the binary.

These go beyond injected-JS behaviour — every assertion here can only pass if
the fingerprint patch set was actually compiled into VEIL_CHROME_PATH:

  * screen.__width/__height    — new IDL attributes (patch 019)
  * screen.width spoofing      — consumed switches (patch set core)
  * Notification/permissions/  — headless masking (patch 020)
    document.hasFocus()
  * ActiveText system color    — deterministic colors (patch 021)
"""

from __future__ import annotations

from veilbrowser import FingerprintProfile
from veilbrowser.inject import screen_metrics


SCREEN_JS = """({
  width: screen.width,
  height: screen.height,
  availWidth: screen.availWidth,
  availHeight: screen.availHeight,
  realWidth: typeof screen.__width === 'number' ? screen.__width : null,
  realHeight: typeof screen.__height === 'number' ? screen.__height : null,
  outerWidth: window.outerWidth,
  outerHeight: window.outerHeight,
  protoHasAttrs: Object.getOwnPropertyNames(Screen.prototype)
                 .filter(n => n === '__width' || n === '__height')
})"""


def test_kernel_screen_cpp_spoof_and_real_expose(make_browser):
    """screen.width comes from the C++ switch consumption; __width/__height are
    the new IDL attributes exposing the real host window via realOuterWidth."""
    profile = FingerprintProfile(seed=4242, platform="windows")
    sm = screen_metrics(profile)
    b = make_browser(profile)
    page = b.new_page("about:blank")
    r = page.evaluate(SCREEN_JS)
    # C++ spoofing active: matches the profile-derived switch values exactly
    assert (r["width"], r["height"]) == (sm["w"], sm["h"])
    assert (r["availWidth"], r["availHeight"]) == (sm["availW"], sm["availH"])
    # new IDL surface exists (Screen.prototype carries the attributes)
    assert r["protoHasAttrs"] == ["__width", "__height"]
    assert isinstance(r["realWidth"], int) and r["realWidth"] > 0
    assert isinstance(r["realHeight"], int) and r["realHeight"] > 0
    # realOuterWidth/realOuterHeight mirror the true window, not the spoof
    assert r["realWidth"] == r["outerWidth"]
    assert r["realHeight"] == r["outerHeight"]
    # and they differ from the spoofed screen (otherwise nothing was spoofed)
    assert (r["realWidth"], r["realHeight"]) != (r["width"], r["height"])


def test_kernel_headless_masking(make_browser):
    """Under --headless=new the kernel reports non-automation signals
    (patch 020 gates on the 'headless' switch). Notification/permissions need
    a SECURE context: stock Chrome also reports 'denied' on insecure origins,
    so the masking only applies to https/file pages."""
    from veilbrowser.probe import open_probe_page

    b = make_browser(FingerprintProfile(seed=77, platform="windows"))
    page = open_probe_page(b)
    assert page.evaluate("Notification.permission") == "default"
    state = page.evaluate(
        "navigator.permissions.query({name:'notifications'})"
        ".then(s => s.state)", await_promise=True)
    assert state == "prompt"
    assert page.evaluate("document.hasFocus()") is True


def test_kernel_active_text_color(make_browser):
    """System colors are deterministic under headless (patch 021): ActiveText
    is pinned to the stock-Chrome fallback red instead of theme-dependent."""
    b = make_browser(FingerprintProfile(seed=5, platform="windows"))
    page = b.new_page("about:blank")
    r = page.evaluate("""
      (() => {
        const el = document.createElement('span');
        el.style.color = 'activetext';
        document.body.appendChild(el);
        const c = getComputedStyle(el).color;
        el.remove();
        return c;
      })()
    """)
    assert r == "rgb(255, 0, 0)"
