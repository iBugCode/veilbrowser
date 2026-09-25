"""E2E: browser -> local auth forwarder -> authenticated upstream -> target.

Proves the CloakBrowser gap (proxy password auth) is filled, for both HTTP
and SOCKS5 upstreams. Loopback bypass is disabled with <-loopback> so the
chain is actually exercised. The page navigates directly to the target
(top-level navigation, no CORS interference with the proxy assertion).
"""

import base64
import json

import pytest

from tests._proxies import TargetSite, UpstreamHTTPProxy, UpstreamSocks5Proxy
from veilbrowser import FingerprintProfile
from veilbrowser.probe import open_probe_page

pytestmark = pytest.mark.integration

BYPASS_LOOPBACK = ["--proxy-bypass-list=<-loopback>"]


def _load_target(page, url):
    """Navigate to the target through the proxy chain, return parsed body JSON."""
    page.navigate(url)
    body = page.evaluate("document.body.innerText")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise AssertionError(f"target page not loaded through proxy; body={body[:200]!r}")


def test_http_upstream_chain_with_auth(make_browser):
    target = TargetSite()
    upstream = UpstreamHTTPProxy("alice", "s3cret")
    try:
        prof = FingerprintProfile(seed=71,
                                  proxy=f"http://alice:s3cret@127.0.0.1:{upstream.port}")
        b = make_browser(prof, extra_flags=BYPASS_LOOPBACK)
        assert b.proxy_arg.startswith("http://127.0.0.1:")  # via local forwarder
        with open_probe_page(b) as page:
            result = _load_target(page, target.url)
        assert result == {"ok": True, "path": "/probe"}
        assert "/probe" in target.requests           # origin saw the request
        # favicon hits may add entries; membership is the robust check
        assert "Basic " + base64.b64encode(b"alice:s3cret").decode() in upstream.saw_auth
        assert upstream.connects                      # CONNECT through the forwarder
    finally:
        target.stop()
        upstream.stop()


def test_http_upstream_rejects_wrong_password(make_browser):
    target = TargetSite()
    upstream = UpstreamHTTPProxy("alice", "right")
    try:
        prof = FingerprintProfile(seed=72,
                                  proxy="http://alice:wrong@127.0.0.1:"
                                        f"{upstream.port}")
        b = make_browser(prof, extra_flags=BYPASS_LOOPBACK)
        with open_probe_page(b) as page:
            page.navigate(target.url)
            body = page.evaluate("document.body.innerText")
        assert "ok" not in body                      # error page, not the target
        assert "/probe" not in target.requests       # nothing reached the origin
    finally:
        target.stop()
        upstream.stop()


def test_socks5_upstream_chain_with_auth(make_browser):
    target = TargetSite()
    upstream = UpstreamSocks5Proxy("bob", "hunter2")
    try:
        prof = FingerprintProfile(seed=73,
                                  proxy=f"socks5://bob:hunter2@127.0.0.1:{upstream.port}")
        b = make_browser(prof, extra_flags=BYPASS_LOOPBACK)
        assert b.proxy_arg.startswith("socks5://127.0.0.1:")
        with open_probe_page(b) as page:
            result = _load_target(page, target.url)
        assert result == {"ok": True, "path": "/probe"}
        assert ("bob", "hunter2") in upstream.saw_auth
        assert any(c.endswith(f":{target.port}") for c in upstream.connects)
        assert "/probe" in target.requests
    finally:
        target.stop()
        upstream.stop()


def test_proxy_without_creds_gets_407_blocked(make_browser):
    target = TargetSite()
    upstream = UpstreamHTTPProxy("carol", "pw")  # upstream demands auth, browser has none
    try:
        prof = FingerprintProfile(seed=74,
                                  proxy=f"http://127.0.0.1:{upstream.port}")
        b = make_browser(prof, extra_flags=BYPASS_LOOPBACK)
        # no credentials -> no local forwarder; raw upstream handed to chromium
        assert b.proxy_arg == f"http://127.0.0.1:{upstream.port}"
        with open_probe_page(b) as page:
            page.navigate(target.url)
            body = page.evaluate("document.body.innerText")
        assert "ok" not in body
        assert "/probe" not in target.requests  # 407 blocked, as expected
    finally:
        target.stop()
        upstream.stop()
