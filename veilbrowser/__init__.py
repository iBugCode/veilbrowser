"""veilbrowser — a CloakBrowser-style fingerprint browser wrapper.

Kernel-independent by default: the fingerprint lives in our JS bundle
(veilbrowser.inject) injected via CDP into any vanilla ungoogled-chromium.
A fingerprint-chromium kernel can be used via engine="kernel"/"both".

    from veilbrowser import launch, from_preset

    with launch(from_preset("windows-us-office", seed=1001),
                proxy="socks5://user:pass@proxy.example.com:1080") as browser:
        page = browser.new_page("https://example.com")
        print(page.evaluate("navigator.userAgent"))
"""

from .browser import Browser, default_binary, launch, start
from .cdp import CDP, CDPError, DevTools
from .geo import align_profile, locale_for_country, query_geo
from .inject import build_script, install, js_params
from .profile import FingerprintProfile, PRESETS, from_preset
from .probe import check, collect, print_report
from .upgrade import latest_release, upgrade as upgrade_kernel

__version__ = "0.6.0"

__all__ = [
    "Browser", "CDP", "CDPError", "DevTools", "FingerprintProfile", "PRESETS",
    "launch", "start", "default_binary", "from_preset", "collect", "check", "print_report",
    "js_params", "build_script", "install", "latest_release", "upgrade_kernel",
    "align_profile", "locale_for_country", "query_geo",
    "__version__",
]
