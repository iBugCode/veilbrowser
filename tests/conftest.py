"""Shared fixtures: binary discovery + browser factory with auto-cleanup.

Two kernels in play:
  * binary_path — fingerprint-chromium kernel (C++ patches), engine="kernel"
  * vanilla_path — unpatched ungoogled-chromium, engine="js"
"""

from __future__ import annotations

import pytest

import veilbrowser
from veilbrowser.browser import default_binary


@pytest.fixture(scope="session")
def binary_path():
    import os
    env = os.environ.get("VEIL_CHROME_PATH")
    if env and os.path.isfile(env):
        return env
    path = default_binary()
    if not path:
        pytest.skip("fingerprint-chromium binary not found (set VEIL_CHROME_PATH)")
    return path


@pytest.fixture(scope="session")
def vanilla_path():
    import os
    env = os.environ.get("VEIL_VANILLA_CHROME_PATH")
    if env and os.path.isfile(env):
        return env
    path = default_binary(vanilla=True)
    if not path:
        pytest.skip("vanilla ungoogled-chromium binary not found "
                    "(set VEIL_VANILLA_CHROME_PATH)")
    return path


@pytest.fixture()
def make_browser(binary_path):
    browsers = []

    def _make(profile=None, **kwargs):
        kwargs.setdefault("engine", "kernel")
        b = veilbrowser.launch(profile, binary=binary_path, **kwargs)
        browsers.append(b)
        return b

    yield _make
    for b in browsers:
        b.stop()


@pytest.fixture()
def make_js_browser(vanilla_path):
    """Vanilla kernel + our injected JS engine."""
    browsers = []

    def _make(profile=None, **kwargs):
        kwargs.setdefault("engine", "js")
        b = veilbrowser.launch(profile, binary=vanilla_path, **kwargs)
        browsers.append(b)
        return b

    yield _make
    for b in browsers:
        b.stop()


@pytest.fixture()
def probe_page(make_browser):
    """Factory: (profile, kwargs) -> (browser, CDP page on a secure-context file:// URL)."""
    from veilbrowser.probe import open_probe_page

    def _make(profile=None, **kwargs):
        b = make_browser(profile, **kwargs)
        page = open_probe_page(b)
        return b, page

    return _make


@pytest.fixture()
def js_probe_page(make_js_browser):
    from veilbrowser.probe import open_probe_page

    def _make(profile=None, **kwargs):
        b = make_js_browser(profile, **kwargs)
        page = open_probe_page(b)
        return b, page

    return _make
